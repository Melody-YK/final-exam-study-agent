"""note workflow expand

Revision ID: 7102eb21ee91
Revises: 20260721_0007
Create Date: 2026-07-22 11:41:26.275881
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7102eb21ee91"
down_revision: str | None = "20260721_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "note_command_dedup",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("command_scope", sa.String(length=128), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("result_type", sa.String(length=64), nullable=False),
        sa.Column("result_id", sa.String(length=36), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="ck_note_command_dedup_key_hash"),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'", name="ck_note_command_dedup_request_hash"
        ),
        sa.CheckConstraint(
            "response_status >= 100 AND response_status <= 599", name="ck_note_command_dedup_status"
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_note_command_dedup_course_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "course_id", "command_scope", "key_hash", name="uq_note_command_dedup_key"
        ),
    )
    op.create_index(
        "ix_note_command_dedup_expiry", "note_command_dedup", ["expires_at"], unique=False
    )
    op.create_table(
        "note_generation_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("retry_of_batch_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("cancel_epoch", sa.Integer(), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status IN ('partial_success', 'succeeded', 'failed', 'cancelled') "
            "AND completed_at IS NOT NULL) OR "
            "(status IN ('queued', 'running', 'cancelling') AND completed_at IS NULL)",
            name="ck_note_generation_batches_terminal_time",
        ),
        sa.CheckConstraint(
            "mode IN ('merged', 'per_document')", name="ck_note_generation_batches_mode"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'partial_success', 'succeeded', "
            "'failed', 'cancelling', 'cancelled')",
            name="ck_note_generation_batches_status",
        ),
        sa.CheckConstraint("cancel_epoch >= 0", name="ck_note_generation_batches_cancel_epoch"),
        sa.CheckConstraint("event_sequence >= 0", name="ck_note_generation_batches_event_sequence"),
        sa.CheckConstraint("state_version >= 1", name="ck_note_generation_batches_state_version"),
        sa.ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_note_generation_batches_course_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_batch_id", "course_id", "user_id"],
            [
                "note_generation_batches.id",
                "note_generation_batches.course_id",
                "note_generation_batches.user_id",
            ],
            name="fk_note_generation_batches_retry_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "course_id", "user_id", name="uq_note_generation_batches_id_scope"
        ),
    )
    op.create_index(
        "ix_note_generation_batches_claim",
        "note_generation_batches",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_note_generation_batches_scope_updated",
        "note_generation_batches",
        ["user_id", "course_id", "updated_at"],
        unique=False,
    )
    op.create_table(
        "note_content_versions",
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("section_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("content_ast", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ast_schema_version", sa.String(length=32), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("body_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_set_sha256", sa.String(length=64), nullable=False),
        sa.Column("coverage_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("note_version_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "body_sha256 ~ '^[0-9a-f]{64}$'", name="ck_note_content_versions_body_sha256"
        ),
        sa.CheckConstraint("btrim(body_markdown) <> ''", name="ck_note_content_versions_body"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_note_content_versions_title"),
        sa.CheckConstraint(
            "coverage_manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_note_content_versions_coverage_sha256",
        ),
        sa.CheckConstraint(
            "created_by IN ('generated', 'user', 'legacy_backfill')",
            name="ck_note_content_versions_created_by",
        ),
        sa.CheckConstraint(
            "note_version_sha256 ~ '^[0-9a-f]{64}$'", name="ck_note_content_versions_version_sha256"
        ),
        sa.CheckConstraint(
            "source_set_sha256 ~ '^[0-9a-f]{64}$'", name="ck_note_content_versions_source_sha256"
        ),
        sa.CheckConstraint("version >= 1", name="ck_note_content_versions_version"),
        sa.ForeignKeyConstraint(
            ["note_id", "course_id", "user_id"],
            ["notes.id", "notes.course_id", "notes.user_id"],
            name="fk_note_content_versions_note_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("note_id", "version"),
        sa.UniqueConstraint(
            "note_id", "version", "course_id", "user_id", name="uq_note_content_versions_scope"
        ),
    )
    op.create_index(
        "ix_note_content_versions_note_version",
        "note_content_versions",
        ["note_id", "version"],
        unique=False,
    )
    op.create_index(
        "ix_note_content_versions_scope_created",
        "note_content_versions",
        ["user_id", "course_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "note_generation_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_note_generation_events_sequence"),
        sa.CheckConstraint("state_version >= 1", name="ck_note_generation_events_state_version"),
        sa.ForeignKeyConstraint(
            ["batch_id", "course_id", "user_id"],
            [
                "note_generation_batches.id",
                "note_generation_batches.course_id",
                "note_generation_batches.user_id",
            ],
            name="fk_note_generation_events_batch_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "batch_id",
            "course_id",
            "user_id",
            "sequence",
            name="uq_note_generation_events_sequence",
        ),
        sa.UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_generation_events_id_scope"
        ),
    )
    op.create_index(
        "ix_note_generation_events_batch_sequence",
        "note_generation_events",
        ["batch_id", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_note_generation_events_expiry", "note_generation_events", ["expires_at"], unique=False
    )
    op.create_table(
        "note_generation_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_owner_id", sa.String(length=128), nullable=True),
        sa.Column("lease_token_hash", sa.String(length=64), nullable=True),
        sa.Column("lease_version", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_epoch", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled') "
            "AND completed_at IS NOT NULL) OR "
            "(status IN ('queued', 'leased', 'running', 'retry_wait', 'cancelling') "
            "AND completed_at IS NULL)",
            name="ck_note_generation_items_terminal_time",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'leased', 'running', 'retry_wait', 'succeeded', "
            "'failed', 'cancelling', 'cancelled')",
            name="ck_note_generation_items_status",
        ),
        sa.CheckConstraint(
            "phase IS NULL OR phase IN ('validating_inputs', 'segmenting', 'retrieving', "
            "'outlining', 'generating', 'validating_output', 'saving')",
            name="ck_note_generation_items_phase",
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_note_generation_items_attempt"),
        sa.CheckConstraint(
            "available_at IS NOT NULL", name="ck_note_generation_items_available_at"
        ),
        sa.CheckConstraint("cancel_epoch >= 0", name="ck_note_generation_items_cancel_epoch"),
        sa.CheckConstraint("lease_version >= 0", name="ck_note_generation_items_lease_version"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_note_generation_items_max_attempts"),
        sa.CheckConstraint("state_version >= 1", name="ck_note_generation_items_state_version"),
        sa.ForeignKeyConstraint(
            ["batch_id", "course_id", "user_id"],
            [
                "note_generation_batches.id",
                "note_generation_batches.course_id",
                "note_generation_batches.user_id",
            ],
            name="fk_note_generation_items_batch_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "batch_id", "course_id", "user_id", "ordinal", name="uq_note_generation_items_ordinal"
        ),
        sa.UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_generation_items_id_scope"
        ),
    )
    op.create_index(
        "ix_note_generation_items_batch_status",
        "note_generation_items",
        ["batch_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_note_generation_items_claim",
        "note_generation_items",
        ["status", "available_at", "created_at"],
        unique=False,
    )
    op.create_table(
        "note_generation_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("runner_id", sa.String(length=128), nullable=False),
        sa.Column("provider_alias", sa.String(length=64), nullable=True),
        sa.Column("provider_model", sa.String(length=255), nullable=True),
        sa.Column("contract_version", sa.String(length=32), nullable=False),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_note_generation_attempts_attempt"),
        sa.CheckConstraint(
            "cost_microusd IS NULL OR cost_microusd >= 0", name="ck_note_generation_attempts_cost"
        ),
        sa.ForeignKeyConstraint(
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_generation_attempts_id_scope"
        ),
        sa.UniqueConstraint(
            "item_id",
            "attempt",
            "batch_id",
            "course_id",
            "user_id",
            name="uq_note_generation_attempts_item_attempt",
        ),
    )
    op.create_index(
        "ix_note_generation_attempts_item",
        "note_generation_attempts",
        ["item_id", "attempt"],
        unique=False,
    )
    op.create_table(
        "note_generation_inputs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("deletion_epoch", sa.Integer(), nullable=False),
        sa.Column("document_name", sa.String(length=1024), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("index_manifest_at_submit", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_note_generation_inputs_content_sha256"
        ),
        sa.CheckConstraint("deletion_epoch >= 0", name="ck_note_generation_inputs_deletion_epoch"),
        sa.CheckConstraint("ordinal >= 1", name="ck_note_generation_inputs_ordinal"),
        sa.ForeignKeyConstraint(
            ["batch_id", "course_id", "user_id"],
            [
                "note_generation_batches.id",
                "note_generation_batches.course_id",
                "note_generation_batches.user_id",
            ],
            name="fk_note_generation_inputs_batch_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "course_id", "user_id"],
            ["documents.id", "documents.course_id", "documents.user_id"],
            name="fk_note_generation_inputs_document_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "batch_id",
            "course_id",
            "user_id",
            "document_id",
            name="uq_note_generation_inputs_document",
        ),
        sa.UniqueConstraint(
            "batch_id", "course_id", "user_id", "ordinal", name="uq_note_generation_inputs_ordinal"
        ),
        sa.UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_generation_inputs_id_scope"
        ),
    )
    op.create_index(
        "ix_note_generation_inputs_document",
        "note_generation_inputs",
        ["user_id", "course_id", "document_id"],
        unique=False,
    )
    op.create_table(
        "note_generation_outputs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["note_id", "course_id", "user_id"],
            ["notes.id", "notes.course_id", "notes.user_id"],
            name="fk_note_generation_outputs_note_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_generation_outputs_id_scope"
        ),
        sa.UniqueConstraint(
            "item_id", "batch_id", "course_id", "user_id", name="uq_note_generation_outputs_item"
        ),
        sa.UniqueConstraint(
            "note_id", "course_id", "user_id", name="uq_note_generation_outputs_note"
        ),
    )
    op.create_index(
        "ix_note_generation_outputs_batch",
        "note_generation_outputs",
        ["batch_id", "course_id", "user_id"],
        unique=False,
    )
    op.create_table(
        "note_version_coverage",
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("basis", sa.String(length=64), nullable=False),
        sa.Column("generated_from_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(basis = 'user_edited_from_generated_version' "
            "AND generated_from_version IS NOT NULL) OR "
            "(basis <> 'user_edited_from_generated_version' "
            "AND generated_from_version IS NULL)",
            name="ck_note_version_coverage_basis_source",
        ),
        sa.CheckConstraint(
            "basis IN ('generated', 'user_edited_from_generated_version', 'legacy_backfill')",
            name="ck_note_version_coverage_basis",
        ),
        sa.CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_note_version_coverage_manifest_sha256"
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'partial', 'unknown_legacy')",
            name="ck_note_version_coverage_status",
        ),
        sa.ForeignKeyConstraint(
            ["note_id", "generated_from_version", "course_id", "user_id"],
            [
                "note_content_versions.note_id",
                "note_content_versions.version",
                "note_content_versions.course_id",
                "note_content_versions.user_id",
            ],
            name="fk_note_version_coverage_generated_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["note_id", "version", "course_id", "user_id"],
            [
                "note_content_versions.note_id",
                "note_content_versions.version",
                "note_content_versions.course_id",
                "note_content_versions.user_id",
            ],
            name="fk_note_version_coverage_version_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("note_id", "version", "user_id", "course_id"),
    )
    op.create_index(
        "ix_note_version_coverage_scope",
        "note_version_coverage",
        ["user_id", "course_id", "note_id", "version"],
        unique=False,
    )
    op.create_table(
        "note_version_source_snapshots",
        sa.Column("source_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.String(length=255), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=255), nullable=False),
        sa.Column("document_name", sa.String(length=1024), nullable=False),
        sa.Column("document_deletion_epoch", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_note_version_source_snapshots_content_sha256",
        ),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_note_version_source_snapshots_payload_sha256",
        ),
        sa.CheckConstraint(
            "document_deletion_epoch >= 0", name="ck_note_version_source_snapshots_deletion_epoch"
        ),
        sa.CheckConstraint("ordinal >= 1", name="ck_note_version_source_snapshots_ordinal"),
        sa.ForeignKeyConstraint(
            ["note_id", "version", "course_id", "user_id"],
            [
                "note_content_versions.note_id",
                "note_content_versions.version",
                "note_content_versions.course_id",
                "note_content_versions.user_id",
            ],
            name="fk_note_version_source_snapshots_version_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_snapshot_id"),
        sa.UniqueConstraint(
            "note_id",
            "version",
            "course_id",
            "user_id",
            "ordinal",
            name="uq_note_version_source_snapshots_ordinal",
        ),
        sa.UniqueConstraint(
            "source_snapshot_id",
            "note_id",
            "version",
            "course_id",
            "user_id",
            name="uq_note_version_source_snapshots_scope",
        ),
    )
    op.create_index(
        "ix_note_version_source_snapshots_document",
        "note_version_source_snapshots",
        ["user_id", "course_id", "document_id"],
        unique=False,
    )
    op.create_index(
        "ix_note_version_source_snapshots_version",
        "note_version_source_snapshots",
        ["note_id", "version", "course_id", "user_id"],
        unique=False,
    )
    op.create_table(
        "note_coverage_units",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("input_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("unit_type", sa.String(length=32), nullable=False),
        sa.Column("locator", sa.String(length=1024), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("is_substantive", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_note_coverage_units_content_sha256"
        ),
        sa.CheckConstraint(
            "unit_type IN ('slide', 'pdf_section', 'pdf_page_window')",
            name="ck_note_coverage_units_type",
        ),
        sa.CheckConstraint("ordinal >= 1", name="ck_note_coverage_units_ordinal"),
        sa.ForeignKeyConstraint(
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "input_id",
            "batch_id",
            "course_id",
            "user_id",
            name="uq_note_coverage_units_id_scope",
        ),
        sa.UniqueConstraint(
            "input_id",
            "batch_id",
            "course_id",
            "user_id",
            "ordinal",
            name="uq_note_coverage_units_ordinal",
        ),
    )
    op.create_index(
        "ix_note_coverage_units_input",
        "note_coverage_units",
        ["batch_id", "input_id", "ordinal"],
        unique=False,
    )
    op.create_table(
        "note_item_inputs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("input_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ordinal >= 1", name="ck_note_item_inputs_ordinal"),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_item_inputs_id_scope"
        ),
        sa.UniqueConstraint(
            "item_id",
            "input_id",
            "batch_id",
            "course_id",
            "user_id",
            name="uq_note_item_inputs_item_input",
        ),
    )
    op.create_index(
        "ix_note_item_inputs_input", "note_item_inputs", ["batch_id", "input_id"], unique=False
    )
    op.create_table(
        "note_source_state_overlays",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("overlay_sequence", sa.Integer(), nullable=False),
        sa.Column("display_epoch", sa.Integer(), nullable=False),
        sa.Column("access_epoch", sa.Integer(), nullable=False),
        sa.Column("display_state", sa.String(length=32), nullable=False),
        sa.Column("access_state", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=False),
        sa.Column("cause_type", sa.String(length=64), nullable=False),
        sa.Column("cause_id", sa.String(length=255), nullable=False),
        sa.Column("cause_epoch", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "access_state IN ('authorized', 'revoked', 'redacted', 'unauthorized')",
            name="ck_note_source_state_overlays_access_state",
        ),
        sa.CheckConstraint(
            "display_state IN ('current', 'stale', 'unavailable', 'redacted')",
            name="ck_note_source_state_overlays_display_state",
        ),
        sa.CheckConstraint("access_epoch >= 0", name="ck_note_source_state_overlays_access_epoch"),
        sa.CheckConstraint("cause_epoch >= 0", name="ck_note_source_state_overlays_cause_epoch"),
        sa.CheckConstraint(
            "display_epoch >= 0", name="ck_note_source_state_overlays_display_epoch"
        ),
        sa.CheckConstraint("overlay_sequence >= 1", name="ck_note_source_state_overlays_sequence"),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id", "note_id", "version", "course_id", "user_id"],
            [
                "note_version_source_snapshots.source_snapshot_id",
                "note_version_source_snapshots.note_id",
                "note_version_source_snapshots.version",
                "note_version_source_snapshots.course_id",
                "note_version_source_snapshots.user_id",
            ],
            name="fk_note_source_state_overlays_snapshot_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_snapshot_id",
            "note_id",
            "version",
            "course_id",
            "user_id",
            "cause_type",
            "cause_id",
            "cause_epoch",
            name="uq_note_source_state_overlays_cause",
        ),
        sa.UniqueConstraint(
            "source_snapshot_id",
            "note_id",
            "version",
            "course_id",
            "user_id",
            "overlay_sequence",
            name="uq_note_source_state_overlays_sequence",
        ),
    )
    op.create_index(
        "ix_note_source_state_overlays_latest",
        "note_source_state_overlays",
        ["source_snapshot_id", "note_id", "version", "course_id", "user_id", "overlay_sequence"],
        unique=False,
    )
    op.create_table(
        "note_version_coverage_units",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("input_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("unit_type", sa.String(length=32), nullable=False),
        sa.Column("locator", sa.String(length=1024), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("is_substantive", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("ast_node_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_snapshot_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_note_version_coverage_units_content_sha256",
        ),
        sa.CheckConstraint(
            "unit_type IN ('slide', 'pdf_section', 'pdf_page_window')",
            name="ck_note_version_coverage_units_type",
        ),
        sa.CheckConstraint(
            "(status IN ('pending', 'covered') AND reason_code IS NULL) OR "
            "(status IN ('skipped', 'failed') AND reason_code IS NOT NULL "
            "AND btrim(reason_code) <> '')",
            name="ck_note_version_coverage_units_reason",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'covered', 'skipped', 'failed')",
            name="ck_note_version_coverage_units_status",
        ),
        sa.CheckConstraint("ordinal >= 1", name="ck_note_version_coverage_units_ordinal"),
        sa.ForeignKeyConstraint(
            ["note_id", "version", "course_id", "user_id"],
            [
                "note_version_coverage.note_id",
                "note_version_coverage.version",
                "note_version_coverage.course_id",
                "note_version_coverage.user_id",
            ],
            name="fk_note_version_coverage_units_manifest_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "note_id",
            "version",
            "course_id",
            "user_id",
            "id",
            name="uq_note_version_coverage_units_scope",
        ),
        sa.UniqueConstraint(
            "note_id",
            "version",
            "course_id",
            "user_id",
            "ordinal",
            name="uq_note_version_coverage_units_ordinal",
        ),
    )
    op.create_index(
        "ix_note_version_coverage_units_version",
        "note_version_coverage_units",
        ["note_id", "version", "course_id", "user_id"],
        unique=False,
    )
    op.create_table(
        "note_version_source_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("source_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("citation_id", sa.String(length=255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("ast_node_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ordinal >= 1", name="ck_note_version_source_links_ordinal"),
        sa.ForeignKeyConstraint(
            ["note_id", "version", "course_id", "user_id"],
            [
                "note_content_versions.note_id",
                "note_content_versions.version",
                "note_content_versions.course_id",
                "note_content_versions.user_id",
            ],
            name="fk_note_version_source_links_version_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id", "note_id", "version", "course_id", "user_id"],
            [
                "note_version_source_snapshots.source_snapshot_id",
                "note_version_source_snapshots.note_id",
                "note_version_source_snapshots.version",
                "note_version_source_snapshots.course_id",
                "note_version_source_snapshots.user_id",
            ],
            name="fk_note_version_source_links_snapshot_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "note_id",
            "version",
            "course_id",
            "user_id",
            "citation_id",
            name="uq_note_version_source_links_citation",
        ),
        sa.UniqueConstraint(
            "note_id",
            "version",
            "course_id",
            "user_id",
            "ordinal",
            name="uq_note_version_source_links_ordinal",
        ),
    )
    op.create_index(
        "ix_note_version_source_links_version",
        "note_version_source_links",
        ["note_id", "version", "course_id", "user_id"],
        unique=False,
    )
    op.create_table(
        "note_version_source_payloads",
        sa.Column("source_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("bounding_boxes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("redacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redaction_reason", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quote IS NULL OR btrim(quote) <> ''",
            name="ck_note_version_source_payloads_quote_nonblank",
        ),
        sa.CheckConstraint(
            "(quote IS NOT NULL AND bounding_boxes IS NOT NULL "
            "AND provenance IS NOT NULL AND redacted_at IS NULL "
            "AND redaction_reason IS NULL) OR "
            "(quote IS NULL AND bounding_boxes IS NULL AND provenance IS NULL "
            "AND redacted_at IS NOT NULL AND redaction_reason IS NOT NULL)",
            name="ck_note_version_source_payloads_redaction_state",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id", "note_id", "version", "course_id", "user_id"],
            [
                "note_version_source_snapshots.source_snapshot_id",
                "note_version_source_snapshots.note_id",
                "note_version_source_snapshots.version",
                "note_version_source_snapshots.course_id",
                "note_version_source_snapshots.user_id",
            ],
            name="fk_note_version_source_payloads_snapshot_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_snapshot_id", "note_id", "version", "user_id", "course_id"),
    )
    op.create_index(
        "ix_note_version_source_payloads_version",
        "note_version_source_payloads",
        ["note_id", "version", "course_id", "user_id"],
        unique=False,
    )
    op.create_table(
        "note_coverage_unit_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("input_id", sa.String(length=36), nullable=False),
        sa.Column("unit_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("evidence_set_sha256", sa.String(length=64), nullable=True),
        sa.Column("ast_node_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "evidence_set_sha256 IS NULL OR evidence_set_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_note_coverage_unit_results_evidence_sha256",
        ),
        sa.CheckConstraint(
            "(status = 'covered' AND reason_code IS NULL) OR "
            "(status IN ('skipped', 'failed') AND reason_code IS NOT NULL "
            "AND btrim(reason_code) <> '')",
            name="ck_note_coverage_unit_results_reason",
        ),
        sa.CheckConstraint(
            "status IN ('covered', 'skipped', 'failed')",
            name="ck_note_coverage_unit_results_status",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_note_coverage_unit_results_attempt"),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_note_coverage_unit_results_id_scope"
        ),
        sa.UniqueConstraint(
            "item_id",
            "attempt",
            "input_id",
            "unit_id",
            "batch_id",
            "course_id",
            "user_id",
            name="uq_note_coverage_unit_results_attempt_unit",
        ),
    )
    op.create_index(
        "ix_note_coverage_unit_results_item_attempt",
        "note_coverage_unit_results",
        ["item_id", "attempt"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(
        "ix_note_coverage_unit_results_item_attempt", table_name="note_coverage_unit_results"
    )
    op.drop_table("note_coverage_unit_results")
    op.drop_index(
        "ix_note_version_source_payloads_version", table_name="note_version_source_payloads"
    )
    op.drop_table("note_version_source_payloads")
    op.drop_index("ix_note_version_source_links_version", table_name="note_version_source_links")
    op.drop_table("note_version_source_links")
    op.drop_index(
        "ix_note_version_coverage_units_version", table_name="note_version_coverage_units"
    )
    op.drop_table("note_version_coverage_units")
    op.drop_index("ix_note_source_state_overlays_latest", table_name="note_source_state_overlays")
    op.drop_table("note_source_state_overlays")
    op.drop_index("ix_note_item_inputs_input", table_name="note_item_inputs")
    op.drop_table("note_item_inputs")
    op.drop_index("ix_note_coverage_units_input", table_name="note_coverage_units")
    op.drop_table("note_coverage_units")
    op.drop_index(
        "ix_note_version_source_snapshots_version", table_name="note_version_source_snapshots"
    )
    op.drop_index(
        "ix_note_version_source_snapshots_document", table_name="note_version_source_snapshots"
    )
    op.drop_table("note_version_source_snapshots")
    op.drop_index("ix_note_version_coverage_scope", table_name="note_version_coverage")
    op.drop_table("note_version_coverage")
    op.drop_index("ix_note_generation_outputs_batch", table_name="note_generation_outputs")
    op.drop_table("note_generation_outputs")
    op.drop_index("ix_note_generation_inputs_document", table_name="note_generation_inputs")
    op.drop_table("note_generation_inputs")
    op.drop_index("ix_note_generation_attempts_item", table_name="note_generation_attempts")
    op.drop_table("note_generation_attempts")
    op.drop_index("ix_note_generation_items_claim", table_name="note_generation_items")
    op.drop_index("ix_note_generation_items_batch_status", table_name="note_generation_items")
    op.drop_table("note_generation_items")
    op.drop_index("ix_note_generation_events_expiry", table_name="note_generation_events")
    op.drop_index("ix_note_generation_events_batch_sequence", table_name="note_generation_events")
    op.drop_table("note_generation_events")
    op.drop_index("ix_note_content_versions_scope_created", table_name="note_content_versions")
    op.drop_index("ix_note_content_versions_note_version", table_name="note_content_versions")
    op.drop_table("note_content_versions")
    op.drop_index("ix_note_generation_batches_scope_updated", table_name="note_generation_batches")
    op.drop_index("ix_note_generation_batches_claim", table_name="note_generation_batches")
    op.drop_table("note_generation_batches")
    op.drop_index("ix_note_command_dedup_expiry", table_name="note_command_dedup")
    op.drop_table("note_command_dedup")
    # ### end Alembic commands ###
