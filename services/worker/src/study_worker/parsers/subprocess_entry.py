"""Fixed-argument child entry point for isolated native parsing."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path, PurePosixPath

from study_contracts import canonical_json_bytes
from study_worker.parsers.pdf_native import PDFNativeParser
from study_worker.parsers.pptx_native import PPTXNativeParser
from study_worker.parsers.protocols import ParseRequest
from study_worker.parsers.router import NativeParserError, NativeParserRouter


def main() -> int:
    arguments = _parser().parse_args()
    try:
        return asyncio.run(_run(arguments))
    except NativeParserError as exc:
        print(canonical_json_bytes({"code": exc.code, "retryable": exc.retryable}).decode())
        return 2
    except Exception:
        print(canonical_json_bytes({"code": "PARSER_CHILD_FAILED", "retryable": True}).decode())
        return 3


async def _run(arguments: argparse.Namespace) -> int:
    input_path = _sandbox_path(arguments.input, must_exist=True)
    output_path = _sandbox_path(arguments.output, must_exist=False)
    requested_pages = _parse_requested_pages(arguments.requested_pages)
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    router = NativeParserRouter(
        (
            PDFNativeParser(max_pages=arguments.max_pages, max_pixels=arguments.max_pixels),
            PPTXNativeParser(max_pages=arguments.max_pages, max_pixels=arguments.max_pixels),
        )
    )
    result = await router.parse(
        ParseRequest(
            job_id="isolated-parser",
            document_id="isolated-document",
            document_sha256=arguments.document_sha256,
            media_type=arguments.media_type,
            input_path=input_path,
            output_dir=output_path.parent,
            requested_pages=requested_pages,
        )
    )
    temporary = output_path.with_suffix(".tmp")
    temporary.write_bytes(canonical_json_bytes(result.model_dump(mode="json")))
    os.chmod(temporary, 0o600)
    os.replace(temporary, output_path)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--media-type", required=True)
    parser.add_argument("--document-sha256", required=True)
    parser.add_argument("--requested-pages", required=True)
    parser.add_argument("--max-pages", type=int, required=True)
    parser.add_argument("--max-pixels", type=int, required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    return parser


def _sandbox_path(value: str, *, must_exist: bool) -> Path:
    pure_path = PurePosixPath(value)
    if (
        pure_path.is_absolute()
        or "\\" in value
        or "\x00" in value
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        raise NativeParserError("PARSER_CHILD_PATH_INVALID")
    root = Path.cwd().resolve(strict=True)
    candidate = root / Path(*pure_path.parts)
    current = root
    for part in pure_path.parts:
        current /= part
        if current.is_symlink():
            raise NativeParserError("PARSER_CHILD_PATH_INVALID")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise NativeParserError("PARSER_CHILD_PATH_INVALID") from None
    if must_exist and not resolved.is_file():
        raise NativeParserError("PARSER_CHILD_INPUT_MISSING", retryable=True)
    return resolved


def _parse_requested_pages(value: str) -> tuple[int, ...]:
    if not value:
        return ()
    try:
        pages = tuple(int(item) for item in value.split(","))
    except ValueError:
        raise NativeParserError("REQUESTED_PAGE_INVALID") from None
    if any(page < 1 for page in pages) or len(pages) != len(set(pages)):
        raise NativeParserError("REQUESTED_PAGE_INVALID")
    return pages


if __name__ == "__main__":
    raise SystemExit(main())
