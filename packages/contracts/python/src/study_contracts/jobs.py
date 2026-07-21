"""Versioned contracts for persistent parse jobs and the pull worker."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from study_contracts.documents import ContractModel, NonEmptyString, Sha256Hex


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    PARSING = "parsing"
    RESULT_SUBMITTED = "result_submitted"
    VALIDATING = "validating"
    INDEXING = "indexing"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkerCapabilities(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    parser_profiles: list[NonEmptyString] = Field(default_factory=list)
    media_types: list[NonEmptyString] = Field(default_factory=list)
    supports_ocr: bool = False
    supports_rendering: bool = False
    max_input_bytes: int = Field(gt=0)
    max_pages: int = Field(gt=0)

    @field_validator("parser_profiles", "media_types")
    @classmethod
    def values_must_be_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("capability values must be unique")
        return values

    @field_validator("media_types")
    @classmethod
    def media_types_must_be_valid(cls, values: list[str]) -> list[str]:
        if any(
            "/" not in value or value.startswith("/") or value.endswith("/") for value in values
        ):
            raise ValueError("media_types must use type/subtype values")
        return values


class JobClaimRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    worker_id: NonEmptyString
    capabilities: WorkerCapabilities


class WorkerLease(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    job_id: NonEmptyString
    job_type: Literal["parse"] = "parse"
    course_id: NonEmptyString
    document_id: NonEmptyString
    document_sha256: Sha256Hex
    deletion_epoch: int = Field(ge=0)
    media_type: NonEmptyString
    parser_profile: NonEmptyString
    parser_schema_version: Literal["1.0"] = "1.0"
    attempt: int = Field(ge=1)
    lease_version: int = Field(ge=1)
    lease_token: str = Field(min_length=24, max_length=512)
    lease_expires_at: datetime
    input_url: NonEmptyString
    artifact_upload_url: NonEmptyString
    requested_pages: list[int] = Field(default_factory=list)

    @field_validator("requested_pages")
    @classmethod
    def requested_pages_must_be_unique_and_positive(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values) or len(values) != len(set(values)):
            raise ValueError("requested_pages must contain unique positive ordinals")
        return values


class JobClaimResponse(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    lease: WorkerLease | None = None
    retry_after_ms: int = Field(default=1_000, ge=0)


class LeaseCommand(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    worker_id: NonEmptyString
    lease_token: str = Field(min_length=24, max_length=512)
    lease_version: int = Field(ge=1)
    attempt: int = Field(ge=1)
    deletion_epoch: int = Field(ge=0)


class JobStartRequest(LeaseCommand):
    pass


class JobProgress(ContractModel):
    phase: NonEmptyString
    completed_pages: int = Field(default=0, ge=0)
    total_pages: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def completed_pages_must_not_exceed_total(self) -> Self:
        if self.total_pages is not None and self.completed_pages > self.total_pages:
            raise ValueError("completed_pages must not exceed total_pages")
        return self


class JobHeartbeatRequest(LeaseCommand):
    progress: JobProgress


class PageCheckpointRequest(LeaseCommand):
    page_ordinal: int = Field(ge=1)
    status: Literal["succeeded", "failed"]
    output_ref: NonEmptyString
    output_sha256: Sha256Hex
    output_size_bytes: int = Field(ge=0)
    output_schema_version: Literal["1.0"] = "1.0"
    source_backend: NonEmptyString
    source_version: NonEmptyString
    error_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")

    @model_validator(mode="after")
    def failure_requires_error_code(self) -> Self:
        if self.status == "failed" and self.error_code is None:
            raise ValueError("failed checkpoints require error_code")
        if self.status == "succeeded" and self.error_code is not None:
            raise ValueError("successful checkpoints cannot include error_code")
        return self


class JobCompleteRequest(LeaseCommand):
    result_manifest_ref: NonEmptyString
    result_sha256: Sha256Hex
    result_size_bytes: int = Field(ge=0)
    manifest_schema_version: Literal["1.0"] = "1.0"
    page_count: int = Field(ge=0)
    failed_pages: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def failed_pages_must_be_unique_and_in_range(self) -> Self:
        if len(self.failed_pages) != len(set(self.failed_pages)):
            raise ValueError("failed_pages must be unique")
        if any(page < 1 or page > self.page_count for page in self.failed_pages):
            raise ValueError("failed_pages must be within page_count")
        return self


class JobFailRequest(LeaseCommand):
    error_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    retryable: bool
    error_summary: str | None = Field(default=None, max_length=500)


class JobArtifactReceipt(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    artifact_ref: NonEmptyString
    artifact_name: NonEmptyString
    size_bytes: int = Field(ge=0)
    sha256: Sha256Hex
    media_type: NonEmptyString
    artifact_schema_version: Literal["1.0"] = "1.0"


class JobSnapshot(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    id: NonEmptyString
    document_id: NonEmptyString
    course_id: NonEmptyString
    status: JobStatus
    state_version: int = Field(ge=1)
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    lease_version: int = Field(ge=0)
    lease_expires_at: datetime | None = None
    parser_profile: NonEmptyString
    parser_schema_version: Literal["1.0"] = "1.0"
    progress: dict[str, Any] = Field(default_factory=dict)
    failure_code: str | None = None
    retryable: bool | None = None
    available_at: datetime
    created_at: datetime
    updated_at: datetime


class JobEventEnvelope(ContractModel):
    stream_version: Literal["1"] = "1"
    sequence: int = Field(ge=1)
    occurred_at: datetime
    trace_id: NonEmptyString
    event_type: NonEmptyString
    data: dict[str, Any] = Field(default_factory=dict)
