"""repair databases upgraded by the pre-release learning-loop migration

Revision ID: 20260802_0014
Revises: 20260801_0013
Create Date: 2026-08-02 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0014"
down_revision: str | None = "20260801_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_info(table_name: str) -> dict[str, dict[str, object]]:
    return {
        str(column["name"]): column for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _check_names(table_name: str) -> set[str]:
    return {
        str(constraint["name"])
        for constraint in sa.inspect(op.get_bind()).get_check_constraints(table_name)
        if constraint.get("name") is not None
    }


def _ensure_cascade_fk(
    table_name: str,
    constraint_name: str,
    local_columns: list[str],
    remote_table: str,
    remote_columns: list[str],
) -> None:
    inspector = sa.inspect(op.get_bind())
    current = next(
        (
            foreign_key
            for foreign_key in inspector.get_foreign_keys(table_name)
            if foreign_key.get("name") == constraint_name
        ),
        None,
    )
    current_ondelete = str((current or {}).get("options", {}).get("ondelete", "")).upper()
    if current is not None and current_ondelete == "CASCADE":
        return
    if current is not None:
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")
    op.create_foreign_key(
        constraint_name,
        table_name,
        remote_table,
        local_columns,
        remote_columns,
        ondelete="CASCADE",
    )


def upgrade() -> None:
    """Bring an already-applied pre-release 0013 schema to the final shape.

    A fresh database already has these columns and CASCADE foreign keys from
    0013, so every operation below is conditional and becomes a no-op there.
    """

    columns = _column_info("practice_attempts")
    if "previous_mastery_level" not in columns:
        op.add_column(
            "practice_attempts",
            sa.Column(
                "previous_mastery_level",
                sa.String(16),
                nullable=False,
                server_default=sa.text("'new'"),
            ),
        )
    elif columns["previous_mastery_level"].get("nullable"):
        op.execute(
            sa.text(
                "UPDATE practice_attempts SET previous_mastery_level = 'new' "
                "WHERE previous_mastery_level IS NULL"
            )
        )
        op.alter_column("practice_attempts", "previous_mastery_level", nullable=False)

    columns = _column_info("practice_attempts")
    if "mastery_level" not in columns:
        op.add_column(
            "practice_attempts",
            sa.Column(
                "mastery_level",
                sa.String(16),
                nullable=False,
                server_default=sa.text("'new'"),
            ),
        )
    elif columns["mastery_level"].get("nullable"):
        op.execute(
            sa.text(
                "UPDATE practice_attempts SET mastery_level = 'new' WHERE mastery_level IS NULL"
            )
        )
        op.alter_column("practice_attempts", "mastery_level", nullable=False)

    columns = _column_info("practice_attempts")
    if "next_review_at" not in columns:
        op.add_column(
            "practice_attempts",
            sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(
            sa.text(
                "UPDATE practice_attempts SET next_review_at = answered_at "
                "WHERE next_review_at IS NULL"
            )
        )
        op.alter_column("practice_attempts", "next_review_at", nullable=False)
    elif columns["next_review_at"].get("nullable"):
        op.execute(
            sa.text(
                "UPDATE practice_attempts SET next_review_at = answered_at "
                "WHERE next_review_at IS NULL"
            )
        )
        op.alter_column("practice_attempts", "next_review_at", nullable=False)

    checks = _check_names("practice_attempts")
    if "ck_practice_attempts_previous_mastery" not in checks:
        op.create_check_constraint(
            "ck_practice_attempts_previous_mastery",
            "practice_attempts",
            "previous_mastery_level IN ('new', 'learning', 'review', 'mastered')",
        )
    if "ck_practice_attempts_mastery" not in checks:
        op.create_check_constraint(
            "ck_practice_attempts_mastery",
            "practice_attempts",
            "mastery_level IN ('new', 'learning', 'review', 'mastered')",
        )
    if "ck_practice_attempts_review_time" not in checks:
        op.create_check_constraint(
            "ck_practice_attempts_review_time",
            "practice_attempts",
            "next_review_at >= answered_at",
        )

    _ensure_cascade_fk(
        "learning_unit_sources",
        "fk_learning_unit_sources_document_scope",
        ["document_id", "course_id", "user_id"],
        "documents",
        ["id", "course_id", "user_id"],
    )
    _ensure_cascade_fk(
        "learning_unit_sources",
        "fk_learning_unit_sources_revision_scope",
        ["document_id", "revision_id"],
        "document_revisions",
        ["document_id", "id"],
    )
    _ensure_cascade_fk(
        "learning_unit_sources",
        "fk_learning_unit_sources_chunk_scope",
        ["chunk_id", "revision_id"],
        "revision_chunks",
        ["id", "revision_id"],
    )
    _ensure_cascade_fk(
        "practice_question_evidence",
        "fk_practice_question_evidence_document_scope",
        ["document_id", "course_id", "user_id"],
        "documents",
        ["id", "course_id", "user_id"],
    )
    _ensure_cascade_fk(
        "practice_question_evidence",
        "fk_practice_question_evidence_revision_scope",
        ["document_id", "revision_id"],
        "document_revisions",
        ["document_id", "id"],
    )
    _ensure_cascade_fk(
        "practice_question_evidence",
        "fk_practice_question_evidence_chunk_scope",
        ["chunk_id", "revision_id"],
        "revision_chunks",
        ["id", "revision_id"],
    )


def downgrade() -> None:
    """Keep the final 0013 schema intact when leaving this repair marker."""
