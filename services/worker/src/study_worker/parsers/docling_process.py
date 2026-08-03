"""Restricted adapter for the optional isolated Docling runtime."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from study_worker.parsers.normalize import RawDocument
from study_worker.parsers.protocols import ParseRequest, ParserExecutionError
from study_worker.sandbox import (
    CommandPolicy,
    ProcessBoundaryError,
    ProcessTimeoutError,
    RestrictedProcessRunner,
    Sandbox,
)

DoclingBackend = Literal["standard", "vlm"]
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_PDF_MEDIA_TYPE = "application/pdf"


class DoclingSubprocessParser:
    def __init__(
        self,
        *,
        executable: Path,
        artifacts_root: Path,
        backend: DoclingBackend,
        max_pages: int,
        max_result_bytes: int,
    ) -> None:
        if max_pages <= 0 or max_result_bytes <= 0:
            raise ValueError("Docling subprocess limits must be positive")
        configured_executable = executable.expanduser()
        configured_artifacts = artifacts_root.expanduser()
        if (
            not configured_executable.is_absolute()
            or configured_executable.is_symlink()
            or not configured_executable.is_file()
            or not os.access(configured_executable, os.X_OK)
        ):
            raise ValueError("isolated Docling executable is unavailable")
        if not configured_artifacts.is_absolute() or configured_artifacts.is_symlink():
            raise ValueError("Docling artifacts root is unavailable")
        try:
            resolved_artifacts = configured_artifacts.resolve(strict=True)
        except OSError:
            raise ValueError("Docling artifacts root is unavailable") from None
        if not resolved_artifacts.is_dir():
            raise ValueError("Docling artifacts root is unavailable")
        self._executable = configured_executable.resolve(strict=True)
        self._artifacts_root = resolved_artifacts
        self._backend = backend
        self._max_pages = max_pages
        self._max_result_bytes = max_result_bytes
        self._output_path = Path(f"output/docling-{backend}.json")
        self._runner = RestrictedProcessRunner(
            (
                CommandPolicy(
                    name=f"docling-{backend}",
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
            raise ParserExecutionError("DOCLING_SANDBOX_MISMATCH")
        if request.media_type != _PDF_MEDIA_TYPE:
            raise ParserExecutionError("UNSUPPORTED_MEDIA_TYPE")
        if len(request.requested_pages) != 1:
            raise ParserExecutionError("DOCLING_PAGE_REQUEST_INVALID")
        output_path = sandbox.root / self._output_path
        output_path.unlink(missing_ok=True)
        args = self._args(request)
        try:
            result = await self._runner.run(
                f"docling-{self._backend}",
                args,
                sandbox=sandbox,
                timeout_seconds=timeout_seconds,
            )
        except ProcessTimeoutError:
            raise ParserExecutionError("DOCLING_TIMEOUT", retryable=True) from None
        except ProcessBoundaryError:
            raise ParserExecutionError("DOCLING_CHILD_FAILED", retryable=True) from None
        if result.returncode != 0:
            code, retryable = _parse_child_error(result.stdout)
            raise ParserExecutionError(code, retryable=retryable)
        try:
            if output_path.is_symlink() or not output_path.is_file():
                raise OSError
            if output_path.stat().st_size > self._max_result_bytes:
                raise ParserExecutionError("DOCLING_RESULT_TOO_LARGE")
            document = RawDocument.model_validate_json(output_path.read_bytes())
        except ParserExecutionError:
            raise
        except (OSError, ValidationError, ValueError):
            raise ParserExecutionError("DOCLING_RESULT_INVALID") from None
        expected_backend = f"docling-{self._backend}"
        if document.document_sha256 != request.document_sha256:
            raise ParserExecutionError("DOCLING_RESULT_HASH_MISMATCH")
        if document.parser_profile != "native-v1" or document.source_backend != expected_backend:
            raise ParserExecutionError("DOCLING_RESULT_BACKEND_MISMATCH")
        if [page.ordinal for page in document.pages] != list(request.requested_pages):
            raise ParserExecutionError("DOCLING_RESULT_COVERAGE_MISMATCH")
        return document

    def _args(self, request: ParseRequest) -> tuple[str, ...]:
        return (
            "run",
            "--backend",
            self._backend,
            "--input",
            "input.bin",
            "--document-sha256",
            request.document_sha256,
            "--page",
            str(request.requested_pages[0]),
            "--artifacts-root",
            str(self._artifacts_root),
            "--max-pages",
            str(self._max_pages),
            "--output",
            self._output_path.as_posix(),
        )

    def _validate_args(self, args: tuple[str, ...]) -> bool:
        if len(args) != 15 or args[:5] != (
            "run",
            "--backend",
            self._backend,
            "--input",
            "input.bin",
        ):
            return False
        if args[5] != "--document-sha256" or _SHA256_PATTERN.fullmatch(args[6]) is None:
            return False
        try:
            page = int(args[8])
        except ValueError:
            return False
        return page >= 1 and args[7:] == (
            "--page",
            str(page),
            "--artifacts-root",
            str(self._artifacts_root),
            "--max-pages",
            str(self._max_pages),
            "--output",
            self._output_path.as_posix(),
        )


def _parse_child_error(payload: bytes) -> tuple[str, bool]:
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "DOCLING_CHILD_FAILED", True
    if not isinstance(parsed, dict):
        return "DOCLING_CHILD_FAILED", True
    code = parsed.get("code")
    retryable = parsed.get("retryable")
    if not isinstance(code, str) or _ERROR_CODE_PATTERN.fullmatch(code) is None:
        return "DOCLING_CHILD_FAILED", True
    return code, retryable if isinstance(retryable, bool) else False
