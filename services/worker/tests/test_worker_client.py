from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from study_contracts import (
    JobClaimRequest,
    JobCompleteRequest,
    JobStartRequest,
    WorkerCapabilities,
    WorkerLease,
)
from study_worker.poller.client import (
    InputDownloadError,
    LeaseLostError,
    WorkerClient,
)


def _lease(*, input_url: str = "https://objects.example/input.pdf") -> WorkerLease:
    return WorkerLease(
        job_id="job-1",
        course_id="course-1",
        document_id="document-1",
        document_sha256="a" * 64,
        deletion_epoch=0,
        media_type="application/pdf",
        parser_profile="native-v1",
        attempt=1,
        lease_version=2,
        lease_token="lease-token-that-is-long-enough",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        input_url=input_url,
        artifact_upload_url="/worker/v1/jobs/job-1/artifacts",
    )


def _snapshot_json() -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": "1.0",
        "id": "job-1",
        "document_id": "document-1",
        "course_id": "course-1",
        "status": "parsing",
        "state_version": 3,
        "attempt": 1,
        "max_attempts": 3,
        "lease_version": 2,
        "lease_expires_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        "parser_profile": "native-v1",
        "parser_schema_version": "1.0",
        "progress": {},
        "failure_code": None,
        "retryable": None,
        "available_at": now,
        "created_at": now,
        "updated_at": now,
    }


def _claim_request() -> JobClaimRequest:
    return JobClaimRequest(
        worker_id="worker-1",
        capabilities=WorkerCapabilities(
            parser_profiles=["native-v1"],
            media_types=["application/pdf"],
            max_input_bytes=1024,
            max_pages=20,
        ),
    )


@pytest.mark.asyncio
async def test_client_uses_worker_auth_for_control_plane_and_parses_claim() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"lease": None, "retry_after_ms": 250})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WorkerClient(
            base_url="http://127.0.0.1:8000",
            worker_id="worker-1",
            token=SecretStr("control-plane-secret"),
            timeout_seconds=10,
            http_client=http_client,
        )
        response = await client.claim(_claim_request(), wait_seconds=17)

    assert response.lease is None
    assert response.retry_after_ms == 250
    assert seen[0].url.path == "/worker/v1/jobs:claim"
    assert seen[0].url.params["wait_seconds"] == "17"
    assert seen[0].headers["authorization"] == "Bearer control-plane-secret"


@pytest.mark.asyncio
async def test_client_sends_stable_idempotency_header_for_commands() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_snapshot_json())

    transport = httpx.MockTransport(handler)
    lease = _lease()
    request = JobStartRequest(
        worker_id="worker-1",
        lease_token=lease.lease_token,
        lease_version=lease.lease_version,
        attempt=lease.attempt,
        deletion_epoch=lease.deletion_epoch,
    )
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WorkerClient(
            base_url="http://127.0.0.1:8000",
            worker_id="worker-1",
            token="secret",
            timeout_seconds=10,
            http_client=http_client,
        )
        snapshot = await client.start(
            lease.job_id,
            request,
            idempotency_key="stable-command-key",
        )

    assert snapshot.id == lease.job_id
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/worker/v1/jobs/job-1:start"
    assert seen[0].headers["idempotency-key"] == "stable-command-key"


@pytest.mark.asyncio
async def test_client_maps_lease_lost_without_exposing_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "type": "about:blank",
                "title": "Lease lost",
                "status": 409,
                "code": "LEASE_LOST",
                "trace_id": "trace-1",
                "retryable": False,
                "field_errors": [],
            },
        )

    lease = _lease()
    command = JobCompleteRequest(
        worker_id="worker-1",
        lease_token=lease.lease_token,
        lease_version=lease.lease_version,
        attempt=lease.attempt,
        deletion_epoch=lease.deletion_epoch,
        result_manifest_ref="objects/result.json",
        result_sha256="b" * 64,
        result_size_bytes=10,
        page_count=1,
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WorkerClient(
            base_url="http://127.0.0.1:8000",
            worker_id="worker-1",
            token="control-plane-secret",
            timeout_seconds=10,
            http_client=http_client,
        )
        with pytest.raises(LeaseLostError) as caught:
            await client.complete("job-1", command, idempotency_key="complete-key")

    rendered = f"{caught.value!r} {caught.value}"
    assert caught.value.code == "LEASE_LOST"
    assert lease.lease_token not in rendered
    assert "control-plane-secret" not in rendered


@pytest.mark.asyncio
async def test_signed_input_download_never_receives_worker_authorization(tmp_path: Path) -> None:
    payload = b"%PDF-1.7\nminimal"
    digest = hashlib.sha256(payload).hexdigest()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=payload)

    lease = _lease(input_url="https://objects.example/signed/input.pdf?sig=sensitive")
    lease = lease.model_copy(update={"document_sha256": digest})
    transport = httpx.MockTransport(handler)
    destination = tmp_path / "input.bin"
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WorkerClient(
            base_url="http://127.0.0.1:8000",
            worker_id="worker-1",
            token="control-plane-secret",
            timeout_seconds=10,
            http_client=http_client,
        )
        size = await client.download_input(lease, destination, max_bytes=1024)

    assert size == len(payload)
    assert destination.read_bytes() == payload
    assert "authorization" not in seen[0].headers


@pytest.mark.asyncio
async def test_local_input_is_confined_to_configured_storage_root(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    source = storage_root / "courses" / "document.pdf"
    source.parent.mkdir(parents=True)
    payload = b"%PDF-local"
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    destination = tmp_path / "sandbox" / "input.bin"
    destination.parent.mkdir()

    client = WorkerClient(
        base_url="http://127.0.0.1:8000",
        worker_id="worker-1",
        token="secret",
        timeout_seconds=10,
        local_storage_root=storage_root,
    )
    lease = _lease(input_url="local:///courses/document.pdf").model_copy(
        update={"document_sha256": digest}
    )
    try:
        assert await client.download_input(lease, destination, max_bytes=1024) == len(payload)
        with pytest.raises(InputDownloadError, match="INPUT_LOCATION_INVALID"):
            await client.download_input(
                lease.model_copy(update={"input_url": "local:///../outside.pdf"}),
                tmp_path / "sandbox" / "outside.bin",
                max_bytes=1024,
            )
        symlink = storage_root / "courses" / "linked.pdf"
        symlink.symlink_to(source)
        with pytest.raises(InputDownloadError, match="INPUT_LOCATION_INVALID"):
            await client.download_input(
                lease.model_copy(update={"input_url": "local:///courses/linked.pdf"}),
                tmp_path / "sandbox" / "linked.bin",
                max_bytes=1024,
            )
    finally:
        await client.aclose()


def test_owned_control_plane_client_never_inherits_ambient_proxy_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, object]] = []

    class StubClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(httpx, "AsyncClient", StubClient)

    WorkerClient(
        base_url="http://127.0.0.1:8000",
        worker_id="worker-1",
        token="secret",
        timeout_seconds=10,
    )

    assert created == [{"trust_env": False}]


@pytest.mark.asyncio
async def test_oversized_download_removes_partial_file(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"too-large")

    destination = tmp_path / "input.bin"
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WorkerClient(
            base_url="http://127.0.0.1:8000",
            worker_id="worker-1",
            token="secret",
            timeout_seconds=10,
            http_client=http_client,
        )
        with pytest.raises(InputDownloadError, match="INPUT_TOO_LARGE"):
            await client.download_input(_lease(), destination, max_bytes=4)

    assert not destination.exists()


@pytest.mark.asyncio
async def test_relative_control_plane_input_uses_lease_headers_without_leaking_them(
    tmp_path: Path,
) -> None:
    payload = b"%PDF-control-plane"
    digest = hashlib.sha256(payload).hexdigest()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=payload)

    lease = _lease(input_url="/worker/v1/jobs/job-1/input?lease_version=2").model_copy(
        update={"document_sha256": digest}
    )
    destination = tmp_path / "input.bin"
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WorkerClient(
            base_url="http://127.0.0.1:8000",
            worker_id="worker-1",
            token="control-plane-secret",
            timeout_seconds=10,
            http_client=http_client,
        )
        await client.download_input(lease, destination, max_bytes=1024)

    request = seen[0]
    assert request.url.path == "/worker/v1/jobs/job-1/input"
    assert request.headers["authorization"] == "Bearer control-plane-secret"
    assert request.headers["x-worker-id"] == "worker-1"
    assert request.headers["x-lease-token"] == lease.lease_token
    assert request.headers["x-lease-version"] == "2"
    assert request.headers["x-attempt"] == "1"
    assert request.headers["x-deletion-epoch"] == "0"


@pytest.mark.asyncio
async def test_relative_input_lease_error_does_not_expose_download_credentials(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "title": "Lease lost",
                "status": 409,
                "code": "LEASE_LOST",
                "trace_id": "trace-1",
                "retryable": False,
            },
        )

    lease = _lease(input_url="/worker/v1/jobs/job-1/input?lease_version=2")
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WorkerClient(
            base_url="http://127.0.0.1:8000",
            worker_id="worker-1",
            token="control-plane-secret",
            timeout_seconds=10,
            http_client=http_client,
        )
        with pytest.raises(LeaseLostError) as caught:
            await client.download_input(lease, tmp_path / "input.bin", max_bytes=1024)

    rendered = f"{caught.value!r} {caught.value}"
    assert lease.lease_token not in rendered
    assert "control-plane-secret" not in rendered


@pytest.mark.asyncio
async def test_artifact_upload_is_same_origin_and_receipt_verified(tmp_path: Path) -> None:
    payload = b'{"schema_version":"1.0"}'
    digest = hashlib.sha256(payload).hexdigest()
    source = tmp_path / "manifest.json"
    source.write_bytes(payload)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201,
            json={
                "artifact_ref": "jobs/job-1/attempt-1/manifest.json",
                "artifact_name": "manifest.json",
                "size_bytes": len(payload),
                "sha256": digest,
                "media_type": "application/json",
                "artifact_schema_version": "1.0",
            },
        )

    lease = _lease()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WorkerClient(
            base_url="http://127.0.0.1:8000",
            worker_id="worker-1",
            token="control-plane-secret",
            timeout_seconds=10,
            http_client=http_client,
        )
        receipt = await client.upload_artifact(
            lease,
            artifact_name="manifest.json",
            source=source,
            media_type="application/json",
            max_bytes=1024,
        )

    assert receipt.sha256 == digest
    request = seen[0]
    assert request.url.path == "/worker/v1/jobs/job-1/artifacts/manifest.json"
    assert request.url.params["artifact_schema_version"] == "1.0"
    assert request.headers["authorization"] == "Bearer control-plane-secret"
    assert request.headers["x-lease-token"] == lease.lease_token
    assert request.content == payload
