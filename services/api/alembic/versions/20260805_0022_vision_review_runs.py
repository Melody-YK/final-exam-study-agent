"""add secret-safe multimodal review audit facts

Revision ID: 20260805_0022
Revises: 20260805_0021
Create Date: 2026-08-05 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260805_0022"
down_revision: str | None = "20260805_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vision_review_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("unit_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("source_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("provider_response_id", sa.String(length=255), nullable=True),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("image_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="ck_vision_review_runs_status",
        ),
        sa.CheckConstraint(
            "duration_ms >= 0",
            name="ck_vision_review_runs_duration_nonnegative",
        ),
        sa.CheckConstraint(
            "image_size_bytes >= 0",
            name="ck_vision_review_runs_image_size_nonnegative",
        ),
        sa.CheckConstraint(
            "source_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_vision_review_runs_source_hash",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_vision_review_runs_course_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id", "course_id", "user_id"],
            ["learning_units.id", "learning_units.course_id", "learning_units.user_id"],
            name="fk_vision_review_runs_unit_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "course_id", "user_id"],
            [
                "learning_unit_sources.id",
                "learning_unit_sources.course_id",
                "learning_unit_sources.user_id",
            ],
            name="fk_vision_review_runs_source_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_vision_review_runs_scope_created",
        "vision_review_runs",
        ["user_id", "course_id", "created_at"],
    )
    op.create_index(
        "ix_vision_review_runs_status_created",
        "vision_review_runs",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_vision_review_runs_status_created", table_name="vision_review_runs")
    op.drop_index("ix_vision_review_runs_scope_created", table_name="vision_review_runs")
    op.drop_table("vision_review_runs")
