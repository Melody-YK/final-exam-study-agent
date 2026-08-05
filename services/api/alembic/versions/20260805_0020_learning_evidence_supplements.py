"""add versioned learning-unit evidence supplements

Revision ID: 20260805_0020
Revises: 20260804_0019
Create Date: 2026-08-05 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0020"
down_revision: str | None = "20260804_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_learning_unit_sources_id_scope",
        "learning_unit_sources",
        ["id", "course_id", "user_id"],
    )
    op.create_table(
        "learning_unit_evidence_supplements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("unit_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
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
            "role IN ('complete_prototype', 'reference_solution', 'additional_context')",
            name="ck_learning_evidence_supplements_role",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'revoked')",
            name="ck_learning_evidence_supplements_status",
        ),
        sa.CheckConstraint(
            "btrim(text) <> ''",
            name="ck_learning_evidence_supplements_text_nonblank",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_learning_evidence_supplements_hash",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id", "course_id", "user_id"],
            ["learning_units.id", "learning_units.course_id", "learning_units.user_id"],
            name="fk_learning_evidence_supplements_unit_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "course_id", "user_id"],
            [
                "learning_unit_sources.id",
                "learning_unit_sources.course_id",
                "learning_unit_sources.user_id",
            ],
            name="fk_learning_evidence_supplements_source_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "course_id",
            "user_id",
            name="uq_learning_evidence_supplements_id_scope",
        ),
    )
    op.create_index(
        "uq_learning_evidence_supplements_active_unit",
        "learning_unit_evidence_supplements",
        ["user_id", "course_id", "unit_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_learning_evidence_supplements_scope_status",
        "learning_unit_evidence_supplements",
        ["user_id", "course_id", "status"],
    )

    op.add_column(
        "practice_question_evidence",
        sa.Column("supplement_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_practice_question_evidence_supplement_scope",
        "practice_question_evidence",
        "learning_unit_evidence_supplements",
        ["supplement_id", "course_id", "user_id"],
        ["id", "course_id", "user_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_practice_question_evidence_supplement_scope",
        "practice_question_evidence",
        type_="foreignkey",
    )
    op.drop_column("practice_question_evidence", "supplement_id")
    op.drop_index(
        "ix_learning_evidence_supplements_scope_status",
        table_name="learning_unit_evidence_supplements",
    )
    op.drop_index(
        "uq_learning_evidence_supplements_active_unit",
        table_name="learning_unit_evidence_supplements",
    )
    op.drop_table("learning_unit_evidence_supplements")
    op.drop_constraint(
        "uq_learning_unit_sources_id_scope",
        "learning_unit_sources",
        type_="unique",
    )
