"""Adapter for a separately self-hosted MinerU FastAPI service."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
import pypdfium2 as pdfium  # type: ignore[import-untyped]
from pydantic import SecretStr

from study_contracts import BlockType
from study_worker.parsers.normalize import RawBlock, RawBoundingBox, RawDocument, RawPage
from study_worker.parsers.protocols import ParseRequest, ParserExecutionError
from study_worker.sandbox import Sandbox

_PDF_MEDIA_TYPE = "application/pdf"


class MineruHttpParser:
    """Submit a PDF once and map MinerU content-list output into raw pages."""

    def __init__(
        self,
        *,
        base_url: str,
        token: SecretStr | None,
        source_version: str,
        backend: str,
        max_pages: int,
        max_result_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.strip() or not source_version.strip() or backend != "pipeline":
            raise ValueError("MinerU parser configuration is invalid")
        if max_pages <= 0 or max_result_bytes <= 0:
            raise ValueError("MinerU parser limits must be positive")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._source_version = source_version
        self._backend = backend
        self._max_pages = max_pages
        self._max_result_bytes = max_result_bytes
        self._transport = transport

    async def parse(
        self,
        request: ParseRequest,
        *,
        sandbox: Sandbox,
        timeout_seconds: float,
    ) -> RawDocument:
        if request.input_path != sandbox.input_path or request.output_dir != sandbox.output_dir:
            raise ParserExecutionError("MINERU_SANDBOX_MISMATCH")
        if request.media_type != _PDF_MEDIA_TYPE:
            raise ParserExecutionError("UNSUPPORTED_MEDIA_TYPE")
        dimensions = _pdf_dimensions(
            request.input_path,
            expected_sha256=request.document_sha256,
            max_pages=self._max_pages,
        )
        total_pages = len(dimensions)
        selected = request.requested_pages or tuple(range(1, total_pages + 1))
        if any(page > total_pages for page in selected):
            raise ParserExecutionError("MINERU_PAGE_OUT_OF_RANGE")
        payload = await self._file_parse(
            request.input_path,
            selected_pages=selected,
            timeout_seconds=timeout_seconds,
        )
        content_items = _content_list(payload)
        first_selected = min(selected)
        parsed_page_count = max(selected) - first_selected + 1
        selected_set = set(selected)
        by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for item in content_items:
            page_index = item.get("page_idx")
            if (
                not isinstance(page_index, int)
                or isinstance(page_index, bool)
                or not 0 <= page_index < parsed_page_count
            ):
                raise ParserExecutionError("MINERU_RESULT_INVALID")
            # MinerU rewrites start_page_id..end_page_id into a new PDF and
            # numbers content-list pages from zero within that rewritten range.
            ordinal = first_selected + page_index
            if ordinal in selected_set:
                by_page[ordinal].append(item)
        pages = [
            _raw_page(
                ordinal,
                width=dimensions[ordinal - 1][0],
                height=dimensions[ordinal - 1][1],
                items=by_page.get(ordinal, []),
            )
            for ordinal in selected
        ]
        return RawDocument(
            document_sha256=request.document_sha256,
            parser_profile="mineru-v1",
            source_backend="mineru-pipeline",
            source_version=self._source_version,
            total_page_count=total_pages,
            pages=pages,
        )

    async def _file_parse(
        self,
        input_path: Path,
        *,
        selected_pages: tuple[int, ...],
        timeout_seconds: float,
    ) -> object:
        headers = {"Accept": "application/json"}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token.get_secret_value()}"
        data = {
            "backend": self._backend,
            "parse_method": "auto",
            "return_content_list": "true",
            "return_md": "false",
            "return_middle_json": "false",
            "return_model_output": "false",
            "return_images": "false",
            "start_page_id": str(min(selected_pages) - 1),
            "end_page_id": str(max(selected_pages) - 1),
        }
        stream = None
        try:
            stream = input_path.open("rb")
            files = {"files": (input_path.name or "document.pdf", stream, _PDF_MEDIA_TYPE)}
            client = httpx.AsyncClient(
                timeout=timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
                trust_env=False,
            )
            async with (
                client,
                client.stream(
                    "POST",
                    f"{self._base_url}/file_parse",
                    headers=headers,
                    data=data,
                    files=files,
                ) as response,
            ):
                if response.status_code != 200:
                    raise ParserExecutionError(
                        "MINERU_API_ERROR",
                        retryable=response.status_code >= 500,
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_result_bytes:
                        raise ParserExecutionError("MINERU_RESULT_TOO_LARGE")
        except ParserExecutionError:
            raise
        except httpx.TimeoutException:
            raise ParserExecutionError("MINERU_TIMEOUT", retryable=True) from None
        except (httpx.HTTPError, OSError):
            raise ParserExecutionError("MINERU_API_UNREACHABLE", retryable=True) from None
        finally:
            if stream is not None:
                stream.close()
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ParserExecutionError("MINERU_RESULT_INVALID") from None


def _pdf_dimensions(
    path: Path,
    *,
    expected_sha256: str,
    max_pages: int,
) -> list[tuple[int, int]]:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            prefix = stream.read(5)
            digest.update(prefix)
            while block := stream.read(1024 * 1024):
                digest.update(block)
    except OSError:
        raise ParserExecutionError("MINERU_INPUT_UNREADABLE", retryable=True) from None
    if prefix != b"%PDF-":
        raise ParserExecutionError("MINERU_PDF_INVALID")
    if digest.hexdigest() != expected_sha256:
        raise ParserExecutionError("MINERU_INPUT_HASH_MISMATCH")
    try:
        document = pdfium.PdfDocument(path)
    except Exception:
        raise ParserExecutionError("MINERU_PDF_INVALID") from None
    try:
        page_count = len(document)
        if page_count < 1 or page_count > max_pages:
            raise ParserExecutionError("MINERU_PAGE_COUNT_INVALID")
        dimensions: list[tuple[int, int]] = []
        for index in range(page_count):
            page = document[index]
            try:
                width, height = page.get_size()
            finally:
                page.close()
            rounded = (max(1, math.ceil(width)), max(1, math.ceil(height)))
            dimensions.append(rounded)
        return dimensions
    except ParserExecutionError:
        raise
    except Exception:
        raise ParserExecutionError("MINERU_PDF_INVALID") from None
    finally:
        document.close()


def _content_list(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ParserExecutionError("MINERU_RESULT_INVALID")
    results = payload.get("results")
    if not isinstance(results, dict) or not results:
        raise ParserExecutionError("MINERU_RESULT_INVALID")
    candidates = [value for value in results.values() if isinstance(value, dict)]
    if len(candidates) != 1:
        raise ParserExecutionError("MINERU_RESULT_INVALID")
    raw_content = candidates[0].get("content_list")
    if isinstance(raw_content, str):
        try:
            raw_content = json.loads(raw_content)
        except json.JSONDecodeError:
            raise ParserExecutionError("MINERU_RESULT_INVALID") from None
    if not isinstance(raw_content, list) or any(not isinstance(item, dict) for item in raw_content):
        raise ParserExecutionError("MINERU_RESULT_INVALID")
    return raw_content


def _raw_page(
    ordinal: int,
    *,
    width: int,
    height: int,
    items: list[dict[str, Any]],
) -> RawPage:
    blocks: list[RawBlock] = []
    section_path: list[str] = []
    for item in items:
        block_type = _block_type(item)
        text = _item_text(item, block_type)
        if block_type is BlockType.TITLE and text:
            section_path = [text]
        blocks.append(
            RawBlock(
                type=block_type,
                text=text,
                bbox=_bbox(item.get("bbox"), width=width, height=height),
                reading_order=len(blocks),
                section_path=list(section_path),
                metadata={
                    "mineru_type": str(item.get("type", "unknown"))[:100],
                    "mineru_page_index": ordinal - 1,
                    "parser_route": "mineru",
                    "parser_route_reason": "USER_SELECTED_MINERU",
                },
            )
        )
    return RawPage(
        ordinal=ordinal,
        width=width,
        height=height,
        source_kind="page",
        native_text_present=any(block.text for block in blocks),
        blocks=blocks,
        metadata={
            "parser_route": "mineru",
            "parser_route_reason": "USER_SELECTED_MINERU",
            "mineru_item_count": len(blocks),
        },
    )


def _block_type(item: dict[str, Any]) -> BlockType:
    item_type = str(item.get("type", "text")).lower()
    if item_type in {"title", "section_header"} or (
        item_type == "text" and item.get("text_level") is not None
    ):
        return BlockType.TITLE
    if "table" in item_type:
        return BlockType.TABLE
    if item_type in {"interline_equation", "inline_equation", "equation", "formula"}:
        return BlockType.FORMULA
    if item_type in {"image", "chart"}:
        return BlockType.IMAGE
    if "code" in item_type:
        return BlockType.CODE
    return BlockType.PARAGRAPH


def _item_text(item: dict[str, Any], block_type: BlockType) -> str:
    keys = {
        BlockType.TABLE: ("table_body", "text"),
        BlockType.FORMULA: ("text", "latex", "formula"),
        BlockType.IMAGE: ("image_caption", "image_body", "text"),
    }.get(block_type, ("text",))
    values: list[str] = []
    for key in keys:
        values.extend(_strings(item.get(key)))
    return "\n".join(dict.fromkeys(value.strip() for value in values if value.strip()))


def _strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _bbox(value: object, *, width: int, height: int) -> RawBoundingBox:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(not isinstance(point, int | float) or isinstance(point, bool) for point in value)
    ):
        return RawBoundingBox(x0=0, top=0, x1=width, bottom=height)
    x0, top, x1, bottom = (min(1000.0, max(0.0, float(point))) for point in value)
    left_px = min(float(width), max(0.0, x0 / 1000 * width))
    right_px = min(float(width), max(left_px, x1 / 1000 * width))
    top_px = min(float(height), max(0.0, top / 1000 * height))
    bottom_px = min(float(height), max(top_px, bottom / 1000 * height))
    return RawBoundingBox(x0=left_px, top=top_px, x1=right_px, bottom=bottom_px)
