"""Add trusted query, citation dependency, and note persistence.

Revision ID: 20260719_0006
Revises: 20260719_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260719_0006"
down_revision: str | None = "20260719_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "query_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("question_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "requested_document_ids",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("answer_schema_version", sa.String(length=32), nullable=False),
        sa.Column("answer_markdown", sa.Text(), nullable=False),
        sa.Column(
            "claims", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "citations",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("refusal_code", sa.String(length=128), nullable=True),
        sa.Column("refusal_message", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("provider_alias", sa.String(length=64), nullable=True),
        sa.Column("provider_model", sa.String(length=255), nullable=True),
        sa.Column("provider_response_id", sa.String(length=255), nullable=True),
        sa.Column(
            "usage", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("event_sequence >= 0", name="ck_query_runs_event_sequence"),
        sa.CheckConstraint(
            "status IN ('pending', 'retrieving', 'generating', 'answered', "
            "'abstained', 'failed', 'invalidated')",
            name="ck_query_runs_status",
        ),
        sa.CheckConstraint(
            "cost_microusd IS NULL OR cost_microusd >= 0",
            name="ck_query_runs_cost_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_query_runs_course_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "course_id", "user_id", name="uq_query_runs_id_scope"),
    )
    op.create_index(
        "ix_query_runs_scope_created", "query_runs", ["user_id", "course_id", "created_at"]
    )
    op.create_index("ix_query_runs_scope_status", "query_runs", ["user_id", "course_id", "status"])

    op.create_table(
        "query_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_query_events_sequence_positive"),
        sa.ForeignKeyConstraint(
            ["query_id", "course_id", "user_id"],
            ["query_runs.id", "query_runs.course_id", "query_runs.user_id"],
            name="fk_query_events_query_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("query_id", "sequence", name="uq_query_events_query_sequence"),
    )
    op.create_index("ix_query_events_query_sequence", "query_events", ["query_id", "sequence"])
    op.create_index("ix_query_events_expires_at", "query_events", ["expires_at"])

    op.create_table(
        "retrieval_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_trace_id", sa.String(length=36), nullable=True),
        sa.Column("active_lexical_index_id", sa.String(length=36), nullable=True),
        sa.Column(
            "active_revision_ids",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "document_epochs",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evidence_payload",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("candidate_count >= 0", name="ck_retrieval_snapshots_candidate_count"),
        sa.ForeignKeyConstraint(
            ["query_id", "course_id", "user_id"],
            ["query_runs.id", "query_runs.course_id", "query_runs.user_id"],
            name="fk_retrieval_snapshots_query_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_trace_id"],
            ["retrieval_traces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("query_id", name="uq_retrieval_snapshots_query"),
        sa.UniqueConstraint("id", "query_id", name="uq_retrieval_snapshots_id_query"),
    )
    op.create_index(
        "ix_retrieval_snapshots_scope_created",
        "retrieval_snapshots",
        ["user_id", "course_id", "created_at"],
    )

    op.create_table(
        "answer_dependencies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id", sa.String(length=255), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=255), nullable=False),
        sa.Column("document_name", sa.String(length=1024), nullable=False),
        sa.Column("document_deletion_epoch", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column(
            "bounding_boxes",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("invalidated_reason", sa.String(length=128), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "document_deletion_epoch >= 0", name="ck_answer_dependencies_deletion_epoch"
        ),
        sa.ForeignKeyConstraint(
            ["query_id", "course_id", "user_id"],
            ["query_runs.id", "query_runs.course_id", "query_runs.user_id"],
            name="fk_answer_dependencies_query_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_snapshot_id", "query_id"],
            ["retrieval_snapshots.id", "retrieval_snapshots.query_id"],
            name="fk_answer_dependencies_snapshot_query",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "query_id", "evidence_id", name="uq_answer_dependencies_query_evidence"
        ),
    )
    op.create_index(
        "ix_answer_dependencies_query", "answer_dependencies", ["query_id", "available"]
    )
    op.create_index(
        "ix_answer_dependencies_document",
        "answer_dependencies",
        ["document_id", "available", "created_at"],
    )

    op.create_table(
        "notes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("section_path", postgresql.JSONB(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("generated_by_model", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_notes_version_positive"),
        sa.CheckConstraint("generation >= 1", name="ck_notes_generation_positive"),
        sa.CheckConstraint("jsonb_array_length(section_path) >= 1", name="ck_notes_section_path"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_notes_title_nonblank"),
        sa.CheckConstraint("btrim(body_markdown) <> ''", name="ck_notes_body_nonblank"),
        sa.CheckConstraint("status IN ('generating', 'ready', 'failed')", name="ck_notes_status"),
        sa.ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_notes_course_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "course_id", "user_id", name="uq_notes_id_scope"),
    )
    op.create_index("ix_notes_scope_updated", "notes", ["user_id", "course_id", "updated_at"])

    op.create_table(
        "note_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("note_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id", sa.String(length=255), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=255), nullable=False),
        sa.Column("document_name", sa.String(length=1024), nullable=False),
        sa.Column("document_deletion_epoch", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column(
            "bounding_boxes",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("unavailable_reason", sa.String(length=128), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("document_deletion_epoch >= 0", name="ck_note_sources_deletion_epoch"),
        sa.ForeignKeyConstraint(
            ["note_id", "course_id", "user_id"],
            ["notes.id", "notes.course_id", "notes.user_id"],
            name="fk_note_sources_note_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("note_id", "evidence_id", name="uq_note_sources_note_evidence"),
    )
    op.create_index("ix_note_sources_note", "note_sources", ["note_id", "available"])
    op.create_index("ix_note_sources_document", "note_sources", ["document_id", "available"])


def downgrade() -> None:
    op.drop_index("ix_note_sources_document", table_name="note_sources")
    op.drop_index("ix_note_sources_note", table_name="note_sources")
    op.drop_table("note_sources")
    op.drop_index("ix_notes_scope_updated", table_name="notes")
    op.drop_table("notes")
    op.drop_index("ix_answer_dependencies_document", table_name="answer_dependencies")
    op.drop_index("ix_answer_dependencies_query", table_name="answer_dependencies")
    op.drop_table("answer_dependencies")
    op.drop_index("ix_retrieval_snapshots_scope_created", table_name="retrieval_snapshots")
    op.drop_table("retrieval_snapshots")
    op.drop_index("ix_query_events_expires_at", table_name="query_events")
    op.drop_index("ix_query_events_query_sequence", table_name="query_events")
    op.drop_table("query_events")
    op.drop_index("ix_query_runs_scope_status", table_name="query_runs")
    op.drop_index("ix_query_runs_scope_created", table_name="query_runs")
    op.drop_table("query_runs")
