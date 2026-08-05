"""Stable contracts for source-bound active recall and review scheduling."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from study_contracts.documents import ContractModel, NonEmptyString, Sha256Hex, SourceLocator


class LearningUnitKind(StrEnum):
    SECTION = "section"
    CONCEPT = "concept"


class LearningUnitStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


class LearningUnitPracticeStatus(StrEnum):
    READY = "ready"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    STALE = "stale"


class LearningUnitPracticeMode(StrEnum):
    """Generation strategy implied by the selected learning-unit material."""

    KNOWLEDGE_RECALL = "knowledge_recall"
    EXERCISE_VARIANT = "exercise_variant"


class QuestionType(StrEnum):
    SINGLE_CHOICE = "single_choice"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"
    CALCULATION = "calculation"

    @property
    def is_constructed_response(self) -> bool:
        return self in {QuestionType.SHORT_ANSWER, QuestionType.CALCULATION}


class QuestionStatus(StrEnum):
    READY = "ready"
    STALE = "stale"
    INVALID = "invalid"


class PracticeBatchStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PARTIAL_SUCCESS = "partial_success"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PracticeBatchPhase(StrEnum):
    VALIDATING_INPUTS = "validating_inputs"
    GENERATING = "generating"
    VALIDATING_OUTPUT = "validating_output"
    SAVING = "saving"


class PracticeSessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PracticeTutorMode(StrEnum):
    HINT = "hint"
    REVIEW = "review"


class PracticeTutorIntent(StrEnum):
    HINT = "hint"
    CLARIFY = "clarify"
    EXAMPLE = "example"
    ANSWER_CHECK = "answer_check"
    SOLUTION = "solution"
    REFLECTION = "reflection"
    SOURCE = "source"
    OPEN_QUESTION = "open_question"


class MasteryLevel(StrEnum):
    NEW = "new"
    LEARNING = "learning"
    REVIEW = "review"
    MASTERED = "mastered"

    @property
    def value_number(self) -> int:
        return list(type(self)).index(self)


class LearningSourceStatus(StrEnum):
    VALID = "valid"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class AttemptOutcome(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"


class EvidenceReference(ContractModel):
    """A user-visible citation that can be revalidated before display."""

    document_id: NonEmptyString
    document_name: NonEmptyString | None = None
    revision_id: NonEmptyString
    chunk_id: NonEmptyString
    content_sha256: Sha256Hex
    locator: SourceLocator
    quote: NonEmptyString = Field(max_length=2_000)


class LearningUnitSource(ContractModel):
    document_id: NonEmptyString
    revision_id: NonEmptyString
    chunk_id: NonEmptyString
    content_sha256: Sha256Hex
    locator: SourceLocator
    status: LearningSourceStatus = LearningSourceStatus.VALID


class LearningUnit(ContractModel):
    id: NonEmptyString
    course_id: NonEmptyString
    canonical_key: NonEmptyString = Field(max_length=255)
    label: NonEmptyString = Field(max_length=255)
    kind: LearningUnitKind
    parent_id: NonEmptyString | None = None
    status: LearningUnitStatus
    practice_status: LearningUnitPracticeStatus = LearningUnitPracticeStatus.INSUFFICIENT_EVIDENCE
    practice_mode: LearningUnitPracticeMode = LearningUnitPracticeMode.KNOWLEDGE_RECALL
    prototype_question_type: QuestionType | None = None
    evidence_chunk_count: int = Field(default=0, ge=0)
    evidence_char_count: int = Field(default=0, ge=0)
    sources: list[LearningUnitSource] = Field(default_factory=list)
    mastery_level: MasteryLevel = MasteryLevel.NEW
    next_review_at: datetime | None = None

    @field_validator("next_review_at")
    @classmethod
    def review_time_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("next_review_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def status_must_match_sources(self) -> Self:
        has_valid_source = any(
            source.status is LearningSourceStatus.VALID for source in self.sources
        )
        if self.status is LearningUnitStatus.AVAILABLE and not has_valid_source:
            raise ValueError("an available learning unit requires a valid source")
        if self.practice_status is LearningUnitPracticeStatus.READY and (
            self.status is not LearningUnitStatus.AVAILABLE or self.evidence_chunk_count < 1
        ):
            raise ValueError("a practice-ready learning unit requires current evidence")
        return self


class QuestionOption(ContractModel):
    id: NonEmptyString = Field(max_length=32)
    label: NonEmptyString = Field(max_length=1_000)


class Question(ContractModel):
    id: NonEmptyString
    course_id: NonEmptyString
    learning_unit_id: NonEmptyString
    source_revision_id: NonEmptyString
    question_type: QuestionType
    prompt: NonEmptyString = Field(max_length=4_000)
    options: list[QuestionOption] = Field(default_factory=list, max_length=4)
    correct_answer: NonEmptyString = Field(max_length=8_000)
    explanation: NonEmptyString = Field(max_length=8_000)
    evidence_refs: list[EvidenceReference] = Field(min_length=1, max_length=8)
    difficulty: int = Field(ge=1, le=3)
    status: QuestionStatus = QuestionStatus.READY
    content_sha256: Sha256Hex

    @model_validator(mode="after")
    def answer_and_options_must_match(self) -> Self:
        option_ids = [option.id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("question options must be unique")
        if self.question_type is QuestionType.SINGLE_CHOICE:
            if len(self.options) < 2 or self.correct_answer not in option_ids:
                raise ValueError("single choice questions require a valid option answer")
        elif self.question_type is QuestionType.TRUE_FALSE and (
            set(option_ids) != {"true", "false"} or self.correct_answer not in {"true", "false"}
        ):
            raise ValueError("true false questions require true and false options")
        elif self.question_type.is_constructed_response and self.options:
            raise ValueError("constructed response questions must not expose answer options")
        return self


class PracticeBatchRequest(ContractModel):
    learning_unit_ids: list[NonEmptyString] = Field(min_length=1, max_length=10)
    question_count: int = Field(ge=1, le=10)

    @field_validator("learning_unit_ids")
    @classmethod
    def unit_ids_must_be_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("learning_unit_ids must be unique")
        return values

    @model_validator(mode="after")
    def question_count_must_cover_scopes(self) -> Self:
        if self.question_count < len(self.learning_unit_ids):
            raise ValueError("question_count must be at least the number of selected scopes")
        return self


class PracticeBatchItem(ContractModel):
    id: NonEmptyString
    question_id: NonEmptyString | None = None
    status: Literal["queued", "succeeded", "failed"]
    failure_code: str | None = None
    attempt_count: int = Field(ge=0)


class PracticeBatchSnapshot(ContractModel):
    id: NonEmptyString
    course_id: NonEmptyString
    learning_unit_ids: list[NonEmptyString] = Field(min_length=1, max_length=10)
    target_question_count: int = Field(ge=1, le=10)
    status: PracticeBatchStatus
    phase: PracticeBatchPhase | None = None
    completed_items: int = Field(ge=0)
    total_items: int = Field(ge=1, le=10)
    question_ids: list[NonEmptyString] = Field(default_factory=list, max_length=10)
    items: list[PracticeBatchItem] = Field(default_factory=list, max_length=10)
    failure_code: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def terminal_time_must_match_status(self) -> Self:
        terminal = self.status in {
            PracticeBatchStatus.PARTIAL_SUCCESS,
            PracticeBatchStatus.SUCCEEDED,
            PracticeBatchStatus.FAILED,
            PracticeBatchStatus.CANCELLED,
        }
        if terminal != (self.completed_at is not None):
            raise ValueError("terminal batch status must match completed_at")
        if self.completed_items > self.total_items:
            raise ValueError("completed_items must not exceed total_items")
        return self

    @model_validator(mode="after")
    def question_count_must_cover_scopes(self) -> Self:
        if self.target_question_count < len(self.learning_unit_ids):
            raise ValueError("target_question_count must be at least the number of selected scopes")
        return self


class PracticeSessionRequest(ContractModel):
    question_ids: list[NonEmptyString] = Field(min_length=1, max_length=10)

    @field_validator("question_ids")
    @classmethod
    def question_ids_must_be_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("question_ids must be unique")
        return values


class PracticeQuestionView(ContractModel):
    id: NonEmptyString
    learning_unit_id: NonEmptyString
    question_type: QuestionType
    practice_mode: LearningUnitPracticeMode = LearningUnitPracticeMode.KNOWLEDGE_RECALL
    prompt: NonEmptyString
    options: list[QuestionOption] = Field(max_length=4)
    difficulty: int = Field(ge=1, le=3)
    status: QuestionStatus
    evidence_refs: list[EvidenceReference] = Field(max_length=8)
    answered: bool = False
    outcome: AttemptOutcome | None = None
    submitted_answer: NonEmptyString | None = Field(default=None, max_length=8_000)
    explanation: NonEmptyString | None = Field(default=None, max_length=8_000)
    grading_feedback: NonEmptyString | None = Field(default=None, max_length=2_000)
    mastery_reason: NonEmptyString | None = Field(default=None, max_length=1_000)
    viewed_hint: bool | None = None

    @model_validator(mode="after")
    def submitted_feedback_must_match_answered_state(self) -> Self:
        submitted_fields = (
            self.outcome,
            self.submitted_answer,
            self.explanation,
            self.mastery_reason,
            self.viewed_hint,
        )
        if self.answered and any(value is None for value in submitted_fields):
            raise ValueError("answered questions require submitted feedback")
        if not self.answered and any(value is not None for value in submitted_fields):
            raise ValueError("unanswered questions must not expose submitted feedback")
        if not self.answered and self.grading_feedback is not None:
            raise ValueError("unanswered questions must not expose grading feedback")
        return self


class PracticeSessionSnapshot(ContractModel):
    id: NonEmptyString
    course_id: NonEmptyString
    status: PracticeSessionStatus
    question_count: int = Field(ge=1, le=10)
    questions: list[PracticeQuestionView] = Field(min_length=1, max_length=10)
    started_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def completed_time_must_match_status(self) -> Self:
        terminal = self.status is not PracticeSessionStatus.ACTIVE
        if terminal != (self.completed_at is not None):
            raise ValueError("completed session status must match completed_at")
        return self


class PracticeAttemptRequest(ContractModel):
    question_id: NonEmptyString
    answer: NonEmptyString = Field(max_length=8_000)
    viewed_hint: bool = False
    elapsed_ms: int | None = Field(default=None, ge=0, le=86_400_000)


class PracticeTutorTurn(ContractModel):
    role: Literal["user", "assistant"]
    content: NonEmptyString = Field(max_length=4_000)


class PracticeTutorRequest(ContractModel):
    message: NonEmptyString = Field(max_length=1_000)
    turn_id: NonEmptyString = Field(max_length=64)


class PracticeTutorMessage(ContractModel):
    id: NonEmptyString
    role: Literal["user", "assistant"]
    content: NonEmptyString = Field(max_length=4_000)
    intent: PracticeTutorIntent
    mode: PracticeTutorMode | None = None
    evidence_refs: list[EvidenceReference] = Field(max_length=8)
    created_at: datetime

    @model_validator(mode="after")
    def fields_must_match_role(self) -> Self:
        if self.role == "user" and (self.mode is not None or self.evidence_refs):
            raise ValueError("user tutor messages cannot contain mode or evidence")
        if self.role == "assistant" and (self.mode is None or not self.evidence_refs):
            raise ValueError("assistant tutor messages require mode and evidence")
        return self


class PracticeTutorConversation(ContractModel):
    conversation_id: NonEmptyString | None = None
    session_id: NonEmptyString
    question_id: NonEmptyString
    messages: list[PracticeTutorMessage] = Field(max_length=200)
    has_earlier_messages: bool = False


class PracticeTutorResponse(ContractModel):
    conversation_id: NonEmptyString
    message_id: NonEmptyString
    intent: PracticeTutorIntent
    mode: PracticeTutorMode
    answer_markdown: NonEmptyString = Field(max_length=4_000)
    evidence_refs: list[EvidenceReference] = Field(min_length=1, max_length=8)
    created_at: datetime


class MasteryUpdate(ContractModel):
    learning_unit_id: NonEmptyString
    previous_level: MasteryLevel
    level: MasteryLevel
    reason: NonEmptyString
    next_review_at: datetime

    @field_validator("next_review_at")
    @classmethod
    def review_time_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("next_review_at must be timezone-aware")
        return value


class PracticeAttemptResult(ContractModel):
    id: NonEmptyString
    question_id: NonEmptyString
    outcome: AttemptOutcome
    score: int = Field(ge=0, le=1)
    explanation: NonEmptyString
    grading_feedback: NonEmptyString | None = Field(default=None, max_length=2_000)
    evidence_refs: list[EvidenceReference] = Field(min_length=1, max_length=8)
    mastery: MasteryUpdate


class ReviewQueueItem(ContractModel):
    learning_unit_id: NonEmptyString
    label: NonEmptyString
    kind: LearningUnitKind
    mastery_level: MasteryLevel
    next_review_at: datetime
    source_status: LearningSourceStatus
    weakness_score: int = Field(ge=0, le=3)


class LearningSummary(ContractModel):
    course_id: NonEmptyString
    total_questions: int = Field(ge=0)
    correct_questions: int = Field(ge=0)
    accuracy: float = Field(ge=0, le=1)
    due_review_count: int = Field(ge=0)
    units: list[LearningUnit] = Field(default_factory=list)
    weak_units: list[ReviewQueueItem] = Field(default_factory=list)
    next_action: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def correct_questions_must_not_exceed_total(self) -> Self:
        if self.correct_questions > self.total_questions:
            raise ValueError("correct_questions must not exceed total_questions")
        return self
