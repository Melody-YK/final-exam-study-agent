"""HTTP schema facade for the source-bound learning-loop contracts."""

from study_contracts import (
    EvidenceReference,
    LearningSummary,
    LearningUnit,
    LearningUnitSource,
    PracticeAttemptRequest,
    PracticeAttemptResult,
    PracticeBatchRequest,
    PracticeBatchSnapshot,
    PracticeSessionRequest,
    PracticeSessionSnapshot,
    PracticeTutorRequest,
    PracticeTutorResponse,
    ReviewQueueItem,
)

LearningUnitResponse = LearningUnit
LearningUnitSourceResponse = LearningUnitSource
PracticeBatchResponse = PracticeBatchSnapshot
PracticeSessionResponse = PracticeSessionSnapshot
PracticeAttemptResponse = PracticeAttemptResult
PracticeTutorResponseModel = PracticeTutorResponse
ReviewQueueItemResponse = ReviewQueueItem
LearningSummaryResponse = LearningSummary

__all__ = [
    "EvidenceReference",
    "LearningSummaryResponse",
    "LearningUnitResponse",
    "LearningUnitSourceResponse",
    "PracticeAttemptRequest",
    "PracticeAttemptResponse",
    "PracticeBatchRequest",
    "PracticeBatchResponse",
    "PracticeSessionRequest",
    "PracticeSessionResponse",
    "PracticeTutorRequest",
    "PracticeTutorResponseModel",
    "ReviewQueueItemResponse",
]
