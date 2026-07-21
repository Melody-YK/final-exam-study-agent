"""Permissive native PDF extraction using pypdf and pdfplumber."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pdfplumber
from pypdf import PdfReader

from study_contracts import BlockType
from study_worker.parsers.normalize import (
    MetadataValue,
    RawBlock,
    RawBoundingBox,
    RawDocument,
    RawPage,
)
from study_worker.parsers.protocols import ParserCapability, ParseRequest
from study_worker.parsers.router import NativeParserError

PDF_MEDIA_TYPE = "application/pdf"
BlockDraft = tuple[
    float,
    float,
    BlockType,
    str,
    RawBoundingBox,
    dict[str, MetadataValue],
]


class PDFNativeParser:
    def __init__(self, *, max_pages: int, max_pixels: int) -> None:
        if max_pages <= 0 or max_pixels <= 0:
            raise ValueError("PDF parser limits must be positive")
        self._max_pages = max_pages
        self._max_pixels = max_pixels
        self._capability = ParserCapability(
            profile="native-v1",
            source_backend="pdf-native",
            source_version="1.0",
            media_types=frozenset({PDF_MEDIA_TYPE}),
        )

    @property
    def capability(self) -> ParserCapability:
        return self._capability

    async def parse(self, request: ParseRequest) -> RawDocument:
        return await asyncio.to_thread(self.parse_sync, request)

    def parse_sync(self, request: ParseRequest) -> RawDocument:
        if request.media_type != PDF_MEDIA_TYPE:
            raise NativeParserError("UNSUPPORTED_MEDIA_TYPE")
        _verify_input(request.input_path, request.document_sha256)
        try:
            reader = PdfReader(request.input_path, strict=False)
        except Exception:
            raise NativeParserError("PDF_CONTAINER_INVALID") from None
        if reader.is_encrypted:
            raise NativeParserError("PDF_ENCRYPTED")
        total_page_count = len(reader.pages)
        if total_page_count == 0:
            raise NativeParserError("PDF_EMPTY")
        if total_page_count > self._max_pages:
            raise NativeParserError("PAGE_LIMIT_EXCEEDED")
        selected = _selected_ordinals(request.requested_pages, total_page_count)
        pages: list[RawPage] = []
        try:
            with pdfplumber.open(request.input_path) as document:
                if len(document.pages) != total_page_count:
                    raise NativeParserError("PDF_PAGE_COUNT_MISMATCH")
                for ordinal in selected:
                    pages.append(self._parse_page(document.pages[ordinal - 1], ordinal))
        except NativeParserError:
            raise
        except Exception:
            raise NativeParserError("PDF_PARSE_FAILED", retryable=True) from None
        return RawDocument(
            document_sha256=request.document_sha256,
            source_backend="pdf-native",
            source_version="1.0",
            total_page_count=total_page_count,
            pages=pages,
        )

    def _parse_page(self, page: Any, ordinal: int) -> RawPage:
        width = max(1, round(float(page.width)))
        height = max(1, round(float(page.height)))
        tables = list(page.find_tables())
        table_boxes: list[tuple[float, float, float, float]] = [
            (
                float(table.bbox[0]),
                float(table.bbox[1]),
                float(table.bbox[2]),
                float(table.bbox[3]),
            )
            for table in tables
        ]
        drafts: list[BlockDraft] = []

        words = page.extract_words(
            use_text_flow=True,
            keep_blank_chars=False,
            extra_attrs=["size"],
        )
        for line in _group_words_into_lines(words, excluded_boxes=table_boxes):
            text = " ".join(str(word.get("text", "")).strip() for word in line).strip()
            if not text:
                continue
            x0 = min(float(word["x0"]) for word in line)
            top = min(float(word["top"]) for word in line)
            x1 = max(float(word["x1"]) for word in line)
            bottom = max(float(word["bottom"]) for word in line)
            max_font_size = max(float(word.get("size") or 0) for word in line)
            block_type = BlockType.TITLE if max_font_size >= 15 else BlockType.PARAGRAPH
            drafts.append(
                (
                    top,
                    x0,
                    block_type,
                    text,
                    _bbox(x0, top, x1, bottom, width=width, height=height),
                    {"font_size": round(max_font_size, 3)},
                )
            )

        for table_index, table in enumerate(tables, start=1):
            rows = table.extract() or []
            normalized_rows = [
                [str(cell or "").replace("\r", " ").replace("\n", " ").strip() for cell in row]
                for row in rows
            ]
            text = "\n".join("\t".join(row) for row in normalized_rows).strip()
            x0, top, x1, bottom = (float(value) for value in table.bbox)
            drafts.append(
                (
                    top,
                    x0,
                    BlockType.TABLE,
                    text,
                    _bbox(x0, top, x1, bottom, width=width, height=height),
                    {
                        "table_index": table_index,
                        "row_count": len(normalized_rows),
                        "column_count": max((len(row) for row in normalized_rows), default=0),
                    },
                )
            )

        total_image_pixels = 0
        for image_index, image in enumerate(page.images, start=1):
            pixel_width, pixel_height = _image_size(image)
            total_image_pixels += pixel_width * pixel_height
            if total_image_pixels > self._max_pixels:
                raise NativeParserError("PIXEL_LIMIT_EXCEEDED")
            x0 = float(image.get("x0", 0))
            x1 = float(image.get("x1", x0))
            top = float(image.get("top", 0))
            bottom = float(image.get("bottom", top))
            drafts.append(
                (
                    top,
                    x0,
                    BlockType.IMAGE,
                    "",
                    _bbox(x0, top, x1, bottom, width=width, height=height),
                    {
                        "image_index": image_index,
                        "pixel_width": pixel_width,
                        "pixel_height": pixel_height,
                    },
                )
            )

        drafts.sort(key=lambda item: (item[0], item[1], item[2].value))
        blocks = [
            RawBlock(
                type=block_type,
                text=text,
                bbox=box,
                reading_order=reading_order,
                metadata=metadata,
            )
            for reading_order, (_, _, block_type, text, box, metadata) in enumerate(drafts)
        ]
        extracted_text = str(page.extract_text() or "").strip()
        return RawPage(
            ordinal=ordinal,
            width=width,
            height=height,
            source_kind="page",
            native_text_present=bool(extracted_text),
            blocks=blocks,
            metadata={
                "rotation": int(getattr(page, "rotation", 0) or 0),
                "table_count": len(tables),
                "image_count": len(page.images),
            },
        )


def _verify_input(path: Path, expected_sha256: str) -> None:
    try:
        with path.open("rb") as stream:
            prefix = stream.read(5)
            digest = hashlib.sha256(prefix)
            while block := stream.read(1024 * 1024):
                digest.update(block)
    except OSError:
        raise NativeParserError("PDF_INPUT_UNREADABLE", retryable=True) from None
    if prefix != b"%PDF-":
        raise NativeParserError("PDF_CONTAINER_INVALID")
    if digest.hexdigest() != expected_sha256:
        raise NativeParserError("INPUT_HASH_MISMATCH")


def _selected_ordinals(requested: tuple[int, ...], total: int) -> tuple[int, ...]:
    selected = requested or tuple(range(1, total + 1))
    if any(ordinal > total for ordinal in selected):
        raise NativeParserError("REQUESTED_PAGE_INVALID")
    return selected


def _group_words_into_lines(
    words: list[dict[str, Any]],
    *,
    excluded_boxes: Iterable[tuple[float, float, float, float]],
) -> list[list[dict[str, Any]]]:
    filtered = [
        word for word in words if not any(_word_inside_box(word, box) for box in excluded_boxes)
    ]
    filtered.sort(key=lambda word: (round(float(word["top"]), 1), float(word["x0"])))
    lines: list[list[dict[str, Any]]] = []
    for word in filtered:
        if not lines or abs(float(word["top"]) - float(lines[-1][0]["top"])) > 3:
            lines.append([word])
        else:
            lines[-1].append(word)
    return lines


def _word_inside_box(word: dict[str, Any], box: tuple[float, float, float, float]) -> bool:
    center_x = (float(word["x0"]) + float(word["x1"])) / 2
    center_y = (float(word["top"]) + float(word["bottom"])) / 2
    x0, top, x1, bottom = box
    return x0 <= center_x <= x1 and top <= center_y <= bottom


def _image_size(image: dict[str, Any]) -> tuple[int, int]:
    source_size = image.get("srcsize")
    if isinstance(source_size, tuple | list) and len(source_size) == 2:
        try:
            return max(1, int(source_size[0])), max(1, int(source_size[1]))
        except (TypeError, ValueError):
            pass
    return 1, 1


def _bbox(
    x0: float,
    top: float,
    x1: float,
    bottom: float,
    *,
    width: int,
    height: int,
) -> RawBoundingBox:
    return RawBoundingBox(
        x0=min(float(width), max(0.0, x0)),
        top=min(float(height), max(0.0, top)),
        x1=min(float(width), max(0.0, x1)),
        bottom=min(float(height), max(0.0, bottom)),
    )
