from __future__ import annotations

from pathlib import Path

import pytest

from study_contracts import PageQualityStatus
from study_worker.parsers.normalize import normalize_page
from study_worker.parsers.pdf_native import PDFNativeParser
from study_worker.parsers.protocols import ParseRequest
from study_worker.parsers.quality import evaluate_page_quality
from tests.fixtures.build_documents import build_documents, sha256


@pytest.mark.asyncio
async def test_quality_marks_native_text_and_requires_ocr_for_image_only_page(
    tmp_path: Path,
) -> None:
    fixture = build_documents(tmp_path / "fixtures").pdf
    raw = await PDFNativeParser(max_pages=20, max_pixels=10_000_000).parse(
        ParseRequest(
            job_id="job-1",
            document_id="document-1",
            document_sha256=sha256(fixture),
            media_type="application/pdf",
            input_path=fixture,
            output_dir=tmp_path / "output",
        )
    )

    first_quality = evaluate_page_quality(raw.pages[0])
    second_quality = evaluate_page_quality(raw.pages[1])

    assert first_quality.status is PageQualityStatus.PASSED
    assert first_quality.text_layer == "native"
    assert first_quality.requires_ocr is False
    assert second_quality.status is PageQualityStatus.FAILED
    assert second_quality.text_layer == "none"
    assert second_quality.requires_ocr is True
    assert [issue.code for issue in second_quality.issues] == ["OCR_REQUIRED"]
    assert second_quality.issues[0].retryable is True


@pytest.mark.asyncio
async def test_normalization_produces_full_page_bbox_stable_ids_and_opaque_raw_refs(
    tmp_path: Path,
) -> None:
    fixture = build_documents(tmp_path / "fixtures").pdf
    raw = await PDFNativeParser(max_pages=20, max_pixels=10_000_000).parse(
        ParseRequest(
            job_id="job-1",
            document_id="document-1",
            document_sha256=sha256(fixture),
            media_type="application/pdf",
            input_path=fixture,
            output_dir=tmp_path / "output",
            requested_pages=(1,),
        )
    )
    raw_page = raw.pages[0]
    opaque_ref = "opaque-receipt-from-api"

    page = normalize_page(
        raw_page,
        raw_result_ref=opaque_ref,
        quality=evaluate_page_quality(raw_page),
        source_backend=raw.source_backend,
        source_version=raw.source_version,
    )

    assert page.bbox_norm.model_dump() == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    assert page.raw_result_ref == opaque_ref
    assert all(block.raw_result_ref == opaque_ref for block in page.blocks)
    assert len({block.id for block in page.blocks}) == len(page.blocks)
    assert all(0 <= block.bbox_norm.x <= 1 for block in page.blocks)
    title = next(block for block in page.blocks if block.type.value == "title")
    paragraph = next(block for block in page.blocks if "virtual memory" in block.text)
    assert paragraph.parent_id == title.id
    assert paragraph.section_path == [title.text]
