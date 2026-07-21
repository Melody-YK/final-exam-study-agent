from __future__ import annotations

import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from study_contracts import JobArtifactReceipt, JobProgress, ParseAttemptResult, WorkerLease
from study_worker.dispatcher import PageCheckpoint, TaskExecutionError
from study_worker.parsers.ocr_process import OcrSubprocessParser
from study_worker.parsers.protocols import ParseRequest, ParserExecutionError
from study_worker.runtime import OcrTaskHandler
from study_worker.sandbox import SandboxManager
from tests.fixtures.build_documents import build_documents


@dataclass(frozen=True, slots=True)
class Uploaded:
    name: str
    payload: bytes
    receipt: JobArtifactReceipt


class RecordingReporter:
    def __init__(self) -> None:
        self.progress: list[JobProgress] = []
        self.uploads: list[Uploaded] = []
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
            artifact_ref=f"opaque/ocr/{artifact_name}",
            artifact_name=artifact_name,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            media_type=media_type,
        )
        self.uploads.append(Uploaded(artifact_name, payload, receipt))
        return receipt

    async def checkpoint(self, checkpoint: PageCheckpoint) -> None:
        self.checkpoints.append(checkpoint)


def _profile_script(
    path: Path,
    *,
    total_pages: int = 1,
    fail_page_index: int | None = None,
    fail_call_number: int | None = None,
    invalid_output: bool = False,
    delay_seconds: float = 0,
    complex_general: bool = False,
) -> Path:
    script = f"""#!{sys.executable}
import argparse
import json
import time
from pathlib import Path
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("command")
parser.add_argument("--backend")
parser.add_argument("--input")
parser.add_argument("--input-sha256")
parser.add_argument("--document-sha256")
parser.add_argument("--page-index", type=int)
parser.add_argument("--cache-root")
parser.add_argument("--max-pages", type=int)
parser.add_argument("--max-pixels", type=int)
parser.add_argument("--output")
args = parser.parse_args()
time.sleep({delay_seconds!r})
invocations = Path("output/invocations.json")
history = json.loads(invocations.read_text(encoding="utf-8")) if invocations.exists() else []
with Image.open(args.input) as image:
    input_format = image.format
    input_size = list(image.size)
history.append({{
    "backend": args.backend,
    "input": args.input,
    "input_format": input_format,
    "input_size": input_size,
    "page_index": args.page_index,
}})
invocations.parent.mkdir(parents=True, exist_ok=True)
invocations.write_text(json.dumps(history), encoding="utf-8")
if args.page_index == {fail_page_index!r}:
    print(json.dumps({{"code": "OCR_OUT_OF_MEMORY", "retryable": True}}))
    raise SystemExit(2)
if len(history) == {fail_call_number!r}:
    print(json.dumps({{"code": "OCR_OUT_OF_MEMORY", "retryable": True}}))
    raise SystemExit(2)
output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
if {invalid_output!r}:
    output.write_text("not-json", encoding="utf-8")
    raise SystemExit(0)
if args.backend == "pp-structure-v3":
    page = {{
        "page_index": args.page_index,
        "image_width": 800,
        "image_height": 600,
        "parsing_res_list": [{{
            "block_label": "table",
            "block_content": "A | B",
            "block_bbox": [30, 40, 760, 500],
            "block_order": 0,
            "score": 0.95,
        }}],
    }}
else:
    polygons = (
        [
            [[20, 20], [500, 20], [500, 180], [20, 180]],
            [[100, 100], [700, 100], [700, 260], [100, 260]],
        ]
        if {complex_general!r}
        else [[[20, 20], [700, 20], [700, 100], [20, 100]]]
    )
    page = {{
        "page_index": args.page_index,
        "image_width": 800,
        "image_height": 600,
        "dt_polys": polygons,
        "rec_polys": None,
        "rec_texts": [f"self-authored page {{args.page_index + 1}}"] * len(polygons),
        "rec_scores": [0.98] * len(polygons),
    }}
payload = {{
    "schema_version": "1.0",
    "document_sha256": args.document_sha256,
    "backend": args.backend,
    "source_version": "paddleocr=3.7.0-test;paddlepaddle=3.3.1-test",
    "total_page_count": {total_pages},
    "page": page,
}}
output.write_text(json.dumps(payload), encoding="utf-8")
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(0o700)
    return path.absolute()


def _lease(
    source: Path, *, media_type: str, requested_pages: list[int] | None = None
) -> WorkerLease:
    return WorkerLease(
        job_id="ocr-job",
        course_id="course-1",
        document_id="document-1",
        document_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        deletion_epoch=0,
        media_type=media_type,
        parser_profile="ocr-v1",
        attempt=1,
        lease_version=1,
        lease_token="lease-token-that-is-long-enough",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        input_url="local:///objects/input",
        artifact_upload_url="/worker/v1/jobs/ocr-job/artifacts",
        requested_pages=requested_pages or [],
    )


def _parser(
    executable: Path,
    cache: Path,
    *,
    complex_parser_enabled: bool = False,
    max_pixels: int = 2_000_000,
) -> OcrSubprocessParser:
    return OcrSubprocessParser(
        executable=executable,
        model_cache=cache,
        max_pages=20,
        max_pixels=max_pixels,
        max_result_bytes=1_000_000,
        complex_parser_enabled=complex_parser_enabled,
        pp_structure_available=True,
    )


def _uploaded(reporter: RecordingReporter, name: str) -> Uploaded:
    return next(item for item in reporter.uploads if item.name == name)


def _fractional_page_pdf(path: Path, *, use_cropbox: bool) -> Path:
    writer = PdfWriter()
    if use_cropbox:
        page = writer.add_blank_page(width=700, height=900)
        page.cropbox.lower_left = (0.25, 0.25)
        page.cropbox.upper_right = (612.5, 792.5)
    else:
        writer.add_blank_page(width=612.25, height=792.25)
    with path.open("wb") as target:
        writer.write(target)
    return path


@pytest.mark.asyncio
async def test_ocr_handler_executes_general_image_and_persists_page_checkpoint(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scan.png"
    Image.new("RGB", (120, 80), "white").save(source)
    cache = tmp_path / "models"
    cache.mkdir()
    (cache / "model.pdparams").write_bytes(b"model-marker")
    executable = _profile_script(tmp_path / "profile")
    reporter = RecordingReporter()
    handler = OcrTaskHandler(parser=_parser(executable, cache), timeout_seconds=2)
    manager = SandboxManager(tmp_path / "sandboxes")

    with manager.create() as sandbox:
        shutil.copyfile(source, sandbox.input_path)
        result = await handler(_lease(source, media_type="image/png"), sandbox, reporter)

    assert result.page_count == 1
    assert result.failed_pages == ()
    assert [checkpoint.status for checkpoint in reporter.checkpoints] == ["succeeded"]
    assert reporter.checkpoints[0].source_backend == "paddleocr-general"
    attempt = ParseAttemptResult.model_validate_json(
        _uploaded(reporter, "parse-result.json").payload
    )
    assert attempt.parser_profile == "ocr-v1"
    assert attempt.pages[0].quality is not None
    assert attempt.pages[0].quality.text_layer == "ocr"
    assert [block.text for block in attempt.pages[0].blocks] == ["self-authored page 1"]
    assert not any(name == "paddle" or name.startswith("paddle.") for name in sys.modules)


@pytest.mark.asyncio
async def test_ocr_handler_preserves_first_checkpoint_when_later_page_is_retryable(
    tmp_path: Path,
) -> None:
    source = build_documents(tmp_path / "fixtures").pdf
    cache = tmp_path / "models"
    cache.mkdir()
    (cache / "model.pdparams").write_bytes(b"model-marker")
    executable = _profile_script(tmp_path / "profile", fail_call_number=2)
    reporter = RecordingReporter()
    handler = OcrTaskHandler(parser=_parser(executable, cache), timeout_seconds=2)
    manager = SandboxManager(tmp_path / "sandboxes")

    with manager.create() as sandbox:
        shutil.copyfile(source, sandbox.input_path)
        with pytest.raises(TaskExecutionError, match="OCR_OUT_OF_MEMORY") as caught:
            await handler(_lease(source, media_type="application/pdf"), sandbox, reporter)

    assert caught.value.retryable is True
    assert [checkpoint.page_ordinal for checkpoint in reporter.checkpoints] == [1]
    assert [item.name for item in reporter.uploads] == [
        "raw-page-000001.json",
        "page-000001.json",
    ]


@pytest.mark.asyncio
async def test_ocr_pdf_subset_renders_only_requested_page_for_child(tmp_path: Path) -> None:
    source = build_documents(tmp_path / "fixtures").pdf
    cache = tmp_path / "models"
    cache.mkdir()
    (cache / "model.pdparams").write_bytes(b"model-marker")
    executable = _profile_script(tmp_path / "profile")
    parser = _parser(executable, cache)
    manager = SandboxManager(tmp_path / "sandboxes")

    with manager.create() as sandbox:
        shutil.copyfile(source, sandbox.input_path)
        result = await parser.parse(
            ParseRequest(
                job_id="ocr-job",
                document_id="document-1",
                document_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                media_type="application/pdf",
                input_path=sandbox.input_path,
                output_dir=sandbox.output_dir,
                requested_pages=(2,),
            ),
            sandbox=sandbox,
            timeout_seconds=2,
        )
        invocations = json.loads(
            (sandbox.root / "output/invocations.json").read_text(encoding="utf-8")
        )

    assert result.total_page_count == 2
    assert [page.ordinal for page in result.pages] == [2]
    assert invocations == [
        {
            "backend": "general",
            "input": "output/ocr-input.png",
            "input_format": "PNG",
            "input_size": [1224, 1584],
            "page_index": 0,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("use_cropbox", [False, True], ids=["mediabox", "cropbox"])
async def test_ocr_pdf_fractional_page_box_uses_pdfium_bitmap_dimensions(
    tmp_path: Path,
    use_cropbox: bool,
) -> None:
    source = _fractional_page_pdf(tmp_path / "fractional.pdf", use_cropbox=use_cropbox)
    cache = tmp_path / "models"
    cache.mkdir()
    executable = _profile_script(tmp_path / "profile")
    bitmap_size = (1225, 1585)
    parser = _parser(executable, cache, max_pixels=bitmap_size[0] * bitmap_size[1])
    manager = SandboxManager(tmp_path / "sandboxes")

    with manager.create() as sandbox:
        shutil.copyfile(source, sandbox.input_path)
        result = await parser.parse(
            ParseRequest(
                job_id="ocr-job",
                document_id="document-1",
                document_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                media_type="application/pdf",
                input_path=sandbox.input_path,
                output_dir=sandbox.output_dir,
                requested_pages=(1,),
            ),
            sandbox=sandbox,
            timeout_seconds=2,
        )
        invocations = json.loads(
            (sandbox.root / "output/invocations.json").read_text(encoding="utf-8")
        )

    assert result.total_page_count == 1
    assert invocations[0]["input_size"] == list(bitmap_size)


@pytest.mark.asyncio
async def test_ocr_pdf_fractional_page_box_rejects_one_pixel_over_limit_before_child(
    tmp_path: Path,
) -> None:
    source = _fractional_page_pdf(tmp_path / "fractional.pdf", use_cropbox=False)
    cache = tmp_path / "models"
    cache.mkdir()
    executable = _profile_script(tmp_path / "profile")
    bitmap_pixels = 1225 * 1585
    parser = _parser(executable, cache, max_pixels=bitmap_pixels - 1)
    manager = SandboxManager(tmp_path / "sandboxes")

    with manager.create() as sandbox:
        shutil.copyfile(source, sandbox.input_path)
        with pytest.raises(ParserExecutionError, match="OCR_PIXEL_LIMIT_EXCEEDED"):
            await parser.parse(
                ParseRequest(
                    job_id="ocr-job",
                    document_id="document-1",
                    document_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    media_type="application/pdf",
                    input_path=sandbox.input_path,
                    output_dir=sandbox.output_dir,
                    requested_pages=(1,),
                ),
                sandbox=sandbox,
                timeout_seconds=2,
            )
        assert not (sandbox.root / "output/invocations.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("script_options", "expected_code", "retryable"),
    [
        ({"delay_seconds": 0.2}, "OCR_TIMEOUT", True),
        ({"invalid_output": True}, "OCR_RESULT_INVALID", False),
    ],
)
async def test_ocr_subprocess_maps_timeout_and_invalid_output(
    tmp_path: Path,
    script_options: dict[str, object],
    expected_code: str,
    retryable: bool,
) -> None:
    source = build_documents(tmp_path / "fixtures").pdf
    cache = tmp_path / "models"
    cache.mkdir()
    (cache / "model.pdparams").write_bytes(b"model-marker")
    executable = _profile_script(tmp_path / "profile", **script_options)
    reporter = RecordingReporter()
    timeout = 0.02 if script_options.get("delay_seconds") else 2
    handler = OcrTaskHandler(parser=_parser(executable, cache), timeout_seconds=timeout)
    manager = SandboxManager(tmp_path / "sandboxes")

    with manager.create() as sandbox:
        shutil.copyfile(source, sandbox.input_path)
        with pytest.raises(TaskExecutionError, match=expected_code) as caught:
            await handler(
                _lease(source, media_type="application/pdf", requested_pages=[1]), sandbox, reporter
            )

    assert caught.value.retryable is retryable
    assert reporter.checkpoints == []


@pytest.mark.asyncio
async def test_complex_page_uses_pp_structure_only_when_explicitly_enabled(tmp_path: Path) -> None:
    source = build_documents(tmp_path / "fixtures").pdf
    cache = tmp_path / "models"
    cache.mkdir()
    (cache / "model.pdparams").write_bytes(b"model-marker")
    executable = _profile_script(tmp_path / "profile", complex_general=True)
    parser = _parser(executable, cache, complex_parser_enabled=True)
    manager = SandboxManager(tmp_path / "sandboxes")

    with manager.create() as sandbox:
        shutil.copyfile(source, sandbox.input_path)
        result = await parser.parse(
            ParseRequest(
                job_id="ocr-job",
                document_id="document-1",
                document_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                media_type="application/pdf",
                input_path=sandbox.input_path,
                output_dir=sandbox.output_dir,
                requested_pages=(1,),
            ),
            sandbox=sandbox,
            timeout_seconds=2,
        )

    assert result.source_backend == "pp-structure-v3"
    assert result.pages[0].blocks[0].metadata["ocr_backend"] == "pp-structure-v3"
    assert result.pages[0].blocks[0].type.value == "table"
