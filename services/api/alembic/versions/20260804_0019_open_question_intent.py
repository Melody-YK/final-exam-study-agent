"""allow an open-ended practice tutor intent

Revision ID: 20260804_0019
Revises: 20260804_0018
Create Date: 2026-08-04 23:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260804_0019"
down_revision: str | None = "20260804_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INTENT_CONSTRAINT = "ck_conversation_messages_intent"


def upgrade() -> None:
    op.drop_constraint(_INTENT_CONSTRAINT, "conversation_messages", type_="check")
    op.create_check_constraint(
        _INTENT_CONSTRAINT,
        "conversation_messages",
        "intent IS NULL OR intent IN "
        "('hint', 'clarify', 'example', 'answer_check', 'solution', 'reflection', 'source', "
        "'open_question')",
    )


def downgrade() -> None:
    op.drop_constraint(_INTENT_CONSTRAINT, "conversation_messages", type_="check")
    op.create_check_constraint(
        _INTENT_CONSTRAINT,
        "conversation_messages",
        "intent IS NULL OR intent IN "
        "('hint', 'clarify', 'example', 'answer_check', 'solution', 'reflection', 'source')",
    )
