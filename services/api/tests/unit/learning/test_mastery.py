from study_agent.modules.learning.mastery import MasteryState, update_mastery
from study_contracts import MasteryLevel


def test_mastery_rises_one_level_and_explains_hint_cap() -> None:
    result = update_mastery(MasteryState(level=MasteryLevel.NEW), correct=True, viewed_hint=True)
    assert result.level is MasteryLevel.LEARNING
    assert "提示" in result.reason


def test_mastery_drops_and_clamps_at_bounds() -> None:
    assert (
        update_mastery(MasteryState(level=MasteryLevel.MASTERED), correct=False).level
        is MasteryLevel.REVIEW
    )
    assert (
        update_mastery(MasteryState(level=MasteryLevel.NEW), correct=False).level
        is MasteryLevel.NEW
    )


def test_mastery_caps_at_mastered() -> None:
    assert (
        update_mastery(MasteryState(level=MasteryLevel.MASTERED), correct=True).level
        is MasteryLevel.MASTERED
    )
