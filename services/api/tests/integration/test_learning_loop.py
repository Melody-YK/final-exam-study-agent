import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select, text

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import LocalPrincipalProvider, Principal
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    ConversationMessageModel,
    ConversationModel,
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    LearningMasteryModel,
    LearningUnitModel,
    LearningUnitSourceModel,
    LexicalManifestModel,
    PracticeBatchAttemptModel,
    PracticeBatchItemModel,
    PracticeBatchModel,
    PracticeQuestionEvidenceModel,
    PracticeQuestionModel,
    PracticeSessionModel,
    PracticeSessionQuestionModel,
    RevisionChunkModel,
    RevisionPageModel,
    StoredObjectModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.courses.repository import Course, CourseRepository
from study_agent.modules.jobs.clock import SystemClock
from study_agent.modules.learning.runner import LearningBatchRunner
from study_agent.modules.learning.service import (
    LearningLoopService,
    LearningServiceError,
    LearningServiceErrorCode,
)
from study_agent.providers.errors import ProviderError, ProviderErrorCode
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import JsonCompletionPrompt, StructuredJsonDraft
from study_contracts import (
    LearningUnitPracticeStatus,
    LearningUnitStatus,
    PracticeBatchRequest,
    canonical_sha256,
)


async def _table_names(connection: object) -> set[str]:
    result = await connection.execute(  # type: ignore[attr-defined]
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND (table_name LIKE 'learning_%' "
            "OR table_name LIKE 'practice_%' OR table_name = 'conversation_messages')"
        )
    )
    return {str(row[0]) for row in result}


async def _constraint_names(connection: object, table_name: str) -> set[str]:
    result = await connection.execute(  # type: ignore[attr-defined]
        text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_schema = 'public' AND table_name = :table_name"
        ),
        {"table_name": table_name},
    )
    return {str(row[0]) for row in result}


async def _seed_practice_fixture(
    database: Database,
    principal: Principal,
    course: Course,
) -> tuple[str, str]:
    document_id = str(uuid4())
    revision_id = str(uuid4())
    chunk_id = f"{revision_id}:chunk:1"
    unit_id = str(uuid4())
    question_id = str(uuid4())
    session_id = str(uuid4())
    content_sha256 = "c" * 64
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    locator = {"kind": "page", "ordinal": 1}
    async with database.session(principal) as session:
        session.add(
            StoredObjectModel(
                id=str(uuid4()),
                user_id=course.user_id,
                course_id=course.id,
                object_key=f"tests/{course.id}/{document_id}",
                purpose="original",
                sha256="d" * 64,
                size_bytes=100,
                media_type="application/pdf",
            )
        )
        await session.flush()
        stored_object = await session.scalar(
            select(StoredObjectModel).where(
                StoredObjectModel.course_id == course.id,
                StoredObjectModel.user_id == course.user_id,
            )
        )
        assert stored_object is not None
        session.add(
            DocumentModel(
                id=document_id,
                user_id=course.user_id,
                course_id=course.id,
                stored_object_id=stored_object.id,
                filename="learning.pdf",
                media_type="application/pdf",
                corpus_role="corpus",
                verified_sha256="d" * 64,
                status="ready",
                review_status="approved",
                deletion_epoch=0,
                active_revision_id=None,
            )
        )
        await session.flush()
        session.add(
            DocumentRevisionModel(
                id=revision_id,
                document_id=document_id,
                ordinal=1,
                manifest={},
                canonical_sha256="e" * 64,
                total_page_count=1,
                parser_profile="native-v1",
                parser_schema_version="1.0",
                chunker_version="section-page-v1",
                quality_status="passed",
            )
        )
        await session.flush()
        session.add(
            RevisionPageModel(
                id=str(uuid4()),
                revision_id=revision_id,
                page_ordinal=1,
                source_kind="page",
                width=1000,
                height=1000,
                bbox_norm={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                source_backend="pdf-native",
                source_version="1",
                raw_result_ref="artifact://test",
                quality={"status": "passed"},
            )
        )
        await session.flush()
        session.add(
            RevisionChunkModel(
                id=chunk_id,
                revision_id=revision_id,
                ordinal=1,
                text=(
                    "进程是资源分配的基本单位。它拥有独立的地址空间和系统资源，"  # noqa: RUF001
                    "由操作系统负责创建、调度和回收。线程是进程中的执行单元，"  # noqa: RUF001
                    "同一进程内的线程共享进程资源，但可以独立参与处理器调度。"  # noqa: RUF001
                ),
                locator_kind="page",
                page_ordinal=1,
                section_path=["测试章节"],
                source_block_ids=["block-1"],
                token_count_estimate=12,
                content_sha256=content_sha256,
                chunker_version="section-page-v1",
            )
        )
        document = await session.scalar(
            select(DocumentModel).where(DocumentModel.id == document_id)
        )
        assert document is not None
        document.active_revision_id = revision_id
        session.add(
            LearningUnitModel(
                id=unit_id,
                user_id=course.user_id,
                course_id=course.id,
                canonical_key="section:test",
                label="测试章节",
                kind="section",
                status="available",
            )
        )
        session.add(
            LearningUnitSourceModel(
                id=str(uuid4()),
                user_id=course.user_id,
                course_id=course.id,
                unit_id=unit_id,
                document_id=document_id,
                revision_id=revision_id,
                chunk_id=chunk_id,
                content_sha256=content_sha256,
                locator=locator,
                status="valid",
            )
        )
        evidence = {
            "document_id": document_id,
            "revision_id": revision_id,
            "chunk_id": chunk_id,
            "content_sha256": content_sha256,
            "locator": locator,
            "quote": "进程是资源分配的基本单位。",
        }
        session.add(
            PracticeQuestionModel(
                id=question_id,
                user_id=course.user_id,
                course_id=course.id,
                learning_unit_id=unit_id,
                source_revision_id=revision_id,
                question_type="single_choice",
                prompt="进程是什么?",
                options=[
                    {"id": "a", "label": "资源分配的基本单位"},
                    {"id": "b", "label": "文件格式"},
                ],
                correct_answer="a",
                explanation="原文明确说明进程是资源分配的基本单位。",
                evidence_refs=[evidence],
                difficulty=1,
                status="ready",
                content_sha256="f" * 64,
            )
        )
        await session.flush()
        session.add(
            PracticeQuestionEvidenceModel(
                id=str(uuid4()),
                user_id=course.user_id,
                course_id=course.id,
                question_id=question_id,
                ordinal=1,
                document_id=document_id,
                revision_id=revision_id,
                chunk_id=chunk_id,
                content_sha256=content_sha256,
                locator=locator,
                quote="进程是资源分配的基本单位。",
            )
        )
        session.add(
            PracticeSessionModel(
                id=session_id,
                user_id=course.user_id,
                course_id=course.id,
                learning_unit_ids=[unit_id],
                question_count=1,
                mode="practice",
                status="active",
                started_at=now,
            )
        )
        session.add(
            PracticeSessionQuestionModel(
                id=str(uuid4()),
                user_id=course.user_id,
                course_id=course.id,
                session_id=session_id,
                question_id=question_id,
                ordinal=1,
            )
        )
    return session_id, question_id


class _GeneratedQuestionProvider:
    async def complete_json(self, request: JsonCompletionPrompt) -> StructuredJsonDraft:
        if request.response_schema_version == "practice-tutor-1.1":
            mode = request.payload["mode"]
            intent = request.payload["current_intent"]
            return StructuredJsonDraft(
                payload={
                    "answer_markdown": (
                        "可以类比公司分配预算和员工执行任务。"
                        if intent == "example"
                        else (
                            "先比较题干强调的是资源归属还是执行调度。"
                            if mode == "hint"
                            else "进程负责资源分配, 这与课程定义一致。"
                        )
                    ),
                    "evidence_ids": ["E1"],
                },
                model="test-provider",
            )
        if request.response_schema_version == "learning-question-review-1.1":
            candidate = request.payload["candidate"]
            assert isinstance(candidate, dict)
            options = candidate["options"]
            assert isinstance(options, list)
            correct_index = int(candidate["proposed_correct_option_index"])
            return StructuredJsonDraft(
                payload={
                    "verdict": "pass",
                    "correct_option_index": correct_index,
                    "option_verdicts": [
                        "correct" if index == correct_index else "incorrect"
                        for index in range(len(options))
                    ],
                    "issue_codes": [],
                    "reason": "证据只支持指定的正确选项。",
                },
                model="test-provider",
            )
        evidence = request.payload["evidence"][0]
        assert isinstance(evidence, dict)
        text = str(evidence["text"])
        question_type = str(request.payload["question_type"])
        options = (
            ["资源分配的基本单位", "文件格式", "存储介质"]
            if question_type == "single_choice"
            else ["正确", "错误"]
        )
        return StructuredJsonDraft(
            payload={
                "question_type": question_type,
                "prompt": "进程是什么?",
                "options": options,
                "correct_option_index": 0,
                "explanation": text,
                "evidence_ids": [evidence["id"]],
                "difficulty": "easy",
            },
            model="test-provider",
        )


class _RetryOnceQuestionProvider:
    def __init__(self, failure_mode: str) -> None:
        self.failure_mode = failure_mode
        self.generation_calls = 0
        self.review_calls = 0

    async def complete_json(self, request: JsonCompletionPrompt) -> StructuredJsonDraft:
        if request.response_schema_version == "learning-question-review-1.1":
            self.review_calls += 1
            if self.failure_mode == "semantic" and self.review_calls == 1:
                candidate = request.payload["candidate"]
                assert isinstance(candidate, dict)
                options = candidate["options"]
                assert isinstance(options, list)
                return StructuredJsonDraft(
                    payload={
                        "verdict": "reject",
                        "correct_option_index": 0,
                        "option_verdicts": [
                            "correct" if index == 0 else "also_correct"
                            for index in range(len(options))
                        ],
                        "issue_codes": ["MULTIPLE_CORRECT"],
                        "reason": "干扰项仍然可以成立。",
                    },
                    model="test-provider",
                )
            return await _GeneratedQuestionProvider().complete_json(request)
        self.generation_calls += 1
        if self.failure_mode == "provider_bad_response" and self.generation_calls == 1:
            raise ProviderError(
                ProviderErrorCode.BAD_RESPONSE,
                provider="test-provider",
                retryable=False,
            )
        return await _GeneratedQuestionProvider().complete_json(request)


async def test_learning_migration_creates_scoped_tables_and_constraints(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            tables = await _table_names(connection)
            assert {
                "learning_units",
                "learning_unit_sources",
                "learning_mastery",
                "practice_questions",
                "practice_question_evidence",
                "practice_batches",
                "practice_batch_items",
                "practice_batch_attempts",
                "practice_batch_events",
                "practice_sessions",
                "practice_session_questions",
                "practice_attempts",
                "conversation_messages",
            } <= tables
            unit_constraints = await _constraint_names(connection, "learning_units")
            assert {
                "uq_learning_units_course_key",
                "ck_learning_units_kind",
                "ck_learning_units_status",
            } <= unit_constraints
            attempt_constraints = await _constraint_names(connection, "practice_attempts")
            assert {
                "uq_practice_attempts_idempotency",
                "uq_practice_attempts_question",
                "ck_practice_attempts_score",
                "ck_practice_attempts_previous_mastery",
                "ck_practice_attempts_mastery",
                "ck_practice_attempts_review_time",
            } <= attempt_constraints
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_learning_batch_flushes_generated_question_before_item_reference(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    course = await CourseRepository(database).create(principal, "题目生成保存顺序")
    _session_id, seeded_question_id = await _seed_practice_fixture(database, principal, course)
    async with database.session(principal) as session:
        seeded_question = await session.scalar(
            select(PracticeQuestionModel).where(PracticeQuestionModel.id == seeded_question_id)
        )
        assert seeded_question is not None
        unit_id = seeded_question.learning_unit_id

    request = PracticeBatchRequest(learning_unit_ids=[unit_id], question_count=1)
    batch_id = str(uuid4())
    async with database.session(principal) as session:
        session.add(
            PracticeBatchModel(
                id=batch_id,
                user_id=course.user_id,
                course_id=course.id,
                learning_unit_ids=[unit_id],
                target_question_count=1,
                total_items=1,
                status="queued",
                phase="validating_inputs",
                idempotency_key_hash=hashlib.sha256(batch_id.encode()).hexdigest(),
                request_hash=canonical_sha256(
                    {"course_id": course.id, **request.model_dump(mode="json")}
                ),
                state_version=1,
                attempt_count=0,
            )
        )
        await session.flush()
        session.add(
            PracticeBatchItemModel(
                id=str(uuid4()),
                user_id=course.user_id,
                course_id=course.id,
                batch_id=batch_id,
                ordinal=1,
                status="queued",
                attempt_count=0,
            )
        )

    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        lexical_index_root=tmp_path / "lexical",
        practice_generation_enabled=True,
        practice_runner_enabled=True,
    )
    registry = ProviderRegistry(
        embedding_provider=None,
        chat_provider=_GeneratedQuestionProvider(),  # type: ignore[arg-type]
        http_client=None,
        owns_http_client=False,
    )
    service = LearningLoopService(database, settings, SystemClock(), registry)
    runner = LearningBatchRunner(database, settings, SystemClock(), service)
    try:
        assert await runner.run_once(batch_id) == batch_id
        snapshot = await service.get_batch(principal, batch_id)
        assert snapshot.status.value == "succeeded"
        assert len(snapshot.question_ids) == 1
        async with database.session(principal) as session:
            generated = await session.scalar(
                select(PracticeQuestionModel).where(
                    PracticeQuestionModel.id == snapshot.question_ids[0]
                )
            )
            evidence = await session.scalar(
                select(PracticeQuestionEvidenceModel).where(
                    PracticeQuestionEvidenceModel.question_id == snapshot.question_ids[0]
                )
            )
        assert generated is not None
        assert evidence is not None
    finally:
        await runner.shutdown()
        await database.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("failure_mode", "expected_review_calls"),
    (("semantic", 6), ("provider_bad_response", 5)),
)
async def test_learning_batch_retries_invalid_model_output_until_all_items_succeed(
    test_database_url: str,
    tmp_path: Path,
    failure_mode: str,
    expected_review_calls: int,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    course = await CourseRepository(database).create(principal, "题目生成重试")
    _session_id, seeded_question_id = await _seed_practice_fixture(database, principal, course)
    async with database.session(principal) as session:
        seeded_question = await session.scalar(
            select(PracticeQuestionModel).where(PracticeQuestionModel.id == seeded_question_id)
        )
        assert seeded_question is not None
        unit_id = seeded_question.learning_unit_id

    batch_id = str(uuid4())
    request = PracticeBatchRequest(learning_unit_ids=[unit_id], question_count=5)
    async with database.session(principal) as session:
        session.add(
            PracticeBatchModel(
                id=batch_id,
                user_id=course.user_id,
                course_id=course.id,
                learning_unit_ids=[unit_id],
                target_question_count=5,
                total_items=5,
                status="queued",
                phase="validating_inputs",
                idempotency_key_hash=hashlib.sha256(batch_id.encode()).hexdigest(),
                request_hash=canonical_sha256(
                    {"course_id": course.id, **request.model_dump(mode="json")}
                ),
                state_version=1,
                attempt_count=0,
            )
        )
        await session.flush()
        session.add_all(
            [
                PracticeBatchItemModel(
                    id=str(uuid4()),
                    user_id=course.user_id,
                    course_id=course.id,
                    batch_id=batch_id,
                    ordinal=ordinal,
                    status="queued",
                    attempt_count=0,
                )
                for ordinal in range(1, 6)
            ]
        )

    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        lexical_index_root=tmp_path / "lexical",
        practice_generation_enabled=True,
        practice_runner_enabled=True,
        practice_generation_max_attempts=3,
    )
    provider = _RetryOnceQuestionProvider(failure_mode)
    registry = ProviderRegistry(
        embedding_provider=None,
        chat_provider=provider,  # type: ignore[arg-type]
        http_client=None,
        owns_http_client=False,
    )
    service = LearningLoopService(database, settings, SystemClock(), registry)
    runner = LearningBatchRunner(database, settings, SystemClock(), service)
    try:
        assert await runner.run_once(batch_id) == batch_id
        snapshot = await service.get_batch(principal, batch_id)
        assert snapshot.status.value == "succeeded"
        assert snapshot.completed_items == 5
        assert len(snapshot.question_ids) == 5
        assert provider.generation_calls == 6
        assert provider.review_calls == expected_review_calls
        assert snapshot.items[0].attempt_count == 2
        assert all(item.attempt_count == 1 for item in snapshot.items[1:])
        if failure_mode == "provider_bad_response":
            async with database.session(principal) as session:
                error_codes = list(
                    await session.scalars(
                        select(PracticeBatchAttemptModel.error_code).where(
                            PracticeBatchAttemptModel.batch_id == batch_id
                        )
                    )
                )
            assert ProviderErrorCode.BAD_RESPONSE.value in error_codes
            assert "PROVIDER_PROVIDER_BAD_RESPONSE" not in error_codes
    finally:
        await runner.shutdown()
        await database.dispose()


@pytest.mark.integration
async def test_learning_units_project_topics_and_aggregate_child_evidence(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    course = await CourseRepository(database).create(principal, "主题投影")
    _session_id, _question_id = await _seed_practice_fixture(database, principal, course)

    async with database.session(principal) as session:
        document = await session.scalar(
            select(DocumentModel).where(DocumentModel.course_id == course.id)
        )
        assert document is not None
        assert document.active_revision_id is not None
        session.add(
            RevisionPageModel(
                id=str(uuid4()),
                revision_id=document.active_revision_id,
                page_ordinal=2,
                source_kind="page",
                width=1000,
                height=1000,
                bbox_norm={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                source_backend="pdf-native",
                source_version="1",
                raw_result_ref="artifact://test-child",
                quality={"status": "passed"},
            )
        )
        await session.flush()
        session.add(
            RevisionChunkModel(
                id=f"{document.active_revision_id}:chunk:2",
                revision_id=document.active_revision_id,
                ordinal=2,
                text="子章节补充了线程共享地址空间以及调度状态之间的关系。",
                locator_kind="page",
                page_ordinal=2,
                section_path=["测试章节", "线程与调度"],
                source_block_ids=["block-2"],
                token_count_estimate=10,
                content_sha256="b" * 64,
                chunker_version="section-page-v1",
            )
        )

    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        lexical_index_root=tmp_path / "lexical",
    )
    service = LearningLoopService(
        database,
        settings,
        SystemClock(),
        ProviderRegistry(
            embedding_provider=None,
            chat_provider=None,
            http_client=None,
            owns_http_client=False,
        ),
    )
    try:
        units = await service.list_learning_units(principal, course.id)
        topics = [unit for unit in units if unit.label == "测试章节" and unit.status == "available"]
        assert len(topics) == 1
        topic = topics[0]
        assert topic.practice_status is LearningUnitPracticeStatus.READY
        assert topic.evidence_chunk_count == 2
        assert len(topic.sources) == 2
        assert all(unit.label != "线程与调度" for unit in units)

        app = create_app(settings=settings, database=database)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(f"/api/v1/courses/{course.id}/learning-units/regenerate")

        assert response.status_code == 200
        regenerated_labels = {item["label"] for item in response.json()}
        assert "测试章节" in regenerated_labels
        assert "线程与调度" not in regenerated_labels
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_learning_batch_rejects_topic_with_insufficient_evidence(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    course = await CourseRepository(database).create(principal, "出题预检")
    _session_id, _question_id = await _seed_practice_fixture(database, principal, course)
    async with database.session(principal) as session:
        manifest = LexicalManifestModel(
            id=str(uuid4()),
            user_id=course.user_id,
            course_id=course.id,
            version_id="practice-preflight-v1",
            storage_path=str(tmp_path / "lexical-preflight"),
            manifest_hash="a" * 64,
            document_set_hash="b" * 64,
            tokenizer_version="test-v1",
            dictionary_hash="c" * 64,
            chunk_count=1,
            document_ids=[],
            revision_ids=[],
            status="active",
        )
        session.add(manifest)
        await session.flush()
        course_model = await session.scalar(select(CourseModel).where(CourseModel.id == course.id))
        assert course_model is not None
        course_model.active_lexical_index_id = manifest.id

    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        lexical_index_root=tmp_path / "lexical",
        course_terms=("进程",),
        practice_generation_enabled=True,
        practice_runner_enabled=True,
    )
    service = LearningLoopService(
        database,
        settings,
        SystemClock(),
        ProviderRegistry(
            embedding_provider=None,
            chat_provider=_GeneratedQuestionProvider(),  # type: ignore[arg-type]
            http_client=None,
            owns_http_client=False,
        ),
    )
    try:
        units = await service.list_learning_units(principal, course.id)
        topic = next(
            unit for unit in units if unit.label == "测试章节" and unit.status == "available"
        )
        concept = next(unit for unit in units if unit.parent_id == topic.id)
        with pytest.raises(LearningServiceError) as overlapping:
            await service.create_batch(
                principal,
                course.id,
                PracticeBatchRequest(
                    learning_unit_ids=[topic.id, concept.id],
                    question_count=2,
                ),
                "overlapping-scope",
            )
        assert overlapping.value.code is LearningServiceErrorCode.INVALID_REQUEST

        async with database.session(principal) as session:
            chunk = await session.scalar(
                select(RevisionChunkModel).where(RevisionChunkModel.text.like("进程是资源%"))
            )
            assert chunk is not None
            chunk.text = "只有标题"
            chunk.content_sha256 = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()

        with pytest.raises(LearningServiceError) as caught:
            await service.create_batch(
                principal,
                course.id,
                PracticeBatchRequest(learning_unit_ids=[topic.id], question_count=1),
                "short-evidence",
            )
        assert caught.value.code is LearningServiceErrorCode.INSUFFICIENT_EVIDENCE
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_learning_units_hide_legacy_zero_placeholder_without_deleting_references(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    course = await CourseRepository(database).create(principal, "学习单元回归")
    _session_id, question_id = await _seed_practice_fixture(database, principal, course)
    placeholder_id = str(uuid4())
    unavailable_id = str(uuid4())
    stale_id = str(uuid4())

    async with database.session(principal) as session:
        document = await session.scalar(
            select(DocumentModel).where(DocumentModel.course_id == course.id)
        )
        question = await session.scalar(
            select(PracticeQuestionModel).where(PracticeQuestionModel.id == question_id)
        )
        assert document is not None
        assert document.active_revision_id is not None
        chunk = await session.scalar(
            select(RevisionChunkModel).where(
                RevisionChunkModel.revision_id == document.active_revision_id
            )
        )
        assert chunk is not None
        assert question is not None
        valid_unit_id = question.learning_unit_id
        session.add_all(
            [
                LearningUnitModel(
                    id=placeholder_id,
                    user_id=course.user_id,
                    course_id=course.id,
                    canonical_key="section:0 0 0 0",
                    label="0 0 0 0",
                    kind="section",
                    status="available",
                ),
                LearningUnitModel(
                    id=unavailable_id,
                    user_id=course.user_id,
                    course_id=course.id,
                    canonical_key="section:旧章节",
                    label="旧章节",
                    kind="section",
                    status="unavailable",
                ),
                LearningUnitModel(
                    id=stale_id,
                    user_id=course.user_id,
                    course_id=course.id,
                    canonical_key="section:过期章节",
                    label="过期章节",
                    kind="section",
                    status="stale",
                ),
            ]
        )
        await session.flush()
        question.learning_unit_id = placeholder_id
        session.add_all(
            [
                LearningUnitSourceModel(
                    id=str(uuid4()),
                    user_id=course.user_id,
                    course_id=course.id,
                    unit_id=placeholder_id,
                    document_id=document.id,
                    revision_id=document.active_revision_id,
                    chunk_id=chunk.id,
                    content_sha256=chunk.content_sha256,
                    locator={"kind": "page", "ordinal": 1},
                    status="valid",
                ),
                LearningUnitSourceModel(
                    id=str(uuid4()),
                    user_id=course.user_id,
                    course_id=course.id,
                    unit_id=unavailable_id,
                    document_id=document.id,
                    revision_id=document.active_revision_id,
                    chunk_id=chunk.id,
                    content_sha256=chunk.content_sha256,
                    locator={"kind": "page", "ordinal": 1},
                    status="stale",
                ),
                LearningUnitSourceModel(
                    id=str(uuid4()),
                    user_id=course.user_id,
                    course_id=course.id,
                    unit_id=stale_id,
                    document_id=document.id,
                    revision_id=document.active_revision_id,
                    chunk_id=chunk.id,
                    content_sha256=chunk.content_sha256,
                    locator={"kind": "page", "ordinal": 1},
                    status="stale",
                ),
                LearningMasteryModel(
                    id=str(uuid4()),
                    user_id=course.user_id,
                    course_id=course.id,
                    learning_unit_id=placeholder_id,
                    attempt_count=1,
                    correct_count=0,
                    last_score=0,
                    mastery_level="learning",
                    next_review_at=datetime(2000, 1, 1, tzinfo=UTC),
                ),
                LearningMasteryModel(
                    id=str(uuid4()),
                    user_id=course.user_id,
                    course_id=course.id,
                    learning_unit_id=valid_unit_id,
                    attempt_count=1,
                    correct_count=0,
                    last_score=0,
                    mastery_level="learning",
                    next_review_at=datetime(2000, 1, 1, tzinfo=UTC),
                ),
                LearningMasteryModel(
                    id=str(uuid4()),
                    user_id=course.user_id,
                    course_id=course.id,
                    learning_unit_id=stale_id,
                    attempt_count=1,
                    correct_count=0,
                    last_score=0,
                    mastery_level="learning",
                    next_review_at=datetime(2000, 1, 1, tzinfo=UTC),
                ),
            ]
        )

    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        lexical_index_root=tmp_path / "lexical",
    )
    app = create_app(settings=settings, database=database)
    service = app.state.learning_service
    async with database.session(principal) as session:
        snapshots = await service._unit_snapshots(session, principal, course.id)
    snapshots_by_id = {unit.id: unit for unit in snapshots}
    assert placeholder_id not in snapshots_by_id
    assert snapshots_by_id[unavailable_id].status is LearningUnitStatus.UNAVAILABLE
    assert snapshots_by_id[stale_id].status is LearningUnitStatus.STALE

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        queue_response = await client.get(f"/api/v1/courses/{course.id}/review-queue")
        response = await client.get(f"/api/v1/courses/{course.id}/learning-units")

    assert queue_response.status_code == 200
    queue_ids = {item["learning_unit_id"] for item in queue_response.json()}
    assert valid_unit_id in queue_ids
    assert placeholder_id not in queue_ids
    assert unavailable_id not in queue_ids
    assert stale_id not in queue_ids
    assert response.status_code == 200
    units = {item["id"]: item for item in response.json()}
    assert placeholder_id not in units
    assert units[unavailable_id]["status"] == "unavailable"
    assert units[unavailable_id]["sources"][0]["status"] == "stale"
    assert units[stale_id]["status"] == "unavailable"
    assert units[stale_id]["sources"][0]["status"] == "stale"

    async with database.session(principal) as session:
        persisted_placeholder = await session.scalar(
            select(LearningUnitModel).where(LearningUnitModel.id == placeholder_id)
        )
        persisted_source = await session.scalar(
            select(LearningUnitSourceModel).where(LearningUnitSourceModel.unit_id == placeholder_id)
        )
        persisted_mastery = await session.scalar(
            select(LearningMasteryModel).where(
                LearningMasteryModel.learning_unit_id == placeholder_id
            )
        )
        persisted_question = await session.scalar(
            select(PracticeQuestionModel).where(PracticeQuestionModel.id == question_id)
        )

    assert persisted_placeholder is not None
    assert persisted_source is not None
    assert persisted_source.status == "stale"
    assert persisted_mastery is not None
    assert persisted_question is not None
    assert persisted_question.learning_unit_id == placeholder_id
    await database.dispose()


@pytest.mark.integration
async def test_learning_attempt_idempotency_and_stale_session_evidence(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    course = await CourseRepository(database).create(principal, "操作系统")
    session_id, question_id = await _seed_practice_fixture(database, principal, course)
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        lexical_index_root=tmp_path / "lexical",
    )
    registry = ProviderRegistry(
        embedding_provider=None,
        chat_provider=_GeneratedQuestionProvider(),  # type: ignore[arg-type]
        http_client=None,
        owns_http_client=False,
    )
    app = create_app(settings=settings, database=database, provider_registry=registry)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        hint = await client.post(
            f"/api/v1/practice-sessions/{session_id}/questions/{question_id}/tutor",
            json={"message": "以后请先解释概念, 给我一点提示", "turn_id": "turn-hint"},
        )
        example = await client.post(
            f"/api/v1/practice-sessions/{session_id}/questions/{question_id}/tutor",
            json={"message": "你能给个例子吗?", "turn_id": "turn-example"},
        )
        example_replay = await client.post(
            f"/api/v1/practice-sessions/{session_id}/questions/{question_id}/tutor",
            json={"message": "你能给个例子吗?", "turn_id": "turn-example"},
        )
        example_conflict = await client.post(
            f"/api/v1/practice-sessions/{session_id}/questions/{question_id}/tutor",
            json={"message": "换一个问题", "turn_id": "turn-example"},
        )
        restored_hint_conversation = await client.get(
            f"/api/v1/practice-sessions/{session_id}/questions/{question_id}/tutor"
        )
        tutor_memories = await client.get(f"/api/v1/courses/{course.id}/learner-memories")
        first = await client.post(
            f"/api/v1/practice-sessions/{session_id}/attempts",
            headers={"Idempotency-Key": "attempt-1"},
            json={"question_id": question_id, "answer": "a", "viewed_hint": False},
        )
        replay = await client.post(
            f"/api/v1/practice-sessions/{session_id}/attempts",
            headers={"Idempotency-Key": "attempt-1"},
            json={"question_id": question_id, "answer": "b"},
        )
        session = await client.get(f"/api/v1/practice-sessions/{session_id}")
        review = await client.post(
            f"/api/v1/practice-sessions/{session_id}/questions/{question_id}/tutor",
            json={"message": "为什么这个答案正确?", "turn_id": "turn-review"},
        )
        restored_review_conversation = await client.get(
            f"/api/v1/practice-sessions/{session_id}/questions/{question_id}/tutor"
        )
        async with database.session(principal) as db_session:
            question = await db_session.scalar(
                select(PracticeQuestionModel).where(PracticeQuestionModel.id == question_id)
            )
            assert question is not None
            question.status = "invalid"
        invalid_session = await client.get(f"/api/v1/practice-sessions/{session_id}")
        async with database.session(principal) as db_session:
            question = await db_session.scalar(
                select(PracticeQuestionModel).where(PracticeQuestionModel.id == question_id)
            )
            document = await db_session.scalar(select(DocumentModel))
            assert question is not None
            assert document is not None
            question.status = "ready"
            document.review_status = "pending"
        stale_session = await client.get(f"/api/v1/practice-sessions/{session_id}")
        stale_replay = await client.post(
            f"/api/v1/practice-sessions/{session_id}/attempts",
            headers={"Idempotency-Key": "attempt-1"},
            json={"question_id": question_id, "answer": "a"},
        )

    assert hint.status_code == 200
    assert hint.json()["mode"] == "hint"
    assert hint.json()["evidence_refs"][0]["document_name"] == "learning.pdf"
    assert hint.json()["intent"] == "hint"
    assert example.status_code == 200
    assert example.json()["intent"] == "example"
    assert "公司分配预算" in example.json()["answer_markdown"]
    assert example_replay.status_code == 200
    assert example_replay.json()["message_id"] == example.json()["message_id"]
    assert example_conflict.status_code == 409
    assert example_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert restored_hint_conversation.status_code == 200
    assert [message["role"] for message in restored_hint_conversation.json()["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert restored_hint_conversation.json()["messages"][-1]["intent"] == "example"
    assert tutor_memories.status_code == 200
    assert [memory["content"] for memory in tutor_memories.json()] == [
        "以后请先解释概念, 给我一点提示"
    ]
    assert first.status_code == 201
    assert replay.status_code == 409
    assert replay.json()["code"] == "STATE_CONFLICT"
    assert session.status_code == 200
    assert session.json()["questions"][0]["outcome"] == "correct"
    assert session.json()["questions"][0]["submitted_answer"] == "a"
    assert session.json()["questions"][0]["explanation"].startswith("原文明确说明")
    assert session.json()["questions"][0]["mastery_reason"]
    assert session.json()["questions"][0]["viewed_hint"] is True
    assert review.status_code == 200
    assert review.json()["mode"] == "review"
    assert restored_review_conversation.status_code == 200
    assert len(restored_review_conversation.json()["messages"]) == 6
    async with database.session(principal) as db_session:
        conversations = list(
            await db_session.scalars(
                select(ConversationModel).where(
                    ConversationModel.conversation_type == "practice_tutor"
                )
            )
        )
        messages = list(await db_session.scalars(select(ConversationMessageModel)))
    assert len(conversations) == 1
    assert len(messages) == 6
    assert invalid_session.status_code == 200
    assert invalid_session.json()["questions"][0]["status"] == "invalid"
    assert invalid_session.json()["questions"][0]["evidence_refs"] == []
    assert stale_session.status_code == 200
    assert stale_session.json()["questions"][0]["status"] == "stale"
    assert stale_session.json()["questions"][0]["evidence_refs"] == []
    assert stale_replay.status_code == 409
    assert stale_replay.json()["code"] == "STATE_CONFLICT"
    await database.dispose()


@pytest.mark.integration
async def test_learning_batch_replay_survives_provider_disabled_gate(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    course = await CourseRepository(database).create(principal, "数据库")
    request = PracticeBatchRequest(learning_unit_ids=["unit-1"], question_count=1)
    request_hash = canonical_sha256({"course_id": course.id, **request.model_dump(mode="json")})
    key_hash = hashlib.sha256(b"replay-batch").hexdigest()
    batch_id = str(uuid4())
    async with database.session(principal) as session:
        session.add(
            PracticeBatchModel(
                id=batch_id,
                user_id=course.user_id,
                course_id=course.id,
                learning_unit_ids=["unit-1"],
                target_question_count=1,
                total_items=1,
                completed_items=0,
                status="queued",
                phase="validating_inputs",
                idempotency_key_hash=key_hash,
                request_hash=request_hash,
                state_version=1,
                attempt_count=0,
            )
        )
        await session.flush()
        session.add(
            PracticeBatchItemModel(
                id=str(uuid4()),
                user_id=course.user_id,
                course_id=course.id,
                batch_id=batch_id,
                ordinal=1,
                status="queued",
                attempt_count=0,
            )
        )
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        practice_generation_enabled=False,
        practice_runner_enabled=False,
    )
    from study_agent.modules.learning.service import LearningLoopService

    snapshot = await LearningLoopService(
        database,
        settings,
        SystemClock(),
        ProviderRegistry(
            embedding_provider=None,
            chat_provider=None,
            http_client=None,
            owns_http_client=False,
        ),
    ).create_batch(principal, course.id, request, "replay-batch")

    assert snapshot.id == batch_id
    await database.dispose()
