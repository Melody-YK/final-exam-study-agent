"""Persistent parse jobs, page checkpoints, and replayable events."""

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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from study_agent.infrastructure.db.base import Base
from study_agent.infrastructure.db.models.core import TimestampMixin, new_id


class ParseJobModel(TimestampMixin, Base):
    __tablename__ = "parse_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "course_id", "user_id"],
            ["documents.id", "documents.course_id", "documents.user_id"],
            name="fk_parse_jobs_document_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["stored_object_id", "course_id", "user_id"],
            ["stored_objects.id", "stored_objects.course_id", "stored_objects.user_id"],
            name="fk_parse_jobs_object_scope",
        ),
        UniqueConstraint(
            "id",
            "document_id",
            "course_id",
            "user_id",
            name="uq_parse_jobs_scope",
        ),
        CheckConstraint("state_version >= 1", name="ck_parse_jobs_state_version"),
        CheckConstraint("attempt >= 0", name="ck_parse_jobs_attempt"),
        CheckConstraint("max_attempts >= 1", name="ck_parse_jobs_max_attempts"),
        CheckConstraint("lease_version >= 0", name="ck_parse_jobs_lease_version"),
        CheckConstraint("event_sequence >= 0", name="ck_parse_jobs_event_sequence"),
        CheckConstraint("input_size_bytes >= 0", name="ck_parse_jobs_input_size"),
        Index(
            "ix_parse_jobs_claim",
            "status",
            "available_at",
            "created_at",
        ),
        Index("ix_parse_jobs_document_status", "document_id", "status"),
        Index(
            "uq_parse_jobs_document_nonterminal",
            "document_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued','leased','parsing','result_submitted',"
                "'validating','indexing','retry_wait')"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    stored_object_id: Mapped[str] = mapped_column(
        ForeignKey("stored_objects.id", ondelete="RESTRICT"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(String(32), nullable=False, default="parse")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    parser_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0")
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    document_deletion_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    input_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    estimated_pages: Mapped[int | None] = mapped_column(Integer)
    requires_ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_rendering: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requested_pages: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lease_owner_id: Mapped[str | None] = mapped_column(String(128))
    lease_token_hash: Mapped[str | None] = mapped_column(String(64))
    lease_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_manifest_ref: Mapped[str | None] = mapped_column(String(1024))
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    result_page_count: Mapped[int | None] = mapped_column(Integer)
    failed_pages: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    failure_code: Mapped[str | None] = mapped_column(String(128))
    failure_summary: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool | None] = mapped_column(Boolean)


class JobArtifactModel(TimestampMixin, Base):
    __tablename__ = "job_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
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
        ForeignKeyConstraint(
            ["stored_object_id", "course_id", "user_id"],
            ["stored_objects.id", "stored_objects.course_id", "stored_objects.user_id"],
            name="fk_job_artifacts_object_scope",
        ),
        UniqueConstraint(
            "job_id", "attempt", "artifact_name", name="uq_job_artifacts_job_attempt_name"
        ),
        CheckConstraint("attempt >= 1", name="ck_job_artifacts_attempt"),
        CheckConstraint("deletion_epoch >= 0", name="ck_job_artifacts_deletion_epoch"),
        CheckConstraint("size_bytes >= 0", name="ck_job_artifacts_size"),
        Index("ix_job_artifacts_job_attempt", "job_id", "attempt"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("parse_jobs.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    stored_object_id: Mapped[str] = mapped_column(
        ForeignKey("stored_objects.id", ondelete="RESTRICT"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    deletion_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="available")


class PageCheckpointModel(TimestampMixin, Base):
    __tablename__ = "page_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
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
        UniqueConstraint(
            "job_id",
            "attempt",
            "page_ordinal",
            name="uq_page_checkpoints_job_attempt_page",
        ),
        CheckConstraint("attempt >= 1", name="ck_page_checkpoints_attempt"),
        CheckConstraint("page_ordinal >= 1", name="ck_page_checkpoints_page"),
        CheckConstraint("lease_version >= 1", name="ck_page_checkpoints_lease_version"),
        Index("ix_page_checkpoints_job_attempt", "job_id", "attempt"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("parse_jobs.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    page_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    output_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    output_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_backend: Mapped[str] = mapped_column(String(128), nullable=False)
    source_version: Mapped[str] = mapped_column(String(64), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))


class JobEventModel(Base):
    __tablename__ = "job_events"
    __table_args__ = (
        ForeignKeyConstraint(
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
        UniqueConstraint("job_id", "sequence", name="uq_job_events_job_sequence"),
        CheckConstraint("sequence >= 1", name="ck_job_events_sequence"),
        Index("ix_job_events_job_sequence", "job_id", "sequence"),
        Index("ix_job_events_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("parse_jobs.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    trace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
