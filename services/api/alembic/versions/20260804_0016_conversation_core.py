"""add shared conversation messages and practice tutor scope

Revision ID: 20260804_0016
Revises: 20260804_0015
Create Date: 2026-08-04 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_0016"
down_revision: str | None = "20260804_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "conversation_type",
            sa.String(length=32),
            server_default="course_qa",
            nullable=False,
        ),
    )
    op.add_column(
        "conversations",
        sa.Column("practice_session_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("practice_question_id", sa.String(length=36), nullable=True),
    )
    op.create_check_constraint(
        "ck_conversations_type",
        "conversations",
        "conversation_type IN ('course_qa', 'practice_tutor')",
    )
    op.create_check_constraint(
        "ck_conversations_subject_scope",
        "conversations",
        "(conversation_type = 'course_qa' AND practice_session_id IS NULL "
        "AND practice_question_id IS NULL) OR "
        "(conversation_type = 'practice_tutor' AND practice_session_id IS NOT NULL "
        "AND practice_question_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_conversations_practice_session_scope",
        "conversations",
        "practice_sessions",
        ["practice_session_id", "course_id", "user_id"],
        ["id", "course_id", "user_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_conversations_practice_question_scope",
        "conversations",
        "practice_questions",
        ["practice_question_id", "course_id", "user_id"],
        ["id", "course_id", "user_id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_conversations_practice_scope",
        "conversations",
        [
            "user_id",
            "course_id",
            "conversation_type",
            "practice_session_id",
            "practice_question_id",
        ],
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(length=32), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=True),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_conversation_messages_sequence"),
        sa.CheckConstraint("btrim(turn_id) <> ''", name="ck_conversation_messages_turn_nonblank"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_conversation_messages_role"),
        sa.CheckConstraint(
            "intent IS NULL OR intent IN "
            "('hint', 'clarify', 'example', 'answer_check', 'solution', 'reflection', 'source')",
            name="ck_conversation_messages_intent",
        ),
        sa.CheckConstraint(
            "mode IS NULL OR mode IN ('hint', 'review')",
            name="ck_conversation_messages_mode",
        ),
        sa.CheckConstraint(
            "btrim(content) <> ''", name="ck_conversation_messages_content_nonblank"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "course_id", "user_id"],
            ["conversations.id", "conversations.course_id", "conversations.user_id"],
            name="fk_conversation_messages_conversation_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_conversation_messages_sequence"
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "turn_id",
            "role",
            name="uq_conversation_messages_turn_role",
        ),
    )
    op.create_index(
        "ix_conversation_messages_scope_sequence",
        "conversation_messages",
        ["user_id", "course_id", "conversation_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_messages_scope_sequence", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_constraint("uq_conversations_practice_scope", "conversations", type_="unique")
    op.drop_constraint(
        "fk_conversations_practice_question_scope", "conversations", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_conversations_practice_session_scope", "conversations", type_="foreignkey"
    )
    op.drop_constraint("ck_conversations_subject_scope", "conversations", type_="check")
    op.drop_constraint("ck_conversations_type", "conversations", type_="check")
    op.drop_column("conversations", "practice_question_id")
    op.drop_column("conversations", "practice_session_id")
    op.drop_column("conversations", "conversation_type")
