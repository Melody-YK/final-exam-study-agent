from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from study_contracts import (
    PARSE_ATTEMPT_MEDIA_TYPE,
    PARSE_PAGE_MEDIA_TYPE,
    PARSER_RAW_MEDIA_TYPE,
    BlockType,
    JobArtifactReceipt,
    JobProgress,
    Page,
    PageQualityStatus,
    ParseAttemptResult,
    WorkerLease,
    canonical_json_bytes,
)
from study_worker.artifacts import RAW_PAGE_MEDIA_TYPE
from study_worker.config import WorkerMode, WorkerSettings
from study_worker.dispatcher import PageCheckpoint, TaskExecutionError
from study_worker.parsers.normalize import RawBlock, RawBoundingBox, RawDocument, RawPage
from study_worker.parsers.protocols import ParseRequest
from study_worker.parsers.router import NativeParserError
from study_worker.runtime import NativeTaskHandler, build_native_handler
from study_worker.sandbox import Sandbox, SandboxManager
from tests.fixtures.build_documents import build_documents, sha256

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@dataclass(frozen=True, slots=True)
class UploadedArtifact:
    name: str
    media_type: str
    payload: bytes
    receipt: JobArtifactReceipt


class RecordingReporter:
    def __init__(self) -> None:
        self.progress: list[JobProgress] = []
        self.uploads: list[UploadedArtifact] = []
        self.checkpoints: list[PageCheckpoint] = []

    def update_progress(self, progress: JobProgress) -> None:
        self.progress.append(progress)

    async def upload_artifact(
        self,
        *,
        artifact_name: str,
        source: Path,
        media_type: str,
    ) -> JobArtifactReceipt:
        payload = source.read_bytes()
        receipt = JobArtifactReceipt(
            artifact_ref=f"opaque/job-1/{artifact_name}",
            artifact_name=artifact_name,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            media_type=media_type,
        )
        self.uploads.append(
            UploadedArtifact(
                name=artifact_name,
                media_type=media_type,
                payload=payload,
                receipt=receipt,
            )
        )
        return receipt

    async def checkpoint(self, checkpoint: PageCheckpoint) -> None:
        self.checkpoints.append(checkpoint)


def _settings(tmp_path: Path) -> WorkerSettings:
    return WorkerSettings(
        _env_file=None,
        mode=WorkerMode.TEST,
        work_root=tmp_path / "worker",
        local_storage_root=tmp_path / "storage",
        max_pages=20,
        max_pixels=10_000_000,
        max_input_bytes=10_000_000,
        external_process_timeout_seconds=10,
    )


def _lease(
    source: Path,
    *,
    media_type: str,
    requested_pages: list[int] | None = None,
) -> WorkerLease:
    return WorkerLease(
        job_id="job-1",
        course_id="course-1",
        document_id="document-1",
        document_sha256=sha256(source),
        deletion_epoch=0,
        media_type=media_type,
        parser_profile="native-v1",
        attempt=1,
        lease_version=1,
        lease_token="lease-token-that-is-long-enough",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        input_url="local:///objects/input",
        artifact_upload_url="/worker/v1/jobs/job-1/artifacts",
        requested_pages=requested_pages or [],
    )


async def _run_handler(
    tmp_path: Path,
    source: Path,
    *,
    media_type: str,
    requested_pages: list[int] | None = None,
) -> tuple[object, RecordingReporter]:
    manager = SandboxManager(tmp_path / "sandboxes")
    reporter = RecordingReporter()
    handler = build_native_handler(_settings(tmp_path))
    with manager.create() as sandbox:
        shutil.copyfile(source, sandbox.input_path)
        result = await handler(
            _lease(source, media_type=media_type, requested_pages=requested_pages),
            sandbox,
            reporter,
        )
    return result, reporter


def _upload(reporter: RecordingReporter, name: str) -> UploadedArtifact:
    return next(upload for upload in reporter.uploads if upload.name == name)


def _assert_canonical(upload: UploadedArtifact) -> dict[str, object]:
    payload = json.loads(upload.payload)
    assert upload.payload == canonical_json_bytes(payload)
    assert upload.receipt.sha256 == hashlib.sha256(upload.payload).hexdigest()
    return payload


@pytest.mark.asyncio
async def test_native_handler_packages_full_pdf_with_failed_ocr_page(
    tmp_path: Path,
) -> None:
    fixture = build_documents(tmp_path / "fixtures").pdf

    result, reporter = await _run_handler(tmp_path, fixture, media_type="application/pdf")

    assert result.page_count == 2
    assert result.failed_pages == (2,)
    assert reporter.progress[-1].total_pages == 2
    assert [upload.name for upload in reporter.uploads] == [
        "raw-page-000001.json",
        "page-000001.json",
        "raw-page-000002.json",
        "page-000002.json",
        "parse-result.json",
    ]
    assert [upload.media_type for upload in reporter.uploads] == [
        PARSER_RAW_MEDIA_TYPE,
        PARSE_PAGE_MEDIA_TYPE,
        PARSER_RAW_MEDIA_TYPE,
        PARSE_PAGE_MEDIA_TYPE,
        PARSE_ATTEMPT_MEDIA_TYPE,
    ]
    assert RAW_PAGE_MEDIA_TYPE == PARSER_RAW_MEDIA_TYPE

    first_raw = _upload(reporter, "raw-page-000001.json")
    first_page_upload = _upload(reporter, "page-000001.json")
    second_page_upload = _upload(reporter, "page-000002.json")
    _assert_canonical(first_raw)
    first_page = Page.model_validate(_assert_canonical(first_page_upload))
    second_page = Page.model_validate(_assert_canonical(second_page_upload))
    attempt = ParseAttemptResult.model_validate(
        _assert_canonical(_upload(reporter, "parse-result.json"))
    )

    assert first_page.raw_result_ref == first_raw.receipt.artifact_ref
    assert {block.raw_result_ref for block in first_page.blocks} == {first_raw.receipt.artifact_ref}
    assert second_page.quality is not None
    assert second_page.quality.status is PageQualityStatus.FAILED
    assert second_page.quality.requires_ocr is True
    assert second_page.quality.issues[0].code == "OCR_REQUIRED"
    assert second_page.quality.issues[0].retryable is True
    assert attempt.requested_page_ordinals == [1, 2]
    assert attempt.covered_page_ordinals == [1, 2]
    assert [checkpoint.output_ref for checkpoint in reporter.checkpoints] == [
        first_page_upload.receipt.artifact_ref,
        second_page_upload.receipt.artifact_ref,
    ]
    assert [checkpoint.status for checkpoint in reporter.checkpoints] == [
        "succeeded",
        "failed",
    ]
    assert reporter.checkpoints[1].error_code == "OCR_REQUIRED"


@pytest.mark.asyncio
async def test_native_handler_packages_only_requested_pdf_page(tmp_path: Path) -> None:
    fixture = build_documents(tmp_path / "fixtures").pdf

    result, reporter = await _run_handler(
        tmp_path,
        fixture,
        media_type="application/pdf",
        requested_pages=[2],
    )

    assert result.page_count == 2
    assert result.failed_pages == (2,)
    assert reporter.progress[-1].total_pages == 2
    assert [upload.name for upload in reporter.uploads] == [
        "raw-page-000002.json",
        "page-000002.json",
        "parse-result.json",
    ]
    attempt = ParseAttemptResult.model_validate_json(_upload(reporter, "parse-result.json").payload)
    assert attempt.total_page_count == 2
    assert attempt.requested_page_ordinals == [2]
    assert attempt.covered_page_ordinals == [2]
    assert [page.ordinal for page in attempt.pages] == [2]


@pytest.mark.asyncio
async def test_native_handler_parses_pptx_without_libreoffice(tmp_path: Path) -> None:
    fixture = build_documents(tmp_path / "fixtures").pptx
    assert _settings(tmp_path).soffice_bin is None

    result, reporter = await _run_handler(tmp_path, fixture, media_type=PPTX_MEDIA_TYPE)

    assert result.page_count == 1
    assert result.failed_pages == ()
    assert any(upload.media_type == "image/png" for upload in reporter.uploads)
    attempt = ParseAttemptResult.model_validate_json(_upload(reporter, "parse-result.json").payload)
    assert attempt.source_backend == "pptx-native"
    assert attempt.pages[0].source_kind == "slide"
    assert any(asset.metadata.get("metadata_only") is False for asset in attempt.assets)
    assert any(asset.metadata.get("ole") is True for asset in attempt.assets)


@pytest.mark.asyncio
async def test_native_handler_maps_sanitized_parser_error(tmp_path: Path) -> None:
    class FailingParser:
        async def parse(
            self,
            request: ParseRequest,
            *,
            sandbox: Sandbox,
            timeout_seconds: float,
        ) -> object:
            del request, sandbox, timeout_seconds
            raise NativeParserError("PDF_CONTAINER_INVALID", retryable=False)

    handler = NativeTaskHandler(parser=FailingParser(), timeout_seconds=5)
    manager = SandboxManager(tmp_path / "sandboxes")
    source = tmp_path / "input.pdf"
    source.write_bytes(b"not a pdf")
    reporter = RecordingReporter()

    with manager.create() as sandbox:
        shutil.copyfile(source, sandbox.input_path)
        with pytest.raises(TaskExecutionError, match="PDF_CONTAINER_INVALID") as caught:
            await handler(
                _lease(source, media_type="application/pdf"),
                sandbox,
                reporter,
            )

    assert caught.value.code == "PDF_CONTAINER_INVALID"
    assert caught.value.retryable is False
    assert caught.value.summary == "native parser rejected the document"


@pytest.mark.asyncio
async def test_native_handler_checkpoints_earlier_page_before_later_child_failure(
    tmp_path: Path,
) -> None:
    reporter = RecordingReporter()

    class PageParser:
        def __init__(self) -> None:
            self.requests: list[tuple[int, ...]] = []

        async def parse(
            self,
            request: ParseRequest,
            *,
            sandbox: Sandbox,
            timeout_seconds: float,
        ) -> RawDocument:
            del sandbox, timeout_seconds
            self.requests.append(request.requested_pages)
            if request.requested_pages == (2,):
                assert [checkpoint.page_ordinal for checkpoint in reporter.checkpoints] == [1]
                raise NativeParserError("PDF_PARSE_FAILED", retryable=True)
            assert request.requested_pages == (1,)
            return RawDocument(
                document_sha256=request.document_sha256,
                source_backend="pdf-native",
                source_version="1.0",
                total_page_count=2,
                pages=[
                    RawPage(
                        ordinal=1,
                        width=100,
                        height=100,
                        source_kind="page",
                        native_text_present=True,
                        blocks=[
                            RawBlock(
                                type=BlockType.PARAGRAPH,
                                text="first page content",
                                bbox=RawBoundingBox(x0=0, top=0, x1=100, bottom=20),
                                reading_order=0,
                            )
                        ],
                    )
                ],
            )

    parser = PageParser()
    handler = NativeTaskHandler(parser=parser, timeout_seconds=5)
    manager = SandboxManager(tmp_path / "sandboxes")
    source = tmp_path / "input.pdf"
    source.write_bytes(b"self-authored parser input")

    with manager.create() as sandbox:
        shutil.copyfile(source, sandbox.input_path)
        with pytest.raises(TaskExecutionError, match="PDF_PARSE_FAILED") as caught:
            await handler(
                _lease(source, media_type="application/pdf"),
                sandbox,
                reporter,
            )

    assert caught.value.retryable is True
    assert parser.requests == [(1,), (2,)]
    assert [upload.name for upload in reporter.uploads] == [
        "raw-page-000001.json",
        "page-000001.json",
    ]
    assert [checkpoint.page_ordinal for checkpoint in reporter.checkpoints] == [1]
    assert reporter.progress[-1].total_pages == 2
