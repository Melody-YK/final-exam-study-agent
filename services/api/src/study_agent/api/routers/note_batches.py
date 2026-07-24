"""HTTP control plane for local asynchronous note-generation batches."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Header, Request, Response, status

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.api.schemas.note_workflow import (
    LocalDemoNoteBatchSnapshot,
    MergedNoteBatchRequest,
)
from study_agent.config import Settings
from study_agent.identity.principal import Principal
from study_agent.identity.session import get_request_principal
from study_agent.infrastructure.db.session import Database
from study_agent.modules.notes.batch_service import (
    NoteBatchService,
    NoteBatchServiceError,
    NoteBatchServiceErrorCode,
)
from study_agent.modules.notes.demo_runner import DemoNoteRunner
from study_agent.providers.protocols import Clock
from study_contracts import NoteBatchSnapshot

router = APIRouter(prefix="/api/v1", tags=["note-batches"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]


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


@router.get("/note-batches/{batch_id}", response_model=LocalDemoNoteBatchSnapshot)
async def get_note_batch(batch_id: str, request: Request) -> LocalDemoNoteBatchSnapshot:
    try:
        snapshot = await _service(request).get_batch(await _principal(request), batch_id)
    except NoteBatchServiceError as exc:
        raise _problem(exc) from exc
    return _public_snapshot(snapshot)


__all__ = ["router"]
