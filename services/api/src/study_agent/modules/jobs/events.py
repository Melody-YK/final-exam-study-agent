"""Transactional job event creation and SSE serialization helpers."""

from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    CourseModel,
    DocumentModel,
    JobEventModel,
    ParseJobModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.observability.trace import get_trace_id, new_trace_id
from study_agent.providers.protocols import Clock
from study_contracts import JobEventEnvelope


def append_job_event(
    session: AsyncSession,
    job: ParseJobModel,
    event_type: str,
    payload: dict[str, Any],
    *,
    now: datetime,
    retention: timedelta,
) -> JobEventModel:
    job.event_sequence += 1
    event = JobEventModel(
        id=new_trace_id(),
        job_id=job.id,
        user_id=job.user_id,
        course_id=job.course_id,
        document_id=job.document_id,
        sequence=job.event_sequence,
        event_type=event_type,
        state_version=job.state_version,
        payload=payload,
        trace_id=get_trace_id() or new_trace_id(),
        occurred_at=now,
        expires_at=now + retention,
    )
    session.add(event)
    return event


class JobEventReader:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    async def events_after(
        self,
        principal: Principal,
        job_id: str,
        after_sequence: int,
    ) -> list[JobEventEnvelope] | None:
        now = self._clock.now()
        async with self._database.session(principal) as session:
            job = cast(
                ParseJobModel | None,
                await session.scalar(
                    select(ParseJobModel)
                    .join(
                        DocumentModel,
                        and_(
                            DocumentModel.id == ParseJobModel.document_id,
                            DocumentModel.course_id == ParseJobModel.course_id,
                            DocumentModel.user_id == ParseJobModel.user_id,
                        ),
                    )
                    .join(
                        CourseModel,
                        and_(
                            CourseModel.id == ParseJobModel.course_id,
                            CourseModel.user_id == ParseJobModel.user_id,
                        ),
                    )
                    .join(UserModel, UserModel.id == ParseJobModel.user_id)
                    .where(
                        ParseJobModel.id == job_id,
                        DocumentModel.deleted_at.is_(None),
                        DocumentModel.deletion_epoch == ParseJobModel.document_deletion_epoch,
                        CourseModel.deleted_at.is_(None),
                        UserModel.subject == principal.subject,
                        UserModel.authentication_method == principal.authentication_method.value,
                    )
                ),
            )
            if job is None:
                return None
            earliest = await session.scalar(
                select(func.min(JobEventModel.sequence)).where(
                    JobEventModel.job_id == job.id,
                    JobEventModel.expires_at > now,
                )
            )
            if (earliest is not None and after_sequence < earliest - 1) or (
                earliest is None and job.event_sequence > after_sequence
            ):
                raise ApiProblem(
                    status=410,
                    code=ProblemCode.EVENT_HISTORY_EXPIRED,
                    title="任务事件历史已过期",
                    detail="请重新读取任务快照。",
                )
            events = (
                await session.scalars(
                    select(JobEventModel)
                    .where(
                        JobEventModel.job_id == job.id,
                        JobEventModel.sequence > after_sequence,
                        JobEventModel.expires_at > now,
                    )
                    .order_by(JobEventModel.sequence)
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
