"""Deterministic CommonMark extraction into stable section-based source units."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

from study_contracts import BlockType
from study_worker.parsers.normalize import RawBlock, RawBoundingBox, RawDocument, RawPage
from study_worker.parsers.protocols import ParserCapability, ParseRequest
from study_worker.parsers.router import NativeParserError

MARKDOWN_MEDIA_TYPE = "text/markdown"
_VIRTUAL_WIDTH = 1_000
_BLOCK_HEIGHT = 100


@dataclass(frozen=True, slots=True)
class _BlockDraft:
    type: BlockType
    text: str
    section_path: tuple[str, ...]
    line_start: int
    line_end: int
    heading_level: int | None = None


@dataclass(frozen=True, slots=True)
class _SectionDraft:
    path: tuple[str, ...]
    blocks: tuple[_BlockDraft, ...]


class MarkdownNativeParser:
    def __init__(self, *, max_sections: int) -> None:
        if max_sections <= 0:
            raise ValueError("Markdown parser section limit must be positive")
        self._max_sections = max_sections
        self._parser = MarkdownIt("commonmark", {"html": False})
        self._capability = ParserCapability(
            profile="native-v1",
            source_backend="markdown-native",
            source_version="1.0",
            media_types=frozenset({MARKDOWN_MEDIA_TYPE}),
        )

    @property
    def capability(self) -> ParserCapability:
        return self._capability

    async def parse(self, request: ParseRequest) -> RawDocument:
        return await asyncio.to_thread(self.parse_sync, request)

    def parse_sync(self, request: ParseRequest) -> RawDocument:
        if request.media_type != MARKDOWN_MEDIA_TYPE:
            raise NativeParserError("UNSUPPORTED_MEDIA_TYPE")
        source = _read_source(request.input_path, request.document_sha256)
        sections = _sections(self._parser.parse(source))
        if not sections:
            raise NativeParserError("MARKDOWN_EMPTY")
        if len(sections) > self._max_sections:
            raise NativeParserError("PAGE_LIMIT_EXCEEDED")
        selected = _selected_ordinals(request.requested_pages, len(sections))
        return RawDocument(
            document_sha256=request.document_sha256,
            source_backend="markdown-native",
            source_version="1.0",
            total_page_count=len(sections),
            pages=[_page(sections[ordinal - 1], ordinal) for ordinal in selected],
        )


def _read_source(path: Path, expected_sha256: str) -> str:
    try:
        payload = path.read_bytes()
    except OSError:
        raise NativeParserError("MARKDOWN_INPUT_UNREADABLE", retryable=True) from None
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise NativeParserError("INPUT_HASH_MISMATCH")
    if b"\x00" in payload:
        raise NativeParserError("MARKDOWN_BINARY_CONTENT")
    try:
        source = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise NativeParserError("MARKDOWN_ENCODING_INVALID") from None
    if not source.strip():
        raise NativeParserError("MARKDOWN_EMPTY")
    return source


def _sections(tokens: list[Token]) -> tuple[_SectionDraft, ...]:
    sections: list[tuple[tuple[str, ...], list[_BlockDraft]]] = []
    heading_path: list[str] = []
    current_path: tuple[str, ...] = ("文档开头",)
    current_blocks: list[_BlockDraft] = []

    def flush() -> None:
        nonlocal current_blocks
        if current_blocks:
            sections.append((current_path, current_blocks))
            current_blocks = []

    for index, token in enumerate(tokens):
        previous_type = tokens[index - 1].type if index > 0 else None
        if token.type == "inline" and previous_type == "heading_open":
            title = _inline_text(token).strip()
            if not title:
                continue
            flush()
            level = int(tokens[index - 1].tag.removeprefix("h"))
            heading_path[level - 1 :] = [title]
            current_path = tuple(heading_path)
            start, end = _line_range(token)
            current_blocks.append(
                _BlockDraft(
                    type=BlockType.TITLE,
                    text=title,
                    section_path=current_path,
                    line_start=start,
                    line_end=end,
                    heading_level=level,
                )
            )
            continue
        if token.type == "inline" and previous_type == "paragraph_open":
            text = _inline_text(token).strip()
            if text:
                start, end = _line_range(token)
                current_blocks.append(
                    _BlockDraft(
                        type=BlockType.PARAGRAPH,
                        text=text,
                        section_path=current_path,
                        line_start=start,
                        line_end=end,
                    )
                )
            continue
        if token.type in {"fence", "code_block"}:
            text = token.content.strip()
            if text:
                start, end = _line_range(token)
                current_blocks.append(
                    _BlockDraft(
                        type=BlockType.CODE,
                        text=text,
                        section_path=current_path,
                        line_start=start,
                        line_end=end,
                    )
                )
    flush()
    return tuple(_SectionDraft(path=path, blocks=tuple(blocks)) for path, blocks in sections)


def _inline_text(token: Token) -> str:
    parts: list[str] = []
    for child in token.children or ():
        if child.type in {"text", "code_inline", "image"}:
            parts.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append("\n")
    return "".join(parts)


def _line_range(token: Token) -> tuple[int, int]:
    if token.map is None:
        return (1, 1)
    return (token.map[0] + 1, max(token.map[0] + 1, token.map[1]))


def _page(section: _SectionDraft, ordinal: int) -> RawPage:
    height = max(_BLOCK_HEIGHT, len(section.blocks) * _BLOCK_HEIGHT)
    blocks = [
        RawBlock(
            type=block.type,
            text=block.text,
            bbox=RawBoundingBox(
                x0=0,
                top=index * _BLOCK_HEIGHT,
                x1=_VIRTUAL_WIDTH,
                bottom=(index + 1) * _BLOCK_HEIGHT,
            ),
            reading_order=index,
            section_path=list(block.section_path),
            metadata={
                "line_start": block.line_start,
                "line_end": block.line_end,
                **(
                    {"heading_level": block.heading_level}
                    if block.heading_level is not None
                    else {}
                ),
            },
        )
        for index, block in enumerate(section.blocks)
    ]
    return RawPage(
        ordinal=ordinal,
        width=_VIRTUAL_WIDTH,
        height=height,
        source_kind="section",
        native_text_present=True,
        blocks=blocks,
        metadata={"section_path": " > ".join(section.path)},
    )


def _selected_ordinals(requested: tuple[int, ...], total: int) -> tuple[int, ...]:
    selected = requested or tuple(range(1, total + 1))
    if any(ordinal > total for ordinal in selected):
        raise NativeParserError("REQUESTED_PAGE_INVALID")
    return selected
