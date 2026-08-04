"""add rolling conversation summaries and learner memories

Revision ID: 20260804_0018
Revises: 20260804_0017
Create Date: 2026-08-04 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0018"
down_revision: str | None = "20260804_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary_text", sa.Text(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("summary_version", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("summary_turn_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_conversations_summary_turn_count",
        "conversations",
        "summary_turn_count >= 0",
    )

    op.create_table(
        "learner_memories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_message_id", sa.String(length=36), nullable=True),
        sa.Column("source_query_id", sa.String(length=36), nullable=True),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "memory_type IN ('preference', 'confirmed_misconception', 'learning_goal')",
            name="ck_learner_memories_type",
        ),
        sa.CheckConstraint(
            "source_kind IN ('explicit_user', 'manual')",
            name="ck_learner_memories_source_kind",
        ),
        sa.CheckConstraint("btrim(content) <> ''", name="ck_learner_memories_content_nonblank"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_learner_memories_confidence",
        ),
        sa.CheckConstraint(
            "source_message_id IS NULL OR source_query_id IS NULL",
            name="ck_learner_memories_single_source",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_learner_memories_course_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["conversation_messages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_query_id"],
            ["query_runs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "course_id",
            "memory_type",
            "content_sha256",
            name="uq_learner_memories_content",
        ),
    )
    op.create_index(
        "ix_learner_memories_scope_updated",
        "learner_memories",
        ["user_id", "course_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_learner_memories_scope_updated", table_name="learner_memories")
    op.drop_table("learner_memories")
    op.drop_constraint("ck_conversations_summary_turn_count", "conversations", type_="check")
    op.drop_column("conversations", "summary_turn_count")
    op.drop_column("conversations", "summary_version")
    op.drop_column("conversations", "summary_text")
