"""Principal-scoped reads for durable note-generation events."""

from __future__ import annotations

from typing import cast

from sqlalchemy import and_, func, select

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    CourseModel,
    NoteGenerationBatchModel,
    NoteGenerationEventModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.providers.protocols import Clock
from study_contracts import JobEventEnvelope


class NoteGenerationEventReader:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    async def events_after(
        self,
        principal: Principal,
        batch_id: str,
        after_sequence: int,
    ) -> list[JobEventEnvelope] | None:
        now = self._clock.now()
        async with self._database.session(principal) as session:
            batch = cast(
                NoteGenerationBatchModel | None,
                await session.scalar(
                    select(NoteGenerationBatchModel)
                    .join(
                        CourseModel,
                        and_(
                            CourseModel.id == NoteGenerationBatchModel.course_id,
                            CourseModel.user_id == NoteGenerationBatchModel.user_id,
                        ),
                    )
                    .join(UserModel, UserModel.id == NoteGenerationBatchModel.user_id)
                    .where(
                        NoteGenerationBatchModel.id == batch_id,
                        NoteGenerationBatchModel.command_kind.in_(("create", "regeneration")),
                        NoteGenerationBatchModel.mode == "merged",
                        CourseModel.lifecycle == "active",
                        CourseModel.deleted_at.is_(None),
                        UserModel.subject == principal.subject,
                        UserModel.authentication_method == principal.authentication_method.value,
                    )
                ),
            )
            if batch is None:
                return None
            earliest = await session.scalar(
                select(func.min(NoteGenerationEventModel.sequence)).where(
                    NoteGenerationEventModel.batch_id == batch.id,
                    NoteGenerationEventModel.expires_at > now,
                )
            )
            if (earliest is not None and after_sequence < earliest - 1) or (
                earliest is None and batch.event_sequence > after_sequence
            ):
                raise ApiProblem(
                    status=410,
                    code=ProblemCode.EVENT_HISTORY_EXPIRED,
                    title="笔记生成事件历史已过期",
                    detail="请重新读取笔记批次快照。",
                )
            events = (
                await session.scalars(
                    select(NoteGenerationEventModel)
                    .where(
                        NoteGenerationEventModel.batch_id == batch.id,
                        NoteGenerationEventModel.sequence > after_sequence,
                        NoteGenerationEventModel.expires_at > now,
                    )
                    .order_by(NoteGenerationEventModel.sequence)
                )
            ).all()
        return [
            JobEventEnvelope(
                sequence=event.sequence,
                occurred_at=event.occurred_at,
                trace_id=event.trace_id,
                event_type=event.event_type,
                data=event.payload,
            )
            for event in events
        ]


__all__ = ["NoteGenerationEventReader"]
