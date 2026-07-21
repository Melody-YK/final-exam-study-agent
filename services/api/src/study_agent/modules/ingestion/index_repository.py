"""PostgreSQL persistence adapter for the recoverable IndexRunner."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.infrastructure.db.models import (
    ChunkEmbeddingModel,
    DocumentModel,
    EmbeddingModelModel,
    IndexJobModel,
    LexicalManifestModel,
    RevisionChunkModel,
)
from study_agent.infrastructure.db.models.core import new_id
from study_agent.infrastructure.db.session import Database
from study_agent.modules.ingestion.activation import ActivationService
from study_agent.modules.ingestion.index_runner import (
    EmbeddingModelIdentity,
    IndexChunk,
    IndexWork,
)
from study_agent.modules.retrieval.bm25_index import BuiltLexicalIndex, LexicalDocument
from study_agent.providers.protocols import EmbeddingContract


class PostgresIndexRepository:
    def __init__(
        self,
        database: Database,
        *,
        runner_id: str,
        requested_provider: str,
        requested_model: str,
        contract_version: str = "1",
        lease_seconds: int = 300,
        activation: ActivationService | None = None,
    ) -> None:
        if not runner_id.strip():
            raise ValueError("runner_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("index lease must be positive")
        self._database = database
        self._runner_id = runner_id
        self._requested_provider = requested_provider
        self._requested_model = requested_model
        self._contract_version = contract_version
        self._lease = timedelta(seconds=lease_seconds)
        self._activation = activation or ActivationService()

    async def claim(self) -> IndexWork | None:
        now = datetime.now(UTC)
        async with self._database.worker_session(self._runner_id) as session:
            await self._enqueue_previews(session, now)
            job = cast(
                IndexJobModel | None,
                await session.scalar(
                    select(IndexJobModel)
                    .where(
                        or_(
                            and_(
                                IndexJobModel.status == "pending",
                                IndexJobModel.available_at <= now,
                            ),
                            and_(
                                IndexJobModel.status == "running",
                                IndexJobModel.lease_expires_at.is_not(None),
                                IndexJobModel.lease_expires_at <= now,
                            ),
                        )
                    )
                    .order_by(
                        IndexJobModel.available_at, IndexJobModel.created_at, IndexJobModel.id
                    )
                    .limit(1)
                    .with_for_update(skip_locked=True, of=IndexJobModel)
                ),
            )
            if job is None:
                return None
            job.status = "running"
            job.attempt_count += 1
            job.runner_id = self._runner_id
            job.lease_expires_at = now + self._lease
            job.failure_code = None
            document = await session.get(DocumentModel, job.document_id)
            if document is None or document.preview_revision_id != job.revision_id:
                job.status = "failed"
                job.failure_code = "INDEX_STALE_PREVIEW"
                job.lease_expires_at = None
                return None
            document.status = "indexing"
            return self._work(job)

    async def block_provider(self, work: IndexWork, code: str) -> None:
        async with self._database.worker_session(self._runner_id) as session:
            job = await self._locked_running(session, work)
            job.status = "index_blocked_provider"
            job.failure_code = code
            self._clear_lease(job)
            await self._set_document_blocked(session, work)

    async def fail(self, work: IndexWork, code: str) -> None:
        async with self._database.worker_session(self._runner_id) as session:
            job = await self._locked_running(session, work)
            job.status = "failed"
            job.failure_code = code
            self._clear_lease(job)
            await self._set_document_blocked(session, work)

    async def ensure_model(
        self,
        work: IndexWork,
        contract: EmbeddingContract,
    ) -> EmbeddingModelIdentity:
        async with self._database.worker_session(self._runner_id) as session:
            job = await self._locked_running(session, work)
            model = cast(
                EmbeddingModelModel | None,
                await session.scalar(
                    select(EmbeddingModelModel).where(
                        EmbeddingModelModel.provider_alias == contract.provider,
                        EmbeddingModelModel.model_name == contract.model,
                        EmbeddingModelModel.dimensions == contract.dimensions,
                        EmbeddingModelModel.distance_function == "cosine",
                        EmbeddingModelModel.contract_version == work.contract_version,
                    )
                ),
            )
            if model is None:
                model = EmbeddingModelModel(
                    id=new_id(),
                    provider_alias=contract.provider,
                    model_name=contract.model,
                    dimensions=contract.dimensions,
                    distance_function="cosine",
                    contract_version=work.contract_version,
                    status="active",
                )
                session.add(model)
                await session.flush()
            job.embedding_model_id = model.id
            job.dimensions = model.dimensions
            return self._identity(model)

    async def chunks(self, work: IndexWork) -> list[IndexChunk]:
        async with self._database.worker_session(self._runner_id) as session:
            await self._locked_running(session, work)
            rows = list(
                await session.scalars(
                    select(RevisionChunkModel)
                    .where(RevisionChunkModel.revision_id == work.revision_id)
                    .order_by(RevisionChunkModel.ordinal, RevisionChunkModel.id)
                )
            )
            return [
                IndexChunk(id=row.id, text=row.text, content_sha256=row.content_sha256)
                for row in rows
            ]

    async def replace_embeddings(
        self,
        work: IndexWork,
        model: EmbeddingModelIdentity,
        chunks: Sequence[IndexChunk],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunk and embedding counts differ")
        async with self._database.worker_session(self._runner_id) as session:
            await self._locked_running(session, work)
            await session.execute(
                delete(ChunkEmbeddingModel).where(
                    ChunkEmbeddingModel.revision_id == work.revision_id,
                    ChunkEmbeddingModel.embedding_model_id == model.id,
                )
            )
            session.add_all(
                [
                    ChunkEmbeddingModel(
                        id=new_id(),
                        user_id=work.user_id,
                        course_id=work.course_id,
                        document_id=work.document_id,
                        revision_id=work.revision_id,
                        chunk_id=chunk.id,
                        embedding_model_id=model.id,
                        dimensions=model.dimensions,
                        embedding=list(vector),
                    )
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ]
            )

    async def lexical_documents(self, work: IndexWork) -> list[LexicalDocument]:
        async with self._database.worker_session(self._runner_id) as session:
            await self._locked_running(session, work)
            documents = list(
                await session.scalars(
                    select(DocumentModel)
                    .where(
                        DocumentModel.user_id == work.user_id,
                        DocumentModel.course_id == work.course_id,
                        DocumentModel.deleted_at.is_(None),
                        DocumentModel.corpus_role == "corpus",
                    )
                    .order_by(DocumentModel.id)
                )
            )
            revision_to_document: dict[str, str] = {}
            for document in documents:
                revision_id = (
                    work.revision_id
                    if document.id == work.document_id
                    else document.active_revision_id
                )
                if revision_id is not None:
                    revision_to_document[revision_id] = document.id
            if not revision_to_document:
                return []
            chunks = list(
                await session.scalars(
                    select(RevisionChunkModel)
                    .where(RevisionChunkModel.revision_id.in_(revision_to_document))
                    .order_by(RevisionChunkModel.revision_id, RevisionChunkModel.ordinal)
                )
            )
            return [
                LexicalDocument(
                    chunk_id=chunk.id,
                    user_id=work.user_id,
                    course_id=work.course_id,
                    document_id=revision_to_document[chunk.revision_id],
                    revision_id=chunk.revision_id,
                    text=chunk.text,
                    content_sha256=chunk.content_sha256,
                )
                for chunk in chunks
            ]

    async def register_lexical(
        self,
        work: IndexWork,
        built: BuiltLexicalIndex,
    ) -> str:
        async with self._database.worker_session(self._runner_id) as session:
            await self._locked_running(session, work)
            manifest_id = new_id()
            session.add(
                LexicalManifestModel(
                    id=manifest_id,
                    user_id=work.user_id,
                    course_id=work.course_id,
                    version_id=built.version_id,
                    storage_path=str(Path(built.storage_path)),
                    manifest_hash=built.manifest_hash,
                    document_set_hash=built.document_set_hash,
                    tokenizer_version=built.tokenizer_version,
                    dictionary_hash=built.dictionary_hash,
                    chunk_count=built.chunk_count,
                    document_ids=list(built.document_ids),
                    revision_ids=list(built.revision_ids),
                    status="ready",
                )
            )
            return manifest_id

    async def activate(
        self,
        work: IndexWork,
        model: EmbeddingModelIdentity,
        manifest_id: str,
    ) -> None:
        async with self._database.worker_session(self._runner_id) as session:
            job = await self._locked_running(session, work)
            await self._activation.activate(
                session,
                user_id=work.user_id,
                course_id=work.course_id,
                document_id=work.document_id,
                revision_id=work.revision_id,
                model=model,
                lexical_manifest_id=manifest_id,
            )
            job.status = "succeeded"
            job.failure_code = None
            job.completed_at = datetime.now(UTC)
            self._clear_lease(job)

    async def resume_provider_blocked(self) -> int:
        now = datetime.now(UTC)
        async with self._database.worker_session(self._runner_id) as session:
            jobs = list(
                await session.scalars(
                    select(IndexJobModel)
                    .where(
                        IndexJobModel.status == "index_blocked_provider",
                        IndexJobModel.requested_provider == self._requested_provider,
                        IndexJobModel.requested_model == self._requested_model,
                        IndexJobModel.contract_version == self._contract_version,
                    )
                    .with_for_update(skip_locked=True, of=IndexJobModel)
                )
            )
            for job in jobs:
                job.status = "pending"
                job.available_at = now
                job.failure_code = None
                self._clear_lease(job)
            return len(jobs)

    async def _enqueue_previews(self, session: AsyncSession, now: datetime) -> None:
        previews = (
            await session.execute(
                select(
                    DocumentModel.user_id,
                    DocumentModel.course_id,
                    DocumentModel.id,
                    DocumentModel.preview_revision_id,
                ).where(
                    DocumentModel.preview_revision_id.is_not(None),
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.corpus_role == "corpus",
                    DocumentModel.status.in_(("parsed_index_blocked", "indexing")),
                )
            )
        ).all()
        for user_id, course_id, document_id, revision_id in previews:
            statement = (
                insert(IndexJobModel)
                .values(
                    id=new_id(),
                    user_id=user_id,
                    course_id=course_id,
                    document_id=document_id,
                    revision_id=revision_id,
                    requested_provider=self._requested_provider,
                    requested_model=self._requested_model,
                    contract_version=self._contract_version,
                    status="pending",
                    attempt_count=0,
                    available_at=now,
                )
                .on_conflict_do_nothing(constraint="uq_index_jobs_revision_model")
            )
            await session.execute(statement)

    async def _locked_running(
        self,
        session: AsyncSession,
        work: IndexWork,
    ) -> IndexJobModel:
        job = cast(
            IndexJobModel | None,
            await session.scalar(
                select(IndexJobModel)
                .where(
                    IndexJobModel.id == work.job_id,
                    IndexJobModel.status == "running",
                    IndexJobModel.runner_id == self._runner_id,
                    IndexJobModel.attempt_count == work.attempt,
                )
                .with_for_update(of=IndexJobModel)
            ),
        )
        if job is None:
            raise RuntimeError("index job lease is no longer active")
        return job

    async def _set_document_blocked(self, session: AsyncSession, work: IndexWork) -> None:
        document = await session.get(DocumentModel, work.document_id, with_for_update=True)
        if document is not None and document.preview_revision_id == work.revision_id:
            document.status = "parsed_index_blocked"

    @staticmethod
    def _clear_lease(job: IndexJobModel) -> None:
        job.lease_expires_at = None
        job.runner_id = None

    @staticmethod
    def _work(job: IndexJobModel) -> IndexWork:
        return IndexWork(
            job_id=job.id,
            attempt=job.attempt_count,
            user_id=job.user_id,
            course_id=job.course_id,
            document_id=job.document_id,
            revision_id=job.revision_id,
            requested_provider=job.requested_provider,
            requested_model=job.requested_model,
            contract_version=job.contract_version,
        )

    @staticmethod
    def _identity(model: EmbeddingModelModel) -> EmbeddingModelIdentity:
        return EmbeddingModelIdentity(
            id=model.id,
            provider=model.provider_alias,
            model=model.model_name,
            dimensions=model.dimensions,
            distance=model.distance_function,
            contract_version=model.contract_version,
        )
