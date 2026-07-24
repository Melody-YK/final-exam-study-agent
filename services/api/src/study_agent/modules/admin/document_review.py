"""Administrator document review queries and state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import cast

from sqlalchemy import Row, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from study_agent.config import Settings
from study_agent.infrastructure.db.models import (
    AccountModel,
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    StoredObjectModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.auth.service import AccountIdentity, AccountRole, AccountStatus
from study_agent.modules.ingestion.index_repository import enqueue_approved_document_preview
from study_agent.providers.protocols import Clock

_ADMIN_DOCUMENT_REVIEW_ACTOR = "admin-document-review"


class DocumentReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class DocumentReviewErrorCode(StrEnum):
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"


class DocumentReviewError(RuntimeError):
    def __init__(self, code: DocumentReviewErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class AdminDocument:
    id: str
    course_id: str
    course_title: str
    owner_account_id: str | None
    owner_email: str | None
    owner_display_name: str | None
    owner_subject: str
    filename: str
    media_type: str
    size_bytes: int
    corpus_role: str
    status: str
    page_count: int | None
    review_status: DocumentReviewStatus
    review_note: str | None
    reviewed_by_account_id: str | None
    reviewed_by_email: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AdminDocumentContent:
    object_key: str
    filename: str
    media_type: str
    size_bytes: int


class AdminDocumentReviewService:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._database = database
        self._clock = clock
        self._settings = settings

    async def list_documents(
        self,
        actor: AccountIdentity,
        review_status: DocumentReviewStatus | None = None,
    ) -> list[AdminDocument]:
        _require_admin(actor)
        statement = _document_statement()
        if review_status is not None:
            statement = statement.where(DocumentModel.review_status == review_status.value)
        statement = statement.order_by(DocumentModel.created_at.desc(), DocumentModel.id)
        async with self._database.system_session(_ADMIN_DOCUMENT_REVIEW_ACTOR) as session:
            rows = (await session.execute(statement)).all()
        return [_document_from_row(row) for row in rows]

    async def get_content(
        self,
        actor: AccountIdentity,
        document_id: str,
    ) -> AdminDocumentContent:
        _require_admin(actor)
        async with self._database.system_session(_ADMIN_DOCUMENT_REVIEW_ACTOR) as session:
            row = (
                await session.execute(
                    select(DocumentModel, StoredObjectModel)
                    .join(
                        StoredObjectModel,
                        StoredObjectModel.id == DocumentModel.stored_object_id,
                    )
                    .where(
                        DocumentModel.id == document_id,
                        DocumentModel.deleted_at.is_(None),
                        StoredObjectModel.deleted_at.is_(None),
                    )
                )
            ).one_or_none()
        if row is None:
            raise DocumentReviewError(
                DocumentReviewErrorCode.NOT_FOUND,
                "资料原文件不存在或不可用。",
            )
        document, stored_object = row
        return AdminDocumentContent(
            object_key=stored_object.object_key,
            filename=document.filename,
            media_type=document.media_type,
            size_bytes=stored_object.size_bytes,
        )

    async def review(
        self,
        actor: AccountIdentity,
        document_id: str,
        *,
        review_status: DocumentReviewStatus,
        review_note: str | None,
    ) -> AdminDocument:
        _require_admin(actor)
        if review_status is DocumentReviewStatus.PENDING:
            raise ValueError("pending is not a review decision")
        normalized_note = _normalize_review_note(review_status, review_note)
        now = self._clock.now()
        async with self._database.system_session(_ADMIN_DOCUMENT_REVIEW_ACTOR) as session:
            document = await session.scalar(
                select(DocumentModel)
                .where(
                    DocumentModel.id == document_id,
                    DocumentModel.deleted_at.is_(None),
                )
                .with_for_update(of=DocumentModel)
            )
            if document is None:
                raise DocumentReviewError(
                    DocumentReviewErrorCode.NOT_FOUND,
                    "资料不存在。",
                )
            if document.status == "uploading":
                raise DocumentReviewError(
                    DocumentReviewErrorCode.CONFLICT,
                    "资料尚未完成上传, 不能审核。",
                )

            current_status = DocumentReviewStatus(document.review_status)
            if current_status is not DocumentReviewStatus.PENDING:
                if current_status is not review_status:
                    raise DocumentReviewError(
                        DocumentReviewErrorCode.CONFLICT,
                        "资料已经完成审核, 不能改为相反的审核决定。",
                    )
                return await _get_document(session, document.id)

            document.review_status = review_status.value
            document.review_note = normalized_note
            document.reviewed_by_account_id = actor.account.id
            document.reviewed_at = now
            await session.flush()

            if review_status is DocumentReviewStatus.APPROVED:
                await enqueue_approved_document_preview(
                    session,
                    document,
                    requested_provider=self._settings.embedding_provider,
                    requested_model=self._settings.embedding_model,
                    now=now,
                )
            return await _get_document(session, document.id)


type DocumentRow = tuple[
    DocumentModel,
    str,
    str,
    str | None,
    str | None,
    str | None,
    int,
    int | None,
    str | None,
]


def _document_statement() -> Select[DocumentRow]:
    owner_account = aliased(AccountModel, name="owner_account")
    reviewer_account = aliased(AccountModel, name="reviewer_account")
    revision = aliased(DocumentRevisionModel, name="selected_revision")
    selected_revision_id = func.coalesce(
        DocumentModel.preview_revision_id,
        DocumentModel.active_revision_id,
    )
    statement = (
        select(
            DocumentModel,
            CourseModel.title,
            UserModel.subject,
            owner_account.id,
            owner_account.email,
            owner_account.display_name,
            StoredObjectModel.size_bytes,
            revision.total_page_count,
            reviewer_account.email,
        )
        .join(CourseModel, CourseModel.id == DocumentModel.course_id)
        .join(UserModel, UserModel.id == DocumentModel.user_id)
        .join(StoredObjectModel, StoredObjectModel.id == DocumentModel.stored_object_id)
        .outerjoin(owner_account, owner_account.user_id == UserModel.id)
        .outerjoin(
            reviewer_account,
            reviewer_account.id == DocumentModel.reviewed_by_account_id,
        )
        .outerjoin(revision, revision.id == selected_revision_id)
        .where(
            DocumentModel.deleted_at.is_(None),
            CourseModel.deleted_at.is_(None),
        )
    )
    return cast(Select[DocumentRow], statement)


async def _get_document(session: AsyncSession, document_id: str) -> AdminDocument:
    row = (
        await session.execute(_document_statement().where(DocumentModel.id == document_id))
    ).one_or_none()
    if row is None:
        raise DocumentReviewError(
            DocumentReviewErrorCode.NOT_FOUND,
            "资料不存在。",
        )
    return _document_from_row(row)


def _document_from_row(row: Row[DocumentRow]) -> AdminDocument:
    (
        document,
        course_title,
        owner_subject,
        owner_account_id,
        owner_email,
        owner_display_name,
        size_bytes,
        page_count,
        reviewed_by_email,
    ) = row
    return AdminDocument(
        id=document.id,
        course_id=document.course_id,
        course_title=course_title,
        owner_account_id=owner_account_id,
        owner_email=owner_email,
        owner_display_name=owner_display_name,
        owner_subject=owner_subject,
        filename=document.filename,
        media_type=document.media_type,
        size_bytes=size_bytes,
        corpus_role=document.corpus_role,
        status=document.status,
        page_count=page_count,
        review_status=DocumentReviewStatus(document.review_status),
        review_note=document.review_note,
        reviewed_by_account_id=document.reviewed_by_account_id,
        reviewed_by_email=reviewed_by_email,
        reviewed_at=document.reviewed_at,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _normalize_review_note(
    review_status: DocumentReviewStatus,
    review_note: str | None,
) -> str | None:
    normalized = None if review_note is None else review_note.strip()
    if normalized == "":
        normalized = None
    if normalized is not None and len(normalized) > 500:
        raise ValueError("review note must be at most 500 characters")
    if review_status is DocumentReviewStatus.REJECTED and normalized is None:
        raise ValueError("rejected documents require a review note")
    return normalized


def _require_admin(actor: AccountIdentity) -> None:
    if (
        actor.account.role is not AccountRole.ADMIN
        or actor.account.status is not AccountStatus.ACTIVE
    ):
        raise DocumentReviewError(
            DocumentReviewErrorCode.FORBIDDEN,
            "需要管理员权限。",
        )


__all__ = [
    "AdminDocument",
    "AdminDocumentContent",
    "AdminDocumentReviewService",
    "DocumentReviewError",
    "DocumentReviewErrorCode",
    "DocumentReviewStatus",
]
