"""add durable note batch command metadata

Revision ID: 20260722_0008
Revises: 7102eb21ee91
Create Date: 2026-07-22 16:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0008"
down_revision: str | None = "7102eb21ee91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "note_generation_batches",
        sa.Column(
            "command_kind",
            sa.String(length=32),
            server_default=sa.text("'create'"),
            nullable=False,
        ),
    )
    op.add_column(
        "note_generation_batches",
        sa.Column("title", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "note_generation_batches",
        sa.Column("title_prefix", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "note_generation_batches",
        sa.Column(
            "section_path",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[\"未分类\"]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "note_generation_batches",
        sa.Column("target_note_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "note_generation_batches",
        sa.Column("target_note_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "note_generation_batches",
        sa.Column("target_note_version_sha256", sa.String(length=64), nullable=True),
    )
    op.alter_column(
        "note_generation_batches",
        "command_kind",
        server_default=None,
    )
    op.alter_column(
        "note_generation_batches",
        "section_path",
        server_default=None,
    )
    op.create_check_constraint(
        "ck_note_generation_batches_command_kind",
        "note_generation_batches",
        "command_kind IN ('create', 'retry_failed', 'retry_gaps', 'regeneration')",
    )
    op.create_check_constraint(
        "ck_note_generation_batches_section_path",
        "note_generation_batches",
        "jsonb_typeof(section_path) = 'array' AND jsonb_array_length(section_path) >= 1",
    )
    op.create_check_constraint(
        "ck_note_generation_batches_title_mode",
        "note_generation_batches",
        "(mode = 'merged' AND title_prefix IS NULL) OR (mode = 'per_document' AND title IS NULL)",
    )
    op.create_check_constraint(
        "ck_note_generation_batches_retry_parent",
        "note_generation_batches",
        "(command_kind IN ('retry_failed', 'retry_gaps') AND retry_of_batch_id IS NOT NULL) "
        "OR (command_kind IN ('create', 'regeneration') AND retry_of_batch_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_note_generation_batches_target",
        "note_generation_batches",
        "(command_kind = 'regeneration' AND mode = 'merged' "
        "AND target_note_id IS NOT NULL AND target_note_version IS NOT NULL "
        "AND target_note_version_sha256 IS NOT NULL) OR "
        "(command_kind <> 'regeneration' AND target_note_id IS NULL "
        "AND target_note_version IS NULL AND target_note_version_sha256 IS NULL)",
    )
    op.create_check_constraint(
        "ck_note_generation_batches_target_version",
        "note_generation_batches",
        "target_note_version IS NULL OR target_note_version >= 1",
    )
    op.create_check_constraint(
        "ck_note_generation_batches_target_sha256",
        "note_generation_batches",
        "target_note_version_sha256 IS NULL OR target_note_version_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_foreign_key(
        "fk_note_generation_batches_target_version_scope",
        "note_generation_batches",
        "note_content_versions",
        ["target_note_id", "target_note_version", "course_id", "user_id"],
        ["note_id", "version", "course_id", "user_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_note_generation_batches_user_status",
        "note_generation_batches",
        ["user_id", "status"],
        unique=False,
    )

    op.add_column(
        "note_generation_outputs",
        sa.Column("note_version", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE note_generation_outputs AS output "
            "SET note_version = note.version FROM notes AS note "
            "WHERE note.id = output.note_id "
            "AND note.course_id = output.course_id AND note.user_id = output.user_id"
        )
    )
    op.alter_column("note_generation_outputs", "note_version", nullable=False)
    op.drop_constraint(
        "uq_note_generation_outputs_note",
        "note_generation_outputs",
        type_="unique",
    )
    op.create_check_constraint(
        "ck_note_generation_outputs_note_version",
        "note_generation_outputs",
        "note_version >= 1",
    )
    op.create_foreign_key(
        "fk_note_generation_outputs_note_version_scope",
        "note_generation_outputs",
        "note_content_versions",
        ["note_id", "note_version", "course_id", "user_id"],
        ["note_id", "version", "course_id", "user_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_note_generation_outputs_note_version",
        "note_generation_outputs",
        ["note_id", "note_version", "course_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_note_generation_outputs_note_version",
        "note_generation_outputs",
        type_="unique",
    )
    op.drop_constraint(
        "fk_note_generation_outputs_note_version_scope",
        "note_generation_outputs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_note_generation_outputs_note_version",
        "note_generation_outputs",
        type_="check",
    )
    # The pre-0008 schema can retain only one output per Note. Keep the newest
    # version deterministically before restoring its Note-level uniqueness.
    op.execute(
        sa.text(
            "DELETE FROM note_generation_outputs WHERE id IN ("
            "SELECT id FROM ("
            "SELECT id, row_number() OVER ("
            "PARTITION BY note_id, course_id, user_id "
            "ORDER BY note_version DESC, created_at DESC, id DESC"
            ") AS output_rank FROM note_generation_outputs"
            ") AS ranked_outputs WHERE output_rank > 1"
            ")"
        )
    )
    op.create_unique_constraint(
        "uq_note_generation_outputs_note",
        "note_generation_outputs",
        ["note_id", "course_id", "user_id"],
    )
    op.drop_column("note_generation_outputs", "note_version")

    op.drop_index(
        "ix_note_generation_batches_user_status",
        table_name="note_generation_batches",
        if_exists=True,
    )
    op.drop_constraint(
        "fk_note_generation_batches_target_version_scope",
        "note_generation_batches",
        type_="foreignkey",
    )
    for constraint in (
        "ck_note_generation_batches_target_sha256",
        "ck_note_generation_batches_target_version",
        "ck_note_generation_batches_target",
        "ck_note_generation_batches_retry_parent",
        "ck_note_generation_batches_title_mode",
        "ck_note_generation_batches_section_path",
        "ck_note_generation_batches_command_kind",
    ):
        op.drop_constraint(constraint, "note_generation_batches", type_="check")
    for column in (
        "target_note_version_sha256",
        "target_note_version",
        "target_note_id",
        "section_path",
        "title_prefix",
        "title",
        "command_kind",
    ):
        op.drop_column("note_generation_batches", column)
