"""Authenticated Worker API for persistent parse jobs."""

from __future__ import annotations

import hmac
from datetime import timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Header, Query, Request, status
from starlette.responses import StreamingResponse

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.config import Settings
from study_agent.infrastructure.db.session import Database
from study_agent.modules.jobs.capabilities import claim_capabilities_are_eligible
from study_agent.modules.jobs.lease import LeasePolicy
from study_agent.modules.jobs.presence import WorkerPresenceRegistry
from study_agent.modules.jobs.service import JobService
from study_agent.modules.jobs.waiter import ClaimWaiter
from study_agent.providers.protocols import Clock
from study_agent.storage.local import LocalStorage
from study_contracts import (
    JobArtifactReceipt,
    JobClaimRequest,
    JobClaimResponse,
    JobCompleteRequest,
    JobFailRequest,
    JobHeartbeatRequest,
    JobSnapshot,
    JobStartRequest,
    PageCheckpointRequest,
)

router = APIRouter(prefix="/worker/v1", tags=["worker-jobs"])


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


def _storage(request: Request) -> LocalStorage:
    return cast(LocalStorage, request.app.state.storage)


def _clock(request: Request) -> Clock:
    return cast(Clock, request.app.state.clock)


def _claim_waiter(request: Request) -> ClaimWaiter:
    return cast(ClaimWaiter, request.app.state.claim_waiter)


def _worker_presence(request: Request) -> WorkerPresenceRegistry:
    return cast(WorkerPresenceRegistry, request.app.state.worker_presence)


def _job_service(request: Request) -> JobService:
    settings = _settings(request)
    return JobService(
        _database(request),
        _storage(request),
        _clock(request),
        lease_policy=LeasePolicy(
            ttl=timedelta(seconds=settings.worker_lease_seconds),
            retry_base=timedelta(seconds=settings.job_retry_base_seconds),
        ),
        event_retention=timedelta(seconds=settings.job_event_retention_seconds),
    )


def _authorize_worker(request: Request, authorization: str | None) -> None:
    configured = _settings(request).worker_token
    provided = ""
    if authorization is not None and authorization.startswith("Bearer "):
        provided = authorization.removeprefix("Bearer ").strip()
    expected = "" if configured is None else configured.get_secret_value()
    if not expected or not provided or not hmac.compare_digest(provided, expected):
        raise ApiProblem(
            status=401,
            code=ProblemCode.AUTH_REQUIRED,
            title="Worker 身份验证失败",
        )


@router.post("/jobs:claim", response_model=JobClaimResponse)
async def claim_job(
    payload: JobClaimRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    wait_seconds: Annotated[float, Query(ge=0, le=30)] = 0,
) -> JobClaimResponse:
    _authorize_worker(request, authorization)
    service = _job_service(request)
    if not claim_capabilities_are_eligible(payload.capabilities):
        return JobClaimResponse(lease=None, retry_after_ms=1_000)
    await _worker_presence(request).record(payload, now=_clock(request).now())
    remaining = wait_seconds
    while True:
        lease = await service.claim(payload)
        if lease is not None:
            return JobClaimResponse(lease=lease, retry_after_ms=1_000)
        if remaining <= 0:
            return JobClaimResponse(lease=None, retry_after_ms=1_000)
        interval = min(0.05, remaining)
        await _claim_waiter(request).wait(interval)
        remaining -= interval


@router.get("/jobs/{job_id}/input")
async def read_job_input(
    job_id: str,
    request: Request,
    lease_version: Annotated[int, Query(ge=1)],
    authorization: Annotated[str | None, Header()] = None,
    worker_id: Annotated[str, Header(alias="X-Worker-ID")] = "",
    lease_token: Annotated[str, Header(alias="X-Lease-Token")] = "",
    attempt: Annotated[int, Header(alias="X-Attempt", ge=1)] = 1,
    deletion_epoch: Annotated[int, Header(alias="X-Deletion-Epoch", ge=0)] = 0,
) -> StreamingResponse:
    _authorize_worker(request, authorization)
    object_key, media_type = await _job_service(request).input_object_key(
        job_id,
        worker_id=worker_id,
        lease_token=lease_token,
        lease_version=lease_version,
        attempt=attempt,
        deletion_epoch=deletion_epoch,
    )
    return StreamingResponse(
        _storage(request).stream_bytes(object_key),
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@router.put(
    "/jobs/{job_id}/artifacts/{artifact_name}",
    response_model=JobArtifactReceipt,
    status_code=status.HTTP_201_CREATED,
)
async def upload_job_artifact(
    job_id: str,
    artifact_name: str,
    request: Request,
    artifact_schema_version: Annotated[str, Query()] = "1.0",
    authorization: Annotated[str | None, Header()] = None,
    worker_id: Annotated[str, Header(alias="X-Worker-ID")] = "",
    lease_token: Annotated[str, Header(alias="X-Lease-Token")] = "",
    lease_version: Annotated[int, Header(alias="X-Lease-Version", ge=1)] = 1,
    attempt: Annotated[int, Header(alias="X-Attempt", ge=1)] = 1,
    deletion_epoch: Annotated[int, Header(alias="X-Deletion-Epoch", ge=0)] = 0,
) -> JobArtifactReceipt:
    _authorize_worker(request, authorization)
    return await _job_service(request).upload_artifact(
        job_id,
        artifact_name,
        request.stream(),
        request.headers.get("content-type", "application/octet-stream"),
        artifact_schema_version,
        worker_id=worker_id,
        lease_token=lease_token,
        lease_version=lease_version,
        attempt=attempt,
        deletion_epoch=deletion_epoch,
        max_bytes=_settings(request).max_upload_bytes,
    )


@router.post("/jobs/{job_id}:start", response_model=JobSnapshot)
async def start_job(
    job_id: str,
    payload: JobStartRequest,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    authorization: Annotated[str | None, Header()] = None,
) -> JobSnapshot:
    _authorize_worker(request, authorization)
    return await _job_service(request).start(job_id, payload, idempotency_key)


@router.put("/jobs/{job_id}/heartbeat", response_model=JobSnapshot)
async def heartbeat_job(
    job_id: str,
    payload: JobHeartbeatRequest,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    authorization: Annotated[str | None, Header()] = None,
) -> JobSnapshot:
    _authorize_worker(request, authorization)
    return await _job_service(request).heartbeat(job_id, payload, idempotency_key)


@router.put("/jobs/{job_id}/pages/{page_ordinal}/checkpoint", response_model=JobSnapshot)
async def checkpoint_page(
    job_id: str,
    page_ordinal: int,
    payload: PageCheckpointRequest,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    authorization: Annotated[str | None, Header()] = None,
) -> JobSnapshot:
    _authorize_worker(request, authorization)
    if page_ordinal != payload.page_ordinal:
        raise ApiProblem(
            status=422,
            code=ProblemCode.INVALID_REQUEST,
            title="页序与请求体不一致",
        )
    return await _job_service(request).checkpoint(job_id, payload, idempotency_key)


@router.post("/jobs/{job_id}:complete", response_model=JobSnapshot)
async def complete_job(
    job_id: str,
    payload: JobCompleteRequest,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    authorization: Annotated[str | None, Header()] = None,
) -> JobSnapshot:
    _authorize_worker(request, authorization)
    return await _job_service(request).complete(job_id, payload, idempotency_key)


@router.post("/jobs/{job_id}:fail", response_model=JobSnapshot)
async def fail_job(
    job_id: str,
    payload: JobFailRequest,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    authorization: Annotated[str | None, Header()] = None,
) -> JobSnapshot:
    _authorize_worker(request, authorization)
    return await _job_service(request).fail(job_id, payload, idempotency_key)
