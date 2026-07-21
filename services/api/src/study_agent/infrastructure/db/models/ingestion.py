"""Validated parser attempts and immutable normalized revision derivatives."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
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


class ParseAttemptResultModel(Base):
    __tablename__ = "parse_attempt_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "document_id", "course_id", "user_id"],
            [
                "parse_jobs.id",
                "parse_jobs.document_id",
                "parse_jobs.course_id",
                "parse_jobs.user_id",
            ],
            name="fk_parse_attempt_results_job_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint("job_id", "attempt", name="uq_parse_attempt_results_job_attempt"),
        UniqueConstraint("artifact_id", name="uq_parse_attempt_results_artifact"),
        CheckConstraint("attempt >= 1", name="ck_parse_attempt_results_attempt"),
        CheckConstraint("total_page_count >= 1", name="ck_parse_attempt_results_total_page_count"),
        Index("ix_parse_attempt_results_job_attempt", "job_id", "attempt"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("parse_jobs.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("job_artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    parser_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    source_backend: Mapped[str] = mapped_column(String(128), nullable=False)
    source_version: Mapped[str] = mapped_column(String(64), nullable=False)
    total_page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_page_ordinals: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    covered_page_ordinals: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    canonical_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RevisionPageModel(Base):
    __tablename__ = "revision_pages"
    __table_args__ = (
        UniqueConstraint("revision_id", "page_ordinal", name="uq_revision_pages_ordinal"),
        CheckConstraint("page_ordinal >= 1", name="ck_revision_pages_ordinal"),
        CheckConstraint("width >= 1", name="ck_revision_pages_width"),
        CheckConstraint("height >= 1", name="ck_revision_pages_height"),
        Index("ix_revision_pages_revision", "revision_id", "page_ordinal"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="CASCADE"), nullable=False
    )
    page_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_norm: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False)
    source_backend: Mapped[str] = mapped_column(String(128), nullable=False)
    source_version: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_result_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    quality: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class RevisionBlockModel(Base):
    __tablename__ = "revision_blocks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["revision_id", "page_ordinal"],
            ["revision_pages.revision_id", "revision_pages.page_ordinal"],
            name="fk_revision_blocks_page",
            ondelete="CASCADE",
        ),
        UniqueConstraint("revision_id", "block_id", name="uq_revision_blocks_revision_block"),
        UniqueConstraint(
            "revision_id",
            "page_ordinal",
            "reading_order",
            name="uq_revision_blocks_reading_order",
        ),
        CheckConstraint("page_ordinal >= 1", name="ck_revision_blocks_page_ordinal"),
        CheckConstraint("reading_order >= 0", name="ck_revision_blocks_reading_order"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_revision_blocks_confidence",
        ),
        Index("ix_revision_blocks_revision_page", "revision_id", "page_ordinal"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="CASCADE"), nullable=False
    )
    page_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    block_id: Mapped[str] = mapped_column(String(255), nullable=False)
    block_type: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    bbox_norm: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False)
    reading_order: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_backend: Mapped[str] = mapped_column(String(128), nullable=False)
    source_version: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_result_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    parent_block_id: Mapped[str | None] = mapped_column(String(255))
    section_path: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)


class RevisionAssetModel(Base):
    __tablename__ = "revision_assets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["revision_id", "page_ordinal"],
            ["revision_pages.revision_id", "revision_pages.page_ordinal"],
            name="fk_revision_assets_page",
            ondelete="CASCADE",
        ),
        UniqueConstraint("revision_id", "asset_id", name="uq_revision_assets_revision_asset"),
        CheckConstraint("page_ordinal >= 1", name="ck_revision_assets_page_ordinal"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_revision_assets_size"),
        Index("ix_revision_assets_revision_page", "revision_id", "page_ordinal"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    locator_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    page_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_norm: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False)
    object_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_backend: Mapped[str] = mapped_column(String(128), nullable=False)
    source_version: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_result_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)


class RevisionChunkModel(Base):
    __tablename__ = "revision_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["revision_id", "page_ordinal"],
            ["revision_pages.revision_id", "revision_pages.page_ordinal"],
            name="fk_revision_chunks_page",
            ondelete="CASCADE",
        ),
        UniqueConstraint("revision_id", "ordinal", name="uq_revision_chunks_ordinal"),
        UniqueConstraint("id", "revision_id", name="uq_revision_chunks_id_revision"),
        UniqueConstraint(
            "revision_id",
            "content_sha256",
            "ordinal",
            name="uq_revision_chunks_hash",
        ),
        CheckConstraint("ordinal >= 1", name="ck_revision_chunks_ordinal"),
        CheckConstraint("page_ordinal >= 1", name="ck_revision_chunks_page_ordinal"),
        CheckConstraint("token_count_estimate >= 1", name="ck_revision_chunks_token_count"),
        Index("ix_revision_chunks_revision_page", "revision_id", "page_ordinal"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    locator_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    page_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source_block_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    token_count_estimate: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(64), nullable=False)
