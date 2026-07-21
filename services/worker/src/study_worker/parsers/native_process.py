"""Restricted subprocess adapter for native PDF/PPTX parsing."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from pydantic import ValidationError

from study_worker.parsers.normalize import RawDocument
from study_worker.parsers.pdf_native import PDF_MEDIA_TYPE
from study_worker.parsers.pptx_native import PPTX_MEDIA_TYPE
from study_worker.parsers.protocols import ParseRequest
from study_worker.parsers.router import NativeParserError
from study_worker.sandbox import (
    CommandPolicy,
    ProcessBoundaryError,
    ProcessTimeoutError,
    RestrictedProcessRunner,
    Sandbox,
)

_OUTPUT_PATH = Path("output/raw-document.json")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class NativeSubprocessParser:
    def __init__(
        self,
        *,
        max_pages: int,
        max_pixels: int,
        max_result_bytes: int,
    ) -> None:
        if max_pages <= 0 or max_pixels <= 0 or max_result_bytes <= 0:
            raise ValueError("native subprocess parser limits must be positive")
        self._max_pages = max_pages
        self._max_pixels = max_pixels
        self._max_result_bytes = max_result_bytes
        executable = Path(sys.executable).parent / "study-agent-native-parser"
        if executable.is_symlink() or not executable.is_file():
            raise ValueError("study-agent-native-parser console script is not installed")
        self._runner = RestrictedProcessRunner(
            (
                CommandPolicy(
                    name="native-parser",
                    executable=executable,
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
            raise NativeParserError("PARSER_SANDBOX_MISMATCH")
        args = self._args(request)
        try:
            process_result = await self._runner.run(
                "native-parser",
                args,
                sandbox=sandbox,
                timeout_seconds=timeout_seconds,
            )
        except ProcessTimeoutError:
            raise NativeParserError("PARSER_TIMEOUT", retryable=True) from None
        except ProcessBoundaryError:
            raise NativeParserError("PARSER_CHILD_FAILED", retryable=True) from None
        if process_result.returncode != 0:
            code, retryable = _parse_child_error(process_result.stdout)
            raise NativeParserError(code, retryable=retryable)
        output_path = sandbox.root / _OUTPUT_PATH
        try:
            if output_path.is_symlink() or not output_path.is_file():
                raise OSError
            if output_path.stat().st_size > self._max_result_bytes:
                raise NativeParserError("PARSER_RESULT_TOO_LARGE")
            result = RawDocument.model_validate_json(output_path.read_bytes())
        except NativeParserError:
            raise
        except (OSError, ValidationError, ValueError):
            raise NativeParserError("PARSER_RESULT_INVALID") from None
        if result.document_sha256 != request.document_sha256:
            raise NativeParserError("PARSER_RESULT_HASH_MISMATCH")
        expected_backend = "pdf-native" if request.media_type == PDF_MEDIA_TYPE else "pptx-native"
        if result.source_backend != expected_backend:
            raise NativeParserError("PARSER_RESULT_BACKEND_MISMATCH")
        expected_pages = request.requested_pages or tuple(range(1, result.total_page_count + 1))
        if tuple(page.ordinal for page in result.pages) != expected_pages:
            raise NativeParserError("PARSER_RESULT_COVERAGE_MISMATCH")
        return result

    def _args(self, request: ParseRequest) -> tuple[str, ...]:
        return (
            "--media-type",
            request.media_type,
            "--document-sha256",
            request.document_sha256,
            "--requested-pages",
            ",".join(str(page) for page in request.requested_pages),
            "--max-pages",
            str(self._max_pages),
            "--max-pixels",
            str(self._max_pixels),
            "--input",
            "input.bin",
            "--output",
            _OUTPUT_PATH.as_posix(),
        )

    def _validate_args(self, args: tuple[str, ...]) -> bool:
        if len(args) != 14:
            return False
        if args[0] != "--media-type":
            return False
        if args[1] not in {PDF_MEDIA_TYPE, PPTX_MEDIA_TYPE}:
            return False
        if args[2] != "--document-sha256" or _SHA256_PATTERN.fullmatch(args[3]) is None:
            return False
        if args[4] != "--requested-pages" or not _valid_requested_pages(args[5]):
            return False
        return args[6:] == (
            "--max-pages",
            str(self._max_pages),
            "--max-pixels",
            str(self._max_pixels),
            "--input",
            "input.bin",
            "--output",
            _OUTPUT_PATH.as_posix(),
        )


def _valid_requested_pages(value: str) -> bool:
    if not value:
        return True
    try:
        pages = tuple(int(item) for item in value.split(","))
    except ValueError:
        return False
    return all(page >= 1 for page in pages) and len(pages) == len(set(pages))


def _parse_child_error(payload: bytes) -> tuple[str, bool]:
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "PARSER_CHILD_FAILED", True
    if not isinstance(parsed, dict):
        return "PARSER_CHILD_FAILED", True
    code = parsed.get("code")
    retryable = parsed.get("retryable")
    if not isinstance(code, str) or _ERROR_CODE_PATTERN.fullmatch(code) is None:
        return "PARSER_CHILD_FAILED", True
    return code, retryable if isinstance(retryable, bool) else False
