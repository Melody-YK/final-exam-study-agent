"""add invitation-only registration and account controls

Revision ID: 20260724_0011
Revises: 20260723_0010
Create Date: 2026-07-24 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_0011"
down_revision: str | None = "20260723_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
    )
    op.add_column("accounts", sa.Column("admin_note", sa.Text(), nullable=True))
    op.execute(
        "UPDATE accounts SET status = CASE "
        "WHEN disabled_at IS NULL THEN 'active' ELSE 'suspended' END"
    )
    op.alter_column("accounts", "status", server_default=None)
    op.create_check_constraint(
        "ck_accounts_status",
        "accounts",
        "status IN ('active', 'suspended')",
    )
    op.create_check_constraint(
        "ck_accounts_status_disabled",
        "accounts",
        "(status = 'active' AND disabled_at IS NULL) OR "
        "(status = 'suspended' AND disabled_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_accounts_admin_note",
        "accounts",
        "admin_note IS NULL OR (admin_note = btrim(admin_note) "
        "AND length(admin_note) BETWEEN 1 AND 1000)",
    )
    op.create_index(
        "ix_accounts_status_role",
        "accounts",
        ["status", "role"],
        unique=False,
    )

    op.create_table(
        "registration_invitations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_account_id", sa.String(length=36), nullable=False),
        sa.Column("used_by_account_id", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "code_hash ~ '^[0-9a-f]{64}$'",
            name="ck_registration_invitations_code_hash",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_registration_invitations_expiry",
        ),
        sa.CheckConstraint(
            "used_at IS NULL OR (used_at >= created_at AND used_at < expires_at)",
            name="ck_registration_invitations_used_at",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_registration_invitations_revoked_at",
        ),
        sa.CheckConstraint(
            "(used_at IS NULL AND used_by_account_id IS NULL) OR "
            "(used_at IS NOT NULL AND used_by_account_id IS NOT NULL)",
            name="ck_registration_invitations_used_by",
        ),
        sa.CheckConstraint(
            "used_at IS NULL OR revoked_at IS NULL",
            name="ck_registration_invitations_terminal_state",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"],
            ["accounts.id"],
            name="fk_registration_invitations_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["used_by_account_id"],
            ["accounts.id"],
            name="fk_registration_invitations_used_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_registration_invitations"),
        sa.UniqueConstraint(
            "code_hash",
            name="uq_registration_invitations_code_hash",
        ),
        sa.UniqueConstraint(
            "used_by_account_id",
            name="uq_registration_invitations_used_by",
        ),
    )
    op.create_index(
        "ix_registration_invitations_active",
        "registration_invitations",
        ["expires_at", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_registration_invitations_active",
        table_name="registration_invitations",
        if_exists=True,
    )
    op.drop_table("registration_invitations")
    op.drop_index("ix_accounts_status_role", table_name="accounts", if_exists=True)
    op.drop_constraint("ck_accounts_admin_note", "accounts", type_="check")
    op.drop_constraint("ck_accounts_status_disabled", "accounts", type_="check")
    op.drop_constraint("ck_accounts_status", "accounts", type_="check")
    op.drop_column("accounts", "admin_note")
    op.drop_column("accounts", "status")
