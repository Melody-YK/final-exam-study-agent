"""Pure normalization for PP-StructureV3 JSON emitted by the isolated profile."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from study_contracts import BlockType, Page
from study_worker.parsers.normalize import RawBlock, RawBoundingBox, RawPage, normalize_page
from study_worker.parsers.paddle_general import build_ocr_quality

_MAX_BLOCKS_PER_PAGE = 10_000


class PPStructureBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_label: str = Field(min_length=1, max_length=100)
    block_content: str = Field(default="", max_length=200_000)
    block_bbox: tuple[float, float, float, float]
    block_order: int | None = Field(default=None, ge=0)
    score: float = Field(
        default=0.0,
        ge=0,
        le=1,
        validation_alias=AliasChoices("score", "block_score"),
    )

    @field_validator("block_content")
    @classmethod
    def content_must_not_contain_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("structure block content is invalid")
        return value

    @field_validator("block_bbox")
    @classmethod
    def bbox_must_be_ordered(
        cls,
        value: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        x0, top, x1, bottom = value
        if not all(math.isfinite(coordinate) and coordinate >= 0 for coordinate in value):
            raise ValueError("structure bbox coordinates must be finite and non-negative")
        if x1 < x0 or bottom < top:
            raise ValueError("structure bbox coordinates must be ordered")
        return value


class PPStructureOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_index: int = Field(ge=0)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    parsing_res_list: list[PPStructureBlock] = Field(max_length=_MAX_BLOCKS_PER_PAGE)

    @model_validator(mode="after")
    def explicit_orders_must_be_unique(self) -> Self:
        orders = [
            block.block_order for block in self.parsing_res_list if block.block_order is not None
        ]
        if len(orders) != len(set(orders)):
            raise ValueError("structure block orders must be unique")
        return self


def normalize_pp_structure_output(
    payload: PPStructureOutput | Mapping[str, object],
    *,
    page_ordinal: int,
    raw_result_ref: str,
    source_version: str,
) -> Page:
    """Normalize one experimental PP-StructureV3 page without vendor imports."""

    raw_page = pp_structure_raw_page(payload, page_ordinal=page_ordinal)
    return normalize_page(
        raw_page,
        raw_result_ref=raw_result_ref,
        quality=build_ocr_quality(raw_page.blocks, experimental=True),
        source_backend="pp-structure-v3",
        source_version=source_version,
    )


def pp_structure_raw_page(
    payload: PPStructureOutput | Mapping[str, object],
    *,
    page_ordinal: int,
) -> RawPage:
    """Convert a strict experimental structure result into a shared raw page."""

    output = (
        payload
        if isinstance(payload, PPStructureOutput)
        else PPStructureOutput.model_validate(payload)
    )
    if output.page_index != page_ordinal - 1:
        raise ValueError("structure page index does not match the requested page ordinal")
    ordered = sorted(
        enumerate(output.parsing_res_list),
        key=lambda item: (
            item[1].block_order if item[1].block_order is not None else item[0],
            item[0],
        ),
    )
    raw_blocks: list[RawBlock] = []
    for _, block in ordered:
        x0, top, x1, bottom = block.block_bbox
        if x1 > output.image_width or bottom > output.image_height:
            raise ValueError("structure bbox exceeds page dimensions")
        raw_blocks.append(
            RawBlock(
                type=_block_type(block.block_label),
                text=block.block_content,
                bbox=RawBoundingBox(x0=x0, top=top, x1=x1, bottom=bottom),
                reading_order=len(raw_blocks),
                confidence=block.score,
                metadata={
                    "ocr_backend": "pp-structure-v3",
                    "pp_structure_label": block.block_label.casefold(),
                },
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


def _block_type(label: str) -> BlockType:
    normalized = label.casefold().replace("-", "_").replace(" ", "_")
    if normalized in {"doc_title", "paragraph_title", "section_title", "title"}:
        return BlockType.TITLE
    if normalized in {"table", "table_body", "table_caption"}:
        return BlockType.TABLE
    if normalized in {"formula", "equation", "display_formula", "inline_formula"}:
        return BlockType.FORMULA
    if normalized in {"image", "figure", "chart", "seal"}:
        return BlockType.IMAGE
    if normalized in {"code", "algorithm"}:
        return BlockType.CODE
    return BlockType.PARAGRAPH
