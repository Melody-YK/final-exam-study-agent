from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from study_contracts import BlockType, PageQualityStatus
from study_worker.parsers.normalize import RawBlock, RawBoundingBox, RawPage, normalize_page
from study_worker.parsers.pdf_native import PDFNativeParser
from study_worker.parsers.protocols import ParseRequest
from study_worker.parsers.quality import evaluate_page_quality, native_text_mapping_is_corrupted
from tests.fixtures.build_documents import build_documents, sha256


def _native_page(
    text: str,
    *,
    source_kind: Literal["page", "slide", "section"] = "page",
    text_mapping_corrupted: bool | None = None,
) -> RawPage:
    return RawPage(
        ordinal=1,
        width=100,
        height=100,
        source_kind=source_kind,
        native_text_present=True,
        metadata=(
            {"text_mapping_corrupted": text_mapping_corrupted}
            if text_mapping_corrupted is not None
            else {}
        ),
        blocks=[
            RawBlock(
                type=BlockType.PARAGRAPH,
                text=text,
                bbox=RawBoundingBox(x0=0, top=0, x1=100, bottom=100),
                reading_order=0,
            )
        ],
    )


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


@pytest.mark.parametrize(
    "text",
    [
        "资" * 80 + "学" * 12 + "操" * 6 + "使" * 4,
        "资," * 30 + "学," * 4,
    ],
)
def test_pdf_cjk_mapping_detector_flags_corrupted_character_mapping(text: str) -> None:
    assert native_text_mapping_is_corrupted(text) is True


@pytest.mark.parametrize("source_kind", ["section", "slide"])
def test_generic_quality_does_not_apply_pdf_cjk_mapping_detector(
    source_kind: Literal["section", "slide"],
) -> None:
    text = "资" * 80 + "学" * 12 + "操" * 6 + "使" * 4
    assert native_text_mapping_is_corrupted(text) is True

    quality = evaluate_page_quality(_native_page(text, source_kind=source_kind))

    assert quality.status is PageQualityStatus.PASSED
    assert quality.requires_ocr is False


def test_quality_requires_ocr_when_parser_marks_text_mapping_as_corrupted() -> None:
    quality = evaluate_page_quality(
        _native_page(
            "资" * 80 + "学" * 12 + "操" * 6 + "使" * 4,
            text_mapping_corrupted=True,
        )
    )

    assert quality.status is PageQualityStatus.FAILED
    assert quality.text_layer == "native"
    assert quality.requires_ocr is True
    assert [issue.code for issue in quality.issues] == ["OCR_REQUIRED"]
    assert quality.issues[0].retryable is True


def test_quality_accepts_repaired_page_when_corruption_marker_is_false() -> None:
    quality = evaluate_page_quality(
        _native_page(
            "修复后的文本由解析器确认可用。",
            text_mapping_corrupted=False,
        )
    )

    assert quality.status is PageQualityStatus.PASSED
    assert quality.requires_ocr is False


def test_quality_accepts_valid_chinese_prose() -> None:
    raw_page = _native_page(
        "操作系统通过进程调度协调处理器资源, 并使用虚拟内存隔离不同程序的地址空间。"
        "文件系统负责持久化数据, 访问控制则限制用户能够执行的操作。"
    )

    quality = evaluate_page_quality(raw_page)

    assert (
        native_text_mapping_is_corrupted("\n".join(block.text for block in raw_page.blocks))
        is False
    )
    assert quality.status is PageQualityStatus.PASSED
    assert quality.requires_ocr is False


@pytest.mark.parametrize(
    "text",
    [
        "哈" * 23,
        "天地玄黄宇" * 12,
    ],
)
def test_quality_accepts_legitimate_repeated_cjk_snippets(text: str) -> None:
    quality = evaluate_page_quality(_native_page(text))

    assert native_text_mapping_is_corrupted(text) is False
    assert quality.status is PageQualityStatus.PASSED
    assert quality.requires_ocr is False


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
