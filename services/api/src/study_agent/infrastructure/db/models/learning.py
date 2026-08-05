"""Durable source-bound learning units, practice control-plane rows and mastery."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from study_agent.infrastructure.db.base import Base
from study_agent.infrastructure.db.models.core import new_id

_HASH_CHECK = "~ '^[0-9a-f]{64}$'"


class LearningUnitModel(Base):
    __tablename__ = "learning_units"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_learning_units_course_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["parent_id", "course_id", "user_id"],
            ["learning_units.id", "learning_units.course_id", "learning_units.user_id"],
            name="fk_learning_units_parent_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "course_id", "user_id", name="uq_learning_units_id_scope"),
        UniqueConstraint(
            "course_id", "user_id", "canonical_key", name="uq_learning_units_course_key"
        ),
        CheckConstraint("kind IN ('section', 'concept')", name="ck_learning_units_kind"),
        CheckConstraint(
            "status IN ('available', 'unavailable', 'stale')", name="ck_learning_units_status"
        ),
        Index("ix_learning_units_scope_status", "user_id", "course_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="available")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class LearningUnitSourceModel(Base):
    __tablename__ = "learning_unit_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["unit_id", "course_id", "user_id"],
            ["learning_units.id", "learning_units.course_id", "learning_units.user_id"],
            name="fk_learning_unit_sources_unit_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "course_id", "user_id"],
            ["documents.id", "documents.course_id", "documents.user_id"],
            name="fk_learning_unit_sources_document_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "revision_id"],
            ["document_revisions.document_id", "document_revisions.id"],
            name="fk_learning_unit_sources_revision_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["chunk_id", "revision_id"],
            ["revision_chunks.id", "revision_chunks.revision_id"],
            name="fk_learning_unit_sources_chunk_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "unit_id",
            "course_id",
            "user_id",
            "document_id",
            "revision_id",
            "chunk_id",
            name="uq_learning_unit_sources_binding",
        ),
        UniqueConstraint("id", "course_id", "user_id", name="uq_learning_unit_sources_id_scope"),
        CheckConstraint(
            "status IN ('valid', 'stale', 'unavailable')", name="ck_learning_unit_sources_status"
        ),
        CheckConstraint(f"content_sha256 {_HASH_CHECK}", name="ck_learning_unit_sources_hash"),
        Index("ix_learning_unit_sources_scope_status", "user_id", "course_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="valid")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LearningUnitEvidenceSupplementModel(Base):
    __tablename__ = "learning_unit_evidence_supplements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["unit_id", "course_id", "user_id"],
            ["learning_units.id", "learning_units.course_id", "learning_units.user_id"],
            name="fk_learning_evidence_supplements_unit_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_id", "course_id", "user_id"],
            [
                "learning_unit_sources.id",
                "learning_unit_sources.course_id",
                "learning_unit_sources.user_id",
            ],
            name="fk_learning_evidence_supplements_source_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id", "course_id", "user_id", name="uq_learning_evidence_supplements_id_scope"
        ),
        CheckConstraint(
            "role IN ('complete_prototype', 'reference_solution', 'additional_context')",
            name="ck_learning_evidence_supplements_role",
        ),
        CheckConstraint(
            "status IN ('active', 'superseded', 'revoked')",
            name="ck_learning_evidence_supplements_status",
        ),
        CheckConstraint("btrim(text) <> ''", name="ck_learning_evidence_supplements_text_nonblank"),
        CheckConstraint(
            f"content_sha256 {_HASH_CHECK}", name="ck_learning_evidence_supplements_hash"
        ),
        CheckConstraint(
            f"source_content_sha256 {_HASH_CHECK}",
            name="ck_learning_evidence_supplements_source_hash",
        ),
        Index(
            "uq_learning_evidence_supplements_active_unit",
            "user_id",
            "course_id",
            "unit_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_learning_evidence_supplements_scope_status",
            "user_id",
            "course_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PracticeQuestionModel(Base):
    __tablename__ = "practice_questions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["learning_unit_id", "course_id", "user_id"],
            ["learning_units.id", "learning_units.course_id", "learning_units.user_id"],
            name="fk_practice_questions_unit_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "course_id", "user_id", name="uq_practice_questions_id_scope"),
        CheckConstraint(
            "question_type IN ('single_choice', 'true_false', 'short_answer', 'calculation')",
            name="ck_practice_questions_type",
        ),
        CheckConstraint(
            "status IN ('ready', 'stale', 'invalid')", name="ck_practice_questions_status"
        ),
        CheckConstraint("difficulty BETWEEN 1 AND 3", name="ck_practice_questions_difficulty"),
        CheckConstraint(f"content_sha256 {_HASH_CHECK}", name="ck_practice_questions_hash"),
        Index("ix_practice_questions_scope_status", "user_id", "course_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    learning_unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    question_type: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    correct_answer: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PracticeQuestionEvidenceModel(Base):
    __tablename__ = "practice_question_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_id", "course_id", "user_id"],
            ["practice_questions.id", "practice_questions.course_id", "practice_questions.user_id"],
            name="fk_practice_question_evidence_question_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "course_id", "user_id"],
            ["documents.id", "documents.course_id", "documents.user_id"],
            name="fk_practice_question_evidence_document_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "revision_id"],
            ["document_revisions.document_id", "document_revisions.id"],
            name="fk_practice_question_evidence_revision_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["chunk_id", "revision_id"],
            ["revision_chunks.id", "revision_chunks.revision_id"],
            name="fk_practice_question_evidence_chunk_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["supplement_id", "course_id", "user_id"],
            [
                "learning_unit_evidence_supplements.id",
                "learning_unit_evidence_supplements.course_id",
                "learning_unit_evidence_supplements.user_id",
            ],
            name="fk_practice_question_evidence_supplement_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "question_id",
            "course_id",
            "user_id",
            "ordinal",
            name="uq_practice_question_evidence_order",
        ),
        CheckConstraint("ordinal >= 1", name="ck_practice_question_evidence_ordinal"),
        CheckConstraint(f"content_sha256 {_HASH_CHECK}", name="ck_practice_question_evidence_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    question_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(255), nullable=False)
    supplement_id: Mapped[str | None] = mapped_column(String(36))
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)


class PracticeBatchModel(Base):
    __tablename__ = "practice_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_practice_batches_course_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "course_id", "user_id", name="uq_practice_batches_id_scope"),
        UniqueConstraint(
            "user_id", "course_id", "idempotency_key_hash", name="uq_practice_batches_idempotency"
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'partial_success', 'succeeded', 'failed', "
            "'cancelled')",
            name="ck_practice_batches_status",
        ),
        CheckConstraint(
            "phase IS NULL OR phase IN ('validating_inputs', 'generating', "
            "'validating_output', 'saving')",
            name="ck_practice_batches_phase",
        ),
        CheckConstraint(
            "target_question_count BETWEEN 1 AND 10", name="ck_practice_batches_target"
        ),
        CheckConstraint(
            "completed_items >= 0 AND completed_items <= total_items",
            name="ck_practice_batches_completed",
        ),
        CheckConstraint("total_items BETWEEN 1 AND 10", name="ck_practice_batches_total"),
        CheckConstraint(f"idempotency_key_hash {_HASH_CHECK}", name="ck_practice_batches_key_hash"),
        CheckConstraint(f"request_hash {_HASH_CHECK}", name="ck_practice_batches_request_hash"),
        CheckConstraint(
            "(status IN ('partial_success', 'succeeded', 'failed', 'cancelled') "
            "AND completed_at IS NOT NULL) OR "
            "(status IN ('queued', 'running') AND completed_at IS NULL)",
            name="ck_practice_batches_terminal_time",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_practice_batches_attempt_count"),
        Index("ix_practice_batches_claim", "status", "lease_expires_at", "created_at"),
        Index("ix_practice_batches_scope_status", "user_id", "course_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    learning_unit_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    target_question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_items: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    phase: Mapped[str | None] = mapped_column(String(32))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runner_id: Mapped[str | None] = mapped_column(String(128))
    failure_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PracticeBatchItemModel(Base):
    __tablename__ = "practice_batch_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "course_id", "user_id"],
            ["practice_batches.id", "practice_batches.course_id", "practice_batches.user_id"],
            name="fk_practice_batch_items_batch_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["question_id", "course_id", "user_id"],
            ["practice_questions.id", "practice_questions.course_id", "practice_questions.user_id"],
            name="fk_practice_batch_items_question_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id", "batch_id", "course_id", "user_id", name="uq_practice_batch_items_id_scope"
        ),
        UniqueConstraint(
            "batch_id", "course_id", "user_id", "ordinal", name="uq_practice_batch_items_order"
        ),
        CheckConstraint("ordinal >= 1 AND ordinal <= 10", name="ck_practice_batch_items_ordinal"),
        CheckConstraint(
            "status IN ('queued', 'succeeded', 'failed')", name="ck_practice_batch_items_status"
        ),
        CheckConstraint("attempt_count >= 0", name="ck_practice_batch_items_attempt_count"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    question_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(128))
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(255))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PracticeBatchAttemptModel(Base):
    __tablename__ = "practice_batch_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "batch_id", "course_id", "user_id"],
            [
                "practice_batch_items.id",
                "practice_batch_items.batch_id",
                "practice_batch_items.course_id",
                "practice_batch_items.user_id",
            ],
            name="fk_practice_batch_attempts_item_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint("item_id", "attempt_number", name="uq_practice_batch_attempts_number"),
        CheckConstraint("attempt_number >= 1", name="ck_practice_batch_attempts_number"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_practice_batch_attempts_duration"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(255))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PracticeBatchEventModel(Base):
    __tablename__ = "practice_batch_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id", "course_id", "user_id"],
            ["practice_batches.id", "practice_batches.course_id", "practice_batches.user_id"],
            name="fk_practice_batch_events_batch_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "batch_id", "course_id", "user_id", "sequence", name="uq_practice_batch_events_sequence"
        ),
        CheckConstraint("sequence >= 1", name="ck_practice_batch_events_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PracticeSessionModel(Base):
    __tablename__ = "practice_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_practice_sessions_course_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "course_id", "user_id", name="uq_practice_sessions_id_scope"),
        CheckConstraint(
            "status IN ('active', 'completed', 'cancelled')", name="ck_practice_sessions_status"
        ),
        CheckConstraint(
            "question_count BETWEEN 1 AND 10", name="ck_practice_sessions_question_count"
        ),
        CheckConstraint(
            "(status = 'active' AND completed_at IS NULL) OR "
            "(status IN ('completed', 'cancelled') AND completed_at IS NOT NULL)",
            name="ck_practice_sessions_terminal_time",
        ),
        Index("ix_practice_sessions_scope_status", "user_id", "course_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    learning_unit_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="practice")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PracticeSessionQuestionModel(Base):
    __tablename__ = "practice_session_questions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "course_id", "user_id"],
            ["practice_sessions.id", "practice_sessions.course_id", "practice_sessions.user_id"],
            name="fk_practice_session_questions_session_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["question_id", "course_id", "user_id"],
            ["practice_questions.id", "practice_questions.course_id", "practice_questions.user_id"],
            name="fk_practice_session_questions_question_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "session_id",
            "course_id",
            "user_id",
            "question_id",
            name="uq_practice_session_questions_question",
        ),
        UniqueConstraint(
            "session_id",
            "course_id",
            "user_id",
            "ordinal",
            name="uq_practice_session_questions_order",
        ),
        CheckConstraint(
            "ordinal >= 1 AND ordinal <= 10", name="ck_practice_session_questions_ordinal"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    question_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class PracticeAttemptModel(Base):
    __tablename__ = "practice_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "course_id", "user_id"],
            ["practice_sessions.id", "practice_sessions.course_id", "practice_sessions.user_id"],
            name="fk_practice_attempts_session_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["question_id", "course_id", "user_id"],
            ["practice_questions.id", "practice_questions.course_id", "practice_questions.user_id"],
            name="fk_practice_attempts_question_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "session_id",
            "course_id",
            "user_id",
            "question_id",
            name="uq_practice_attempts_question",
        ),
        UniqueConstraint(
            "session_id",
            "course_id",
            "user_id",
            "idempotency_key_hash",
            name="uq_practice_attempts_idempotency",
        ),
        CheckConstraint(
            f"idempotency_key_hash {_HASH_CHECK}", name="ck_practice_attempts_key_hash"
        ),
        CheckConstraint("score IN (0, 1)", name="ck_practice_attempts_score"),
        CheckConstraint(
            "elapsed_ms IS NULL OR elapsed_ms >= 0", name="ck_practice_attempts_elapsed"
        ),
        CheckConstraint(
            "previous_mastery_level IN ('new', 'learning', 'review', 'mastered')",
            name="ck_practice_attempts_previous_mastery",
        ),
        CheckConstraint(
            "mastery_level IN ('new', 'learning', 'review', 'mastered')",
            name="ck_practice_attempts_mastery",
        ),
        CheckConstraint("next_review_at >= answered_at", name="ck_practice_attempts_review_time"),
        Index("ix_practice_attempts_scope_answered", "user_id", "course_id", "answered_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    question_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    viewed_hint: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    previous_mastery_level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="new", server_default=text("'new'")
    )
    mastery_level: Mapped[str] = mapped_column(
        String(16), nullable=False, default="new", server_default=text("'new'")
    )
    next_review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feedback: Mapped[str] = mapped_column(Text, nullable=False)
    grading_feedback: Mapped[str | None] = mapped_column(Text)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LearningMasteryModel(Base):
    __tablename__ = "learning_mastery"
    __table_args__ = (
        ForeignKeyConstraint(
            ["learning_unit_id", "course_id", "user_id"],
            ["learning_units.id", "learning_units.course_id", "learning_units.user_id"],
            name="fk_learning_mastery_unit_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "learning_unit_id", "course_id", "user_id", name="uq_learning_mastery_unit_scope"
        ),
        CheckConstraint("attempt_count >= 0", name="ck_learning_mastery_attempts"),
        CheckConstraint(
            "correct_count >= 0 AND correct_count <= attempt_count",
            name="ck_learning_mastery_correct",
        ),
        CheckConstraint("last_score IN (0, 1)", name="ck_learning_mastery_last_score"),
        CheckConstraint(
            "mastery_level IN ('new', 'learning', 'review', 'mastered')",
            name="ck_learning_mastery_level",
        ),
        CheckConstraint(
            "next_review_at IS NULL OR last_attempt_at IS NULL OR "
            "next_review_at >= last_attempt_at",
            name="ck_learning_mastery_review_time",
        ),
        Index(
            "ix_learning_mastery_review_queue",
            "user_id",
            "course_id",
            "next_review_at",
            "mastery_level",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    learning_unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mastery_level: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
