"""Administrator-only, read-only learning-content queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import cast

from sqlalchemy import Row, Select, func, select
from sqlalchemy.orm import aliased

from study_agent.infrastructure.db.models import (
    AccountModel,
    CourseModel,
    DocumentModel,
    NoteModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.auth.service import AccountIdentity, AccountRole, AccountStatus

_ADMIN_CONTENT_ACTOR = "admin-content-inspection"


class AdminContentErrorCode(StrEnum):
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"


class AdminContentError(RuntimeError):
    def __init__(self, code: AdminContentErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class AdminCourse:
    id: str
    title: str
    lifecycle: str
    owner_account_id: str | None
    owner_email: str | None
    owner_display_name: str | None
    owner_subject: str
    document_count: int
    note_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AdminNote:
    id: str
    course_id: str
    section_path: tuple[str, ...]
    title: str
    body_markdown: str
    version: int
    generation: int
    generated_by_model: bool
    status: str
    created_at: datetime
    updated_at: datetime


type AdminCourseRow = tuple[
    CourseModel,
    str,
    str | None,
    str | None,
    str | None,
    int,
    int,
]


class AdminContentService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_courses(self, actor: AccountIdentity) -> list[AdminCourse]:
        _require_admin(actor)
        async with self._database.system_session(_ADMIN_CONTENT_ACTOR) as session:
            rows = (await session.execute(_course_statement())).all()
        return [_course_from_row(row) for row in rows]

    async def list_notes(
        self,
        actor: AccountIdentity,
        course_id: str,
    ) -> list[AdminNote]:
        _require_admin(actor)
        async with self._database.system_session(_ADMIN_CONTENT_ACTOR) as session:
            course = await session.scalar(
                select(CourseModel)
                .join(AccountModel, AccountModel.user_id == CourseModel.user_id)
                .where(
                    CourseModel.id == course_id,
                    CourseModel.deleted_at.is_(None),
                    AccountModel.role == AccountRole.USER.value,
                )
            )
            if course is None:
                raise AdminContentError(
                    AdminContentErrorCode.NOT_FOUND,
                    "课程不存在。",
                )
            notes = list(
                await session.scalars(
                    select(NoteModel)
                    .where(
                        NoteModel.user_id == course.user_id,
                        NoteModel.course_id == course.id,
                    )
                    .order_by(NoteModel.updated_at.desc(), NoteModel.id)
                )
            )
        return [_note_from_model(note) for note in notes]


def _course_statement() -> Select[AdminCourseRow]:
    owner_account = aliased(AccountModel, name="owner_account")
    document_count = (
        select(func.count(DocumentModel.id))
        .where(
            DocumentModel.course_id == CourseModel.id,
            DocumentModel.deleted_at.is_(None),
        )
        .correlate(CourseModel)
        .scalar_subquery()
    )
    note_count = (
        select(func.count(NoteModel.id))
        .where(NoteModel.course_id == CourseModel.id)
        .correlate(CourseModel)
        .scalar_subquery()
    )
    statement = (
        select(
            CourseModel,
            UserModel.subject,
            owner_account.id,
            owner_account.email,
            owner_account.display_name,
            document_count,
            note_count,
        )
        .join(UserModel, UserModel.id == CourseModel.user_id)
        .join(owner_account, owner_account.user_id == UserModel.id)
        .where(
            CourseModel.deleted_at.is_(None),
            owner_account.role == AccountRole.USER.value,
        )
        .order_by(CourseModel.created_at.desc(), CourseModel.id)
    )
    return cast(Select[AdminCourseRow], statement)


def _course_from_row(row: Row[AdminCourseRow]) -> AdminCourse:
    (
        course,
        owner_subject,
        owner_account_id,
        owner_email,
        owner_display_name,
        document_count,
        note_count,
    ) = row
    return AdminCourse(
        id=course.id,
        title=course.title,
        lifecycle=course.lifecycle,
        owner_account_id=owner_account_id,
        owner_email=owner_email,
        owner_display_name=owner_display_name,
        owner_subject=owner_subject,
        document_count=document_count,
        note_count=note_count,
        created_at=course.created_at,
        updated_at=course.updated_at,
    )


def _note_from_model(note: NoteModel) -> AdminNote:
    return AdminNote(
        id=note.id,
        course_id=note.course_id,
        section_path=tuple(note.section_path),
        title=note.title,
        body_markdown=note.body_markdown,
        version=note.version,
        generation=note.generation,
        generated_by_model=note.generated_by_model,
        status=note.status,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def _require_admin(actor: AccountIdentity) -> None:
    if (
        actor.account.role is not AccountRole.ADMIN
        or actor.account.status is not AccountStatus.ACTIVE
    ):
        raise AdminContentError(
            AdminContentErrorCode.FORBIDDEN,
            "需要管理员权限。",
        )


__all__ = [
    "AdminContentError",
    "AdminContentErrorCode",
    "AdminContentService",
    "AdminCourse",
    "AdminNote",
]
