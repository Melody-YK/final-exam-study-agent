"""Versioned OCR evaluation manifest and human-gold contracts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EvalContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoldBox(EvalContract):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(ge=0, le=1)
    height: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def box_must_fit_page(self) -> Self:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("gold box must fit within normalized page bounds")
        return self


class OcrGoldBlock(EvalContract):
    id: str = Field(min_length=1)
    text: str
    reading_order: int = Field(ge=0)
    bbox_norm: GoldBox
    kind: Literal["text", "title", "table", "formula", "image"] = "text"


class OcrGoldPage(EvalContract):
    schema_version: Literal["1.0"] = "1.0"
    page_ordinal: int = Field(ge=1)
    text: str
    blocks: list[OcrGoldBlock] = Field(default_factory=list)
    table_cells: list[list[str]] = Field(default_factory=list)
    formulas: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def block_ids_and_orders_must_be_unique(self) -> Self:
        ids = [block.id for block in self.blocks]
        orders = [block.reading_order for block in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("gold block ids must be unique")
        if len(orders) != len(set(orders)):
            raise ValueError("gold reading orders must be unique")
        return self


class OcrEvalEntry(EvalContract):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    source_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: Literal["application/pdf", "image/jpeg", "image/png", "image/tiff"]
    split: Literal["development", "validation", "test"]
    purpose: Literal["ocr-evaluation"] = "ocr-evaluation"
    privacy: Literal["private-authorized", "public"]
    license_status: Literal["private-use-only", "self-authored", "open-license"]
    gold_path: str | None = None

    @field_validator("source_path", "gold_path")
    @classmethod
    def paths_must_be_absolute_when_present(cls, value: str | None) -> str | None:
        if value is not None and not Path(value).is_absolute():
            raise ValueError("evaluation paths must be absolute")
        return value

    @model_validator(mode="after")
    def private_entries_cannot_claim_public_license(self) -> Self:
        if self.privacy == "private-authorized" and self.license_status != "private-use-only":
            raise ValueError("private entries must use private-use-only license status")
        return self


class OcrEvalManifest(EvalContract):
    schema_version: Literal["1.0"] = "1.0"
    generated_at: datetime
    entries: list[OcrEvalEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def entry_ids_and_hashes_must_be_unique(self) -> Self:
        ids = [entry.id for entry in self.entries]
        hashes = [entry.sha256 for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation entry ids must be unique")
        if len(hashes) != len(set(hashes)):
            raise ValueError("duplicate evaluation content is not allowed")
        return self
