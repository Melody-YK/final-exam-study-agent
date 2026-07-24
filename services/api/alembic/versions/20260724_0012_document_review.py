"""add administrator document review state

Revision ID: 20260724_0012
Revises: 20260724_0011
Create Date: 2026-07-24 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_0012"
down_revision: str | None = "20260724_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("review_status", sa.String(length=16), nullable=True),
    )
    op.add_column("documents", sa.Column("review_note", sa.Text(), nullable=True))
    op.add_column(
        "documents",
        sa.Column("reviewed_by_account_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute("UPDATE documents SET review_status = 'approved'")
    op.alter_column(
        "documents",
        "review_status",
        existing_type=sa.String(length=16),
        nullable=False,
        server_default=sa.text("'pending'"),
    )
    op.create_foreign_key(
        "fk_documents_reviewed_by_account",
        "documents",
        "accounts",
        ["reviewed_by_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_documents_review_status",
        "documents",
        "review_status IN ('pending', 'approved', 'rejected')",
    )
    op.create_check_constraint(
        "ck_documents_review_note",
        "documents",
        "review_note IS NULL OR (review_note = btrim(review_note) "
        "AND length(review_note) BETWEEN 1 AND 500)",
    )
    op.create_check_constraint(
        "ck_documents_review_actor_time",
        "documents",
        "(reviewed_by_account_id IS NULL) = (reviewed_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_documents_review_state",
        "documents",
        "(review_status NOT IN ('pending', 'approved', 'rejected')) OR "
        "(review_status = 'pending' AND reviewed_by_account_id IS NULL "
        "AND reviewed_at IS NULL AND review_note IS NULL) OR "
        "(review_status = 'approved' AND (review_note IS NULL "
        "OR reviewed_by_account_id IS NOT NULL)) OR "
        "(review_status = 'rejected' AND reviewed_by_account_id IS NOT NULL "
        "AND reviewed_at IS NOT NULL AND review_note IS NOT NULL)",
    )
    op.create_index(
        "ix_documents_review_queue",
        "documents",
        ["review_status", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_documents_review_queue", table_name="documents", if_exists=True)
    op.drop_constraint("ck_documents_review_state", "documents", type_="check")
    op.drop_constraint("ck_documents_review_actor_time", "documents", type_="check")
    op.drop_constraint("ck_documents_review_note", "documents", type_="check")
    op.drop_constraint("ck_documents_review_status", "documents", type_="check")
    op.drop_constraint("fk_documents_reviewed_by_account", "documents", type_="foreignkey")
    op.drop_column("documents", "reviewed_at")
    op.drop_column("documents", "reviewed_by_account_id")
    op.drop_column("documents", "review_note")
    op.drop_column("documents", "review_status")
