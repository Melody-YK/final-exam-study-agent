from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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
    WorkerLease,
)
from study_worker.dispatcher import (
    Dispatcher,
    JobReporter,
    PageCheckpoint,
    TaskExecutionError,
    TaskResult,
    native_capabilities,
)
from study_worker.poller.client import LeaseLostError, WorkerTransportError
from study_worker.poller.poller import Poller, WorkerEventLogger
from study_worker.sandbox import Sandbox, SandboxManager


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value


class BlockingSleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []
        self.first_sleep_started = asyncio.Event()
        self.release_first_sleep = asyncio.Event()
        self._count = 0

    async def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._count += 1
        if self._count == 1:
            self.first_sleep_started.set()
            await self.release_first_sleep.wait()
            return
        await asyncio.Event().wait()


class CallbackSleeper:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.calls: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.callback()


class TriggeredSleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []
        self.started = asyncio.Event()
        self.trigger = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.started.set()
        await self.trigger.wait()


def _lease(now: datetime) -> WorkerLease:
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
        lease_expires_at=now + timedelta(minutes=1),
        input_url="local:///objects/input.pdf",
        artifact_upload_url="/worker/v1/jobs/job-1/artifacts",
        requested_pages=[1],
    )


class FakeClient:
    def __init__(self, claim_response: JobClaimResponse) -> None:
        self.claim_response = claim_response
        self.events: list[tuple[str, object]] = []
        self.heartbeat_seen = asyncio.Event()
        self.claim_error: Exception | None = None
        self.complete_error: Exception | None = None

    async def claim(self, request: JobClaimRequest, *, wait_seconds: int) -> JobClaimResponse:
        self.events.append(("claim", request))
        assert wait_seconds >= 0
        if self.claim_error is not None:
            raise self.claim_error
        return self.claim_response

    async def start(self, job_id: str, request: JobStartRequest, *, idempotency_key: str) -> object:
        self.events.append(("start", (job_id, request, idempotency_key)))
        return object()

    async def heartbeat(
        self, job_id: str, request: JobHeartbeatRequest, *, idempotency_key: str
    ) -> object:
        self.events.append(("heartbeat", (job_id, request, idempotency_key)))
        self.heartbeat_seen.set()
        return object()

    async def checkpoint(
        self, job_id: str, request: PageCheckpointRequest, *, idempotency_key: str
    ) -> object:
        self.events.append(("checkpoint", (job_id, request, idempotency_key)))
        return object()

    async def complete(
        self, job_id: str, request: JobCompleteRequest, *, idempotency_key: str
    ) -> object:
        self.events.append(("complete", (job_id, request, idempotency_key)))
        if self.complete_error is not None:
            raise self.complete_error
        return object()

    async def fail(self, job_id: str, request: JobFailRequest, *, idempotency_key: str) -> object:
        self.events.append(("fail", (job_id, request, idempotency_key)))
        return object()

    async def download_input(self, lease: WorkerLease, destination: Path, *, max_bytes: int) -> int:
        del lease
        payload = b"input"
        assert len(payload) <= max_bytes
        destination.write_bytes(payload)
        self.events.append(("download", destination))
        return len(payload)

    async def upload_artifact(
        self,
        lease: WorkerLease,
        *,
        artifact_name: str,
        source: Path,
        media_type: str,
        max_bytes: int,
    ) -> JobArtifactReceipt:
        del lease, artifact_name, source, media_type, max_bytes
        raise AssertionError("not used")

    async def aclose(self) -> None:
        self.events.append(("close", object()))


@pytest.mark.asyncio
async def test_poller_runs_start_heartbeat_checkpoint_and_complete_without_real_sleep(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    lease = _lease(now)
    client = FakeClient(JobClaimResponse(lease=lease, retry_after_ms=0))
    sleeper = BlockingSleeper()
    handler_started = asyncio.Event()
    finish_handler = asyncio.Event()

    async def handler(lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter) -> TaskResult:
        assert sandbox.input_path.read_bytes() == b"input"
        reporter.update_progress(JobProgress(phase="parsing", completed_pages=0, total_pages=1))
        handler_started.set()
        await finish_handler.wait()
        await reporter.checkpoint(
            PageCheckpoint(
                page_ordinal=1,
                status="succeeded",
                output_ref="objects/page-1.json",
                output_sha256="c" * 64,
                output_size_bytes=50,
                source_backend="pdf-native",
                source_version="1.0",
            )
        )
        return TaskResult(
            result_manifest_ref="objects/manifest.json",
            result_sha256="b" * 64,
            result_size_bytes=100,
            page_count=1,
        )

    poller = Poller(
        client=client,
        dispatcher=Dispatcher(parse_handler=handler),
        sandboxes=SandboxManager(tmp_path / "worker"),
        worker_id="worker-1",
        capabilities=native_capabilities(max_input_bytes=1024, max_pages=20),
        clock=FixedClock(now),
        sleeper=sleeper,
        poll_wait_seconds=10,
        heartbeat_interval_seconds=3,
    )

    cycle = asyncio.create_task(poller.run_once())
    await asyncio.wait_for(handler_started.wait(), timeout=1)
    await asyncio.wait_for(sleeper.first_sleep_started.wait(), timeout=1)
    sleeper.release_first_sleep.set()
    await asyncio.wait_for(client.heartbeat_seen.wait(), timeout=1)
    finish_handler.set()

    assert await asyncio.wait_for(cycle, timeout=1) == 0
    names = [name for name, _ in client.events]
    assert names[:3] == ["claim", "start", "download"]
    assert "heartbeat" in names
    assert names[-2:] == ["checkpoint", "complete"]
    complete = next(value for name, value in client.events if name == "complete")
    assert isinstance(complete, tuple)
    complete_request = complete[1]
    assert isinstance(complete_request, JobCompleteRequest)
    assert complete_request.lease_token == lease.lease_token
    assert complete_request.page_count == 1
    assert not list((tmp_path / "worker").glob("job-*"))


@pytest.mark.asyncio
async def test_handler_failure_is_normalized_and_does_not_send_exception_text(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    client = FakeClient(JobClaimResponse(lease=_lease(now)))

    async def handler(lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter) -> TaskResult:
        del lease, sandbox, reporter
        raise TaskExecutionError(
            code="PARSER_TIMEOUT",
            retryable=True,
            summary="native parser exceeded the configured deadline",
        )

    poller = Poller(
        client=client,
        dispatcher=Dispatcher(parse_handler=handler),
        sandboxes=SandboxManager(tmp_path / "worker"),
        worker_id="worker-1",
        capabilities=native_capabilities(max_input_bytes=1024, max_pages=20),
        clock=FixedClock(now),
        heartbeat_interval_seconds=5,
    )

    assert await poller.run_once() == 0
    failure = next(value for name, value in client.events if name == "fail")
    assert isinstance(failure, tuple)
    request = failure[1]
    assert isinstance(request, JobFailRequest)
    assert request.error_code == "PARSER_TIMEOUT"
    assert request.retryable is True
    assert request.error_summary == "native parser exceeded the configured deadline"
    assert all(name != "complete" for name, _ in client.events)


@pytest.mark.asyncio
async def test_handler_failure_summary_cannot_echo_lease_token(tmp_path: Path) -> None:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    lease = _lease(now)
    client = FakeClient(JobClaimResponse(lease=lease))

    async def handler(lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter) -> TaskResult:
        del sandbox, reporter
        raise TaskExecutionError(
            code="PARSER_TIMEOUT",
            retryable=True,
            summary=f"parser context accidentally included {lease.lease_token}",
        )

    poller = Poller(
        client=client,
        dispatcher=Dispatcher(parse_handler=handler),
        sandboxes=SandboxManager(tmp_path / "worker"),
        worker_id="worker-1",
        capabilities=native_capabilities(max_input_bytes=1024, max_pages=20),
        clock=FixedClock(now),
    )

    assert await poller.run_once() == 0
    failure = next(value for name, value in client.events if name == "fail")
    assert isinstance(failure, tuple)
    request = failure[1]
    assert isinstance(request, JobFailRequest)
    assert request.error_summary == "worker task failed with redacted details"
    assert lease.lease_token not in request.error_summary


@pytest.mark.asyncio
async def test_late_complete_is_abandoned_without_a_second_callback(tmp_path: Path) -> None:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    client = FakeClient(JobClaimResponse(lease=_lease(now)))
    client.complete_error = LeaseLostError(status_code=409, code="LEASE_LOST")

    async def handler(lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter) -> TaskResult:
        del lease, sandbox, reporter
        return TaskResult(
            result_manifest_ref="objects/manifest.json",
            result_sha256="b" * 64,
            result_size_bytes=100,
            page_count=0,
        )

    poller = Poller(
        client=client,
        dispatcher=Dispatcher(parse_handler=handler),
        sandboxes=SandboxManager(tmp_path / "worker"),
        worker_id="worker-1",
        capabilities=native_capabilities(max_input_bytes=1024, max_pages=20),
        clock=FixedClock(now),
    )

    assert await poller.run_once() == 0
    assert [name for name, _ in client.events].count("complete") == 1
    assert all(name != "fail" for name, _ in client.events)


@pytest.mark.asyncio
async def test_task_deadline_cancels_handler_and_reports_parser_timeout(tmp_path: Path) -> None:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    client = FakeClient(JobClaimResponse(lease=_lease(now)))
    timeout_sleeper = TriggeredSleeper()
    handler_started = asyncio.Event()
    handler_cancelled = asyncio.Event()

    async def handler(lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter) -> TaskResult:
        del lease, sandbox, reporter
        handler_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            handler_cancelled.set()
        raise AssertionError("timed-out handler must not return")

    poller = Poller(
        client=client,
        dispatcher=Dispatcher(parse_handler=handler),
        sandboxes=SandboxManager(tmp_path / "worker"),
        worker_id="worker-1",
        capabilities=native_capabilities(max_input_bytes=1024, max_pages=20),
        clock=FixedClock(now),
        timeout_sleeper=timeout_sleeper,
        task_timeout_seconds=37,
    )

    cycle = asyncio.create_task(poller.run_once())
    await asyncio.wait_for(handler_started.wait(), timeout=1)
    await asyncio.wait_for(timeout_sleeper.started.wait(), timeout=1)
    timeout_sleeper.trigger.set()
    assert await asyncio.wait_for(cycle, timeout=1) == 0

    assert timeout_sleeper.calls == [37]
    assert handler_cancelled.is_set()
    failure = next(value for name, value in client.events if name == "fail")
    assert isinstance(failure, tuple)
    request = failure[1]
    assert isinstance(request, JobFailRequest)
    assert request.error_code == "PARSER_TIMEOUT"
    assert request.retryable is True
    assert all(name != "complete" for name, _ in client.events)
    assert not list((tmp_path / "worker").glob("job-*"))


@pytest.mark.asyncio
async def test_artifact_upload_cannot_escape_the_job_output_directory(tmp_path: Path) -> None:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    client = FakeClient(JobClaimResponse(lease=_lease(now)))
    outside = tmp_path / "outside.json"
    outside.write_text("private", encoding="utf-8")

    async def handler(lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter) -> TaskResult:
        del lease, sandbox
        await reporter.upload_artifact(
            artifact_name="outside.json",
            source=outside,
            media_type="application/json",
        )
        raise AssertionError("escaped artifact must not be uploaded")

    poller = Poller(
        client=client,
        dispatcher=Dispatcher(parse_handler=handler),
        sandboxes=SandboxManager(tmp_path / "worker"),
        worker_id="worker-1",
        capabilities=native_capabilities(max_input_bytes=1024, max_pages=20),
        clock=FixedClock(now),
    )

    assert await poller.run_once() == 0
    failure = next(value for name, value in client.events if name == "fail")
    assert isinstance(failure, tuple)
    request = failure[1]
    assert isinstance(request, JobFailRequest)
    assert request.error_code == "ARTIFACT_LOCATION_INVALID"


@pytest.mark.asyncio
async def test_run_forever_uses_injected_exponential_backoff(tmp_path: Path) -> None:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    client = FakeClient(JobClaimResponse(lease=None))
    client.claim_error = WorkerTransportError(code="WORKER_API_UNAVAILABLE")

    async def handler(lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter) -> TaskResult:
        del lease, sandbox, reporter
        raise AssertionError("no job should be dispatched")

    poller: Poller
    sleeper = CallbackSleeper(lambda: poller.request_stop())
    poller = Poller(
        client=client,
        dispatcher=Dispatcher(parse_handler=handler),
        sandboxes=SandboxManager(tmp_path / "worker"),
        worker_id="worker-1",
        capabilities=native_capabilities(max_input_bytes=1024, max_pages=20),
        clock=FixedClock(now),
        sleeper=sleeper,
        backoff_initial_seconds=0.25,
        backoff_max_seconds=4,
    )

    await asyncio.wait_for(poller.run_forever(), timeout=1)

    assert sleeper.calls == [0.25]
    assert client.events[-1][0] == "close"


@pytest.mark.asyncio
async def test_stop_cancels_active_handler_and_cleans_sandbox(tmp_path: Path) -> None:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    client = FakeClient(JobClaimResponse(lease=_lease(now)))
    handler_started = asyncio.Event()

    async def handler(lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter) -> TaskResult:
        del lease, reporter
        assert sandbox.input_path.exists()
        handler_started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled handler must not return")

    poller = Poller(
        client=client,
        dispatcher=Dispatcher(parse_handler=handler),
        sandboxes=SandboxManager(tmp_path / "worker"),
        worker_id="worker-1",
        capabilities=native_capabilities(max_input_bytes=1024, max_pages=20),
        clock=FixedClock(now),
    )

    running = asyncio.create_task(poller.run_forever())
    await asyncio.wait_for(handler_started.wait(), timeout=1)
    poller.request_stop()
    await asyncio.wait_for(running, timeout=1)

    assert not list((tmp_path / "worker").glob("job-*"))
    assert client.events[-1][0] == "close"
    assert all(name not in {"complete", "fail"} for name, _ in client.events)


@pytest.mark.asyncio
async def test_structured_worker_events_are_allowlisted_and_redacted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="study_worker.events")
    event_logger = WorkerEventLogger(logging.getLogger("test.worker.events"))
    caplog.set_level(logging.INFO, logger="test.worker.events")
    now = datetime(2026, 7, 19, tzinfo=UTC)
    sensitive = "SENSITIVE_SENTINEL_DO_NOT_LOG_123456"
    lease = _lease(now).model_copy(
        update={
            "lease_token": f"lease-token-{sensitive}",
            "input_url": f"/private/input/{sensitive}",
            "artifact_upload_url": f"/private/artifacts/{sensitive}",
        }
    )

    async def successful_handler(
        lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter
    ) -> TaskResult:
        del lease, sandbox
        await reporter.checkpoint(
            PageCheckpoint(
                page_ordinal=1,
                status="succeeded",
                output_ref=f"private/{sensitive}/page.json",
                output_sha256="c" * 64,
                output_size_bytes=20,
                source_backend="pdf-native",
                source_version="1.0",
            )
        )
        return TaskResult(
            result_manifest_ref=f"private/{sensitive}/manifest.json",
            result_sha256="b" * 64,
            result_size_bytes=100,
            page_count=1,
        )

    successful = Poller(
        client=FakeClient(JobClaimResponse(lease=lease)),
        dispatcher=Dispatcher(parse_handler=successful_handler),
        sandboxes=SandboxManager(tmp_path / "successful"),
        worker_id="worker-1",
        capabilities=native_capabilities(max_input_bytes=1024, max_pages=20),
        clock=FixedClock(now),
        event_logger=event_logger,
    )
    assert await successful.run_once() == 0
    successful.request_stop()
    await successful.run_forever()

    async def unused_handler(
        lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter
    ) -> TaskResult:
        del lease, sandbox, reporter
        raise AssertionError("no job must not dispatch")

    no_job = Poller(
        client=FakeClient(JobClaimResponse(lease=None, retry_after_ms=250)),
        dispatcher=Dispatcher(parse_handler=unused_handler),
        sandboxes=SandboxManager(tmp_path / "no-job"),
        worker_id="worker-1",
        capabilities=native_capabilities(max_input_bytes=1024, max_pages=20),
        clock=FixedClock(now),
        event_logger=event_logger,
    )
    assert await no_job.run_once() == 250

    async def failing_handler(
        lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter
    ) -> TaskResult:
        del sandbox, reporter
        raise TaskExecutionError(
            code="PARSER_TIMEOUT",
            retryable=True,
            summary=f"unsafe text {lease.lease_token}",
        )

    failed = Poller(
        client=FakeClient(JobClaimResponse(lease=lease)),
        dispatcher=Dispatcher(parse_handler=failing_handler),
        sandboxes=SandboxManager(tmp_path / "failed"),
        worker_id="worker-1",
        capabilities=native_capabilities(max_input_bytes=1024, max_pages=20),
        clock=FixedClock(now),
        event_logger=event_logger,
    )
    assert await failed.run_once() == 0

    late_client = FakeClient(JobClaimResponse(lease=lease))
    late_client.complete_error = LeaseLostError(status_code=409, code="LEASE_LOST")
    late = Poller(
        client=late_client,
        dispatcher=Dispatcher(parse_handler=successful_handler),
        sandboxes=SandboxManager(tmp_path / "late"),
        worker_id="worker-1",
        capabilities=native_capabilities(max_input_bytes=1024, max_pages=20),
        clock=FixedClock(now),
        event_logger=event_logger,
    )
    assert await late.run_once() == 0

    payloads = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "test.worker.events"
    ]
    events = {payload["event"] for payload in payloads}
    assert {
        "claim",
        "no-job",
        "start",
        "checkpoint",
        "complete",
        "fail",
        "lease-lost",
        "stop",
    } <= events
    for payload in payloads:
        event = payload.pop("event")
        assert payload.keys() <= WorkerEventLogger._ALLOWED_FIELDS[event]

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert sensitive not in rendered
    logger = event_logger
    with pytest.raises(ValueError, match="non-allowlisted"):
        logger.emit("claim", authorization=sensitive)
    with pytest.raises(ValueError, match="non-allowlisted"):
        logger.emit("complete", object_path=sensitive)
    with pytest.raises(ValueError, match="non-allowlisted"):
        logger.emit("fail", text=sensitive)
