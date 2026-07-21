"""Native parser task handler wired into the persistent worker runtime."""

from __future__ import annotations

from collections.abc import Collection
from typing import Protocol

from study_contracts import JobProgress, WorkerLease
from study_worker.artifacts import PackagedPage, finalize_parse_attempt, package_parse_page
from study_worker.capabilities import OcrCapabilityStatus
from study_worker.config import WorkerSettings
from study_worker.dispatcher import (
    NATIVE_MEDIA_TYPES,
    NATIVE_PROFILE,
    OCR_MEDIA_TYPES,
    JobReporter,
    TaskExecutionError,
    TaskHandler,
    TaskResult,
)
from study_worker.parsers.native_process import NativeSubprocessParser
from study_worker.parsers.normalize import RawDocument
from study_worker.parsers.ocr_process import OcrSubprocessParser
from study_worker.parsers.protocols import ParseRequest, ParserExecutionError
from study_worker.sandbox import Sandbox


class ParserProcess(Protocol):
    async def parse(
        self,
        request: ParseRequest,
        *,
        sandbox: Sandbox,
        timeout_seconds: float,
    ) -> RawDocument: ...


class NativeTaskHandler:
    """Translate one lease into an isolated parse attempt and immutable artifacts."""

    def __init__(
        self,
        *,
        parser: ParserProcess,
        timeout_seconds: float,
        parser_profile: str = NATIVE_PROFILE,
        media_types: Collection[str] = NATIVE_MEDIA_TYPES,
        rejection_summary: str = "native parser rejected the document",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("native handler timeout must be positive")
        if not parser_profile.strip() or not media_types or not rejection_summary.strip():
            raise ValueError("parser handler routing must not be empty")
        self._parser = parser
        self._timeout_seconds = timeout_seconds
        self._parser_profile = parser_profile
        self._media_types = frozenset(media_types)
        self._rejection_summary = rejection_summary

    async def __call__(
        self,
        lease: WorkerLease,
        sandbox: Sandbox,
        reporter: JobReporter,
    ) -> TaskResult:
        if lease.parser_profile != self._parser_profile:
            raise TaskExecutionError(
                code="UNSUPPORTED_PARSER_PROFILE",
                retryable=False,
                summary="worker received an unsupported parser profile",
            )
        if lease.media_type not in self._media_types:
            raise TaskExecutionError(
                code="UNSUPPORTED_MEDIA_TYPE",
                retryable=False,
                summary="worker received an unsupported document media type",
            )

        requested_pages = tuple(sorted(lease.requested_pages))
        reporter.update_progress(
            JobProgress(
                phase="parsing",
                completed_pages=0,
                total_pages=None,
            )
        )
        first_ordinal = requested_pages[0] if requested_pages else 1
        raw_document = await self._parse_page(
            lease,
            sandbox=sandbox,
            ordinal=first_ordinal,
        )
        self._validate_page_result(raw_document, ordinal=first_ordinal)
        requested_pages = requested_pages or tuple(range(1, raw_document.total_page_count + 1))

        reporter.update_progress(
            JobProgress(
                phase="parsing",
                completed_pages=0,
                total_pages=raw_document.total_page_count,
            )
        )

        packaged_pages = [
            await self._package_page(
                raw_document,
                sandbox=sandbox,
                reporter=reporter,
            )
        ]
        for ordinal in requested_pages[1:]:
            page_document = await self._parse_page(
                lease,
                sandbox=sandbox,
                ordinal=ordinal,
            )
            self._validate_page_result(
                page_document,
                ordinal=ordinal,
                baseline=raw_document,
            )
            packaged_pages.append(
                await self._package_page(
                    page_document,
                    sandbox=sandbox,
                    reporter=reporter,
                )
            )

        try:
            return await finalize_parse_attempt(
                raw_document,
                requested_pages=requested_pages,
                packaged_pages=packaged_pages,
                sandbox=sandbox,
                reporter=reporter,
            )
        except ValueError:
            raise TaskExecutionError(
                code="PARSER_RESULT_INVALID",
                retryable=False,
                summary="native parser returned an invalid result",
            ) from None
        except OSError:
            raise TaskExecutionError(
                code="ARTIFACT_WRITE_FAILED",
                retryable=True,
                summary="worker could not persist parser artifacts",
            ) from None

    async def _parse_page(
        self,
        lease: WorkerLease,
        *,
        sandbox: Sandbox,
        ordinal: int,
    ) -> RawDocument:
        request = ParseRequest(
            job_id=lease.job_id,
            document_id=lease.document_id,
            document_sha256=lease.document_sha256,
            media_type=lease.media_type,
            input_path=sandbox.input_path,
            output_dir=sandbox.output_dir,
            requested_pages=(ordinal,),
        )
        try:
            return await self._parser.parse(
                request,
                sandbox=sandbox,
                timeout_seconds=self._timeout_seconds,
            )
        except ParserExecutionError as exc:
            raise TaskExecutionError(
                code=exc.code,
                retryable=exc.retryable,
                summary=self._rejection_summary,
            ) from None

    @staticmethod
    def _validate_page_result(
        result: RawDocument,
        *,
        ordinal: int,
        baseline: RawDocument | None = None,
    ) -> None:
        if [page.ordinal for page in result.pages] != [ordinal]:
            raise TaskExecutionError(
                code="PARSER_RESULT_INVALID",
                retryable=False,
                summary="native parser returned invalid page coverage",
            )
        if baseline is not None and (
            result.document_sha256 != baseline.document_sha256
            or result.parser_profile != baseline.parser_profile
            or result.source_backend != baseline.source_backend
            or result.source_version != baseline.source_version
            or result.total_page_count != baseline.total_page_count
        ):
            raise TaskExecutionError(
                code="PARSER_RESULT_INCONSISTENT",
                retryable=False,
                summary="native parser returned inconsistent document metadata",
            )

    @staticmethod
    async def _package_page(
        raw_document: RawDocument,
        *,
        sandbox: Sandbox,
        reporter: JobReporter,
    ) -> PackagedPage:
        try:
            return await package_parse_page(
                raw_document.pages[0],
                raw_document=raw_document,
                sandbox=sandbox,
                reporter=reporter,
            )
        except ValueError:
            raise TaskExecutionError(
                code="PARSER_RESULT_INVALID",
                retryable=False,
                summary="native parser returned an invalid result",
            ) from None
        except OSError:
            raise TaskExecutionError(
                code="ARTIFACT_WRITE_FAILED",
                retryable=True,
                summary="worker could not persist parser artifacts",
            ) from None


def build_native_handler(settings: WorkerSettings) -> TaskHandler:
    """Build the one installed native handler from validated worker settings."""

    parser = NativeSubprocessParser(
        max_pages=settings.max_pages,
        max_pixels=settings.max_pixels,
        max_result_bytes=settings.max_input_bytes,
    )
    return NativeTaskHandler(
        parser=parser,
        timeout_seconds=settings.external_process_timeout_seconds,
    )


class OcrTaskHandler(NativeTaskHandler):
    """OCR-specific route using the same page checkpoint and artifact pipeline."""

    def __init__(self, *, parser: ParserProcess, timeout_seconds: float) -> None:
        super().__init__(
            parser=parser,
            timeout_seconds=timeout_seconds,
            parser_profile="ocr-v1",
            media_types=OCR_MEDIA_TYPES,
            rejection_summary="isolated OCR parser rejected the document",
        )


def build_ocr_handler(settings: WorkerSettings, status: OcrCapabilityStatus) -> TaskHandler:
    """Build an OCR handler only after the isolated executable probe succeeds."""

    if (
        not status.ready
        or not status.supports_ocr
        or settings.paddle_profile_bin is None
        or settings.paddle_model_cache is None
    ):
        raise ValueError("OCR handler requires a verified isolated profile")
    parser = OcrSubprocessParser(
        executable=settings.paddle_profile_bin,
        model_cache=settings.paddle_model_cache,
        max_pages=settings.max_pages,
        max_pixels=settings.max_pixels,
        max_result_bytes=min(settings.max_input_bytes, 16 * 1024 * 1024),
        complex_parser_enabled=settings.complex_parser_enabled,
        pp_structure_available=status.supports_pp_structure,
    )
    return OcrTaskHandler(
        parser=parser,
        timeout_seconds=settings.external_process_timeout_seconds,
    )
