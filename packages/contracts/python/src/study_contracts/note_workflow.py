"""Versioned contracts for asynchronous note generation and export."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from study_contracts.documents import ContractModel, NonEmptyString, Sha256Hex


class NoteBatchMode(StrEnum):
    MERGED = "merged"
    PER_DOCUMENT = "per_document"


class NoteBatchCommandKind(StrEnum):
    CREATE = "create"
    RETRY_FAILED = "retry_failed"
    RETRY_GAPS = "retry_gaps"
    REGENERATION = "regeneration"


class NoteBatchStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PARTIAL_SUCCESS = "partial_success"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class NoteItemStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class NoteGenerationPhase(StrEnum):
    VALIDATING_INPUTS = "validating_inputs"
    SEGMENTING = "segmenting"
    RETRIEVING = "retrieving"
    OUTLINING = "outlining"
    GENERATING = "generating"
    VALIDATING_OUTPUT = "validating_output"
    SAVING = "saving"


class EtaConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EtaUnavailableReason(StrEnum):
    NOT_STARTED = "not_started"
    TERMINAL = "terminal"
    INSUFFICIENT_HISTORY = "insufficient_history"
    RETRYING = "retrying"
    PROVIDER_CHANGED = "provider_changed"
    OUTLIER = "outlier"


class CoverageUnitType(StrEnum):
    SLIDE = "slide"
    PDF_SECTION = "pdf_section"
    PDF_PAGE_WINDOW = "pdf_page_window"


class CoverageUnitStatus(StrEnum):
    PENDING = "pending"
    COVERED = "covered"
    SKIPPED = "skipped"
    FAILED = "failed"


class NoteCoverageStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN_LEGACY = "unknown_legacy"


class NoteCoverageBasis(StrEnum):
    GENERATED = "generated"
    USER_EDITED_FROM_GENERATED_VERSION = "user_edited_from_generated_version"
    LEGACY_BACKFILL = "legacy_backfill"


class NoteAstNodeType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TEXT = "text"
    EMPHASIS = "emphasis"
    STRONG = "strong"
    LIST = "list"
    LIST_ITEM = "list_item"
    BLOCKQUOTE = "blockquote"
    CODE_BLOCK = "code_block"
    INLINE_CODE = "inline_code"
    TABLE = "table"
    TABLE_ROW = "table_row"
    TABLE_CELL = "table_cell"
    THEMATIC_BREAK = "thematic_break"
    CITATION = "citation"


class NoteAstProvenance(StrEnum):
    SOURCE_BACKED = "source_backed"
    USER_AUTHORED_UNVERIFIED = "user_authored_unverified"
    SYSTEM_GENERATED = "system_generated"


class NoteExportFormat(StrEnum):
    DOCX = "docx"


class NoteExportStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RENDERING = "rendering"
    VALIDATING = "validating"
    STORING = "storing"
    RETRY_WAIT = "retry_wait"
    AVAILABLE = "available"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REVOKED = "revoked"


class _CreateNoteBatchRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    document_ids: list[NonEmptyString] = Field(min_length=1)
    section_path: list[NonEmptyString] | None = Field(default=None, max_length=32)

    @field_validator("document_ids")
    @classmethod
    def document_ids_must_be_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("document_ids must be unique")
        return values


class MergedNoteBatchRequest(_CreateNoteBatchRequest):
    mode: Literal[NoteBatchMode.MERGED] = NoteBatchMode.MERGED
    title: str | None = Field(default=None, max_length=255)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        return _normalize_optional_text(value)


class PerDocumentNoteBatchRequest(_CreateNoteBatchRequest):
    mode: Literal[NoteBatchMode.PER_DOCUMENT] = NoteBatchMode.PER_DOCUMENT
    title_prefix: str | None = Field(default=None, max_length=255)

    @field_validator("title_prefix", mode="before")
    @classmethod
    def normalize_title_prefix(cls, value: object) -> object:
        return _normalize_optional_text(value)


CreateNoteBatchRequest = Annotated[
    MergedNoteBatchRequest | PerDocumentNoteBatchRequest,
    Field(discriminator="mode"),
]


class EtaRange(ContractModel):
    lower_seconds: int = Field(ge=0)
    upper_seconds: int = Field(ge=0)
    confidence: EtaConfidence
    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def as_of_must_include_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        return value

    @model_validator(mode="after")
    def lower_must_not_exceed_upper(self) -> Self:
        if self.lower_seconds > self.upper_seconds:
            raise ValueError("lower_seconds must not exceed upper_seconds")
        return self


class NoteInputSnapshot(ContractModel):
    id: NonEmptyString
    ordinal: int = Field(ge=1)
    document_id: NonEmptyString
    revision_id: NonEmptyString
    deletion_epoch: int = Field(ge=0)
    document_name: NonEmptyString
    media_type: NonEmptyString
    content_sha256: Sha256Hex
    index_manifest_at_submit: NonEmptyString


class CoverageUnitSnapshot(ContractModel):
    id: NonEmptyString
    input_id: NonEmptyString
    ordinal: int = Field(ge=1)
    unit_type: CoverageUnitType
    locator: NonEmptyString
    status: CoverageUnitStatus
    reason_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")

    @model_validator(mode="after")
    def reason_must_match_status(self) -> Self:
        if self.status in {CoverageUnitStatus.SKIPPED, CoverageUnitStatus.FAILED}:
            if self.reason_code is None:
                raise ValueError("skipped and failed coverage units require reason_code")
        elif self.reason_code is not None:
            raise ValueError("pending and covered coverage units cannot include reason_code")
        return self


class NoteItemSnapshot(ContractModel):
    id: NonEmptyString
    input_ids: list[NonEmptyString]
    status: NoteItemStatus
    phase: NoteGenerationPhase | None = None
    elapsed_seconds: int = Field(ge=0)
    eta: EtaRange | None = None
    eta_unavailable_reason: EtaUnavailableReason | None = None
    attempt: int = Field(ge=0)
    note_id: NonEmptyString | None = None
    failure_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    retryable_in_new_batch: bool

    @field_validator("input_ids")
    @classmethod
    def input_ids_must_be_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("item input_ids must be unique")
        return values

    @model_validator(mode="after")
    def eta_must_have_exactly_one_representation(self) -> Self:
        if self.eta is None and self.eta_unavailable_reason is None:
            raise ValueError("an unavailable eta requires eta_unavailable_reason")
        if self.eta is not None and self.eta_unavailable_reason is not None:
            raise ValueError("eta and eta_unavailable_reason are mutually exclusive")
        terminal = {
            NoteItemStatus.SUCCEEDED,
            NoteItemStatus.FAILED,
            NoteItemStatus.CANCELLED,
        }
        if self.status in terminal and (
            self.eta is not None or self.eta_unavailable_reason is not EtaUnavailableReason.TERMINAL
        ):
            raise ValueError("terminal items require the terminal eta unavailable reason")
        if (
            self.status not in terminal
            and self.eta_unavailable_reason is EtaUnavailableReason.TERMINAL
        ):
            raise ValueError("non-terminal items cannot use the terminal eta unavailable reason")
        return self


class NoteBatchSnapshot(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    id: NonEmptyString
    command_kind: NoteBatchCommandKind = NoteBatchCommandKind.CREATE
    retry_of_batch_id: NonEmptyString | None = None
    course_id: NonEmptyString
    mode: NoteBatchMode
    title: str | None = None
    title_prefix: str | None = None
    section_path: list[NonEmptyString] = Field(default_factory=lambda: ["未分类"])
    target_note_id: NonEmptyString | None = None
    target_note_version: int | None = Field(default=None, ge=1)
    target_note_version_sha256: Sha256Hex | None = None
    status: NoteBatchStatus
    completed_items: int = Field(ge=0)
    total_items: int = Field(ge=1)
    inputs: list[NoteInputSnapshot]
    coverage_units: list[CoverageUnitSnapshot] = Field(default_factory=list)
    items: list[NoteItemSnapshot]
    last_event_sequence: int = Field(ge=0)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @field_validator("created_at", "started_at", "completed_at")
    @classmethod
    def timestamps_must_include_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("batch timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def counts_and_terminal_timestamp_must_match(self) -> Self:
        if self.completed_items > self.total_items:
            raise ValueError("completed_items must not exceed total_items")
        terminal = {
            NoteBatchStatus.PARTIAL_SUCCESS,
            NoteBatchStatus.SUCCEEDED,
            NoteBatchStatus.FAILED,
            NoteBatchStatus.CANCELLED,
        }
        if self.status in terminal and self.completed_at is None:
            raise ValueError("terminal batches require completed_at")
        if self.status not in terminal and self.completed_at is not None:
            raise ValueError("non-terminal batches cannot include completed_at")
        if self.mode is NoteBatchMode.MERGED and self.title_prefix is not None:
            raise ValueError("merged batches cannot include title_prefix")
        if self.mode is NoteBatchMode.PER_DOCUMENT and self.title is not None:
            raise ValueError("per-document batches cannot include title")
        target_values = (
            self.target_note_id,
            self.target_note_version,
            self.target_note_version_sha256,
        )
        if self.command_kind is NoteBatchCommandKind.REGENERATION:
            if self.mode is not NoteBatchMode.MERGED or any(
                value is None for value in target_values
            ):
                raise ValueError("regeneration batches require an exact merged Note target")
        elif any(value is not None for value in target_values):
            raise ValueError("only regeneration batches can include a Note target")
        if self.command_kind in {
            NoteBatchCommandKind.RETRY_FAILED,
            NoteBatchCommandKind.RETRY_GAPS,
        }:
            if self.retry_of_batch_id is None:
                raise ValueError("retry batches require retry_of_batch_id")
        elif self.retry_of_batch_id is not None:
            raise ValueError("create and regeneration batches cannot include retry_of_batch_id")
        return self


class NoteDraftClaim(ContractModel):
    id: NonEmptyString
    text: NonEmptyString
    citation_ids: list[NonEmptyString] = Field(min_length=1)

    @field_validator("citation_ids")
    @classmethod
    def citation_ids_must_be_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("claim citation_ids must be unique")
        return values


class NoteDraftCitation(ContractModel):
    id: NonEmptyString
    evidence_id: NonEmptyString
    coverage_unit_ids: list[NonEmptyString] = Field(min_length=1)

    @field_validator("coverage_unit_ids")
    @classmethod
    def coverage_unit_ids_must_be_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("citation coverage_unit_ids must be unique")
        return values


class NoteAstNode(ContractModel):
    id: NonEmptyString
    type: NoteAstNodeType
    text: str | None = None
    children: list[NoteAstNode] = Field(default_factory=list)
    citation_id: NonEmptyString | None = None
    level: int | None = Field(default=None, ge=1, le=6)
    ordered: bool | None = None
    language: NonEmptyString | None = None
    provenance: NoteAstProvenance = NoteAstProvenance.SOURCE_BACKED

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.replace("\r\n", "\n").replace("\r", "\n")

    @model_validator(mode="after")
    def required_attributes_must_match_type(self) -> Self:
        if self.type is NoteAstNodeType.HEADING and self.level is None:
            raise ValueError("heading nodes require level")
        if self.type is NoteAstNodeType.CITATION and self.citation_id is None:
            raise ValueError("citation nodes require citation_id")
        return self


class NoteContentAstV1(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    nodes: list[NoteAstNode]

    @model_validator(mode="after")
    def node_ids_must_be_unique(self) -> Self:
        seen: set[str] = set()
        stack = list(self.nodes)
        while stack:
            node = stack.pop()
            if node.id in seen:
                raise ValueError("AST node identifiers must be unique")
            seen.add(node.id)
            stack.extend(node.children)
        return self


class StructuredNoteDraftV1(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    title: NonEmptyString
    body_markdown: str = Field(min_length=1, max_length=1_000_000)
    claims: list[NoteDraftClaim] = Field(min_length=1)
    citations: list[NoteDraftCitation] = Field(min_length=1)
    coverage_unit_refs: list[NonEmptyString] = Field(min_length=1)
    content_ast: NoteContentAstV1

    @field_validator("body_markdown")
    @classmethod
    def normalize_body_markdown(cls, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise ValueError("body_markdown must not be blank")
        return normalized

    @model_validator(mode="after")
    def references_must_be_closed_and_unique(self) -> Self:
        claim_ids = [claim.id for claim in self.claims]
        citation_ids = [citation.id for citation in self.citations]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim identifiers must be unique")
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("citation identifiers must be unique")
        if len(self.coverage_unit_refs) != len(set(self.coverage_unit_refs)):
            raise ValueError("coverage_unit_refs must be unique")

        known_citations = set(citation_ids)
        referenced_citations = {item for claim in self.claims for item in claim.citation_ids}
        if referenced_citations - known_citations:
            raise ValueError("claims reference unknown citations")
        if known_citations - referenced_citations:
            raise ValueError("draft citations must be referenced by at least one claim")

        ast_citations: set[str] = set()
        ast_stack = list(self.content_ast.nodes)
        while ast_stack:
            node = ast_stack.pop()
            if node.type is NoteAstNodeType.CITATION and node.citation_id is not None:
                ast_citations.add(node.citation_id)
            ast_stack.extend(node.children)
        if ast_citations - known_citations:
            raise ValueError("AST nodes reference unknown citations")

        known_units = set(self.coverage_unit_refs)
        referenced_units = {
            unit_id for citation in self.citations for unit_id in citation.coverage_unit_ids
        }
        if referenced_units - known_units:
            raise ValueError("citations reference unknown coverage units")
        return self


class NoteVersionCoverage(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    policy_version: NonEmptyString
    status: NoteCoverageStatus
    basis: NoteCoverageBasis
    generated_from_version: int | None = Field(default=None, ge=1)
    manifest_sha256: Sha256Hex
    units: list[CoverageUnitSnapshot]

    @model_validator(mode="after")
    def version_and_units_must_match_basis(self) -> Self:
        unit_ids = [unit.id for unit in self.units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("coverage unit identifiers must be unique")
        if (
            self.basis is NoteCoverageBasis.USER_EDITED_FROM_GENERATED_VERSION
            and self.generated_from_version is None
        ):
            raise ValueError("edited coverage requires generated_from_version")
        if (
            self.basis is not NoteCoverageBasis.USER_EDITED_FROM_GENERATED_VERSION
            and self.generated_from_version is not None
        ):
            raise ValueError("generated_from_version is only valid for edited coverage")
        return self


class NoteExportSnapshot(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    id: NonEmptyString
    note_id: NonEmptyString
    note_version: int = Field(ge=1)
    format: NoteExportFormat = NoteExportFormat.DOCX
    status: NoteExportStatus
    version_preview_path: NonEmptyString
    filename: NonEmptyString | None = None
    media_type: (
        Literal["application/vnd.openxmlformats-officedocument.wordprocessingml.document"] | None
    ) = None
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: Sha256Hex | None = None
    expires_at: datetime | None = None
    failure_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")

    @field_validator("expires_at")
    @classmethod
    def expires_at_must_include_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("expires_at must include a timezone")
        return value


def _normalize_optional_text(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    return normalized or None
