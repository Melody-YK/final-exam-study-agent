"""Versioned document parsing and chunk contracts."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from study_contracts.canonical import canonical_sha256

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PARSE_PAGE_MEDIA_TYPE = "application/vnd.study-agent.parse-page+json"
PARSE_ATTEMPT_MEDIA_TYPE = "application/vnd.study-agent.parse-attempt+json"
PARSER_RAW_MEDIA_TYPE = "application/vnd.study-agent.parser-raw+json"


class ContractModel(BaseModel):
    """Base settings for data crossing a process boundary."""

    model_config = ConfigDict(extra="forbid")


class BoundingBox(ContractModel):
    """A page-relative rectangle with coordinates normalized to ``[0, 1]``."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(ge=0.0, le=1.0)
    height: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_page_bounds(self) -> Self:
        if self.x + self.width > 1.0:
            raise ValueError("x + width must not exceed the normalized page width")
        if self.y + self.height > 1.0:
            raise ValueError("y + height must not exceed the normalized page height")
        return self


class BlockType(StrEnum):
    TITLE = "title"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    FORMULA = "formula"
    IMAGE = "image"
    CODE = "code"


class PageQualityStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class PageIssueSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class PageIssue(ContractModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    severity: PageIssueSeverity
    retryable: bool = False
    message: str = Field(min_length=1, max_length=300)


class PageQuality(ContractModel):
    status: PageQualityStatus
    text_layer: Literal["native", "ocr", "mixed", "none", "unknown"]
    requires_ocr: bool = False
    text_char_count: int = Field(ge=0)
    block_count: int = Field(ge=0)
    issues: list[PageIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def status_must_match_issues(self) -> Self:
        has_error = any(issue.severity is PageIssueSeverity.ERROR for issue in self.issues)
        if self.status is PageQualityStatus.FAILED and not has_error:
            raise ValueError("failed page quality requires an error issue")
        if self.status is PageQualityStatus.PASSED and has_error:
            raise ValueError("passed page quality cannot contain an error issue")
        if self.status is PageQualityStatus.PASSED and self.requires_ocr:
            raise ValueError("passed page quality cannot require OCR")
        if self.text_layer == "none" and self.text_char_count != 0:
            raise ValueError("a page without a text layer cannot report text characters")
        return self


class AssetType(StrEnum):
    """Portable asset categories emitted by parser adapters."""

    IMAGE = "image"
    RENDERED_PAGE = "rendered_page"
    TABLE = "table"
    FORMULA = "formula"


class SourceLocator(ContractModel):
    kind: Literal["page", "slide", "section"]
    ordinal: int = Field(ge=1)


class Block(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    id: str = Field(min_length=1)
    type: BlockType
    text: str
    bbox_norm: BoundingBox
    reading_order: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    source_backend: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    raw_result_ref: NonEmptyString | None = None
    parent_id: NonEmptyString | None = None
    section_path: list[NonEmptyString] = Field(default_factory=list)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()


class Page(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    ordinal: int = Field(ge=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    source_kind: Literal["page", "slide", "section"] = "page"
    bbox_norm: BoundingBox = Field(
        default_factory=lambda: BoundingBox(x=0.0, y=0.0, width=1.0, height=1.0)
    )
    source_backend: NonEmptyString
    source_version: NonEmptyString
    raw_result_ref: NonEmptyString
    blocks: list[Block] = Field(default_factory=list)
    quality: PageQuality | None = None

    @model_validator(mode="after")
    def validate_blocks_and_quality(self) -> Self:
        block_ids = [block.id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("page block identifiers must be unique")
        reading_orders = [block.reading_order for block in self.blocks]
        if len(reading_orders) != len(set(reading_orders)):
            raise ValueError("page reading_order values must be unique")
        block_id_set = set(block_ids)
        if any(
            block.parent_id is not None and block.parent_id not in block_id_set
            for block in self.blocks
        ):
            raise ValueError("block parent_id must reference a block on the same page")
        if any(block.parent_id == block.id for block in self.blocks):
            raise ValueError("block cannot be its own parent")
        if any(
            block.source_backend != self.source_backend
            or block.source_version != self.source_version
            for block in self.blocks
        ):
            raise ValueError("block parser provenance must match its page")
        if self.quality is not None:
            if self.quality.block_count != len(self.blocks):
                raise ValueError("quality block_count must match page blocks")
            text_char_count = sum(len(block.text) for block in self.blocks)
            if self.quality.text_char_count != text_char_count:
                raise ValueError("quality text_char_count must match normalized block text")
        return self


class ParseResultManifest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    document_sha256: Sha256Hex
    parser_profile: str = Field(min_length=1)
    pages: list[Page] = Field(default_factory=list)


class ParseResultBundle(ContractModel):
    """Strict normalized parser result accepted by the API revision boundary."""

    schema_version: Literal["1.0"] = "1.0"
    document_sha256: Sha256Hex
    parser_profile: NonEmptyString
    source_backend: NonEmptyString
    source_version: NonEmptyString
    pages: list[Page] = Field(min_length=1)
    assets: list["Asset"] = Field(default_factory=list)
    canonical_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        page_ordinals = [page.ordinal for page in self.pages]
        if page_ordinals != list(range(1, len(self.pages) + 1)):
            raise ValueError("page ordinals must be contiguous and ordered from one")
        if any(page.quality is None for page in self.pages):
            raise ValueError("every bundle page requires quality metadata")
        page_provenance = {(page.source_backend, page.source_version) for page in self.pages}
        expected_bundle_provenance = (
            next(iter(page_provenance)) if len(page_provenance) == 1 else ("mixed", "mixed")
        )
        if (self.source_backend, self.source_version) != expected_bundle_provenance:
            raise ValueError("bundle parser provenance must summarize its pages")
        blocks = [block for page in self.pages for block in page.blocks]
        if any(
            block.source_backend != page.source_backend
            or block.source_version != page.source_version
            for page in self.pages
            for block in page.blocks
        ):
            raise ValueError("block parser provenance must match its page")
        if any(
            block.raw_result_ref != page.raw_result_ref
            for page in self.pages
            for block in page.blocks
        ):
            raise ValueError("bundle blocks must reference their page raw artifact")
        block_ids = [block.id for block in blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("block identifiers must be globally unique")
        if any(block.raw_result_ref is None for block in blocks):
            raise ValueError("every bundle block requires raw_result_ref")
        asset_ids = [asset.id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset identifiers must be unique")
        page_set = set(page_ordinals)
        if any(asset.locator.ordinal not in page_set for asset in self.assets):
            raise ValueError("asset locator must reference a bundle page")
        pages_by_ordinal = {page.ordinal: page for page in self.pages}
        if any(
            asset.source_backend != pages_by_ordinal[asset.locator.ordinal].source_backend
            or asset.source_version != pages_by_ordinal[asset.locator.ordinal].source_version
            or asset.raw_result_ref != pages_by_ordinal[asset.locator.ordinal].raw_result_ref
            or asset.locator.kind != pages_by_ordinal[asset.locator.ordinal].source_kind
            for asset in self.assets
        ):
            raise ValueError("asset provenance must match its located bundle page")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"canonical_sha256"}))
        if self.canonical_sha256 != expected:
            raise ValueError("canonical_sha256 does not match bundle content")
        return self


class Asset(ContractModel):
    """A stored visual or structural artifact with a stable source location."""

    schema_version: Literal["1.0"] = "1.0"
    id: NonEmptyString
    type: AssetType
    locator: SourceLocator
    bbox_norm: BoundingBox
    object_ref: NonEmptyString
    media_type: NonEmptyString
    sha256: Sha256Hex
    source_backend: NonEmptyString
    source_version: NonEmptyString
    raw_result_ref: NonEmptyString
    size_bytes: int | None = Field(default=None, ge=0)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        if "/" not in value or value.startswith("/") or value.endswith("/"):
            raise ValueError("media_type must use a type/subtype value")
        return value


class ParseAttemptResult(ContractModel):
    """Canonical coverage envelope produced by one parser job attempt.

    Unlike :class:`ParseResultBundle`, an attempt may cover an ordered subset of
    the document. The API combines independently verified page checkpoints from
    one or more attempts before it constructs a complete revision bundle.
    """

    schema_version: Literal["1.0"] = "1.0"
    document_sha256: Sha256Hex
    parser_profile: NonEmptyString
    source_backend: NonEmptyString
    source_version: NonEmptyString
    total_page_count: int = Field(gt=0)
    requested_page_ordinals: list[int] = Field(min_length=1)
    covered_page_ordinals: list[int] = Field(default_factory=list)
    pages: list[Page] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    canonical_sha256: Sha256Hex

    @field_validator("requested_page_ordinals", "covered_page_ordinals")
    @classmethod
    def ordinals_must_be_strictly_increasing(cls, values: list[int]) -> list[int]:
        if any(ordinal < 1 for ordinal in values):
            raise ValueError("page ordinals must be positive")
        if values != sorted(set(values)):
            raise ValueError("page ordinals must be ordered and unique")
        return values

    @model_validator(mode="after")
    def validate_attempt(self) -> Self:
        requested = set(self.requested_page_ordinals)
        covered = set(self.covered_page_ordinals)
        if any(ordinal > self.total_page_count for ordinal in requested | covered):
            raise ValueError("page ordinal exceeds total_page_count")
        if not covered <= requested:
            raise ValueError("covered pages must be a subset of requested pages")
        if [page.ordinal for page in self.pages] != self.covered_page_ordinals:
            raise ValueError("pages must exactly match covered_page_ordinals")
        if any(page.quality is None for page in self.pages):
            raise ValueError("every attempt page requires quality metadata")
        if any(
            page.source_backend != self.source_backend or page.source_version != self.source_version
            for page in self.pages
        ):
            raise ValueError("page parser provenance must match the attempt")
        blocks = [block for page in self.pages for block in page.blocks]
        if any(
            block.source_backend != self.source_backend
            or block.source_version != self.source_version
            or block.source_backend != page.source_backend
            or block.source_version != page.source_version
            for page in self.pages
            for block in page.blocks
        ):
            raise ValueError("block parser provenance must match its page and attempt")
        if any(
            block.raw_result_ref != page.raw_result_ref
            for page in self.pages
            for block in page.blocks
        ):
            raise ValueError("attempt blocks must reference their page raw artifact")
        block_ids = [block.id for block in blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("block identifiers must be globally unique")
        if any(block.raw_result_ref is None for block in blocks):
            raise ValueError("every attempt block requires raw_result_ref")
        asset_ids = [asset.id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset identifiers must be unique")
        if any(asset.locator.ordinal not in covered for asset in self.assets):
            raise ValueError("asset locator must reference a covered page")
        pages_by_ordinal = {page.ordinal: page for page in self.pages}
        if any(
            asset.source_backend != self.source_backend
            or asset.source_version != self.source_version
            or asset.raw_result_ref != pages_by_ordinal[asset.locator.ordinal].raw_result_ref
            or asset.locator.kind != pages_by_ordinal[asset.locator.ordinal].source_kind
            for asset in self.assets
        ):
            raise ValueError("asset provenance must match its located attempt page")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"canonical_sha256"}))
        if self.canonical_sha256 != expected:
            raise ValueError("canonical_sha256 does not match attempt content")
        return self


class Chunk(ContractModel):
    """An immutable retrieval unit linked back to normalized source blocks."""

    schema_version: Literal["1.0"] = "1.0"
    id: NonEmptyString
    revision_id: NonEmptyString
    text: NonEmptyString
    locator: SourceLocator
    section_path: list[NonEmptyString] = Field(default_factory=list)
    source_block_ids: list[NonEmptyString] = Field(min_length=1)
    token_count_estimate: int = Field(gt=0)
    content_sha256: Sha256Hex
    ordinal: int = Field(default=1, ge=1)
    chunker_version: NonEmptyString = "section-page-v1"

    @model_validator(mode="after")
    def validate_unique_source_blocks(self) -> Self:
        if len(self.source_block_ids) != len(set(self.source_block_ids)):
            raise ValueError("source_block_ids must be unique")
        return self
