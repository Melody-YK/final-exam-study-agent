from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from study_contracts import (
    EvidenceReference,
    LearningSummary,
    LearningUnit,
    LearningUnitPracticeStatus,
    LearningUnitSource,
    PracticeBatchRequest,
    PracticeBatchSnapshot,
    PracticeQuestionView,
    PracticeSessionSnapshot,
    PracticeTutorRequest,
    Question,
    QuestionOption,
    QuestionType,
)


def _source(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "document_id": "doc-1",
        "revision_id": "rev-1",
        "chunk_id": "chunk-1",
        "content_sha256": "a" * 64,
        "locator": {"kind": "page", "ordinal": 1},
    }
    value.update(overrides)
    return value


def _evidence() -> EvidenceReference:
    return EvidenceReference(
        document_id="doc-1",
        revision_id="rev-1",
        chunk_id="chunk-1",
        content_sha256="a" * 64,
        locator={"kind": "page", "ordinal": 1},
        quote="公开 fixture 的依据",
    )


def test_learning_unit_requires_valid_source_when_available() -> None:
    unit = LearningUnit(
        id="unit-1",
        course_id="course-1",
        canonical_key="section:function-basics",
        label="函数基础",
        kind="section",
        status="available",
        sources=[LearningUnitSource(**_source())],
    )

    assert unit.sources[0].locator.kind == "page"

    ready_data = unit.model_dump()
    ready_data.update(
        {
            "practice_status": LearningUnitPracticeStatus.READY,
            "evidence_chunk_count": 1,
            "evidence_char_count": 80,
        }
    )
    ready = LearningUnit(**ready_data)
    assert ready.practice_status is LearningUnitPracticeStatus.READY

    with pytest.raises(ValidationError):
        invalid_data = unit.model_dump()
        invalid_data.update(
            {"practice_status": LearningUnitPracticeStatus.READY, "evidence_chunk_count": 0}
        )
        LearningUnit(**invalid_data)

    with pytest.raises(ValidationError):
        LearningUnit(
            id="unit-2",
            course_id="course-1",
            canonical_key="concept:missing",
            label="没有依据",
            kind="concept",
            status="available",
            sources=[],
        )


def test_question_contract_restricts_v1_types_and_answers() -> None:
    question = Question(
        id="question-1",
        course_id="course-1",
        learning_unit_id="unit-1",
        source_revision_id="rev-1",
        question_type=QuestionType.SINGLE_CHOICE,
        prompt="函数的定义域是什么?",
        options=[
            QuestionOption(id="a", label="输入集合"),
            QuestionOption(id="b", label="输出集合"),
        ],
        correct_answer="a",
        explanation="定义域是允许输入的集合。",
        evidence_refs=[_evidence()],
        difficulty=1,
        content_sha256="b" * 64,
    )
    assert question.correct_answer == "a"

    with pytest.raises(ValidationError):
        Question(
            **question.model_dump(exclude={"question_type"}),
            question_type="essay",  # type: ignore[arg-type]
        )


def test_requests_and_batch_snapshots_are_bounded_and_terminal_time_is_consistent() -> None:
    with pytest.raises(ValidationError):
        PracticeBatchRequest(learning_unit_ids=["unit-1"] * 2, question_count=2)

    with pytest.raises(ValidationError):
        PracticeBatchRequest(
            learning_unit_ids=[f"unit-{index}" for index in range(11)],
            question_count=10,
        )

    with pytest.raises(ValidationError):
        PracticeBatchRequest(learning_unit_ids=["unit-1", "unit-2"], question_count=1)

    largest_batch = PracticeBatchRequest(
        learning_unit_ids=[f"unit-{index}" for index in range(10)],
        question_count=10,
    )
    assert len(largest_batch.learning_unit_ids) == 10

    now = datetime(2026, 8, 2, tzinfo=UTC)
    snapshot = PracticeBatchSnapshot(
        id="batch-1",
        course_id="course-1",
        learning_unit_ids=["unit-1"],
        target_question_count=2,
        status="succeeded",
        phase="saving",
        completed_items=2,
        total_items=2,
        question_ids=["question-1", "question-2"],
        created_at=now,
        started_at=now,
        completed_at=now,
    )
    assert snapshot.status.value == "succeeded"

    with pytest.raises(ValidationError):
        PracticeBatchSnapshot.model_validate(
            snapshot.model_dump(mode="python", round_trip=True) | {"completed_at": None}
        )


def test_stale_question_view_allows_empty_evidence_but_ready_question_does_not() -> None:
    stale = PracticeQuestionView(
        id="question-1",
        learning_unit_id="unit-1",
        question_type="single_choice",
        prompt="题干",
        options=[QuestionOption(id="a", label="A"), QuestionOption(id="b", label="B")],
        difficulty=1,
        status="stale",
        evidence_refs=[],
    )
    assert stale.evidence_refs == []

    with pytest.raises(ValidationError):
        PracticeQuestionView(
            **stale.model_dump(
                exclude={
                    "answered",
                    "outcome",
                    "submitted_answer",
                    "explanation",
                    "mastery_reason",
                    "viewed_hint",
                }
            ),
            answered=True,
            outcome="correct",
        )

    answered = PracticeQuestionView(
        **stale.model_dump(
            exclude={
                "answered",
                "outcome",
                "submitted_answer",
                "explanation",
                "mastery_reason",
                "viewed_hint",
            }
        ),
        answered=True,
        outcome="correct",
        submitted_answer="a",
        explanation="解释",
        mastery_reason="掌握度已更新",
        viewed_hint=False,
    )
    assert answered.submitted_answer == "a"

    with pytest.raises(ValidationError):
        Question(
            id="question-2",
            course_id="course-1",
            learning_unit_id="unit-1",
            source_revision_id="rev-1",
            question_type="single_choice",
            prompt="题干",
            options=[QuestionOption(id="a", label="A"), QuestionOption(id="b", label="B")],
            correct_answer="a",
            explanation="解释",
            evidence_refs=[],
            difficulty=1,
            content_sha256="b" * 64,
        )


def test_session_and_summary_contracts_reject_inconsistent_terminal_values() -> None:
    with pytest.raises(ValidationError):
        PracticeSessionSnapshot(
            id="session-1",
            course_id="course-1",
            status="active",
            question_count=1,
            questions=[],
            started_at=datetime(2026, 8, 2, tzinfo=UTC),
            completed_at=datetime(2026, 8, 2, tzinfo=UTC),
        )

    with pytest.raises(ValidationError):
        LearningSummary(
            course_id="course-1",
            total_questions=1,
            correct_questions=2,
            accuracy=1.0,
            due_review_count=0,
            next_action="继续练习",
        )


def test_practice_tutor_request_bounds_conversation_history() -> None:
    request = PracticeTutorRequest(
        message="这道题应该从哪里入手?",
        history=[{"role": "assistant", "content": "先找题干里的定义关键词。"}],
    )
    assert request.history[0].role == "assistant"

    with pytest.raises(ValidationError):
        PracticeTutorRequest(
            message="继续",
            history=[{"role": "user", "content": str(index)} for index in range(9)],
        )
