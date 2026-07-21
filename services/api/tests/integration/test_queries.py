import json
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import CourseScope, LocalPrincipalProvider, Principal
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    LexicalManifestModel,
    RevisionChunkModel,
    RevisionPageModel,
    StoredObjectModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.answering.retrieval import RetrievedEvidence
from study_agent.modules.answering.types import AuthorizedEvidence
from study_agent.modules.courses.repository import CourseRepository
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import EvidencePrompt, StructuredAnswerDraft
from study_agent.storage.local import LocalStorage
from study_contracts import Evidence, SourceLocator


async def _seed_revision(
    database: Database,
    *,
    principal: Principal,
    user_id: str,
    course_id: str,
) -> tuple[str, str, str, str]:
    document_id = str(uuid4())
    revision_id = str(uuid4())
    chunk_id = f"{revision_id}:chunk:1"
    content_sha256 = "c" * 64
    async with database.session(principal) as session:
        session.add(
            StoredObjectModel(
                id=str(uuid4()),
                user_id=user_id,
                course_id=course_id,
                object_key=f"tests/{course_id}/{document_id}",
                purpose="original",
                sha256="d" * 64,
                size_bytes=100,
                media_type="application/pdf",
            )
        )
        await session.flush()
        stored_object = await session.scalar(
            select(StoredObjectModel).where(
                StoredObjectModel.object_key == f"tests/{course_id}/{document_id}"
            )
        )
        assert stored_object is not None
        document = DocumentModel(
            id=document_id,
            user_id=user_id,
            course_id=course_id,
            stored_object_id=stored_object.id,
            filename=f"{document_id}.pdf",
            media_type="application/pdf",
            corpus_role="corpus",
            verified_sha256="d" * 64,
            status="ready",
            deletion_epoch=0,
        )
        session.add(document)
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
                text="进程是资源分配的基本单位。",
                locator_kind="page",
                page_ordinal=1,
                section_path=["测试"],
                source_block_ids=["block-1"],
                token_count_estimate=12,
                content_sha256=content_sha256,
                chunker_version="section-page-v1",
            )
        )
        document.active_revision_id = revision_id
    return document_id, revision_id, chunk_id, content_sha256


class FakeQueryEvidence:
    def __init__(self, result: RetrievedEvidence, *, current: bool = True) -> None:
        self.result = result
        self.current = current
        self.calls = 0

    async def retrieve(
        self,
        _principal: object,
        _course_id: str,
        _question: str,
        *,
        document_ids: frozenset[str] | None,
    ) -> RetrievedEvidence:
        self.calls += 1
        assert document_ids is None
        return self.result

    async def sources_are_current(
        self,
        _principal: object,
        _course_id: str,
        _active_lexical_index_id: str | None,
        _candidates: tuple[AuthorizedEvidence, ...],
    ) -> bool:
        return self.current


class RecordingChatProvider:
    def __init__(self) -> None:
        self.requests: list[EvidencePrompt] = []

    async def answer(self, request: EvidencePrompt) -> StructuredAnswerDraft:
        self.requests.append(request)
        passage = request.passages[0]
        metadata = passage.metadata
        return StructuredAnswerDraft(
            model="test-chat",
            provider_response_id="response-1",
            usage={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
            payload={
                "status": "answered",
                "answer_markdown": "进程是资源分配的基本单位。",
                "claims": [
                    {
                        "id": "claim-1",
                        "text": "进程是资源分配的基本单位。",
                        "citation_ids": [passage.id],
                    }
                ],
                "citations": [
                    {
                        "id": passage.id,
                        "document_id": metadata["document_id"],
                        "revision_id": metadata["revision_id"],
                        "chunk_id": metadata["chunk_id"],
                        "document_name": metadata["document_name"],
                        "locator": {"kind": "page", "ordinal": 1},
                        "quote": "进程是资源分配的基本单位",
                        "bounding_boxes": [],
                    }
                ],
            },
        )


def _authorized(
    course_id: str,
    *,
    document_id: str,
    revision_id: str,
    chunk_id: str,
    content_sha256: str,
) -> AuthorizedEvidence:
    return AuthorizedEvidence(
        evidence=Evidence(
            id=chunk_id,
            course_id=course_id,
            document_id=document_id,
            revision_id=revision_id,
            chunk_id=chunk_id,
            text="进程是资源分配的基本单位。",
            content_sha256=content_sha256,
            locator=SourceLocator(kind="page", ordinal=1),
        ),
        document_name=f"{document_id}.pdf",
        score=0.9,
        document_deletion_epoch=0,
        provenance=("pdf-native@1",),
    )


def _settings(database_url: str, root: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(database_url),
        local_storage_root=root,
        lexical_index_root=root / "lexical",
    )


def _registry(provider: RecordingChatProvider) -> ProviderRegistry:
    return ProviderRegistry(
        embedding_provider=None,
        chat_provider=provider,
        http_client=None,
        owns_http_client=False,
    )


def _sse_data(body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.integration
async def test_query_persists_answer_snapshot_and_aggregated_sse(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    provider = RecordingChatProvider()
    evidence = FakeQueryEvidence(
        RetrievedEvidence(
            active_index=True,
            candidates=(),
            retrieval_trace_id=None,
            active_lexical_index_id="lexical-1",
        )
    )
    app = create_app(
        settings=_settings(test_database_url, tmp_path),
        database=database,
        storage=LocalStorage(tmp_path),
        provider_registry=_registry(provider),
        query_evidence=evidence,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        course = await client.post("/api/v1/courses", json={"title": "操作系统"})
        course_id = course.json()["id"]
        principal = LocalPrincipalProvider().resolve("127.0.0.1")
        course_record = await CourseRepository(database).get(
            CourseScope(principal=principal, course_id=course_id)
        )
        assert course_record is not None
        document_id, revision_id, chunk_id, content_sha256 = await _seed_revision(
            database,
            principal=principal,
            user_id=course_record.user_id,
            course_id=course_id,
        )
        async with database.session(principal) as session:
            manifest = LexicalManifestModel(
                id="lexical-1",
                user_id=course_record.user_id,
                course_id=course_id,
                version_id="query-test-v1",
                storage_path=str(tmp_path / "lexical-1"),
                manifest_hash="f" * 64,
                document_set_hash="a" * 64,
                tokenizer_version="test-v1",
                dictionary_hash="b" * 64,
                chunk_count=1,
                document_ids=[document_id],
                revision_ids=[revision_id],
                status="active",
            )
            session.add(manifest)
            await session.flush()
            course_model = await session.get(CourseModel, course_id)
            assert course_model is not None
            course_model.active_lexical_index_id = manifest.id
        evidence.result = RetrievedEvidence(
            active_index=True,
            candidates=(
                _authorized(
                    course_id,
                    document_id=document_id,
                    revision_id=revision_id,
                    chunk_id=chunk_id,
                    content_sha256=content_sha256,
                ),
            ),
            retrieval_trace_id=None,
            active_lexical_index_id="lexical-1",
        )
        created = await client.post(
            f"/api/v1/courses/{course_id}/queries",
            json={"question": "什么是进程?"},
        )

        assert created.status_code == 202
        query = created.json()
        assert query["status"] == "answered"
        assert query["answer"]["claims"][0]["citation_ids"] == [chunk_id]
        assert query["trace"]["retrieval_snapshot_id"]
        assert query["trace"]["retrieval_trace_id"] is None
        assert query["usage"]["total_tokens"] == 30

        snapshot = await client.get(f"/api/v1/queries/{query['id']}")
        events = await client.get(f"/api/v1/queries/{query['id']}/events?once=true")

    assert snapshot.status_code == 200
    envelopes = _sse_data(events.text)
    deltas = [item for item in envelopes if item["event_type"] == "answer.delta"]
    assert len(deltas) == 1
    assert deltas[0]["data"]["delta"] == "进程是资源分配的基本单位。"  # type: ignore[index]
    assert len(provider.requests) == 1
    assert evidence.calls == 1
    await database.dispose()


@pytest.mark.integration
async def test_query_without_active_index_abstains_without_chat_call(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    provider = RecordingChatProvider()
    evidence = FakeQueryEvidence(RetrievedEvidence(active_index=False, candidates=()))
    app = create_app(
        settings=_settings(test_database_url, tmp_path),
        database=database,
        storage=LocalStorage(tmp_path),
        provider_registry=_registry(provider),
        query_evidence=evidence,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        course = await client.post("/api/v1/courses", json={"title": "数据库"})
        response = await client.post(
            f"/api/v1/courses/{course.json()['id']}/queries",
            json={"question": "课件外问题"},
        )

    assert response.status_code == 202
    assert response.json()["status"] == "abstained"
    assert response.json()["answer"]["refusal"]["code"] == "INDEX_UNAVAILABLE"
    assert provider.requests == []
    await database.dispose()
