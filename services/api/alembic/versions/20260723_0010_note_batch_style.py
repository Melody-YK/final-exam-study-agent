"""add note batch style

Revision ID: 20260723_0010
Revises: 20260723_0009
Create Date: 2026-07-23 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0010"
down_revision: str | None = "20260723_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "note_generation_batches",
        sa.Column(
            "style",
            sa.String(length=32),
            server_default=sa.text("'exam_focus'"),
            nullable=False,
        ),
    )
    op.alter_column("note_generation_batches", "style", server_default=None)
    op.create_check_constraint(
        "ck_note_generation_batches_style",
        "note_generation_batches",
        "style IN ('exam_focus', 'outline', 'complete')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_note_generation_batches_style",
        "note_generation_batches",
        type_="check",
    )
    op.drop_column("note_generation_batches", "style")
