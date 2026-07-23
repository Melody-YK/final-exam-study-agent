"""Persistent note-generation batches, attempts, and events.

These models contain only control-plane facts.  Provider credentials, raw
provider responses, and mutable parser rows are intentionally absent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from study_agent.infrastructure.db.base import Base
from study_agent.infrastructure.db.models.core import new_id

_HASH_CHECK = "~ '^[0-9a-f]{64}$'"


class NoteGenerationBatchModel(Base):
    """A durable merge or per-document generation command."""

    __tablename__ = "note_generation_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_note_generation_batches_course_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["retry_of_batch_id", "course_id", "user_id"],
            [
                "note_generation_batches.id",
                "note_generation_batches.course_id",
                "note_generation_batches.user_id",
            ],
            name="fk_note_generation_batches_retry_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_note_id", "target_note_version", "course_id", "user_id"],
            [
                "note_content_versions.note_id",
                "note_content_versions.version",
                "note_content_versions.course_id",
                "note_content_versions.user_id",
            ],
            name="fk_note_generation_batches_target_version_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "course_id", "user_id", name="uq_note_generation_batches_id_scope"),
        CheckConstraint(
            "command_kind IN ('create', 'retry_failed', 'retry_gaps', 'regeneration')",
            name="ck_note_generation_batches_command_kind",
        ),
        CheckConstraint(
            "mode IN ('merged', 'per_document')", name="ck_note_generation_batches_mode"
        ),
        CheckConstraint(
            "jsonb_typeof(section_path) = 'array' AND jsonb_array_length(section_path) >= 1",
            name="ck_note_generation_batches_section_path",
        ),
        CheckConstraint(
            "(mode = 'merged' AND title_prefix IS NULL) OR "
            "(mode = 'per_document' AND title IS NULL)",
            name="ck_note_generation_batches_title_mode",
        ),
        CheckConstraint(
            "(command_kind IN ('retry_failed', 'retry_gaps') AND retry_of_batch_id IS NOT NULL) "
            "OR (command_kind IN ('create', 'regeneration') AND retry_of_batch_id IS NULL)",
            name="ck_note_generation_batches_retry_parent",
        ),
        CheckConstraint(
            "(command_kind = 'regeneration' AND mode = 'merged' "
            "AND target_note_id IS NOT NULL AND target_note_version IS NOT NULL "
            "AND target_note_version_sha256 IS NOT NULL) OR "
            "(command_kind <> 'regeneration' AND target_note_id IS NULL "
            "AND target_note_version IS NULL AND target_note_version_sha256 IS NULL)",
            name="ck_note_generation_batches_target",
        ),
        CheckConstraint(
            "target_note_version IS NULL OR target_note_version >= 1",
            name="ck_note_generation_batches_target_version",
        ),
        CheckConstraint(
            f"target_note_version_sha256 IS NULL OR target_note_version_sha256 {_HASH_CHECK}",
            name="ck_note_generation_batches_target_sha256",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'partial_success', 'succeeded', 'failed', "
            "'cancelling', 'cancelled')",
            name="ck_note_generation_batches_status",
        ),
        CheckConstraint("state_version >= 1", name="ck_note_generation_batches_state_version"),
        CheckConstraint("event_sequence >= 0", name="ck_note_generation_batches_event_sequence"),
        CheckConstraint("cancel_epoch >= 0", name="ck_note_generation_batches_cancel_epoch"),
        CheckConstraint(
            "(status IN ('partial_success', 'succeeded', 'failed', 'cancelled') "
            "AND completed_at IS NOT NULL) OR "
            "(status IN ('queued', 'running', 'cancelling') AND completed_at IS NULL)",
            name="ck_note_generation_batches_terminal_time",
        ),
        Index(
            "ix_note_generation_batches_claim",
            "status",
            "created_at",
        ),
        Index(
            "ix_note_generation_batches_scope_updated",
            "user_id",
            "course_id",
            "updated_at",
        ),
        Index(
            "ix_note_generation_batches_user_status",
            "user_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    command_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="create")
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    retry_of_batch_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str | None] = mapped_column(String(255))
    title_prefix: Mapped[str | None] = mapped_column(String(255))
    section_path: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=lambda: ["未分类"]
    )
    target_note_id: Mapped[str | None] = mapped_column(String(36))
    target_note_version: Mapped[int | None] = mapped_column(Integer)
    target_note_version_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NoteGenerationInputModel(Base):
    """Immutable document/revision snapshot selected for a batch."""

    __tablename__ = "note_generation_inputs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "course_id", "user_id"],
            [
                "note_generation_batches.id",
                "note_generation_batches.course_id",
                "note_generation_batches.user_id",
            ],
            name="fk_note_generation_inputs_batch_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "course_id", "user_id"],
            ["documents.id", "documents.course_id", "documents.user_id"],
            name="fk_note_generation_inputs_document_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_generation_inputs_id_scope"
        ),
        UniqueConstraint(
            "batch_id", "course_id", "user_id", "ordinal", name="uq_note_generation_inputs_ordinal"
        ),
        UniqueConstraint(
            "batch_id",
            "course_id",
            "user_id",
            "document_id",
            name="uq_note_generation_inputs_document",
        ),
        CheckConstraint("ordinal >= 1", name="ck_note_generation_inputs_ordinal"),
        CheckConstraint("deletion_epoch >= 0", name="ck_note_generation_inputs_deletion_epoch"),
        CheckConstraint(
            f"content_sha256 {_HASH_CHECK}", name="ck_note_generation_inputs_content_sha256"
        ),
        Index(
            "ix_note_generation_inputs_document",
            "user_id",
            "course_id",
            "document_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    deletion_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    document_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    index_manifest_at_submit: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NoteCoverageUnitModel(Base):
    """Frozen substantive input unit used by a generation batch."""

    __tablename__ = "note_coverage_units"
    __table_args__ = (
        ForeignKeyConstraint(
            ["input_id", "batch_id", "course_id", "user_id"],
            [
                "note_generation_inputs.id",
                "note_generation_inputs.batch_id",
                "note_generation_inputs.course_id",
                "note_generation_inputs.user_id",
            ],
            name="fk_note_coverage_units_input_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id",
            "input_id",
            "batch_id",
            "course_id",
            "user_id",
            name="uq_note_coverage_units_id_scope",
        ),
        UniqueConstraint(
            "input_id",
            "batch_id",
            "course_id",
            "user_id",
            "ordinal",
            name="uq_note_coverage_units_ordinal",
        ),
        CheckConstraint("ordinal >= 1", name="ck_note_coverage_units_ordinal"),
        CheckConstraint(
            "unit_type IN ('slide', 'pdf_section', 'pdf_page_window')",
            name="ck_note_coverage_units_type",
        ),
        CheckConstraint(
            f"content_sha256 {_HASH_CHECK}", name="ck_note_coverage_units_content_sha256"
        ),
        Index(
            "ix_note_coverage_units_input",
            "batch_id",
            "input_id",
            "ordinal",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    input_id: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_type: Mapped[str] = mapped_column(String(32), nullable=False)
    locator: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    is_substantive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NoteGenerationItemModel(Base):
    """One merge/per-document output state machine row."""

    __tablename__ = "note_generation_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "course_id", "user_id"],
            [
                "note_generation_batches.id",
                "note_generation_batches.course_id",
                "note_generation_batches.user_id",
            ],
            name="fk_note_generation_items_batch_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_generation_items_id_scope"
        ),
        UniqueConstraint(
            "batch_id", "course_id", "user_id", "ordinal", name="uq_note_generation_items_ordinal"
        ),
        CheckConstraint(
            "status IN ('queued', 'leased', 'running', 'retry_wait', 'succeeded', 'failed', "
            "'cancelling', 'cancelled')",
            name="ck_note_generation_items_status",
        ),
        CheckConstraint(
            "phase IS NULL OR phase IN ('validating_inputs', 'segmenting', 'retrieving', "
            "'outlining', 'generating', 'validating_output', 'saving')",
            name="ck_note_generation_items_phase",
        ),
        CheckConstraint("state_version >= 1", name="ck_note_generation_items_state_version"),
        CheckConstraint("attempt >= 0", name="ck_note_generation_items_attempt"),
        CheckConstraint("max_attempts >= 1", name="ck_note_generation_items_max_attempts"),
        CheckConstraint("lease_version >= 0", name="ck_note_generation_items_lease_version"),
        CheckConstraint("cancel_epoch >= 0", name="ck_note_generation_items_cancel_epoch"),
        CheckConstraint("available_at IS NOT NULL", name="ck_note_generation_items_available_at"),
        CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled') AND completed_at IS NOT NULL) OR "
            "(status IN ('queued', 'leased', 'running', 'retry_wait', 'cancelling') "
            "AND completed_at IS NULL)",
            name="ck_note_generation_items_terminal_time",
        ),
        Index(
            "ix_note_generation_items_claim",
            "status",
            "available_at",
            "created_at",
        ),
        Index(
            "ix_note_generation_items_batch_status",
            "batch_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    phase: Mapped[str | None] = mapped_column(String(64))
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lease_owner_id: Mapped[str | None] = mapped_column(String(128))
    lease_token_hash: Mapped[str | None] = mapped_column(String(64))
    lease_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(128))
    failure_summary: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool | None] = mapped_column(Boolean)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class NoteItemInputModel(Base):
    """Explicit item/input membership with repeated scope columns."""

    __tablename__ = "note_item_inputs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "batch_id", "course_id", "user_id"],
            [
                "note_generation_items.id",
                "note_generation_items.batch_id",
                "note_generation_items.course_id",
                "note_generation_items.user_id",
            ],
            name="fk_note_item_inputs_item_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["input_id", "batch_id", "course_id", "user_id"],
            [
                "note_generation_inputs.id",
                "note_generation_inputs.batch_id",
                "note_generation_inputs.course_id",
                "note_generation_inputs.user_id",
            ],
            name="fk_note_item_inputs_input_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_item_inputs_id_scope"
        ),
        UniqueConstraint(
            "item_id",
            "input_id",
            "batch_id",
            "course_id",
            "user_id",
            name="uq_note_item_inputs_item_input",
        ),
        CheckConstraint("ordinal >= 1", name="ck_note_item_inputs_ordinal"),
        Index("ix_note_item_inputs_input", "batch_id", "input_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    input_id: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NoteCoverageUnitResultModel(Base):
    """Immutable unit disposition for one item attempt."""

    __tablename__ = "note_coverage_unit_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "batch_id", "course_id", "user_id"],
            [
                "note_generation_items.id",
                "note_generation_items.batch_id",
                "note_generation_items.course_id",
                "note_generation_items.user_id",
            ],
            name="fk_note_coverage_unit_results_item_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["unit_id", "input_id", "batch_id", "course_id", "user_id"],
            [
                "note_coverage_units.id",
                "note_coverage_units.input_id",
                "note_coverage_units.batch_id",
                "note_coverage_units.course_id",
                "note_coverage_units.user_id",
            ],
            name="fk_note_coverage_unit_results_unit_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["item_id", "input_id", "batch_id", "course_id", "user_id"],
            [
                "note_item_inputs.item_id",
                "note_item_inputs.input_id",
                "note_item_inputs.batch_id",
                "note_item_inputs.course_id",
                "note_item_inputs.user_id",
            ],
            name="fk_note_coverage_unit_results_item_input_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["item_id", "attempt", "batch_id", "course_id", "user_id"],
            [
                "note_generation_attempts.item_id",
                "note_generation_attempts.attempt",
                "note_generation_attempts.batch_id",
                "note_generation_attempts.course_id",
                "note_generation_attempts.user_id",
            ],
            name="fk_note_coverage_unit_results_attempt_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_coverage_unit_results_id_scope"
        ),
        UniqueConstraint(
            "item_id",
            "attempt",
            "input_id",
            "unit_id",
            "batch_id",
            "course_id",
            "user_id",
            name="uq_note_coverage_unit_results_attempt_unit",
        ),
        CheckConstraint("attempt >= 1", name="ck_note_coverage_unit_results_attempt"),
        CheckConstraint(
            "status IN ('covered', 'skipped', 'failed')",
            name="ck_note_coverage_unit_results_status",
        ),
        CheckConstraint(
            "(status = 'covered' AND reason_code IS NULL) OR "
            "(status IN ('skipped', 'failed') AND reason_code IS NOT NULL "
            "AND btrim(reason_code) <> '')",
            name="ck_note_coverage_unit_results_reason",
        ),
        CheckConstraint(
            f"evidence_set_sha256 IS NULL OR evidence_set_sha256 {_HASH_CHECK}",
            name="ck_note_coverage_unit_results_evidence_sha256",
        ),
        Index(
            "ix_note_coverage_unit_results_item_attempt",
            "item_id",
            "attempt",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    input_id: Mapped[str] = mapped_column(String(36), nullable=False)
    unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    evidence_set_sha256: Mapped[str | None] = mapped_column(String(64))
    ast_node_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NoteGenerationOutputModel(Base):
    """Scope-safe association between one item and its resulting Note."""

    __tablename__ = "note_generation_outputs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "batch_id", "course_id", "user_id"],
            [
                "note_generation_items.id",
                "note_generation_items.batch_id",
                "note_generation_items.course_id",
                "note_generation_items.user_id",
            ],
            name="fk_note_generation_outputs_item_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["note_id", "course_id", "user_id"],
            ["notes.id", "notes.course_id", "notes.user_id"],
            name="fk_note_generation_outputs_note_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["note_id", "note_version", "course_id", "user_id"],
            [
                "note_content_versions.note_id",
                "note_content_versions.version",
                "note_content_versions.course_id",
                "note_content_versions.user_id",
            ],
            name="fk_note_generation_outputs_note_version_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_generation_outputs_id_scope"
        ),
        UniqueConstraint(
            "item_id", "batch_id", "course_id", "user_id", name="uq_note_generation_outputs_item"
        ),
        UniqueConstraint(
            "note_id",
            "note_version",
            "course_id",
            "user_id",
            name="uq_note_generation_outputs_note_version",
        ),
        CheckConstraint("note_version >= 1", name="ck_note_generation_outputs_note_version"),
        Index("ix_note_generation_outputs_batch", "batch_id", "course_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    note_id: Mapped[str] = mapped_column(String(36), nullable=False)
    note_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NoteGenerationAttemptModel(Base):
    """Provider/runner metadata for an item attempt without credentials."""

    __tablename__ = "note_generation_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "batch_id", "course_id", "user_id"],
            [
                "note_generation_items.id",
                "note_generation_items.batch_id",
                "note_generation_items.course_id",
                "note_generation_items.user_id",
            ],
            name="fk_note_generation_attempts_item_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_generation_attempts_id_scope"
        ),
        UniqueConstraint(
            "item_id",
            "attempt",
            "batch_id",
            "course_id",
            "user_id",
            name="uq_note_generation_attempts_item_attempt",
        ),
        CheckConstraint("attempt >= 1", name="ck_note_generation_attempts_attempt"),
        CheckConstraint(
            "cost_microusd IS NULL OR cost_microusd >= 0", name="ck_note_generation_attempts_cost"
        ),
        Index("ix_note_generation_attempts_item", "item_id", "attempt"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    runner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_alias: Mapped[str | None] = mapped_column(String(64))
    provider_model: Mapped[str | None] = mapped_column(String(255))
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    cost_microusd: Mapped[int | None] = mapped_column(BigInteger)
    failure_code: Mapped[str | None] = mapped_column(String(128))
    failure_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NoteGenerationEventModel(Base):
    """Replayable, scope-bound batch event envelope."""

    __tablename__ = "note_generation_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "course_id", "user_id"],
            [
                "note_generation_batches.id",
                "note_generation_batches.course_id",
                "note_generation_batches.user_id",
            ],
            name="fk_note_generation_events_batch_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_generation_events_id_scope"
        ),
        UniqueConstraint(
            "batch_id",
            "course_id",
            "user_id",
            "sequence",
            name="uq_note_generation_events_sequence",
        ),
        CheckConstraint("sequence >= 1", name="ck_note_generation_events_sequence"),
        CheckConstraint("state_version >= 1", name="ck_note_generation_events_state_version"),
        Index("ix_note_generation_events_batch_sequence", "batch_id", "sequence"),
        Index("ix_note_generation_events_expiry", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NoteCommandDedupModel(Base):
    """Long-lived command idempotency record scoped to a course."""

    __tablename__ = "note_command_dedup"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_note_command_dedup_course_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "user_id",
            "course_id",
            "command_scope",
            "key_hash",
            name="uq_note_command_dedup_key",
        ),
        CheckConstraint(f"key_hash {_HASH_CHECK}", name="ck_note_command_dedup_key_hash"),
        CheckConstraint(f"request_hash {_HASH_CHECK}", name="ck_note_command_dedup_request_hash"),
        CheckConstraint(
            "response_status >= 100 AND response_status <= 599", name="ck_note_command_dedup_status"
        ),
        Index("ix_note_command_dedup_expiry", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    command_scope: Mapped[str] = mapped_column(String(128), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_type: Mapped[str] = mapped_column(String(64), nullable=False)
    result_id: Mapped[str] = mapped_column(String(36), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "NoteCommandDedupModel",
    "NoteCoverageUnitModel",
    "NoteCoverageUnitResultModel",
    "NoteGenerationAttemptModel",
    "NoteGenerationBatchModel",
    "NoteGenerationEventModel",
    "NoteGenerationInputModel",
    "NoteGenerationItemModel",
    "NoteGenerationOutputModel",
    "NoteItemInputModel",
]
