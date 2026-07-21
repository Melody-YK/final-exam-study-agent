"""Restricted base-Worker adapter for the isolated Paddle OCR profile."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from study_worker.parsers.complex import ComplexityRouter, OcrBackend, PageComplexity
from study_worker.parsers.normalize import RawDocument
from study_worker.parsers.paddle_general import (
    PaddleGeneralOutput,
    paddle_general_raw_page,
)
from study_worker.parsers.pp_structure import PPStructureOutput, pp_structure_raw_page
from study_worker.parsers.protocols import ParseRequest, ParserExecutionError
from study_worker.preprocess.images import ImagePreprocessError, preprocess_image
from study_worker.sandbox import (
    CommandPolicy,
    ProcessBoundaryError,
    ProcessTimeoutError,
    RestrictedProcessRunner,
    Sandbox,
)

_OUTPUT_PATH = Path("output/ocr-result.json")
_NORMALIZED_IMAGE_PATH = Path("output/ocr-input.png")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_PDF_MEDIA_TYPE = "application/pdf"
_IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/tiff"})
_PDF_RENDER_SCALE = 2.0


@dataclass(frozen=True, slots=True)
class _PreparedOcrInput:
    path: Path
    argument: str
    sha256: str
    child_page_index: int
    total_page_count: int


class _OcrProfileEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    backend: Literal["general", "pp-structure-v3"]
    source_version: str = Field(min_length=1, max_length=500)
    total_page_count: int = Field(gt=0)
    page: dict[str, object]


class OcrSubprocessParser:
    """Execute one OCR page at a time without importing Paddle into the base Worker."""

    def __init__(
        self,
        *,
        executable: Path,
        model_cache: Path,
        max_pages: int,
        max_pixels: int,
        max_result_bytes: int,
        complex_parser_enabled: bool,
        pp_structure_available: bool,
    ) -> None:
        if max_pages <= 0 or max_pixels <= 0 or max_result_bytes <= 0:
            raise ValueError("OCR subprocess limits must be positive")
        resolved_executable = executable.expanduser()
        resolved_cache = model_cache.expanduser()
        if (
            not resolved_executable.is_absolute()
            or resolved_executable.is_symlink()
            or not resolved_executable.is_file()
            or not os.access(resolved_executable, os.X_OK)
        ):
            raise ValueError("isolated OCR executable is unavailable")
        if not resolved_cache.is_absolute() or resolved_cache.is_symlink():
            raise ValueError("isolated OCR model cache is unavailable")
        try:
            cache = resolved_cache.resolve(strict=True)
        except OSError:
            raise ValueError("isolated OCR model cache is unavailable") from None
        if not cache.is_dir():
            raise ValueError("isolated OCR model cache is unavailable")
        self._executable = resolved_executable.resolve(strict=True)
        self._model_cache = cache
        self._max_pages = max_pages
        self._max_pixels = max_pixels
        self._max_result_bytes = max_result_bytes
        self._router = ComplexityRouter(
            enabled=complex_parser_enabled,
            pp_structure_available=pp_structure_available,
        )
        self._runner = RestrictedProcessRunner(
            (
                CommandPolicy(
                    name="paddle-ocr",
                    executable=self._executable,
                    validate_args=self._validate_args,
                ),
            ),
            max_output_bytes=4_096,
        )

    async def parse(
        self,
        request: ParseRequest,
        *,
        sandbox: Sandbox,
        timeout_seconds: float,
    ) -> RawDocument:
        if request.input_path != sandbox.input_path or request.output_dir != sandbox.output_dir:
            raise ParserExecutionError("OCR_SANDBOX_MISMATCH")
        if request.media_type not in {_PDF_MEDIA_TYPE, *_IMAGE_MEDIA_TYPES}:
            raise ParserExecutionError("UNSUPPORTED_MEDIA_TYPE")
        if len(request.requested_pages) != 1:
            raise ParserExecutionError("OCR_PAGE_REQUEST_INVALID")
        ordinal = request.requested_pages[0]
        prepared = self._prepare_input(request, sandbox, ordinal=ordinal)
        general = await self._run_backend(
            "general",
            request=request,
            sandbox=sandbox,
            prepared=prepared,
            timeout_seconds=timeout_seconds,
        )
        general_output = self._general_output(
            general,
            child_page_index=prepared.child_page_index,
            page_ordinal=ordinal,
        )
        decision = self._router.route(_page_complexity(general_output))
        selected = general
        if decision.backend is OcrBackend.PP_STRUCTURE_V3:
            selected = await self._run_backend(
                "pp-structure-v3",
                request=request,
                sandbox=sandbox,
                prepared=prepared,
                timeout_seconds=timeout_seconds,
            )
            if (
                selected.total_page_count != general.total_page_count
                or selected.source_version != general.source_version
            ):
                raise ParserExecutionError("OCR_RESULT_INCONSISTENT")
            raw_page = pp_structure_raw_page(
                self._structure_output(
                    selected,
                    child_page_index=prepared.child_page_index,
                    page_ordinal=ordinal,
                ),
                page_ordinal=ordinal,
            )
        else:
            raw_page = paddle_general_raw_page(general_output, page_ordinal=ordinal)
        for block in raw_page.blocks:
            block.metadata["ocr_route_reason"] = decision.reason_code
        return RawDocument(
            document_sha256=request.document_sha256,
            parser_profile="ocr-v1",
            source_backend=(
                "pp-structure-v3"
                if decision.backend is OcrBackend.PP_STRUCTURE_V3
                else "paddleocr-general"
            ),
            source_version=selected.source_version,
            total_page_count=prepared.total_page_count,
            pages=[raw_page],
        )

    def _prepare_input(
        self,
        request: ParseRequest,
        sandbox: Sandbox,
        *,
        ordinal: int,
    ) -> _PreparedOcrInput:
        if request.media_type in _IMAGE_MEDIA_TYPES:
            if ordinal != 1:
                raise ParserExecutionError("OCR_PAGE_OUT_OF_RANGE")
            destination = sandbox.root / _NORMALIZED_IMAGE_PATH
            try:
                normalized = preprocess_image(
                    request.input_path,
                    destination,
                    max_pixels=self._max_pixels,
                    max_input_bytes=self._max_result_bytes,
                )
            except ImagePreprocessError as exc:
                raise ParserExecutionError(exc.code, retryable=exc.retryable) from None
            return _PreparedOcrInput(
                path=normalized.path,
                argument=_NORMALIZED_IMAGE_PATH.as_posix(),
                sha256=normalized.sha256,
                child_page_index=0,
                total_page_count=1,
            )
        return self._render_pdf_page(request, sandbox, ordinal=ordinal)

    def _render_pdf_page(
        self,
        request: ParseRequest,
        sandbox: Sandbox,
        *,
        ordinal: int,
    ) -> _PreparedOcrInput:
        try:
            digest = _sha256(request.input_path)
        except OSError:
            raise ParserExecutionError("OCR_INPUT_UNREADABLE", retryable=True) from None
        if digest != request.document_sha256:
            raise ParserExecutionError("OCR_INPUT_HASH_MISMATCH")
        try:
            document = pdfium.PdfDocument(request.input_path)
        except Exception:
            raise ParserExecutionError("OCR_PDF_INVALID") from None
        try:
            total_page_count = len(document)
            if total_page_count <= 0 or total_page_count > self._max_pages:
                raise ParserExecutionError("OCR_PAGE_COUNT_INVALID")
            if ordinal > total_page_count:
                raise ParserExecutionError("OCR_PAGE_OUT_OF_RANGE")
            page = document[ordinal - 1]
            try:
                width_points, height_points = page.get_size()
                width = math.ceil(width_points * _PDF_RENDER_SCALE)
                height = math.ceil(height_points * _PDF_RENDER_SCALE)
                if width <= 0 or height <= 0:
                    raise ParserExecutionError("OCR_RENDER_INVALID")
                if width * height > self._max_pixels:
                    raise ParserExecutionError("OCR_PIXEL_LIMIT_EXCEEDED")
                bitmap = page.render(scale=_PDF_RENDER_SCALE)
                try:
                    if (bitmap.width, bitmap.height) != (width, height):
                        raise ParserExecutionError("OCR_RENDER_INVALID")
                    if bitmap.width * bitmap.height > self._max_pixels:
                        raise ParserExecutionError("OCR_PIXEL_LIMIT_EXCEEDED")
                    image = bitmap.to_pil().convert("RGB")
                    if image.size != (width, height):
                        raise ParserExecutionError("OCR_RENDER_INVALID")
                    if image.width * image.height > self._max_pixels:
                        raise ParserExecutionError("OCR_PIXEL_LIMIT_EXCEEDED")
                    destination = sandbox.root / _NORMALIZED_IMAGE_PATH
                    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    image.save(destination, format="PNG", optimize=False, compress_level=6)
                    os.chmod(destination, 0o600)
                finally:
                    bitmap.close()
            finally:
                page.close()
        except ParserExecutionError:
            raise
        except Exception:
            raise ParserExecutionError("OCR_RENDER_FAILED", retryable=True) from None
        finally:
            document.close()
        return _PreparedOcrInput(
            path=destination,
            argument=_NORMALIZED_IMAGE_PATH.as_posix(),
            sha256=_sha256(destination),
            child_page_index=0,
            total_page_count=total_page_count,
        )

    async def _run_backend(
        self,
        backend: Literal["general", "pp-structure-v3"],
        *,
        request: ParseRequest,
        sandbox: Sandbox,
        prepared: _PreparedOcrInput,
        timeout_seconds: float,
    ) -> _OcrProfileEnvelope:
        output_path = sandbox.root / _OUTPUT_PATH
        output_path.unlink(missing_ok=True)
        args = self._args(
            backend,
            input_argument=prepared.argument,
            input_sha256=prepared.sha256,
            document_sha256=request.document_sha256,
            page_index=prepared.child_page_index,
        )
        try:
            process_result = await self._runner.run(
                "paddle-ocr",
                args,
                sandbox=sandbox,
                timeout_seconds=timeout_seconds,
            )
        except ProcessTimeoutError:
            raise ParserExecutionError("OCR_TIMEOUT", retryable=True) from None
        except ProcessBoundaryError:
            raise ParserExecutionError("OCR_CHILD_FAILED", retryable=True) from None
        if process_result.returncode != 0:
            code, retryable = _parse_child_error(process_result.stdout)
            raise ParserExecutionError(code, retryable=retryable)
        try:
            if output_path.is_symlink() or not output_path.is_file():
                raise OSError
            if output_path.stat().st_size > self._max_result_bytes:
                raise ParserExecutionError("OCR_RESULT_TOO_LARGE")
            result = _OcrProfileEnvelope.model_validate_json(output_path.read_bytes())
        except ParserExecutionError:
            raise
        except (OSError, ValidationError, ValueError):
            raise ParserExecutionError("OCR_RESULT_INVALID") from None
        if result.document_sha256 != request.document_sha256:
            raise ParserExecutionError("OCR_RESULT_HASH_MISMATCH")
        if result.backend != backend:
            raise ParserExecutionError("OCR_RESULT_BACKEND_MISMATCH")
        if result.total_page_count != 1:
            raise ParserExecutionError("OCR_PAGE_COUNT_INVALID")
        if prepared.path.is_symlink() or not prepared.path.is_file():
            raise ParserExecutionError("OCR_INPUT_INVALID")
        return result

    @staticmethod
    def _general_output(
        result: _OcrProfileEnvelope,
        *,
        child_page_index: int,
        page_ordinal: int,
    ) -> PaddleGeneralOutput:
        try:
            output = PaddleGeneralOutput.model_validate(result.page)
        except ValidationError:
            raise ParserExecutionError("OCR_RESULT_INVALID") from None
        if output.page_index != child_page_index:
            raise ParserExecutionError("OCR_RESULT_COVERAGE_MISMATCH")
        return output.model_copy(update={"page_index": page_ordinal - 1})

    @staticmethod
    def _structure_output(
        result: _OcrProfileEnvelope,
        *,
        child_page_index: int,
        page_ordinal: int,
    ) -> PPStructureOutput:
        try:
            output = PPStructureOutput.model_validate(result.page)
        except ValidationError:
            raise ParserExecutionError("OCR_RESULT_INVALID") from None
        if output.page_index != child_page_index:
            raise ParserExecutionError("OCR_RESULT_COVERAGE_MISMATCH")
        return output.model_copy(update={"page_index": page_ordinal - 1})

    def _args(
        self,
        backend: str,
        *,
        input_argument: str,
        input_sha256: str,
        document_sha256: str,
        page_index: int,
    ) -> tuple[str, ...]:
        return (
            "run",
            "--backend",
            backend,
            "--input",
            input_argument,
            "--input-sha256",
            input_sha256,
            "--document-sha256",
            document_sha256,
            "--page-index",
            str(page_index),
            "--cache-root",
            str(self._model_cache),
            "--max-pages",
            str(self._max_pages),
            "--max-pixels",
            str(self._max_pixels),
            "--output",
            _OUTPUT_PATH.as_posix(),
        )

    def _validate_args(self, args: tuple[str, ...]) -> bool:
        if len(args) != 19 or args[0] != "run":
            return False
        if args[1] != "--backend" or args[2] not in {"general", "pp-structure-v3"}:
            return False
        if args[3] != "--input" or args[4] not in {
            "input.bin",
            _NORMALIZED_IMAGE_PATH.as_posix(),
        }:
            return False
        if args[5] != "--input-sha256" or _SHA256_PATTERN.fullmatch(args[6]) is None:
            return False
        if args[7] != "--document-sha256" or _SHA256_PATTERN.fullmatch(args[8]) is None:
            return False
        try:
            page_index = int(args[10])
        except ValueError:
            return False
        return (
            args[9] == "--page-index"
            and page_index >= 0
            and args[11:]
            == (
                "--cache-root",
                str(self._model_cache),
                "--max-pages",
                str(self._max_pages),
                "--max-pixels",
                str(self._max_pixels),
                "--output",
                _OUTPUT_PATH.as_posix(),
            )
        )


def _page_complexity(output: PaddleGeneralOutput) -> PageComplexity:
    polygons = output.rec_polys if output.rec_polys is not None else output.dt_polys
    boxes = [
        (
            min(point[0] for point in polygon),
            min(point[1] for point in polygon),
            max(point[0] for point in polygon),
            max(point[1] for point in polygon),
        )
        for polygon in polygons
    ]
    overlaps = 0
    for index, left in enumerate(boxes):
        for right in boxes[index + 1 :]:
            if min(left[2], right[2]) > max(left[0], right[0]) and min(left[3], right[3]) > max(
                left[1], right[1]
            ):
                overlaps += 1
    return PageComplexity(overlapping_regions=overlaps)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_child_error(payload: bytes) -> tuple[str, bool]:
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "OCR_CHILD_FAILED", True
    if not isinstance(parsed, dict):
        return "OCR_CHILD_FAILED", True
    code = parsed.get("code")
    retryable = parsed.get("retryable")
    if not isinstance(code, str) or _ERROR_CODE_PATTERN.fullmatch(code) is None:
        return "OCR_CHILD_FAILED", True
    return code, retryable if isinstance(retryable, bool) else False
