"""Adapter from hybrid retrieval to immutable, citation-ready evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from sqlalchemy import and_, select

from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    CourseModel,
    DocumentModel,
    EmbeddingModelModel,
    LexicalManifestModel,
    RevisionBlockModel,
    RevisionChunkModel,
    RevisionPageModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.answering.types import AuthorizedEvidence
from study_agent.modules.ingestion.index_runner import EmbeddingModelIdentity
from study_agent.modules.retrieval.hybrid import Evidence as RetrievalEvidence
from study_agent.modules.retrieval.hybrid import HybridRetriever
from study_agent.providers.errors import ProviderError, ProviderErrorCode
from study_agent.providers.factory import ProviderRegistry
from study_contracts import BoundingBox, Evidence, SourceLocator


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    active_index: bool
    candidates: tuple[AuthorizedEvidence, ...]
    retrieval_trace_id: str | None = None
    active_lexical_index_id: str | None = None

    def __post_init__(self) -> None:
        if not self.active_index and self.candidates:
            raise ValueError("inactive indexes cannot return evidence")
        if self.candidates and self.active_lexical_index_id is None:
            raise ValueError("evidence requires an active lexical index snapshot")


class QueryEvidence(Protocol):
    async def retrieve(
        self,
        principal: Principal,
        course_id: str,
        question: str,
        *,
        document_ids: frozenset[str] | None,
    ) -> RetrievedEvidence: ...

    async def sources_are_current(
        self,
        principal: Principal,
        course_id: str,
        active_lexical_index_id: str | None,
        candidates: tuple[AuthorizedEvidence, ...],
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class _ActiveIndex:
    manifest_id: str


class PostgresQueryEvidence:
    """Calls providers only after confirming a principal-scoped active index."""

    def __init__(
        self,
        database: Database,
        registry: ProviderRegistry,
        hybrid: HybridRetriever,
    ) -> None:
        self._database = database
        self._registry = registry
        self._hybrid = hybrid

    async def retrieve(
        self,
        principal: Principal,
        course_id: str,
        question: str,
        *,
        document_ids: frozenset[str] | None,
    ) -> RetrievedEvidence:
        active = await self._active_index(principal, course_id)
        if active is None:
            return RetrievedEvidence(active_index=False, candidates=())

        provider = self._registry.embedding()
        contract = await provider.probe()
        model = await self._model_identity(
            principal,
            contract.provider,
            contract.model,
            contract.dimensions,
        )
        if model is None:
            raise ProviderError(
                ProviderErrorCode.EMBEDDING_MODEL_CHANGED,
                provider=contract.provider,
                retryable=False,
            )
        vector = await provider.embed_query(question)
        if len(vector) != model.dimensions:
            raise ProviderError(
                ProviderErrorCode.EMBEDDING_DIMENSION_CHANGED,
                provider=contract.provider,
                retryable=False,
            )
        try:
            evidence_set = await self._hybrid.retrieve(
                principal,
                course_id,
                question,
                query_vector=vector,
                model=model,
                document_ids=document_ids,
                limit=8,
            )
        except LookupError:
            return RetrievedEvidence(active_index=False, candidates=())
        candidates = await self._authorize(principal, course_id, evidence_set.evidence)
        return RetrievedEvidence(
            active_index=True,
            candidates=candidates,
            retrieval_trace_id=evidence_set.trace_id,
            active_lexical_index_id=active.manifest_id,
        )

    async def sources_are_current(
        self,
        principal: Principal,
        course_id: str,
        active_lexical_index_id: str | None,
        candidates: tuple[AuthorizedEvidence, ...],
    ) -> bool:
        if not candidates:
            return True
        if active_lexical_index_id is None:
            return False
        chunk_ids = tuple(item.evidence.chunk_id for item in candidates)
        async with self._database.session(principal) as session:
            manifest_id = await session.scalar(
                select(CourseModel.active_lexical_index_id)
                .join(UserModel, UserModel.id == CourseModel.user_id)
                .where(
                    CourseModel.id == course_id,
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            )
            if manifest_id != active_lexical_index_id:
                return False
            rows = (
                await session.execute(
                    select(
                        RevisionChunkModel.id,
                        RevisionChunkModel.content_sha256,
                        DocumentModel.id.label("document_id"),
                        DocumentModel.active_revision_id,
                        DocumentModel.deletion_epoch,
                    )
                    .join(
                        DocumentModel,
                        and_(
                            DocumentModel.active_revision_id == RevisionChunkModel.revision_id,
                            DocumentModel.course_id == course_id,
                        ),
                    )
                    .join(CourseModel, CourseModel.id == DocumentModel.course_id)
                    .join(UserModel, UserModel.id == CourseModel.user_id)
                    .where(
                        RevisionChunkModel.id.in_(chunk_ids),
                        DocumentModel.deleted_at.is_(None),
                        CourseModel.deleted_at.is_(None),
                        UserModel.subject == principal.subject,
                        UserModel.authentication_method == principal.authentication_method.value,
                    )
                    .with_for_update(of=DocumentModel)
                )
            ).all()
        by_chunk = {str(row.id): row for row in rows}
        for item in candidates:
            row = by_chunk.get(item.evidence.chunk_id)
            if row is None:
                return False
            if str(row.document_id) != item.evidence.document_id:
                return False
            if str(row.active_revision_id) != item.evidence.revision_id:
                return False
            if int(row.deletion_epoch) != item.document_deletion_epoch:
                return False
            if str(row.content_sha256) != item.evidence.content_sha256:
                return False
        return True

    async def _active_index(self, principal: Principal, course_id: str) -> _ActiveIndex | None:
        async with self._database.session(principal) as session:
            manifest_id = await session.scalar(
                select(LexicalManifestModel.id)
                .join(
                    CourseModel,
                    and_(
                        CourseModel.id == LexicalManifestModel.course_id,
                        CourseModel.active_lexical_index_id == LexicalManifestModel.id,
                    ),
                )
                .join(UserModel, UserModel.id == CourseModel.user_id)
                .where(
                    CourseModel.id == course_id,
                    CourseModel.deleted_at.is_(None),
                    LexicalManifestModel.status == "active",
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            )
        return None if manifest_id is None else _ActiveIndex(str(manifest_id))

    async def _model_identity(
        self,
        principal: Principal,
        provider: str,
        model_name: str,
        dimensions: int,
    ) -> EmbeddingModelIdentity | None:
        async with self._database.session(principal) as session:
            model = cast(
                EmbeddingModelModel | None,
                await session.scalar(
                    select(EmbeddingModelModel)
                    .where(
                        EmbeddingModelModel.provider_alias == provider,
                        EmbeddingModelModel.model_name == model_name,
                        EmbeddingModelModel.dimensions == dimensions,
                        EmbeddingModelModel.distance_function == "cosine",
                        EmbeddingModelModel.status == "active",
                    )
                    .order_by(EmbeddingModelModel.created_at.desc(), EmbeddingModelModel.id)
                    .limit(1)
                ),
            )
        if model is None:
            return None
        return EmbeddingModelIdentity(
            id=model.id,
            provider=model.provider_alias,
            model=model.model_name,
            dimensions=model.dimensions,
            distance=model.distance_function,
            contract_version=model.contract_version,
        )

    async def _authorize(
        self,
        principal: Principal,
        course_id: str,
        retrieved: tuple[RetrievalEvidence, ...],
    ) -> tuple[AuthorizedEvidence, ...]:
        if not retrieved:
            return ()
        chunk_ids = tuple(item.chunk_id for item in retrieved)
        async with self._database.session(principal) as session:
            rows = (
                await session.execute(
                    select(
                        RevisionChunkModel,
                        DocumentModel.filename,
                        DocumentModel.id.label("document_id"),
                        DocumentModel.deletion_epoch,
                        RevisionPageModel.source_backend,
                        RevisionPageModel.source_version,
                        RevisionPageModel.bbox_norm.label("page_bbox"),
                    )
                    .join(
                        DocumentModel,
                        and_(
                            DocumentModel.active_revision_id == RevisionChunkModel.revision_id,
                            DocumentModel.course_id == course_id,
                        ),
                    )
                    .join(CourseModel, CourseModel.id == DocumentModel.course_id)
                    .join(UserModel, UserModel.id == CourseModel.user_id)
                    .join(
                        RevisionPageModel,
                        and_(
                            RevisionPageModel.revision_id == RevisionChunkModel.revision_id,
                            RevisionPageModel.page_ordinal == RevisionChunkModel.page_ordinal,
                        ),
                    )
                    .where(
                        RevisionChunkModel.id.in_(chunk_ids),
                        DocumentModel.deleted_at.is_(None),
                        CourseModel.deleted_at.is_(None),
                        UserModel.subject == principal.subject,
                        UserModel.authentication_method == principal.authentication_method.value,
                    )
                )
            ).all()
            source_ids = {str(block_id) for row in rows for block_id in row[0].source_block_ids}
            blocks = (
                (
                    await session.scalars(
                        select(RevisionBlockModel).where(
                            RevisionBlockModel.revision_id.in_(
                                tuple(str(row[0].revision_id) for row in rows)
                            ),
                            RevisionBlockModel.block_id.in_(source_ids),
                        )
                    )
                ).all()
                if source_ids
                else []
            )
        block_map = {(item.revision_id, item.block_id): item for item in blocks}
        row_by_chunk = {str(row[0].id): row for row in rows}
        retrieved_by_chunk = {item.chunk_id: item for item in retrieved}
        authorized: list[AuthorizedEvidence] = []
        for chunk_id in chunk_ids:
            row = row_by_chunk.get(chunk_id)
            source = retrieved_by_chunk.get(chunk_id)
            if row is None or source is None:
                continue
            chunk = row[0]
            source_blocks = [
                block_map[(chunk.revision_id, block_id)]
                for block_id in chunk.source_block_ids
                if (chunk.revision_id, block_id) in block_map
            ]
            bounding_boxes = [
                BoundingBox.model_validate(block.bbox_norm) for block in source_blocks
            ]
            provenance = tuple(
                sorted(
                    {f"{block.source_backend}@{block.source_version}" for block in source_blocks}
                )
            )
            if not provenance:
                provenance = (f"{row.source_backend}@{row.source_version}",)
            if not bounding_boxes:
                bounding_boxes = [BoundingBox.model_validate(row.page_bbox)]
            score = source.rerank_score if source.rerank_score is not None else source.fused_score
            authorized.append(
                AuthorizedEvidence(
                    evidence=Evidence(
                        id=chunk_id,
                        course_id=course_id,
                        document_id=str(row.document_id),
                        revision_id=str(chunk.revision_id),
                        chunk_id=chunk_id,
                        text=str(chunk.text),
                        content_sha256=str(chunk.content_sha256),
                        locator=SourceLocator(
                            kind=str(chunk.locator_kind), ordinal=int(chunk.page_ordinal)
                        ),
                        bounding_boxes=bounding_boxes,
                    ),
                    document_name=str(row.filename),
                    score=float(score),
                    document_deletion_epoch=int(row.deletion_epoch),
                    provenance=provenance,
                )
            )
        return tuple(authorized)
