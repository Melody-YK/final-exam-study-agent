"""support constructed-response practice questions

Revision ID: 20260804_0015
Revises: 20260802_0014
Create Date: 2026-08-04 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0015"
down_revision: str | None = "20260802_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_practice_questions_type", "practice_questions", type_="check")
    op.create_check_constraint(
        "ck_practice_questions_type",
        "practice_questions",
        "question_type IN ('single_choice', 'true_false', 'short_answer', 'calculation')",
    )
    op.alter_column(
        "practice_questions",
        "correct_answer",
        existing_type=sa.String(length=32),
        type_=sa.Text(),
        existing_nullable=False,
    )
    op.alter_column(
        "practice_attempts",
        "answer",
        existing_type=sa.String(length=32),
        type_=sa.Text(),
        existing_nullable=False,
    )
    op.add_column("practice_attempts", sa.Column("grading_feedback", sa.Text(), nullable=True))


def downgrade() -> None:
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM practice_questions "
            "WHERE question_type IN ('short_answer', 'calculation')) THEN "
            "RAISE EXCEPTION 'remove constructed-response questions before downgrade'; "
            "END IF; END $$"
        )
    )
    op.drop_column("practice_attempts", "grading_feedback")
    op.alter_column(
        "practice_attempts",
        "answer",
        existing_type=sa.Text(),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.alter_column(
        "practice_questions",
        "correct_answer",
        existing_type=sa.Text(),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.drop_constraint("ck_practice_questions_type", "practice_questions", type_="check")
    op.create_check_constraint(
        "ck_practice_questions_type",
        "practice_questions",
        "question_type IN ('single_choice', 'true_false')",
    )
