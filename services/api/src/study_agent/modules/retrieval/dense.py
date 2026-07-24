"""Exact pgvector retrieval with mandatory principal and course scope."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, select

from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    ChunkEmbeddingModel,
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    RevisionChunkModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.ingestion.index_runner import EmbeddingModelIdentity


@dataclass(frozen=True, slots=True)
class DenseHit:
    chunk_id: str
    document_id: str
    revision_id: str
    text: str
    locator_kind: str
    page_ordinal: int
    section_path: tuple[str, ...]
    distance: float
    score: float


class DenseRetriever:
    """Uses an exact ORDER BY vector operator; no ANN index is assumed."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def retrieve(
        self,
        principal: Principal,
        course_id: str,
        query_vector: list[float],
        *,
        model: EmbeddingModelIdentity,
        document_ids: frozenset[str] | None = None,
        limit: int = 20,
    ) -> tuple[DenseHit, ...]:
        if len(query_vector) != model.dimensions:
            raise ValueError("query vector dimension does not match embedding model")
        if model.distance != "cosine":
            raise ValueError("only exact cosine retrieval is enabled")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if document_ids is not None and not document_ids:
            return ()

        distance = ChunkEmbeddingModel.embedding.cosine_distance(query_vector).label("distance")
        statement = (
            select(
                RevisionChunkModel.id,
                DocumentModel.id.label("document_id"),
                RevisionChunkModel.revision_id,
                RevisionChunkModel.text,
                RevisionChunkModel.locator_kind,
                RevisionChunkModel.page_ordinal,
                RevisionChunkModel.section_path,
                distance,
            )
            .join(
                ChunkEmbeddingModel,
                and_(
                    ChunkEmbeddingModel.chunk_id == RevisionChunkModel.id,
                    ChunkEmbeddingModel.revision_id == RevisionChunkModel.revision_id,
                ),
            )
            .join(
                DocumentRevisionModel,
                and_(
                    DocumentRevisionModel.id == RevisionChunkModel.revision_id,
                    DocumentRevisionModel.document_id == ChunkEmbeddingModel.document_id,
                ),
            )
            .join(
                DocumentModel,
                and_(
                    DocumentModel.id == DocumentRevisionModel.document_id,
                    DocumentModel.course_id == ChunkEmbeddingModel.course_id,
                    DocumentModel.user_id == ChunkEmbeddingModel.user_id,
                    DocumentModel.active_revision_id == DocumentRevisionModel.id,
                ),
            )
            .join(
                CourseModel,
                and_(
                    CourseModel.id == DocumentModel.course_id,
                    CourseModel.user_id == DocumentModel.user_id,
                ),
            )
            .join(UserModel, UserModel.id == CourseModel.user_id)
            .where(
                UserModel.subject == principal.subject,
                UserModel.authentication_method == principal.authentication_method.value,
                CourseModel.id == course_id,
                CourseModel.deleted_at.is_(None),
                DocumentModel.deleted_at.is_(None),
                DocumentModel.corpus_role == "corpus",
                DocumentModel.review_status == "approved",
                ChunkEmbeddingModel.embedding_model_id == model.id,
                ChunkEmbeddingModel.dimensions == model.dimensions,
                ChunkEmbeddingModel.course_id == course_id,
            )
            .order_by(distance, RevisionChunkModel.id)
            .limit(limit)
        )
        if document_ids is not None:
            statement = statement.where(DocumentModel.id.in_(document_ids))
        async with self._database.session(principal) as session:
            rows = (await session.execute(statement)).all()
        return tuple(
            DenseHit(
                chunk_id=str(row.id),
                document_id=str(row.document_id),
                revision_id=str(row.revision_id),
                text=str(row.text),
                locator_kind=str(row.locator_kind),
                page_ordinal=int(row.page_ordinal),
                section_path=tuple(row.section_path),
                distance=float(row.distance),
                score=1.0 - float(row.distance),
            )
            for row in rows
        )
