"""Strict raw parser models and normalization helpers."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from study_contracts import Block, BlockType, BoundingBox, Page, PageQuality
from study_contracts.documents import Sha256Hex

MetadataValue = str | int | float | bool | None


class RawModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RawBoundingBox(RawModel):
    x0: float = Field(ge=0)
    top: float = Field(ge=0)
    x1: float = Field(ge=0)
    bottom: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
        values = (self.x0, self.top, self.x1, self.bottom)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("raw bounding box values must be finite")
        if self.x1 < self.x0 or self.bottom < self.top:
            raise ValueError("raw bounding box coordinates must be ordered")
        return self


class RawArtifact(RawModel):
    relative_path: str = Field(min_length=1)
    media_type: str = Field(min_length=3)
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)

    @field_validator("relative_path")
    @classmethod
    def relative_path_must_be_normalized(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or "\\" in value
            or "\x00" in value
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("raw artifact path must be normalized and relative")
        return value

    @field_validator("media_type")
    @classmethod
    def media_type_must_be_valid(cls, value: str) -> str:
        if "/" not in value or value.startswith("/") or value.endswith("/"):
            raise ValueError("media_type must use type/subtype syntax")
        return value


class RawBlock(RawModel):
    type: BlockType
    text: str = ""
    bbox: RawBoundingBox
    reading_order: int = Field(ge=0)
    confidence: float = Field(default=1.0, ge=0, le=1)
    section_path: list[str] = Field(default_factory=list)
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)
    artifact: RawArtifact | None = None

    @field_validator("text")
    @classmethod
    def normalize_newlines(cls, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()


class RawPage(RawModel):
    ordinal: int = Field(ge=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    source_kind: Literal["page", "slide", "section"]
    native_text_present: bool
    blocks: list[RawBlock] = Field(default_factory=list)
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_blocks(self) -> Self:
        orders = [block.reading_order for block in self.blocks]
        if len(orders) != len(set(orders)):
            raise ValueError("raw page reading orders must be unique")
        if any(
            block.bbox.x1 > self.width or block.bbox.bottom > self.height for block in self.blocks
        ):
            raise ValueError("raw block bounding box exceeds page dimensions")
        return self


class RawDocument(RawModel):
    schema_version: Literal["1.0"] = "1.0"
    document_sha256: Sha256Hex
    parser_profile: Literal["native-v1", "ocr-v1", "mineru-v1"] = "native-v1"
    source_backend: Literal[
        "markdown-native",
        "pdf-native",
        "pptx-native",
        "paddleocr-general",
        "pp-structure-v3",
        "docling-standard",
        "docling-vlm",
        "mineru-pipeline",
    ]
    source_version: str = Field(min_length=1)
    total_page_count: int = Field(gt=0)
    pages: list[RawPage] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_page_coverage(self) -> Self:
        ordinals = [page.ordinal for page in self.pages]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("raw document page ordinals must be unique")
        if any(ordinal > self.total_page_count for ordinal in ordinals):
            raise ValueError("raw page ordinal exceeds total page count")
        return self


def normalize_page(
    raw_page: RawPage,
    *,
    raw_result_ref: str,
    quality: PageQuality,
    source_backend: str,
    source_version: str,
    asset_ids_by_order: Mapping[int, str] | None = None,
) -> Page:
    """Convert absolute top-left coordinates into the shared normalized Page contract."""

    normalized_blocks: list[Block] = []
    current_title_id: str | None = None
    current_section: list[str] = []
    linked_assets = asset_ids_by_order or {}
    for raw_block in sorted(raw_page.blocks, key=lambda item: item.reading_order):
        block_id = f"{raw_page.source_kind}-{raw_page.ordinal}-block-{raw_block.reading_order}"
        if raw_block.type is BlockType.TITLE and raw_block.text:
            current_title_id = block_id
            current_section = list(raw_block.section_path) or [raw_block.text]
        elif raw_block.section_path:
            current_section = list(raw_block.section_path)
        parent_id = None if raw_block.type is BlockType.TITLE else current_title_id
        normalized_blocks.append(
            Block(
                id=block_id,
                type=raw_block.type,
                text=raw_block.text,
                bbox_norm=normalize_bbox(
                    raw_block.bbox,
                    page_width=raw_page.width,
                    page_height=raw_page.height,
                ),
                reading_order=raw_block.reading_order,
                confidence=raw_block.confidence,
                source_backend=source_backend,
                source_version=source_version,
                raw_result_ref=raw_result_ref,
                parent_id=parent_id,
                section_path=list(current_section),
                metadata={
                    **raw_block.metadata,
                    **(
                        {"asset_id": linked_assets[raw_block.reading_order]}
                        if raw_block.reading_order in linked_assets
                        else {}
                    ),
                },
            )
        )
    return Page(
        ordinal=raw_page.ordinal,
        width=raw_page.width,
        height=raw_page.height,
        source_kind=raw_page.source_kind,
        bbox_norm=BoundingBox(x=0, y=0, width=1, height=1),
        source_backend=source_backend,
        source_version=source_version,
        raw_result_ref=raw_result_ref,
        blocks=normalized_blocks,
        quality=quality,
    )


def normalize_bbox(
    bbox: RawBoundingBox,
    *,
    page_width: int,
    page_height: int,
) -> BoundingBox:
    if page_width <= 0 or page_height <= 0:
        raise ValueError("page dimensions must be positive")
    x = _bounded(bbox.x0 / page_width)
    y = _bounded(bbox.top / page_height)
    right = _bounded(bbox.x1 / page_width)
    bottom = _bounded(bbox.bottom / page_height)
    return BoundingBox(
        x=x,
        y=y,
        width=max(0.0, round(right - x, 10)),
        height=max(0.0, round(bottom - y, 10)),
    )


def _bounded(value: float) -> float:
    return min(1.0, max(0.0, round(value, 10)))
