"""add bounded query planning and retrieval diagnostics

Revision ID: 20260804_0017
Revises: 20260804_0016
Create Date: 2026-08-04 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804_0017"
down_revision: str | None = "20260804_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("query_runs", sa.Column("query_intent", sa.String(length=32), nullable=True))
    op.add_column("query_runs", sa.Column("standalone_question", sa.Text(), nullable=True))
    op.add_column(
        "query_runs",
        sa.Column(
            "retrieval_rounds",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "query_runs",
        sa.Column("retrieval_diagnostic", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_query_runs_intent",
        "query_runs",
        "query_intent IS NULL OR query_intent IN "
        "('new_question', 'follow_up', 'comparison', 'summary', 'clarification')",
    )
    op.create_check_constraint(
        "ck_query_runs_retrieval_diagnostic",
        "query_runs",
        "retrieval_diagnostic IS NULL OR retrieval_diagnostic IN "
        "('initial_sufficient', 'repair_succeeded', 'index_unavailable', "
        "'no_candidates', 'low_relevance')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_query_runs_retrieval_diagnostic", "query_runs", type_="check")
    op.drop_constraint("ck_query_runs_intent", "query_runs", type_="check")
    op.drop_column("query_runs", "retrieval_diagnostic")
    op.drop_column("query_runs", "retrieval_rounds")
    op.drop_column("query_runs", "standalone_question")
    op.drop_column("query_runs", "query_intent")
