"""Principal-scoped ParseJob snapshots and event streams."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Header, Query, Request
from starlette.responses import StreamingResponse

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.config import Settings
from study_agent.identity.principal import Principal, PrincipalProvider
from study_agent.infrastructure.db.session import Database
from study_agent.modules.jobs.events import JobEventReader
from study_agent.modules.jobs.lease import LeasePolicy
from study_agent.modules.jobs.service import JobService
from study_agent.modules.jobs.waiter import ClaimWaiter
from study_agent.observability.trace import new_trace_id
from study_agent.providers.protocols import Clock
from study_agent.storage.local import LocalStorage
from study_contracts import JobEventEnvelope, JobSnapshot

router = APIRouter(prefix="/api/v1", tags=["job-events"])


def _principal(request: Request) -> Principal:
    if request.client is None:
        raise ApiProblem(status=401, code=ProblemCode.AUTH_REQUIRED, title="需要身份验证")
    provider = cast(PrincipalProvider, request.app.state.principal_provider)
    try:
        return provider.resolve(request.client.host)
    except PermissionError as exc:
        raise ApiProblem(status=401, code=ProblemCode.AUTH_REQUIRED, title="需要身份验证") from exc


def _service(request: Request) -> JobService:
    settings = cast(Settings, request.app.state.settings)
    return JobService(
        cast(Database, request.app.state.database),
        cast(LocalStorage, request.app.state.storage),
        cast(Clock, request.app.state.clock),
        lease_policy=LeasePolicy(
            ttl=timedelta(seconds=settings.worker_lease_seconds),
            retry_base=timedelta(seconds=settings.job_retry_base_seconds),
        ),
        event_retention=timedelta(seconds=settings.job_event_retention_seconds),
    )


def _event_reader(request: Request) -> JobEventReader:
    return JobEventReader(
        cast(Database, request.app.state.database),
        cast(Clock, request.app.state.clock),
    )


@router.get("/parse-jobs/{job_id}", response_model=JobSnapshot)
async def get_job_snapshot(job_id: str, request: Request) -> JobSnapshot:
    snapshot = await _service(request).public_snapshot(_principal(request), job_id)
    if snapshot is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="任务不存在",
        )
    return snapshot


@router.get("/parse-jobs/{job_id}/events")
async def stream_job_events(
    job_id: str,
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
    reader = _event_reader(request)
    initial = await reader.events_after(principal, job_id, after_sequence)
    if initial is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="任务不存在",
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
                next_events = await reader.events_after(principal, job_id, after_sequence)
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
