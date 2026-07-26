"""Neutral source previews for notes and knowledge-graph occurrences."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    JobArtifactModel,
    NoteModel,
    NoteSourceModel,
    ParseJobModel,
    RevisionAssetModel,
    RevisionChunkModel,
    StoredObjectModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.courses.upload_validation import (
    MARKDOWN_MEDIA_TYPE,
    MAX_MARKDOWN_UPLOAD_BYTES,
)
from study_agent.providers.protocols import ObjectStorage
from study_contracts import AssetType, BoundingBox, SourceLocator

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_ORIGINAL_PREVIEW_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "text/markdown",
    }
)


class SourcePreviewUnavailableReason(StrEnum):
    RENDERED_PAGE_MISSING = "rendered_page_missing"
    ORIGINAL_SOURCE_MISSING = "original_source_missing"
    SOURCE_TOO_LARGE = "source_too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"


class SourcePreviewUnavailable(RuntimeError):
    """A current source has no persisted representation that the viewer can render."""

    def __init__(self, reason: SourcePreviewUnavailableReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SourcePreview:
    source_id: str
    document_id: str
    revision_id: str
    chunk_id: str
    document_name: str
    locator: SourceLocator
    section_path: tuple[str, ...]
    quote: str
    bounding_boxes: tuple[BoundingBox, ...]
    provenance: tuple[str, ...]
    object_key: str
    media_type: str
    read_url: str
    read_url_expires_at: datetime


@dataclass(frozen=True, slots=True)
class _PreviewReference:
    source_id: str
    user_id: str
    course_id: str
    document_id: str
    revision_id: str
    chunk_id: str
    document_deletion_epoch: int
    content_sha256: str
    locator: SourceLocator
    quote: str
    bounding_boxes: tuple[BoundingBox, ...]
    provenance: tuple[str, ...]


class _PreviewResolver:
    """Resolve only server-derived source identities to persisted preview objects."""

    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    async def resolve(
        self,
        session: AsyncSession,
        reference: _PreviewReference,
    ) -> SourcePreview | None:
        row = (
            (
                await session.execute(
                    select(
                        DocumentModel,
                        DocumentRevisionModel,
                        RevisionChunkModel,
                        StoredObjectModel,
                    )
                    .join(
                        DocumentRevisionModel,
                        and_(
                            DocumentRevisionModel.id == reference.revision_id,
                            DocumentRevisionModel.document_id == DocumentModel.id,
                        ),
                    )
                    .join(
                        RevisionChunkModel,
                        and_(
                            RevisionChunkModel.id == reference.chunk_id,
                            RevisionChunkModel.revision_id == DocumentRevisionModel.id,
                        ),
                    )
                    .join(
                        StoredObjectModel,
                        and_(
                            StoredObjectModel.id == DocumentModel.stored_object_id,
                            StoredObjectModel.user_id == DocumentModel.user_id,
                            StoredObjectModel.course_id == DocumentModel.course_id,
                        ),
                    )
                    .outerjoin(
                        ParseJobModel,
                        and_(
                            ParseJobModel.id == DocumentRevisionModel.parse_job_id,
                            ParseJobModel.user_id == DocumentModel.user_id,
                            ParseJobModel.course_id == DocumentModel.course_id,
                            ParseJobModel.document_id == DocumentModel.id,
                        ),
                    )
                    .where(
                        DocumentModel.id == reference.document_id,
                        DocumentModel.user_id == reference.user_id,
                        DocumentModel.course_id == reference.course_id,
                        DocumentModel.deleted_at.is_(None),
                        DocumentModel.status == "ready",
                        DocumentModel.review_status == "approved",
                        DocumentModel.deletion_epoch == reference.document_deletion_epoch,
                        DocumentModel.active_revision_id == reference.revision_id,
                        RevisionChunkModel.content_sha256 == reference.content_sha256,
                        RevisionChunkModel.locator_kind == reference.locator.kind,
                        RevisionChunkModel.page_ordinal == reference.locator.ordinal,
                        StoredObjectModel.purpose == "original",
                        StoredObjectModel.sha256 == DocumentModel.verified_sha256,
                        StoredObjectModel.media_type == DocumentModel.media_type,
                        StoredObjectModel.deleted_at.is_(None),
                        or_(
                            DocumentRevisionModel.parse_job_id.is_(None),
                            ParseJobModel.document_deletion_epoch
                            == reference.document_deletion_epoch,
                        ),
                    )
                    .with_for_update(of=DocumentModel)
                )
            )
            .tuples()
            .first()
        )
        if row is None:
            return None
        document, _, chunk, original_object = row

        needs_rendered_page = self._needs_rendered_page(document, reference.locator)
        if needs_rendered_page:
            stored_object = await self._rendered_page_object(
                session,
                reference=reference,
            )
            if stored_object is None:
                raise SourcePreviewUnavailable(SourcePreviewUnavailableReason.RENDERED_PAGE_MISSING)
        else:
            media_type = self._base_media_type(original_object.media_type)
            if media_type not in _ORIGINAL_PREVIEW_MEDIA_TYPES:
                raise SourcePreviewUnavailable(
                    SourcePreviewUnavailableReason.UNSUPPORTED_MEDIA_TYPE
                )
            if (
                media_type == MARKDOWN_MEDIA_TYPE
                and original_object.size_bytes > MAX_MARKDOWN_UPLOAD_BYTES
            ):
                raise SourcePreviewUnavailable(SourcePreviewUnavailableReason.SOURCE_TOO_LARGE)
            stored_object = original_object

        try:
            signed = await self._storage.sign_read(stored_object.object_key)
        except (FileNotFoundError, OSError):
            reason = (
                SourcePreviewUnavailableReason.RENDERED_PAGE_MISSING
                if needs_rendered_page
                else SourcePreviewUnavailableReason.ORIGINAL_SOURCE_MISSING
            )
            raise SourcePreviewUnavailable(reason) from None
        return SourcePreview(
            source_id=reference.source_id,
            document_id=document.id,
            revision_id=reference.revision_id,
            chunk_id=reference.chunk_id,
            document_name=document.filename,
            locator=reference.locator,
            section_path=tuple(chunk.section_path),
            quote=reference.quote,
            bounding_boxes=reference.bounding_boxes,
            provenance=reference.provenance,
            object_key=stored_object.object_key,
            media_type=stored_object.media_type,
            read_url=signed.url,
            read_url_expires_at=signed.expires_at,
        )

    @staticmethod
    def _base_media_type(media_type: str) -> str:
        return media_type.partition(";")[0].strip().lower()

    @classmethod
    def _needs_rendered_page(cls, document: DocumentModel, locator: SourceLocator) -> bool:
        return (
            locator.kind == "slide"
            or cls._base_media_type(document.media_type) == PPTX_MEDIA_TYPE
            or document.filename.lower().endswith(".pptx")
        )

    @staticmethod
    async def _rendered_page_object(
        session: AsyncSession,
        *,
        reference: _PreviewReference,
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
                RevisionAssetModel.revision_id == reference.revision_id,
                RevisionAssetModel.asset_type == AssetType.RENDERED_PAGE.value,
                RevisionAssetModel.locator_kind == reference.locator.kind,
                RevisionAssetModel.page_ordinal == reference.locator.ordinal,
                RevisionAssetModel.media_type == JobArtifactModel.media_type,
                RevisionAssetModel.sha256 == JobArtifactModel.sha256,
                or_(
                    RevisionAssetModel.size_bytes.is_(None),
                    RevisionAssetModel.size_bytes == JobArtifactModel.size_bytes,
                ),
                DocumentRevisionModel.document_id == reference.document_id,
                JobArtifactModel.user_id == reference.user_id,
                JobArtifactModel.course_id == reference.course_id,
                JobArtifactModel.document_id == reference.document_id,
                JobArtifactModel.deletion_epoch == reference.document_deletion_epoch,
                JobArtifactModel.status == "available",
                StoredObjectModel.user_id == reference.user_id,
                StoredObjectModel.course_id == reference.course_id,
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


class SourcePreviewService:
    """Owner-scoped preview lookups for persisted learning surfaces."""

    def __init__(self, database: Database, storage: ObjectStorage) -> None:
        self._database = database
        self._resolver = _PreviewResolver(storage)

    async def get_note_source(
        self,
        principal: Principal,
        note_id: str,
        source_id: str,
    ) -> SourcePreview | None:
        async with self._database.session(principal) as session:
            source = await session.scalar(
                select(NoteSourceModel)
                .join(
                    NoteModel,
                    and_(
                        NoteModel.id == NoteSourceModel.note_id,
                        NoteModel.user_id == NoteSourceModel.user_id,
                        NoteModel.course_id == NoteSourceModel.course_id,
                    ),
                )
                .join(
                    CourseModel,
                    and_(
                        CourseModel.id == NoteModel.course_id,
                        CourseModel.user_id == NoteModel.user_id,
                    ),
                )
                .join(UserModel, UserModel.id == NoteModel.user_id)
                .where(
                    NoteModel.id == note_id,
                    NoteModel.status == "ready",
                    NoteSourceModel.id == source_id,
                    NoteSourceModel.available.is_(True),
                    NoteSourceModel.invalidated_at.is_(None),
                    CourseModel.lifecycle == "active",
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            )
            if source is None:
                return None
            try:
                reference = _PreviewReference(
                    source_id=source.id,
                    user_id=source.user_id,
                    course_id=source.course_id,
                    document_id=source.document_id,
                    revision_id=source.revision_id,
                    chunk_id=source.chunk_id,
                    document_deletion_epoch=source.document_deletion_epoch,
                    content_sha256=source.content_sha256,
                    locator=SourceLocator.model_validate(source.locator),
                    quote=source.quote,
                    bounding_boxes=tuple(
                        BoundingBox.model_validate(item) for item in source.bounding_boxes
                    ),
                    provenance=tuple(source.provenance),
                )
            except ValidationError:
                return None
            return await self._resolver.resolve(session, reference)

    async def get_graph_source(
        self,
        principal: Principal,
        course_id: str,
        revision_id: str,
        chunk_id: str,
    ) -> SourcePreview | None:
        async with self._database.session(principal) as session:
            row = (
                (
                    await session.execute(
                        select(DocumentModel, RevisionChunkModel)
                        .join(
                            CourseModel,
                            and_(
                                CourseModel.id == DocumentModel.course_id,
                                CourseModel.user_id == DocumentModel.user_id,
                            ),
                        )
                        .join(UserModel, UserModel.id == DocumentModel.user_id)
                        .join(
                            RevisionChunkModel,
                            and_(
                                RevisionChunkModel.revision_id == DocumentModel.active_revision_id,
                                RevisionChunkModel.id == chunk_id,
                            ),
                        )
                        .where(
                            CourseModel.id == course_id,
                            CourseModel.lifecycle == "active",
                            CourseModel.deleted_at.is_(None),
                            DocumentModel.active_revision_id == revision_id,
                            DocumentModel.deleted_at.is_(None),
                            DocumentModel.status == "ready",
                            DocumentModel.review_status == "approved",
                            DocumentModel.corpus_role == "corpus",
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
            document, chunk = row
            try:
                locator = SourceLocator(
                    kind=chunk.locator_kind,
                    ordinal=chunk.page_ordinal,
                )
            except ValidationError:
                return None
            reference = _PreviewReference(
                source_id=chunk.id,
                user_id=document.user_id,
                course_id=document.course_id,
                document_id=document.id,
                revision_id=revision_id,
                chunk_id=chunk.id,
                document_deletion_epoch=document.deletion_epoch,
                content_sha256=chunk.content_sha256,
                locator=locator,
                quote=chunk.text,
                bounding_boxes=(),
                provenance=(),
            )
            return await self._resolver.resolve(session, reference)
