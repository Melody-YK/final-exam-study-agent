"""Principal-scoped course persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from study_agent.identity.principal import CourseScope, Principal
from study_agent.infrastructure.db.models import CourseModel, UserModel
from study_agent.infrastructure.db.session import Database


@dataclass(frozen=True, slots=True)
class Course:
    """Repository result detached from SQLAlchemy session identity."""

    id: str
    user_id: str
    title: str
    lifecycle: str
    active_lexical_index_id: str | None
    row_version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


def _course_from_model(model: CourseModel) -> Course:
    return Course(
        id=model.id,
        user_id=model.user_id,
        title=model.title,
        lifecycle=model.lifecycle,
        active_lexical_index_id=model.active_lexical_index_id,
        row_version=model.row_version,
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )


class CourseRepository:
    """Store courses while making an unscoped lookup impossible by API shape."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create(self, principal: Principal, title: str) -> Course:
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("course title must not be blank")
        if len(normalized_title) > 255:
            raise ValueError("course title must be at most 255 characters")

        async with self._database.session(principal) as session:
            user_id = await session.scalar(
                insert(UserModel)
                .values(
                    id=str(uuid4()),
                    subject=principal.subject,
                    authentication_method=principal.authentication_method.value,
                )
                .on_conflict_do_nothing(constraint="uq_users_authentication_subject")
                .returning(UserModel.id)
            )
            if user_id is None:
                user_id = await session.scalar(
                    select(UserModel.id).where(
                        UserModel.subject == principal.subject,
                        UserModel.authentication_method == principal.authentication_method.value,
                    )
                )
            if user_id is None:
                raise RuntimeError("principal user upsert did not return an identifier")

            model = CourseModel(
                id=str(uuid4()),
                user_id=user_id,
                title=normalized_title,
                lifecycle="active",
                row_version=1,
            )
            session.add(model)
            await session.flush()
            await session.refresh(model)
            return _course_from_model(model)

    async def get(self, scope: CourseScope) -> Course | None:
        async with self._database.session(scope.principal) as session:
            model = await session.scalar(
                select(CourseModel)
                .join(UserModel, UserModel.id == CourseModel.user_id)
                .where(
                    CourseModel.id == scope.course_id,
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == scope.subject,
                    UserModel.authentication_method == scope.principal.authentication_method.value,
                )
            )
            return None if model is None else _course_from_model(model)
