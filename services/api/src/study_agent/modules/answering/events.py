"""Persisted query events and principal-scoped SSE reads."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    CourseModel,
    QueryEventModel,
    QueryRunModel,
    UserModel,
)
from study_agent.infrastructure.db.models.core import new_id
from study_agent.infrastructure.db.session import Database
from study_agent.observability.trace import get_trace_id, new_trace_id
from study_agent.providers.protocols import Clock
from study_contracts import JobEventEnvelope


def append_query_event(
    session: AsyncSession,
    query: QueryRunModel,
    event_type: str,
    payload: dict[str, Any],
    *,
    now: datetime,
    retention: timedelta,
) -> QueryEventModel:
    query.event_sequence += 1
    event = QueryEventModel(
        id=new_id(),
        query_id=query.id,
        user_id=query.user_id,
        course_id=query.course_id,
        sequence=query.event_sequence,
        event_type=event_type,
        payload=payload,
        trace_id=get_trace_id() or query.trace_id or new_trace_id(),
        occurred_at=now,
        expires_at=now + retention,
    )
    session.add(event)
    return event


class QueryEventReader:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    async def events_after(
        self,
        principal: Principal,
        query_id: str,
        after_sequence: int,
    ) -> list[JobEventEnvelope] | None:
        now = self._clock.now()
        async with self._database.session(principal) as session:
            query = cast(
                QueryRunModel | None,
                await session.scalar(
                    select(QueryRunModel)
                    .join(
                        CourseModel,
                        and_(
                            CourseModel.id == QueryRunModel.course_id,
                            CourseModel.user_id == QueryRunModel.user_id,
                        ),
                    )
                    .join(UserModel, UserModel.id == QueryRunModel.user_id)
                    .where(
                        QueryRunModel.id == query_id,
                        CourseModel.deleted_at.is_(None),
                        UserModel.subject == principal.subject,
                        UserModel.authentication_method == principal.authentication_method.value,
                    )
                ),
            )
            if query is None:
                return None
            earliest = await session.scalar(
                select(func.min(QueryEventModel.sequence)).where(
                    QueryEventModel.query_id == query.id,
                    QueryEventModel.expires_at > now,
                )
            )
            if (earliest is not None and after_sequence < earliest - 1) or (
                earliest is None and query.event_sequence > after_sequence
            ):
                raise ApiProblem(
                    status=410,
                    code=ProblemCode.EVENT_HISTORY_EXPIRED,
                    title="问答事件历史已过期",
                    detail="请重新读取问答快照。",
                )
            events = (
                await session.scalars(
                    select(QueryEventModel)
                    .where(
                        QueryEventModel.query_id == query.id,
                        QueryEventModel.sequence > after_sequence,
                        QueryEventModel.expires_at > now,
                    )
                    .order_by(QueryEventModel.sequence)
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
