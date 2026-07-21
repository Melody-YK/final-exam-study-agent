"""Create the principal-scoped course ingestion data plane.

Revision ID: 20260718_0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_0001"
down_revision: str | None = None
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
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("authentication_method", sa.String(length=32), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "authentication_method", "subject", name="uq_users_authentication_subject"
        ),
    )
    op.create_table(
        "courses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("lifecycle", sa.String(length=32), nullable=False),
        sa.Column("active_lexical_index_id", sa.String(length=36), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("row_version >= 1", name="ck_courses_row_version_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_courses_user_visible", "courses", ["user_id", "deleted_at"])

    op.create_table(
        "stored_objects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=True),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_stored_objects_size_nonnegative"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(
        "ix_stored_objects_course_visible", "stored_objects", ["course_id", "deleted_at"]
    )

    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("stored_object_id", sa.String(length=36), nullable=True),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("expected_sha256", sa.String(length=64), nullable=False),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("expected_size >= 0", name="ck_upload_sessions_size_nonnegative"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stored_object_id"], ["stored_objects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_upload_sessions_course_status", "upload_sessions", ["course_id", "status"])

    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("stored_object_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=1024), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("corpus_role", sa.String(length=32), nullable=False),
        sa.Column("verified_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("preview_revision_id", sa.String(length=36), nullable=True),
        sa.Column("active_revision_id", sa.String(length=36), nullable=True),
        sa.Column("deletion_epoch", sa.Integer(), server_default="0", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("deletion_epoch >= 0", name="ck_documents_deletion_epoch_nonnegative"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stored_object_id"], ["stored_objects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documents_course_visible", "documents", ["course_id", "deleted_at"])

    op.create_table(
        "document_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("parse_job_id", sa.String(length=36), nullable=True),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("parser_profile", sa.String(length=64), nullable=False),
        sa.Column("parser_schema_version", sa.String(length=32), nullable=False),
        sa.Column("quality_status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_document_revisions_ordinal"),
    )
    op.create_index(
        "ix_document_revisions_document_created",
        "document_revisions",
        ["document_id", "created_at"],
    )
    op.create_foreign_key(
        "fk_documents_preview_revision",
        "documents",
        "document_revisions",
        ["preview_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_documents_active_revision",
        "documents",
        "document_revisions",
        ["active_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_subject", sa.String(length=255), nullable=False),
        sa.Column("operation", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_subject",
            "operation",
            "idempotency_key",
            name="uq_idempotency_actor_operation_key",
        ),
    )
    op.create_index("ix_idempotency_expires_at", "idempotency_records", ["expires_at"])

    op.create_table(
        "deletion_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("deletion_epoch", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("attempt_count >= 0", name="ck_deletion_jobs_attempt_nonnegative"),
        sa.CheckConstraint("deletion_epoch >= 0", name="ck_deletion_jobs_epoch_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "target_type", "target_id", "deletion_epoch", name="uq_deletion_target_epoch"
        ),
    )
    op.create_index(
        "ix_deletion_jobs_status_available", "deletion_jobs", ["status", "available_at"]
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error_detail", sa.Text(), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_events_attempt_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outbox_events_status_available", "outbox_events", ["status", "available_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_status_available", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_deletion_jobs_status_available", table_name="deletion_jobs")
    op.drop_table("deletion_jobs")
    op.drop_index("ix_idempotency_expires_at", table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_constraint("fk_documents_active_revision", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_preview_revision", "documents", type_="foreignkey")
    op.drop_index("ix_document_revisions_document_created", table_name="document_revisions")
    op.drop_table("document_revisions")
    op.drop_index("ix_documents_course_visible", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_upload_sessions_course_status", table_name="upload_sessions")
    op.drop_table("upload_sessions")
    op.drop_index("ix_stored_objects_course_visible", table_name="stored_objects")
    op.drop_table("stored_objects")
    op.drop_index("ix_courses_user_visible", table_name="courses")
    op.drop_table("courses")
    op.drop_table("users")
