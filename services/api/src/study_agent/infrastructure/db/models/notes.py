"""Editable notes whose source relationships are stored independently."""

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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from study_agent.infrastructure.db.base import Base
from study_agent.infrastructure.db.models.core import new_id


class NoteModel(Base):
    __tablename__ = "notes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_notes_course_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "course_id", "user_id", name="uq_notes_id_scope"),
        CheckConstraint("version >= 1", name="ck_notes_version_positive"),
        CheckConstraint("generation >= 1", name="ck_notes_generation_positive"),
        CheckConstraint("jsonb_array_length(section_path) >= 1", name="ck_notes_section_path"),
        CheckConstraint("btrim(title) <> ''", name="ck_notes_title_nonblank"),
        CheckConstraint("btrim(body_markdown) <> ''", name="ck_notes_body_nonblank"),
        CheckConstraint(
            "status IN ('generating', 'ready', 'failed')",
            name="ck_notes_status",
        ),
        Index("ix_notes_scope_updated", "user_id", "course_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    section_path: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    generated_by_model: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    failure_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class NoteSourceModel(Base):
    __tablename__ = "note_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["note_id", "course_id", "user_id"],
            ["notes.id", "notes.course_id", "notes.user_id"],
            name="fk_note_sources_note_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint("note_id", "evidence_id", name="uq_note_sources_note_evidence"),
        CheckConstraint(
            "document_deletion_epoch >= 0",
            name="ck_note_sources_deletion_epoch",
        ),
        Index("ix_note_sources_note", "note_id", "available"),
        Index("ix_note_sources_document", "document_id", "available"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    note_id: Mapped[str] = mapped_column(String(36), nullable=False)
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
    unavailable_reason: Mapped[str | None] = mapped_column(String(128))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
