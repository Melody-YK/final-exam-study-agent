"""Explainable bounded mastery updates."""

from __future__ import annotations

from dataclasses import dataclass

from study_contracts import MasteryLevel


@dataclass(frozen=True, slots=True)
class MasteryState:
    level: MasteryLevel = MasteryLevel.NEW
    attempt_count: int = 0
    correct_count: int = 0
    last_score: int = 0


@dataclass(frozen=True, slots=True)
class MasteryResult:
    previous_level: MasteryLevel
    level: MasteryLevel
    attempt_count: int
    correct_count: int
    last_score: int
    reason: str


def update_mastery(
    state: MasteryState, *, correct: bool, viewed_hint: bool = False
) -> MasteryResult:
    previous = state.level
    previous_value = previous.value_number
    if correct:
        step = 1
        next_value = min(3, previous_value + step)
        reason = "答对, 掌握度上升一级。"
        if viewed_hint:
            next_value = min(next_value, previous_value + 1)
            reason = "查看提示后答对, 掌握度最多上升一级。"
    else:
        next_value = max(0, previous_value - 1)
        reason = "答错, 掌握度下降一级并安排较早复习。"
    level = list(MasteryLevel)[next_value]
    return MasteryResult(
        previous_level=previous,
        level=level,
        attempt_count=state.attempt_count + 1,
        correct_count=state.correct_count + int(correct),
        last_score=int(correct),
        reason=reason,
    )
