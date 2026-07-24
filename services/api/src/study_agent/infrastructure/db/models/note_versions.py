"""Immutable note versions, source snapshots, coverage, and lifecycle overlays.

The rows in this module deliberately do not reference mutable revision/chunk
rows.  Ingestion cleanup is allowed to remove those rows while a historical
note version remains an auditable fact.
"""

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

_HASH_CHECK = "~ '^[0-9a-f]{64}$'"


class NoteContentVersionModel(Base):
    """One immutable content snapshot for a note version number."""

    __tablename__ = "note_content_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["note_id", "course_id", "user_id"],
            ["notes.id", "notes.course_id", "notes.user_id"],
            name="fk_note_content_versions_note_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "note_id",
            "version",
            "course_id",
            "user_id",
            name="uq_note_content_versions_scope",
        ),
        CheckConstraint("version >= 1", name="ck_note_content_versions_version"),
        CheckConstraint("btrim(title) <> ''", name="ck_note_content_versions_title"),
        CheckConstraint("btrim(body_markdown) <> ''", name="ck_note_content_versions_body"),
        CheckConstraint(f"body_sha256 {_HASH_CHECK}", name="ck_note_content_versions_body_sha256"),
        CheckConstraint(
            f"source_set_sha256 {_HASH_CHECK}",
            name="ck_note_content_versions_source_sha256",
        ),
        CheckConstraint(
            f"coverage_manifest_sha256 {_HASH_CHECK}",
            name="ck_note_content_versions_coverage_sha256",
        ),
        CheckConstraint(
            f"note_version_sha256 {_HASH_CHECK}",
            name="ck_note_content_versions_version_sha256",
        ),
        CheckConstraint(
            "created_by IN ('generated', 'user', 'legacy_backfill')",
            name="ck_note_content_versions_created_by",
        ),
        Index(
            "ix_note_content_versions_scope_created",
            "user_id",
            "course_id",
            "created_at",
        ),
        Index("ix_note_content_versions_note_version", "note_id", "version"),
    )

    note_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    section_path: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_ast: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ast_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    coverage_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    note_version_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NoteVersionSourceSnapshotModel(Base):
    """Immutable source identity core bound to exactly one note version."""

    __tablename__ = "note_version_source_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["note_id", "version", "course_id", "user_id"],
            [
                "note_content_versions.note_id",
                "note_content_versions.version",
                "note_content_versions.course_id",
                "note_content_versions.user_id",
            ],
            name="fk_note_version_source_snapshots_version_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "source_snapshot_id",
            "note_id",
            "version",
            "course_id",
            "user_id",
            name="uq_note_version_source_snapshots_scope",
        ),
        UniqueConstraint(
            "note_id",
            "version",
            "course_id",
            "user_id",
            "ordinal",
            name="uq_note_version_source_snapshots_ordinal",
        ),
        CheckConstraint("ordinal >= 1", name="ck_note_version_source_snapshots_ordinal"),
        CheckConstraint(
            "document_deletion_epoch >= 0",
            name="ck_note_version_source_snapshots_deletion_epoch",
        ),
        CheckConstraint(
            f"content_sha256 {_HASH_CHECK}",
            name="ck_note_version_source_snapshots_content_sha256",
        ),
        CheckConstraint(
            f"payload_sha256 {_HASH_CHECK}",
            name="ck_note_version_source_snapshots_payload_sha256",
        ),
        Index(
            "ix_note_version_source_snapshots_document",
            "user_id",
            "course_id",
            "document_id",
        ),
        Index(
            "ix_note_version_source_snapshots_version",
            "note_id",
            "version",
            "course_id",
            "user_id",
        ),
    )

    source_snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    note_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(255), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(255), nullable=False)
    document_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    document_deletion_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NoteVersionSourcePayloadModel(Base):
    """Sensitive source payload, tombstonable without changing identity core."""

    __tablename__ = "note_version_source_payloads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_snapshot_id", "note_id", "version", "course_id", "user_id"],
            [
                "note_version_source_snapshots.source_snapshot_id",
                "note_version_source_snapshots.note_id",
                "note_version_source_snapshots.version",
                "note_version_source_snapshots.course_id",
                "note_version_source_snapshots.user_id",
            ],
            name="fk_note_version_source_payloads_snapshot_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "(quote IS NOT NULL AND bounding_boxes IS NOT NULL AND provenance IS NOT NULL "
            "AND redacted_at IS NULL AND redaction_reason IS NULL) OR "
            "(quote IS NULL AND bounding_boxes IS NULL AND provenance IS NULL "
            "AND redacted_at IS NOT NULL AND redaction_reason IS NOT NULL)",
            name="ck_note_version_source_payloads_redaction_state",
        ),
        CheckConstraint(
            "quote IS NULL OR btrim(quote) <> ''",
            name="ck_note_version_source_payloads_quote_nonblank",
        ),
        Index(
            "ix_note_version_source_payloads_version",
            "note_id",
            "version",
            "course_id",
            "user_id",
        ),
    )

    source_snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    note_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    quote: Mapped[str | None] = mapped_column(Text)
    # ``None`` must be SQL NULL, not JSON ``null``: the redaction CHECK uses
    # SQL nullability to distinguish a live payload from a tombstoned one.
    bounding_boxes: Mapped[list[dict[str, float]] | None] = mapped_column(JSONB(none_as_null=True))
    provenance: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True))
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redaction_reason: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NoteVersionSourceLinkModel(Base):
    """Citation token to source snapshot relationship for one version."""

    __tablename__ = "note_version_source_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["note_id", "version", "course_id", "user_id"],
            [
                "note_content_versions.note_id",
                "note_content_versions.version",
                "note_content_versions.course_id",
                "note_content_versions.user_id",
            ],
            name="fk_note_version_source_links_version_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_snapshot_id", "note_id", "version", "course_id", "user_id"],
            [
                "note_version_source_snapshots.source_snapshot_id",
                "note_version_source_snapshots.note_id",
                "note_version_source_snapshots.version",
                "note_version_source_snapshots.course_id",
                "note_version_source_snapshots.user_id",
            ],
            name="fk_note_version_source_links_snapshot_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "note_id",
            "version",
            "course_id",
            "user_id",
            "citation_id",
            name="uq_note_version_source_links_citation",
        ),
        UniqueConstraint(
            "note_id",
            "version",
            "course_id",
            "user_id",
            "ordinal",
            name="uq_note_version_source_links_ordinal",
        ),
        CheckConstraint("ordinal >= 1", name="ck_note_version_source_links_ordinal"),
        Index(
            "ix_note_version_source_links_version",
            "note_id",
            "version",
            "course_id",
            "user_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    note_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    citation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    ast_node_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NoteVersionCoverageModel(Base):
    """Immutable, system-owned coverage manifest for a version."""

    __tablename__ = "note_version_coverage"
    __table_args__ = (
        ForeignKeyConstraint(
            ["note_id", "version", "course_id", "user_id"],
            [
                "note_content_versions.note_id",
                "note_content_versions.version",
                "note_content_versions.course_id",
                "note_content_versions.user_id",
            ],
            name="fk_note_version_coverage_version_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["note_id", "generated_from_version", "course_id", "user_id"],
            [
                "note_content_versions.note_id",
                "note_content_versions.version",
                "note_content_versions.course_id",
                "note_content_versions.user_id",
            ],
            name="fk_note_version_coverage_generated_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('complete', 'partial', 'unknown_legacy')",
            name="ck_note_version_coverage_status",
        ),
        CheckConstraint(
            "basis IN ('generated', 'user_edited_from_generated_version', 'legacy_backfill')",
            name="ck_note_version_coverage_basis",
        ),
        CheckConstraint(
            "(basis = 'user_edited_from_generated_version' AND generated_from_version IS NOT NULL) "
            "OR (basis <> 'user_edited_from_generated_version' AND generated_from_version IS NULL)",
            name="ck_note_version_coverage_basis_source",
        ),
        CheckConstraint(
            f"manifest_sha256 {_HASH_CHECK}", name="ck_note_version_coverage_manifest_sha256"
        ),
        Index(
            "ix_note_version_coverage_scope",
            "user_id",
            "course_id",
            "note_id",
            "version",
        ),
    )

    note_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    basis: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_from_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NoteVersionCoverageUnitModel(Base):
    """Frozen coverage disposition for one substantive unit."""

    __tablename__ = "note_version_coverage_units"
    __table_args__ = (
        ForeignKeyConstraint(
            ["note_id", "version", "course_id", "user_id"],
            [
                "note_version_coverage.note_id",
                "note_version_coverage.version",
                "note_version_coverage.course_id",
                "note_version_coverage.user_id",
            ],
            name="fk_note_version_coverage_units_manifest_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "note_id",
            "version",
            "course_id",
            "user_id",
            "id",
            name="uq_note_version_coverage_units_scope",
        ),
        UniqueConstraint(
            "note_id",
            "version",
            "course_id",
            "user_id",
            "ordinal",
            name="uq_note_version_coverage_units_ordinal",
        ),
        CheckConstraint("ordinal >= 1", name="ck_note_version_coverage_units_ordinal"),
        CheckConstraint(
            "status IN ('pending', 'covered', 'skipped', 'failed')",
            name="ck_note_version_coverage_units_status",
        ),
        CheckConstraint(
            "unit_type IN ('slide', 'pdf_section', 'pdf_page_window')",
            name="ck_note_version_coverage_units_type",
        ),
        CheckConstraint(
            "(status IN ('pending', 'covered') AND reason_code IS NULL) OR "
            "(status IN ('skipped', 'failed') AND reason_code IS NOT NULL "
            "AND btrim(reason_code) <> '')",
            name="ck_note_version_coverage_units_reason",
        ),
        CheckConstraint(
            f"content_sha256 {_HASH_CHECK}",
            name="ck_note_version_coverage_units_content_sha256",
        ),
        Index(
            "ix_note_version_coverage_units_version",
            "note_id",
            "version",
            "course_id",
            "user_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    note_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    input_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_type: Mapped[str] = mapped_column(String(32), nullable=False)
    locator: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    is_substantive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    ast_node_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    source_snapshot_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NoteSourceStateOverlayModel(Base):
    """Append-only display/access state for one immutable source snapshot."""

    __tablename__ = "note_source_state_overlays"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_snapshot_id", "note_id", "version", "course_id", "user_id"],
            [
                "note_version_source_snapshots.source_snapshot_id",
                "note_version_source_snapshots.note_id",
                "note_version_source_snapshots.version",
                "note_version_source_snapshots.course_id",
                "note_version_source_snapshots.user_id",
            ],
            name="fk_note_source_state_overlays_snapshot_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "source_snapshot_id",
            "note_id",
            "version",
            "course_id",
            "user_id",
            "overlay_sequence",
            name="uq_note_source_state_overlays_sequence",
        ),
        UniqueConstraint(
            "source_snapshot_id",
            "note_id",
            "version",
            "course_id",
            "user_id",
            "cause_type",
            "cause_id",
            "cause_epoch",
            name="uq_note_source_state_overlays_cause",
        ),
        CheckConstraint("overlay_sequence >= 1", name="ck_note_source_state_overlays_sequence"),
        CheckConstraint("display_epoch >= 0", name="ck_note_source_state_overlays_display_epoch"),
        CheckConstraint("access_epoch >= 0", name="ck_note_source_state_overlays_access_epoch"),
        CheckConstraint(
            "display_state IN ('current', 'stale', 'unavailable', 'redacted')",
            name="ck_note_source_state_overlays_display_state",
        ),
        CheckConstraint(
            "access_state IN ('authorized', 'revoked', 'redacted', 'unauthorized')",
            name="ck_note_source_state_overlays_access_state",
        ),
        CheckConstraint("cause_epoch >= 0", name="ck_note_source_state_overlays_cause_epoch"),
        Index(
            "ix_note_source_state_overlays_latest",
            "source_snapshot_id",
            "note_id",
            "version",
            "course_id",
            "user_id",
            "overlay_sequence",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    note_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    overlay_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    display_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    access_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    display_state: Mapped[str] = mapped_column(String(32), nullable=False)
    access_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    cause_type: Mapped[str] = mapped_column(String(64), nullable=False)
    cause_id: Mapped[str] = mapped_column(String(255), nullable=False)
    cause_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "NoteContentVersionModel",
    "NoteSourceStateOverlayModel",
    "NoteVersionCoverageModel",
    "NoteVersionCoverageUnitModel",
    "NoteVersionSourceLinkModel",
    "NoteVersionSourcePayloadModel",
    "NoteVersionSourceSnapshotModel",
]
