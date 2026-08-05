from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from study_worker.rendering.pdfium import PdfRenderError, render_pdf_page
from tests.fixtures.build_documents import build_documents, sha256


def test_render_pdf_page_writes_bounded_png(tmp_path: Path) -> None:
    source = build_documents(tmp_path / "fixtures").pdf
    destination = tmp_path / "output" / "rendered-page-000002.png"

    result = render_pdf_page(
        source,
        destination,
        ordinal=2,
        expected_sha256=sha256(source),
        max_pages=20,
        max_pixels=10_000_000,
    )

    assert result.path == destination
    assert result.sha256
    assert result.size_bytes == destination.stat().st_size
    assert (result.width, result.height) == (1224, 1584)
    with Image.open(destination) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (result.width, result.height)
    assert destination.stat().st_mode & 0o777 == 0o600


def test_render_pdf_page_rejects_pixel_bomb_before_rasterizing(tmp_path: Path) -> None:
    source = build_documents(tmp_path / "fixtures").pdf

    with pytest.raises(PdfRenderError, match="PDF_RENDER_PIXEL_LIMIT_EXCEEDED"):
        render_pdf_page(
            source,
            tmp_path / "output" / "rendered-page.png",
            ordinal=1,
            expected_sha256=sha256(source),
            max_pages=20,
            max_pixels=1_000,
        )


def test_render_pdf_page_rejects_input_hash_mismatch(tmp_path: Path) -> None:
    source = build_documents(tmp_path / "fixtures").pdf

    with pytest.raises(PdfRenderError, match="PDF_RENDER_INPUT_HASH_MISMATCH"):
        render_pdf_page(
            source,
            tmp_path / "output" / "rendered-page.png",
            ordinal=1,
            expected_sha256="0" * 64,
            max_pages=20,
            max_pixels=10_000_000,
        )
