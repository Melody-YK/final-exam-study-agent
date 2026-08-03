"""Deterministic objective-question scoring."""

from __future__ import annotations

from dataclasses import dataclass

from study_contracts import QuestionType


class InvalidAnswerError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScoreResult:
    submitted_answer: str
    correct: bool
    score: int


def score_answer(
    question_type: QuestionType | str,
    correct_answer: str,
    submitted_answer: str,
) -> ScoreResult:
    normalized_type = QuestionType(question_type)
    answer = submitted_answer.strip()
    expected = correct_answer.strip()
    if normalized_type is QuestionType.TRUE_FALSE:
        answer = answer.casefold()
        expected = expected.casefold()
        if answer not in {"true", "false"}:
            raise InvalidAnswerError("true_false answers must be true or false")
    elif not answer:
        raise InvalidAnswerError("answer must not be blank")
    correct = answer == expected
    return ScoreResult(submitted_answer=answer, correct=correct, score=int(correct))
