"""Principal- and course-scoped learning-loop application service."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.config import Settings
from study_agent.identity.principal import AuthenticationMethod, Principal
from study_agent.infrastructure.db.models import (
    CourseModel,
    DocumentModel,
    LearningMasteryModel,
    LearningUnitModel,
    LearningUnitSourceModel,
    PracticeAttemptModel,
    PracticeBatchAttemptModel,
    PracticeBatchEventModel,
    PracticeBatchItemModel,
    PracticeBatchModel,
    PracticeQuestionEvidenceModel,
    PracticeQuestionModel,
    PracticeSessionModel,
    PracticeSessionQuestionModel,
    RevisionChunkModel,
    UserModel,
)
from study_agent.infrastructure.db.models.core import new_id
from study_agent.infrastructure.db.session import Database
from study_agent.modules.learning.concepts import (
    ChunkCandidate,
    build_learning_unit_candidates,
    clean_learning_unit_label,
    document_topic_from_filename,
    is_zero_placeholder_label,
    practice_evidence_stats,
)
from study_agent.modules.learning.mastery import MasteryState, update_mastery
from study_agent.modules.learning.questions import (
    AuthorizedEvidence,
    QuestionGenerator,
    QuestionValidationError,
    balanced_random_answer_position,
    position_correct_option,
    select_question_evidence,
)
from study_agent.modules.learning.runner import LearningBatchProcessor, RetryableLearningBatchError
from study_agent.modules.learning.scheduling import next_review_at
from study_agent.modules.learning.scoring import InvalidAnswerError, score_answer
from study_agent.modules.learning.tutor import (
    PracticeTutor,
    TutorEvidence,
    TutorGenerationError,
)
from study_agent.providers.errors import ProviderError, ProviderErrorCode
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import Clock
from study_contracts import (
    AttemptOutcome,
    EvidenceReference,
    LearningSourceStatus,
    LearningSummary,
    LearningUnit,
    LearningUnitKind,
    LearningUnitPracticeStatus,
    LearningUnitSource,
    LearningUnitStatus,
    MasteryLevel,
    MasteryUpdate,
    PracticeAttemptRequest,
    PracticeAttemptResult,
    PracticeBatchItem,
    PracticeBatchPhase,
    PracticeBatchRequest,
    PracticeBatchSnapshot,
    PracticeBatchStatus,
    PracticeQuestionView,
    PracticeSessionRequest,
    PracticeSessionSnapshot,
    PracticeSessionStatus,
    PracticeTutorMode,
    PracticeTutorRequest,
    PracticeTutorResponse,
    QuestionOption,
    QuestionStatus,
    QuestionType,
    ReviewQueueItem,
    canonical_sha256,
)
from study_contracts.documents import SourceLocator


class LearningServiceErrorCode(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    INVALID_REQUEST = "INVALID_REQUEST"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    GENERATION_DISABLED = "GENERATION_DISABLED"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_BAD_RESPONSE = "PROVIDER_BAD_RESPONSE"
    INDEX_UNAVAILABLE = "INDEX_UNAVAILABLE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    BATCH_LIMIT = "BATCH_LIMIT"
    STALE_QUESTION = "STALE_QUESTION"
    SESSION_COMPLETE = "SESSION_COMPLETE"
    ATTEMPT_CONFLICT = "ATTEMPT_CONFLICT"


class LearningServiceError(RuntimeError):
    def __init__(self, code: LearningServiceErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _now(clock: Clock) -> datetime:
    value = clock.now()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _locator(value: object) -> SourceLocator:
    return SourceLocator.model_validate(value)


def _unit_source_from_row(row: LearningUnitSourceModel) -> LearningUnitSource:
    return LearningUnitSource(
        document_id=row.document_id,
        revision_id=row.revision_id,
        chunk_id=row.chunk_id,
        content_sha256=row.content_sha256,
        locator=_locator(row.locator),
        status=LearningSourceStatus(row.status),
    )


def _is_legacy_zero_placeholder(unit: LearningUnitModel) -> bool:
    key_label = unit.canonical_key.rsplit("/", 1)[-1]
    if ":" in key_label:
        key_label = key_label.rsplit(":", 1)[-1]
    return is_zero_placeholder_label(unit.label) or is_zero_placeholder_label(key_label)


class LearningLoopService(LearningBatchProcessor):
    def __init__(
        self,
        database: Database,
        settings: Settings,
        clock: Clock,
        provider_registry: ProviderRegistry,
    ) -> None:
        self._database = database
        self._settings = settings
        self._clock = clock
        self._provider_registry = provider_registry
        self._generator = QuestionGenerator(
            provider_registry,
            timeout_seconds=settings.provider_timeout_seconds,
        )
        self._tutor = PracticeTutor(
            provider_registry,
            timeout_seconds=settings.provider_timeout_seconds,
        )

    def generation_available(self) -> tuple[bool, str | None]:
        if (
            not self._settings.practice_generation_enabled
            or not self._settings.practice_runner_enabled
        ):
            return False, "PRACTICE_GENERATION_DISABLED"
        try:
            self._provider_registry.chat()
        except ProviderError as exc:
            if exc.code is ProviderErrorCode.NOT_CONFIGURED:
                return False, "PROVIDER_NOT_CONFIGURED"
            return False, exc.code.value
        return True, None

    async def list_learning_units(self, principal: Principal, course_id: str) -> list[LearningUnit]:
        async with self._database.session(principal) as session:
            course = await self._course_for_principal(session, principal, course_id)
            if course is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "课程不存在。")
            await self._sync_units(session, course)
            return await self._unit_snapshots(session, principal, course_id)

    async def regenerate_learning_units(
        self, principal: Principal, course_id: str
    ) -> list[LearningUnit]:
        """Explicitly rebuild the current projection without deleting history."""

        async with self._database.session(principal) as session:
            course = await self._course_for_principal(session, principal, course_id)
            if course is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "课程不存在。")
            await self._sync_units(session, course)
            return await self._unit_snapshots(session, principal, course_id)

    async def create_batch(
        self,
        principal: Principal,
        course_id: str,
        request: PracticeBatchRequest,
        idempotency_key: str,
    ) -> PracticeBatchSnapshot:
        key = idempotency_key.strip()
        if not key:
            raise LearningServiceError(
                LearningServiceErrorCode.INVALID_REQUEST, "Idempotency-Key 不能为空。"
            )
        request_hash = canonical_sha256({"course_id": course_id, **request.model_dump(mode="json")})
        key_hash = _hash_key(key)
        async with self._database.session(principal) as session:
            course = await self._course_for_principal(session, principal, course_id)
            if course is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "课程不存在。")
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
                {"lock_name": f"practice:{course.user_id}:{course.id}:{key_hash}"},
            )
            existing = await session.scalar(
                select(PracticeBatchModel).where(
                    PracticeBatchModel.user_id == course.user_id,
                    PracticeBatchModel.course_id == course.id,
                    PracticeBatchModel.idempotency_key_hash == key_hash,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise LearningServiceError(
                        LearningServiceErrorCode.IDEMPOTENCY_CONFLICT,
                        "Idempotency-Key 已绑定到另一份请求。",
                    )
                return await self._batch_snapshot(session, existing)

            if request.question_count > self._settings.practice_batch_max_questions:
                raise LearningServiceError(
                    LearningServiceErrorCode.BATCH_LIMIT, "题目数量超过当前上限。"
                )
            if request.question_count < len(request.learning_unit_ids):
                raise LearningServiceError(
                    LearningServiceErrorCode.INVALID_REQUEST,
                    "题目数量不能少于所选范围数量。",
                )
            available, failure = self.generation_available()
            if not available:
                code = (
                    LearningServiceErrorCode.PROVIDER_NOT_CONFIGURED
                    if failure == "PROVIDER_NOT_CONFIGURED"
                    else LearningServiceErrorCode.GENERATION_DISABLED
                )
                raise LearningServiceError(code, "题目生成当前不可用; 已有有效题目仍可练习。")
            if course.active_lexical_index_id is None:
                raise LearningServiceError(
                    LearningServiceErrorCode.INDEX_UNAVAILABLE,
                    "当前课程还没有可用的活动索引。",
                )

            await self._sync_units(session, course)
            units = list(
                await session.scalars(
                    select(LearningUnitModel).where(
                        LearningUnitModel.user_id == course.user_id,
                        LearningUnitModel.course_id == course.id,
                        LearningUnitModel.id.in_(request.learning_unit_ids),
                    )
                )
            )
            if len(units) != len(request.learning_unit_ids):
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "学习单元不存在。")
            unit_by_id = {unit.id: unit for unit in units}
            selected_ids = set(request.learning_unit_ids)
            for unit_id in request.learning_unit_ids:
                unit = unit_by_id[unit_id]
                if unit.parent_id in selected_ids:
                    raise LearningServiceError(
                        LearningServiceErrorCode.INVALID_REQUEST,
                        "章节范围不能和其中的知识目标同时选择。",
                    )
                if unit.kind == LearningUnitKind.SECTION.value and unit.parent_id is not None:
                    raise LearningServiceError(
                        LearningServiceErrorCode.SOURCE_UNAVAILABLE,
                        "请选择主题或概念，不要直接选择子章节。",  # noqa: RUF001
                    )
                if unit.status != LearningUnitStatus.AVAILABLE.value:
                    raise LearningServiceError(
                        LearningServiceErrorCode.SOURCE_UNAVAILABLE,
                        "所选学习单元的来源已失效。",
                    )
                evidence = await self._current_evidence(session, course.user_id, course.id, unit_id)
                if not evidence[0]:
                    raise LearningServiceError(
                        LearningServiceErrorCode.SOURCE_UNAVAILABLE,
                        "所选学习单元没有当前有效来源。",
                    )
                stats = practice_evidence_stats(chunk.text for chunk in evidence[1])
                if not stats.is_sufficient:
                    raise LearningServiceError(
                        LearningServiceErrorCode.INSUFFICIENT_EVIDENCE,
                        "所选主题的有效正文不足，暂时无法稳定生成题目。",  # noqa: RUF001
                    )
            batch = PracticeBatchModel(
                id=new_id(),
                user_id=course.user_id,
                course_id=course.id,
                learning_unit_ids=list(request.learning_unit_ids),
                target_question_count=request.question_count,
                total_items=request.question_count,
                status="queued",
                phase="validating_inputs",
                idempotency_key_hash=key_hash,
                request_hash=request_hash,
                state_version=1,
                attempt_count=0,
            )
            session.add(batch)
            # The composite batch-item foreign key has no ORM relationship to
            # establish insert ordering, so persist the parent row first.
            await session.flush()
            for ordinal in range(1, request.question_count + 1):
                unit_id = request.learning_unit_ids[(ordinal - 1) % len(request.learning_unit_ids)]
                if unit_id not in unit_by_id:
                    raise LearningServiceError(
                        LearningServiceErrorCode.NOT_FOUND, "学习单元不存在。"
                    )
                session.add(
                    PracticeBatchItemModel(
                        id=new_id(),
                        user_id=course.user_id,
                        course_id=course.id,
                        batch_id=batch.id,
                        ordinal=ordinal,
                        status="queued",
                        attempt_count=0,
                    )
                )
            await session.flush()
            return await self._batch_snapshot(session, batch)

    async def get_batch(self, principal: Principal, batch_id: str) -> PracticeBatchSnapshot:
        async with self._database.session(principal) as session:
            batch = await session.scalar(
                select(PracticeBatchModel)
                .join(UserModel, UserModel.id == PracticeBatchModel.user_id)
                .where(
                    PracticeBatchModel.id == batch_id,
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            )
            if batch is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "练习批次不存在。")
            return await self._batch_snapshot(session, batch)

    async def process_batch(self, batch_id: str, runner_id: str) -> None:
        async with self._database.worker_session(runner_id) as session:
            row = await session.execute(
                select(PracticeBatchModel, UserModel)
                .join(UserModel, UserModel.id == PracticeBatchModel.user_id)
                .where(PracticeBatchModel.id == batch_id, PracticeBatchModel.runner_id == runner_id)
            )
            result = row.first()
            if result is None:
                return
            batch, user = result
            principal = Principal(
                subject=user.subject,
                authentication_method=AuthenticationMethod(user.authentication_method),
            )
        while True:
            async with self._database.session(principal) as session:
                item = await session.scalar(
                    select(PracticeBatchItemModel)
                    .where(
                        PracticeBatchItemModel.batch_id == batch_id,
                        PracticeBatchItemModel.user_id == batch.user_id,
                        PracticeBatchItemModel.course_id == batch.course_id,
                        PracticeBatchItemModel.status == "queued",
                    )
                    .order_by(PracticeBatchItemModel.ordinal)
                    .with_for_update()
                )
                if item is None:
                    break
                await self._process_item(session, batch, item)
        async with self._database.worker_session(runner_id) as session:
            current = await session.scalar(
                select(PracticeBatchModel)
                .where(PracticeBatchModel.id == batch_id, PracticeBatchModel.runner_id == runner_id)
                .with_for_update()
            )
            if current is None:
                return
            items = list(
                await session.scalars(
                    select(PracticeBatchItemModel)
                    .where(
                        PracticeBatchItemModel.batch_id == batch_id,
                        PracticeBatchItemModel.user_id == current.user_id,
                        PracticeBatchItemModel.course_id == current.course_id,
                    )
                    .order_by(PracticeBatchItemModel.ordinal)
                )
            )
            success_count = sum(item.status == "succeeded" for item in items)
            current.completed_items = sum(item.status in {"succeeded", "failed"} for item in items)
            current.status = (
                "succeeded"
                if success_count == len(items)
                else ("partial_success" if success_count else "failed")
            )
            failure_codes = sorted(
                {
                    item.failure_code
                    for item in items
                    if item.status == "failed" and item.failure_code
                }
            )
            current.failure_code = ",".join(failure_codes)[:128] or None
            current.phase = "saving"
            current.completed_at = _now(self._clock)
            current.runner_id = None
            current.lease_expires_at = None
            current.state_version += 1

    async def create_session(
        self,
        principal: Principal,
        course_id: str,
        request: PracticeSessionRequest,
    ) -> PracticeSessionSnapshot:
        async with self._database.session(principal) as session:
            course = await self._course_for_principal(session, principal, course_id)
            if course is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "课程不存在。")
            questions = list(
                await session.scalars(
                    select(PracticeQuestionModel).where(
                        PracticeQuestionModel.user_id == course.user_id,
                        PracticeQuestionModel.course_id == course.id,
                        PracticeQuestionModel.id.in_(request.question_ids),
                        PracticeQuestionModel.status == "ready",
                    )
                )
            )
            if len(questions) != len(request.question_ids):
                raise LearningServiceError(
                    LearningServiceErrorCode.STALE_QUESTION, "题目不存在或已失效。"
                )
            for question in questions:
                if not await self._question_evidence(session, question):
                    raise LearningServiceError(
                        LearningServiceErrorCode.STALE_QUESTION, "题目来源已失效。"
                    )
            now = _now(self._clock)
            session_model = PracticeSessionModel(
                id=new_id(),
                user_id=course.user_id,
                course_id=course.id,
                learning_unit_ids=sorted({question.learning_unit_id for question in questions}),
                question_count=len(questions),
                mode="practice",
                status="active",
                started_at=now,
            )
            session.add(session_model)
            question_by_id = {question.id: question for question in questions}
            for ordinal, question_id in enumerate(request.question_ids, start=1):
                if question_id not in question_by_id:
                    raise LearningServiceError(
                        LearningServiceErrorCode.STALE_QUESTION, "题目不存在。"
                    )
                session.add(
                    PracticeSessionQuestionModel(
                        id=new_id(),
                        user_id=course.user_id,
                        course_id=course.id,
                        session_id=session_model.id,
                        question_id=question_id,
                        ordinal=ordinal,
                    )
                )
            await session.flush()
            return await self._session_snapshot(session, session_model)

    async def get_session(self, principal: Principal, session_id: str) -> PracticeSessionSnapshot:
        async with self._database.session(principal) as session:
            model = await self._session_for_principal(session, principal, session_id)
            if model is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "练习会话不存在。")
            return await self._session_snapshot(session, model)

    async def ask_tutor(
        self,
        principal: Principal,
        session_id: str,
        question_id: str,
        request: PracticeTutorRequest,
    ) -> PracticeTutorResponse:
        async with self._database.session(principal) as session:
            session_model = await self._session_for_principal(session, principal, session_id)
            if session_model is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "练习会话不存在。")
            question = await session.scalar(
                select(PracticeQuestionModel)
                .join(
                    PracticeSessionQuestionModel,
                    and_(
                        PracticeSessionQuestionModel.question_id == PracticeQuestionModel.id,
                        PracticeSessionQuestionModel.course_id == PracticeQuestionModel.course_id,
                        PracticeSessionQuestionModel.user_id == PracticeQuestionModel.user_id,
                    ),
                )
                .where(
                    PracticeSessionQuestionModel.session_id == session_model.id,
                    PracticeSessionQuestionModel.course_id == session_model.course_id,
                    PracticeSessionQuestionModel.user_id == session_model.user_id,
                    PracticeQuestionModel.id == question_id,
                    PracticeQuestionModel.status == "ready",
                )
            )
            if question is None:
                raise LearningServiceError(
                    LearningServiceErrorCode.STALE_QUESTION,
                    "题目不属于当前会话或来源已失效。",
                )
            evidence_refs, evidence_material = await self._question_evidence_material(
                session, question
            )
            if not evidence_refs:
                raise LearningServiceError(
                    LearningServiceErrorCode.STALE_QUESTION,
                    "题目来源已失效。",
                )
            attempt = await session.scalar(
                select(PracticeAttemptModel).where(
                    PracticeAttemptModel.session_id == session_model.id,
                    PracticeAttemptModel.question_id == question.id,
                    PracticeAttemptModel.course_id == session_model.course_id,
                    PracticeAttemptModel.user_id == session_model.user_id,
                )
            )
            mode = PracticeTutorMode.REVIEW if attempt is not None else PracticeTutorMode.HINT
            options = [QuestionOption.model_validate(option) for option in question.options]
            tutor_evidence = tuple(
                TutorEvidence(reference=reference, text=chunk.text)
                for reference, chunk in zip(
                    evidence_refs,
                    evidence_material,
                    strict=True,
                )
            )
            question_type = QuestionType(question.question_type)
            prompt = question.prompt
            correct_answer = question.correct_answer
            explanation = question.explanation
            submitted_answer = None if attempt is None else attempt.answer

        try:
            return await self._tutor.answer(
                mode=mode,
                question_type=question_type,
                prompt=prompt,
                options=options,
                correct_answer=correct_answer,
                explanation=explanation,
                submitted_answer=submitted_answer,
                message=request.message,
                history=request.history,
                evidence=tutor_evidence,
            )
        except ProviderError as exc:
            if exc.code is ProviderErrorCode.NOT_CONFIGURED:
                code = LearningServiceErrorCode.PROVIDER_NOT_CONFIGURED
                detail = "AI 学习助手 Provider 未配置。"
            elif exc.code is ProviderErrorCode.TIMEOUT:
                code = LearningServiceErrorCode.PROVIDER_TIMEOUT
                detail = "AI 学习助手响应超时, 请稍后重试。"
            else:
                code = LearningServiceErrorCode.PROVIDER_BAD_RESPONSE
                detail = "AI 学习助手暂时无法生成可靠回答。"
            raise LearningServiceError(code, detail) from exc
        except TutorGenerationError as exc:
            code = (
                LearningServiceErrorCode.PROVIDER_TIMEOUT
                if exc.code is ProviderErrorCode.TIMEOUT
                else LearningServiceErrorCode.PROVIDER_BAD_RESPONSE
            )
            detail = (
                "AI 学习助手响应超时, 请稍后重试。"
                if code is LearningServiceErrorCode.PROVIDER_TIMEOUT
                else "AI 学习助手未能生成符合要求的回答。"
            )
            raise LearningServiceError(code, detail) from exc

    async def submit_attempt(
        self,
        principal: Principal,
        session_id: str,
        request: PracticeAttemptRequest,
        idempotency_key: str,
    ) -> PracticeAttemptResult:
        return await self.submit_attempt_for_question(
            principal, session_id, request.question_id, request, idempotency_key
        )

    async def submit_attempt_for_question(
        self,
        principal: Principal,
        session_id: str,
        question_id: str,
        request: PracticeAttemptRequest,
        idempotency_key: str,
    ) -> PracticeAttemptResult:
        key = idempotency_key.strip()
        if not key:
            raise LearningServiceError(
                LearningServiceErrorCode.INVALID_REQUEST, "Idempotency-Key 不能为空。"
            )
        key_hash = _hash_key(key)
        async with self._database.session(principal) as session:
            session_model = await self._session_for_principal(
                session, principal, session_id, lock=True
            )
            if session_model is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "练习会话不存在。")
            existing_by_key = await session.scalar(
                select(PracticeAttemptModel).where(
                    PracticeAttemptModel.session_id == session_id,
                    PracticeAttemptModel.course_id == session_model.course_id,
                    PracticeAttemptModel.user_id == session_model.user_id,
                    PracticeAttemptModel.idempotency_key_hash == key_hash,
                )
            )
            if existing_by_key is not None:
                existing_question = await session.scalar(
                    select(PracticeQuestionModel).where(
                        PracticeQuestionModel.id == existing_by_key.question_id,
                        PracticeQuestionModel.user_id == session_model.user_id,
                        PracticeQuestionModel.course_id == session_model.course_id,
                    )
                )
                if existing_question is None:
                    raise LearningServiceError(
                        LearningServiceErrorCode.NOT_FOUND, "作答结果不存在。"
                    )
                if (
                    existing_question.id != request.question_id
                    or existing_by_key.viewed_hint != request.viewed_hint
                    or existing_by_key.elapsed_ms != request.elapsed_ms
                ):
                    raise LearningServiceError(
                        LearningServiceErrorCode.ATTEMPT_CONFLICT,
                        "幂等键已绑定到另一份作答请求。",
                    )
                try:
                    normalized_answer = score_answer(
                        existing_question.question_type,
                        existing_question.correct_answer,
                        request.answer,
                    ).submitted_answer
                except (InvalidAnswerError, ValueError) as exc:
                    raise LearningServiceError(
                        LearningServiceErrorCode.INVALID_REQUEST, "答案格式无效。"
                    ) from exc
                if normalized_answer != existing_by_key.answer:
                    raise LearningServiceError(
                        LearningServiceErrorCode.ATTEMPT_CONFLICT,
                        "幂等键已绑定到另一份作答请求。",
                    )
                return await self._attempt_result(session, existing_by_key)
            if session_model.status != "active":
                raise LearningServiceError(
                    LearningServiceErrorCode.SESSION_COMPLETE, "练习会话已完成。"
                )
            question = await session.scalar(
                select(PracticeQuestionModel)
                .join(
                    PracticeSessionQuestionModel,
                    and_(
                        PracticeSessionQuestionModel.question_id == PracticeQuestionModel.id,
                        PracticeSessionQuestionModel.course_id == PracticeQuestionModel.course_id,
                        PracticeSessionQuestionModel.user_id == PracticeQuestionModel.user_id,
                    ),
                )
                .where(
                    PracticeSessionQuestionModel.session_id == session_id,
                    PracticeSessionQuestionModel.question_id == question_id,
                    PracticeQuestionModel.status == "ready",
                )
            )
            if question is None:
                raise LearningServiceError(
                    LearningServiceErrorCode.STALE_QUESTION, "题目不属于当前会话或已失效。"
                )
            old_attempt = await session.scalar(
                select(PracticeAttemptModel).where(
                    PracticeAttemptModel.session_id == session_id,
                    PracticeAttemptModel.question_id == question_id,
                    PracticeAttemptModel.user_id == session_model.user_id,
                    PracticeAttemptModel.course_id == session_model.course_id,
                )
            )
            if old_attempt is not None:
                raise LearningServiceError(
                    LearningServiceErrorCode.ATTEMPT_CONFLICT,
                    "该题目已经提交过作答。",
                )
            evidence = await self._question_evidence(session, question)
            if not evidence:
                raise LearningServiceError(
                    LearningServiceErrorCode.STALE_QUESTION, "题目来源已失效。"
                )
            try:
                scored = score_answer(
                    question.question_type, question.correct_answer, request.answer
                )
            except (InvalidAnswerError, ValueError) as exc:
                raise LearningServiceError(
                    LearningServiceErrorCode.INVALID_REQUEST, "答案格式无效。"
                ) from exc
            mastery = await session.scalar(
                select(LearningMasteryModel)
                .where(
                    LearningMasteryModel.learning_unit_id == question.learning_unit_id,
                    LearningMasteryModel.course_id == session_model.course_id,
                    LearningMasteryModel.user_id == session_model.user_id,
                )
                .with_for_update()
            )
            old_state = MasteryState(
                level=MasteryLevel.NEW if mastery is None else MasteryLevel(mastery.mastery_level),
                attempt_count=0 if mastery is None else mastery.attempt_count,
                correct_count=0 if mastery is None else mastery.correct_count,
                last_score=0 if mastery is None else mastery.last_score,
            )
            mastery_result = update_mastery(
                old_state, correct=scored.correct, viewed_hint=request.viewed_hint
            )
            now = _now(self._clock)
            review_at = next_review_at(mastery_result.level, correct=scored.correct, now=now)
            if mastery is None:
                mastery = LearningMasteryModel(
                    id=new_id(),
                    user_id=session_model.user_id,
                    course_id=session_model.course_id,
                    learning_unit_id=question.learning_unit_id,
                )
                session.add(mastery)
            mastery.attempt_count = mastery_result.attempt_count
            mastery.correct_count = mastery_result.correct_count
            mastery.last_score = mastery_result.last_score
            mastery.mastery_level = mastery_result.level.value
            mastery.next_review_at = review_at
            mastery.last_attempt_at = now
            attempt = PracticeAttemptModel(
                id=new_id(),
                user_id=session_model.user_id,
                course_id=session_model.course_id,
                session_id=session_id,
                question_id=question_id,
                idempotency_key_hash=key_hash,
                answer=scored.submitted_answer,
                score=scored.score,
                correct=scored.correct,
                viewed_hint=request.viewed_hint,
                elapsed_ms=request.elapsed_ms,
                previous_mastery_level=mastery_result.previous_level.value,
                mastery_level=mastery_result.level.value,
                next_review_at=review_at,
                feedback=mastery_result.reason,
                evidence_refs=[ref.model_dump(mode="json") for ref in evidence],
                answered_at=now,
            )
            session.add(attempt)
            answered = await session.scalar(
                select(func.count(PracticeAttemptModel.id)).where(
                    PracticeAttemptModel.session_id == session_id,
                    PracticeAttemptModel.user_id == session_model.user_id,
                    PracticeAttemptModel.course_id == session_model.course_id,
                )
            )
            if answered == session_model.question_count:
                session_model.status = "completed"
                session_model.completed_at = now
            await session.flush()
            return PracticeAttemptResult(
                id=attempt.id,
                question_id=question_id,
                outcome=AttemptOutcome.CORRECT if scored.correct else AttemptOutcome.INCORRECT,
                score=scored.score,
                explanation=question.explanation,
                evidence_refs=evidence,
                mastery=MasteryUpdate(
                    learning_unit_id=question.learning_unit_id,
                    previous_level=mastery_result.previous_level,
                    level=mastery_result.level,
                    reason=mastery_result.reason,
                    next_review_at=review_at,
                ),
            )

    async def review_queue(self, principal: Principal, course_id: str) -> list[ReviewQueueItem]:
        async with self._database.session(principal) as session:
            course = await self._course_for_principal(session, principal, course_id)
            if course is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "课程不存在。")
            now = _now(self._clock)
            rows = list(
                await session.execute(
                    select(LearningMasteryModel, LearningUnitModel)
                    .join(
                        LearningUnitModel,
                        and_(
                            LearningUnitModel.id == LearningMasteryModel.learning_unit_id,
                            LearningUnitModel.course_id == LearningMasteryModel.course_id,
                            LearningUnitModel.user_id == LearningMasteryModel.user_id,
                        ),
                    )
                    .where(
                        LearningMasteryModel.user_id == course.user_id,
                        LearningMasteryModel.course_id == course.id,
                        LearningMasteryModel.next_review_at <= now,
                        LearningUnitModel.status == "available",
                        (LearningUnitModel.kind == "concept")
                        | (
                            (LearningUnitModel.kind == "section")
                            & LearningUnitModel.parent_id.is_(None)
                        ),
                    )
                    .order_by(
                        LearningMasteryModel.next_review_at,
                        LearningMasteryModel.mastery_level,
                        LearningUnitModel.id,
                    )
                )
            )
            result: list[ReviewQueueItem] = []
            for mastery, unit in rows:
                if _is_legacy_zero_placeholder(unit):
                    continue
                evidence = await self._current_evidence(session, course.user_id, course.id, unit.id)
                if not evidence[0]:
                    continue
                if not practice_evidence_stats(chunk.text for chunk in evidence[1]).is_sufficient:
                    continue
                result.append(
                    ReviewQueueItem(
                        learning_unit_id=unit.id,
                        label=clean_learning_unit_label(unit.label),
                        kind=LearningUnitKind(unit.kind),
                        mastery_level=MasteryLevel(mastery.mastery_level),
                        next_review_at=mastery.next_review_at or now,
                        source_status=LearningSourceStatus.VALID,
                        weakness_score=3 - MasteryLevel(mastery.mastery_level).value_number,
                    )
                )
            return result

    async def summary(self, principal: Principal, course_id: str) -> LearningSummary:
        async with self._database.session(principal) as session:
            course = await self._course_for_principal(session, principal, course_id)
            if course is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "课程不存在。")
            await self._sync_units(session, course)
            units = await self._unit_snapshots(session, principal, course_id)
            total = (
                await session.scalar(
                    select(func.count(PracticeAttemptModel.id)).where(
                        PracticeAttemptModel.user_id == course.user_id,
                        PracticeAttemptModel.course_id == course.id,
                    )
                )
                or 0
            )
            correct = (
                await session.scalar(
                    select(func.count(PracticeAttemptModel.id)).where(
                        PracticeAttemptModel.user_id == course.user_id,
                        PracticeAttemptModel.course_id == course.id,
                        PracticeAttemptModel.correct.is_(True),
                    )
                )
                or 0
            )
            queue = await self.review_queue(principal, course_id)
            next_action = (
                f"复习 {queue[0].label}"
                if queue
                else ("开始一组练习" if total == 0 else "继续练习")
            )
            return LearningSummary(
                course_id=course.id,
                total_questions=int(total),
                correct_questions=int(correct),
                accuracy=(float(correct) / float(total)) if total else 0.0,
                due_review_count=len(queue),
                units=units,
                weak_units=queue[:8],
                next_action=next_action,
            )

    async def _process_item(
        self,
        session: AsyncSession,
        batch: PracticeBatchModel,
        item: PracticeBatchItemModel,
    ) -> None:
        managed_batch = await session.scalar(
            select(PracticeBatchModel)
            .where(
                PracticeBatchModel.id == batch.id,
                PracticeBatchModel.user_id == batch.user_id,
                PracticeBatchModel.course_id == batch.course_id,
            )
            .with_for_update()
        )
        if managed_batch is None:
            return
        unit_id = managed_batch.learning_unit_ids[
            (item.ordinal - 1) % len(managed_batch.learning_unit_ids)
        ]
        unit = await session.scalar(
            select(LearningUnitModel).where(
                LearningUnitModel.id == unit_id,
                LearningUnitModel.user_id == managed_batch.user_id,
                LearningUnitModel.course_id == managed_batch.course_id,
            )
        )
        evidence = await self._current_evidence(
            session, managed_batch.user_id, managed_batch.course_id, unit_id
        )
        started = _now(self._clock)
        provider_name: str | None = None
        model_name: str | None = None
        retry_item_failure = False
        try:
            if unit is None or not evidence[0]:
                raise QuestionValidationError("SOURCE_UNAVAILABLE", "来源已失效。")
            question_type = (
                QuestionType.SINGLE_CHOICE if item.ordinal % 2 else QuestionType.TRUE_FALSE
            )
            chunk_text_by_id = {chunk.id: chunk.text for chunk in evidence[1]}
            authorized_evidence: list[AuthorizedEvidence] = []
            for ref in evidence[0]:
                chunk_text = chunk_text_by_id.get(ref.chunk_id)
                if chunk_text is None:
                    raise QuestionValidationError("SOURCE_UNAVAILABLE", "来源片段已失效。")
                authorized_evidence.append(
                    AuthorizedEvidence(
                        course_id=batch.course_id,
                        document_id=ref.document_id,
                        revision_id=ref.revision_id,
                        chunk_id=ref.chunk_id,
                        content_sha256=ref.content_sha256,
                        text=chunk_text,
                        locator=ref.locator,
                    )
                )
            question_evidence = select_question_evidence(
                tuple(authorized_evidence), seed=item.ordinal
            )
            if not question_evidence:
                raise QuestionValidationError("SOURCE_UNAVAILABLE", "来源片段已失效。")
            provider_name = "deepseek"
            question, _response_id, model_name = await self._generator.generate(
                question_id=new_id(),
                course_id=managed_batch.course_id,
                learning_unit_id=unit_id,
                unit_label=unit.label,
                question_type=question_type,
                evidence=question_evidence,
            )
            question = position_correct_option(
                question,
                target_index=balanced_random_answer_position(
                    batch_id=managed_batch.id,
                    question_type=question_type,
                    ordinal=item.ordinal,
                    option_count=len(question.options),
                ),
            )
            provider_name = "deepseek"
            question_model = PracticeQuestionModel(
                id=question.id,
                user_id=managed_batch.user_id,
                course_id=managed_batch.course_id,
                learning_unit_id=question.learning_unit_id,
                source_revision_id=question.source_revision_id,
                question_type=question.question_type.value,
                prompt=question.prompt,
                options=[option.model_dump(mode="json") for option in question.options],
                correct_answer=question.correct_answer,
                explanation=question.explanation,
                evidence_refs=[ref.model_dump(mode="json") for ref in question.evidence_refs],
                difficulty=question.difficulty,
                status="ready",
                content_sha256=question.content_sha256,
            )
            session.add(question_model)
            # These composite foreign keys do not have ORM relationships, so
            # SQLAlchemy cannot infer that the question must be inserted
            # before the evidence rows and batch-item update.
            await session.flush()
            for ordinal, ref in enumerate(question.evidence_refs, start=1):
                session.add(
                    PracticeQuestionEvidenceModel(
                        id=new_id(),
                        user_id=managed_batch.user_id,
                        course_id=managed_batch.course_id,
                        question_id=question.id,
                        ordinal=ordinal,
                        document_id=ref.document_id,
                        revision_id=ref.revision_id,
                        chunk_id=ref.chunk_id,
                        content_sha256=ref.content_sha256,
                        locator=ref.locator.model_dump(mode="json"),
                        quote=ref.quote,
                    )
                )
            await session.flush()
            item.question_id = question.id
            item.status = "succeeded"
            item.failure_code = None
        except (QuestionValidationError, ValueError) as exc:
            item.status = "failed"
            item.failure_code = getattr(exc, "code", "QUESTION_OUTPUT_INVALID")
            retry_item_failure = item.failure_code != "SOURCE_UNAVAILABLE"
        except ProviderError as exc:
            if exc.retryable or exc.code is ProviderErrorCode.TIMEOUT:
                raise RetryableLearningBatchError(str(exc)) from exc
            item.status = "failed"
            item.failure_code = exc.code.value
            retry_item_failure = exc.code is ProviderErrorCode.BAD_RESPONSE
        except TimeoutError as exc:
            raise RetryableLearningBatchError("question provider timed out") from exc
        finally:
            item.attempt_count += 1
            if (
                item.status == "failed"
                and retry_item_failure
                and item.attempt_count < self._settings.practice_generation_max_attempts
            ):
                item.status = "queued"
            item.provider = provider_name
            item.model = model_name
            item.duration_ms = max(0, int((_now(self._clock) - started).total_seconds() * 1000))
            session.add(
                PracticeBatchAttemptModel(
                    id=new_id(),
                    user_id=managed_batch.user_id,
                    course_id=managed_batch.course_id,
                    batch_id=managed_batch.id,
                    item_id=item.id,
                    attempt_number=item.attempt_count,
                    provider=provider_name,
                    model=model_name,
                    duration_ms=item.duration_ms,
                    error_code=item.failure_code,
                )
            )
            terminal = item.status in {"succeeded", "failed"}
            if terminal:
                managed_batch.completed_items += 1
            managed_batch.state_version += 1
            session.add(
                PracticeBatchEventModel(
                    id=new_id(),
                    user_id=managed_batch.user_id,
                    course_id=managed_batch.course_id,
                    batch_id=managed_batch.id,
                    sequence=managed_batch.state_version,
                    event_type="practice.item.completed" if terminal else "practice.item.retrying",
                    data={
                        "item_id": item.id,
                        "status": item.status,
                        "question_id": item.question_id,
                        "attempt_count": item.attempt_count,
                        "failure_code": item.failure_code,
                    },
                )
            )

    async def _course_for_principal(
        self,
        session: AsyncSession,
        principal: Principal,
        course_id: str,
    ) -> CourseModel | None:
        result = await session.scalar(
            select(CourseModel)
            .join(UserModel, UserModel.id == CourseModel.user_id)
            .where(
                CourseModel.id == course_id,
                CourseModel.deleted_at.is_(None),
                UserModel.subject == principal.subject,
                UserModel.authentication_method == principal.authentication_method.value,
            )
        )
        return result

    async def _session_for_principal(
        self,
        session: AsyncSession,
        principal: Principal,
        session_id: str,
        *,
        lock: bool = False,
    ) -> PracticeSessionModel | None:
        statement = (
            select(PracticeSessionModel)
            .join(UserModel, UserModel.id == PracticeSessionModel.user_id)
            .where(
                PracticeSessionModel.id == session_id,
                UserModel.subject == principal.subject,
                UserModel.authentication_method == principal.authentication_method.value,
            )
        )
        if lock:
            statement = statement.with_for_update()
        result = await session.scalar(statement)
        return result

    async def _sync_units(self, session: AsyncSession, course: CourseModel) -> None:
        # Several workspace panels request the same projection at startup.
        # Serialize the read/project/flush sequence so two requests cannot
        # both observe a missing canonical key and insert duplicate units.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": f"learning-units:{course.user_id}:{course.id}"},
        )
        rows = list(
            await session.execute(
                select(DocumentModel, RevisionChunkModel)
                .join(
                    RevisionChunkModel,
                    RevisionChunkModel.revision_id == DocumentModel.active_revision_id,
                )
                .where(
                    DocumentModel.course_id == course.id,
                    DocumentModel.user_id == course.user_id,
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.review_status == "approved",
                    DocumentModel.active_revision_id.is_not(None),
                )
            )
        )
        chunks = [
            ChunkCandidate(
                course_id=course.id,
                document_id=document.id,
                revision_id=chunk.revision_id,
                chunk_id=chunk.id,
                content_sha256=chunk.content_sha256,
                text=chunk.text,
                section_path=tuple(chunk.section_path),
                locator_kind=chunk.locator_kind,
                page_ordinal=chunk.page_ordinal,
                ordinal=chunk.ordinal,
                document_topic=document_topic_from_filename(document.filename),
            )
            for document, chunk in rows
        ]
        candidates = build_learning_unit_candidates(
            course.id,
            chunks,
            controlled_terms=self._settings.course_terms,
        )
        units = list(
            await session.scalars(
                select(LearningUnitModel).where(
                    LearningUnitModel.user_id == course.user_id,
                    LearningUnitModel.course_id == course.id,
                )
            )
        )
        by_key = {unit.canonical_key: unit for unit in units}
        source_rows = list(
            await session.scalars(
                select(LearningUnitSourceModel).where(
                    LearningUnitSourceModel.user_id == course.user_id,
                    LearningUnitSourceModel.course_id == course.id,
                )
            )
        )
        for source in source_rows:
            source.status = "stale"
        for stored_unit in units:
            stored_unit.status = "unavailable"
        source_by_key = {
            (source.unit_id, source.document_id, source.revision_id, source.chunk_id): source
            for source in source_rows
        }
        ordered = sorted(
            candidates,
            key=lambda item: (
                item.kind is not LearningUnitKind.SECTION,
                item.canonical_key.count("/"),
                item.canonical_key,
            ),
        )
        for candidate in ordered:
            candidate_unit: LearningUnitModel | None = by_key.get(candidate.canonical_key)
            if candidate_unit is None:
                candidate_unit = LearningUnitModel(
                    id=new_id(),
                    user_id=course.user_id,
                    course_id=course.id,
                    canonical_key=candidate.canonical_key,
                    label=candidate.label,
                    kind=candidate.kind.value,
                    status="unavailable",
                )
                session.add(candidate_unit)
                await session.flush()
                by_key[candidate.canonical_key] = candidate_unit
            candidate_unit.label = candidate.label
            candidate_unit.kind = candidate.kind.value
            parent = by_key.get(candidate.parent_canonical_key or "")
            candidate_unit.parent_id = None if parent is None else parent.id
            valid = False
            for source_contract in candidate.sources:
                key = (
                    candidate_unit.id,
                    source_contract.document_id,
                    source_contract.revision_id,
                    source_contract.chunk_id,
                )
                candidate_source: LearningUnitSourceModel | None = source_by_key.get(key)
                if candidate_source is None:
                    candidate_source = LearningUnitSourceModel(
                        id=new_id(),
                        user_id=course.user_id,
                        course_id=course.id,
                        unit_id=candidate_unit.id,
                        document_id=source_contract.document_id,
                        revision_id=source_contract.revision_id,
                        chunk_id=source_contract.chunk_id,
                        content_sha256=source_contract.content_sha256,
                        locator=source_contract.locator.model_dump(mode="json"),
                        status="valid",
                    )
                    session.add(candidate_source)
                else:
                    candidate_source.content_sha256 = source_contract.content_sha256
                    candidate_source.locator = source_contract.locator.model_dump(mode="json")
                    candidate_source.status = "valid"
                valid = True
            candidate_unit.status = "available" if valid else "unavailable"

    async def _unit_snapshots(
        self,
        session: AsyncSession,
        principal: Principal,
        course_id: str,
    ) -> list[LearningUnit]:
        rows = list(
            await session.scalars(
                select(LearningUnitModel)
                .join(UserModel, UserModel.id == LearningUnitModel.user_id)
                .where(
                    LearningUnitModel.course_id == course_id,
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
                .order_by(LearningUnitModel.kind, LearningUnitModel.canonical_key)
            )
        )
        # Legacy placeholder rows can still be referenced by questions, mastery, and sources.
        rows = [
            unit
            for unit in rows
            if not _is_legacy_zero_placeholder(unit)
            and (
                unit.kind == LearningUnitKind.CONCEPT.value
                or (unit.kind == LearningUnitKind.SECTION.value and unit.parent_id is None)
            )
        ]
        if not rows:
            return []
        user_id = rows[0].user_id
        source_rows = list(
            await session.scalars(
                select(LearningUnitSourceModel).where(
                    LearningUnitSourceModel.course_id == course_id,
                    LearningUnitSourceModel.user_id == user_id,
                )
            )
        )
        source_by_unit: dict[str, list[LearningUnitSource]] = {}
        for source in source_rows:
            source_by_unit.setdefault(source.unit_id, []).append(_unit_source_from_row(source))
        mastery_rows = list(
            await session.scalars(
                select(LearningMasteryModel).where(
                    LearningMasteryModel.course_id == course_id,
                    LearningMasteryModel.user_id == user_id,
                )
            )
        )
        mastery_by_unit = {row.learning_unit_id: row for row in mastery_rows}
        snapshots: list[LearningUnit] = []
        for unit in rows:
            evidence = await self._current_evidence(session, user_id, course_id, unit.id)
            stats = practice_evidence_stats(chunk.text for chunk in evidence[1])
            if unit.status == LearningUnitStatus.AVAILABLE.value and evidence[0]:
                practice_status = (
                    LearningUnitPracticeStatus.READY
                    if stats.is_sufficient
                    else LearningUnitPracticeStatus.INSUFFICIENT_EVIDENCE
                )
            else:
                practice_status = LearningUnitPracticeStatus.STALE
            unit_sources = source_by_unit.get(unit.id, [])
            if unit.kind == LearningUnitKind.SECTION.value and evidence[0]:
                unit_sources = [
                    LearningUnitSource(
                        document_id=ref.document_id,
                        revision_id=ref.revision_id,
                        chunk_id=ref.chunk_id,
                        content_sha256=ref.content_sha256,
                        locator=ref.locator,
                        status=LearningSourceStatus.VALID,
                    )
                    for ref in evidence[0]
                ]
            snapshot = LearningUnit(
                id=unit.id,
                course_id=unit.course_id,
                canonical_key=unit.canonical_key,
                label=clean_learning_unit_label(unit.label),
                kind=LearningUnitKind(unit.kind),
                parent_id=unit.parent_id,
                status=LearningUnitStatus(unit.status),
                practice_status=practice_status,
                evidence_chunk_count=stats.chunk_count,
                evidence_char_count=stats.char_count,
                sources=unit_sources,
                mastery_level=MasteryLevel(mastery_by_unit[unit.id].mastery_level)
                if unit.id in mastery_by_unit
                else MasteryLevel.NEW,
                next_review_at=mastery_by_unit[unit.id].next_review_at
                if unit.id in mastery_by_unit
                else None,
            )
            snapshots.append(snapshot)
        return sorted(
            snapshots,
            key=lambda unit: (
                unit.kind is not LearningUnitKind.SECTION,
                unit.parent_id or "",
                min(
                    (source.locator.ordinal for source in unit.sources),
                    default=1_000_000,
                ),
                unit.label.casefold(),
            ),
        )

    async def _unit_scope_ids(
        self,
        session: AsyncSession,
        user_id: str,
        course_id: str,
        unit_id: str,
    ) -> set[str]:
        rows = list(
            await session.execute(
                select(LearningUnitModel.id, LearningUnitModel.parent_id).where(
                    LearningUnitModel.user_id == user_id,
                    LearningUnitModel.course_id == course_id,
                )
            )
        )
        children: dict[str, list[str]] = {}
        for child_id, parent_id in rows:
            if parent_id is not None:
                children.setdefault(parent_id, []).append(child_id)
        result = {unit_id}
        pending = [unit_id]
        while pending:
            parent_id = pending.pop()
            for child_id in children.get(parent_id, []):
                if child_id not in result:
                    result.add(child_id)
                    pending.append(child_id)
        return result

    async def _current_evidence(
        self,
        session: AsyncSession,
        user_id: str,
        course_id: str,
        unit_id: str,
    ) -> tuple[list[EvidenceReference], list[RevisionChunkModel]]:
        unit_scope_ids = await self._unit_scope_ids(session, user_id, course_id, unit_id)
        rows = list(
            await session.execute(
                select(LearningUnitSourceModel, DocumentModel, RevisionChunkModel)
                .join(
                    DocumentModel,
                    and_(
                        DocumentModel.id == LearningUnitSourceModel.document_id,
                        DocumentModel.course_id == LearningUnitSourceModel.course_id,
                        DocumentModel.user_id == LearningUnitSourceModel.user_id,
                    ),
                )
                .join(
                    RevisionChunkModel,
                    and_(
                        RevisionChunkModel.id == LearningUnitSourceModel.chunk_id,
                        RevisionChunkModel.revision_id == LearningUnitSourceModel.revision_id,
                    ),
                )
                .where(
                    LearningUnitSourceModel.user_id == user_id,
                    LearningUnitSourceModel.course_id == course_id,
                    LearningUnitSourceModel.unit_id.in_(unit_scope_ids),
                    LearningUnitSourceModel.status == "valid",
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.review_status == "approved",
                    DocumentModel.active_revision_id == LearningUnitSourceModel.revision_id,
                    RevisionChunkModel.content_sha256 == LearningUnitSourceModel.content_sha256,
                )
                .order_by(RevisionChunkModel.ordinal, RevisionChunkModel.id)
            )
        )
        refs: list[EvidenceReference] = []
        chunks: list[RevisionChunkModel] = []
        seen_chunks: set[tuple[str, str, str]] = set()
        for source, document, chunk in rows:
            chunk_key = (source.document_id, source.revision_id, source.chunk_id)
            if chunk_key in seen_chunks:
                continue
            seen_chunks.add(chunk_key)
            locator = _locator(source.locator)
            refs.append(
                EvidenceReference(
                    document_id=source.document_id,
                    document_name=document.filename,
                    revision_id=source.revision_id,
                    chunk_id=source.chunk_id,
                    content_sha256=source.content_sha256,
                    locator=locator,
                    quote=chunk.text[: min(300, len(chunk.text))],
                )
            )
            chunks.append(chunk)
        return refs, chunks

    async def _question_evidence(
        self,
        session: AsyncSession,
        question: PracticeQuestionModel,
    ) -> list[EvidenceReference]:
        refs, _chunks = await self._question_evidence_material(session, question)
        return refs

    async def _question_evidence_material(
        self,
        session: AsyncSession,
        question: PracticeQuestionModel,
    ) -> tuple[list[EvidenceReference], list[RevisionChunkModel]]:
        rows = list(
            await session.execute(
                select(PracticeQuestionEvidenceModel, DocumentModel, RevisionChunkModel)
                .join(
                    DocumentModel,
                    and_(
                        DocumentModel.id == PracticeQuestionEvidenceModel.document_id,
                        DocumentModel.course_id == PracticeQuestionEvidenceModel.course_id,
                        DocumentModel.user_id == PracticeQuestionEvidenceModel.user_id,
                    ),
                )
                .join(
                    RevisionChunkModel,
                    and_(
                        RevisionChunkModel.id == PracticeQuestionEvidenceModel.chunk_id,
                        RevisionChunkModel.revision_id == PracticeQuestionEvidenceModel.revision_id,
                    ),
                )
                .where(
                    PracticeQuestionEvidenceModel.question_id == question.id,
                    PracticeQuestionEvidenceModel.course_id == question.course_id,
                    PracticeQuestionEvidenceModel.user_id == question.user_id,
                    PracticeQuestionEvidenceModel.revision_id == question.source_revision_id,
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.review_status == "approved",
                    DocumentModel.active_revision_id == PracticeQuestionEvidenceModel.revision_id,
                    RevisionChunkModel.content_sha256
                    == PracticeQuestionEvidenceModel.content_sha256,
                )
                .order_by(PracticeQuestionEvidenceModel.ordinal)
            )
        )
        refs: list[EvidenceReference] = []
        chunks: list[RevisionChunkModel] = []
        for evidence, document, chunk in rows:
            if evidence.quote not in chunk.text:
                continue
            quote = chunk.text[: min(2_000, len(chunk.text))].strip()
            if not quote:
                continue
            refs.append(
                EvidenceReference(
                    document_id=evidence.document_id,
                    document_name=document.filename,
                    revision_id=evidence.revision_id,
                    chunk_id=evidence.chunk_id,
                    content_sha256=evidence.content_sha256,
                    locator=_locator(evidence.locator),
                    quote=quote,
                )
            )
            chunks.append(chunk)
        return refs, chunks

    async def _batch_snapshot(
        self, session: AsyncSession, batch: PracticeBatchModel
    ) -> PracticeBatchSnapshot:
        items = list(
            await session.scalars(
                select(PracticeBatchItemModel)
                .where(
                    PracticeBatchItemModel.batch_id == batch.id,
                    PracticeBatchItemModel.user_id == batch.user_id,
                    PracticeBatchItemModel.course_id == batch.course_id,
                )
                .order_by(PracticeBatchItemModel.ordinal)
            )
        )
        return PracticeBatchSnapshot(
            id=batch.id,
            course_id=batch.course_id,
            learning_unit_ids=list(batch.learning_unit_ids),
            target_question_count=batch.target_question_count,
            status=PracticeBatchStatus(batch.status),
            phase=None if batch.phase is None else PracticeBatchPhase(batch.phase),
            completed_items=batch.completed_items,
            total_items=batch.total_items,
            question_ids=[item.question_id for item in items if item.question_id is not None],
            items=[
                PracticeBatchItem(
                    id=item.id,
                    question_id=item.question_id,
                    status=item.status,
                    failure_code=item.failure_code,
                    attempt_count=item.attempt_count,
                )
                for item in items
            ],
            failure_code=batch.failure_code,
            created_at=batch.created_at,
            started_at=batch.started_at,
            completed_at=batch.completed_at,
        )

    async def _session_snapshot(
        self,
        session: AsyncSession,
        session_model: PracticeSessionModel,
    ) -> PracticeSessionSnapshot:
        rows = list(
            await session.execute(
                select(PracticeSessionQuestionModel, PracticeQuestionModel)
                .join(
                    PracticeQuestionModel,
                    and_(
                        PracticeQuestionModel.id == PracticeSessionQuestionModel.question_id,
                        PracticeQuestionModel.course_id == PracticeSessionQuestionModel.course_id,
                        PracticeQuestionModel.user_id == PracticeSessionQuestionModel.user_id,
                    ),
                )
                .where(
                    PracticeSessionQuestionModel.session_id == session_model.id,
                    PracticeSessionQuestionModel.course_id == session_model.course_id,
                    PracticeSessionQuestionModel.user_id == session_model.user_id,
                )
                .order_by(PracticeSessionQuestionModel.ordinal)
            )
        )
        attempts = list(
            await session.scalars(
                select(PracticeAttemptModel).where(
                    PracticeAttemptModel.session_id == session_model.id,
                    PracticeAttemptModel.course_id == session_model.course_id,
                    PracticeAttemptModel.user_id == session_model.user_id,
                )
            )
        )
        attempts_by_question = {attempt.question_id: attempt for attempt in attempts}
        questions: list[PracticeQuestionView] = []
        for _session_question, question in rows:
            attempt = attempts_by_question.get(question.id)
            persisted_status = QuestionStatus(question.status)
            evidence = (
                await self._question_evidence(session, question)
                if persisted_status is QuestionStatus.READY
                else []
            )
            view_status = (
                (QuestionStatus.READY if evidence else QuestionStatus.STALE)
                if persisted_status is QuestionStatus.READY
                else persisted_status
            )
            questions.append(
                PracticeQuestionView(
                    id=question.id,
                    learning_unit_id=question.learning_unit_id,
                    question_type=question.question_type,
                    prompt=question.prompt,
                    options=question.options,
                    difficulty=question.difficulty,
                    status=view_status,
                    evidence_refs=evidence,
                    answered=attempt is not None,
                    outcome=(
                        None
                        if attempt is None
                        else (
                            AttemptOutcome.CORRECT if attempt.correct else AttemptOutcome.INCORRECT
                        )
                    ),
                    submitted_answer=None if attempt is None else attempt.answer,
                    explanation=None if attempt is None else question.explanation,
                    mastery_reason=None if attempt is None else attempt.feedback,
                    viewed_hint=None if attempt is None else attempt.viewed_hint,
                )
            )
        return PracticeSessionSnapshot(
            id=session_model.id,
            course_id=session_model.course_id,
            status=PracticeSessionStatus(session_model.status),
            question_count=session_model.question_count,
            questions=questions,
            started_at=session_model.started_at,
            completed_at=session_model.completed_at,
        )

    async def _attempt_result(
        self,
        session: AsyncSession,
        attempt: PracticeAttemptModel,
    ) -> PracticeAttemptResult:
        mastery = await session.scalar(
            select(LearningMasteryModel).where(
                LearningMasteryModel.learning_unit_id
                == (
                    await session.scalar(
                        select(PracticeQuestionModel.learning_unit_id).where(
                            PracticeQuestionModel.id == attempt.question_id,
                            PracticeQuestionModel.user_id == attempt.user_id,
                            PracticeQuestionModel.course_id == attempt.course_id,
                        )
                    )
                ),
                LearningMasteryModel.user_id == attempt.user_id,
                LearningMasteryModel.course_id == attempt.course_id,
            )
        )
        question = await session.scalar(
            select(PracticeQuestionModel).where(
                PracticeQuestionModel.id == attempt.question_id,
                PracticeQuestionModel.user_id == attempt.user_id,
                PracticeQuestionModel.course_id == attempt.course_id,
            )
        )
        if question is None or mastery is None:
            raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "作答结果不存在。")
        refs = await self._question_evidence(session, question)
        if not refs:
            raise LearningServiceError(LearningServiceErrorCode.STALE_QUESTION, "题目来源已失效。")
        current_level = MasteryLevel(attempt.mastery_level)
        previous = MasteryLevel(attempt.previous_mastery_level)
        return PracticeAttemptResult(
            id=attempt.id,
            question_id=attempt.question_id,
            outcome=AttemptOutcome.CORRECT if attempt.correct else AttemptOutcome.INCORRECT,
            score=attempt.score,
            explanation=question.explanation,
            evidence_refs=refs,
            mastery=MasteryUpdate(
                learning_unit_id=question.learning_unit_id,
                previous_level=previous,
                level=current_level,
                reason=attempt.feedback,
                next_review_at=attempt.next_review_at,
            ),
        )
