"""Pure normalization for JSON emitted by an isolated PaddleOCR General process."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from study_contracts import (
    BlockType,
    BoundingBox,
    Page,
    PageIssue,
    PageIssueSeverity,
    PageQuality,
    PageQualityStatus,
)
from study_worker.parsers.normalize import (
    RawBlock,
    RawBoundingBox,
    RawPage,
    normalize_bbox,
    normalize_page,
)

_MAX_BLOCKS_PER_PAGE = 10_000
_MAX_POLYGON_POINTS = 128


class PaddleGeneralOutput(BaseModel):
    """Strict, text-only boundary schema produced by the isolated profile."""

    model_config = ConfigDict(extra="forbid")

    page_index: int = Field(ge=0)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    dt_polys: list[list[tuple[float, float]]] = Field(max_length=_MAX_BLOCKS_PER_PAGE)
    rec_polys: list[list[tuple[float, float]]] | None = Field(
        default=None,
        max_length=_MAX_BLOCKS_PER_PAGE,
    )
    rec_texts: list[str] = Field(max_length=_MAX_BLOCKS_PER_PAGE)
    rec_scores: list[float] = Field(max_length=_MAX_BLOCKS_PER_PAGE)

    @field_validator("dt_polys", "rec_polys")
    @classmethod
    def polygons_must_be_bounded(
        cls,
        value: list[list[tuple[float, float]]] | None,
    ) -> list[list[tuple[float, float]]] | None:
        if value is None:
            return None
        for polygon in value:
            if not 4 <= len(polygon) <= _MAX_POLYGON_POINTS:
                raise ValueError("OCR polygons must contain a bounded set of points")
            if any(
                not math.isfinite(coordinate) or coordinate < 0
                for point in polygon
                for coordinate in point
            ):
                raise ValueError("OCR polygon coordinates must be finite and non-negative")
        return value

    @field_validator("rec_texts")
    @classmethod
    def text_must_be_bounded(cls, value: list[str]) -> list[str]:
        if any(len(text) > 100_000 or "\x00" in text for text in value):
            raise ValueError("OCR text is invalid")
        return value

    @field_validator("rec_scores")
    @classmethod
    def scores_must_be_probabilities(cls, value: list[float]) -> list[float]:
        if any(not math.isfinite(score) or score < 0 or score > 1 for score in value):
            raise ValueError("OCR scores must be finite probabilities")
        return value

    @model_validator(mode="after")
    def aligned_arrays_must_have_equal_lengths(self) -> Self:
        expected = len(self.rec_texts)
        polygons = self.rec_polys if self.rec_polys is not None else self.dt_polys
        if len(polygons) != expected or len(self.rec_scores) != expected:
            raise ValueError("OCR polygons, texts, and scores must align")
        return self


def polygon_to_bbox(
    polygon: list[tuple[float, float]],
    *,
    page_width: int,
    page_height: int,
) -> BoundingBox:
    """Convert a Paddle polygon to a normalized, page-bounded rectangle."""

    if page_width <= 0 or page_height <= 0:
        raise ValueError("page dimensions must be positive")
    if not 4 <= len(polygon) <= _MAX_POLYGON_POINTS:
        raise ValueError("OCR polygons must contain a bounded set of points")
    coordinates = [coordinate for point in polygon for coordinate in point]
    if any(not math.isfinite(value) or value < 0 for value in coordinates):
        raise ValueError("OCR polygon coordinates must be finite and non-negative")
    x_values = [point[0] for point in polygon]
    y_values = [point[1] for point in polygon]
    raw = RawBoundingBox(
        x0=min(x_values),
        top=min(y_values),
        x1=max(x_values),
        bottom=max(y_values),
    )
    if raw.x1 > page_width or raw.bottom > page_height:
        raise ValueError("OCR polygon exceeds page dimensions")
    return normalize_bbox(raw, page_width=page_width, page_height=page_height)


def normalize_paddle_general_output(
    payload: PaddleGeneralOutput | Mapping[str, object],
    *,
    page_ordinal: int,
    raw_result_ref: str,
    source_version: str,
) -> Page:
    """Normalize one isolated General result without importing Paddle packages."""

    raw_page = paddle_general_raw_page(payload, page_ordinal=page_ordinal)
    return normalize_page(
        raw_page,
        raw_result_ref=raw_result_ref,
        quality=build_ocr_quality(raw_page.blocks, experimental=False),
        source_backend="paddleocr-general",
        source_version=source_version,
    )


def paddle_general_raw_page(
    payload: PaddleGeneralOutput | Mapping[str, object],
    *,
    page_ordinal: int,
) -> RawPage:
    """Convert a strict isolated result into the shared raw-page representation."""

    output = (
        payload
        if isinstance(payload, PaddleGeneralOutput)
        else PaddleGeneralOutput.model_validate(payload)
    )
    if output.page_index != page_ordinal - 1:
        raise ValueError("OCR page index does not match the requested page ordinal")
    polygons = output.rec_polys if output.rec_polys is not None else output.dt_polys
    raw_blocks: list[RawBlock] = []
    for polygon, text, score in zip(polygons, output.rec_texts, output.rec_scores, strict=True):
        normalized_text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized_text:
            continue
        normalized_bbox = polygon_to_bbox(
            polygon,
            page_width=output.image_width,
            page_height=output.image_height,
        )
        raw_blocks.append(
            RawBlock(
                type=BlockType.PARAGRAPH,
                text=normalized_text,
                bbox=RawBoundingBox(
                    x0=normalized_bbox.x * output.image_width,
                    top=normalized_bbox.y * output.image_height,
                    x1=(normalized_bbox.x + normalized_bbox.width) * output.image_width,
                    bottom=(normalized_bbox.y + normalized_bbox.height) * output.image_height,
                ),
                reading_order=len(raw_blocks),
                confidence=score,
                metadata={"ocr_backend": "paddle-general"},
            )
        )
    return RawPage(
        ordinal=page_ordinal,
        width=output.image_width,
        height=output.image_height,
        source_kind="page",
        native_text_present=False,
        blocks=raw_blocks,
    )


def build_ocr_quality(blocks: list[RawBlock], *, experimental: bool) -> PageQuality:
    text_char_count = sum(len(block.text) for block in blocks)
    if not blocks or text_char_count == 0:
        return PageQuality(
            status=PageQualityStatus.FAILED,
            text_layer="none",
            requires_ocr=True,
            text_char_count=0,
            block_count=len(blocks),
            issues=[
                PageIssue(
                    code="OCR_EMPTY_RESULT",
                    severity=PageIssueSeverity.ERROR,
                    retryable=True,
                    message="The isolated OCR process returned no usable text blocks.",
                )
            ],
        )
    return PageQuality(
        status=PageQualityStatus.WARNING,
        text_layer="ocr",
        requires_ocr=False,
        text_char_count=text_char_count,
        block_count=len(blocks),
        issues=[
            PageIssue(
                code="PP_STRUCTURE_EXPERIMENTAL" if experimental else "OCR_BENCHMARK_PENDING",
                severity=PageIssueSeverity.WARNING,
                retryable=False,
                message=(
                    "Experimental structure output requires benchmark review."
                    if experimental
                    else "OCR output has not passed a corpus-specific quality benchmark."
                ),
            )
        ],
    )
