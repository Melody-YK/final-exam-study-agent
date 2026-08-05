"""Native parser task handler wired into the persistent worker runtime."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Protocol

from study_contracts import JobProgress, WorkerLease
from study_worker.artifacts import (
    PackagedPage,
    finalize_parse_attempt,
    package_parse_attempt,
    package_parse_page,
)
from study_worker.capabilities import (
    DOCLING_STANDARD_MARKER,
    DOCLING_VLM_MARKER,
    MINERU_PROFILE,
    MineruCapabilityStatus,
    OcrCapabilityStatus,
)
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
from study_worker.parsers.docling_process import DoclingSubprocessParser
from study_worker.parsers.hybrid import HybridPdfParser
from study_worker.parsers.mineru_http import MineruHttpParser
from study_worker.parsers.native_process import NativeSubprocessParser
from study_worker.parsers.normalize import RawDocument
from study_worker.parsers.ocr_process import OcrSubprocessParser
from study_worker.parsers.protocols import ParseRequest, ParserExecutionError
from study_worker.rendering.pdfium import PdfRenderError
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
        max_pages: int = 2_000,
        max_pixels: int = 100_000_000,
    ) -> None:
        if timeout_seconds <= 0 or max_pages <= 0 or max_pixels <= 0:
            raise ValueError("native handler limits must be positive")
        if not parser_profile.strip() or not media_types or not rejection_summary.strip():
            raise ValueError("parser handler routing must not be empty")
        self._parser = parser
        self._timeout_seconds = timeout_seconds
        self._parser_profile = parser_profile
        self._media_types = frozenset(media_types)
        self._rejection_summary = rejection_summary
        self._max_pages = max_pages
        self._max_pixels = max_pixels

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
                media_type=lease.media_type,
                max_pages=self._max_pages,
                max_pixels=self._max_pixels,
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
                    media_type=lease.media_type,
                    max_pages=self._max_pages,
                    max_pixels=self._max_pixels,
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
        except PdfRenderError as exc:
            raise TaskExecutionError(
                code=exc.code,
                retryable=exc.retryable,
                summary="worker could not render a PDF evidence page",
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
        media_type: str,
        max_pages: int,
        max_pixels: int,
    ) -> PackagedPage:
        try:
            return await package_parse_page(
                raw_document.pages[0],
                raw_document=raw_document,
                sandbox=sandbox,
                reporter=reporter,
                media_type=media_type,
                max_pages=max_pages,
                max_pixels=max_pixels,
            )
        except ValueError:
            raise TaskExecutionError(
                code="PARSER_RESULT_INVALID",
                retryable=False,
                summary="native parser returned an invalid result",
            ) from None
        except PdfRenderError as exc:
            raise TaskExecutionError(
                code=exc.code,
                retryable=exc.retryable,
                summary="worker could not render a PDF evidence page",
            ) from None
        except OSError:
            raise TaskExecutionError(
                code="ARTIFACT_WRITE_FAILED",
                retryable=True,
                summary="worker could not persist parser artifacts",
            ) from None


def build_native_handler(settings: WorkerSettings) -> TaskHandler:
    """Build native parsing with optional, pre-warmed Docling page fallbacks."""

    native_parser = NativeSubprocessParser(
        max_pages=settings.max_pages,
        max_pixels=settings.max_pixels,
        max_result_bytes=settings.max_input_bytes,
    )
    standard_parser: ParserProcess | None = None
    vlm_parser: ParserProcess | None = None
    if (
        settings.docling_profile_bin is not None
        and settings.docling_artifacts_root is not None
        and _docling_marker_ready(settings.docling_artifacts_root, DOCLING_STANDARD_MARKER)
    ):
        standard_parser = DoclingSubprocessParser(
            executable=settings.docling_profile_bin,
            artifacts_root=settings.docling_artifacts_root,
            backend="standard",
            max_pages=settings.max_pages,
            max_result_bytes=settings.max_input_bytes,
        )
        if _docling_marker_ready(settings.docling_artifacts_root, DOCLING_VLM_MARKER):
            vlm_parser = DoclingSubprocessParser(
                executable=settings.docling_profile_bin,
                artifacts_root=settings.docling_artifacts_root,
                backend="vlm",
                max_pages=settings.max_pages,
                max_result_bytes=settings.max_input_bytes,
            )
    parser = HybridPdfParser(
        native=native_parser,
        docling_standard=standard_parser,
        docling_vlm=vlm_parser,
    )
    return NativeTaskHandler(
        parser=parser,
        timeout_seconds=settings.external_process_timeout_seconds,
        max_pages=settings.max_pages,
        max_pixels=settings.max_pixels,
    )


def _docling_marker_ready(root: Path, marker_name: str) -> bool:
    marker = root / marker_name
    try:
        return not marker.is_symlink() and marker.is_file() and marker.stat().st_size > 0
    except OSError:
        return False


class OcrTaskHandler(NativeTaskHandler):
    """OCR-specific route using the same page checkpoint and artifact pipeline."""

    def __init__(
        self,
        *,
        parser: ParserProcess,
        timeout_seconds: float,
        max_pages: int = 2_000,
        max_pixels: int = 100_000_000,
    ) -> None:
        super().__init__(
            parser=parser,
            timeout_seconds=timeout_seconds,
            parser_profile="ocr-v1",
            media_types=OCR_MEDIA_TYPES,
            rejection_summary="isolated OCR parser rejected the document",
            max_pages=max_pages,
            max_pixels=max_pixels,
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
        max_pages=settings.max_pages,
        max_pixels=settings.max_pixels,
    )


class MineruTaskHandler:
    """Run one remote MinerU request for the lease and checkpoint every returned page."""

    def __init__(
        self,
        *,
        parser: ParserProcess,
        timeout_seconds: float,
        max_pages: int = 2_000,
        max_pixels: int = 100_000_000,
    ) -> None:
        if timeout_seconds <= 0 or max_pages <= 0 or max_pixels <= 0:
            raise ValueError("MinerU handler limits must be positive")
        self._parser = parser
        self._timeout_seconds = timeout_seconds
        self._max_pages = max_pages
        self._max_pixels = max_pixels

    async def __call__(
        self,
        lease: WorkerLease,
        sandbox: Sandbox,
        reporter: JobReporter,
    ) -> TaskResult:
        if lease.parser_profile != MINERU_PROFILE:
            raise TaskExecutionError(
                code="UNSUPPORTED_PARSER_PROFILE",
                retryable=False,
                summary="worker received an unsupported parser profile",
            )
        if lease.media_type != "application/pdf":
            raise TaskExecutionError(
                code="UNSUPPORTED_MEDIA_TYPE",
                retryable=False,
                summary="MinerU only accepts PDF documents",
            )
        reporter.update_progress(JobProgress(phase="parsing", completed_pages=0, total_pages=None))
        request = ParseRequest(
            job_id=lease.job_id,
            document_id=lease.document_id,
            document_sha256=lease.document_sha256,
            media_type=lease.media_type,
            input_path=sandbox.input_path,
            output_dir=sandbox.output_dir,
            requested_pages=tuple(sorted(lease.requested_pages)),
        )
        try:
            result = await self._parser.parse(
                request,
                sandbox=sandbox,
                timeout_seconds=self._timeout_seconds,
            )
            reporter.update_progress(
                JobProgress(phase="parsing", completed_pages=0, total_pages=result.total_page_count)
            )
            return await package_parse_attempt(
                result,
                lease=lease,
                sandbox=sandbox,
                reporter=reporter,
                media_type=lease.media_type,
                max_pages=self._max_pages,
                max_pixels=self._max_pixels,
            )
        except ParserExecutionError as exc:
            raise TaskExecutionError(
                code=exc.code,
                retryable=exc.retryable,
                summary="self-hosted MinerU parser rejected the document",
            ) from None
        except ValueError:
            raise TaskExecutionError(
                code="PARSER_RESULT_INVALID",
                retryable=False,
                summary="MinerU returned an invalid result",
            ) from None
        except PdfRenderError as exc:
            raise TaskExecutionError(
                code=exc.code,
                retryable=exc.retryable,
                summary="worker could not render a PDF evidence page",
            ) from None
        except OSError:
            raise TaskExecutionError(
                code="ARTIFACT_WRITE_FAILED",
                retryable=True,
                summary="worker could not persist MinerU artifacts",
            ) from None


def build_mineru_handler(
    settings: WorkerSettings,
    status: MineruCapabilityStatus,
) -> TaskHandler:
    if not status.ready or status.source_version is None or settings.mineru_base_url is None:
        raise ValueError("MinerU handler requires a healthy self-hosted API")
    parser = MineruHttpParser(
        base_url=str(settings.mineru_base_url),
        token=settings.mineru_token,
        source_version=status.source_version,
        backend=settings.mineru_backend,
        max_pages=settings.max_pages,
        max_result_bytes=settings.max_input_bytes,
    )
    return MineruTaskHandler(
        parser=parser,
        timeout_seconds=settings.external_process_timeout_seconds,
        max_pages=settings.max_pages,
        max_pixels=settings.max_pixels,
    )
