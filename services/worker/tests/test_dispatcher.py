from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from study_contracts import JobArtifactReceipt, JobProgress, WorkerLease
from study_worker.capabilities import MineruCapabilityStatus, OcrCapabilityStatus
from study_worker.dispatcher import (
    Dispatcher,
    JobReporter,
    TaskExecutionError,
    TaskResult,
    UnsupportedTaskError,
    capabilities_for_handlers,
    native_capabilities,
)
from study_worker.sandbox import Sandbox


def _lease() -> WorkerLease:
    return WorkerLease(
        job_id="job-1",
        course_id="course-1",
        document_id="document-1",
        document_sha256="a" * 64,
        deletion_epoch=0,
        media_type="application/pdf",
        parser_profile="native-v1",
        attempt=1,
        lease_version=1,
        lease_token="lease-token-that-is-long-enough",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        input_url="local:///objects/input.pdf",
        artifact_upload_url="/worker/v1/jobs/job-1/artifacts",
    )


class ReporterStub:
    def update_progress(self, progress: JobProgress) -> None:
        del progress

    async def checkpoint(self, checkpoint: object) -> None:
        del checkpoint

    async def upload_artifact(
        self, *, artifact_name: str, source: Path, media_type: str
    ) -> JobArtifactReceipt:
        del artifact_name, source, media_type
        raise AssertionError("not used")


@pytest.mark.asyncio
async def test_dispatcher_executes_only_the_static_parse_route(tmp_path: Path) -> None:
    calls = 0
    expected = TaskResult(
        result_manifest_ref="objects/manifest.json",
        result_sha256="b" * 64,
        result_size_bytes=100,
        page_count=1,
    )

    async def parse_handler(
        lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter
    ) -> TaskResult:
        nonlocal calls
        del lease, sandbox, reporter
        calls += 1
        return expected

    dispatcher = Dispatcher(parse_handler=parse_handler)
    sandbox = Sandbox(
        root=tmp_path,
        input_path=tmp_path / "input.bin",
        output_dir=tmp_path / "output",
    )

    assert await dispatcher.dispatch(_lease(), sandbox, ReporterStub()) == expected
    assert calls == 1

    unknown_lease = _lease().model_copy(update={"job_type": "shell"})
    with pytest.raises(UnsupportedTaskError, match="UNSUPPORTED_TASK"):
        await dispatcher.dispatch(unknown_lease, sandbox, ReporterStub())
    assert calls == 1


def test_native_capabilities_do_not_advertise_ocr_or_rendering() -> None:
    capabilities = native_capabilities(max_input_bytes=1234, max_pages=42)

    assert capabilities.parser_profiles == ["native-v1"]
    assert capabilities.media_types == [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/markdown",
    ]
    assert capabilities.supports_ocr is False
    assert capabilities.supports_rendering is False


@pytest.mark.asyncio
async def test_dispatcher_routes_optional_profiles_only_to_explicit_handlers(
    tmp_path: Path,
) -> None:
    expected = TaskResult(
        result_manifest_ref="objects/ocr-manifest.json",
        result_sha256="c" * 64,
        result_size_bytes=80,
        page_count=2,
    )
    calls: list[str] = []

    async def native_handler(
        lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter
    ) -> TaskResult:
        del sandbox, reporter
        calls.append(f"native:{lease.parser_profile}")
        return expected

    async def ocr_handler(
        lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter
    ) -> TaskResult:
        del sandbox, reporter
        calls.append(f"ocr:{lease.parser_profile}")
        return expected

    async def mineru_handler(
        lease: WorkerLease, sandbox: Sandbox, reporter: JobReporter
    ) -> TaskResult:
        del sandbox, reporter
        calls.append(f"mineru:{lease.parser_profile}")
        return expected

    sandbox = Sandbox(tmp_path, tmp_path / "input", tmp_path / "output")
    ocr_lease = _lease().model_copy(update={"parser_profile": "ocr-v1", "requested_pages": [2]})
    without_ocr = Dispatcher(parse_handler=native_handler)
    with pytest.raises(TaskExecutionError, match="OCR_CAPABILITY_UNAVAILABLE") as unavailable:
        await without_ocr.dispatch(ocr_lease, sandbox, ReporterStub())
    assert unavailable.value.retryable is True

    dispatcher = Dispatcher(parse_handler=native_handler, ocr_handler=ocr_handler)
    assert await dispatcher.dispatch(ocr_lease, sandbox, ReporterStub()) == expected
    assert calls == ["ocr:ocr-v1"]

    mineru_lease = ocr_lease.model_copy(update={"parser_profile": "mineru-v1"})
    with pytest.raises(TaskExecutionError, match="MINERU_CAPABILITY_UNAVAILABLE") as unavailable:
        await dispatcher.dispatch(mineru_lease, sandbox, ReporterStub())
    assert unavailable.value.retryable is True

    dispatcher = Dispatcher(
        parse_handler=native_handler,
        ocr_handler=ocr_handler,
        mineru_handler=mineru_handler,
    )
    assert await dispatcher.dispatch(mineru_lease, sandbox, ReporterStub()) == expected
    assert calls == ["ocr:ocr-v1", "mineru:mineru-v1"]


def test_ocr_claim_capability_requires_both_verified_probe_and_handler() -> None:
    ready = OcrCapabilityStatus(
        ready=True,
        reason_code=None,
        supports_ocr=True,
        supports_pp_structure=True,
        cached_file_count=1,
    )

    no_handler = capabilities_for_handlers(
        max_input_bytes=1234,
        max_pages=42,
        ocr_status=ready,
        ocr_handler_available=False,
    )
    with_handler = capabilities_for_handlers(
        max_input_bytes=1234,
        max_pages=42,
        ocr_status=ready,
        ocr_handler_available=True,
    )

    assert no_handler.parser_profiles == ["native-v1"]
    assert no_handler.supports_ocr is False
    assert with_handler.parser_profiles == ["native-v1", "ocr-v1"]
    assert with_handler.supports_ocr is True
    assert "image/png" in with_handler.media_types


def test_mineru_claim_capability_requires_health_probe_and_handler() -> None:
    ready = MineruCapabilityStatus(
        ready=True,
        reason_code=None,
        source_version="3.4.4",
        protocol_version=2,
    )

    no_handler = capabilities_for_handlers(
        max_input_bytes=1234,
        max_pages=42,
        mineru_status=ready,
        mineru_handler_available=False,
    )
    with_handler = capabilities_for_handlers(
        max_input_bytes=1234,
        max_pages=42,
        mineru_status=ready,
        mineru_handler_available=True,
    )

    assert no_handler.parser_profiles == ["native-v1"]
    assert with_handler.parser_profiles == ["native-v1", "mineru-v1"]
    assert with_handler.supports_ocr is False
