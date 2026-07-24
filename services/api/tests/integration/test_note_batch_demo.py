from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from services.api.tests.integration.retrieval_helpers import seed_document_revision
from study_agent.config import AppMode, Settings
from study_agent.identity.principal import AuthenticationMethod, Principal
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    DocumentModel,
    DocumentRevisionModel,
    NoteContentVersionModel,
    NoteCoverageUnitModel,
    NoteCoverageUnitResultModel,
    NoteGenerationBatchModel,
    NoteGenerationEventModel,
    NoteGenerationItemModel,
    NoteGenerationOutputModel,
    NoteModel,
    NoteSourceModel,
    RevisionChunkModel,
    RevisionPageModel,
    StoredObjectModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.courses.repository import CourseRepository
from study_agent.modules.jobs.clock import SystemClock
from study_agent.modules.notes.batch_service import (
    NoteBatchService,
    NoteBatchServiceError,
    NoteBatchServiceErrorCode,
)
from study_agent.modules.notes.demo_runner import DemoNoteRunner
from study_agent.providers.factory import ProviderRegistry
from study_agent.storage.local import LocalStorage
from study_contracts import (
    MergedNoteBatchRequest,
    NoteBatchCommandKind,
    NoteBatchMode,
    NoteBatchStyle,
    NoteContentAstV1,
)


class StaticPrincipalProvider:
    def __init__(self, principal: Principal) -> None:
        self._principal = principal

    def resolve(self, client_host: str) -> Principal:
        del client_host
        return self._principal


@dataclass(frozen=True, slots=True)
class _StyleArtifacts:
    body: str
    source_node_texts: tuple[str, ...]
    source_count: int
    covered_entry_counts_by_page: dict[int, int]
    covered_pages: frozenset[int]
    skipped_pages: frozenset[int]
    source_set_sha256: str


def _settings(database_url: str, root: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(database_url),
        local_storage_root=root,
        lexical_index_root=root / "lexical",
        note_demo_phase_delay_seconds=0,
    )


def _provider_registry_without_upstream() -> ProviderRegistry:
    return ProviderRegistry(
        embedding_provider=None,
        chat_provider=None,
        http_client=None,
        owns_http_client=False,
    )


async def _seed_ready_documents(
    database: Database,
    principal: Principal,
) -> tuple[str, tuple[str, str]]:
    course = await CourseRepository(database).create(principal, "本地笔记演示")
    document_ids: list[str] = []
    async with database.session(principal) as session:
        for ordinal, text in enumerate(
            ("进程是操作系统进行资源分配的基本单位。", "临界区需要互斥访问共享资源。"),
            start=1,
        ):
            seeded = await seed_document_revision(
                session,
                user_id=course.user_id,
                course_id=course.id,
                text_chunks=[text],
                active=True,
                preview=False,
            )
            document = await session.get(DocumentModel, seeded.document_id)
            assert document is not None
            media_type = (
                "application/pdf"
                if ordinal == 1
                else "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            )
            document.filename = f"chapter-{ordinal}.{'pdf' if ordinal == 1 else 'pptx'}"
            document.media_type = media_type
            document.status = "ready"
            stored_object = await session.get(StoredObjectModel, document.stored_object_id)
            assert stored_object is not None
            stored_object.media_type = media_type
            document_ids.append(document.id)
    return course.id, (document_ids[0], document_ids[1])


def _style_page_chunks(page_ordinal: int) -> tuple[str, ...]:
    prefix = f"P{page_ordinal:02d}"
    first_detail = (
        f"{prefix}-C1-B 关键结论要求必须检查核心条件、关键步骤、原因、结果、区别、作用和注意事项。"
        if page_ordinal == 1
        else f"{prefix}-C1-B 边界之外的普通说明用于对照课堂例子。"
    )
    return (
        f"{prefix}-C1-A 核心定义说明第 {page_ordinal} 页的互斥边界是共享状态"
        f"一致性的基础, 关键原则适用于每次访问。{first_detail}",
        f"{prefix}-C2-A 调度案例记录两个执行单元交替读写同一数据的时序, "
        f"并保留第 {page_ordinal} 页的现场快照。{prefix}-C2-B 课堂讨论比较不同交错次序。",
        f"{prefix}-C3-A 课堂示例展示加锁前后共享状态的变化, "
        f"编号 {page_ordinal} 用于定位原始材料。{prefix}-C3-B 示例结束后核对每次读写。",
        f"{prefix}-C4-A 练习材料描述一组独立操作和对应输出, "
        f"页码 {page_ordinal} 保证摘录能够唯一回溯。{prefix}-C4-B 练习答案留作课后复盘。",
    )


async def _seed_style_document(
    database: Database,
    principal: Principal,
) -> tuple[str, str, str, tuple[str, ...]]:
    course = await CourseRepository(database).create(principal, "笔记模板差异")
    source_texts: list[str] = []
    async with database.session(principal) as session:
        first_page_chunks = _style_page_chunks(1)
        seeded = await seed_document_revision(
            session,
            user_id=course.user_id,
            course_id=course.id,
            text_chunks=first_page_chunks,
            active=True,
            preview=False,
        )
        source_texts.extend(first_page_chunks)
        document = await session.get(DocumentModel, seeded.document_id)
        revision = await session.get(DocumentRevisionModel, seeded.revision_id)
        assert document is not None
        assert revision is not None
        document.filename = "concurrency-review.pdf"
        revision.total_page_count = 16
        for page_ordinal in range(2, 17):
            session.add(
                RevisionPageModel(
                    id=str(uuid4()),
                    revision_id=revision.id,
                    page_ordinal=page_ordinal,
                    source_kind="page",
                    width=1000,
                    height=1000,
                    bbox_norm={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                    source_backend="pdf-native",
                    source_version="1",
                    raw_result_ref=f"artifact://test/page/{page_ordinal}",
                    quality={"status": "passed"},
                )
            )
            await session.flush()
            for chunk_in_page, text in enumerate(_style_page_chunks(page_ordinal), start=1):
                ordinal = (page_ordinal - 1) * 4 + chunk_in_page
                source_texts.append(text)
                session.add(
                    RevisionChunkModel(
                        id=f"{revision.id}:chunk:{ordinal}",
                        revision_id=revision.id,
                        ordinal=ordinal,
                        text=text,
                        locator_kind="page",
                        page_ordinal=page_ordinal,
                        section_path=["测试"],
                        source_block_ids=[f"block-{ordinal}"],
                        token_count_estimate=max(1, len(text)),
                        content_sha256=sha256(text.encode()).hexdigest(),
                        chunker_version="section-page-v1",
                    )
                )
        await session.flush()
    return course.id, seeded.document_id, seeded.revision_id, tuple(source_texts)


@pytest.mark.integration
async def test_note_batch_demo_runs_without_providers_and_persists_preview(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject="note-demo-owner",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    course_id, document_ids = await _seed_ready_documents(database, principal)
    app = create_app(
        settings=_settings(test_database_url, tmp_path),
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(principal),
        provider_registry=_provider_registry_without_upstream(),
    )
    payload = {
        "mode": "merged",
        "document_ids": list(document_ids),
        "title": "操作系统来源摘录",
        "section_path": ["期末复习"],
    }
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            created = await client.post(
                f"/api/v1/courses/{course_id}/note-batches",
                headers={"Idempotency-Key": "demo-batch-1"},
                json=payload,
            )
            assert created.status_code == 202
            created_body = created.json()
            batch_id = created_body["id"]
            assert created.headers["location"] == f"/api/v1/note-batches/{batch_id}"
            assert created_body["status"] == "queued"
            assert created_body["style"] == "exam_focus"
            assert created_body["total_items"] == 1
            assert [item["document_id"] for item in created_body["inputs"]] == list(document_ids)

            runner = app.state.note_runner
            assert isinstance(runner, DemoNoteRunner)
            assert await runner.run_once(batch_id) == batch_id

            completed = await client.get(f"/api/v1/note-batches/{batch_id}")
            assert completed.status_code == 200
            completed_body = completed.json()
            assert completed_body["status"] == "succeeded"
            assert completed_body["completed_items"] == 1
            assert completed_body["completed_at"] is not None
            assert completed_body["items"][0]["status"] == "succeeded"
            assert completed_body["items"][0]["attempt"] == 1
            assert completed_body["items"][0]["elapsed_seconds"] >= 0
            note_id = completed_body["items"][0]["note_id"]
            assert note_id
            assert {unit["status"] for unit in completed_body["coverage_units"]} == {"covered"}

            note_response = await client.get(f"/api/v1/notes/{note_id}")
            assert note_response.status_code == 200
            note = note_response.json()
            assert note["generated_by_model"] is False
            body_markdown = note["body_markdown"]
            assert "## chapter-1.pdf" in body_markdown
            assert "### 第 1 页" in body_markdown
            assert "## chapter-2.pptx" in body_markdown
            assert "### 幻灯片 1" in body_markdown
            assert "Source-derived local demo note" not in body_markdown
            assert "进程是操作系统进行资源分配的基本单位" in body_markdown
            assert "临界区需要互斥访问共享资源" in body_markdown
            assert len(note["sources"]) == 2
            assert all(source["chunk_id"] not in body_markdown for source in note["sources"])

            replay = await client.post(
                f"/api/v1/courses/{course_id}/note-batches",
                headers={"Idempotency-Key": "demo-batch-1"},
                json=payload,
            )
            assert replay.status_code == 202
            assert replay.json()["id"] == batch_id
            conflict = await client.post(
                f"/api/v1/courses/{course_id}/note-batches",
                headers={"Idempotency-Key": "demo-batch-1"},
                json={**payload, "title": "不同请求"},
            )
            assert conflict.status_code == 409
            assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
            style_conflict = await client.post(
                f"/api/v1/courses/{course_id}/note-batches",
                headers={"Idempotency-Key": "demo-batch-1"},
                json={**payload, "style": "outline"},
            )
            assert style_conflict.status_code == 409
            assert style_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

            async with database.session(principal) as session:
                document = await session.get(DocumentModel, document_ids[0])
                assert document is not None
                document.review_status = "pending"
            review_filtered_note = await client.get(f"/api/v1/notes/{note_id}")
            sources_by_document = {
                source["document_id"]: source for source in review_filtered_note.json()["sources"]
            }
            assert sources_by_document[document_ids[0]]["available"] is False
            assert sources_by_document[document_ids[0]]["unavailable_reason"] == (
                "SOURCE_UNAVAILABLE"
            )
            assert sources_by_document[document_ids[1]]["available"] is True

        async with database.session(principal) as session:
            assert (
                await session.scalar(select(func.count()).select_from(NoteGenerationBatchModel))
                == 1
            )
            persisted_batch = await session.get(NoteGenerationBatchModel, batch_id)
            assert persisted_batch is not None
            assert persisted_batch.style == NoteBatchStyle.EXAM_FOCUS.value
            assert await session.scalar(select(func.count()).select_from(NoteModel)) == 1
            output = await session.scalar(
                select(NoteGenerationOutputModel).where(
                    NoteGenerationOutputModel.batch_id == batch_id
                )
            )
            assert output is not None
            version = await session.get(NoteContentVersionModel, (output.note_id, 1))
            assert version is not None
            assert version.parser_version == "local-demo-v1"
            NoteContentAstV1.model_validate(version.content_ast)
            events = list(
                await session.scalars(
                    select(NoteGenerationEventModel)
                    .where(NoteGenerationEventModel.batch_id == batch_id)
                    .order_by(NoteGenerationEventModel.sequence)
                )
            )
            event_types = [event.event_type for event in events]
            assert event_types[0] == "note.batch.created"
            assert "note.item.phase" in event_types
            assert event_types[-1] == "note.batch.succeeded"
            phases = [
                event.payload["phase"]
                for event in events
                if event.event_type in {"note.item.running", "note.item.phase"}
            ]
            assert phases == ["validating_inputs", "retrieving", "generating", "saving"]
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_note_batch_styles_render_distinct_source_backed_previews(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject="note-demo-styles",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    course_id, document_id, revision_id, source_texts = await _seed_style_document(
        database, principal
    )
    settings = _settings(test_database_url, tmp_path)
    clock = SystemClock()
    service = NoteBatchService(database, settings, clock)
    runner = DemoNoteRunner(database, settings, clock)
    labels = {
        NoteBatchStyle.EXAM_FOCUS: "考前速记",
        NoteBatchStyle.OUTLINE: "结构提纲",
        NoteBatchStyle.COMPLETE: "完整讲义",
    }
    artifacts: dict[NoteBatchStyle, _StyleArtifacts] = {}
    try:
        for style, label in labels.items():
            created = await service.create_batch(
                principal,
                course_id,
                MergedNoteBatchRequest(
                    mode="merged",
                    document_ids=[document_id],
                    style=style,
                ),
                f"style-{style.value}",
            )
            assert created.style == style
            await runner.run_once(created.id)
            completed = await service.get_batch(principal, created.id)
            assert completed.style == style
            assert completed.items[0].note_id is not None
            async with database.session(principal) as session:
                note = await session.get(NoteModel, completed.items[0].note_id)
                version = await session.get(
                    NoteContentVersionModel, (completed.items[0].note_id, 1)
                )
                sources = list(
                    await session.scalars(
                        select(NoteSourceModel).where(
                            NoteSourceModel.note_id == completed.items[0].note_id
                        )
                    )
                )
                coverage_results = list(
                    await session.scalars(
                        select(NoteCoverageUnitResultModel).where(
                            NoteCoverageUnitResultModel.batch_id == created.id
                        )
                    )
                )
                coverage_units = list(
                    await session.scalars(
                        select(NoteCoverageUnitModel).where(
                            NoteCoverageUnitModel.batch_id == created.id
                        )
                    )
                )
                raw_chunks = list(
                    await session.scalars(
                        select(RevisionChunkModel)
                        .where(RevisionChunkModel.revision_id == revision_id)
                        .order_by(RevisionChunkModel.ordinal)
                    )
                )
            assert note is not None
            assert version is not None
            NoteContentAstV1.model_validate(version.content_ast)
            assert f"笔记模板: {label}" in note.body_markdown
            assert all(chunk.id not in note.body_markdown for chunk in raw_chunks)

            source_nodes = [
                node
                for node in version.content_ast["nodes"]
                if node["type"] == "paragraph" and node["provenance"] == "source_backed"
            ]
            source_node_ids = {str(node["id"]) for node in source_nodes}
            assert len(source_node_ids) == len(source_nodes)
            assert all(
                sum(str(node["text"]) in source.quote for source in sources) == 1
                for node in source_nodes
            )
            assert all(
                any(str(node["text"]) in source.quote for node in source_nodes)
                for source in sources
            )

            raw_chunks_by_id = {chunk.id: chunk for chunk in raw_chunks}
            backing_chunk_ids: list[str] = []
            for node in source_nodes:
                matches = [chunk.id for chunk in raw_chunks if str(node["text"]) in chunk.text]
                assert len(matches) == 1
                backing_chunk_ids.append(matches[0])
            assert {source.chunk_id for source in sources} == set(backing_chunk_ids)
            assert len({source.chunk_id for source in sources}) == len(sources)
            assert all(
                source.quote == raw_chunks_by_id[source.chunk_id].text.strip() for source in sources
            )

            units_by_id = {unit.id: unit for unit in coverage_units}
            assert len(coverage_results) == 16
            covered_ast_node_ids: list[str] = []
            covered_counts_by_page: dict[int, int] = {}
            skipped_pages: set[int] = set()
            for result in coverage_results:
                unit = units_by_id[result.unit_id]
                if result.status == "covered":
                    assert result.reason_code is None
                    assert result.evidence_set_sha256 is not None
                    assert len(result.evidence_set_sha256) == 64
                    int(result.evidence_set_sha256, 16)
                    assert result.ast_node_ids
                    assert all(node_id in source_node_ids for node_id in result.ast_node_ids)
                    covered_ast_node_ids.extend(result.ast_node_ids)
                    covered_counts_by_page[unit.ordinal] = len(result.ast_node_ids)
                else:
                    assert result.status == "skipped"
                    assert result.reason_code == "not_selected_for_style"
                    assert result.evidence_set_sha256 is None
                    assert result.ast_node_ids == []
                    skipped_pages.add(unit.ordinal)
            assert len(covered_ast_node_ids) == len(set(covered_ast_node_ids))
            assert set(covered_ast_node_ids) == source_node_ids

            artifacts[style] = _StyleArtifacts(
                body=note.body_markdown,
                source_node_texts=tuple(str(node["text"]) for node in source_nodes),
                source_count=len(sources),
                covered_entry_counts_by_page=covered_counts_by_page,
                covered_pages=frozenset(covered_counts_by_page),
                skipped_pages=frozenset(skipped_pages),
                source_set_sha256=version.source_set_sha256,
            )

        assert len({artifact.body for artifact in artifacts.values()}) == len(NoteBatchStyle)
        assert len({artifact.source_set_sha256 for artifact in artifacts.values()}) == len(
            NoteBatchStyle
        )

        exam = artifacts[NoteBatchStyle.EXAM_FOCUS]
        assert len(exam.source_node_texts) == 12
        assert exam.covered_entry_counts_by_page
        assert max(exam.covered_entry_counts_by_page.values()) <= 2
        assert exam.source_count < len(exam.source_node_texts)
        assert exam.skipped_pages
        assert all("核心定义" in point or "关键结论" in point for point in exam.source_node_texts)

        outline = artifacts[NoteBatchStyle.OUTLINE]
        assert len(outline.source_node_texts) == 30
        assert max(outline.covered_entry_counts_by_page.values()) <= 3
        assert outline.source_count == len(outline.source_node_texts)
        assert outline.skipped_pages
        assert max(map(len, outline.source_node_texts)) <= 72
        assert all(
            any(point in source for source in source_texts) for point in outline.source_node_texts
        )

        complete = artifacts[NoteBatchStyle.COMPLETE]
        assert len(complete.source_node_texts) == 64
        assert complete.source_count == 64
        assert complete.covered_pages == frozenset(range(1, 17))
        assert not complete.skipped_pages
        assert set(complete.covered_entry_counts_by_page.values()) == {4}
        assert set(complete.source_node_texts) == set(source_texts)
        assert all(source in complete.body for source in source_texts)
        assert len(exam.body) < len(complete.body)
        assert len(outline.body) < len(complete.body)
    finally:
        await runner.shutdown()
        await database.dispose()


@pytest.mark.integration
async def test_note_batch_style_database_constraint_rejects_invalid_value(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject="note-demo-style-constraint",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    course_id, document_ids = await _seed_ready_documents(database, principal)
    service = NoteBatchService(database, _settings(test_database_url, tmp_path), SystemClock())
    created = await service.create_batch(
        principal,
        course_id,
        MergedNoteBatchRequest(mode="merged", document_ids=list(document_ids)),
        "style-constraint",
    )
    try:
        async with database.session(principal) as session:
            batch = await session.get(NoteGenerationBatchModel, created.id)
            assert batch is not None
            batch.style = "invalid"
            with pytest.raises(IntegrityError, match="ck_note_generation_batches_style"):
                await session.flush()
            await session.rollback()
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_note_batch_submit_rejects_pending_ready_document(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject="note-demo-review-gate",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    course_id, document_ids = await _seed_ready_documents(database, principal)
    async with database.session(principal) as session:
        document = await session.get(DocumentModel, document_ids[0])
        assert document is not None
        assert document.status == "ready"
        assert document.active_revision_id is not None
        document.review_status = "pending"

    service = NoteBatchService(
        database,
        _settings(test_database_url, tmp_path),
        SystemClock(),
    )
    try:
        with pytest.raises(NoteBatchServiceError) as caught:
            await service.create_batch(
                principal,
                course_id,
                MergedNoteBatchRequest(mode="merged", document_ids=list(document_ids)),
                "review-gate",
            )
        assert caught.value.code is NoteBatchServiceErrorCode.DOCUMENT_NOT_READY
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("source_change", ["deletion_epoch", "review_pending"])
async def test_note_batch_demo_fails_without_output_when_frozen_source_changes(
    test_database_url: str,
    tmp_path: Path,
    source_change: str,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject="note-demo-source-change",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    course_id, document_ids = await _seed_ready_documents(database, principal)
    settings = _settings(test_database_url, tmp_path)
    clock = SystemClock()
    service = NoteBatchService(database, settings, clock)
    batch = await service.create_batch(
        principal,
        course_id,
        MergedNoteBatchRequest(mode="merged", document_ids=list(document_ids)),
        "source-change-before-save",
    )
    try:
        async with database.session(principal) as session:
            document = await session.get(DocumentModel, document_ids[0])
            assert document is not None
            if source_change == "deletion_epoch":
                document.deletion_epoch += 1
            else:
                document.review_status = "pending"

        runner = DemoNoteRunner(database, settings, clock, phase_delay_seconds=0)
        assert await runner.run_once(batch.id) == batch.id
        snapshot = await service.get_batch(principal, batch.id)
        assert snapshot.items[0].retryable_in_new_batch is True

        async with database.session(principal) as session:
            persisted_batch = await session.get(NoteGenerationBatchModel, batch.id)
            item = await session.scalar(
                select(NoteGenerationItemModel).where(NoteGenerationItemModel.batch_id == batch.id)
            )
            assert persisted_batch is not None
            assert item is not None
            assert persisted_batch.status == "failed"
            assert item.status == "failed"
            assert item.failure_code == "NOTE_SOURCE_CHANGED"
            assert item.failure_summary == "所选资料在任务创建后发生变化, 请重新创建笔记任务。"
            assert item.retryable is True
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(NoteModel)
                    .where(NoteModel.course_id == course_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(NoteGenerationOutputModel)
                    .where(NoteGenerationOutputModel.batch_id == batch.id)
                )
                == 0
            )
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    "source_mutation",
    ["rewrite", "delete"],
)
async def test_note_batch_demo_rejects_chunk_changes_since_submit(
    test_database_url: str,
    tmp_path: Path,
    source_mutation: str,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject=f"note-demo-chunk-{source_mutation}",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    course_id, document_ids = await _seed_ready_documents(database, principal)
    settings = _settings(test_database_url, tmp_path)
    clock = SystemClock()
    service = NoteBatchService(database, settings, clock)
    batch = await service.create_batch(
        principal,
        course_id,
        MergedNoteBatchRequest(mode="merged", document_ids=[document_ids[0]]),
        f"chunk-change-{source_mutation}",
    )
    try:
        async with database.session(principal) as session:
            chunk = await session.scalar(
                select(RevisionChunkModel)
                .where(RevisionChunkModel.revision_id == batch.inputs[0].revision_id)
                .order_by(RevisionChunkModel.ordinal)
            )
            assert chunk is not None
            if source_mutation == "delete":
                await session.delete(chunk)
            else:
                chunk.text = "任务提交后被修改的资料内容。"
                chunk.content_sha256 = sha256(chunk.text.encode("utf-8")).hexdigest()

        runner = DemoNoteRunner(database, settings, clock, phase_delay_seconds=0)
        assert await runner.run_once(batch.id) == batch.id

        snapshot = await service.get_batch(principal, batch.id)
        assert snapshot.status.value == "failed"
        assert snapshot.items[0].failure_code == "NOTE_SOURCE_CHANGED"
        assert snapshot.items[0].retryable_in_new_batch is True
        async with database.session(principal) as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(NoteModel)
                    .where(NoteModel.course_id == course_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(NoteGenerationOutputModel)
                    .where(NoteGenerationOutputModel.batch_id == batch.id)
                )
                == 0
            )
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_note_batch_route_reports_workflow_disabled_without_creating_batch(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject="note-demo-disabled",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    course_id, document_ids = await _seed_ready_documents(database, principal)
    settings = _settings(test_database_url, tmp_path).model_copy(update={"demo_lab_enabled": False})
    app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(principal),
        provider_registry=_provider_registry_without_upstream(),
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                f"/api/v1/courses/{course_id}/note-batches",
                headers={"Idempotency-Key": "disabled-demo"},
                json={"mode": "merged", "document_ids": list(document_ids)},
            )
        assert response.status_code == 503
        assert response.json()["code"] == "NOTE_WORKFLOW_DISABLED"
        assert response.json()["retryable"] is False
        async with database.session(principal) as session:
            assert (
                await session.scalar(select(func.count()).select_from(NoteGenerationBatchModel))
                == 0
            )
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_app_lifespan_recovers_only_demo_compatible_batches_and_shuts_runner_down(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject="note-demo-recovery",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    course_id, document_ids = await _seed_ready_documents(database, principal)
    settings = _settings(test_database_url, tmp_path)
    clock = SystemClock()
    service = NoteBatchService(database, settings, clock)
    batch = await service.create_batch(
        principal,
        course_id,
        MergedNoteBatchRequest(mode="merged", document_ids=list(document_ids)),
        "recover-queued-batch",
    )
    incompatible_mode = await service.create_batch(
        principal,
        course_id,
        MergedNoteBatchRequest(mode="merged", document_ids=list(document_ids)),
        "do-not-recover-per-document",
    )
    incompatible_command = await service.create_batch(
        principal,
        course_id,
        MergedNoteBatchRequest(mode="merged", document_ids=list(document_ids)),
        "do-not-recover-retry",
    )
    async with database.session(principal) as session:
        incompatible_mode_row = await session.get(
            NoteGenerationBatchModel,
            incompatible_mode.id,
        )
        incompatible_command_row = await session.get(
            NoteGenerationBatchModel,
            incompatible_command.id,
        )
        assert incompatible_mode_row is not None
        assert incompatible_command_row is not None
        incompatible_mode_row.mode = "per_document"
        incompatible_command_row.command_kind = "retry_failed"
        incompatible_command_row.retry_of_batch_id = batch.id
    runner = DemoNoteRunner(database, settings, clock, phase_delay_seconds=5)
    app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(principal),
        provider_registry=_provider_registry_without_upstream(),
        clock=clock,
        note_runner=runner,
    )
    restarted_runner: DemoNoteRunner | None = None
    try:
        async with app.router.lifespan_context(app):
            for _ in range(100):
                async with database.session(principal) as session:
                    status = await session.scalar(
                        select(NoteGenerationBatchModel.status).where(
                            NoteGenerationBatchModel.id == batch.id
                        )
                    )
                if status == "running":
                    break
                await asyncio.sleep(0.01)
            assert status == "running"
            async with database.session(principal) as session:
                assert (
                    await session.scalar(
                        select(NoteGenerationBatchModel.status).where(
                            NoteGenerationBatchModel.id == incompatible_mode.id
                        )
                    )
                    == "queued"
                )
                assert (
                    await session.scalar(
                        select(NoteGenerationBatchModel.status).where(
                            NoteGenerationBatchModel.id == incompatible_command.id
                        )
                    )
                    == "queued"
                )

        async with database.session(principal) as session:
            persisted_batch = await session.get(NoteGenerationBatchModel, batch.id)
            assert persisted_batch is not None
            assert persisted_batch.status == "running"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(NoteGenerationOutputModel)
                    .where(NoteGenerationOutputModel.batch_id == batch.id)
                )
                == 0
            )

        restarted_runner = DemoNoteRunner(database, settings, clock, phase_delay_seconds=0)
        assert await restarted_runner.recover_pending() == 1
        assert await restarted_runner.run_once(batch.id) == batch.id

        async with database.session(principal) as session:
            output = await session.scalar(
                select(NoteGenerationOutputModel).where(
                    NoteGenerationOutputModel.batch_id == batch.id
                )
            )
            assert output is not None
            note = await session.get(NoteModel, output.note_id)
            assert note is not None
            assert note.title == "合并课程笔记"
            assert (
                await session.scalar(
                    select(NoteGenerationBatchModel.status).where(
                        NoteGenerationBatchModel.id == incompatible_mode.id
                    )
                )
                == "queued"
            )
            assert (
                await session.scalar(
                    select(NoteGenerationBatchModel.status).where(
                        NoteGenerationBatchModel.id == incompatible_command.id
                    )
                )
                == "queued"
            )
    finally:
        await runner.shutdown()
        if restarted_runner is not None:
            await restarted_runner.shutdown()
        await database.dispose()


@pytest.mark.integration
async def test_note_batch_get_hides_persisted_batches_outside_demo_contract(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject="note-demo-contract-scope",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    course_id, document_ids = await _seed_ready_documents(database, principal)
    settings = _settings(test_database_url, tmp_path)
    service = NoteBatchService(database, settings, SystemClock())
    incompatible_mode = await service.create_batch(
        principal,
        course_id,
        MergedNoteBatchRequest(mode="merged", document_ids=list(document_ids)),
        "persisted-per-document",
    )
    incompatible_command = await service.create_batch(
        principal,
        course_id,
        MergedNoteBatchRequest(mode="merged", document_ids=list(document_ids)),
        "persisted-retry",
    )
    async with database.session(principal) as session:
        mode_row = await session.get(NoteGenerationBatchModel, incompatible_mode.id)
        command_row = await session.get(NoteGenerationBatchModel, incompatible_command.id)
        assert mode_row is not None
        assert command_row is not None
        mode_row.mode = NoteBatchMode.PER_DOCUMENT.value
        command_row.command_kind = NoteBatchCommandKind.RETRY_FAILED.value
        command_row.retry_of_batch_id = incompatible_mode.id

    app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(principal),
        provider_registry=_provider_registry_without_upstream(),
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            hidden_mode = await client.get(f"/api/v1/note-batches/{incompatible_mode.id}")
            hidden_command = await client.get(f"/api/v1/note-batches/{incompatible_command.id}")

        assert hidden_mode.status_code == 404
        assert hidden_command.status_code == 404
        assert hidden_mode.json()["code"] == "RESOURCE_NOT_FOUND"
        assert hidden_command.json()["code"] == "RESOURCE_NOT_FOUND"
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_note_batch_demo_rejects_non_merged_and_hides_other_principals(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    owner = Principal(
        subject="note-demo-scope-owner",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    outsider = Principal(
        subject="note-demo-scope-outsider",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    course_id, document_ids = await _seed_ready_documents(database, owner)
    settings = _settings(test_database_url, tmp_path)
    owner_app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(owner),
        provider_registry=_provider_registry_without_upstream(),
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=owner_app),
            base_url="http://testserver",
        ) as client:
            unsupported_mode = await client.post(
                f"/api/v1/courses/{course_id}/note-batches",
                headers={"Idempotency-Key": "per-document"},
                json={"mode": "per_document", "document_ids": [document_ids[0]]},
            )
            assert unsupported_mode.status_code == 422
            assert unsupported_mode.json()["code"] == "INVALID_REQUEST"
            created = await client.post(
                f"/api/v1/courses/{course_id}/note-batches",
                headers={"Idempotency-Key": "scope-batch"},
                json={"mode": "merged", "document_ids": [document_ids[0]]},
            )
            assert created.status_code == 202
            batch_id = created.json()["id"]
            await owner_app.state.note_runner.run_once(batch_id)

        outsider_app = create_app(
            settings=settings,
            database=database,
            storage=LocalStorage(tmp_path),
            principal_provider=StaticPrincipalProvider(outsider),
            provider_registry=_provider_registry_without_upstream(),
        )
        async with AsyncClient(
            transport=ASGITransport(app=outsider_app),
            base_url="http://testserver",
        ) as client:
            hidden = await client.get(f"/api/v1/note-batches/{batch_id}")
            hidden_course = await client.post(
                f"/api/v1/courses/{course_id}/note-batches",
                headers={"Idempotency-Key": "outsider-batch"},
                json={"mode": "merged", "document_ids": [document_ids[0]]},
            )
        assert hidden.status_code == 404
        assert hidden_course.status_code == 404
    finally:
        await database.dispose()
