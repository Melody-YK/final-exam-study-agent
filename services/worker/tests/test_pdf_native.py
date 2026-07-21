from __future__ import annotations

from pathlib import Path

import pytest

from study_contracts import BlockType
from study_worker.parsers.pdf_native import PDFNativeParser
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

    assert result.total_page_count == 2
    assert [page.ordinal for page in result.pages] == [1, 2]
    first = result.pages[0]
    assert first.native_text_present is True
    assert any(block.type is BlockType.TITLE for block in first.blocks)
    assert any("virtual memory" in block.text for block in first.blocks)
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
