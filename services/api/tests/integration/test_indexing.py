import resource
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from services.api.tests.integration.retrieval_helpers import seed_document_revision
from study_agent.identity.principal import AuthenticationMethod, Principal
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    ChunkEmbeddingModel,
    CourseModel,
    DocumentModel,
    EmbeddingModelModel,
    IndexJobModel,
    LexicalManifestModel,
    RetrievalTraceModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.courses.repository import CourseRepository
from study_agent.modules.ingestion.activation import ActivationError, ActivationService
from study_agent.modules.ingestion.index_repository import PostgresIndexRepository
from study_agent.modules.ingestion.index_runner import IndexRunner, IndexRunStatus
from study_agent.modules.retrieval.bm25_index import Bm25IndexStore
from study_agent.modules.retrieval.dense import DenseRetriever
from study_agent.modules.retrieval.hybrid import (
    HybridRetriever,
    PostgresEvidenceRepository,
)
from study_agent.modules.retrieval.lexical import LexicalRetriever
from study_agent.modules.retrieval.rerank import RerankService
from study_agent.modules.retrieval.tokenizer import ChineseTokenizer
from study_agent.modules.retrieval.trace import PostgresTraceStore
from study_agent.providers.protocols import EmbeddingContract


class InjectedTestEmbedding:
    async def probe(self) -> EmbeddingContract:
        return EmbeddingContract(provider="test", model="tiny", dimensions=2)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, float(index + 1)] for index, _text in enumerate(texts)]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 1.0]


@pytest.mark.integration
async def test_index_job_blocks_without_provider_then_resumes_and_activates(
    test_database_url: str,
    tmp_path: Path,
    record_property: Callable[[str, object], None],
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(subject="index-owner", authentication_method=AuthenticationMethod.LOCAL)
    course = await CourseRepository(database).create(principal, "操作系统")
    async with database.session(principal) as session:
        preview = await seed_document_revision(
            session,
            user_id=course.user_id,
            course_id=course.id,
            text_chunks=["进程调度", "虚拟内存"],
            active=False,
            preview=True,
        )
    repository = PostgresIndexRepository(
        database,
        runner_id="integration-runner",
        requested_provider="test",
        requested_model="tiny",
        contract_version="1",
    )
    store = Bm25IndexStore(tmp_path, ChineseTokenizer(["虚拟内存"]))
    blocked_runner = IndexRunner(repository, store, provider=None, batch_size=1)

    blocked = await blocked_runner.run_once()

    assert blocked is not None
    assert blocked.status is IndexRunStatus.BLOCKED_PROVIDER
    async with database.session(principal) as session:
        job = await session.scalar(select(IndexJobModel))
        document = await session.get(DocumentModel, preview.document_id)
        assert job is not None
        assert job.status == "index_blocked_provider"
        assert job.failure_code == "PROVIDER_NOT_CONFIGURED"
        assert document is not None
        assert document.active_revision_id is None
        assert document.preview_revision_id == preview.revision_id

    resumed_runner = IndexRunner(
        repository,
        store,
        provider=InjectedTestEmbedding(),
        batch_size=1,
    )
    assert await resumed_runner.resume_provider_blocked() == 1
    succeeded = await resumed_runner.run_once()

    assert succeeded is not None
    assert succeeded.status is IndexRunStatus.SUCCEEDED
    async with database.session(principal) as session:
        job = await session.scalar(select(IndexJobModel))
        document = await session.get(DocumentModel, preview.document_id)
        manifest = await session.scalar(select(LexicalManifestModel))
        embedding_count = await session.scalar(
            select(func.count()).select_from(ChunkEmbeddingModel)
        )
        assert job is not None and job.status == "succeeded"
        assert document is not None
        assert document.active_revision_id == preview.revision_id
        assert document.preview_revision_id is None
        assert manifest is not None and manifest.status == "active"
        assert course.id == manifest.course_id
        assert embedding_count == 2

    lexical = LexicalRetriever(database, store)
    lexical_result = await lexical.retrieve(
        principal,
        course.id,
        "虚拟内存",
        limit=10,
    )
    record_property("bm25_mmap_max_rss", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    assert lexical_result.hits[0].chunk_id == preview.chunk_ids[1]

    assert job is not None and job.embedding_model_id is not None and job.dimensions is not None
    from study_agent.modules.ingestion.index_runner import EmbeddingModelIdentity

    model_identity = EmbeddingModelIdentity(
        id=job.embedding_model_id,
        provider="test",
        model="tiny",
        dimensions=job.dimensions,
        distance="cosine",
        contract_version="1",
    )
    hybrid = HybridRetriever(
        dense=DenseRetriever(database),
        lexical=lexical,
        evidence=PostgresEvidenceRepository(database),
        traces=PostgresTraceStore(database),
        reranker=RerankService(enabled=False),
    )
    evidence_set = await hybrid.retrieve(
        principal,
        course.id,
        "虚拟内存",
        query_vector=[1.0, 2.0],
        model=model_identity,
        limit=2,
    )
    async with database.session(principal) as session:
        trace = await session.get(RetrievalTraceModel, evidence_set.trace_id)
        assert trace is not None
        assert trace.embedding_model_id == model_identity.id
        assert trace.lexical_manifest_id == lexical_result.manifest_id
        assert trace.dense_candidates
        assert trace.lexical_candidates
        assert trace.fused_candidates
        assert trace.reranker_applied is False
        assert trace.reranker_fallback_code == "disabled"
    assert evidence_set.evidence
    assert evidence_set.evidence[0].course_id == course.id
    await database.dispose()


@pytest.mark.integration
async def test_activation_failure_preserves_old_active_and_rollback_is_atomic(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject="activation-owner", authentication_method=AuthenticationMethod.LOCAL
    )
    course = await CourseRepository(database).create(principal, "数据库")
    model = EmbeddingModelModel(
        id=str(uuid4()),
        provider_alias="test",
        model_name="tiny",
        dimensions=2,
        distance_function="cosine",
        contract_version="1",
        status="active",
    )
    async with database.session(principal) as session:
        old = await seed_document_revision(
            session,
            user_id=course.user_id,
            course_id=course.id,
            text_chunks=["旧版本"],
            active=True,
            preview=False,
        )
        candidate = await seed_document_revision(
            session,
            user_id=course.user_id,
            course_id=course.id,
            document_id=old.document_id,
            revision_ordinal=2,
            text_chunks=["新版本"],
            active=False,
            preview=True,
        )
        session.add(model)
        old_manifest = LexicalManifestModel(
            id=str(uuid4()),
            user_id=course.user_id,
            course_id=course.id,
            version_id="old",
            storage_path=f"/tmp/{uuid4()}/old",
            manifest_hash="a" * 64,
            document_set_hash="b" * 64,
            tokenizer_version="jieba-test",
            dictionary_hash="c" * 64,
            chunk_count=1,
            document_ids=[old.document_id],
            revision_ids=[old.revision_id],
            status="active",
        )
        new_manifest = LexicalManifestModel(
            id=str(uuid4()),
            user_id=course.user_id,
            course_id=course.id,
            version_id="new",
            storage_path=f"/tmp/{uuid4()}/new",
            manifest_hash="d" * 64,
            document_set_hash="e" * 64,
            tokenizer_version="jieba-test",
            dictionary_hash="f" * 64,
            chunk_count=1,
            document_ids=[candidate.document_id],
            revision_ids=[candidate.revision_id],
            status="ready",
        )
        session.add_all([old_manifest, new_manifest])
        await session.flush()
        course_row = await session.get(CourseModel, course.id)
        assert course_row is not None
        course_row.active_lexical_index_id = old_manifest.id
        session.add(
            ChunkEmbeddingModel(
                id=str(uuid4()),
                user_id=course.user_id,
                course_id=course.id,
                document_id=old.document_id,
                revision_id=old.revision_id,
                chunk_id=old.chunk_ids[0],
                embedding_model_id=model.id,
                dimensions=2,
                embedding=[1.0, 0.0],
            )
        )
    activation = ActivationService()
    from study_agent.modules.ingestion.index_runner import EmbeddingModelIdentity

    model_identity = EmbeddingModelIdentity(
        id=model.id,
        provider="test",
        model="tiny",
        dimensions=2,
        distance="cosine",
        contract_version="1",
    )
    with pytest.raises(ActivationError, match="dense"):
        async with database.worker_session("activation-test") as session:
            await activation.activate(
                session,
                user_id=course.user_id,
                course_id=course.id,
                document_id=candidate.document_id,
                revision_id=candidate.revision_id,
                model=model_identity,
                lexical_manifest_id=new_manifest.id,
            )
    async with database.session(principal) as session:
        document = await session.get(DocumentModel, old.document_id)
        old_after_failure = await session.get(LexicalManifestModel, old_manifest.id)
        assert document is not None
        assert document.active_revision_id == old.revision_id
        assert document.preview_revision_id == candidate.revision_id
        assert old_after_failure is not None and old_after_failure.status == "active"
        session.add(
            ChunkEmbeddingModel(
                id=str(uuid4()),
                user_id=course.user_id,
                course_id=course.id,
                document_id=candidate.document_id,
                revision_id=candidate.revision_id,
                chunk_id=candidate.chunk_ids[0],
                embedding_model_id=model.id,
                dimensions=2,
                embedding=[0.0, 1.0],
            )
        )
    async with database.worker_session("activation-test") as session:
        await activation.activate(
            session,
            user_id=course.user_id,
            course_id=course.id,
            document_id=candidate.document_id,
            revision_id=candidate.revision_id,
            model=model_identity,
            lexical_manifest_id=new_manifest.id,
        )
    async with database.session(principal) as session:
        document = await session.get(DocumentModel, old.document_id)
        assert document is not None and document.active_revision_id == candidate.revision_id

    async with database.worker_session("activation-test") as session:
        await activation.rollback(
            session,
            user_id=course.user_id,
            course_id=course.id,
            document_id=old.document_id,
            revision_id=old.revision_id,
            model=model_identity,
            lexical_manifest_id=old_manifest.id,
        )
    async with database.session(principal) as session:
        document = await session.get(DocumentModel, old.document_id)
        old_after_rollback = await session.get(LexicalManifestModel, old_manifest.id)
        new_after_rollback = await session.get(LexicalManifestModel, new_manifest.id)
        assert document is not None and document.active_revision_id == old.revision_id
        assert old_after_rollback is not None and old_after_rollback.status == "active"
        assert new_after_rollback is not None and new_after_rollback.status == "superseded"
    await database.dispose()
