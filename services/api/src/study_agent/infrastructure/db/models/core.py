"""Core persistence models for course ingestion and reliable cleanup."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
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


def new_id() -> str:
    """Return a sortable-independent opaque identifier for a new row."""

    return str(uuid4())


class TimestampMixin:
    """Server-maintained creation and modification timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserModel(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint(
            "authentication_method", "subject", name="uq_users_authentication_subject"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    authentication_method: Mapped[str] = mapped_column(String(32), nullable=False)


class CourseModel(TimestampMixin, Base):
    __tablename__ = "courses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "active_lexical_index_id"],
            ["lexical_manifests.course_id", "lexical_manifests.id"],
            name="fk_courses_active_lexical_manifest",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("id", "user_id", name="uq_courses_id_user"),
        CheckConstraint("row_version >= 1", name="ck_courses_row_version_positive"),
        Index("ix_courses_user_visible", "user_id", "deleted_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    active_lexical_index_id: Mapped[str | None] = mapped_column(String(36))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StoredObjectModel(Base):
    __tablename__ = "stored_objects"
    __table_args__ = (
        UniqueConstraint("id", "course_id", "user_id", name="uq_stored_objects_id_course_user"),
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_stored_objects_course_user",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_stored_objects_size_nonnegative"),
        Index("ix_stored_objects_course_visible", "course_id", "deleted_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    course_id: Mapped[str | None] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UploadSessionModel(TimestampMixin, Base):
    __tablename__ = "upload_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_upload_sessions_course_user",
        ),
        ForeignKeyConstraint(
            ["stored_object_id", "course_id", "user_id"],
            ["stored_objects.id", "stored_objects.course_id", "stored_objects.user_id"],
            name="fk_upload_sessions_object_scope",
        ),
        CheckConstraint("expected_size >= 0", name="ck_upload_sessions_size_nonnegative"),
        Index("ix_upload_sessions_course_status", "course_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    stored_object_id: Mapped[str | None] = mapped_column(
        ForeignKey("stored_objects.id", ondelete="SET NULL")
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "documents.id",
            name="fk_upload_sessions_document",
            use_alter=True,
            ondelete="CASCADE",
        )
    )
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    expected_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentModel(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "preview_revision_id"],
            ["document_revisions.document_id", "document_revisions.id"],
            name="fk_documents_preview_revision",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["id", "active_revision_id"],
            ["document_revisions.document_id", "document_revisions.id"],
            name="fk_documents_active_revision",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_documents_course_user",
        ),
        ForeignKeyConstraint(
            ["stored_object_id", "course_id", "user_id"],
            ["stored_objects.id", "stored_objects.course_id", "stored_objects.user_id"],
            name="fk_documents_object_scope",
        ),
        UniqueConstraint("id", "course_id", "user_id", name="uq_documents_id_course_user"),
        CheckConstraint("deletion_epoch >= 0", name="ck_documents_deletion_epoch_nonnegative"),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')",
            name="ck_documents_review_status",
        ),
        CheckConstraint(
            "review_note IS NULL OR (review_note = btrim(review_note) "
            "AND length(review_note) BETWEEN 1 AND 500)",
            name="ck_documents_review_note",
        ),
        CheckConstraint(
            "(reviewed_by_account_id IS NULL) = (reviewed_at IS NULL)",
            name="ck_documents_review_actor_time",
        ),
        CheckConstraint(
            "(review_status NOT IN ('pending', 'approved', 'rejected')) OR "
            "(review_status = 'pending' AND reviewed_by_account_id IS NULL "
            "AND reviewed_at IS NULL AND review_note IS NULL) OR "
            "(review_status = 'approved' AND (review_note IS NULL "
            "OR reviewed_by_account_id IS NOT NULL)) OR "
            "(review_status = 'rejected' AND reviewed_by_account_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND review_note IS NOT NULL)",
            name="ck_documents_review_state",
        ),
        Index(
            "uq_documents_visible_content_role",
            "course_id",
            "verified_sha256",
            "corpus_role",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_documents_course_visible", "course_id", "deleted_at"),
        Index(
            "ix_documents_review_queue",
            "review_status",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    stored_object_id: Mapped[str] = mapped_column(
        ForeignKey("stored_objects.id", ondelete="RESTRICT"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    corpus_role: Mapped[str] = mapped_column(String(32), nullable=False)
    verified_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="uploaded")
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preview_revision_id: Mapped[str | None] = mapped_column(String(36))
    active_revision_id: Mapped[str | None] = mapped_column(String(36))
    deletion_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentRevisionModel(Base):
    __tablename__ = "document_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_document_revisions_ordinal"),
        UniqueConstraint("document_id", "id", name="uq_document_revisions_document_id"),
        CheckConstraint("total_page_count >= 1", name="ck_document_revisions_total_page_count"),
        Index("ix_document_revisions_document_created", "document_id", "created_at"),
        Index(
            "uq_document_revisions_parse_job",
            "parse_job_id",
            unique=True,
            postgresql_where=text("parse_job_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    parse_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("parse_jobs.id", ondelete="SET NULL")
    )
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="0" * 64)
    total_page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parser_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    chunker_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="section-page-v1"
    )
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IdempotencyRecordModel(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "actor_subject",
            "actor_authentication_method",
            "operation",
            "idempotency_key",
            name="uq_idempotency_actor_operation_key",
        ),
        Index("ix_idempotency_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_authentication_method: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeletionJobModel(TimestampMixin, Base):
    __tablename__ = "deletion_jobs"
    __table_args__ = (
        UniqueConstraint(
            "target_type", "target_id", "deletion_epoch", name="uq_deletion_target_epoch"
        ),
        CheckConstraint("deletion_epoch >= 0", name="ck_deletion_jobs_epoch_nonnegative"),
        CheckConstraint("attempt_count >= 0", name="ck_deletion_jobs_attempt_nonnegative"),
        Index("ix_deletion_jobs_status_available", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    deletion_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_outbox_events_attempt_nonnegative"),
        Index("ix_outbox_events_status_available", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    last_error_detail: Mapped[str | None] = mapped_column(Text)
