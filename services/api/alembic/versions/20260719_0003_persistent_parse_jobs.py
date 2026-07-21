"""Create persistent parse jobs, checkpoints, and replayable events.

Revision ID: 20260719_0003
Revises: 20260719_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260719_0003"
down_revision: str | None = "20260719_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.create_table(
        "parse_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("stored_object_id", sa.String(length=36), nullable=False),
        sa.Column("job_type", sa.String(length=32), server_default="parse", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("state_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("parser_profile", sa.String(length=64), nullable=False),
        sa.Column(
            "parser_schema_version", sa.String(length=32), server_default="1.0", nullable=False
        ),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("document_sha256", sa.String(length=64), nullable=False),
        sa.Column("document_deletion_epoch", sa.Integer(), nullable=False),
        sa.Column("input_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("estimated_pages", sa.Integer(), nullable=True),
        sa.Column("requires_ocr", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("requires_rendering", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "requested_pages",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("lease_owner_id", sa.String(length=128), nullable=True),
        sa.Column("lease_token_hash", sa.String(length=64), nullable=True),
        sa.Column("lease_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "progress",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("event_sequence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result_manifest_ref", sa.String(length=1024), nullable=True),
        sa.Column("result_sha256", sa.String(length=64), nullable=True),
        sa.Column("result_page_count", sa.Integer(), nullable=True),
        sa.Column(
            "failed_pages",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("state_version >= 1", name="ck_parse_jobs_state_version"),
        sa.CheckConstraint("attempt >= 0", name="ck_parse_jobs_attempt"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_parse_jobs_max_attempts"),
        sa.CheckConstraint("lease_version >= 0", name="ck_parse_jobs_lease_version"),
        sa.CheckConstraint("event_sequence >= 0", name="ck_parse_jobs_event_sequence"),
        sa.CheckConstraint("input_size_bytes >= 0", name="ck_parse_jobs_input_size"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stored_object_id"], ["stored_objects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["document_id", "course_id", "user_id"],
            ["documents.id", "documents.course_id", "documents.user_id"],
            name="fk_parse_jobs_document_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stored_object_id", "course_id", "user_id"],
            ["stored_objects.id", "stored_objects.course_id", "stored_objects.user_id"],
            name="fk_parse_jobs_object_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "document_id", "course_id", "user_id", name="uq_parse_jobs_scope"
        ),
    )
    op.create_index(
        "ix_parse_jobs_claim",
        "parse_jobs",
        ["status", "available_at", "created_at"],
    )
    op.create_index("ix_parse_jobs_document_status", "parse_jobs", ["document_id", "status"])
    op.create_index(
        "uq_parse_jobs_document_nonterminal",
        "parse_jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued','leased','parsing','result_submitted',"
            "'validating','indexing','retry_wait')"
        ),
    )

    op.create_table(
        "job_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("stored_object_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("deletion_epoch", sa.Integer(), nullable=False),
        sa.Column("artifact_name", sa.String(length=255), nullable=False),
        sa.Column("artifact_schema_version", sa.String(length=32), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="available", nullable=False),
        *_timestamps(),
        sa.CheckConstraint("attempt >= 1", name="ck_job_artifacts_attempt"),
        sa.CheckConstraint("deletion_epoch >= 0", name="ck_job_artifacts_deletion_epoch"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_job_artifacts_size"),
        sa.ForeignKeyConstraint(["job_id"], ["parse_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stored_object_id"], ["stored_objects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["job_id", "document_id", "course_id", "user_id"],
            [
                "parse_jobs.id",
                "parse_jobs.document_id",
                "parse_jobs.course_id",
                "parse_jobs.user_id",
            ],
            name="fk_job_artifacts_job_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stored_object_id", "course_id", "user_id"],
            ["stored_objects.id", "stored_objects.course_id", "stored_objects.user_id"],
            name="fk_job_artifacts_object_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id", "attempt", "artifact_name", name="uq_job_artifacts_job_attempt_name"
        ),
    )
    op.create_index("ix_job_artifacts_job_attempt", "job_artifacts", ["job_id", "attempt"])

    op.create_table(
        "page_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("page_ordinal", sa.Integer(), nullable=False),
        sa.Column("lease_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("output_ref", sa.String(length=1024), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("output_schema_version", sa.String(length=32), nullable=False),
        sa.Column("source_backend", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.String(length=64), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("attempt >= 1", name="ck_page_checkpoints_attempt"),
        sa.CheckConstraint("page_ordinal >= 1", name="ck_page_checkpoints_page"),
        sa.CheckConstraint("lease_version >= 1", name="ck_page_checkpoints_lease_version"),
        sa.ForeignKeyConstraint(["job_id"], ["parse_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["job_id", "document_id", "course_id", "user_id"],
            [
                "parse_jobs.id",
                "parse_jobs.document_id",
                "parse_jobs.course_id",
                "parse_jobs.user_id",
            ],
            name="fk_page_checkpoints_job_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id", "attempt", "page_ordinal", name="uq_page_checkpoints_job_attempt_page"
        ),
    )
    op.create_index("ix_page_checkpoints_job_attempt", "page_checkpoints", ["job_id", "attempt"])

    op.create_table(
        "job_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_job_events_sequence"),
        sa.ForeignKeyConstraint(["job_id"], ["parse_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["job_id", "document_id", "course_id", "user_id"],
            [
                "parse_jobs.id",
                "parse_jobs.document_id",
                "parse_jobs.course_id",
                "parse_jobs.user_id",
            ],
            name="fk_job_events_job_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "sequence", name="uq_job_events_job_sequence"),
    )
    op.create_index("ix_job_events_job_sequence", "job_events", ["job_id", "sequence"])
    op.create_index("ix_job_events_expires_at", "job_events", ["expires_at"])
    op.create_foreign_key(
        "fk_document_revisions_parse_job",
        "document_revisions",
        "parse_jobs",
        ["parse_job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.execute("UPDATE document_revisions SET parse_job_id = NULL WHERE parse_job_id IS NOT NULL")
    op.drop_constraint("fk_document_revisions_parse_job", "document_revisions", type_="foreignkey")
    op.drop_index("ix_job_events_expires_at", table_name="job_events")
    op.drop_index("ix_job_events_job_sequence", table_name="job_events")
    op.drop_table("job_events")
    op.drop_index("ix_page_checkpoints_job_attempt", table_name="page_checkpoints")
    op.drop_table("page_checkpoints")
    op.drop_index("ix_job_artifacts_job_attempt", table_name="job_artifacts")
    op.drop_table("job_artifacts")
    op.drop_index("ix_parse_jobs_document_status", table_name="parse_jobs")
    op.drop_index("uq_parse_jobs_document_nonterminal", table_name="parse_jobs")
    op.drop_index("ix_parse_jobs_claim", table_name="parse_jobs")
    op.drop_table("parse_jobs")
