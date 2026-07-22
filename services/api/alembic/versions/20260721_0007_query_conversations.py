"""Group trusted queries into principal-scoped course conversations.

Downgrading preserves the pre-0007 query rows but necessarily discards
conversation titles and split boundaries because revision 0006 has nowhere to
store them. Data-bearing deployments must use a forward fix instead of a
downgrade once users have created or split conversations.

Revision ID: 20260721_0007
Revises: 20260719_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0007"
down_revision: str | None = "20260719_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "auto_title_pending",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
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
        sa.CheckConstraint("btrim(title) <> ''", name="ck_conversations_title_nonblank"),
        sa.ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_conversations_course_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "course_id", "user_id", name="uq_conversations_id_scope"),
    )
    op.create_index(
        "ix_conversations_scope_updated",
        "conversations",
        ["user_id", "course_id", "updated_at"],
    )

    op.add_column(
        "query_runs",
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
    )
    op.execute(
        """
        INSERT INTO conversations (
            id,
            user_id,
            course_id,
            title,
            auto_title_pending,
            created_at,
            updated_at
        )
        SELECT
            substr(md5('conversation:' || user_id || ':' || course_id), 1, 8)
                || '-' || substr(md5('conversation:' || user_id || ':' || course_id), 9, 4)
                || '-' || substr(md5('conversation:' || user_id || ':' || course_id), 13, 4)
                || '-' || substr(md5('conversation:' || user_id || ':' || course_id), 17, 4)
                || '-' || substr(md5('conversation:' || user_id || ':' || course_id), 21, 12),
            user_id,
            course_id,
            '历史问答',
            false,
            min(created_at),
            max(updated_at)
        FROM query_runs
        GROUP BY user_id, course_id
        """
    )
    op.execute(
        """
        UPDATE query_runs AS query
        SET conversation_id = conversation.id
        FROM conversations AS conversation
        WHERE conversation.user_id = query.user_id
          AND conversation.course_id = query.course_id
        """
    )
    op.alter_column("query_runs", "conversation_id", nullable=False)
    op.create_foreign_key(
        "fk_query_runs_conversation_scope",
        "query_runs",
        "conversations",
        ["conversation_id", "course_id", "user_id"],
        ["id", "course_id", "user_id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_query_runs_conversation_created",
        "query_runs",
        ["user_id", "course_id", "conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_query_runs_conversation_created", table_name="query_runs")
    op.drop_constraint(
        "fk_query_runs_conversation_scope",
        "query_runs",
        type_="foreignkey",
    )
    op.drop_column("query_runs", "conversation_id")
    op.drop_index("ix_conversations_scope_updated", table_name="conversations")
    op.drop_table("conversations")
