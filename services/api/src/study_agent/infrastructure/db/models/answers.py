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
        UniqueConstraint("id", "course_id", "user_id", name="uq_conversations_id_scope"),
        CheckConstraint("btrim(title) <> ''", name="ck_conversations_title_nonblank"),
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
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    auto_title_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
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
    requested_document_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
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
