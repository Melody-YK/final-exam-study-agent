"""Permissive native PDF extraction using pypdf and pdfplumber."""

from __future__ import annotations

import asyncio
import hashlib
import os
import selectors
import shutil
import subprocess
import time
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

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
from study_worker.parsers.quality import native_text_mapping_is_corrupted
from study_worker.parsers.router import NativeParserError

PDF_MEDIA_TYPE = "application/pdf"
PDF_SOURCE_VERSION = "1.2"
_TITLE_MIN_FONT_RATIO = 1.1
_TITLE_MAX_TOP_FRACTION = 0.4
_TITLE_MAX_CHARS = 160
_PDFTOTEXT_TIMEOUT_SECONDS = 30
_PDFTOTEXT_MAX_OUTPUT_BYTES = 32 * 1024 * 1024
_PDFTOTEXT_POLL_SECONDS = 0.05
_PDFTOTEXT_READ_BYTES = 64 * 1024
_MIN_REPAIRED_TEXT_CHARS = 8
_MIN_REPAIRED_TEXT_RATIO = 0.5
_MIN_REPAIRED_VERTICAL_COVERAGE = 0.5
BlockDraft = tuple[
    float,
    float,
    BlockType,
    str,
    RawBoundingBox,
    dict[str, MetadataValue],
]
TextLineDraft = tuple[float, float, str, RawBoundingBox, float]
PopplerLineDraft = tuple[float, float, float, float, str, float]


class PDFNativeParser:
    def __init__(self, *, max_pages: int, max_pixels: int) -> None:
        if max_pages <= 0 or max_pixels <= 0:
            raise ValueError("PDF parser limits must be positive")
        self._max_pages = max_pages
        self._max_pixels = max_pixels
        self._pdftotext_executable, self._source_version = _pdftotext_capability()
        self._capability = ParserCapability(
            profile="native-v1",
            source_backend="pdf-native",
            source_version=self._source_version,
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
        pages, _ = _repair_corrupted_pages(
            pages,
            input_path=request.input_path,
            total_page_count=total_page_count,
            pdftotext_executable=self._pdftotext_executable,
            extractor_identity=self._source_version,
        )
        return RawDocument(
            document_sha256=request.document_sha256,
            source_backend="pdf-native",
            source_version=self._source_version,
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
        text_lines: list[TextLineDraft] = []

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
            text_lines.append(
                (
                    top,
                    x0,
                    text,
                    _bbox(x0, top, x1, bottom, width=width, height=height),
                    max_font_size,
                )
            )

        title_index = _title_line_index(text_lines, page_height=height)
        for index, (top, x0, text, box, max_font_size) in enumerate(text_lines):
            drafts.append(
                (
                    top,
                    x0,
                    BlockType.TITLE if index == title_index else BlockType.PARAGRAPH,
                    text,
                    box,
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


def _repair_corrupted_pages(
    pages: list[RawPage],
    *,
    input_path: Path,
    total_page_count: int,
    pdftotext_executable: str | None,
    extractor_identity: str,
) -> tuple[list[RawPage], bool]:
    corrupted = {
        page.ordinal
        for page in pages
        if native_text_mapping_is_corrupted(
            "\n".join(block.text for block in page.blocks if block.text)
        )
    }
    if not corrupted:
        return pages, False
    marked_pages = [
        page.model_copy(
            update={
                "metadata": {
                    **page.metadata,
                    "text_mapping_corrupted": True,
                }
            }
        )
        if page.ordinal in corrupted
        else page
        for page in pages
    ]
    poppler_pages = _poppler_text_pages(
        input_path,
        executable=pdftotext_executable,
        page_ordinals=tuple(sorted(corrupted)),
        total_page_count=total_page_count,
    )
    if poppler_pages is None:
        return marked_pages, False

    repaired: list[RawPage] = []
    used_poppler = False
    for page in marked_pages:
        lines = poppler_pages.get(page.ordinal, ())
        if page.ordinal in corrupted and _repair_text_is_usable(page, lines):
            repaired.append(
                _replace_page_text(
                    page,
                    lines,
                    extractor_identity=extractor_identity,
                )
            )
            used_poppler = True
        else:
            repaired.append(page)
    return repaired, used_poppler


def _poppler_text_pages(
    input_path: Path,
    *,
    executable: str | None,
    page_ordinals: tuple[int, ...],
    total_page_count: int,
) -> dict[int, tuple[PopplerLineDraft, ...]] | None:
    if not page_ordinals:
        return {}
    first_page = min(page_ordinals)
    last_page = max(page_ordinals)
    if first_page < 1 or last_page > total_page_count:
        return None
    if executable is None:
        return None
    try:
        process = subprocess.Popen(
            [
                executable,
                "-bbox-layout",
                "-enc",
                "UTF-8",
                "-f",
                str(first_page),
                "-l",
                str(last_page),
                str(input_path),
                "-",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        payload = _read_bounded_output(process)
        if payload is None:
            return None
        return _parse_poppler_pages(
            payload,
            first_page=first_page,
            last_page=last_page,
        )
    except (ElementTree.ParseError, KeyError, OSError, TypeError, ValueError):
        return None


def _read_bounded_output(process: subprocess.Popen[bytes]) -> bytes | None:
    if process.stdout is None:
        _terminate_process(process)
        return None
    output = bytearray()
    deadline = time.monotonic() + _PDFTOTEXT_TIMEOUT_SECONDS
    selector = selectors.DefaultSelector()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process(process)
                return None
            events = selector.select(timeout=min(_PDFTOTEXT_POLL_SECONDS, remaining))
            if not events:
                continue
            read_size = min(
                _PDFTOTEXT_READ_BYTES,
                _PDFTOTEXT_MAX_OUTPUT_BYTES - len(output) + 1,
            )
            chunk = os.read(process.stdout.fileno(), read_size)
            if not chunk:
                returncode = process.wait(timeout=min(1.0, max(0.01, remaining)))
                return bytes(output) if returncode == 0 and output else None
            output.extend(chunk)
            if len(output) > _PDFTOTEXT_MAX_OUTPUT_BYTES:
                _terminate_process(process)
                return None
    except (OSError, subprocess.SubprocessError, ValueError):
        _terminate_process(process)
        return None
    finally:
        selector.close()
        process.stdout.close()


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    with suppress(OSError):
        process.kill()
    with suppress(OSError, subprocess.SubprocessError):
        process.wait(timeout=1)


def _find_pdftotext() -> str | None:
    discovered = shutil.which("pdftotext")
    candidates = [
        Path("/usr/bin/pdftotext"),
        Path("/opt/homebrew/bin/pdftotext"),
        Path("/usr/local/bin/pdftotext"),
    ]
    if discovered is not None:
        candidates.insert(0, Path(discovered))
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return str(resolved)
    return None


def _pdftotext_capability() -> tuple[str | None, str]:
    executable = _find_pdftotext()
    if executable is None:
        return None, f"{PDF_SOURCE_VERSION}+native"
    try:
        digest = _sha256_file(Path(executable))
    except OSError:
        return None, f"{PDF_SOURCE_VERSION}+native"
    return executable, f"{PDF_SOURCE_VERSION}+poppler.{digest[:12]}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _parse_poppler_pages(
    payload: bytes,
    *,
    first_page: int,
    last_page: int,
) -> dict[int, tuple[PopplerLineDraft, ...]]:
    if first_page < 1 or last_page < first_page:
        raise ValueError("invalid pdftotext page range")
    root = ElementTree.fromstring(payload)
    page_elements = [element for element in root.iter() if _xml_name(element.tag) == "page"]
    if len(page_elements) != last_page - first_page + 1:
        raise ValueError("pdftotext page count mismatch")

    pages: dict[int, tuple[PopplerLineDraft, ...]] = {}
    for page_ordinal, page in enumerate(page_elements, start=first_page):
        width = max(1.0, float(page.attrib["width"]))
        height = max(1.0, float(page.attrib["height"]))
        lines: list[PopplerLineDraft] = []
        for line in (element for element in page.iter() if _xml_name(element.tag) == "line"):
            words = [
                (element.text or "").strip()
                for element in line.iter()
                if _xml_name(element.tag) == "word" and (element.text or "").strip()
            ]
            text = _join_poppler_words(words)
            if not text:
                continue
            x0 = min(width, max(0.0, float(line.attrib["xMin"])))
            top = min(height, max(0.0, float(line.attrib["yMin"])))
            x1 = min(width, max(x0, float(line.attrib["xMax"])))
            bottom = min(height, max(top, float(line.attrib["yMax"])))
            lines.append((x0, top, x1, bottom, text, bottom - top))
        lines.sort(key=lambda item: (item[1], item[0]))
        pages[page_ordinal] = tuple(lines)
    return pages


def _repair_text_is_usable(
    page: RawPage,
    lines: tuple[PopplerLineDraft, ...],
) -> bool:
    repaired_text = "\n".join(line[4] for line in lines)
    if native_text_mapping_is_corrupted(repaired_text):
        return False
    repaired_char_count = _non_whitespace_char_count(repaired_text)
    if repaired_char_count < _MIN_REPAIRED_TEXT_CHARS:
        return False
    original_text = "\n".join(block.text for block in page.blocks if block.text)
    original_char_count = _non_whitespace_char_count(original_text)
    return (
        repaired_char_count >= original_char_count * _MIN_REPAIRED_TEXT_RATIO
        and _repaired_vertical_coverage(page, lines) >= _MIN_REPAIRED_VERTICAL_COVERAGE
    )


def _non_whitespace_char_count(text: str) -> int:
    return sum(not char.isspace() for char in text)


def _repaired_vertical_coverage(
    page: RawPage,
    lines: tuple[PopplerLineDraft, ...],
) -> float:
    original_boxes = [block.bbox for block in page.blocks if block.text]
    if not original_boxes or not lines:
        return 0.0
    original_span = max(box.bottom for box in original_boxes) - min(
        box.top for box in original_boxes
    )
    if original_span <= 0:
        return 1.0
    repaired_span = max(line[3] for line in lines) - min(line[1] for line in lines)
    return repaired_span / original_span


def _replace_page_text(
    page: RawPage,
    lines: tuple[PopplerLineDraft, ...],
    *,
    extractor_identity: str,
) -> RawPage:
    text_lines: list[TextLineDraft] = [
        (
            top,
            x0,
            text,
            _bbox(x0, top, x1, bottom, width=page.width, height=page.height),
            line_height,
        )
        for x0, top, x1, bottom, text, line_height in lines
    ]
    title_index = _title_line_index(text_lines, page_height=page.height)
    blocks = [
        RawBlock(
            type=BlockType.TITLE if index == title_index else BlockType.PARAGRAPH,
            text=text,
            bbox=box,
            reading_order=index,
            metadata={
                "font_size": round(line_height, 3),
                "text_extraction": "poppler-bbox",
                "text_extractor": extractor_identity,
            },
        )
        for index, (_, _, text, box, line_height) in enumerate(text_lines)
    ]
    structure_blocks = []
    for block in page.blocks:
        if block.type is BlockType.TABLE:
            structure_blocks.append(
                block.model_copy(
                    update={
                        "text": "",
                        "metadata": {
                            **block.metadata,
                            "text_extraction": "structure-only",
                            "text_extractor": extractor_identity,
                            "text_mapping_degraded": True,
                        },
                    }
                )
            )
        elif block.type is BlockType.IMAGE:
            structure_blocks.append(block)
    blocks.extend(
        block.model_copy(update={"reading_order": len(blocks) + index})
        for index, block in enumerate(structure_blocks)
    )
    blocks.sort(key=lambda block: (block.bbox.top, block.bbox.x0, block.type.value))
    blocks = [
        block.model_copy(update={"reading_order": index}) for index, block in enumerate(blocks)
    ]
    return page.model_copy(
        update={
            "native_text_present": True,
            "blocks": blocks,
            "metadata": {
                **page.metadata,
                "text_extraction": "poppler-bbox",
                "text_extractor": extractor_identity,
                "text_mapping_corrupted": False,
                "table_text_degraded": any(
                    block.type is BlockType.TABLE for block in structure_blocks
                ),
            },
        }
    )


def _join_poppler_words(words: list[str]) -> str:
    if not words:
        return ""
    text = words[0]
    for word in words[1:]:
        separator = " " if _ascii_word_edge(text[-1]) and _ascii_word_edge(word[0]) else ""
        text += separator + word
    return text


def _ascii_word_edge(char: str) -> bool:
    return char.isascii() and char.isalnum()


def _xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


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


def _title_line_index(lines: list[TextLineDraft], *, page_height: int) -> int | None:
    """Select at most one prominent page-relative heading."""

    font_sizes = sorted(
        {round(line[4], 1) for line in lines if line[4] > 0},
        reverse=True,
    )
    if len(font_sizes) < 2:
        return None
    largest, next_largest = font_sizes[:2]
    if largest < next_largest * _TITLE_MIN_FONT_RATIO:
        return None

    max_top = page_height * _TITLE_MAX_TOP_FRACTION
    candidates = [
        (index, line)
        for index, line in enumerate(lines)
        if round(line[4], 1) == largest and line[0] <= max_top and len(line[2]) <= _TITLE_MAX_CHARS
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[1][0], item[1][1]))[0]


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
