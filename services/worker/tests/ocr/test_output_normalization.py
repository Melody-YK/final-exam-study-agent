from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from study_contracts import BlockType, PageQualityStatus
from study_worker.parsers.complex import ComplexityRouter, OcrBackend, PageComplexity
from study_worker.parsers.paddle_general import (
    PaddleGeneralOutput,
    normalize_paddle_general_output,
    polygon_to_bbox,
)
from study_worker.parsers.pp_structure import normalize_pp_structure_output


def _general_payload() -> dict[str, object]:
    return {
        "page_index": 0,
        "image_width": 1000,
        "image_height": 500,
        "dt_polys": [
            [[100, 100], [500, 100], [500, 200], [100, 200]],
            [[100, 250], [700, 250], [700, 350], [100, 350]],
        ],
        "rec_polys": None,
        "rec_texts": ["Self-authored title", "Scheduling policy"],
        "rec_scores": [0.99, 0.8],
    }


def test_general_output_normalizes_polygon_bbox_score_and_page_provenance() -> None:
    page = normalize_paddle_general_output(
        _general_payload(),
        page_ordinal=1,
        raw_result_ref="opaque-raw-receipt",
        source_version="3.7.0-test",
    )

    assert page.source_backend == "paddleocr-general"
    assert page.source_version == "3.7.0-test"
    assert page.quality is not None
    assert page.quality.status is PageQualityStatus.WARNING
    assert page.quality.text_layer == "ocr"
    assert [block.text for block in page.blocks] == [
        "Self-authored title",
        "Scheduling policy",
    ]
    assert page.blocks[0].bbox_norm.model_dump() == {
        "x": 0.1,
        "y": 0.2,
        "width": 0.4,
        "height": 0.2,
    }
    assert page.blocks[1].confidence == 0.8
    assert all(block.raw_result_ref == "opaque-raw-receipt" for block in page.blocks)


def test_general_output_marks_empty_text_retryable_without_an_empty_success() -> None:
    payload = {**_general_payload(), "rec_texts": ["", "   "]}

    page = normalize_paddle_general_output(
        payload,
        page_ordinal=1,
        raw_result_ref="opaque-empty-receipt",
        source_version="3.7.0-test",
    )

    assert page.blocks == []
    assert page.quality is not None
    assert page.quality.status is PageQualityStatus.FAILED
    assert page.quality.requires_ocr is True
    assert [issue.code for issue in page.quality.issues] == ["OCR_EMPTY_RESULT"]
    assert page.quality.issues[0].retryable is True


def test_general_output_rejects_untrusted_shape_score_and_page_bounds() -> None:
    with pytest.raises(ValidationError, match="align"):
        PaddleGeneralOutput.model_validate({**_general_payload(), "rec_scores": [0.9]})
    with pytest.raises(ValidationError, match="probabilities"):
        PaddleGeneralOutput.model_validate({**_general_payload(), "rec_scores": [math.nan, 0.8]})
    with pytest.raises(ValueError, match="exceeds"):
        polygon_to_bbox(
            [(0, 0), (1001, 0), (1001, 10), (0, 10)],
            page_width=1000,
            page_height=500,
        )
    with pytest.raises(ValidationError, match="extra"):
        PaddleGeneralOutput.model_validate({**_general_payload(), "raw_image": "blocked"})


def test_pp_structure_normalizes_experimental_blocks_and_reading_order() -> None:
    page = normalize_pp_structure_output(
        {
            "page_index": 1,
            "image_width": 1200,
            "image_height": 1600,
            "parsing_res_list": [
                {
                    "block_label": "table",
                    "block_content": "A | B",
                    "block_bbox": [100, 400, 1100, 900],
                    "block_order": 2,
                    "block_score": 0.8,
                },
                {
                    "block_label": "doc_title",
                    "block_content": "Self-authored layout",
                    "block_bbox": [100, 100, 900, 250],
                    "block_order": 1,
                    "score": 0.95,
                },
            ],
        },
        page_ordinal=2,
        raw_result_ref="opaque-structure-receipt",
        source_version="3.7.0-test",
    )

    assert page.source_backend == "pp-structure-v3"
    assert [block.type for block in page.blocks] == [BlockType.TITLE, BlockType.TABLE]
    assert [block.reading_order for block in page.blocks] == [0, 1]
    assert page.blocks[1].metadata["pp_structure_label"] == "table"
    assert page.quality is not None
    assert [issue.code for issue in page.quality.issues] == ["PP_STRUCTURE_EXPERIMENTAL"]


def test_pp_structure_rejects_duplicate_orders_and_out_of_page_bbox() -> None:
    duplicate = {
        "page_index": 0,
        "image_width": 100,
        "image_height": 100,
        "parsing_res_list": [
            {
                "block_label": "text",
                "block_content": "one",
                "block_bbox": [0, 0, 10, 10],
                "block_order": 1,
            },
            {
                "block_label": "text",
                "block_content": "two",
                "block_bbox": [0, 20, 10, 30],
                "block_order": 1,
            },
        ],
    }
    with pytest.raises(ValidationError, match="orders must be unique"):
        normalize_pp_structure_output(
            duplicate,
            page_ordinal=1,
            raw_result_ref="opaque",
            source_version="test",
        )
    outside = {
        **duplicate,
        "parsing_res_list": [
            {
                "block_label": "text",
                "block_content": "outside",
                "block_bbox": [0, 0, 101, 10],
            }
        ],
    }
    with pytest.raises(ValueError, match="exceeds"):
        normalize_pp_structure_output(
            outside,
            page_ordinal=1,
            raw_result_ref="opaque",
            source_version="test",
        )


def test_complexity_router_defaults_general_and_never_enables_mineru_or_paid_ocr() -> None:
    complex_page = PageComplexity(table_regions=1, estimated_columns=2)
    default_router = ComplexityRouter(pp_structure_available=True)

    default_decision = default_router.route(complex_page)

    assert default_decision.backend is OcrBackend.PADDLE_GENERAL
    assert default_decision.reason_code == "COMPLEX_PARSER_DISABLED"
    enabled_decision = ComplexityRouter(
        enabled=True,
        pp_structure_available=True,
    ).route(complex_page)
    assert enabled_decision.backend is OcrBackend.PP_STRUCTURE_V3
    assert enabled_decision.experimental is True
    assert default_router.availability(OcrBackend.MINERU).available is False
    assert default_router.availability(OcrBackend.MINERU).reason_code == "MINERU_DISABLED"
    assert default_router.availability(OcrBackend.PAID_OCR).available is False
    assert default_router.availability(OcrBackend.PAID_OCR).reason_code == "PAID_OCR_DISABLED"
