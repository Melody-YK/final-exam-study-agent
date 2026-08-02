from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from study_worker.parsers.markdown_native import MARKDOWN_MEDIA_TYPE, MarkdownNativeParser
from study_worker.parsers.protocols import ParseRequest
from study_worker.parsers.router import NativeParserError


def _request(path: Path, payload: bytes, requested: tuple[int, ...] = ()) -> ParseRequest:
    path.write_bytes(payload)
    return ParseRequest(
        job_id="job-markdown",
        document_id="document-markdown",
        document_sha256=hashlib.sha256(payload).hexdigest(),
        media_type=MARKDOWN_MEDIA_TYPE,
        input_path=path,
        output_dir=path.parent / "output",
        requested_pages=requested,
    )


@pytest.mark.asyncio
async def test_parses_commonmark_into_nested_sections(tmp_path: Path) -> None:
    payload = (
        "preface\n\n# 操作系统\n\n课程介绍。\n\n## 进程\n\n"
        "- 进程是资源分配单位\n- 线程是调度单位\n\n```c\nfork();\n```\n"
    ).encode()
    result = await MarkdownNativeParser(max_sections=10).parse(
        _request(tmp_path / "input.md", payload)
    )

    assert result.source_backend == "markdown-native"
    assert result.total_page_count == 3
    assert [page.source_kind for page in result.pages] == ["section", "section", "section"]
    assert result.pages[0].metadata["section_path"] == "文档开头"
    assert result.pages[1].blocks[0].section_path == ["操作系统"]
    assert result.pages[2].blocks[0].section_path == ["操作系统", "进程"]
    assert [block.text for block in result.pages[2].blocks] == [
        "进程",
        "进程是资源分配单位",
        "线程是调度单位",
        "fork();",
    ]


@pytest.mark.asyncio
async def test_returns_only_requested_section(tmp_path: Path) -> None:
    payload = b"# One\n\nFirst.\n\n# Two\n\nSecond.\n"
    result = await MarkdownNativeParser(max_sections=10).parse(
        _request(tmp_path / "input.md", payload, (2,))
    )

    assert result.total_page_count == 2
    assert [page.ordinal for page in result.pages] == [2]
    assert [block.text for block in result.pages[0].blocks] == ["Two", "Second."]


@pytest.mark.asyncio
async def test_ignored_preamble_syntax_does_not_shift_section_ordinals(tmp_path: Path) -> None:
    payload = b"---\n\n# First\n\nBody.\n"
    result = await MarkdownNativeParser(max_sections=10).parse(
        _request(tmp_path / "input.md", payload)
    )

    assert result.total_page_count == 1
    assert result.pages[0].metadata["section_path"] == "First"
    assert [block.text for block in result.pages[0].blocks] == ["First", "Body."]


@pytest.mark.asyncio
async def test_image_alt_text_creates_a_preamble_section(tmp_path: Path) -> None:
    payload = b"![Diagram](https://example.com/diagram.png)\n\n# First\n\nBody.\n"
    result = await MarkdownNativeParser(max_sections=10).parse(
        _request(tmp_path / "input.md", payload)
    )

    assert result.total_page_count == 2
    assert result.pages[0].metadata["section_path"] == "文档开头"
    assert [block.text for block in result.pages[0].blocks] == ["Diagram"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"\x00binary", "MARKDOWN_BINARY_CONTENT"),
        (b"\xff", "MARKDOWN_ENCODING_INVALID"),
        (b"   \n", "MARKDOWN_EMPTY"),
    ],
)
async def test_rejects_invalid_markdown_source(tmp_path: Path, payload: bytes, code: str) -> None:
    with pytest.raises(NativeParserError, match=code):
        await MarkdownNativeParser(max_sections=10).parse(_request(tmp_path / "input.md", payload))


@pytest.mark.asyncio
async def test_enforces_section_limit(tmp_path: Path) -> None:
    payload = b"# One\n\n# Two\n"
    with pytest.raises(NativeParserError, match="PAGE_LIMIT_EXCEEDED"):
        await MarkdownNativeParser(max_sections=1).parse(_request(tmp_path / "input.md", payload))
