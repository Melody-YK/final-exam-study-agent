from __future__ import annotations

import os
from pathlib import Path

import pytest

from study_contracts import BlockType
from study_worker.parsers.normalize import RawBlock, RawBoundingBox, RawPage
from study_worker.parsers.pdf_native import (
    PDFNativeParser,
    _parse_poppler_pages,
    _poppler_text_pages,
    _repair_corrupted_pages,
)
from study_worker.parsers.protocols import ParseRequest
from study_worker.parsers.router import NativeParserError
from tests.fixtures.build_documents import build_documents, sha256


def _request(path: Path, output_dir: Path, requested_pages: tuple[int, ...] = ()) -> ParseRequest:
    return ParseRequest(
        job_id="job-1",
        document_id="document-1",
        document_sha256=sha256(path),
        media_type="application/pdf",
        input_path=path,
        output_dir=output_dir,
        requested_pages=requested_pages,
    )


@pytest.mark.asyncio
async def test_pdf_native_extracts_text_coordinates_table_and_image_metadata(
    tmp_path: Path,
) -> None:
    fixture = build_documents(tmp_path / "fixtures").pdf
    parser = PDFNativeParser(max_pages=20, max_pixels=10_000_000)

    result = await parser.parse(_request(fixture, tmp_path / "output"))

    assert result.source_version == parser.capability.source_version
    assert result.source_version.startswith("1.2+")
    assert result.total_page_count == 2
    assert [page.ordinal for page in result.pages] == [1, 2]
    first = result.pages[0]
    assert first.native_text_present is True
    titles = [block for block in first.blocks if block.type is BlockType.TITLE]
    assert [block.text for block in titles] == ["Operating Systems Review"]
    large_body = next(block for block in first.blocks if "virtual memory" in block.text)
    assert large_body.type is BlockType.PARAGRAPH
    assert large_body.metadata["font_size"] == 24
    second_body = next(block for block in first.blocks if "Large body text" in block.text)
    assert second_body.type is BlockType.PARAGRAPH
    assert second_body.metadata["font_size"] == 20
    table = next(block for block in first.blocks if block.type is BlockType.TABLE)
    assert "Semaphore" in table.text
    assert table.metadata["row_count"] == 2
    assert table.metadata["column_count"] == 2
    image = next(block for block in first.blocks if block.type is BlockType.IMAGE)
    assert image.metadata["pixel_width"] == 1
    assert image.metadata["pixel_height"] == 1
    assert all(
        0 <= block.bbox.x0 <= block.bbox.x1 <= first.width
        and 0 <= block.bbox.top <= block.bbox.bottom <= first.height
        for block in first.blocks
    )
    assert result.pages[1].native_text_present is False


def test_poppler_bbox_text_preserves_cjk_terms_and_english_spacing() -> None:
    payload = b"""<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><body><doc>
      <page width="100" height="100"><flow><block><line xMin="1" yMin="2" xMax="90" yMax="12">
        <word>\xe8\xbf\x9b</word><word>\xe7\xa8\x8b</word><word>CPU</word><word>scheduling</word>
      </line></block></flow></page></doc></body></html>"""

    pages = _parse_poppler_pages(payload, first_page=3, last_page=3)

    assert pages[3][0][4] == "\u8fdb\u7a0bCPU scheduling"


def test_pdf_source_version_fingerprints_installed_pdftotext(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "pdftotext"
    executable.write_bytes(b"test pdftotext binary")
    monkeypatch.setattr(
        "study_worker.parsers.pdf_native._find_pdftotext",
        lambda: str(executable),
    )

    parser = PDFNativeParser(max_pages=20, max_pixels=10_000_000)

    assert parser.capability.source_version == f"1.2+poppler.{sha256(executable)[:12]}"


def test_pdf_source_version_identifies_native_only_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "study_worker.parsers.pdf_native._find_pdftotext",
        lambda: None,
    )

    parser = PDFNativeParser(max_pages=20, max_pixels=10_000_000)

    assert parser.capability.source_version == "1.2+native"


def test_corrupted_native_page_uses_poppler_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = RawPage(
        ordinal=1,
        width=100,
        height=100,
        source_kind="page",
        native_text_present=True,
        blocks=[
            RawBlock(
                type=BlockType.PARAGRAPH,
                text="\u8d44" * 80 + "\u5b66" * 12 + "\u64cd" * 6,
                bbox=RawBoundingBox(x0=0, top=0, x1=100, bottom=80),
                reading_order=0,
            )
        ],
    )
    lines = (
        (5.0, 5.0, 80.0, 25.0, "\u8fdb\u7a0b\u8c03\u5ea6", 20.0),
        (
            5.0,
            30.0,
            90.0,
            60.0,
            "\u8c03\u5ea6\u5668\u9009\u62e9\u5c31\u7eea\u8fdb\u7a0b\u5e76\u5206\u914d\u5904\u7406\u5668\u65f6\u95f4\u7247\uff0c"
            "\u963b\u585e\u8fdb\u7a0b\u5728\u4e8b\u4ef6\u5b8c\u6210\u540e\u91cd\u65b0\u8fdb\u5165\u5c31\u7eea\u961f\u5217\u3002"
            "\u8be5\u8fc7\u7a0b\u4f1a\u6301\u7eed\u91cd\u590d\u76f4\u5230\u4f5c\u4e1a\u5b8c\u6210\u3002",
            10.0,
        ),
    )
    monkeypatch.setattr(
        "study_worker.parsers.pdf_native._poppler_text_pages",
        lambda _path, *, executable, page_ordinals, total_page_count: {1: lines},
    )

    repaired, used_poppler = _repair_corrupted_pages(
        [page],
        input_path=Path("unused.pdf"),
        total_page_count=1,
        pdftotext_executable="/unused/pdftotext",
        extractor_identity="1.2+poppler.0123456789ab",
    )

    assert used_poppler is True
    assert [block.text for block in repaired[0].blocks] == [
        "\u8fdb\u7a0b\u8c03\u5ea6",
        "\u8c03\u5ea6\u5668\u9009\u62e9\u5c31\u7eea\u8fdb\u7a0b\u5e76\u5206\u914d\u5904\u7406\u5668\u65f6\u95f4\u7247\uff0c"
        "\u963b\u585e\u8fdb\u7a0b\u5728\u4e8b\u4ef6\u5b8c\u6210\u540e\u91cd\u65b0\u8fdb\u5165\u5c31\u7eea\u961f\u5217\u3002"
        "\u8be5\u8fc7\u7a0b\u4f1a\u6301\u7eed\u91cd\u590d\u76f4\u5230\u4f5c\u4e1a\u5b8c\u6210\u3002",
    ]
    assert repaired[0].blocks[0].type is BlockType.TITLE
    assert repaired[0].blocks[1].type is BlockType.PARAGRAPH
    assert repaired[0].metadata["text_extraction"] == "poppler-bbox"
    assert repaired[0].metadata["text_extractor"] == "1.2+poppler.0123456789ab"
    assert repaired[0].metadata["text_mapping_corrupted"] is False


def test_quarter_length_poppler_output_does_not_replace_corrupted_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_text = "\u8d44" * 80 + "\u5b66" * 12 + "\u64cd" * 6
    page = RawPage(
        ordinal=1,
        width=100,
        height=100,
        source_kind="page",
        native_text_present=True,
        blocks=[
            RawBlock(
                type=BlockType.PARAGRAPH,
                text=original_text,
                bbox=RawBoundingBox(x0=0, top=0, x1=100, bottom=80),
                reading_order=0,
            )
        ],
    )
    partial_lines = ((5.0, 5.0, 80.0, 80.0, "A" * 25, 10.0),)
    monkeypatch.setattr(
        "study_worker.parsers.pdf_native._poppler_text_pages",
        lambda _path, *, executable, page_ordinals, total_page_count: {1: partial_lines},
    )

    repaired, used_poppler = _repair_corrupted_pages(
        [page],
        input_path=Path("unused.pdf"),
        total_page_count=1,
        pdftotext_executable="/unused/pdftotext",
        extractor_identity="1.2+poppler.0123456789ab",
    )

    assert used_poppler is False
    assert [block.text for block in repaired[0].blocks] == [original_text]
    assert repaired[0].metadata["text_mapping_corrupted"] is True


def test_poppler_repair_preserves_table_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = RawPage(
        ordinal=1,
        width=100,
        height=100,
        source_kind="page",
        native_text_present=True,
        blocks=[
            RawBlock(
                type=BlockType.PARAGRAPH,
                text="\u8d44" * 60 + "\u5b66" * 10,
                bbox=RawBoundingBox(x0=0, top=0, x1=100, bottom=40),
                reading_order=0,
            ),
            RawBlock(
                type=BlockType.TABLE,
                text="\u8d44" * 20,
                bbox=RawBoundingBox(x0=10, top=50, x1=90, bottom=90),
                reading_order=1,
                metadata={"table_index": 1, "row_count": 2, "column_count": 2},
            ),
        ],
        metadata={"table_count": 1},
    )
    lines = (
        (
            5.0,
            5.0,
            90.0,
            20.0,
            "\u8fdb\u7a0b\u8c03\u5ea6\u8d1f\u8d23\u9009\u62e9\u5c31\u7eea\u8fdb\u7a0b\u5e76\u5206\u914d\u5904\u7406\u5668\uff0c"
            "\u8868\u683c\u4e2d\u7684\u4f18\u5148\u7ea7\u548c\u65f6\u95f4\u7247\u7528\u4e8e\u6bd4\u8f83\u8c03\u5ea6\u7b56\u7565\u3002",
            15.0,
        ),
        (
            5.0,
            55.0,
            90.0,
            80.0,
            "\u4f18\u5148\u7ea7\u6570\u503c\u8d8a\u5c0f\u8868\u793a\u8c03\u5ea6\u987a\u5e8f\u8d8a\u9760\u524d\u3002",
            10.0,
        ),
    )
    monkeypatch.setattr(
        "study_worker.parsers.pdf_native._poppler_text_pages",
        lambda _path, *, executable, page_ordinals, total_page_count: {1: lines},
    )

    repaired, used_poppler = _repair_corrupted_pages(
        [page],
        input_path=Path("unused.pdf"),
        total_page_count=1,
        pdftotext_executable="/unused/pdftotext",
        extractor_identity="1.2+poppler.0123456789ab",
    )

    assert used_poppler is True
    table = next(block for block in repaired[0].blocks if block.type is BlockType.TABLE)
    assert table.text == ""
    assert table.metadata["row_count"] == 2
    assert table.metadata["column_count"] == 2
    assert table.metadata["text_mapping_degraded"] is True
    assert repaired[0].metadata["table_count"] == 1
    assert repaired[0].metadata["table_text_degraded"] is True


def test_pdftotext_output_limit_stops_before_reading_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "oversized-pdftotext"
    executable.write_text(
        "#!/bin/sh\nprintf '%0256d' 0\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    monkeypatch.setattr("study_worker.parsers.pdf_native._PDFTOTEXT_MAX_OUTPUT_BYTES", 32)

    pages = _poppler_text_pages(
        tmp_path / "input.pdf",
        executable=str(executable),
        page_ordinals=(1,),
        total_page_count=1,
    )

    assert pages is None


def test_pdftotext_command_failure_is_rejected(tmp_path: Path) -> None:
    executable = tmp_path / "failing-pdftotext"
    executable.write_text(
        "#!/bin/sh\nexit 2\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)

    pages = _poppler_text_pages(
        tmp_path / "input.pdf",
        executable=str(executable),
        page_ordinals=(1,),
        total_page_count=1,
    )

    assert pages is None


def test_pdftotext_runs_in_the_outer_parser_process_group(tmp_path: Path) -> None:
    executable = tmp_path / "process-group-pdftotext"
    executable.write_text(
        """#!/usr/bin/env python3
import os

print(f'''<html><body><doc><page width="100" height="100"><flow><block>
<line xMin="1" yMin="2" xMax="90" yMax="12"><word>{os.getpgrp()}</word></line>
</block></flow></page></doc></body></html>''')
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)

    pages = _poppler_text_pages(
        tmp_path / "input.pdf",
        executable=str(executable),
        page_ordinals=(1,),
        total_page_count=1,
    )

    assert pages is not None
    assert pages[1][0][4] == str(os.getpgrp())


def test_pdftotext_timeout_kills_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "slow-pdftotext"
    executable.write_text(
        "#!/usr/bin/env python3\nimport time\ntime.sleep(5)\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    monkeypatch.setattr("study_worker.parsers.pdf_native._PDFTOTEXT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("study_worker.parsers.pdf_native._PDFTOTEXT_POLL_SECONDS", 0.005)

    pages = _poppler_text_pages(
        tmp_path / "input.pdf",
        executable=str(executable),
        page_ordinals=(1,),
        total_page_count=1,
    )

    assert pages is None


@pytest.mark.asyncio
async def test_pdf_native_honors_requested_page_coverage(tmp_path: Path) -> None:
    fixture = build_documents(tmp_path / "fixtures").pdf
    parser = PDFNativeParser(max_pages=20, max_pixels=10_000_000)

    result = await parser.parse(_request(fixture, tmp_path / "output", requested_pages=(2,)))

    assert result.total_page_count == 2
    assert [page.ordinal for page in result.pages] == [2]


@pytest.mark.asyncio
async def test_pdf_native_rejects_fake_pdf_and_page_limit(tmp_path: Path) -> None:
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"not a pdf")
    parser = PDFNativeParser(max_pages=1, max_pixels=10_000_000)

    with pytest.raises(NativeParserError, match="PDF_CONTAINER_INVALID"):
        await parser.parse(_request(fake, tmp_path / "fake-output"))

    fixture = build_documents(tmp_path / "fixtures").pdf
    with pytest.raises(NativeParserError, match="PAGE_LIMIT_EXCEEDED"):
        await parser.parse(_request(fixture, tmp_path / "output"))
