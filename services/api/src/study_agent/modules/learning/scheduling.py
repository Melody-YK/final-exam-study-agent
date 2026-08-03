"""Small UTC-only review scheduling rule for V1."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from study_contracts import MasteryLevel

REVIEW_INTERVAL_DAYS: dict[MasteryLevel, int] = {
    MasteryLevel.NEW: 0,
    MasteryLevel.LEARNING: 1,
    MasteryLevel.REVIEW: 3,
    MasteryLevel.MASTERED: 7,
}


def next_review_at(
    level: MasteryLevel,
    *,
    correct: bool,
    now: datetime,
) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current = now.astimezone(UTC)
    days = (0 if level is MasteryLevel.NEW else 1) if not correct else REVIEW_INTERVAL_DAYS[level]
    return current + timedelta(days=days)
