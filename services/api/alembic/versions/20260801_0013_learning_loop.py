"""add source-bound active recall and review queue persistence

Revision ID: 20260801_0013
Revises: 20260724_0012
Create Date: 2026-08-01 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_0013"
down_revision: str | None = "20260724_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HASH = "~ '^[0-9a-f]{64}$'"


def _scope_fk(
    name: str,
    columns: list[str],
    referred: list[str],
    *,
    ondelete: str,
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(columns, referred, name=name, ondelete=ondelete)


def upgrade() -> None:
    op.create_table(
        "learning_units",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("canonical_key", sa.String(255), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("parent_id", sa.String(36)),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'available'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _scope_fk(
            "fk_learning_units_course_user",
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            ondelete="CASCADE",
        ),
        _scope_fk(
            "fk_learning_units_parent_scope",
            ["parent_id", "course_id", "user_id"],
            ["learning_units.id", "learning_units.course_id", "learning_units.user_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "course_id", "user_id", name="uq_learning_units_id_scope"),
        sa.UniqueConstraint(
            "course_id", "user_id", "canonical_key", name="uq_learning_units_course_key"
        ),
        sa.CheckConstraint("kind IN ('section', 'concept')", name="ck_learning_units_kind"),
        sa.CheckConstraint(
            "status IN ('available', 'unavailable', 'stale')", name="ck_learning_units_status"
        ),
    )
    op.create_index(
        "ix_learning_units_scope_status", "learning_units", ["user_id", "course_id", "status"]
    )

    op.create_table(
        "learning_unit_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("unit_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("chunk_id", sa.String(255), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("locator", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'valid'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _scope_fk(
            "fk_learning_unit_sources_unit_scope",
            ["unit_id", "course_id", "user_id"],
            ["learning_units.id", "learning_units.course_id", "learning_units.user_id"],
            ondelete="CASCADE",
        ),
        _scope_fk(
            "fk_learning_unit_sources_document_scope",
            ["document_id", "course_id", "user_id"],
            ["documents.id", "documents.course_id", "documents.user_id"],
            ondelete="CASCADE",
        ),
        _scope_fk(
            "fk_learning_unit_sources_revision_scope",
            ["document_id", "revision_id"],
            ["document_revisions.document_id", "document_revisions.id"],
            ondelete="CASCADE",
        ),
        _scope_fk(
            "fk_learning_unit_sources_chunk_scope",
            ["chunk_id", "revision_id"],
            ["revision_chunks.id", "revision_chunks.revision_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "unit_id",
            "course_id",
            "user_id",
            "document_id",
            "revision_id",
            "chunk_id",
            name="uq_learning_unit_sources_binding",
        ),
        sa.CheckConstraint(
            "status IN ('valid', 'stale', 'unavailable')", name="ck_learning_unit_sources_status"
        ),
        sa.CheckConstraint(f"content_sha256 {_HASH}", name="ck_learning_unit_sources_hash"),
    )
    op.create_index(
        "ix_learning_unit_sources_scope_status",
        "learning_unit_sources",
        ["user_id", "course_id", "status"],
    )

    op.create_table(
        "practice_questions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("learning_unit_id", sa.String(36), nullable=False),
        sa.Column("source_revision_id", sa.String(36), nullable=False),
        sa.Column("question_type", sa.String(20), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("options", postgresql.JSONB, nullable=False),
        sa.Column("correct_answer", sa.String(32), nullable=False),
        sa.Column("explanation", sa.Text, nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB, nullable=False),
        sa.Column("difficulty", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ready'")),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _scope_fk(
            "fk_practice_questions_unit_scope",
            ["learning_unit_id", "course_id", "user_id"],
            ["learning_units.id", "learning_units.course_id", "learning_units.user_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("id", "course_id", "user_id", name="uq_practice_questions_id_scope"),
        sa.CheckConstraint(
            "question_type IN ('single_choice', 'true_false')", name="ck_practice_questions_type"
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'stale', 'invalid')", name="ck_practice_questions_status"
        ),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 3", name="ck_practice_questions_difficulty"),
        sa.CheckConstraint(f"content_sha256 {_HASH}", name="ck_practice_questions_hash"),
    )
    op.create_index(
        "ix_practice_questions_scope_status",
        "practice_questions",
        ["user_id", "course_id", "status"],
    )

    op.create_table(
        "practice_question_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("question_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("chunk_id", sa.String(255), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("locator", postgresql.JSONB, nullable=False),
        sa.Column("quote", sa.Text, nullable=False),
        _scope_fk(
            "fk_practice_question_evidence_question_scope",
            ["question_id", "course_id", "user_id"],
            ["practice_questions.id", "practice_questions.course_id", "practice_questions.user_id"],
            ondelete="CASCADE",
        ),
        _scope_fk(
            "fk_practice_question_evidence_document_scope",
            ["document_id", "course_id", "user_id"],
            ["documents.id", "documents.course_id", "documents.user_id"],
            ondelete="CASCADE",
        ),
        _scope_fk(
            "fk_practice_question_evidence_revision_scope",
            ["document_id", "revision_id"],
            ["document_revisions.document_id", "document_revisions.id"],
            ondelete="CASCADE",
        ),
        _scope_fk(
            "fk_practice_question_evidence_chunk_scope",
            ["chunk_id", "revision_id"],
            ["revision_chunks.id", "revision_chunks.revision_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "question_id",
            "course_id",
            "user_id",
            "ordinal",
            name="uq_practice_question_evidence_order",
        ),
        sa.CheckConstraint("ordinal >= 1", name="ck_practice_question_evidence_ordinal"),
        sa.CheckConstraint(f"content_sha256 {_HASH}", name="ck_practice_question_evidence_hash"),
    )

    op.create_table(
        "practice_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("learning_unit_ids", postgresql.JSONB, nullable=False),
        sa.Column("target_question_count", sa.Integer, nullable=False),
        sa.Column("total_items", sa.Integer, nullable=False),
        sa.Column("completed_items", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("phase", sa.String(32)),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state_version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("runner_id", sa.String(128)),
        sa.Column("failure_code", sa.String(128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        _scope_fk(
            "fk_practice_batches_course_user",
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "course_id", "user_id", name="uq_practice_batches_id_scope"),
        sa.UniqueConstraint(
            "user_id", "course_id", "idempotency_key_hash", name="uq_practice_batches_idempotency"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'partial_success', 'succeeded', 'failed', "
            "'cancelled')",
            name="ck_practice_batches_status",
        ),
        sa.CheckConstraint(
            "phase IS NULL OR phase IN ('validating_inputs', 'generating', "
            "'validating_output', 'saving')",
            name="ck_practice_batches_phase",
        ),
        sa.CheckConstraint(
            "target_question_count BETWEEN 1 AND 10", name="ck_practice_batches_target"
        ),
        sa.CheckConstraint(
            "completed_items >= 0 AND completed_items <= total_items",
            name="ck_practice_batches_completed",
        ),
        sa.CheckConstraint("total_items BETWEEN 1 AND 10", name="ck_practice_batches_total"),
        sa.CheckConstraint(f"idempotency_key_hash {_HASH}", name="ck_practice_batches_key_hash"),
        sa.CheckConstraint(f"request_hash {_HASH}", name="ck_practice_batches_request_hash"),
        sa.CheckConstraint(
            "(status IN ('partial_success', 'succeeded', 'failed', 'cancelled') "
            "AND completed_at IS NOT NULL) OR "
            "(status IN ('queued', 'running') AND completed_at IS NULL)",
            name="ck_practice_batches_terminal_time",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_practice_batches_attempt_count"),
    )
    op.create_index(
        "ix_practice_batches_claim",
        "practice_batches",
        ["status", "lease_expires_at", "created_at"],
    )
    op.create_index(
        "ix_practice_batches_scope_status", "practice_batches", ["user_id", "course_id", "status"]
    )

    op.create_table(
        "practice_batch_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("question_id", sa.String(36)),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("failure_code", sa.String(128)),
        sa.Column("provider", sa.String(64)),
        sa.Column("model", sa.String(255)),
        sa.Column("duration_ms", sa.Integer),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _scope_fk(
            "fk_practice_batch_items_batch_scope",
            ["batch_id", "course_id", "user_id"],
            ["practice_batches.id", "practice_batches.course_id", "practice_batches.user_id"],
            ondelete="CASCADE",
        ),
        _scope_fk(
            "fk_practice_batch_items_question_scope",
            ["question_id", "course_id", "user_id"],
            ["practice_questions.id", "practice_questions.course_id", "practice_questions.user_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_practice_batch_items_id_scope"
        ),
        sa.UniqueConstraint(
            "batch_id", "course_id", "user_id", "ordinal", name="uq_practice_batch_items_order"
        ),
        sa.CheckConstraint(
            "ordinal >= 1 AND ordinal <= 10", name="ck_practice_batch_items_ordinal"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'succeeded', 'failed')", name="ck_practice_batch_items_status"
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_practice_batch_items_attempt_count"),
    )

    op.create_table(
        "practice_batch_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("attempt_number", sa.Integer, nullable=False),
        sa.Column("provider", sa.String(64)),
        sa.Column("model", sa.String(255)),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("error_code", sa.String(128)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _scope_fk(
            "fk_practice_batch_attempts_item_scope",
            ["item_id", "batch_id", "course_id", "user_id"],
            [
                "practice_batch_items.id",
                "practice_batch_items.batch_id",
                "practice_batch_items.course_id",
                "practice_batch_items.user_id",
            ],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("item_id", "attempt_number", name="uq_practice_batch_attempts_number"),
        sa.CheckConstraint("attempt_number >= 1", name="ck_practice_batch_attempts_number"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_practice_batch_attempts_duration"
        ),
    )

    op.create_table(
        "practice_batch_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("data", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _scope_fk(
            "fk_practice_batch_events_batch_scope",
            ["batch_id", "course_id", "user_id"],
            ["practice_batches.id", "practice_batches.course_id", "practice_batches.user_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "batch_id", "course_id", "user_id", "sequence", name="uq_practice_batch_events_sequence"
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_practice_batch_events_sequence"),
    )

    op.create_table(
        "practice_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("learning_unit_ids", postgresql.JSONB, nullable=False),
        sa.Column("question_count", sa.Integer, nullable=False),
        sa.Column("mode", sa.String(32), nullable=False, server_default=sa.text("'practice'")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        _scope_fk(
            "fk_practice_sessions_course_user",
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "course_id", "user_id", name="uq_practice_sessions_id_scope"),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'cancelled')", name="ck_practice_sessions_status"
        ),
        sa.CheckConstraint(
            "question_count BETWEEN 1 AND 10", name="ck_practice_sessions_question_count"
        ),
        sa.CheckConstraint(
            "(status = 'active' AND completed_at IS NULL) OR "
            "(status IN ('completed', 'cancelled') AND completed_at IS NOT NULL)",
            name="ck_practice_sessions_terminal_time",
        ),
    )
    op.create_index(
        "ix_practice_sessions_scope_status", "practice_sessions", ["user_id", "course_id", "status"]
    )

    op.create_table(
        "practice_session_questions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("question_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        _scope_fk(
            "fk_practice_session_questions_session_scope",
            ["session_id", "course_id", "user_id"],
            ["practice_sessions.id", "practice_sessions.course_id", "practice_sessions.user_id"],
            ondelete="CASCADE",
        ),
        _scope_fk(
            "fk_practice_session_questions_question_scope",
            ["question_id", "course_id", "user_id"],
            ["practice_questions.id", "practice_questions.course_id", "practice_questions.user_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "session_id",
            "course_id",
            "user_id",
            "question_id",
            name="uq_practice_session_questions_question",
        ),
        sa.UniqueConstraint(
            "session_id",
            "course_id",
            "user_id",
            "ordinal",
            name="uq_practice_session_questions_order",
        ),
        sa.CheckConstraint(
            "ordinal >= 1 AND ordinal <= 10", name="ck_practice_session_questions_ordinal"
        ),
    )

    op.create_table(
        "practice_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("question_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("answer", sa.String(32), nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("correct", sa.Boolean, nullable=False),
        sa.Column("viewed_hint", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("elapsed_ms", sa.Integer),
        sa.Column(
            "previous_mastery_level",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'new'"),
        ),
        sa.Column(
            "mastery_level",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'new'"),
        ),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("feedback", sa.Text, nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB, nullable=False),
        sa.Column(
            "answered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _scope_fk(
            "fk_practice_attempts_session_scope",
            ["session_id", "course_id", "user_id"],
            ["practice_sessions.id", "practice_sessions.course_id", "practice_sessions.user_id"],
            ondelete="CASCADE",
        ),
        _scope_fk(
            "fk_practice_attempts_question_scope",
            ["question_id", "course_id", "user_id"],
            ["practice_questions.id", "practice_questions.course_id", "practice_questions.user_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "session_id",
            "course_id",
            "user_id",
            "question_id",
            name="uq_practice_attempts_question",
        ),
        sa.UniqueConstraint(
            "session_id",
            "course_id",
            "user_id",
            "idempotency_key_hash",
            name="uq_practice_attempts_idempotency",
        ),
        sa.CheckConstraint(f"idempotency_key_hash {_HASH}", name="ck_practice_attempts_key_hash"),
        sa.CheckConstraint("score IN (0, 1)", name="ck_practice_attempts_score"),
        sa.CheckConstraint(
            "elapsed_ms IS NULL OR elapsed_ms >= 0", name="ck_practice_attempts_elapsed"
        ),
        sa.CheckConstraint(
            "previous_mastery_level IN ('new', 'learning', 'review', 'mastered')",
            name="ck_practice_attempts_previous_mastery",
        ),
        sa.CheckConstraint(
            "mastery_level IN ('new', 'learning', 'review', 'mastered')",
            name="ck_practice_attempts_mastery",
        ),
        sa.CheckConstraint(
            "next_review_at >= answered_at", name="ck_practice_attempts_review_time"
        ),
    )
    op.create_index(
        "ix_practice_attempts_scope_answered",
        "practice_attempts",
        ["user_id", "course_id", "answered_at"],
    )

    op.create_table(
        "learning_mastery",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(36), nullable=False),
        sa.Column("learning_unit_id", sa.String(36), nullable=False),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("correct_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_score", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("mastery_level", sa.String(16), nullable=False, server_default=sa.text("'new'")),
        sa.Column("next_review_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _scope_fk(
            "fk_learning_mastery_unit_scope",
            ["learning_unit_id", "course_id", "user_id"],
            ["learning_units.id", "learning_units.course_id", "learning_units.user_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "learning_unit_id", "course_id", "user_id", name="uq_learning_mastery_unit_scope"
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_learning_mastery_attempts"),
        sa.CheckConstraint(
            "correct_count >= 0 AND correct_count <= attempt_count",
            name="ck_learning_mastery_correct",
        ),
        sa.CheckConstraint("last_score IN (0, 1)", name="ck_learning_mastery_last_score"),
        sa.CheckConstraint(
            "mastery_level IN ('new', 'learning', 'review', 'mastered')",
            name="ck_learning_mastery_level",
        ),
        sa.CheckConstraint(
            "next_review_at IS NULL OR last_attempt_at IS NULL OR "
            "next_review_at >= last_attempt_at",
            name="ck_learning_mastery_review_time",
        ),
    )
    op.create_index(
        "ix_learning_mastery_review_queue",
        "learning_mastery",
        ["user_id", "course_id", "next_review_at", "mastery_level"],
    )


def downgrade() -> None:
    op.drop_index("ix_learning_mastery_review_queue", table_name="learning_mastery", if_exists=True)
    op.drop_table("learning_mastery")
    op.drop_index(
        "ix_practice_attempts_scope_answered", table_name="practice_attempts", if_exists=True
    )
    op.drop_table("practice_attempts")
    op.drop_table("practice_session_questions")
    op.drop_index(
        "ix_practice_sessions_scope_status", table_name="practice_sessions", if_exists=True
    )
    op.drop_table("practice_sessions")
    op.drop_table("practice_batch_events")
    op.drop_table("practice_batch_attempts")
    op.drop_table("practice_batch_items")
    op.drop_index("ix_practice_batches_scope_status", table_name="practice_batches", if_exists=True)
    op.drop_index("ix_practice_batches_claim", table_name="practice_batches", if_exists=True)
    op.drop_table("practice_batches")
    op.drop_table("practice_question_evidence")
    op.drop_index(
        "ix_practice_questions_scope_status", table_name="practice_questions", if_exists=True
    )
    op.drop_table("practice_questions")
    op.drop_index(
        "ix_learning_unit_sources_scope_status", table_name="learning_unit_sources", if_exists=True
    )
    op.drop_table("learning_unit_sources")
    op.drop_index("ix_learning_units_scope_status", table_name="learning_units", if_exists=True)
    op.drop_table("learning_units")
