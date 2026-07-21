"""Typed HTTP client for the versioned worker control-plane API."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath
from typing import TypeVar
from urllib.parse import SplitResult, quote, unquote, urljoin, urlsplit

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

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
    WorkerLease,
)

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class WorkerApiError(RuntimeError):
    """A sanitized control-plane failure that never retains request headers."""

    def __init__(
        self,
        *,
        code: str,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after_ms: int | None = None,
    ) -> None:
        status = "transport" if status_code is None else str(status_code)
        super().__init__(f"worker API request failed ({status}, {code})")
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms


class WorkerTransportError(WorkerApiError):
    def __init__(self, *, code: str = "WORKER_API_UNAVAILABLE") -> None:
        super().__init__(code=code, retryable=True)


class WorkerProtocolError(WorkerApiError):
    def __init__(self, *, code: str = "WORKER_API_PROTOCOL_ERROR") -> None:
        super().__init__(code=code, retryable=False)


class LeaseLostError(WorkerApiError):
    pass


class InputDownloadError(RuntimeError):
    """A normalized input-fetch failure safe for a JobFail request."""

    def __init__(self, *, code: str, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class ArtifactUploadError(RuntimeError):
    """A local artifact failure safe for a JobFail request."""

    def __init__(self, *, code: str, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class WorkerClient:
    """Own worker commands and signed/local input downloads."""

    def __init__(
        self,
        *,
        base_url: str,
        worker_id: str,
        token: SecretStr | str | None,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
        local_storage_root: Path | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in worker_id):
            raise ValueError("worker_id must not contain control characters")
        self._worker_id = worker_id
        parsed_base_url = urlsplit(self._base_url)
        if (
            parsed_base_url.username is not None
            or parsed_base_url.password is not None
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise ValueError("worker API base URL contains forbidden components")
        self._base_origin = self._origin(parsed_base_url)
        self._token: str | None
        if isinstance(token, SecretStr):
            self._token = token.get_secret_value()
        else:
            self._token = token
        self._timeout = timeout_seconds
        # Worker control-plane traffic is explicitly configured and may be loopback-only.
        # Never inherit ambient proxy discovery for this security boundary.
        self._http = http_client or httpx.AsyncClient(trust_env=False)
        self._owns_http_client = http_client is None
        self._local_storage_root = (
            local_storage_root.expanduser().resolve(strict=False)
            if local_storage_root is not None
            else None
        )

    async def __aenter__(self) -> WorkerClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def claim(
        self,
        request: JobClaimRequest,
        *,
        wait_seconds: int = 0,
    ) -> JobClaimResponse:
        if not 0 <= wait_seconds <= 30:
            raise ValueError("wait_seconds must be between 0 and 30")
        self._require_worker_identity(request.worker_id)
        return await self._request(
            "POST",
            "/worker/v1/jobs:claim",
            request,
            JobClaimResponse,
            params={"wait_seconds": str(wait_seconds)},
        )

    async def start(
        self,
        job_id: str,
        request: JobStartRequest,
        *,
        idempotency_key: str,
    ) -> JobSnapshot:
        return await self._command(
            "POST", job_id, ":start", request, idempotency_key=idempotency_key
        )

    async def heartbeat(
        self,
        job_id: str,
        request: JobHeartbeatRequest,
        *,
        idempotency_key: str,
    ) -> JobSnapshot:
        return await self._command(
            "PUT", job_id, "/heartbeat", request, idempotency_key=idempotency_key
        )

    async def checkpoint(
        self,
        job_id: str,
        request: PageCheckpointRequest,
        *,
        idempotency_key: str,
    ) -> JobSnapshot:
        suffix = f"/pages/{request.page_ordinal}/checkpoint"
        return await self._command("PUT", job_id, suffix, request, idempotency_key=idempotency_key)

    async def complete(
        self,
        job_id: str,
        request: JobCompleteRequest,
        *,
        idempotency_key: str,
    ) -> JobSnapshot:
        return await self._command(
            "POST", job_id, ":complete", request, idempotency_key=idempotency_key
        )

    async def fail(
        self,
        job_id: str,
        request: JobFailRequest,
        *,
        idempotency_key: str,
    ) -> JobSnapshot:
        return await self._command(
            "POST", job_id, ":fail", request, idempotency_key=idempotency_key
        )

    async def download_input(
        self,
        lease: WorkerLease,
        destination: Path,
        *,
        max_bytes: int,
    ) -> int:
        """Fetch a lease input without forwarding the worker bearer token."""

        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if destination.exists():
            raise InputDownloadError(code="INPUT_DESTINATION_EXISTS")
        parsed = urlsplit(lease.input_url)
        if parsed.scheme == "local":
            return self._copy_local_input(lease, destination, max_bytes=max_bytes)
        if parsed.scheme not in {"", "http", "https"}:
            raise InputDownloadError(code="INPUT_LOCATION_INVALID")
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise InputDownloadError(code="INPUT_LOCATION_INVALID")
        if not parsed.scheme and (parsed.netloc or not parsed.path.startswith("/")):
            raise InputDownloadError(code="INPUT_LOCATION_INVALID")
        input_url = urljoin(f"{self._base_url}/", lease.input_url)
        try:
            control_plane_request = self._origin(urlsplit(input_url)) == self._base_origin
        except ValueError:
            raise InputDownloadError(code="INPUT_LOCATION_INVALID") from None
        return await self._download_http_input(
            lease,
            destination,
            input_url=input_url,
            control_plane_request=control_plane_request,
            max_bytes=max_bytes,
        )

    async def upload_artifact(
        self,
        lease: WorkerLease,
        *,
        artifact_name: str,
        source: Path,
        media_type: str,
        max_bytes: int,
    ) -> JobArtifactReceipt:
        """Upload one immutable result to the lease-scoped control-plane endpoint."""

        if (
            not artifact_name
            or artifact_name in {".", ".."}
            or "/" in artifact_name
            or "\\" in artifact_name
            or "\x00" in artifact_name
        ):
            raise ArtifactUploadError(code="ARTIFACT_NAME_INVALID")
        if "/" not in media_type or media_type.startswith("/") or media_type.endswith("/"):
            raise ArtifactUploadError(code="ARTIFACT_MEDIA_TYPE_INVALID")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if source.is_symlink() or not source.is_file():
            raise ArtifactUploadError(code="ARTIFACT_NOT_FOUND")

        endpoint = urljoin(f"{self._base_url}/", lease.artifact_upload_url)
        parsed_endpoint = urlsplit(endpoint)
        if (
            self._origin(parsed_endpoint) != self._base_origin
            or parsed_endpoint.query
            or parsed_endpoint.fragment
        ):
            raise ArtifactUploadError(code="ARTIFACT_LOCATION_INVALID")
        size, digest = self._hash_file(source, max_bytes=max_bytes)
        upload_url = f"{endpoint.rstrip('/')}/{quote(artifact_name, safe='')}"

        async def body() -> AsyncIterator[bytes]:
            try:
                with source.open("rb") as stream:
                    while block := stream.read(1024 * 1024):
                        yield block
            except OSError:
                raise ArtifactUploadError(code="ARTIFACT_READ_FAILED", retryable=True) from None

        headers = self._lease_headers(lease)
        headers.update(
            {
                "Content-Type": media_type,
                "Content-Length": str(size),
            }
        )
        try:
            response = await self._http.request(
                "PUT",
                upload_url,
                params={"artifact_schema_version": "1.0"},
                headers=headers,
                content=body(),
                timeout=self._timeout,
            )
        except ArtifactUploadError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            raise WorkerTransportError from None
        if response.is_error:
            self._raise_api_error(response)
        if response.status_code != 201:
            raise WorkerProtocolError(code="ARTIFACT_UPLOAD_STATUS_INVALID")
        try:
            receipt = JobArtifactReceipt.model_validate(response.json())
        except (ValueError, ValidationError):
            raise WorkerProtocolError from None
        if (
            receipt.artifact_name != artifact_name
            or receipt.size_bytes != size
            or receipt.sha256 != digest
            or receipt.media_type != media_type
        ):
            raise WorkerProtocolError(code="ARTIFACT_RECEIPT_MISMATCH")
        return receipt

    async def _command(
        self,
        method: str,
        job_id: str,
        suffix: str,
        request: BaseModel,
        *,
        idempotency_key: str,
    ) -> JobSnapshot:
        if not idempotency_key or "\n" in idempotency_key or "\r" in idempotency_key:
            raise ValueError("idempotency_key must be a non-empty header value")
        command_worker_id = getattr(request, "worker_id", None)
        if not isinstance(command_worker_id, str):
            raise ValueError("worker command is missing worker_id")
        self._require_worker_identity(command_worker_id)
        encoded_job_id = quote(job_id, safe="")
        return await self._request(
            method,
            f"/worker/v1/jobs/{encoded_job_id}{suffix}",
            request,
            JobSnapshot,
            extra_headers={"Idempotency-Key": idempotency_key},
        )

    async def _request(
        self,
        method: str,
        path: str,
        request: BaseModel,
        response_model: type[ResponseModel],
        *,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> ResponseModel:
        headers = self._control_plane_headers()
        if extra_headers is not None:
            headers.update(extra_headers)
        try:
            response = await self._http.request(
                method,
                f"{self._base_url}{path}",
                json=request.model_dump(mode="json"),
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
        except (httpx.TimeoutException, httpx.TransportError):
            raise WorkerTransportError from None
        if response.is_error:
            self._raise_api_error(response)
        try:
            return response_model.model_validate(response.json())
        except (ValueError, ValidationError):
            raise WorkerProtocolError from None

    def _control_plane_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _require_worker_identity(self, worker_id: str) -> None:
        if worker_id != self._worker_id:
            raise ValueError("worker request identity does not match the client")

    @staticmethod
    def _raise_api_error(response: httpx.Response) -> None:
        code = f"WORKER_API_HTTP_{response.status_code}"
        retryable = response.status_code >= 500 or response.status_code == 429
        retry_after_ms: int | None = None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            candidate = payload.get("code")
            if (
                isinstance(candidate, str)
                and 1 <= len(candidate) <= 128
                and "A" <= candidate[0] <= "Z"
                and all(
                    character == "_" or character.isdigit() or "A" <= character <= "Z"
                    for character in candidate
                )
            ):
                code = candidate
            retryable_value = payload.get("retryable")
            if isinstance(retryable_value, bool):
                retryable = retryable_value
            retry_after_value = payload.get("retry_after_ms")
            if isinstance(retry_after_value, int) and retry_after_value >= 0:
                retry_after_ms = retry_after_value
        error_type = LeaseLostError if code == "LEASE_LOST" else WorkerApiError
        raise error_type(
            status_code=response.status_code,
            code=code,
            retryable=retryable,
            retry_after_ms=retry_after_ms,
        )

    async def _download_http_input(
        self,
        lease: WorkerLease,
        destination: Path,
        *,
        input_url: str,
        control_plane_request: bool,
        max_bytes: int,
    ) -> int:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        headers = {"Accept": "application/octet-stream"}
        if control_plane_request:
            headers.update(self._lease_headers(lease))
        try:
            async with self._http.stream(
                "GET",
                input_url,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
            ) as response:
                if response.is_error and control_plane_request:
                    self._raise_api_error(response)
                if response.is_error or response.is_redirect:
                    retryable = response.status_code >= 500 or response.status_code == 429
                    raise InputDownloadError(
                        code="INPUT_DOWNLOAD_FAILED",
                        retryable=retryable,
                    )
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        advertised_size = int(content_length)
                    except ValueError:
                        raise InputDownloadError(code="INPUT_RESPONSE_INVALID") from None
                    if advertised_size > max_bytes:
                        raise InputDownloadError(code="INPUT_TOO_LARGE")
                with destination.open("xb") as stream:
                    async for block in response.aiter_bytes():
                        size += len(block)
                        if size > max_bytes:
                            raise InputDownloadError(code="INPUT_TOO_LARGE")
                        stream.write(block)
                        digest.update(block)
        except InputDownloadError:
            destination.unlink(missing_ok=True)
            raise
        except (httpx.TimeoutException, httpx.TransportError, OSError):
            destination.unlink(missing_ok=True)
            raise InputDownloadError(code="INPUT_DOWNLOAD_FAILED", retryable=True) from None
        self._verify_download(lease, destination, digest.hexdigest())
        return size

    def _copy_local_input(
        self,
        lease: WorkerLease,
        destination: Path,
        *,
        max_bytes: int,
    ) -> int:
        if self._local_storage_root is None:
            raise InputDownloadError(code="LOCAL_STORAGE_UNAVAILABLE")
        parsed = urlsplit(lease.input_url)
        if parsed.netloc or parsed.query or parsed.fragment:
            raise InputDownloadError(code="INPUT_LOCATION_INVALID")
        decoded = unquote(parsed.path)
        parts = PurePosixPath(decoded.lstrip("/")).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise InputDownloadError(code="INPUT_LOCATION_INVALID")
        candidate = self._local_storage_root / Path(*parts)
        current = self._local_storage_root
        for part in parts:
            current /= part
            if current.is_symlink():
                raise InputDownloadError(code="INPUT_LOCATION_INVALID")
        source = candidate.resolve(strict=False)
        try:
            source.relative_to(self._local_storage_root)
        except ValueError:
            raise InputDownloadError(code="INPUT_LOCATION_INVALID") from None
        if source.is_symlink() or not source.is_file():
            raise InputDownloadError(code="INPUT_NOT_FOUND", retryable=True)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as source_stream, destination.open("xb") as destination_stream:
                while block := source_stream.read(1024 * 1024):
                    size += len(block)
                    if size > max_bytes:
                        raise InputDownloadError(code="INPUT_TOO_LARGE")
                    destination_stream.write(block)
                    digest.update(block)
        except InputDownloadError:
            destination.unlink(missing_ok=True)
            raise
        except OSError:
            destination.unlink(missing_ok=True)
            raise InputDownloadError(code="INPUT_DOWNLOAD_FAILED", retryable=True) from None
        self._verify_download(lease, destination, digest.hexdigest())
        return size

    @staticmethod
    def _verify_download(lease: WorkerLease, destination: Path, digest: str) -> None:
        if digest != lease.document_sha256:
            destination.unlink(missing_ok=True)
            raise InputDownloadError(code="INPUT_HASH_MISMATCH")

    def _lease_headers(self, lease: WorkerLease) -> dict[str, str]:
        headers = self._control_plane_headers()
        headers.update(
            {
                "X-Worker-ID": self._worker_id,
                "X-Lease-Token": lease.lease_token,
                "X-Lease-Version": str(lease.lease_version),
                "X-Attempt": str(lease.attempt),
                "X-Deletion-Epoch": str(lease.deletion_epoch),
            }
        )
        return headers

    @staticmethod
    def _hash_file(source: Path, *, max_bytes: int) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    size += len(block)
                    if size > max_bytes:
                        raise ArtifactUploadError(code="ARTIFACT_TOO_LARGE")
                    digest.update(block)
        except ArtifactUploadError:
            raise
        except OSError:
            raise ArtifactUploadError(code="ARTIFACT_READ_FAILED", retryable=True) from None
        return size, digest.hexdigest()

    @staticmethod
    def _origin(parsed: SplitResult) -> tuple[str, str, int | None]:
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("worker API base URL must be HTTP(S) with a host")
        default_port = 443 if parsed.scheme == "https" else 80
        return parsed.scheme, parsed.hostname.lower(), parsed.port or default_port
