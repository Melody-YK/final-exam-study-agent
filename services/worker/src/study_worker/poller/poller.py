"""Recoverable pull loop for one-at-a-time M1 worker execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import signal
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Protocol

from study_contracts import (
    JobArtifactReceipt,
    JobClaimRequest,
    JobClaimResponse,
    JobCompleteRequest,
    JobFailRequest,
    JobHeartbeatRequest,
    JobProgress,
    JobStartRequest,
    PageCheckpointRequest,
    WorkerCapabilities,
    WorkerLease,
)
from study_worker.dispatcher import (
    Dispatcher,
    JobReporter,
    PageCheckpoint,
    TaskExecutionError,
    TaskResult,
)
from study_worker.poller.client import (
    ArtifactUploadError,
    InputDownloadError,
    LeaseLostError,
    WorkerApiError,
)
from study_worker.sandbox import Sandbox, SandboxManager


class Clock(Protocol):
    def now(self) -> datetime: ...


class Sleeper(Protocol):
    async def sleep(self, seconds: float) -> None: ...


class WorkerControlPlane(Protocol):
    async def claim(self, request: JobClaimRequest, *, wait_seconds: int) -> JobClaimResponse: ...

    async def start(
        self, job_id: str, request: JobStartRequest, *, idempotency_key: str
    ) -> object: ...

    async def heartbeat(
        self, job_id: str, request: JobHeartbeatRequest, *, idempotency_key: str
    ) -> object: ...

    async def checkpoint(
        self, job_id: str, request: PageCheckpointRequest, *, idempotency_key: str
    ) -> object: ...

    async def complete(
        self, job_id: str, request: JobCompleteRequest, *, idempotency_key: str
    ) -> object: ...

    async def fail(
        self, job_id: str, request: JobFailRequest, *, idempotency_key: str
    ) -> object: ...

    async def download_input(
        self, lease: WorkerLease, destination: Path, *, max_bytes: int
    ) -> int: ...

    async def upload_artifact(
        self,
        lease: WorkerLease,
        *,
        artifact_name: str,
        source: Path,
        media_type: str,
        max_bytes: int,
    ) -> JobArtifactReceipt: ...

    async def aclose(self) -> None: ...


class StoppableService(Protocol):
    def request_stop(self) -> None: ...

    async def run_forever(self) -> None: ...


class WorkerEventLogger:
    """Emit JSON events through a per-event field allowlist."""

    _ALLOWED_FIELDS: ClassVar[dict[str, frozenset[str]]] = {
        "claim": frozenset({"worker_id", "capability_count"}),
        "no-job": frozenset({"retry_after_ms"}),
        "start": frozenset({"job_id", "attempt", "lease_version"}),
        "checkpoint": frozenset({"job_id", "page_ordinal", "status"}),
        "complete": frozenset({"job_id", "page_count", "failed_page_count"}),
        "fail": frozenset({"job_id", "error_code", "retryable"}),
        "lease-lost": frozenset({"job_id", "operation"}),
        "stop": frozenset({"mode"}),
    }

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("study_worker.events")

    @classmethod
    def configure_stderr(cls) -> None:
        """Enable only the allowlisted worker event logger for CLI runtimes."""

        logger = logging.getLogger("study_worker.events")
        logger.setLevel(logging.INFO)
        if not any(handler.get_name() == "study-worker-json" for handler in logger.handlers):
            handler = logging.StreamHandler()
            handler.set_name("study-worker-json")
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        logger.propagate = False

    def emit(self, event: str, **fields: str | int | bool) -> None:
        allowed = self._ALLOWED_FIELDS.get(event)
        if allowed is None or not fields.keys() <= allowed:
            raise ValueError("worker event contains non-allowlisted fields")
        payload: dict[str, str | int | bool] = {"event": event}
        payload.update(fields)
        self._logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class AsyncioSleeper:
    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class IdleWorker:
    """A healthy zero-capability process that never contacts the control plane."""

    capability_count = 0

    def __init__(self, event_logger: WorkerEventLogger | None = None) -> None:
        self._stop_requested = asyncio.Event()
        self._event_logger = event_logger or WorkerEventLogger()
        self._stop_logged = False

    def request_stop(self) -> None:
        self._stop_requested.set()

    async def run_forever(self) -> None:
        try:
            await self._stop_requested.wait()
        finally:
            if not self._stop_logged:
                self._event_logger.emit("stop", mode="idle")
                self._stop_logged = True


class _LeaseReporter(JobReporter):
    def __init__(
        self,
        *,
        client: WorkerControlPlane,
        worker_id: str,
        lease: WorkerLease,
        max_artifact_bytes: int,
        output_dir: Path,
        event_logger: WorkerEventLogger,
    ) -> None:
        self._client = client
        self._worker_id = worker_id
        self._lease = lease
        self._max_artifact_bytes = max_artifact_bytes
        self._output_dir = output_dir.resolve(strict=True)
        self._event_logger = event_logger
        total_pages = len(lease.requested_pages) or None
        self._progress = JobProgress(
            phase="starting",
            completed_pages=0,
            total_pages=total_pages,
        )
        self._completed_pages: set[int] = set()
        self._heartbeat_sequence = 0

    def update_progress(self, progress: JobProgress) -> None:
        self._progress = progress

    async def upload_artifact(
        self,
        *,
        artifact_name: str,
        source: Path,
        media_type: str,
    ) -> JobArtifactReceipt:
        candidate = source.resolve(strict=False)
        try:
            candidate.relative_to(self._output_dir)
        except ValueError:
            raise ArtifactUploadError(code="ARTIFACT_LOCATION_INVALID") from None
        current = self._output_dir
        relative_parts = candidate.relative_to(self._output_dir).parts
        for part in relative_parts:
            current /= part
            if current.is_symlink():
                raise ArtifactUploadError(code="ARTIFACT_LOCATION_INVALID")
        return await self._client.upload_artifact(
            self._lease,
            artifact_name=artifact_name,
            source=source,
            media_type=media_type,
            max_bytes=self._max_artifact_bytes,
        )

    async def heartbeat(self) -> None:
        self._heartbeat_sequence += 1
        request = JobHeartbeatRequest(
            **self._lease_fields(),
            progress=self._progress,
        )
        await self._client.heartbeat(
            self._lease.job_id,
            request,
            idempotency_key=_idempotency_key(
                self._worker_id,
                self._lease,
                "heartbeat",
                str(self._heartbeat_sequence),
            ),
        )

    async def checkpoint(self, checkpoint: PageCheckpoint) -> None:
        request = PageCheckpointRequest(
            **self._lease_fields(),
            page_ordinal=checkpoint.page_ordinal,
            status=checkpoint.status,
            output_ref=checkpoint.output_ref,
            output_sha256=checkpoint.output_sha256,
            output_size_bytes=checkpoint.output_size_bytes,
            output_schema_version=checkpoint.output_schema_version,
            source_backend=checkpoint.source_backend,
            source_version=checkpoint.source_version,
            error_code=checkpoint.error_code,
        )
        await self._client.checkpoint(
            self._lease.job_id,
            request,
            idempotency_key=_idempotency_key(
                self._worker_id,
                self._lease,
                "checkpoint",
                str(checkpoint.page_ordinal),
            ),
        )
        self._event_logger.emit(
            "checkpoint",
            job_id=self._lease.job_id,
            page_ordinal=checkpoint.page_ordinal,
            status=checkpoint.status,
        )
        self._completed_pages.add(checkpoint.page_ordinal)
        self._progress = JobProgress(
            phase="parsing",
            completed_pages=len(self._completed_pages),
            total_pages=self._progress.total_pages,
        )

    def _lease_fields(self) -> dict[str, object]:
        return {
            "worker_id": self._worker_id,
            "lease_token": self._lease.lease_token,
            "lease_version": self._lease.lease_version,
            "attempt": self._lease.attempt,
            "deletion_epoch": self._lease.deletion_epoch,
        }


class Poller:
    """Claim one durable job at a time and report only lease-bound commands."""

    def __init__(
        self,
        *,
        client: WorkerControlPlane,
        dispatcher: Dispatcher,
        sandboxes: SandboxManager,
        worker_id: str,
        capabilities: WorkerCapabilities,
        clock: Clock | None = None,
        sleeper: Sleeper | None = None,
        timeout_sleeper: Sleeper | None = None,
        event_logger: WorkerEventLogger | None = None,
        poll_wait_seconds: int = 20,
        heartbeat_interval_seconds: float = 10,
        task_timeout_seconds: float = 180,
        backoff_initial_seconds: float = 0.5,
        backoff_max_seconds: float = 30,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if not 0 <= poll_wait_seconds <= 30:
            raise ValueError("poll_wait_seconds must be between 0 and 30")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if task_timeout_seconds <= 0:
            raise ValueError("task_timeout_seconds must be positive")
        if backoff_initial_seconds <= 0 or backoff_max_seconds < backoff_initial_seconds:
            raise ValueError("invalid poll backoff bounds")
        self._client = client
        self._dispatcher = dispatcher
        self._sandboxes = sandboxes
        self._worker_id = worker_id
        self._capabilities = capabilities
        self._clock = clock or SystemClock()
        self._sleeper = sleeper or AsyncioSleeper()
        self._timeout_sleeper = timeout_sleeper or AsyncioSleeper()
        self._event_logger = event_logger or WorkerEventLogger()
        self._poll_wait_seconds = poll_wait_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._task_timeout_seconds = task_timeout_seconds
        self._backoff_initial_seconds = backoff_initial_seconds
        self._backoff_max_seconds = backoff_max_seconds
        self._stop_requested = asyncio.Event()
        self._active_execution: asyncio.Task[TaskResult] | None = None
        self._stop_logged = False

    def request_stop(self) -> None:
        """Stop the loop and cancel active untrusted task execution."""

        self._stop_requested.set()
        if self._active_execution is not None:
            self._active_execution.cancel()

    async def run_once(self) -> int:
        """Run one claim cycle and return the server-recommended idle delay in ms."""

        self._event_logger.emit(
            "claim",
            worker_id=self._worker_id,
            capability_count=len(self._capabilities.parser_profiles),
        )
        response = await self._client.claim(
            JobClaimRequest(
                worker_id=self._worker_id,
                capabilities=self._capabilities,
            ),
            wait_seconds=self._poll_wait_seconds,
        )
        if response.lease is None:
            self._event_logger.emit("no-job", retry_after_ms=response.retry_after_ms)
            return response.retry_after_ms
        if response.lease.lease_expires_at <= self._clock.now():
            return 0
        await self._process(response.lease)
        return 0

    async def run_forever(self) -> None:
        failures = 0
        try:
            while not self._stop_requested.is_set():
                try:
                    idle_delay_ms = await self.run_once()
                except WorkerApiError as exc:
                    if not exc.retryable:
                        raise
                    failures += 1
                    if exc.retry_after_ms is not None:
                        delay = exc.retry_after_ms / 1000
                    else:
                        delay = min(
                            self._backoff_max_seconds,
                            self._backoff_initial_seconds * (2 ** (failures - 1)),
                        )
                except asyncio.CancelledError:
                    if self._stop_requested.is_set():
                        return
                    raise
                else:
                    failures = 0
                    delay = idle_delay_ms / 1000
                if delay > 0:
                    await self._sleep_or_stop(delay)
        finally:
            self._sandboxes.cleanup_all()
            self._log_stop()
            await self._client.aclose()

    async def _process(self, lease: WorkerLease) -> None:
        try:
            await self._client.start(
                lease.job_id,
                JobStartRequest(**self._lease_fields(lease)),
                idempotency_key=_idempotency_key(self._worker_id, lease, "start"),
            )
        except LeaseLostError:
            self._event_logger.emit("lease-lost", job_id=lease.job_id, operation="start")
            return
        self._event_logger.emit(
            "start",
            job_id=lease.job_id,
            attempt=lease.attempt,
            lease_version=lease.lease_version,
        )

        with self._sandboxes.create() as sandbox:
            reporter = _LeaseReporter(
                client=self._client,
                worker_id=self._worker_id,
                lease=lease,
                max_artifact_bytes=self._capabilities.max_input_bytes,
                output_dir=sandbox.output_dir,
                event_logger=self._event_logger,
            )
            try:
                result = await self._execute_with_heartbeats(lease, sandbox, reporter)
            except LeaseLostError:
                self._event_logger.emit("lease-lost", job_id=lease.job_id, operation="execute")
                return
            except InputDownloadError as exc:
                await self._notify_failure(
                    lease,
                    code=exc.code,
                    retryable=exc.retryable,
                    summary=None,
                )
                return
            except ArtifactUploadError as exc:
                await self._notify_failure(
                    lease,
                    code=exc.code,
                    retryable=exc.retryable,
                    summary=None,
                )
                return
            except TaskExecutionError as exc:
                await self._notify_failure(
                    lease,
                    code=exc.code,
                    retryable=exc.retryable,
                    summary=self._safe_summary(lease, exc.summary),
                )
                return
            except asyncio.CancelledError:
                raise
            except WorkerApiError:
                raise
            except Exception:
                await self._notify_failure(
                    lease,
                    code="WORKER_INTERNAL_ERROR",
                    retryable=True,
                    summary="worker task execution failed",
                )
                return

            try:
                await self._client.complete(
                    lease.job_id,
                    JobCompleteRequest(
                        **self._lease_fields(lease),
                        result_manifest_ref=result.result_manifest_ref,
                        result_sha256=result.result_sha256,
                        result_size_bytes=result.result_size_bytes,
                        manifest_schema_version=result.manifest_schema_version,
                        page_count=result.page_count,
                        failed_pages=list(result.failed_pages),
                    ),
                    idempotency_key=_idempotency_key(self._worker_id, lease, "complete"),
                )
                self._event_logger.emit(
                    "complete",
                    job_id=lease.job_id,
                    page_count=result.page_count,
                    failed_page_count=len(result.failed_pages),
                )
            except LeaseLostError:
                self._event_logger.emit("lease-lost", job_id=lease.job_id, operation="complete")
                return

    async def _execute_with_heartbeats(
        self,
        lease: WorkerLease,
        sandbox: Sandbox,
        reporter: _LeaseReporter,
    ) -> TaskResult:
        execution = asyncio.create_task(self._execute_task(lease, sandbox, reporter))
        heartbeat = asyncio.create_task(self._heartbeat_loop(reporter))
        self._active_execution = execution
        try:
            done, _ = await asyncio.wait(
                {execution, heartbeat},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done:
                execution.cancel()
                with suppress(asyncio.CancelledError):
                    await execution
                heartbeat.result()
                raise RuntimeError("heartbeat loop stopped unexpectedly")
            result = execution.result()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            return result
        finally:
            self._active_execution = None
            if not execution.done():
                execution.cancel()
            if not heartbeat.done():
                heartbeat.cancel()

    async def _execute_task(
        self,
        lease: WorkerLease,
        sandbox: Sandbox,
        reporter: _LeaseReporter,
    ) -> TaskResult:
        operation = asyncio.create_task(self._download_and_dispatch(lease, sandbox, reporter))
        deadline = asyncio.create_task(self._timeout_sleeper.sleep(self._task_timeout_seconds))
        try:
            done, _ = await asyncio.wait(
                {operation, deadline},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if operation in done:
                return operation.result()
            deadline.result()
            operation.cancel()
            with suppress(asyncio.CancelledError):
                await operation
            raise TaskExecutionError(
                code="PARSER_TIMEOUT",
                retryable=True,
                summary="worker task exceeded the configured deadline",
            )
        finally:
            for task in (operation, deadline):
                if not task.done():
                    task.cancel()
            for task in (operation, deadline):
                with suppress(asyncio.CancelledError):
                    await task

    async def _download_and_dispatch(
        self,
        lease: WorkerLease,
        sandbox: Sandbox,
        reporter: _LeaseReporter,
    ) -> TaskResult:
        await self._client.download_input(
            lease,
            sandbox.input_path,
            max_bytes=self._capabilities.max_input_bytes,
        )
        return await self._dispatcher.dispatch(lease, sandbox, reporter)

    async def _heartbeat_loop(self, reporter: _LeaseReporter) -> None:
        while True:
            await self._sleeper.sleep(self._heartbeat_interval_seconds)
            await reporter.heartbeat()

    async def _notify_failure(
        self,
        lease: WorkerLease,
        *,
        code: str,
        retryable: bool,
        summary: str | None,
    ) -> None:
        try:
            await self._client.fail(
                lease.job_id,
                JobFailRequest(
                    **self._lease_fields(lease),
                    error_code=code,
                    retryable=retryable,
                    error_summary=summary,
                ),
                idempotency_key=_idempotency_key(self._worker_id, lease, "fail", code),
            )
            self._event_logger.emit(
                "fail",
                job_id=lease.job_id,
                error_code=code,
                retryable=retryable,
            )
        except LeaseLostError:
            self._event_logger.emit("lease-lost", job_id=lease.job_id, operation="fail")
            return

    async def _sleep_or_stop(self, seconds: float) -> None:
        sleep_task = asyncio.create_task(self._sleeper.sleep(seconds))
        stop_task = asyncio.create_task(self._stop_requested.wait())
        done, pending = await asyncio.wait(
            {sleep_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        for task in done:
            task.result()

    def _lease_fields(self, lease: WorkerLease) -> dict[str, object]:
        return {
            "worker_id": self._worker_id,
            "lease_token": lease.lease_token,
            "lease_version": lease.lease_version,
            "attempt": lease.attempt,
            "deletion_epoch": lease.deletion_epoch,
        }

    @staticmethod
    def _safe_summary(lease: WorkerLease, summary: str | None) -> str | None:
        if summary is None:
            return None
        sensitive_values = (lease.lease_token, lease.input_url, lease.artifact_upload_url)
        if any(value and value in summary for value in sensitive_values):
            return "worker task failed with redacted details"
        return summary

    def _log_stop(self) -> None:
        if not self._stop_logged:
            self._event_logger.emit("stop", mode="polling")
            self._stop_logged = True


def _idempotency_key(worker_id: str, lease: WorkerLease, *parts: str) -> str:
    material = "\x00".join(
        (worker_id, lease.job_id, str(lease.attempt), str(lease.lease_version), *parts)
    )
    return f"worker-{hashlib.sha256(material.encode()).hexdigest()}"


async def run_with_signal_handlers(service: StoppableService) -> None:
    """Run a worker service until completion, mapping TERM/INT to cleanup."""

    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(handled_signal, service.request_stop)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(handled_signal)
    try:
        await service.run_forever()
    finally:
        for handled_signal in installed:
            loop.remove_signal_handler(handled_signal)
