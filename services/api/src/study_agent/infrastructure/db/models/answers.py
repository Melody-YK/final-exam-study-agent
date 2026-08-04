"""Persistence for query execution, retrieval snapshots, and answer dependencies."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
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


class ConversationModel(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_conversations_course_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["practice_session_id", "course_id", "user_id"],
            ["practice_sessions.id", "practice_sessions.course_id", "practice_sessions.user_id"],
            name="fk_conversations_practice_session_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["practice_question_id", "course_id", "user_id"],
            ["practice_questions.id", "practice_questions.course_id", "practice_questions.user_id"],
            name="fk_conversations_practice_question_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "course_id", "user_id", name="uq_conversations_id_scope"),
        UniqueConstraint(
            "user_id",
            "course_id",
            "conversation_type",
            "practice_session_id",
            "practice_question_id",
            name="uq_conversations_practice_scope",
        ),
        CheckConstraint("btrim(title) <> ''", name="ck_conversations_title_nonblank"),
        CheckConstraint(
            "summary_turn_count >= 0",
            name="ck_conversations_summary_turn_count",
        ),
        CheckConstraint(
            "conversation_type IN ('course_qa', 'practice_tutor')",
            name="ck_conversations_type",
        ),
        CheckConstraint(
            "(conversation_type = 'course_qa' AND practice_session_id IS NULL "
            "AND practice_question_id IS NULL) OR "
            "(conversation_type = 'practice_tutor' AND practice_session_id IS NOT NULL "
            "AND practice_question_id IS NOT NULL)",
            name="ck_conversations_subject_scope",
        ),
        Index(
            "ix_conversations_scope_updated",
            "user_id",
            "course_id",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    conversation_type: Mapped[str] = mapped_column(String(32), nullable=False, default="course_qa")
    practice_session_id: Mapped[str | None] = mapped_column(String(36))
    practice_question_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    auto_title_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    summary_text: Mapped[str | None] = mapped_column(Text)
    summary_version: Mapped[str | None] = mapped_column(String(32))
    summary_turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ConversationMessageModel(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "course_id", "user_id"],
            ["conversations.id", "conversations.course_id", "conversations.user_id"],
            name="fk_conversation_messages_conversation_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint("conversation_id", "sequence", name="uq_conversation_messages_sequence"),
        UniqueConstraint(
            "conversation_id",
            "turn_id",
            "role",
            name="uq_conversation_messages_turn_role",
        ),
        CheckConstraint("sequence >= 1", name="ck_conversation_messages_sequence"),
        CheckConstraint("btrim(turn_id) <> ''", name="ck_conversation_messages_turn_nonblank"),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_conversation_messages_role"),
        CheckConstraint(
            "intent IS NULL OR intent IN "
            "('hint', 'clarify', 'example', 'answer_check', 'solution', 'reflection', 'source')",
            name="ck_conversation_messages_intent",
        ),
        CheckConstraint(
            "mode IS NULL OR mode IN ('hint', 'review')",
            name="ck_conversation_messages_mode",
        ),
        CheckConstraint("btrim(content) <> ''", name="ck_conversation_messages_content_nonblank"),
        Index(
            "ix_conversation_messages_scope_sequence",
            "user_id",
            "course_id",
            "conversation_id",
            "sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    turn_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(32))
    mode: Mapped[str | None] = mapped_column(String(16))
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QueryRunModel(Base):
    __tablename__ = "query_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_query_runs_course_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "course_id", "user_id"],
            ["conversations.id", "conversations.course_id", "conversations.user_id"],
            name="fk_query_runs_conversation_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "course_id", "user_id", name="uq_query_runs_id_scope"),
        CheckConstraint("event_sequence >= 0", name="ck_query_runs_event_sequence"),
        CheckConstraint(
            "status IN ('pending', 'retrieving', 'generating', 'answered', "
            "'abstained', 'failed', 'invalidated')",
            name="ck_query_runs_status",
        ),
        CheckConstraint(
            "cost_microusd IS NULL OR cost_microusd >= 0",
            name="ck_query_runs_cost_nonnegative",
        ),
        CheckConstraint(
            "query_intent IS NULL OR query_intent IN "
            "('new_question', 'follow_up', 'comparison', 'summary', 'clarification')",
            name="ck_query_runs_intent",
        ),
        CheckConstraint(
            "retrieval_diagnostic IS NULL OR retrieval_diagnostic IN "
            "('initial_sufficient', 'repair_succeeded', 'index_unavailable', "
            "'no_candidates', 'low_relevance')",
            name="ck_query_runs_retrieval_diagnostic",
        ),
        Index("ix_query_runs_scope_created", "user_id", "course_id", "created_at"),
        Index("ix_query_runs_scope_status", "user_id", "course_id", "status"),
        Index(
            "ix_query_runs_conversation_created",
            "user_id",
            "course_id",
            "conversation_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    query_intent: Mapped[str | None] = mapped_column(String(32))
    standalone_question: Mapped[str | None] = mapped_column(Text)
    requested_document_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    retrieval_rounds: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    retrieval_diagnostic: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    answer_schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0")
    answer_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    claims: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    refusal_code: Mapped[str | None] = mapped_column(String(128))
    refusal_message: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(String(128))
    provider_alias: Mapped[str | None] = mapped_column(String(64))
    provider_model: Mapped[str | None] = mapped_column(String(255))
    provider_response_id: Mapped[str | None] = mapped_column(String(255))
    usage: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    cost_microusd: Mapped[int | None] = mapped_column(BigInteger)
    trace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LearnerMemoryModel(Base):
    __tablename__ = "learner_memories"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_learner_memories_course_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "user_id",
            "course_id",
            "memory_type",
            "content_sha256",
            name="uq_learner_memories_content",
        ),
        CheckConstraint(
            "memory_type IN ('preference', 'confirmed_misconception', 'learning_goal')",
            name="ck_learner_memories_type",
        ),
        CheckConstraint(
            "source_kind IN ('explicit_user', 'manual')",
            name="ck_learner_memories_source_kind",
        ),
        CheckConstraint("btrim(content) <> ''", name="ck_learner_memories_content_nonblank"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_learner_memories_confidence",
        ),
        CheckConstraint(
            "source_message_id IS NULL OR source_query_id IS NULL",
            name="ck_learner_memories_single_source",
        ),
        Index(
            "ix_learner_memories_scope_updated",
            "user_id",
            "course_id",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False, default=1.0)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="SET NULL")
    )
    source_query_id: Mapped[str | None] = mapped_column(
        ForeignKey("query_runs.id", ondelete="SET NULL")
    )
    last_confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class QueryEventModel(Base):
    __tablename__ = "query_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["query_id", "course_id", "user_id"],
            ["query_runs.id", "query_runs.course_id", "query_runs.user_id"],
            name="fk_query_events_query_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint("query_id", "sequence", name="uq_query_events_query_sequence"),
        CheckConstraint("sequence >= 1", name="ck_query_events_sequence_positive"),
        Index("ix_query_events_query_sequence", "query_id", "sequence"),
        Index("ix_query_events_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    query_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RetrievalSnapshotModel(Base):
    __tablename__ = "retrieval_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["query_id", "course_id", "user_id"],
            ["query_runs.id", "query_runs.course_id", "query_runs.user_id"],
            name="fk_retrieval_snapshots_query_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint("query_id", name="uq_retrieval_snapshots_query"),
        UniqueConstraint("id", "query_id", name="uq_retrieval_snapshots_id_query"),
        CheckConstraint("candidate_count >= 0", name="ck_retrieval_snapshots_candidate_count"),
        Index("ix_retrieval_snapshots_scope_created", "user_id", "course_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    query_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    retrieval_trace_id: Mapped[str | None] = mapped_column(
        ForeignKey("retrieval_traces.id", ondelete="SET NULL")
    )
    active_lexical_index_id: Mapped[str | None] = mapped_column(String(36))
    active_revision_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    document_epochs: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_payload: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AnswerDependencyModel(Base):
    __tablename__ = "answer_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["query_id", "course_id", "user_id"],
            ["query_runs.id", "query_runs.course_id", "query_runs.user_id"],
            name="fk_answer_dependencies_query_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["retrieval_snapshot_id", "query_id"],
            ["retrieval_snapshots.id", "retrieval_snapshots.query_id"],
            name="fk_answer_dependencies_snapshot_query",
            ondelete="CASCADE",
        ),
        UniqueConstraint("query_id", "evidence_id", name="uq_answer_dependencies_query_evidence"),
        CheckConstraint(
            "document_deletion_epoch >= 0",
            name="ck_answer_dependencies_deletion_epoch",
        ),
        Index("ix_answer_dependencies_query", "query_id", "available"),
        Index(
            "ix_answer_dependencies_document",
            "document_id",
            "available",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    query_id: Mapped[str] = mapped_column(String(36), nullable=False)
    retrieval_snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(255), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(255), nullable=False)
    document_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    document_deletion_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    bounding_boxes: Mapped[list[dict[str, float]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    provenance: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    invalidated_reason: Mapped[str | None] = mapped_column(String(128))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
