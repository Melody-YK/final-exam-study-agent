"""add local accounts and cookie sessions

Revision ID: 20260723_0009
Revises: 20260722_0008
Create Date: 2026-07-23 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0009"
down_revision: str | None = "20260722_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
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
            "role IN ('admin', 'user')",
            name="ck_accounts_role",
        ),
        sa.CheckConstraint(
            "email = lower(btrim(email)) AND length(email) BETWEEN 3 AND 320 "
            "AND position('@' IN email) > 1",
            name="ck_accounts_email_normalized",
        ),
        sa.CheckConstraint(
            "display_name = btrim(display_name) AND length(display_name) BETWEEN 1 AND 100",
            name="ck_accounts_display_name",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_accounts_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.UniqueConstraint("email", name="uq_accounts_email"),
        sa.UniqueConstraint("user_id", name="uq_accounts_user"),
    )
    op.create_index(
        "ix_accounts_role_created",
        "accounts",
        ["role", "created_at", "id"],
        unique=False,
    )

    op.create_table(
        "account_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_account_sessions_token_hash",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_account_sessions_expiry",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_account_sessions_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_account_sessions_token_hash"),
    )
    op.create_index(
        "ix_account_sessions_account_active",
        "account_sessions",
        ["account_id", "expires_at"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_account_sessions_account_active",
        table_name="account_sessions",
        if_exists=True,
    )
    op.drop_table("account_sessions")
    op.drop_index("ix_accounts_role_created", table_name="accounts", if_exists=True)
    op.drop_table("accounts")
