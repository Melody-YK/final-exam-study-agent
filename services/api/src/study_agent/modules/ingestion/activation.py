"""Atomic dense, lexical, and revision pointer activation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.infrastructure.db.models import (
    ChunkEmbeddingModel,
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    LexicalManifestModel,
    RevisionChunkModel,
)
from study_agent.modules.ingestion.index_runner import EmbeddingModelIdentity


class ActivationError(RuntimeError):
    """Candidate data is incomplete or no longer matches the locked scope."""


class ActivationService:
    async def activate(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        course_id: str,
        document_id: str,
        revision_id: str,
        model: EmbeddingModelIdentity,
        lexical_manifest_id: str,
    ) -> None:
        await self._switch(
            session,
            user_id=user_id,
            course_id=course_id,
            document_id=document_id,
            revision_id=revision_id,
            model=model,
            lexical_manifest_id=lexical_manifest_id,
            require_preview=True,
        )

    async def rollback(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        course_id: str,
        document_id: str,
        revision_id: str,
        model: EmbeddingModelIdentity,
        lexical_manifest_id: str,
    ) -> None:
        await self._switch(
            session,
            user_id=user_id,
            course_id=course_id,
            document_id=document_id,
            revision_id=revision_id,
            model=model,
            lexical_manifest_id=lexical_manifest_id,
            require_preview=False,
        )

    async def _switch(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        course_id: str,
        document_id: str,
        revision_id: str,
        model: EmbeddingModelIdentity,
        lexical_manifest_id: str,
        require_preview: bool,
    ) -> None:
        course = cast(
            CourseModel | None,
            await session.scalar(
                select(CourseModel)
                .where(
                    CourseModel.id == course_id,
                    CourseModel.user_id == user_id,
                    CourseModel.deleted_at.is_(None),
                )
                .with_for_update(of=CourseModel)
            ),
        )
        document = cast(
            DocumentModel | None,
            await session.scalar(
                select(DocumentModel)
                .where(
                    DocumentModel.id == document_id,
                    DocumentModel.course_id == course_id,
                    DocumentModel.user_id == user_id,
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.corpus_role == "corpus",
                )
                .with_for_update(of=DocumentModel)
            ),
        )
        manifest = cast(
            LexicalManifestModel | None,
            await session.scalar(
                select(LexicalManifestModel)
                .where(
                    LexicalManifestModel.id == lexical_manifest_id,
                    LexicalManifestModel.course_id == course_id,
                    LexicalManifestModel.user_id == user_id,
                    LexicalManifestModel.status.in_(("ready", "superseded", "active")),
                )
                .with_for_update(of=LexicalManifestModel)
            ),
        )
        if course is None or document is None or manifest is None:
            raise ActivationError("activation scope or lexical manifest is unavailable")
        revision_exists = await session.scalar(
            select(DocumentRevisionModel.id).where(
                DocumentRevisionModel.id == revision_id,
                DocumentRevisionModel.document_id == document_id,
            )
        )
        if revision_exists is None:
            raise ActivationError("candidate revision does not belong to document")
        if require_preview and document.preview_revision_id != revision_id:
            raise ActivationError("candidate revision is no longer the document preview")

        documents = list(
            await session.scalars(
                select(DocumentModel)
                .where(
                    DocumentModel.user_id == user_id,
                    DocumentModel.course_id == course_id,
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.corpus_role == "corpus",
                )
                .order_by(DocumentModel.id)
                .with_for_update(of=DocumentModel)
            )
        )
        expected: dict[str, str] = {}
        for item in documents:
            selected_revision = revision_id if item.id == document_id else item.active_revision_id
            if selected_revision is not None:
                expected[item.id] = selected_revision
        if set(manifest.document_ids) != set(expected):
            raise ActivationError("lexical manifest document scope is stale")
        if set(manifest.revision_ids) != set(expected.values()):
            raise ActivationError("lexical manifest revision set is stale")

        revision_ids = tuple(expected.values())
        chunk_count = int(
            await session.scalar(
                select(func.count())
                .select_from(RevisionChunkModel)
                .where(RevisionChunkModel.revision_id.in_(revision_ids))
            )
            or 0
        )
        dense_count = int(
            await session.scalar(
                select(func.count())
                .select_from(ChunkEmbeddingModel)
                .join(
                    RevisionChunkModel,
                    and_(
                        RevisionChunkModel.id == ChunkEmbeddingModel.chunk_id,
                        RevisionChunkModel.revision_id == ChunkEmbeddingModel.revision_id,
                    ),
                )
                .where(
                    ChunkEmbeddingModel.user_id == user_id,
                    ChunkEmbeddingModel.course_id == course_id,
                    ChunkEmbeddingModel.revision_id.in_(revision_ids),
                    ChunkEmbeddingModel.embedding_model_id == model.id,
                    ChunkEmbeddingModel.dimensions == model.dimensions,
                )
            )
            or 0
        )
        if chunk_count == 0 or dense_count != chunk_count:
            raise ActivationError("dense index is incomplete for lexical revision set")
        if manifest.chunk_count != chunk_count:
            raise ActivationError("lexical manifest chunk count is stale")

        if (
            course.active_lexical_index_id is not None
            and course.active_lexical_index_id != manifest.id
        ):
            old_manifest = await session.get(
                LexicalManifestModel,
                course.active_lexical_index_id,
                with_for_update=True,
            )
            if old_manifest is not None:
                old_manifest.status = "superseded"
        now = datetime.now(UTC)
        manifest.status = "active"
        manifest.activated_at = now
        course.active_lexical_index_id = manifest.id
        course.row_version += 1
        document.active_revision_id = revision_id
        if require_preview:
            document.preview_revision_id = None
        document.status = "ready"
