import pytest

from study_agent.modules.learning.scoring import InvalidAnswerError, score_answer
from study_contracts import QuestionType


@pytest.mark.parametrize(
    ("question_type", "correct", "submitted", "expected"),
    [
        (QuestionType.SINGLE_CHOICE, "a", "a", True),
        (QuestionType.SINGLE_CHOICE, "a", "b", False),
        (QuestionType.TRUE_FALSE, "true", "TRUE", True),
        (QuestionType.TRUE_FALSE, "false", "true", False),
    ],
)
def test_score_answer_covers_v1_branches(question_type, correct, submitted, expected) -> None:
    result = score_answer(question_type, correct, submitted)
    assert result.correct is expected
    assert result.score == int(expected)


def test_score_answer_rejects_invalid_objective_input() -> None:
    with pytest.raises(InvalidAnswerError):
        score_answer(QuestionType.TRUE_FALSE, "true", "maybe")
    with pytest.raises(InvalidAnswerError):
        score_answer(QuestionType.SINGLE_CHOICE, "a", " ")
