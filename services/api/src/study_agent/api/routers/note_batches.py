"""HTTP control plane for local asynchronous note-generation batches."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import APIRouter, Header, Query, Request, Response, status
from starlette.responses import StreamingResponse

from study_agent.api.errors import ApiProblem, ProblemCode, ProblemDetails
from study_agent.api.schemas.note_workflow import (
    LocalDemoNoteBatchSnapshot,
    MergedNoteBatchRequest,
)
from study_agent.config import Settings
from study_agent.identity.principal import Principal
from study_agent.identity.session import get_request_principal
from study_agent.infrastructure.db.session import Database
from study_agent.modules.jobs.waiter import ClaimWaiter
from study_agent.modules.notes.batch_service import (
    NoteBatchService,
    NoteBatchServiceError,
    NoteBatchServiceErrorCode,
)
from study_agent.modules.notes.demo_runner import DemoNoteRunner
from study_agent.modules.notes.events import NoteGenerationEventReader
from study_agent.observability.trace import new_trace_id
from study_agent.providers.protocols import Clock
from study_contracts import JobEventEnvelope, NoteBatchSnapshot

router = APIRouter(prefix="/api/v1", tags=["note-batches"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]
_ETAG = re.compile(r'^"([1-9][0-9]*)"$')


async def _principal(request: Request) -> Principal:
    return await get_request_principal(request)


def _service(request: Request) -> NoteBatchService:
    return NoteBatchService(
        cast(Database, request.app.state.database),
        cast(Settings, request.app.state.settings),
        cast(Clock, request.app.state.clock),
    )


def _problem(exc: NoteBatchServiceError) -> ApiProblem:
    mapping: dict[NoteBatchServiceErrorCode, tuple[int, ProblemCode, str, bool]] = {
        NoteBatchServiceErrorCode.NOT_FOUND: (
            404,
            ProblemCode.RESOURCE_NOT_FOUND,
            "资源不存在",
            False,
        ),
        NoteBatchServiceErrorCode.INVALID_REQUEST: (
            422,
            ProblemCode.INVALID_REQUEST,
            "请求参数无效",
            False,
        ),
        NoteBatchServiceErrorCode.IDEMPOTENCY_CONFLICT: (
            409,
            ProblemCode.IDEMPOTENCY_CONFLICT,
            "幂等键冲突",
            False,
        ),
        NoteBatchServiceErrorCode.UNSUPPORTED_MEDIA_TYPE: (
            415,
            ProblemCode.UNSUPPORTED_MEDIA_TYPE,
            "资料格式不受支持",
            False,
        ),
        NoteBatchServiceErrorCode.DOCUMENT_NOT_READY: (
            409,
            ProblemCode.NOTE_DOCUMENT_NOT_READY,
            "资料尚未就绪",
            True,
        ),
        NoteBatchServiceErrorCode.REQUEST_LIMIT_EXCEEDED: (
            422,
            ProblemCode.NOTE_REQUEST_LIMIT_EXCEEDED,
            "批次超过配置上限",
            False,
        ),
        NoteBatchServiceErrorCode.DEMO_UNAVAILABLE: (
            503,
            ProblemCode.NOTE_WORKFLOW_DISABLED,
            "本地笔记演示未启用",
            False,
        ),
        NoteBatchServiceErrorCode.VERSION_CONFLICT: (
            412,
            ProblemCode.VERSION_CONFLICT,
            "笔记版本冲突",
            False,
        ),
        NoteBatchServiceErrorCode.VERSION_NOT_FOUND: (
            409,
            ProblemCode.NOTE_VERSION_NOT_FOUND,
            "笔记版本快照缺失",
            False,
        ),
    }
    status_code, code, title, retryable = mapping[exc.code]
    return ApiProblem(
        status=status_code,
        code=code,
        title=title,
        detail=exc.detail,
        retryable=retryable,
    )


def _public_snapshot(snapshot: NoteBatchSnapshot) -> LocalDemoNoteBatchSnapshot:
    return LocalDemoNoteBatchSnapshot.model_validate(snapshot.model_dump(mode="python"))


def _accepted(
    response: Response,
    snapshot: NoteBatchSnapshot,
) -> LocalDemoNoteBatchSnapshot:
    response.headers["Location"] = f"/api/v1/note-batches/{snapshot.id}"
    return _public_snapshot(snapshot)


def _version(if_match: str) -> int:
    match = _ETAG.fullmatch(if_match.strip())
    if match is None:
        raise ApiProblem(
            status=422,
            code=ProblemCode.INVALID_REQUEST,
            title="If-Match 无效",
        )
    return int(match.group(1))


@router.post(
    "/courses/{course_id}/note-batches",
    response_model=LocalDemoNoteBatchSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_note_batch(
    course_id: str,
    payload: MergedNoteBatchRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
) -> LocalDemoNoteBatchSnapshot:
    try:
        snapshot = await _service(request).create_batch(
            await _principal(request),
            course_id,
            payload,
            idempotency_key,
        )
    except NoteBatchServiceError as exc:
        raise _problem(exc) from exc
    cast(DemoNoteRunner, request.app.state.note_runner).schedule(snapshot.id)
    return _accepted(response, snapshot)


@router.post(
    "/notes/{note_id}/regeneration-batches",
    response_model=LocalDemoNoteBatchSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        409: {"model": ProblemDetails, "description": "幂等键或笔记版本状态冲突"},
        412: {"model": ProblemDetails, "description": "If-Match 与当前笔记版本不一致"},
        428: {"model": ProblemDetails, "description": "缺少必需的 If-Match 前置条件"},
    },
)
async def create_note_regeneration_batch(
    note_id: str,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> LocalDemoNoteBatchSnapshot:
    try:
        snapshot = await _service(request).create_regeneration_batch(
            await _principal(request),
            note_id,
            _version(if_match),
            idempotency_key,
        )
    except NoteBatchServiceError as exc:
        raise _problem(exc) from exc
    cast(DemoNoteRunner, request.app.state.note_runner).schedule(snapshot.id)
    return _accepted(response, snapshot)


@router.get("/note-batches/{batch_id}", response_model=LocalDemoNoteBatchSnapshot)
async def get_note_batch(batch_id: str, request: Request) -> LocalDemoNoteBatchSnapshot:
    try:
        snapshot = await _service(request).get_batch(await _principal(request), batch_id)
    except NoteBatchServiceError as exc:
        raise _problem(exc) from exc
    return _public_snapshot(snapshot)


@router.get("/note-batches/{batch_id}/events")
async def stream_note_batch_events(
    batch_id: str,
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
    principal = await _principal(request)
    reader = NoteGenerationEventReader(
        cast(Database, request.app.state.database),
        cast(Clock, request.app.state.clock),
    )
    initial = await reader.events_after(principal, batch_id, after_sequence)
    if initial is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="笔记生成批次不存在",
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
                next_events = await reader.events_after(principal, batch_id, after_sequence)
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


__all__ = ["router"]
