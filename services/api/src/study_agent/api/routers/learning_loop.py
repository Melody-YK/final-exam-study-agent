"""Principal-scoped HTTP routes for active recall and review scheduling."""

from typing import Annotated, cast

from fastapi import APIRouter, Header, Request, Response, status

from study_agent.api.errors import ApiProblem, ProblemCode, ProblemDetails
from study_agent.api.schemas.learning_loop import (
    LearningSummaryResponse,
    LearningUnitEvidenceResponse,
    LearningUnitEvidenceSupplementRequest,
    LearningUnitResponse,
    PracticeAttemptRequest,
    PracticeAttemptResponse,
    PracticeBatchRequest,
    PracticeBatchResponse,
    PracticeSessionRequest,
    PracticeSessionResponse,
    PracticeTutorConversationResponse,
    PracticeTutorRequest,
    PracticeTutorResponseModel,
    ReviewQueueItemResponse,
    VisionEvidenceReviewResponse,
)
from study_agent.config import Settings
from study_agent.identity.principal import Principal
from study_agent.identity.session import get_request_principal
from study_agent.infrastructure.db.session import Database
from study_agent.modules.learning.runner import LearningBatchRunner
from study_agent.modules.learning.service import (
    LearningLoopService,
    LearningServiceError,
    LearningServiceErrorCode,
)
from study_agent.modules.learning.vision_review import (
    VisionEvidenceReviewService,
    VisionReviewError,
    VisionReviewErrorCode,
)
from study_agent.providers.errors import ProviderError, ProviderErrorCode
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import ObjectStorage
from study_contracts import (
    LearningSummary,
    LearningUnit,
    LearningUnitEvidenceItem,
    PracticeAttemptResult,
    PracticeTutorConversation,
    PracticeTutorResponse,
    VisionEvidenceReview,
)

router = APIRouter(prefix="/api/v1", tags=["learning-loop"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]


async def _principal(request: Request) -> Principal:
    return await get_request_principal(request)


def _service(request: Request) -> LearningLoopService:
    return cast(LearningLoopService, request.app.state.learning_service)


def _runner(request: Request) -> LearningBatchRunner:
    return cast(LearningBatchRunner, request.app.state.learning_runner)


def _vision_service(request: Request) -> VisionEvidenceReviewService:
    return VisionEvidenceReviewService(
        cast(Database, request.app.state.database),
        cast(ObjectStorage, request.app.state.storage),
        cast(Settings, request.app.state.settings),
        cast(ProviderRegistry, request.app.state.provider_registry),
    )


def _vision_problem(exc: VisionReviewError) -> ApiProblem:
    if exc.code is VisionReviewErrorCode.NOT_FOUND:
        return ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="学习证据不存在",
            detail=exc.detail,
        )
    if exc.code is VisionReviewErrorCode.OUTPUT_INVALID:
        return ApiProblem(
            status=502,
            code=ProblemCode.PROVIDER_BAD_RESPONSE,
            title="多模态复核结果无效",
            detail=exc.detail,
            retryable=True,
        )
    return ApiProblem(
        status=409,
        code=ProblemCode.INDEX_UNAVAILABLE,
        title="证据页面不可用于多模态复核",
        detail=exc.detail,
        retryable=True,
    )


def _vision_provider_problem(exc: ProviderError) -> ApiProblem:
    if exc.code is ProviderErrorCode.NOT_CONFIGURED:
        return ApiProblem(
            status=503,
            code=ProblemCode.PROVIDER_NOT_CONFIGURED,
            title="多模态 Provider 未配置",
        )
    if exc.code is ProviderErrorCode.TIMEOUT:
        return ApiProblem(
            status=504,
            code=ProblemCode.PROVIDER_TIMEOUT,
            title="多模态复核响应超时",
            retryable=True,
        )
    return ApiProblem(
        status=502,
        code=ProblemCode.PROVIDER_BAD_RESPONSE,
        title="多模态复核失败",
        retryable=exc.retryable,
    )


def _problem(exc: LearningServiceError) -> ApiProblem:
    mapping: dict[LearningServiceErrorCode, tuple[int, ProblemCode, str, bool]] = {
        LearningServiceErrorCode.NOT_FOUND: (
            404,
            ProblemCode.RESOURCE_NOT_FOUND,
            "学习资源不存在",
            False,
        ),
        LearningServiceErrorCode.INVALID_REQUEST: (
            422,
            ProblemCode.INVALID_REQUEST,
            "学习请求参数无效",
            False,
        ),
        LearningServiceErrorCode.IDEMPOTENCY_CONFLICT: (
            409,
            ProblemCode.IDEMPOTENCY_CONFLICT,
            "幂等键冲突",
            False,
        ),
        LearningServiceErrorCode.GENERATION_DISABLED: (
            503,
            ProblemCode.NOTE_WORKFLOW_DISABLED,
            "题目生成未启用",
            False,
        ),
        LearningServiceErrorCode.PROVIDER_NOT_CONFIGURED: (
            503,
            ProblemCode.PROVIDER_NOT_CONFIGURED,
            "题目生成 Provider 未配置",
            False,
        ),
        LearningServiceErrorCode.PROVIDER_TIMEOUT: (
            504,
            ProblemCode.PROVIDER_TIMEOUT,
            "AI 学习助手响应超时",
            True,
        ),
        LearningServiceErrorCode.PROVIDER_BAD_RESPONSE: (
            502,
            ProblemCode.PROVIDER_BAD_RESPONSE,
            "AI 学习助手回答无效",
            True,
        ),
        LearningServiceErrorCode.INDEX_UNAVAILABLE: (
            409,
            ProblemCode.INDEX_UNAVAILABLE,
            "课程来源索引不可用",
            True,
        ),
        LearningServiceErrorCode.SOURCE_UNAVAILABLE: (
            409,
            ProblemCode.INDEX_UNAVAILABLE,
            "课程来源不可用",
            True,
        ),
        LearningServiceErrorCode.INSUFFICIENT_EVIDENCE: (
            409,
            ProblemCode.INDEX_UNAVAILABLE,
            "课程正文不足以生成稳定题目",
            True,
        ),
        LearningServiceErrorCode.BATCH_LIMIT: (
            422,
            ProblemCode.NOTE_REQUEST_LIMIT_EXCEEDED,
            "练习批次超过上限",
            False,
        ),
        LearningServiceErrorCode.STALE_QUESTION: (
            409,
            ProblemCode.STATE_CONFLICT,
            "题目来源已失效",
            False,
        ),
        LearningServiceErrorCode.SESSION_COMPLETE: (
            409,
            ProblemCode.STATE_CONFLICT,
            "练习会话已完成",
            False,
        ),
        LearningServiceErrorCode.ATTEMPT_CONFLICT: (
            409,
            ProblemCode.STATE_CONFLICT,
            "作答状态冲突",
            False,
        ),
    }
    status_code, code, title, retryable = mapping[exc.code]
    return ApiProblem(
        status=status_code,
        code=code,
        title=title,
        detail=exc.detail,
        retryable=retryable,
    )


@router.get(
    "/courses/{course_id}/learning-units",
    response_model=list[LearningUnitResponse],
)
async def list_learning_units(
    course_id: str,
    request: Request,
) -> list[LearningUnit]:
    try:
        return await _service(request).list_learning_units(await _principal(request), course_id)
    except LearningServiceError as exc:
        raise _problem(exc) from exc


@router.get(
    "/practice-sessions/{session_id}/questions/{question_id}/tutor",
    response_model=PracticeTutorConversationResponse,
    responses={
        409: {"model": ProblemDetails, "description": "题目来源状态冲突"},
    },
)
async def get_practice_tutor_conversation(
    session_id: str,
    question_id: str,
    request: Request,
) -> PracticeTutorConversation:
    try:
        return await _service(request).get_tutor_conversation(
            await _principal(request),
            session_id,
            question_id,
        )
    except LearningServiceError as exc:
        raise _problem(exc) from exc


@router.post(
    "/courses/{course_id}/learning-units/regenerate",
    response_model=list[LearningUnitResponse],
)
async def regenerate_learning_units(
    course_id: str,
    request: Request,
) -> list[LearningUnit]:
    try:
        return await _service(request).regenerate_learning_units(
            await _principal(request), course_id
        )
    except LearningServiceError as exc:
        raise _problem(exc) from exc


@router.get(
    "/courses/{course_id}/learning-units/{unit_id}/evidence",
    response_model=list[LearningUnitEvidenceResponse],
)
async def list_learning_unit_evidence(
    course_id: str,
    unit_id: str,
    request: Request,
) -> list[LearningUnitEvidenceItem]:
    try:
        return await _service(request).list_learning_unit_evidence(
            await _principal(request), course_id, unit_id
        )
    except LearningServiceError as exc:
        raise _problem(exc) from exc


@router.post(
    "/courses/{course_id}/learning-units/{unit_id}/evidence/{source_id}/vision-review",
    response_model=VisionEvidenceReviewResponse,
    responses={
        409: {"model": ProblemDetails, "description": "证据页面不可用"},
        503: {"model": ProblemDetails, "description": "多模态 Provider 未配置"},
    },
)
async def review_learning_unit_evidence_with_vision(
    course_id: str,
    unit_id: str,
    source_id: str,
    request: Request,
    idempotency_key: IdempotencyKey,
) -> VisionEvidenceReview:
    try:
        return await _vision_service(request).review_source(
            await _principal(request),
            course_id,
            unit_id,
            source_id,
            idempotency_key,
        )
    except VisionReviewError as exc:
        raise _vision_problem(exc) from exc
    except ProviderError as exc:
        raise _vision_provider_problem(exc) from exc
    except TimeoutError as exc:
        raise ApiProblem(
            status=504,
            code=ProblemCode.PROVIDER_TIMEOUT,
            title="多模态复核响应超时",
            retryable=True,
        ) from exc


@router.post(
    "/courses/{course_id}/learning-units/{unit_id}/evidence-supplements",
    response_model=LearningUnitEvidenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_learning_unit_evidence_supplement(
    course_id: str,
    unit_id: str,
    payload: LearningUnitEvidenceSupplementRequest,
    request: Request,
) -> LearningUnitEvidenceItem:
    try:
        return await _service(request).create_learning_unit_evidence_supplement(
            await _principal(request), course_id, unit_id, payload
        )
    except LearningServiceError as exc:
        raise _problem(exc) from exc


@router.delete(
    "/courses/{course_id}/learning-units/{unit_id}/evidence-supplements/{supplement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_learning_unit_evidence_supplement(
    course_id: str,
    unit_id: str,
    supplement_id: str,
    request: Request,
) -> Response:
    try:
        await _service(request).revoke_learning_unit_evidence_supplement(
            await _principal(request), course_id, unit_id, supplement_id
        )
    except LearningServiceError as exc:
        raise _problem(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/courses/{course_id}/practice-batches",
    response_model=PracticeBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        409: {"model": ProblemDetails, "description": "请求或来源状态冲突"},
        503: {"model": ProblemDetails, "description": "题目生成不可用"},
    },
)
async def create_practice_batch(
    course_id: str,
    payload: PracticeBatchRequest,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
) -> PracticeBatchResponse:
    try:
        snapshot = await _service(request).create_batch(
            await _principal(request), course_id, payload, idempotency_key
        )
    except LearningServiceError as exc:
        raise _problem(exc) from exc
    response.headers["Location"] = f"/api/v1/practice-batches/{snapshot.id}"
    _runner(request).schedule(snapshot.id)
    return snapshot


@router.get("/practice-batches/{batch_id}", response_model=PracticeBatchResponse)
async def get_practice_batch(
    batch_id: str,
    request: Request,
) -> PracticeBatchResponse:
    try:
        return await _service(request).get_batch(await _principal(request), batch_id)
    except LearningServiceError as exc:
        raise _problem(exc) from exc


@router.post(
    "/courses/{course_id}/practice-sessions",
    response_model=PracticeSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_practice_session(
    course_id: str,
    payload: PracticeSessionRequest,
    request: Request,
) -> PracticeSessionResponse:
    try:
        return await _service(request).create_session(await _principal(request), course_id, payload)
    except LearningServiceError as exc:
        raise _problem(exc) from exc


@router.get("/practice-sessions/{session_id}", response_model=PracticeSessionResponse)
async def get_practice_session(
    session_id: str,
    request: Request,
) -> PracticeSessionResponse:
    try:
        return await _service(request).get_session(await _principal(request), session_id)
    except LearningServiceError as exc:
        raise _problem(exc) from exc


@router.post(
    "/practice-sessions/{session_id}/questions/{question_id}/tutor",
    response_model=PracticeTutorResponseModel,
    responses={
        409: {"model": ProblemDetails, "description": "题目来源状态冲突"},
        502: {"model": ProblemDetails, "description": "模型回答未通过约束"},
        503: {"model": ProblemDetails, "description": "模型 Provider 不可用"},
        504: {"model": ProblemDetails, "description": "模型响应超时"},
    },
)
async def ask_practice_tutor(
    session_id: str,
    question_id: str,
    payload: PracticeTutorRequest,
    request: Request,
) -> PracticeTutorResponse:
    try:
        return await _service(request).ask_tutor(
            await _principal(request),
            session_id,
            question_id,
            payload,
        )
    except LearningServiceError as exc:
        raise _problem(exc) from exc


@router.post(
    "/practice-sessions/{session_id}/attempts",
    response_model=PracticeAttemptResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": ProblemDetails, "description": "题目或会话状态冲突"},
    },
)
async def submit_practice_attempt(
    session_id: str,
    payload: PracticeAttemptRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
) -> PracticeAttemptResult:
    try:
        return await _service(request).submit_attempt(
            await _principal(request), session_id, payload, idempotency_key
        )
    except LearningServiceError as exc:
        raise _problem(exc) from exc


@router.get(
    "/courses/{course_id}/review-queue",
    response_model=list[ReviewQueueItemResponse],
)
async def get_review_queue(
    course_id: str,
    request: Request,
) -> list[ReviewQueueItemResponse]:
    try:
        return await _service(request).review_queue(await _principal(request), course_id)
    except LearningServiceError as exc:
        raise _problem(exc) from exc


@router.get(
    "/courses/{course_id}/learning-summary",
    response_model=LearningSummaryResponse,
)
async def get_learning_summary(
    course_id: str,
    request: Request,
) -> LearningSummary:
    try:
        return await _service(request).summary(await _principal(request), course_id)
    except LearningServiceError as exc:
        raise _problem(exc) from exc


__all__ = ["router"]
