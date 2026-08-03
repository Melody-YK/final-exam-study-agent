from datetime import UTC, datetime

import pytest

from study_agent.modules.learning.scheduling import next_review_at
from study_contracts import MasteryLevel

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("level", "correct", "days"),
    [
        (MasteryLevel.NEW, True, 0),
        (MasteryLevel.LEARNING, True, 1),
        (MasteryLevel.REVIEW, True, 3),
        (MasteryLevel.MASTERED, True, 7),
        (MasteryLevel.NEW, False, 0),
        (MasteryLevel.REVIEW, False, 1),
    ],
)
def test_review_schedule_uses_fixed_utc_intervals(level, correct, days) -> None:
    assert next_review_at(level, correct=correct, now=NOW) == NOW.replace(hour=12) + __import__(
        "datetime"
    ).timedelta(days=days)


def test_review_schedule_rejects_naive_clock() -> None:
    with pytest.raises(ValueError):
        next_review_at(MasteryLevel.NEW, correct=True, now=datetime(2026, 8, 2))
