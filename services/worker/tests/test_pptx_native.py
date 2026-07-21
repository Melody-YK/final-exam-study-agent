from __future__ import annotations

from pathlib import Path

import pytest

from study_contracts import BlockType
from study_worker.parsers.pptx_native import PPTXNativeParser
from study_worker.parsers.protocols import ParseRequest
from study_worker.parsers.router import NativeParserError
from tests.fixtures.build_documents import build_documents, sha256

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _request(path: Path, output_dir: Path) -> ParseRequest:
    return ParseRequest(
        job_id="job-1",
        document_id="document-1",
        document_sha256=sha256(path),
        media_type=PPTX_MEDIA_TYPE,
        input_path=path,
        output_dir=output_dir,
    )


@pytest.mark.asyncio
async def test_pptx_native_extracts_structure_and_never_activates_ole(tmp_path: Path) -> None:
    fixture = build_documents(tmp_path / "fixtures").pptx
    output_dir = tmp_path / "output"
    parser = PPTXNativeParser(max_pages=20, max_pixels=10_000_000)

    result = await parser.parse(_request(fixture, output_dir))

    assert result.total_page_count == 1
    page = result.pages[0]
    assert page.source_kind == "slide"
    assert page.native_text_present is True
    assert any(
        block.type is BlockType.TITLE and "Operating Systems" in block.text for block in page.blocks
    )
    table = next(block for block in page.blocks if block.type is BlockType.TABLE)
    assert table.metadata["row_count"] == 2
    assert table.metadata["column_count"] == 2
    assert any(block.metadata.get("shape_name") == "Rectangle 4" for block in page.blocks)
    picture = next(block for block in page.blocks if block.artifact is not None)
    assert picture.type is BlockType.IMAGE
    assert picture.artifact is not None
    assert (output_dir / picture.artifact.relative_path).is_file()
    assert any(block.metadata.get("omml") is True for block in page.blocks)
    assert any(block.metadata.get("smartart") is True for block in page.blocks)
    ole = next(block for block in page.blocks if block.metadata.get("ole") is True)
    assert ole.metadata["prog_id"] == "Package"
    assert page.metadata["external_relationship_count"] == 1


@pytest.mark.asyncio
async def test_pptx_native_rejects_macro_package_and_slide_pixel_limit(tmp_path: Path) -> None:
    fixtures = build_documents(tmp_path / "fixtures")
    parser = PPTXNativeParser(max_pages=20, max_pixels=100)

    with pytest.raises(NativeParserError, match="MACRO_CONTENT_BLOCKED"):
        await parser.parse(_request(fixtures.macro_pptx, tmp_path / "macro-output"))
    with pytest.raises(NativeParserError, match="PIXEL_LIMIT_EXCEEDED"):
        await parser.parse(_request(fixtures.pptx, tmp_path / "output"))
