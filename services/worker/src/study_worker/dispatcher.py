"""Static worker task routing and result contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from study_contracts import JobArtifactReceipt, JobProgress, WorkerCapabilities, WorkerLease
from study_worker.capabilities import OCR_PROFILE, OcrCapabilityStatus
from study_worker.sandbox import Sandbox

NATIVE_PROFILE = "native-v1"
NATIVE_MEDIA_TYPES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/markdown",
)
OCR_MEDIA_TYPES = (
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/tiff",
)
DISABLED_OCR_PROFILES = frozenset({"mineru-v1", "paid-ocr-v1"})


def _require_error_code(value: str) -> None:
    if not value or not value[0].isalpha() or value != value.upper():
        raise ValueError("error codes must be uppercase identifiers")
    if any(character != "_" and not character.isalnum() for character in value):
        raise ValueError("error codes must be uppercase identifiers")


@dataclass(frozen=True, slots=True)
class PageCheckpoint:
    page_ordinal: int
    status: Literal["succeeded", "failed"]
    output_ref: str
    output_sha256: str
    output_size_bytes: int
    source_backend: str
    source_version: str
    output_schema_version: Literal["1.0"] = "1.0"
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.page_ordinal < 1:
            raise ValueError("page_ordinal must be positive")
        if self.output_size_bytes < 0:
            raise ValueError("output_size_bytes must not be negative")
        if self.status == "failed" and self.error_code is None:
            raise ValueError("failed checkpoints require an error code")
        if self.status == "succeeded" and self.error_code is not None:
            raise ValueError("successful checkpoints cannot include an error code")
        if self.error_code is not None:
            _require_error_code(self.error_code)


@dataclass(frozen=True, slots=True)
class TaskResult:
    result_manifest_ref: str
    result_sha256: str
    result_size_bytes: int
    page_count: int
    failed_pages: tuple[int, ...] = field(default_factory=tuple)
    manifest_schema_version: Literal["1.0"] = "1.0"

    def __post_init__(self) -> None:
        if self.page_count < 0:
            raise ValueError("page_count must not be negative")
        if self.result_size_bytes < 0:
            raise ValueError("result_size_bytes must not be negative")
        if len(self.failed_pages) != len(set(self.failed_pages)):
            raise ValueError("failed_pages must be unique")
        if any(page < 1 or page > self.page_count for page in self.failed_pages):
            raise ValueError("failed_pages must fall within page_count")


class JobReporter(Protocol):
    """Narrow callback surface available to a task implementation."""

    def update_progress(self, progress: JobProgress) -> None: ...

    async def upload_artifact(
        self,
        *,
        artifact_name: str,
        source: Path,
        media_type: str,
    ) -> JobArtifactReceipt: ...

    async def checkpoint(self, checkpoint: PageCheckpoint) -> None: ...


TaskHandler = Callable[[WorkerLease, Sandbox, JobReporter], Awaitable[TaskResult]]


class TaskExecutionError(RuntimeError):
    """A normalized task failure safe to submit to the control plane."""

    def __init__(self, *, code: str, retryable: bool, summary: str | None = None) -> None:
        _require_error_code(code)
        if summary is not None and len(summary) > 500:
            raise ValueError("task failure summaries must not exceed 500 characters")
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.summary = summary


class UnsupportedTaskError(TaskExecutionError):
    def __init__(self) -> None:
        super().__init__(
            code="UNSUPPORTED_TASK",
            retryable=False,
            summary="worker received a task outside its static whitelist",
        )


class Dispatcher:
    """Dispatch parse leases only to explicitly installed profile handlers."""

    def __init__(
        self,
        *,
        parse_handler: TaskHandler,
        ocr_handler: TaskHandler | None = None,
    ) -> None:
        self._parse_handler = parse_handler
        self._ocr_handler = ocr_handler

    async def dispatch(
        self,
        lease: WorkerLease,
        sandbox: Sandbox,
        reporter: JobReporter,
    ) -> TaskResult:
        if lease.job_type != "parse":
            raise UnsupportedTaskError
        if lease.parser_profile == NATIVE_PROFILE:
            return await self._parse_handler(lease, sandbox, reporter)
        if lease.parser_profile == OCR_PROFILE:
            if self._ocr_handler is None:
                raise TaskExecutionError(
                    code="OCR_CAPABILITY_UNAVAILABLE",
                    retryable=True,
                    summary="isolated OCR handler is not installed",
                )
            return await self._ocr_handler(lease, sandbox, reporter)
        if lease.parser_profile in DISABLED_OCR_PROFILES:
            raise TaskExecutionError(
                code="PARSER_CAPABILITY_DISABLED",
                retryable=False,
                summary="requested parser profile is disabled",
            )
        raise UnsupportedTaskError


def native_capabilities(*, max_input_bytes: int, max_pages: int) -> WorkerCapabilities:
    """Advertise only the native P4 profile; OCR/rendering remain unavailable."""

    return WorkerCapabilities(
        parser_profiles=[NATIVE_PROFILE],
        media_types=list(NATIVE_MEDIA_TYPES),
        supports_ocr=False,
        supports_rendering=False,
        max_input_bytes=max_input_bytes,
        max_pages=max_pages,
    )


def capabilities_for_handlers(
    *,
    max_input_bytes: int,
    max_pages: int,
    ocr_status: OcrCapabilityStatus | None = None,
    ocr_handler_available: bool = False,
) -> WorkerCapabilities:
    """Advertise OCR only when both the isolated probe and a handler are available."""

    if (
        ocr_status is None
        or not ocr_status.ready
        or not ocr_status.supports_ocr
        or not ocr_handler_available
    ):
        return native_capabilities(max_input_bytes=max_input_bytes, max_pages=max_pages)
    return WorkerCapabilities(
        parser_profiles=[NATIVE_PROFILE, OCR_PROFILE],
        media_types=list(dict.fromkeys((*NATIVE_MEDIA_TYPES, *OCR_MEDIA_TYPES))),
        supports_ocr=True,
        supports_rendering=False,
        max_input_bytes=max_input_bytes,
        max_pages=max_pages,
    )
