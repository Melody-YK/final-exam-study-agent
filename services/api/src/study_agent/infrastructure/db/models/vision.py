"""Audit facts for on-demand multimodal evidence reviews."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from study_agent.infrastructure.db.base import Base
from study_agent.infrastructure.db.models.core import new_id


class VisionReviewRunModel(Base):
    """Secret-safe metadata for one actual vision provider attempt."""

    __tablename__ = "vision_review_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_vision_review_runs_course_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["unit_id", "course_id", "user_id"],
            ["learning_units.id", "learning_units.course_id", "learning_units.user_id"],
            name="fk_vision_review_runs_unit_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_id", "course_id", "user_id"],
            [
                "learning_unit_sources.id",
                "learning_unit_sources.course_id",
                "learning_unit_sources.user_id",
            ],
            name="fk_vision_review_runs_source_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="ck_vision_review_runs_status",
        ),
        CheckConstraint("duration_ms >= 0", name="ck_vision_review_runs_duration_nonnegative"),
        CheckConstraint(
            "image_size_bytes >= 0",
            name="ck_vision_review_runs_image_size_nonnegative",
        ),
        CheckConstraint(
            "source_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_vision_review_runs_source_hash",
        ),
        Index(
            "ix_vision_review_runs_scope_created",
            "user_id",
            "course_id",
            "created_at",
        ),
        Index("ix_vision_review_runs_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(255))
    provider_response_id: Mapped[str | None] = mapped_column(String(255))
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    image_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
