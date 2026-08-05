"""bind learning-unit evidence supplements to the parsed source version

Revision ID: 20260805_0021
Revises: 20260805_0020
Create Date: 2026-08-05 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0021"
down_revision: str | None = "20260805_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "learning_unit_evidence_supplements",
        sa.Column("source_content_sha256", sa.String(length=64), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE learning_unit_evidence_supplements AS supplement "
            "SET source_content_sha256 = source.content_sha256 "
            "FROM learning_unit_sources AS source "
            "WHERE source.id = supplement.source_id "
            "AND source.course_id = supplement.course_id "
            "AND source.user_id = supplement.user_id"
        )
    )
    op.alter_column(
        "learning_unit_evidence_supplements",
        "source_content_sha256",
        nullable=False,
    )
    op.create_check_constraint(
        "ck_learning_evidence_supplements_source_hash",
        "learning_unit_evidence_supplements",
        "source_content_sha256 ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_learning_evidence_supplements_source_hash",
        "learning_unit_evidence_supplements",
        type_="check",
    )
    op.drop_column("learning_unit_evidence_supplements", "source_content_sha256")
