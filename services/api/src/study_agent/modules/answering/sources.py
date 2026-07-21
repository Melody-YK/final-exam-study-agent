"""Principal-scoped citation source resolution with deletion-safe signing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    AnswerDependencyModel,
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    JobArtifactModel,
    QueryRunModel,
    RevisionAssetModel,
    StoredObjectModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.providers.protocols import ObjectStorage
from study_contracts import AssetType, BoundingBox, SourceLocator

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class CitationPreviewUnavailable(RuntimeError):
    """A valid citation has no safe persisted preview representation."""


@dataclass(frozen=True, slots=True)
class CitationSource:
    citation_id: str
    document_id: str
    revision_id: str
    chunk_id: str
    document_name: str
    locator: SourceLocator
    quote: str
    bounding_boxes: tuple[BoundingBox, ...]
    provenance: tuple[str, ...]
    object_key: str
    media_type: str
    read_url: str
    read_url_expires_at: datetime


class CitationSourceService:
    def __init__(self, database: Database, storage: ObjectStorage) -> None:
        self._database = database
        self._storage = storage

    async def get(
        self,
        principal: Principal,
        query_id: str,
        citation_id: str,
    ) -> CitationSource | None:
        async with self._database.session(principal) as session:
            row = (
                (
                    await session.execute(
                        select(
                            AnswerDependencyModel,
                            DocumentModel,
                            StoredObjectModel,
                        )
                        .join(QueryRunModel, QueryRunModel.id == AnswerDependencyModel.query_id)
                        .join(CourseModel, CourseModel.id == QueryRunModel.course_id)
                        .join(UserModel, UserModel.id == QueryRunModel.user_id)
                        .join(
                            DocumentModel,
                            and_(
                                DocumentModel.id == AnswerDependencyModel.document_id,
                                DocumentModel.course_id == AnswerDependencyModel.course_id,
                                DocumentModel.user_id == AnswerDependencyModel.user_id,
                            ),
                        )
                        .join(
                            StoredObjectModel,
                            StoredObjectModel.id == DocumentModel.stored_object_id,
                        )
                        .where(
                            AnswerDependencyModel.query_id == query_id,
                            AnswerDependencyModel.evidence_id == citation_id,
                            AnswerDependencyModel.available.is_(True),
                            QueryRunModel.status == "answered",
                            CourseModel.deleted_at.is_(None),
                            DocumentModel.deleted_at.is_(None),
                            DocumentModel.deletion_epoch
                            == AnswerDependencyModel.document_deletion_epoch,
                            DocumentModel.active_revision_id == AnswerDependencyModel.revision_id,
                            StoredObjectModel.deleted_at.is_(None),
                            UserModel.subject == principal.subject,
                            UserModel.authentication_method
                            == principal.authentication_method.value,
                        )
                        .with_for_update(of=DocumentModel)
                    )
                )
                .tuples()
                .first()
            )
            if row is None:
                return None
            dependency, document, original_object = row
            locator = SourceLocator.model_validate(dependency.locator)
            needs_rendered_page = self._needs_rendered_page(document, locator)
            if needs_rendered_page:
                rendered_object = await self._rendered_page_object(
                    session,
                    dependency=dependency,
                    locator=locator,
                )
                if rendered_object is None:
                    raise CitationPreviewUnavailable
                stored_object = rendered_object
            else:
                stored_object = original_object
            try:
                signed = await self._storage.sign_read(stored_object.object_key)
            except (FileNotFoundError, OSError):
                if needs_rendered_page:
                    raise CitationPreviewUnavailable from None
                return None
            return CitationSource(
                citation_id=dependency.evidence_id,
                document_id=dependency.document_id,
                revision_id=dependency.revision_id,
                chunk_id=dependency.chunk_id,
                document_name=dependency.document_name,
                locator=locator,
                quote=dependency.quote,
                bounding_boxes=tuple(
                    BoundingBox.model_validate(item) for item in dependency.bounding_boxes
                ),
                provenance=tuple(dependency.provenance),
                object_key=stored_object.object_key,
                media_type=stored_object.media_type,
                read_url=signed.url,
                read_url_expires_at=signed.expires_at,
            )

    @staticmethod
    def _needs_rendered_page(document: DocumentModel, locator: SourceLocator) -> bool:
        return (
            locator.kind == "slide"
            or document.media_type == PPTX_MEDIA_TYPE
            or document.filename.lower().endswith(".pptx")
        )

    @staticmethod
    async def _rendered_page_object(
        session: AsyncSession,
        *,
        dependency: AnswerDependencyModel,
        locator: SourceLocator,
    ) -> StoredObjectModel | None:
        result = await session.execute(
            select(StoredObjectModel)
            .select_from(RevisionAssetModel)
            .join(
                DocumentRevisionModel,
                DocumentRevisionModel.id == RevisionAssetModel.revision_id,
            )
            .join(
                JobArtifactModel,
                and_(
                    JobArtifactModel.id == RevisionAssetModel.object_ref,
                    JobArtifactModel.job_id == DocumentRevisionModel.parse_job_id,
                ),
            )
            .join(
                StoredObjectModel,
                StoredObjectModel.id == JobArtifactModel.stored_object_id,
            )
            .where(
                RevisionAssetModel.revision_id == dependency.revision_id,
                RevisionAssetModel.asset_type == AssetType.RENDERED_PAGE.value,
                RevisionAssetModel.locator_kind == locator.kind,
                RevisionAssetModel.page_ordinal == locator.ordinal,
                RevisionAssetModel.media_type == JobArtifactModel.media_type,
                RevisionAssetModel.sha256 == JobArtifactModel.sha256,
                or_(
                    RevisionAssetModel.size_bytes.is_(None),
                    RevisionAssetModel.size_bytes == JobArtifactModel.size_bytes,
                ),
                DocumentRevisionModel.document_id == dependency.document_id,
                JobArtifactModel.user_id == dependency.user_id,
                JobArtifactModel.course_id == dependency.course_id,
                JobArtifactModel.document_id == dependency.document_id,
                JobArtifactModel.deletion_epoch == dependency.document_deletion_epoch,
                JobArtifactModel.status == "available",
                StoredObjectModel.user_id == dependency.user_id,
                StoredObjectModel.course_id == dependency.course_id,
                StoredObjectModel.purpose == "job-artifact",
                StoredObjectModel.sha256 == JobArtifactModel.sha256,
                StoredObjectModel.size_bytes == JobArtifactModel.size_bytes,
                StoredObjectModel.media_type == JobArtifactModel.media_type,
                StoredObjectModel.media_type.like("image/%"),
                StoredObjectModel.deleted_at.is_(None),
            )
            .order_by(RevisionAssetModel.asset_id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()
