"""Trusted query creation, snapshots, and recoverable SSE."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import StreamingResponse

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.config import Settings
from study_agent.identity.principal import Principal, PrincipalProvider
from study_agent.infrastructure.db.session import Database
from study_agent.modules.answering.events import QueryEventReader
from study_agent.modules.answering.queries import (
    QueryRepository,
    QueryService,
    QuerySnapshot,
    QueryTrace,
)
from study_agent.modules.answering.retrieval import QueryEvidence
from study_agent.modules.jobs.waiter import ClaimWaiter
from study_agent.observability.trace import new_trace_id
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import Clock
from study_contracts import JobEventEnvelope, StructuredAnswer

router = APIRouter(prefix="/api/v1", tags=["queries"])


class QueryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=8_000)
    document_ids: list[str] | None = Field(default=None, max_length=100)


class QueryTraceResponse(BaseModel):
    trace_id: str
    retrieval_snapshot_id: str | None
    retrieval_trace_id: str | None


class QueryResponse(BaseModel):
    id: str
    course_id: str
    question: str
    status: str
    answer: StructuredAnswer | None
    failure_code: str | None
    usage: dict[str, int]
    trace: QueryTraceResponse
    created_at: datetime
    completed_at: datetime | None


def _principal(request: Request) -> Principal:
    if request.client is None:
        raise ApiProblem(status=401, code=ProblemCode.AUTH_REQUIRED, title="需要身份验证")
    provider = cast(PrincipalProvider, request.app.state.principal_provider)
    try:
        return provider.resolve(request.client.host)
    except PermissionError as exc:
        raise ApiProblem(
            status=401,
            code=ProblemCode.AUTH_REQUIRED,
            title="需要身份验证",
        ) from exc


def _repository(request: Request) -> QueryRepository:
    settings = cast(Settings, request.app.state.settings)
    return QueryRepository(
        cast(Database, request.app.state.database),
        cast(Clock, request.app.state.clock),
        event_retention=timedelta(seconds=settings.job_event_retention_seconds),
    )


def _service(request: Request) -> QueryService:
    settings = cast(Settings, request.app.state.settings)
    return QueryService(
        _repository(request),
        cast(QueryEvidence, request.app.state.query_evidence),
        cast(ProviderRegistry, request.app.state.provider_registry),
        timeout_seconds=settings.provider_timeout_seconds,
    )


def _response(snapshot: QuerySnapshot) -> QueryResponse:
    trace: QueryTrace = snapshot.trace
    return QueryResponse(
        id=snapshot.id,
        course_id=snapshot.course_id,
        question=snapshot.question,
        status=snapshot.status,
        answer=snapshot.answer,
        failure_code=snapshot.failure_code,
        usage=snapshot.usage,
        trace=QueryTraceResponse(
            trace_id=trace.trace_id,
            retrieval_snapshot_id=trace.retrieval_snapshot_id,
            retrieval_trace_id=trace.retrieval_trace_id,
        ),
        created_at=snapshot.created_at,
        completed_at=snapshot.completed_at,
    )


@router.post(
    "/courses/{course_id}/queries",
    response_model=QueryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_query(
    course_id: str,
    payload: QueryCreate,
    request: Request,
) -> QueryResponse:
    document_ids = None
    if payload.document_ids is not None:
        if len(payload.document_ids) != len(set(payload.document_ids)):
            raise ApiProblem(
                status=422,
                code=ProblemCode.INVALID_REQUEST,
                title="document_ids 不能重复",
            )
        document_ids = frozenset(payload.document_ids)
    try:
        snapshot = await _service(request).execute(
            _principal(request),
            course_id,
            payload.question,
            document_ids=document_ids,
        )
    except LookupError as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="课程或资料不存在",
        ) from exc
    return _response(snapshot)


@router.get("/queries/{query_id}", response_model=QueryResponse)
async def get_query(query_id: str, request: Request) -> QueryResponse:
    snapshot = await _repository(request).get(_principal(request), query_id)
    if snapshot is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="问答不存在",
        )
    return _response(snapshot)


@router.get("/queries/{query_id}/events")
async def stream_query_events(
    query_id: str,
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    once: Annotated[bool, Query()] = False,
) -> StreamingResponse:
    try:
        after_sequence = 0 if last_event_id is None else int(last_event_id)
    except ValueError as exc:
        raise ApiProblem(
            status=422,
            code=ProblemCode.INVALID_REQUEST,
            title="Last-Event-ID 无效",
        ) from exc
    if after_sequence < 0:
        raise ApiProblem(
            status=422,
            code=ProblemCode.INVALID_REQUEST,
            title="Last-Event-ID 无效",
        )
    principal = _principal(request)
    reader = QueryEventReader(
        cast(Database, request.app.state.database),
        cast(Clock, request.app.state.clock),
    )
    initial = await reader.events_after(principal, query_id, after_sequence)
    if initial is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="问答不存在",
        )
    heartbeat_seconds = cast(Settings, request.app.state.settings).sse_heartbeat_seconds

    async def generate() -> AsyncIterator[str]:
        nonlocal after_sequence
        events = initial
        while True:
            for event in events:
                after_sequence = event.sequence
                yield (
                    f"id: {event.sequence}\n"
                    f"event: {event.event_type}\n"
                    f"data: {event.model_dump_json()}\n\n"
                )
            yield ": heartbeat\n\n"
            if once or await request.is_disconnected():
                return
            await cast(ClaimWaiter, request.app.state.claim_waiter).wait(heartbeat_seconds)
            try:
                next_events = await reader.events_after(principal, query_id, after_sequence)
            except ApiProblem as exc:
                if exc.code is not ProblemCode.EVENT_HISTORY_EXPIRED:
                    raise
                reset = JobEventEnvelope(
                    sequence=max(1, after_sequence),
                    occurred_at=cast(Clock, request.app.state.clock).now(),
                    trace_id=new_trace_id(),
                    event_type="stream.reset",
                    data={
                        "code": ProblemCode.EVENT_HISTORY_EXPIRED.value,
                        "action": "read_snapshot",
                    },
                )
                yield f"event: stream.reset\ndata: {reset.model_dump_json()}\n\n"
                return
            if next_events is None:
                return
            events = next_events

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
