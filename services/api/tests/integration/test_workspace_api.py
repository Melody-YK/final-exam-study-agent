from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import (
    AuthenticationMethod,
    LocalPrincipalProvider,
    Principal,
)
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    AnswerDependencyModel,
    ConversationModel,
    DocumentModel,
    DocumentRevisionModel,
    EmbeddingModelModel,
    LexicalManifestModel,
    NoteModel,
    ParseJobModel,
    QueryRunModel,
    RetrievalSnapshotModel,
    RetrievalTraceModel,
    RevisionPageModel,
    StoredObjectModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.courses.repository import CourseRepository
from study_agent.storage.local import LocalStorage


class StaticPrincipalProvider:
    def __init__(self, principal: Principal) -> None:
        self._principal = principal

    def resolve(self, _client_host: str) -> Principal:
        return self._principal


def _settings(database_url: str, root: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(database_url),
        local_storage_root=root,
        lexical_index_root=root / "lexical",
    )


async def _upload_document(client: AsyncClient, course_id: str) -> dict[str, object]:
    payload = b"%PDF-1.7\nworkspace-test"
    created = await client.post(
        f"/api/v1/courses/{course_id}/documents",
        json={
            "filename": "chapter.pdf",
            "media_type": "application/pdf",
            "size_bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
            "corpus_role": "corpus",
        },
    )
    assert created.status_code == 201
    body = created.json()
    uploaded = await client.put(
        body["upload"]["url"],
        content=payload,
        headers={"Content-Type": "application/pdf"},
    )
    assert uploaded.status_code == 200
    completed = await client.post(
        f"/api/v1/documents/{body['document']['id']}/upload:complete",
        json={"upload_session_id": body["upload"]["id"]},
        headers={"Idempotency-Key": "workspace-upload-complete"},
    )
    assert completed.status_code == 202
    return completed.json()


@pytest.mark.integration
async def test_workspace_lists_status_and_retries_only_requested_pages_idempotently(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    storage = LocalStorage(tmp_path)
    app = create_app(
        settings=_settings(test_database_url, tmp_path),
        database=database,
        storage=storage,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        course = await client.post("/api/v1/courses", json={"title": "操作系统"})
        course_id = course.json()["id"]
        document = await _upload_document(client, course_id)

        capabilities = await client.get("/api/v1/capabilities")
        before = await client.get(f"/api/v1/courses/{course_id}/documents")
        assert capabilities.status_code == 200
        assert capabilities.json()["provider"]["status"] == "not_configured"
        assert capabilities.json()["ocr_parser"]["status"] == "worker_required"
        assert before.status_code == 200
        assert before.json()[0]["status"] == "queued"
        assert before.json()[0]["parse_job_id"]
        assert before.json()[0]["progress"] == {}
        assert before.json()[0]["active_revision_id"] is None

        principal = LocalPrincipalProvider().resolve("127.0.0.1")
        async with database.session(principal) as session:
            job = await session.scalar(
                select(ParseJobModel).where(ParseJobModel.document_id == document["id"])
            )
            persisted_document = await session.get(DocumentModel, document["id"])
            assert job is not None and persisted_document is not None
            job.status = "failed"
            job.failed_pages = [2]
            job.retryable = False
            persisted_document.status = "failed"

        retried = await client.post(
            f"/api/v1/documents/{document['id']}/parse-jobs",
            json={"failed_pages": [2]},
            headers={"Idempotency-Key": "retry-failed-page"},
        )
        replay = await client.post(
            f"/api/v1/documents/{document['id']}/parse-jobs",
            json={"failed_pages": [2]},
            headers={"Idempotency-Key": "retry-failed-page"},
        )
        active_conflict = await client.post(
            f"/api/v1/documents/{document['id']}/parse-jobs",
            json={"failed_pages": [2]},
            headers={"Idempotency-Key": "retry-second-job"},
        )
        after = await client.get(f"/api/v1/courses/{course_id}/documents")

    assert retried.status_code == 202
    assert retried.json()["failed_pages"] == [2]
    assert replay.json() == retried.json()
    assert active_conflict.status_code == 409
    assert active_conflict.json()["code"] == "STATE_CONFLICT"
    assert after.json()[0]["status"] == "queued"
    assert after.json()[0]["failed_pages"] == [2]
    await database.dispose()


@pytest.mark.integration
async def test_notes_and_lab_projection_are_scoped_and_redacted(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    owner = Principal(subject="workspace-owner", authentication_method=AuthenticationMethod.LOCAL)
    outsider = Principal(
        subject="workspace-outsider",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    course = await CourseRepository(database).create(owner, "数据库")
    async with database.session(owner) as session:
        session.add(
            NoteModel(
                id="workspace-note",
                user_id=course.user_id,
                course_id=course.id,
                section_path=["事务"],
                title="事务",
                body_markdown="事务具有原子性。",
                version=1,
                generation=1,
                generated_by_model=False,
                status="ready",
            )
        )
        session.add(
            RetrievalTraceModel(
                id="workspace-trace",
                user_id=course.user_id,
                course_id=course.id,
                query_sha256="a" * 64,
                mode="rrf",
                scope_document_ids=[],
                rrf_k=60,
                dense_candidates=[{"chunk_id": "private-chunk-id", "rank": 1, "score": 0.9}],
                lexical_candidates=[],
                fused_candidates=[{"chunk_id": "private-chunk-id", "score": 0.02}],
                rerank_candidates=[],
                timings_ms={"dense": 4.0, "total": 7.0},
                reranker_applied=False,
            )
        )

    owner_app = create_app(
        settings=_settings(test_database_url, tmp_path),
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(owner),
    )
    outsider_app = create_app(
        settings=_settings(test_database_url, tmp_path),
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(outsider),
    )
    async with AsyncClient(
        transport=ASGITransport(app=owner_app),
        base_url="http://testserver",
    ) as client:
        notes = await client.get(f"/api/v1/courses/{course.id}/notes")
        lab = await client.get(f"/api/v1/courses/{course.id}/lab/trace")
    async with AsyncClient(
        transport=ASGITransport(app=outsider_app),
        base_url="http://testserver",
    ) as client:
        hidden_notes = await client.get(f"/api/v1/courses/{course.id}/notes")
        hidden_lab = await client.get(f"/api/v1/courses/{course.id}/lab/trace")

    assert notes.status_code == 200
    assert notes.json()[0]["id"] == "workspace-note"
    assert lab.status_code == 200
    assert lab.json()["mode"] == "rrf"
    assert lab.json()["candidates"][0]["chunk_id"] != "private-chunk-id"
    assert "private-chunk-id" not in lab.text
    assert lab.json()["revision_id"] is None
    assert lab.json()["parser_backend"] is None
    assert lab.json()["tokenizer_version"] is None
    assert lab.json()["citation_validation"] is None
    assert lab.json()["refusal_reason"] is None
    assert lab.json()["usage"] is None
    assert hidden_notes.status_code == 404
    assert hidden_lab.status_code == 404
    await database.dispose()


@pytest.mark.integration
async def test_lab_trace_projects_only_linked_persisted_retrieval_and_query_facts(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    owner = Principal(subject="lab-facts-owner", authentication_method=AuthenticationMethod.LOCAL)
    course = await CourseRepository(database).create(owner, "编译原理")
    document_id = str(uuid4())
    revision_id = str(uuid4())
    query_id = str(uuid4())
    snapshot_id = str(uuid4())
    evidence_id = "evidence-1"

    async with database.session(owner) as session:
        stored_object = StoredObjectModel(
            id=str(uuid4()),
            user_id=course.user_id,
            course_id=course.id,
            object_key=f"tests/{course.id}/{document_id}.pdf",
            purpose="original",
            sha256="b" * 64,
            size_bytes=100,
            media_type="application/pdf",
        )
        session.add(stored_object)
        await session.flush()
        document = DocumentModel(
            id=document_id,
            user_id=course.user_id,
            course_id=course.id,
            stored_object_id=stored_object.id,
            filename="compiler.pdf",
            media_type="application/pdf",
            corpus_role="corpus",
            verified_sha256="b" * 64,
            status="ready",
            deletion_epoch=0,
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentRevisionModel(
                id=revision_id,
                document_id=document.id,
                ordinal=1,
                manifest={},
                canonical_sha256="c" * 64,
                total_page_count=1,
                parser_profile="native-v1",
                parser_schema_version="1.0",
                chunker_version="section-page-v1",
                quality_status="passed",
            )
        )
        await session.flush()
        document.active_revision_id = revision_id
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
                raw_result_ref="artifact://page-1",
                quality={"status": "passed"},
            )
        )
        lexical_manifest = LexicalManifestModel(
            id=str(uuid4()),
            user_id=course.user_id,
            course_id=course.id,
            version_id="lexical-v1",
            storage_path=f"tests/{course.id}/lexical-v1",
            manifest_hash="d" * 64,
            document_set_hash="e" * 64,
            tokenizer_version="jieba-v9",
            dictionary_hash="f" * 64,
            chunk_count=1,
            document_ids=[document_id],
            revision_ids=[revision_id],
            status="active",
        )
        embedding_model = EmbeddingModelModel(
            id=str(uuid4()),
            provider_alias="test",
            model_name="embedding-test",
            dimensions=3,
            distance_function="cosine",
            contract_version="1",
            status="active",
        )
        session.add_all([lexical_manifest, embedding_model])
        await session.flush()
        trace = RetrievalTraceModel(
            id="lab-persisted-trace",
            user_id=course.user_id,
            course_id=course.id,
            query_sha256="a" * 64,
            mode="hybrid",
            scope_document_ids=[document_id],
            embedding_model_id=embedding_model.id,
            dimensions=embedding_model.dimensions,
            lexical_manifest_id=lexical_manifest.id,
            rrf_k=60,
            dense_candidates=[{"chunk_id": evidence_id, "rank": 1, "score": 0.9}],
            lexical_candidates=[],
            fused_candidates=[],
            rerank_candidates=[],
            timings_ms={"total": 9.5},
            reranker_applied=False,
        )
        conversation = ConversationModel(
            id=str(uuid4()),
            user_id=course.user_id,
            course_id=course.id,
            title="实验室测试",
            auto_title_pending=False,
        )
        session.add(conversation)
        await session.flush()
        query = QueryRunModel(
            id=query_id,
            user_id=course.user_id,
            course_id=course.id,
            conversation_id=conversation.id,
            question="什么是词法分析?",
            question_sha256="1" * 64,
            requested_document_ids=[document_id],
            status="answered",
            answer_markdown="词法分析将字符流转换为 token。",
            claims=[{"id": "claim-1", "citation_ids": [evidence_id]}],
            citations=[{"id": evidence_id}],
            usage={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
            cost_microusd=1250,
            trace_id="request-trace-1",
        )
        session.add_all([trace, query])
        await session.flush()
        session.add(
            RetrievalSnapshotModel(
                id=snapshot_id,
                query_id=query.id,
                user_id=course.user_id,
                course_id=course.id,
                retrieval_trace_id=trace.id,
                active_lexical_index_id=lexical_manifest.id,
                active_revision_ids=[revision_id],
                document_epochs={document_id: 0},
                evidence_payload=[],
                candidate_count=1,
            )
        )
        await session.flush()
        session.add(
            AnswerDependencyModel(
                id=str(uuid4()),
                query_id=query.id,
                retrieval_snapshot_id=snapshot_id,
                user_id=course.user_id,
                course_id=course.id,
                evidence_id=evidence_id,
                document_id=document_id,
                revision_id=revision_id,
                chunk_id="chunk-1",
                document_name="compiler.pdf",
                document_deletion_epoch=0,
                content_sha256="2" * 64,
                locator={"kind": "page", "ordinal": 1},
                quote="词法分析",
                bounding_boxes=[],
                provenance=["pdf-native@1"],
                available=True,
            )
        )

    app = create_app(
        settings=_settings(test_database_url, tmp_path),
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(owner),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(f"/api/v1/courses/{course.id}/lab/trace")

    assert response.status_code == 200
    assert response.json()["revision_id"] == revision_id
    assert response.json()["parser_backend"] == "pdf-native"
    assert response.json()["tokenizer_version"] == "jieba-v9"
    assert response.json()["embedding_model"] == "embedding-test"
    assert response.json()["citation_validation"] == "passed"
    assert response.json()["refusal_reason"] is None
    assert response.json()["usage"] == {
        "input_tokens": 120,
        "output_tokens": 30,
        "estimated_cost": 0.00125,
    }
    await database.dispose()


@pytest.mark.integration
async def test_lab_trace_projects_persisted_refusal_and_partial_usage(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    owner = Principal(subject="lab-refusal-owner", authentication_method=AuthenticationMethod.LOCAL)
    course = await CourseRepository(database).create(owner, "计算机网络")
    trace_id = str(uuid4())
    query_id = str(uuid4())

    async with database.session(owner) as session:
        conversation = ConversationModel(
            id=str(uuid4()),
            user_id=course.user_id,
            course_id=course.id,
            title="拒答测试",
            auto_title_pending=False,
        )
        session.add(conversation)
        await session.flush()
        session.add(
            RetrievalTraceModel(
                id=trace_id,
                user_id=course.user_id,
                course_id=course.id,
                query_sha256="3" * 64,
                mode="lexical",
                scope_document_ids=[],
                rrf_k=60,
                dense_candidates=[],
                lexical_candidates=[],
                fused_candidates=[],
                rerank_candidates=[],
                timings_ms={"total": 2.5},
                reranker_applied=False,
            )
        )
        session.add(
            QueryRunModel(
                id=query_id,
                user_id=course.user_id,
                course_id=course.id,
                conversation_id=conversation.id,
                question="什么是拥塞控制?",
                question_sha256="4" * 64,
                requested_document_ids=[],
                status="abstained",
                refusal_code="INSUFFICIENT_EVIDENCE",
                refusal_message="当前课程资料证据不足。",
                usage={"input_tokens": 42},
                trace_id="request-trace-refusal",
            )
        )
        await session.flush()
        session.add(
            RetrievalSnapshotModel(
                id=str(uuid4()),
                query_id=query_id,
                user_id=course.user_id,
                course_id=course.id,
                retrieval_trace_id=trace_id,
                active_revision_ids=[],
                document_epochs={},
                evidence_payload=[],
                candidate_count=0,
            )
        )

    app = create_app(
        settings=_settings(test_database_url, tmp_path),
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(owner),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(f"/api/v1/courses/{course.id}/lab/trace")

    assert response.status_code == 200
    assert response.json()["refusal_reason"] == "INSUFFICIENT_EVIDENCE"
    assert response.json()["citation_validation"] is None
    assert response.json()["usage"] == {
        "input_tokens": 42,
        "output_tokens": None,
        "estimated_cost": None,
    }
    await database.dispose()
