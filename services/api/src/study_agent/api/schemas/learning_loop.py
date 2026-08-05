"""HTTP schema facade for the source-bound learning-loop contracts."""

from study_contracts import (
    EvidenceReference,
    LearningSummary,
    LearningUnit,
    LearningUnitEvidenceItem,
    LearningUnitEvidenceSupplementRequest,
    LearningUnitSource,
    PracticeAttemptRequest,
    PracticeAttemptResult,
    PracticeBatchRequest,
    PracticeBatchSnapshot,
    PracticeSessionRequest,
    PracticeSessionSnapshot,
    PracticeTutorConversation,
    PracticeTutorRequest,
    PracticeTutorResponse,
    ReviewQueueItem,
    VisionEvidenceReview,
)

LearningUnitResponse = LearningUnit
LearningUnitSourceResponse = LearningUnitSource
LearningUnitEvidenceResponse = LearningUnitEvidenceItem
VisionEvidenceReviewResponse = VisionEvidenceReview
PracticeBatchResponse = PracticeBatchSnapshot
PracticeSessionResponse = PracticeSessionSnapshot
PracticeAttemptResponse = PracticeAttemptResult
PracticeTutorConversationResponse = PracticeTutorConversation
PracticeTutorResponseModel = PracticeTutorResponse
ReviewQueueItemResponse = ReviewQueueItem
LearningSummaryResponse = LearningSummary

__all__ = [
    "EvidenceReference",
    "LearningSummaryResponse",
    "LearningUnitEvidenceResponse",
    "LearningUnitEvidenceSupplementRequest",
    "LearningUnitResponse",
    "LearningUnitSourceResponse",
    "PracticeAttemptRequest",
    "PracticeAttemptResponse",
    "PracticeBatchRequest",
    "PracticeBatchResponse",
    "PracticeSessionRequest",
    "PracticeSessionResponse",
    "PracticeTutorConversationResponse",
    "PracticeTutorRequest",
    "PracticeTutorResponseModel",
    "ReviewQueueItemResponse",
    "VisionEvidenceReviewResponse",
]
