import json
from datetime import timedelta
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
    CourseScope,
    LocalPrincipalProvider,
    Principal,
)
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    AnswerDependencyModel,
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    LexicalManifestModel,
    QueryRunModel,
    RetrievalSnapshotModel,
    RevisionChunkModel,
    RevisionPageModel,
    StoredObjectModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.answering.queries import QueryRepository
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
            review_status="approved",
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
        self.questions: list[str] = []

    async def retrieve(
        self,
        _principal: object,
        _course_id: str,
        question: str,
        *,
        document_ids: frozenset[str] | None,
    ) -> RetrievedEvidence:
        self.calls += 1
        self.questions.append(question)
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


class StaticPrincipalProvider:
    def __init__(self, principal: Principal) -> None:
        self._principal = principal

    def resolve(self, _client_host: str) -> Principal:
        return self._principal


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
async def test_conversations_group_queries_and_remain_course_and_principal_scoped(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    provider = RecordingChatProvider()
    evidence = FakeQueryEvidence(RetrievedEvidence(active_index=False, candidates=()))
    settings = _settings(test_database_url, tmp_path)
    owner = Principal(subject="history-owner", authentication_method=AuthenticationMethod.LOCAL)
    outsider = Principal(
        subject="history-outsider",
        authentication_method=AuthenticationMethod.LOCAL,
    )

    owner_app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(owner),
        provider_registry=_registry(provider),
        query_evidence=evidence,
    )
    async with AsyncClient(
        transport=ASGITransport(app=owner_app), base_url="http://testserver"
    ) as client:
        course = await client.post("/api/v1/courses", json={"title": "操作系统"})
        other_course = await client.post("/api/v1/courses", json={"title": "数据库"})
        course_id = course.json()["id"]
        other_course_id = other_course.json()["id"]
        conversation = await client.post(
            f"/api/v1/courses/{course_id}/conversations",
            json={"title": "进程复习"},
        )
        new_conversation = await client.post(
            f"/api/v1/courses/{course_id}/conversations",
            json={},
        )
        conversation_id = conversation.json()["id"]
        created = []
        for question in ("什么是进程?", "什么是临界区?"):
            created.append(
                (
                    await client.post(
                        f"/api/v1/courses/{course_id}/queries",
                        json={
                            "question": question,
                            "conversation_id": conversation_id,
                        },
                    )
                ).json()
            )
        other_conversation = await client.post(
            f"/api/v1/courses/{other_course_id}/conversations",
            json={"title": "事务复习"},
        )
        other = await client.post(
            f"/api/v1/courses/{other_course_id}/queries",
            json={
                "question": "什么是事务?",
                "conversation_id": other_conversation.json()["id"],
            },
        )
        wrong_course = await client.post(
            f"/api/v1/courses/{other_course_id}/queries",
            json={"question": "越权问题", "conversation_id": conversation_id},
        )
        missing_conversation = await client.post(
            f"/api/v1/courses/{course_id}/queries",
            json={"question": "缺失会话", "conversation_id": "missing-conversation"},
        )

    assert conversation.status_code == 201
    assert conversation.json()["title"] == "进程复习"
    assert new_conversation.status_code == 201
    assert new_conversation.json()["title"] == "新会话"
    assert [item["conversation_id"] for item in created] == [conversation_id, conversation_id]
    assert wrong_course.status_code == 404
    assert missing_conversation.status_code == 404

    reloaded_owner_app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(owner),
        provider_registry=_registry(provider),
        query_evidence=evidence,
    )
    outsider_app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(outsider),
        provider_registry=_registry(provider),
        query_evidence=evidence,
    )
    async with AsyncClient(
        transport=ASGITransport(app=reloaded_owner_app), base_url="http://testserver"
    ) as client:
        conversations = await client.get(f"/api/v1/courses/{course_id}/conversations")
        thread = await client.get(f"/api/v1/conversations/{conversation_id}/queries")
        limited = await client.get(f"/api/v1/conversations/{conversation_id}/queries?limit=1")
        compatibility_history = await client.get(f"/api/v1/courses/{course_id}/queries")
        isolated_course = await client.get(f"/api/v1/courses/{other_course_id}/conversations")
        missing = await client.get("/api/v1/conversations/missing-conversation/queries")
        below_bound = await client.get(f"/api/v1/conversations/{conversation_id}/queries?limit=0")
        above_bound = await client.get(f"/api/v1/conversations/{conversation_id}/queries?limit=101")
    async with AsyncClient(
        transport=ASGITransport(app=outsider_app), base_url="http://testserver"
    ) as client:
        hidden_course = await client.get(f"/api/v1/courses/{course_id}/conversations")
        hidden_thread = await client.get(f"/api/v1/conversations/{conversation_id}/queries")

    by_id = {item["id"]: item for item in conversations.json()}
    assert conversations.status_code == 200
    assert set(by_id) == {conversation_id, new_conversation.json()["id"]}
    assert by_id[conversation_id]["turn_count"] == 2
    assert by_id[conversation_id]["latest_query_id"] == created[-1]["id"]
    assert thread.status_code == 200
    assert [item["id"] for item in thread.json()] == [item["id"] for item in created]
    assert limited.status_code == 200
    assert [item["id"] for item in limited.json()] == [created[-1]["id"]]
    assert compatibility_history.status_code == 200
    assert {item["id"] for item in compatibility_history.json()} == {item["id"] for item in created}
    assert isolated_course.status_code == 200
    assert [item["id"] for item in isolated_course.json()] == [other_conversation.json()["id"]]
    assert other.json()["conversation_id"] == other_conversation.json()["id"]
    assert missing.status_code == 404
    assert missing.json()["code"] == "RESOURCE_NOT_FOUND"
    assert hidden_course.status_code == 404
    assert hidden_thread.status_code == 404
    assert below_bound.status_code == 422
    assert above_bound.status_code == 422
    await database.dispose()


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

        async with database.session(principal) as session:
            document = await session.get(DocumentModel, document_id)
            assert document is not None
            document.review_status = "pending"

        explicit_scope = await client.post(
            f"/api/v1/courses/{course_id}/queries",
            json={"question": "还能选择吗?", "document_ids": [document_id]},
        )
        source_changed = await client.post(
            f"/api/v1/courses/{course_id}/queries",
            json={"question": "审核撤回后还能回答吗?"},
        )

        snapshot = await client.get(f"/api/v1/queries/{query['id']}")
        events = await client.get(f"/api/v1/queries/{query['id']}/events?once=true")

    assert snapshot.status_code == 200
    envelopes = _sse_data(events.text)
    deltas = [item for item in envelopes if item["event_type"] == "answer.delta"]
    assert len(deltas) == 1
    assert deltas[0]["data"]["delta"] == "进程是资源分配的基本单位。"  # type: ignore[index]
    assert explicit_scope.status_code == 404
    assert explicit_scope.json()["code"] == "RESOURCE_NOT_FOUND"
    assert source_changed.status_code == 202
    assert source_changed.json()["status"] == "abstained"
    assert source_changed.json()["answer"]["refusal"]["code"] == "SOURCE_CHANGED"
    assert len(provider.requests) == 2
    assert evidence.calls == 2
    await database.dispose()


@pytest.mark.integration
async def test_follow_up_uses_bounded_non_evidence_context_and_new_conversation_resets_it(
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
        course = await client.post("/api/v1/courses", json={"title": "操作系统"})
        course_id = course.json()["id"]
        conversation = await client.post(
            f"/api/v1/courses/{course_id}/conversations",
            json={"title": "进程与线程"},
        )
        conversation_id = conversation.json()["id"]
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
                id="lexical-context",
                user_id=course_record.user_id,
                course_id=course_id,
                version_id="query-context-v1",
                storage_path=str(tmp_path / "lexical-context"),
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
        answerable_result = RetrievedEvidence(
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
            active_lexical_index_id=manifest.id,
        )
        evidence.result = answerable_result

        first = await client.post(
            f"/api/v1/courses/{course_id}/queries",
            json={"question": "什么是进程?", "conversation_id": conversation_id},
        )
        second = await client.post(
            f"/api/v1/courses/{course_id}/queries",
            json={"question": "它和线程有什么区别?", "conversation_id": conversation_id},
        )

        assert first.status_code == 202
        assert second.status_code == 202
        assert evidence.questions[0] == "什么是进程?"
        assert "[NON_EVIDENCE_CONVERSATION_CONTEXT]" in evidence.questions[1]
        assert "User: 什么是进程?" in evidence.questions[1]
        assert "Assistant:" not in evidence.questions[1]
        assert "进程是资源分配的基本单位。" not in evidence.questions[1]
        assert evidence.questions[1].endswith("[CURRENT_QUESTION]\n它和线程有什么区别?")
        assert provider.requests[0].conversation_context == ()
        assert [turn.question for turn in provider.requests[1].conversation_context] == [
            "什么是进程?"
        ]
        assert provider.requests[1].conversation_context[0].answer_markdown == (
            "进程是资源分配的基本单位。"
        )
        assert [passage.id for passage in provider.requests[1].passages] == [chunk_id]

        async with database.session(principal) as session:
            dependency = await session.scalar(
                select(AnswerDependencyModel).where(
                    AnswerDependencyModel.query_id == first.json()["id"]
                )
            )
            assert dependency is not None
            dependency.available = False

        third = await client.post(
            f"/api/v1/courses/{course_id}/queries",
            json={"question": "再举一个例子", "conversation_id": conversation_id},
        )
        assert third.status_code == 202
        third_context = provider.requests[2].conversation_context
        assert [turn.question for turn in third_context] == [
            "什么是进程?",
            "它和线程有什么区别?",
        ]
        assert third_context[0].answer_markdown is None
        assert third_context[1].answer_markdown == "进程是资源分配的基本单位。"

        evidence.result = RetrievedEvidence(
            active_index=True,
            candidates=(),
            retrieval_trace_id=None,
            active_lexical_index_id=manifest.id,
        )
        unsupported = await client.post(
            f"/api/v1/courses/{course_id}/queries",
            json={
                "question": "只根据之前的回答直接作答",
                "conversation_id": conversation_id,
            },
        )
        assert unsupported.status_code == 202
        assert unsupported.json()["status"] == "abstained"
        assert unsupported.json()["answer"]["refusal"]["code"] == "INSUFFICIENT_EVIDENCE"
        assert len(provider.requests) == 3

        repository = QueryRepository(
            database,
            app.state.clock,
            event_retention=timedelta(hours=1),
        )
        assert await repository.recent_context(principal, first.json()["id"]) == ()

        fresh_conversation = await client.post(
            f"/api/v1/courses/{course_id}/conversations",
            json={},
        )
        evidence.result = answerable_result
        fresh = await client.post(
            f"/api/v1/courses/{course_id}/queries",
            json={
                "question": "重新开始",
                "conversation_id": fresh_conversation.json()["id"],
            },
        )
        assert fresh.status_code == 202
        assert evidence.questions[4] == "重新开始"
        assert provider.requests[3].conversation_context == ()

    await database.dispose()


@pytest.mark.integration
async def test_recent_context_enforces_default_turn_and_character_budgets(
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
        course = await client.post("/api/v1/courses", json={"title": "操作系统"})
        course_id = course.json()["id"]
        conversation = await client.post(
            f"/api/v1/courses/{course_id}/conversations",
            json={"title": "上下文边界"},
        )
        conversation_id = conversation.json()["id"]

    assert course.status_code == 201
    assert conversation.status_code == 201
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

    history_questions = [f"历史问题-{index}" for index in range(6)]
    history_answers = [f"历史回答-{index}" for index in range(6)]
    history_ids = [f"context-history-{index}" for index in range(6)]
    current_id = "context-current"
    current_question = "当前问题"
    base_time = app.state.clock.now() - timedelta(minutes=10)

    async with database.session(principal) as session:
        for index, (query_id, question, answer) in enumerate(
            zip(history_ids, history_questions, history_answers, strict=True)
        ):
            created_at = base_time + timedelta(minutes=index)
            query = QueryRunModel(
                id=query_id,
                user_id=course_record.user_id,
                course_id=course_id,
                conversation_id=conversation_id,
                question=question,
                question_sha256=sha256(question.encode()).hexdigest(),
                requested_document_ids=[],
                status="answered",
                answer_schema_version="1.0",
                answer_markdown=answer,
                claims=[{"id": f"claim-{index}", "citation_ids": [chunk_id]}],
                citations=[{"id": chunk_id}],
                usage={},
                trace_id=f"context-trace-{index}",
                event_sequence=0,
                created_at=created_at,
                updated_at=created_at,
                completed_at=created_at,
            )
            session.add(query)
            await session.flush()
            snapshot = RetrievalSnapshotModel(
                id=f"context-snapshot-{index}",
                query_id=query.id,
                user_id=course_record.user_id,
                course_id=course_id,
                retrieval_trace_id=None,
                active_lexical_index_id=None,
                active_revision_ids=[revision_id],
                document_epochs={document_id: 0},
                evidence_payload=[],
                candidate_count=1,
                created_at=created_at,
            )
            session.add(snapshot)
            await session.flush()
            session.add(
                AnswerDependencyModel(
                    id=f"context-dependency-{index}",
                    query_id=query.id,
                    retrieval_snapshot_id=snapshot.id,
                    user_id=course_record.user_id,
                    course_id=course_id,
                    evidence_id=chunk_id,
                    document_id=document_id,
                    revision_id=revision_id,
                    chunk_id=chunk_id,
                    document_name=f"{document_id}.pdf",
                    document_deletion_epoch=0,
                    content_sha256=content_sha256,
                    locator={"kind": "page", "ordinal": 1},
                    quote="进程是资源分配的基本单位",
                    bounding_boxes=[],
                    provenance=["pdf-native@1"],
                    available=True,
                )
            )
            await session.flush()

        current_time = base_time + timedelta(minutes=len(history_ids))
        session.add(
            QueryRunModel(
                id=current_id,
                user_id=course_record.user_id,
                course_id=course_id,
                conversation_id=conversation_id,
                question=current_question,
                question_sha256=sha256(current_question.encode()).hexdigest(),
                requested_document_ids=[],
                status="pending",
                answer_schema_version="1.0",
                answer_markdown="",
                claims=[],
                citations=[],
                usage={},
                trace_id="context-current-trace",
                event_sequence=0,
                created_at=current_time,
                updated_at=current_time,
            )
        )

    repository = QueryRepository(
        database,
        app.state.clock,
        event_retention=timedelta(hours=1),
    )
    short_context = await repository.recent_context(principal, current_id)
    assert [(turn.question, turn.answer_markdown) for turn in short_context] == list(
        zip(history_questions[2:], history_answers[2:], strict=True)
    )
    returned_short_questions = {turn.question for turn in short_context}
    assert returned_short_questions.isdisjoint(history_questions[:2])
    assert current_question not in returned_short_questions

    long_questions = [f"长问题-{index}-" + "问" * 1_200 for index in range(6)]
    long_answers = [f"长回答-{index}-" + "答" * 1_800 for index in range(6)]
    raw_bounded_total = sum(
        min(len(question), 1_000) + min(len(answer), 1_500)
        for question, answer in zip(long_questions[2:], long_answers[2:], strict=True)
    )
    assert raw_bounded_total > 6_000

    async with database.session(principal) as session:
        for query_id, question, answer in zip(
            history_ids,
            long_questions,
            long_answers,
            strict=True,
        ):
            query = await session.get(QueryRunModel, query_id)
            assert query is not None
            query.question = question
            query.question_sha256 = sha256(question.encode()).hexdigest()
            query.answer_markdown = answer

    source_current_context = await repository.recent_context(
        principal,
        current_id,
        max_chars=20_000,
    )
    assert [(turn.question, turn.answer_markdown) for turn in source_current_context] == [
        (question[:1_000], answer[:1_500])
        for question, answer in zip(long_questions[2:], long_answers[2:], strict=True)
    ]

    bounded_context = await repository.recent_context(principal, current_id)
    assert [turn.question for turn in bounded_context] == [
        long_questions[index][:1_000] for index in (3, 4, 5)
    ]
    assert [turn.answer_markdown for turn in bounded_context] == [
        None,
        long_answers[4][:1_500],
        long_answers[5][:1_500],
    ]
    assert all(len(turn.question) <= 1_000 for turn in bounded_context)
    assert all(
        turn.answer_markdown is None or len(turn.answer_markdown) <= 1_500
        for turn in bounded_context
    )
    assert (
        sum(len(turn.question) + len(turn.answer_markdown or "") for turn in bounded_context)
        == 6_000
    )
    assert bounded_context[-1].question == long_questions[-1][:1_000]
    returned_long_questions = {turn.question for turn in bounded_context}
    assert returned_long_questions.isdisjoint({question[:1_000] for question in long_questions[:3]})
    assert current_question not in returned_long_questions
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
