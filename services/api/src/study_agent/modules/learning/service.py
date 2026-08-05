"""Principal- and course-scoped learning-loop application service."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.config import Settings
from study_agent.identity.principal import AuthenticationMethod, Principal
from study_agent.infrastructure.db.models import (
    ConversationMessageModel,
    ConversationModel,
    CourseModel,
    DocumentModel,
    LearningMasteryModel,
    LearningUnitEvidenceSupplementModel,
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
from study_agent.modules.answering.memory import (
    relevant_memories_in_session,
    upsert_explicit_memories,
)
from study_agent.modules.answering.telemetry import log_conversation_event
from study_agent.modules.learning.concepts import (
    ChunkCandidate,
    build_learning_unit_candidates,
    clean_learning_unit_label,
    document_title_from_filename,
    document_topic_from_filename,
    exercise_prototype_number,
    is_exercise_prototype_label,
    is_zero_placeholder_label,
    practice_confidence_for_unit,
    practice_evidence_stats,
    practice_mode_for_unit,
)
from study_agent.modules.learning.grading import (
    ConstructedAnswerGrader,
    ConstructedGradingError,
    normalize_constructed_answer,
)
from study_agent.modules.learning.mastery import MasteryState, update_mastery
from study_agent.modules.learning.questions import (
    AuthorizedEvidence,
    QuestionGenerator,
    QuestionValidationError,
    balanced_random_answer_position,
    infer_exercise_question_type,
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
from study_agent.providers.protocols import Clock, LearnerMemoryContext
from study_contracts import (
    AttemptOutcome,
    EvidenceReference,
    LearningSourceStatus,
    LearningSummary,
    LearningUnit,
    LearningUnitEvidenceItem,
    LearningUnitEvidenceOrigin,
    LearningUnitEvidenceRole,
    LearningUnitEvidenceSupplementRequest,
    LearningUnitKind,
    LearningUnitPracticeMode,
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
    PracticeTutorConversation,
    PracticeTutorIntent,
    PracticeTutorMessage,
    PracticeTutorMode,
    PracticeTutorRequest,
    PracticeTutorResponse,
    PracticeTutorTurn,
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


@dataclass(frozen=True, slots=True)
class _EvidenceMaterial:
    """Current text for one parsed source, optionally overlaid by the user."""

    source_id: str
    chunk_id: str
    content_sha256: str
    text: str
    supplement_id: str | None = None
    role: str | None = None

    @property
    def id(self) -> str:
        return self.chunk_id


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _practice_confidence_for_materials(
    materials: tuple[_EvidenceMaterial, ...] | list[_EvidenceMaterial],
    *,
    practice_mode: LearningUnitPracticeMode,
    is_exercise_prototype: bool = False,
) -> tuple[LearningUnitPracticeStatus, str | None]:
    texts = tuple(material.text for material in materials)
    status, note = practice_confidence_for_unit(
        texts,
        practice_mode=practice_mode,
        is_exercise_prototype=is_exercise_prototype,
    )
    supplied = [item for item in materials if item.supplement_id is not None]
    if supplied and status is not LearningUnitPracticeStatus.INSUFFICIENT_EVIDENCE:
        return LearningUnitPracticeStatus.READY, "已采用用户补充的高置信证据。"
    if (
        supplied
        and any(item.role == LearningUnitEvidenceRole.COMPLETE_PROTOTYPE.value for item in supplied)
        and sum(len(item.text.strip()) for item in supplied) >= 20
    ):
        # A learner-confirmed complete prototype is allowed to rescue a short or noisy OCR
        # fragment, while the request contract still prevents blank supplements.
        return LearningUnitPracticeStatus.READY, "已采用用户补充的完整原型。"
    return status, note


def _is_legacy_zero_placeholder(unit: LearningUnitModel) -> bool:
    key_label = unit.canonical_key.rsplit("/", 1)[-1]
    if ":" in key_label:
        key_label = key_label.rsplit(":", 1)[-1]
    return is_zero_placeholder_label(unit.label) or is_zero_placeholder_label(key_label)


def _generation_target_schedule(
    group_sizes: tuple[int, ...], total_items: int
) -> tuple[tuple[int, int], ...]:
    """Allocate every selected scope before spreading work across its prototypes."""

    if total_items < 1 or not group_sizes or any(size < 1 for size in group_sizes):
        return ()

    counts = [0] * len(group_sizes)
    for group_index in range(min(total_items, len(group_sizes))):
        counts[group_index] = 1
    remaining = total_items - sum(counts)

    while remaining > 0:
        added_distinct_target = False
        for group_index, group_size in enumerate(group_sizes):
            if remaining == 0:
                break
            if counts[group_index] >= group_size:
                continue
            counts[group_index] += 1
            remaining -= 1
            added_distinct_target = True
        if added_distinct_target:
            continue
        for group_index in range(len(group_sizes)):
            if remaining == 0:
                break
            counts[group_index] += 1
            remaining -= 1

    candidate_indices: list[tuple[int, ...]] = []
    for group_size, count in zip(group_sizes, counts, strict=True):
        if count == 0:
            candidate_indices.append(())
        elif count == 1:
            candidate_indices.append((0,))
        elif count <= group_size:
            denominator = count - 1
            candidate_indices.append(
                tuple(
                    (position * (group_size - 1) + denominator // 2) // denominator
                    for position in range(count)
                )
            )
        else:
            candidate_indices.append(
                tuple(range(group_size))
                + tuple(index % group_size for index in range(count - group_size))
            )

    schedule: list[tuple[int, int]] = []
    for position in range(max(counts, default=0)):
        for group_index, indices in enumerate(candidate_indices):
            if position < len(indices):
                schedule.append((group_index, indices[position]))
    return tuple(schedule)


_TUTOR_HISTORY_MAX_ESTIMATED_TOKENS = 3_000
_TUTOR_HISTORY_MAX_TURNS = 12
_TUTOR_TRANSCRIPT_MAX_MESSAGES = 200
_TUTOR_SUMMARY_MAX_CHARS = 2_000
_TUTOR_SUMMARY_MAX_TOPICS = 24


def _estimated_tokens(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


def _tutor_history(messages: list[ConversationMessageModel]) -> list[PracticeTutorTurn]:
    """Keep recent complete turns within a deterministic provider budget."""

    completed: list[tuple[ConversationMessageModel, ConversationMessageModel]] = []
    index = 0
    while index + 1 < len(messages):
        user_message = messages[index]
        assistant_message = messages[index + 1]
        if user_message.role == "user" and assistant_message.role == "assistant":
            completed.append((user_message, assistant_message))
            index += 2
            continue
        index += 1

    selected: list[tuple[ConversationMessageModel, ConversationMessageModel]] = []
    used_tokens = 0
    for pair in reversed(completed[-_TUTOR_HISTORY_MAX_TURNS:]):
        pair_tokens = sum(_estimated_tokens(message.content) for message in pair)
        if used_tokens + pair_tokens > _TUTOR_HISTORY_MAX_ESTIMATED_TOKENS:
            break
        selected.append(pair)
        used_tokens += pair_tokens

    history: list[PracticeTutorTurn] = []
    for user_message, assistant_message in reversed(selected):
        history.extend(
            (
                PracticeTutorTurn(role="user", content=user_message.content),
                PracticeTutorTurn(role="assistant", content=assistant_message.content),
            )
        )
    return history


def _tutor_conversation_snapshot(
    session_model: PracticeSessionModel,
    question_id: str,
    conversation: ConversationModel | None,
    messages: list[ConversationMessageModel],
) -> PracticeTutorConversation:
    visible_messages = messages[-_TUTOR_TRANSCRIPT_MAX_MESSAGES:]
    return PracticeTutorConversation(
        conversation_id=None if conversation is None else conversation.id,
        session_id=session_model.id,
        question_id=question_id,
        messages=[
            PracticeTutorMessage(
                id=message.id,
                role="user" if message.role == "user" else "assistant",
                content=message.content,
                intent=PracticeTutorIntent(message.intent or PracticeTutorIntent.HINT.value),
                mode=None if message.mode is None else PracticeTutorMode(message.mode),
                evidence_refs=[
                    EvidenceReference.model_validate(reference)
                    for reference in message.evidence_refs
                ],
                created_at=message.created_at,
            )
            for message in visible_messages
        ],
        has_earlier_messages=len(messages) > len(visible_messages),
    )


async def _refresh_tutor_summary(
    session: AsyncSession,
    conversation: ConversationModel,
) -> None:
    total = int(
        await session.scalar(
            select(func.count(ConversationMessageModel.id)).where(
                ConversationMessageModel.conversation_id == conversation.id,
                ConversationMessageModel.user_id == conversation.user_id,
                ConversationMessageModel.course_id == conversation.course_id,
                ConversationMessageModel.role == "user",
            )
        )
        or 0
    )
    older_count = max(0, total - _TUTOR_HISTORY_MAX_TURNS)
    if older_count == 0:
        conversation.summary_text = None
        conversation.summary_version = None
        conversation.summary_turn_count = 0
        return
    rows = list(
        await session.scalars(
            select(ConversationMessageModel)
            .where(
                ConversationMessageModel.conversation_id == conversation.id,
                ConversationMessageModel.user_id == conversation.user_id,
                ConversationMessageModel.course_id == conversation.course_id,
                ConversationMessageModel.role == "user",
            )
            .order_by(ConversationMessageModel.sequence.desc())
            .offset(_TUTOR_HISTORY_MAX_TURNS)
            .limit(_TUTOR_SUMMARY_MAX_TOPICS)
        )
    )
    rows.reverse()
    lines = [
        f"- {message.intent or 'hint'}: {' '.join(message.content.split())[:180]}"
        for message in rows
    ]
    prefix = f"较早的 {older_count} 轮单题辅导主题:"
    while lines and len("\n".join((prefix, *lines))) > _TUTOR_SUMMARY_MAX_CHARS:
        lines.pop(0)
    conversation.summary_text = "\n".join((prefix, *lines))
    conversation.summary_version = "tutor-topic-summary-1.0"
    conversation.summary_turn_count = older_count


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
        self._grader = ConstructedAnswerGrader(
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

    async def list_learning_unit_evidence(
        self,
        principal: Principal,
        course_id: str,
        unit_id: str,
    ) -> list[LearningUnitEvidenceItem]:
        async with self._database.session(principal) as session:
            course = await self._course_for_principal(session, principal, course_id)
            if course is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "课程不存在。")
            await self._sync_units(session, course)
            unit = await session.scalar(
                select(LearningUnitModel).where(
                    LearningUnitModel.id == unit_id,
                    LearningUnitModel.course_id == course_id,
                    LearningUnitModel.user_id == course.user_id,
                )
            )
            if unit is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "学习单元不存在。")
            return await self._list_learning_unit_evidence_in_session(
                session, course.user_id, course_id, unit
            )

    async def create_learning_unit_evidence_supplement(
        self,
        principal: Principal,
        course_id: str,
        unit_id: str,
        request: LearningUnitEvidenceSupplementRequest,
    ) -> LearningUnitEvidenceItem:
        async with self._database.session(principal) as session:
            course = await self._course_for_principal(session, principal, course_id)
            if course is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "课程不存在。")
            await self._sync_units(session, course)
            unit = await session.scalar(
                select(LearningUnitModel).where(
                    LearningUnitModel.id == unit_id,
                    LearningUnitModel.course_id == course_id,
                    LearningUnitModel.user_id == course.user_id,
                )
            )
            if unit is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "学习单元不存在。")
            scope_ids = await self._unit_scope_ids(session, course.user_id, course_id, unit_id)
            source = await session.scalar(
                select(LearningUnitSourceModel)
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
                    LearningUnitSourceModel.id == request.source_id,
                    LearningUnitSourceModel.user_id == course.user_id,
                    LearningUnitSourceModel.course_id == course_id,
                    LearningUnitSourceModel.unit_id.in_(scope_ids),
                    LearningUnitSourceModel.status == "valid",
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.review_status == "approved",
                    DocumentModel.active_revision_id == LearningUnitSourceModel.revision_id,
                    RevisionChunkModel.content_sha256 == LearningUnitSourceModel.content_sha256,
                )
            )
            if source is None:
                raise LearningServiceError(
                    LearningServiceErrorCode.SOURCE_UNAVAILABLE,
                    "所选证据片段已失效, 请重新打开证据列表。",
                )
            # Lock the complete selected scope so replacement and revocation cannot
            # race across a parent section and one of its child learning units.
            await session.execute(
                select(LearningUnitModel.id)
                .where(
                    LearningUnitModel.user_id == course.user_id,
                    LearningUnitModel.course_id == course_id,
                    LearningUnitModel.id.in_(scope_ids),
                )
                .order_by(LearningUnitModel.id)
                .with_for_update()
            )
            supplement = await session.scalar(
                select(LearningUnitEvidenceSupplementModel)
                .where(
                    LearningUnitEvidenceSupplementModel.user_id == course.user_id,
                    LearningUnitEvidenceSupplementModel.course_id == course_id,
                    LearningUnitEvidenceSupplementModel.unit_id == source.unit_id,
                    LearningUnitEvidenceSupplementModel.status == "active",
                )
                .with_for_update()
            )
            if supplement is not None:
                supplement.status = "superseded"
                await self._mark_questions_stale_for_supplement(session, supplement.id)
            normalized_text = request.text.strip()
            supplement = LearningUnitEvidenceSupplementModel(
                id=new_id(),
                user_id=course.user_id,
                course_id=course_id,
                unit_id=source.unit_id,
                source_id=source.id,
                source_content_sha256=source.content_sha256,
                role=request.role.value,
                text=normalized_text,
                content_sha256=_text_sha256(normalized_text),
                status="active",
            )
            session.add(supplement)
            await session.flush()
            items = await self._list_learning_unit_evidence_in_session(
                session, course.user_id, course_id, unit
            )
            created = next((item for item in items if item.supplement_id == supplement.id), None)
            if created is None:
                raise LearningServiceError(
                    LearningServiceErrorCode.SOURCE_UNAVAILABLE,
                    "补充证据保存后无法重新读取。",
                )
            return created

    async def revoke_learning_unit_evidence_supplement(
        self,
        principal: Principal,
        course_id: str,
        unit_id: str,
        supplement_id: str,
    ) -> None:
        async with self._database.session(principal) as session:
            course = await self._course_for_principal(session, principal, course_id)
            if course is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "课程不存在。")
            unit = await session.scalar(
                select(LearningUnitModel).where(
                    LearningUnitModel.id == unit_id,
                    LearningUnitModel.course_id == course_id,
                    LearningUnitModel.user_id == course.user_id,
                )
            )
            if unit is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "学习单元不存在。")
            scope_ids = await self._unit_scope_ids(session, course.user_id, course_id, unit_id)
            await session.execute(
                select(LearningUnitModel.id)
                .where(
                    LearningUnitModel.user_id == course.user_id,
                    LearningUnitModel.course_id == course_id,
                    LearningUnitModel.id.in_(scope_ids),
                )
                .order_by(LearningUnitModel.id)
                .with_for_update()
            )
            supplement = await session.scalar(
                select(LearningUnitEvidenceSupplementModel)
                .where(
                    LearningUnitEvidenceSupplementModel.id == supplement_id,
                    LearningUnitEvidenceSupplementModel.user_id == course.user_id,
                    LearningUnitEvidenceSupplementModel.course_id == course_id,
                    LearningUnitEvidenceSupplementModel.unit_id.in_(scope_ids),
                    LearningUnitEvidenceSupplementModel.status == "active",
                )
                .with_for_update()
            )
            if supplement is None:
                raise LearningServiceError(
                    LearningServiceErrorCode.NOT_FOUND, "有效的补充证据不存在。"
                )
            supplement.status = "revoked"
            await self._mark_questions_stale_for_supplement(session, supplement.id)

    async def _mark_questions_stale_for_supplement(
        self,
        session: AsyncSession,
        supplement_id: str,
    ) -> None:
        question_ids = list(
            await session.scalars(
                select(PracticeQuestionEvidenceModel.question_id).where(
                    PracticeQuestionEvidenceModel.supplement_id == supplement_id
                )
            )
        )
        if question_ids:
            await session.execute(
                update(PracticeQuestionModel)
                .where(PracticeQuestionModel.id.in_(question_ids))
                .values(status=QuestionStatus.STALE.value)
            )

    async def _list_learning_unit_evidence_in_session(
        self,
        session: AsyncSession,
        user_id: str,
        course_id: str,
        unit: LearningUnitModel,
    ) -> list[LearningUnitEvidenceItem]:
        scope_ids = await self._unit_scope_ids(session, user_id, course_id, unit.id)
        rows = list(
            await session.execute(
                select(
                    LearningUnitSourceModel,
                    DocumentModel,
                    RevisionChunkModel,
                    LearningUnitEvidenceSupplementModel,
                )
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
                .outerjoin(
                    LearningUnitEvidenceSupplementModel,
                    and_(
                        LearningUnitEvidenceSupplementModel.source_id == LearningUnitSourceModel.id,
                        LearningUnitEvidenceSupplementModel.course_id
                        == LearningUnitSourceModel.course_id,
                        LearningUnitEvidenceSupplementModel.user_id
                        == LearningUnitSourceModel.user_id,
                        LearningUnitEvidenceSupplementModel.source_content_sha256
                        == LearningUnitSourceModel.content_sha256,
                        LearningUnitEvidenceSupplementModel.status == "active",
                    ),
                )
                .where(
                    LearningUnitSourceModel.user_id == user_id,
                    LearningUnitSourceModel.course_id == course_id,
                    LearningUnitSourceModel.unit_id.in_(scope_ids),
                    LearningUnitSourceModel.status == "valid",
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.review_status == "approved",
                    DocumentModel.active_revision_id == LearningUnitSourceModel.revision_id,
                    RevisionChunkModel.content_sha256 == LearningUnitSourceModel.content_sha256,
                )
                .order_by(RevisionChunkModel.ordinal, LearningUnitSourceModel.id)
            )
        )
        selected: dict[
            tuple[str, str, str],
            tuple[
                LearningUnitSourceModel,
                DocumentModel,
                RevisionChunkModel,
                LearningUnitEvidenceSupplementModel | None,
            ],
        ] = {}
        for source, document, chunk, supplement in rows:
            key = (source.document_id, source.revision_id, source.chunk_id)
            current = selected.get(key)
            if current is None or (current[3] is None and supplement is not None):
                selected[key] = (source, document, chunk, supplement)
        selected_rows = sorted(selected.values(), key=lambda row: (row[2].ordinal, row[2].id))
        materials = [
            _EvidenceMaterial(
                source_id=source.id,
                chunk_id=chunk.id,
                content_sha256=(
                    source.content_sha256 if supplement is None else supplement.content_sha256
                ),
                text=chunk.text if supplement is None else supplement.text,
                supplement_id=None if supplement is None else supplement.id,
                role=None if supplement is None else supplement.role,
            )
            for source, _document, chunk, supplement in selected_rows
        ]
        mode = practice_mode_for_unit(
            unit.kind,
            unit.label,
            evidence_texts=(item.text for item in materials),
        )
        practice_status, confidence_note = _practice_confidence_for_materials(
            materials,
            practice_mode=mode,
            is_exercise_prototype=(
                unit.kind == LearningUnitKind.CONCEPT.value
                and is_exercise_prototype_label(unit.label)
            ),
        )
        result: list[LearningUnitEvidenceItem] = []
        for (source, document, _chunk, supplement), material in zip(
            selected_rows, materials, strict=True
        ):
            result.append(
                LearningUnitEvidenceItem(
                    id=source.id,
                    unit_id=source.unit_id,
                    source_id=source.id,
                    supplement_id=None,
                    origin=LearningUnitEvidenceOrigin.PARSED,
                    role=None,
                    document_id=source.document_id,
                    document_name=document.filename,
                    revision_id=source.revision_id,
                    chunk_id=source.chunk_id,
                    content_sha256=source.content_sha256,
                    locator=_locator(source.locator),
                    text=_chunk.text,
                    is_primary=supplement is None,
                    practice_status=practice_status,
                    confidence_note=confidence_note,
                    created_at=source.created_at,
                )
            )
            if supplement is not None:
                result.append(
                    LearningUnitEvidenceItem(
                        id=supplement.id,
                        unit_id=source.unit_id,
                        source_id=source.id,
                        supplement_id=supplement.id,
                        origin=LearningUnitEvidenceOrigin.USER_SUPPLIED,
                        role=LearningUnitEvidenceRole(supplement.role),
                        document_id=source.document_id,
                        document_name=document.filename,
                        revision_id=source.revision_id,
                        chunk_id=source.chunk_id,
                        content_sha256=material.content_sha256,
                        locator=_locator(source.locator),
                        text=material.text,
                        is_primary=True,
                        practice_status=practice_status,
                        confidence_note=confidence_note,
                        created_at=supplement.created_at,
                    )
                )
        return result

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
                source_texts = tuple(chunk.text for chunk in evidence[1])
                practice_mode = practice_mode_for_unit(
                    unit.kind,
                    unit.label,
                    evidence_texts=source_texts,
                )
                practice_status, _confidence_note = _practice_confidence_for_materials(
                    evidence[1],
                    practice_mode=practice_mode,
                    is_exercise_prototype=(
                        unit.kind == LearningUnitKind.CONCEPT.value
                        and is_exercise_prototype_label(unit.label)
                    ),
                )
                if practice_status is LearningUnitPracticeStatus.INSUFFICIENT_EVIDENCE:
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
        """Answer from server-owned history and persist one complete tutoring turn."""

        started_at = time.perf_counter()

        async with self._database.session(principal) as session:
            session_model = await self._session_for_principal(session, principal, session_id)
            if session_model is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "练习会话不存在。")
            question = await self._tutor_question(session, session_model, question_id)
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
            conversation = await self._tutor_conversation(
                session,
                session_model,
                question_id,
                create=False,
            )
            if conversation is not None:
                replay = await self._tutor_replay(
                    session,
                    conversation,
                    session_model,
                    request,
                )
                if replay is not None:
                    return replay
            history_models = await self._tutor_messages(
                session,
                None if conversation is None else conversation.id,
                session_model,
            )
            conversation_summary = None if conversation is None else conversation.summary_text
            memory_snapshots = await relevant_memories_in_session(
                session,
                user_id=session_model.user_id,
                course_id=session_model.course_id,
                question=f"{question.prompt} {request.message}",
            )
            learner_memories = tuple(
                LearnerMemoryContext(
                    memory_type=memory.memory_type.value,
                    content=memory.content,
                )
                for memory in memory_snapshots
            )
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
            tutor_reply = await self._tutor.answer(
                mode=mode,
                question_type=question_type,
                prompt=prompt,
                options=options,
                correct_answer=correct_answer,
                explanation=explanation,
                submitted_answer=submitted_answer,
                message=request.message,
                history=_tutor_history(history_models),
                evidence=tutor_evidence,
                conversation_summary=conversation_summary,
                learner_memories=learner_memories,
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

        async with self._database.session(principal) as session:
            session_model = await self._session_for_principal(
                session, principal, session_id, lock=True
            )
            if session_model is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "练习会话不存在。")
            question = await self._tutor_question(session, session_model, question_id)
            if question is None:
                raise LearningServiceError(
                    LearningServiceErrorCode.STALE_QUESTION,
                    "题目不属于当前会话或来源已失效。",
                )
            current_evidence, _current_material = await self._question_evidence_material(
                session, question
            )
            if not current_evidence:
                raise LearningServiceError(
                    LearningServiceErrorCode.STALE_QUESTION,
                    "题目来源已失效。",
                )
            conversation = await self._tutor_conversation(
                session,
                session_model,
                question_id,
                create=True,
            )
            if conversation is None:
                raise LearningServiceError(
                    LearningServiceErrorCode.NOT_FOUND,
                    "单题对话不存在。",
                )
            replay = await self._tutor_replay(
                session,
                conversation,
                session_model,
                request,
            )
            if replay is not None:
                return replay
            last_sequence = await session.scalar(
                select(func.max(ConversationMessageModel.sequence)).where(
                    ConversationMessageModel.conversation_id == conversation.id,
                    ConversationMessageModel.course_id == session_model.course_id,
                    ConversationMessageModel.user_id == session_model.user_id,
                )
            )
            first_sequence = int(last_sequence or 0) + 1
            user_message = ConversationMessageModel(
                id=new_id(),
                user_id=session_model.user_id,
                course_id=session_model.course_id,
                conversation_id=conversation.id,
                sequence=first_sequence,
                turn_id=request.turn_id,
                role="user",
                content=request.message,
                intent=tutor_reply.intent.value,
                evidence_refs=[],
            )
            assistant_message = ConversationMessageModel(
                id=new_id(),
                user_id=session_model.user_id,
                course_id=session_model.course_id,
                conversation_id=conversation.id,
                sequence=first_sequence + 1,
                turn_id=request.turn_id,
                role="assistant",
                content=tutor_reply.answer_markdown,
                intent=tutor_reply.intent.value,
                mode=tutor_reply.mode.value,
                evidence_refs=[
                    reference.model_dump(mode="json") for reference in tutor_reply.evidence_refs
                ],
            )
            session.add_all((user_message, assistant_message))
            now = _now(self._clock)
            conversation.updated_at = now
            await session.flush()
            await upsert_explicit_memories(
                session,
                user_id=session_model.user_id,
                course_id=session_model.course_id,
                message=request.message,
                now=now,
                source_message_id=user_message.id,
            )
            await _refresh_tutor_summary(session, conversation)
            await session.flush()
            log_conversation_event(
                "practice_tutor_completed",
                course_id=session_model.course_id,
                conversation_id=conversation.id,
                conversation_type="practice_tutor",
                status="answered",
                intent=tutor_reply.intent.value,
                context_turn_count=len(history_models) // 2,
                memory_count=len(learner_memories),
                message_count=first_sequence + 1,
                duration_ms=max(0, int((time.perf_counter() - started_at) * 1_000)),
            )
            return PracticeTutorResponse(
                conversation_id=conversation.id,
                message_id=assistant_message.id,
                intent=tutor_reply.intent,
                mode=tutor_reply.mode,
                answer_markdown=tutor_reply.answer_markdown,
                evidence_refs=tutor_reply.evidence_refs,
                created_at=assistant_message.created_at,
            )

    async def get_tutor_conversation(
        self,
        principal: Principal,
        session_id: str,
        question_id: str,
    ) -> PracticeTutorConversation:
        async with self._database.session(principal) as session:
            session_model = await self._session_for_principal(session, principal, session_id)
            if session_model is None:
                raise LearningServiceError(LearningServiceErrorCode.NOT_FOUND, "练习会话不存在。")
            question = await self._tutor_question(session, session_model, question_id)
            if question is None:
                raise LearningServiceError(
                    LearningServiceErrorCode.STALE_QUESTION,
                    "题目不属于当前会话或来源已失效。",
                )
            evidence_refs, _evidence_material = await self._question_evidence_material(
                session, question
            )
            if not evidence_refs:
                raise LearningServiceError(
                    LearningServiceErrorCode.STALE_QUESTION,
                    "题目来源已失效。",
                )
            conversation = await self._tutor_conversation(
                session,
                session_model,
                question_id,
                create=False,
            )
            messages = await self._tutor_messages(
                session,
                None if conversation is None else conversation.id,
                session_model,
            )
            if conversation is not None:
                log_conversation_event(
                    "practice_tutor_recovered",
                    course_id=session_model.course_id,
                    conversation_id=conversation.id,
                    conversation_type="practice_tutor",
                    status="recovered",
                    message_count=len(messages),
                )
            return _tutor_conversation_snapshot(
                session_model,
                question_id,
                conversation,
                messages,
            )

    async def _tutor_question(
        self,
        session: AsyncSession,
        session_model: PracticeSessionModel,
        question_id: str,
    ) -> PracticeQuestionModel | None:
        return cast(
            PracticeQuestionModel | None,
            await session.scalar(
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
            ),
        )

    async def _tutor_conversation(
        self,
        session: AsyncSession,
        session_model: PracticeSessionModel,
        question_id: str,
        *,
        create: bool,
    ) -> ConversationModel | None:
        conversation = await session.scalar(
            select(ConversationModel).where(
                ConversationModel.user_id == session_model.user_id,
                ConversationModel.course_id == session_model.course_id,
                ConversationModel.conversation_type == "practice_tutor",
                ConversationModel.practice_session_id == session_model.id,
                ConversationModel.practice_question_id == question_id,
            )
        )
        if conversation is not None or not create:
            return conversation
        conversation = ConversationModel(
            id=new_id(),
            user_id=session_model.user_id,
            course_id=session_model.course_id,
            conversation_type="practice_tutor",
            practice_session_id=session_model.id,
            practice_question_id=question_id,
            title="单题辅导",
            auto_title_pending=False,
        )
        session.add(conversation)
        await session.flush()
        return conversation

    async def _tutor_messages(
        self,
        session: AsyncSession,
        conversation_id: str | None,
        session_model: PracticeSessionModel,
    ) -> list[ConversationMessageModel]:
        if conversation_id is None:
            return []
        rows = await session.scalars(
            select(ConversationMessageModel)
            .where(
                ConversationMessageModel.conversation_id == conversation_id,
                ConversationMessageModel.course_id == session_model.course_id,
                ConversationMessageModel.user_id == session_model.user_id,
            )
            .order_by(ConversationMessageModel.sequence)
        )
        return list(rows)

    async def _tutor_replay(
        self,
        session: AsyncSession,
        conversation: ConversationModel,
        session_model: PracticeSessionModel,
        request: PracticeTutorRequest,
    ) -> PracticeTutorResponse | None:
        rows = list(
            await session.scalars(
                select(ConversationMessageModel)
                .where(
                    ConversationMessageModel.conversation_id == conversation.id,
                    ConversationMessageModel.course_id == session_model.course_id,
                    ConversationMessageModel.user_id == session_model.user_id,
                    ConversationMessageModel.turn_id == request.turn_id,
                )
                .order_by(ConversationMessageModel.sequence)
            )
        )
        if not rows:
            return None
        user_message = next((message for message in rows if message.role == "user"), None)
        assistant_message = next((message for message in rows if message.role == "assistant"), None)
        if user_message is None or assistant_message is None:
            raise LearningServiceError(
                LearningServiceErrorCode.IDEMPOTENCY_CONFLICT,
                "该轮单题对话尚未形成完整记录, 请使用新的请求重试。",
            )
        if user_message.content != request.message:
            raise LearningServiceError(
                LearningServiceErrorCode.IDEMPOTENCY_CONFLICT,
                "同一个对话轮次标识已用于不同问题。",
            )
        if assistant_message.intent is None or assistant_message.mode is None:
            raise LearningServiceError(
                LearningServiceErrorCode.IDEMPOTENCY_CONFLICT,
                "该轮单题对话记录不完整。",
            )
        evidence_refs = [
            EvidenceReference.model_validate(reference)
            for reference in assistant_message.evidence_refs
        ]
        return PracticeTutorResponse(
            conversation_id=conversation.id,
            message_id=assistant_message.id,
            intent=PracticeTutorIntent(assistant_message.intent),
            mode=PracticeTutorMode(assistant_message.mode),
            answer_markdown=assistant_message.content,
            evidence_refs=evidence_refs,
            created_at=assistant_message.created_at,
        )

    async def _tutor_hint_used(
        self,
        session: AsyncSession,
        session_model: PracticeSessionModel,
        question_id: str,
    ) -> bool:
        conversation = await self._tutor_conversation(
            session,
            session_model,
            question_id,
            create=False,
        )
        if conversation is None:
            return False
        count = await session.scalar(
            select(func.count(ConversationMessageModel.id)).where(
                ConversationMessageModel.conversation_id == conversation.id,
                ConversationMessageModel.course_id == session_model.course_id,
                ConversationMessageModel.user_id == session_model.user_id,
                ConversationMessageModel.role == "assistant",
                ConversationMessageModel.mode == PracticeTutorMode.HINT.value,
            )
        )
        return bool(count)

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
            viewed_hint = request.viewed_hint or await self._tutor_hint_used(
                session,
                session_model,
                question_id,
            )
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
                    or existing_by_key.viewed_hint != viewed_hint
                    or existing_by_key.elapsed_ms != request.elapsed_ms
                ):
                    raise LearningServiceError(
                        LearningServiceErrorCode.ATTEMPT_CONFLICT,
                        "幂等键已绑定到另一份作答请求。",
                    )
                try:
                    existing_question_type = QuestionType(existing_question.question_type)
                    normalized_answer = (
                        normalize_constructed_answer(request.answer)
                        if existing_question_type.is_constructed_response
                        else score_answer(
                            existing_question_type,
                            existing_question.correct_answer,
                            request.answer,
                        ).submitted_answer
                    )
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
            evidence, evidence_material = await self._question_evidence_material(session, question)
            if not evidence:
                raise LearningServiceError(
                    LearningServiceErrorCode.STALE_QUESTION, "题目来源已失效。"
                )
            question_type = QuestionType(question.question_type)
            grading_feedback: str | None = None
            if question_type.is_constructed_response:
                try:
                    constructed_grade = await self._grader.grade(
                        question_type=question_type,
                        prompt=question.prompt,
                        reference_answer=question.correct_answer,
                        solution=question.explanation,
                        submitted_answer=request.answer,
                        evidence_texts=tuple(chunk.text for chunk in evidence_material),
                    )
                except ProviderError as exc:
                    if exc.code is ProviderErrorCode.NOT_CONFIGURED:
                        code = LearningServiceErrorCode.PROVIDER_NOT_CONFIGURED
                        detail = "AI 大题判分 Provider 未配置。"
                    elif exc.code is ProviderErrorCode.TIMEOUT:
                        code = LearningServiceErrorCode.PROVIDER_TIMEOUT
                        detail = "AI 大题判分响应超时, 请保留答案后重试。"
                    else:
                        code = LearningServiceErrorCode.PROVIDER_BAD_RESPONSE
                        detail = "AI 大题判分暂时无法给出可靠结果, 请稍后重试。"
                    raise LearningServiceError(code, detail) from exc
                except ConstructedGradingError as exc:
                    code = (
                        LearningServiceErrorCode.PROVIDER_TIMEOUT
                        if exc.code is ProviderErrorCode.TIMEOUT
                        else LearningServiceErrorCode.PROVIDER_BAD_RESPONSE
                    )
                    detail = (
                        "AI 大题判分响应超时, 请保留答案后重试。"
                        if code is LearningServiceErrorCode.PROVIDER_TIMEOUT
                        else "AI 大题判分暂时无法给出可靠结果, 请稍后重试。"
                    )
                    raise LearningServiceError(code, detail) from exc
                submitted_answer = constructed_grade.submitted_answer
                correct = constructed_grade.correct
                score = constructed_grade.score
                grading_feedback = constructed_grade.feedback
            else:
                try:
                    scored = score_answer(question_type, question.correct_answer, request.answer)
                except (InvalidAnswerError, ValueError) as exc:
                    raise LearningServiceError(
                        LearningServiceErrorCode.INVALID_REQUEST, "答案格式无效。"
                    ) from exc
                submitted_answer = scored.submitted_answer
                correct = scored.correct
                score = scored.score
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
            mastery_result = update_mastery(old_state, correct=correct, viewed_hint=viewed_hint)
            now = _now(self._clock)
            review_at = next_review_at(mastery_result.level, correct=correct, now=now)
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
                answer=submitted_answer,
                score=score,
                correct=correct,
                viewed_hint=viewed_hint,
                elapsed_ms=request.elapsed_ms,
                previous_mastery_level=mastery_result.previous_level.value,
                mastery_level=mastery_result.level.value,
                next_review_at=review_at,
                feedback=mastery_result.reason,
                grading_feedback=grading_feedback,
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
                outcome=AttemptOutcome.CORRECT if correct else AttemptOutcome.INCORRECT,
                score=score,
                explanation=question.explanation,
                grading_feedback=grading_feedback,
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
            evidence_by_unit = await self._current_evidence_for_units(
                session,
                course.user_id,
                course.id,
                tuple(unit.id for _mastery, unit in rows),
            )
            for mastery, unit in rows:
                if _is_legacy_zero_placeholder(unit):
                    continue
                evidence = evidence_by_unit.get(unit.id, ([], []))
                if not evidence[0]:
                    continue
                if not practice_evidence_stats(item.text for item in evidence[1]).is_sufficient:
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
        unit, practice_mode = await self._generation_unit_for_item(
            session,
            managed_batch,
            item,
        )
        unit_id = None if unit is None else unit.id
        evidence = await self._current_evidence(
            session, managed_batch.user_id, managed_batch.course_id, unit_id or ""
        )
        started = _now(self._clock)
        provider_name: str | None = None
        model_name: str | None = None
        retry_item_failure = False
        previous_failure_code = item.failure_code
        try:
            if unit is None or not evidence[0]:
                raise QuestionValidationError("SOURCE_UNAVAILABLE", "来源已失效。")
            assert unit_id is not None
            material_by_key = {
                (material.chunk_id, material.content_sha256): material for material in evidence[1]
            }
            authorized_evidence: list[AuthorizedEvidence] = []
            for ref in evidence[0]:
                material = material_by_key.get((ref.chunk_id, ref.content_sha256))
                if material is None:
                    raise QuestionValidationError("SOURCE_UNAVAILABLE", "来源片段已失效。")
                authorized_evidence.append(
                    AuthorizedEvidence(
                        course_id=batch.course_id,
                        document_id=ref.document_id,
                        revision_id=ref.revision_id,
                        chunk_id=ref.chunk_id,
                        content_sha256=ref.content_sha256,
                        text=material.text,
                        locator=ref.locator,
                        supplement_id=material.supplement_id,
                    )
                )
            question_evidence = select_question_evidence(
                tuple(authorized_evidence), seed=item.ordinal
            )
            if not question_evidence:
                raise QuestionValidationError("SOURCE_UNAVAILABLE", "来源片段已失效。")
            _practice_status, confidence_note = _practice_confidence_for_materials(
                evidence[1],
                practice_mode=practice_mode,
                is_exercise_prototype=is_exercise_prototype_label(unit.label),
            )
            question_type = (
                infer_exercise_question_type(tuple(item.text for item in question_evidence))
                if practice_mode is LearningUnitPracticeMode.EXERCISE_VARIANT
                else (QuestionType.SINGLE_CHOICE if item.ordinal % 2 else QuestionType.TRUE_FALSE)
            )
            avoid_prompts = await self._recent_question_prompts(
                session,
                user_id=managed_batch.user_id,
                course_id=managed_batch.course_id,
                learning_unit_id=unit_id,
            )
            provider_name = "deepseek"
            question, _response_id, model_name = await self._generator.generate(
                question_id=new_id(),
                course_id=managed_batch.course_id,
                learning_unit_id=unit_id,
                unit_label=unit.label,
                question_type=question_type,
                evidence=question_evidence,
                generation_mode=practice_mode,
                avoid_prompts=avoid_prompts,
                attempt_number=item.attempt_count + 1,
                previous_failure_code=previous_failure_code,
                practice_confidence_note=confidence_note,
            )
            if not question_type.is_constructed_response:
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
            supplement_by_key = {
                (
                    selected.document_id,
                    selected.revision_id,
                    selected.chunk_id,
                    selected.content_sha256,
                ): selected.supplement_id
                for selected in question_evidence
            }
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
                        supplement_id=supplement_by_key.get(
                            (
                                ref.document_id,
                                ref.revision_id,
                                ref.chunk_id,
                                ref.content_sha256,
                            )
                        ),
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

    async def _generation_unit_for_item(
        self,
        session: AsyncSession,
        batch: PracticeBatchModel,
        item: PracticeBatchItemModel,
    ) -> tuple[LearningUnitModel | None, LearningUnitPracticeMode]:
        """Resolve a section selection to one exercise prototype when possible."""

        selected_ids = list(batch.learning_unit_ids)
        if not selected_ids:
            return None, LearningUnitPracticeMode.KNOWLEDGE_RECALL
        selected_rows = list(
            await session.scalars(
                select(LearningUnitModel).where(
                    LearningUnitModel.id.in_(selected_ids),
                    LearningUnitModel.user_id == batch.user_id,
                    LearningUnitModel.course_id == batch.course_id,
                )
            )
        )
        selected_by_id = {unit.id: unit for unit in selected_rows}
        if any(unit_id not in selected_by_id for unit_id in selected_ids):
            return None, LearningUnitPracticeMode.KNOWLEDGE_RECALL
        selected_units = [selected_by_id[unit_id] for unit_id in selected_ids]

        child_rows = list(
            await session.scalars(
                select(LearningUnitModel).where(
                    LearningUnitModel.parent_id.in_(selected_ids),
                    LearningUnitModel.user_id == batch.user_id,
                    LearningUnitModel.course_id == batch.course_id,
                    LearningUnitModel.kind == LearningUnitKind.CONCEPT.value,
                    LearningUnitModel.status == LearningUnitStatus.AVAILABLE.value,
                )
            )
        )
        children_by_parent: dict[str, list[LearningUnitModel]] = {}
        for child in child_rows:
            if child.parent_id is not None:
                children_by_parent.setdefault(child.parent_id, []).append(child)

        target_groups: list[list[tuple[LearningUnitModel, LearningUnitPracticeMode]]] = []
        for selected in selected_units:
            children = children_by_parent.get(selected.id, [])
            children.sort(
                key=lambda child: (
                    exercise_prototype_number(child.label) or 1_000_000,
                    child.canonical_key,
                )
            )
            mode = practice_mode_for_unit(
                selected.kind,
                selected.label,
                child_labels=(child.label for child in children),
            )
            prototype_children = [
                child for child in children if is_exercise_prototype_label(child.label)
            ]
            if mode is LearningUnitPracticeMode.EXERCISE_VARIANT and prototype_children:
                target_groups.append(
                    [
                        (child, LearningUnitPracticeMode.EXERCISE_VARIANT)
                        for child in prototype_children
                    ]
                )
                continue
            if mode is LearningUnitPracticeMode.KNOWLEDGE_RECALL and (
                selected.kind == LearningUnitKind.SECTION.value
            ):
                _refs, chunks = await self._current_evidence(
                    session,
                    batch.user_id,
                    batch.course_id,
                    selected.id,
                )
                mode = practice_mode_for_unit(
                    selected.kind,
                    selected.label,
                    child_labels=(child.label for child in children),
                    evidence_texts=(chunk.text for chunk in chunks),
                )
            target_groups.append([(selected, mode)])

        schedule = _generation_target_schedule(
            tuple(len(group) for group in target_groups), batch.total_items
        )
        schedule_index = item.ordinal - 1
        if schedule_index < 0 or schedule_index >= len(schedule):
            return None, LearningUnitPracticeMode.KNOWLEDGE_RECALL
        group_index, candidate_index = schedule[schedule_index]
        return target_groups[group_index][candidate_index]

    async def _recent_question_prompts(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        course_id: str,
        learning_unit_id: str | None,
    ) -> tuple[str, ...]:
        if learning_unit_id is None:
            return ()
        prompts = await session.scalars(
            select(PracticeQuestionModel.prompt)
            .where(
                PracticeQuestionModel.user_id == user_id,
                PracticeQuestionModel.course_id == course_id,
                PracticeQuestionModel.learning_unit_id == learning_unit_id,
                PracticeQuestionModel.status == QuestionStatus.READY.value,
            )
            .order_by(PracticeQuestionModel.created_at.desc())
            .limit(8)
        )
        return tuple(prompts)

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
                document_title=document_title_from_filename(document.filename),
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
        all_rows = list(
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
        visible_rows = [unit for unit in all_rows if not _is_legacy_zero_placeholder(unit)]
        child_labels_by_parent: dict[str, list[str]] = {}
        for child in visible_rows:
            if child.parent_id is not None:
                child_labels_by_parent.setdefault(child.parent_id, []).append(child.label)
        rows = [
            unit
            for unit in visible_rows
            if (
                unit.kind == LearningUnitKind.CONCEPT.value
                or (unit.kind == LearningUnitKind.SECTION.value and unit.parent_id is None)
            )
        ]
        if not rows:
            return []
        user_id = rows[0].user_id
        evidence_by_unit = await self._current_evidence_for_units(
            session,
            user_id,
            course_id,
            tuple(unit.id for unit in rows),
        )
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
            evidence = evidence_by_unit.get(unit.id, ([], []))
            practice_mode = practice_mode_for_unit(
                unit.kind,
                unit.label,
                child_labels=child_labels_by_parent.get(unit.id, ()),
                evidence_texts=(chunk.text for chunk in evidence[1]),
            )
            stats = practice_evidence_stats(item.text for item in evidence[1])
            confidence_note: str | None = None
            if unit.status == LearningUnitStatus.AVAILABLE.value and evidence[0]:
                practice_status, confidence_note = _practice_confidence_for_materials(
                    evidence[1],
                    practice_mode=practice_mode,
                    is_exercise_prototype=(
                        unit.kind == LearningUnitKind.CONCEPT.value
                        and is_exercise_prototype_label(unit.label)
                    ),
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
                practice_confidence_note=confidence_note,
                practice_mode=practice_mode,
                prototype_question_type=(
                    infer_exercise_question_type(tuple(chunk.text for chunk in evidence[1]))
                    if practice_mode is LearningUnitPracticeMode.EXERCISE_VARIANT
                    and unit.kind == LearningUnitKind.CONCEPT.value
                    and is_exercise_prototype_label(unit.label)
                    and evidence[1]
                    else None
                ),
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
    ) -> tuple[list[EvidenceReference], list[_EvidenceMaterial]]:
        evidence_by_unit = await self._current_evidence_for_units(
            session,
            user_id,
            course_id,
            (unit_id,),
        )
        return evidence_by_unit.get(unit_id, ([], []))

    async def _current_evidence_for_units(
        self,
        session: AsyncSession,
        user_id: str,
        course_id: str,
        unit_ids: tuple[str, ...],
    ) -> dict[str, tuple[list[EvidenceReference], list[_EvidenceMaterial]]]:
        """Load all current source rows once, then project descendant scopes in memory."""

        if not unit_ids:
            return {}
        hierarchy_rows = list(
            await session.execute(
                select(LearningUnitModel.id, LearningUnitModel.parent_id).where(
                    LearningUnitModel.user_id == user_id,
                    LearningUnitModel.course_id == course_id,
                )
            )
        )
        children: dict[str, list[str]] = {}
        all_unit_ids: list[str] = []
        for child_id, parent_id in hierarchy_rows:
            all_unit_ids.append(child_id)
            if parent_id is not None:
                children.setdefault(parent_id, []).append(child_id)
        if not all_unit_ids:
            return {unit_id: ([], []) for unit_id in unit_ids}

        rows = list(
            await session.execute(
                select(
                    LearningUnitSourceModel,
                    DocumentModel,
                    RevisionChunkModel,
                    LearningUnitEvidenceSupplementModel,
                )
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
                .outerjoin(
                    LearningUnitEvidenceSupplementModel,
                    and_(
                        LearningUnitEvidenceSupplementModel.source_id == LearningUnitSourceModel.id,
                        LearningUnitEvidenceSupplementModel.course_id
                        == LearningUnitSourceModel.course_id,
                        LearningUnitEvidenceSupplementModel.user_id
                        == LearningUnitSourceModel.user_id,
                        LearningUnitEvidenceSupplementModel.source_content_sha256
                        == LearningUnitSourceModel.content_sha256,
                        LearningUnitEvidenceSupplementModel.status == "active",
                    ),
                )
                .where(
                    LearningUnitSourceModel.user_id == user_id,
                    LearningUnitSourceModel.course_id == course_id,
                    LearningUnitSourceModel.unit_id.in_(all_unit_ids),
                    LearningUnitSourceModel.status == "valid",
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.review_status == "approved",
                    DocumentModel.active_revision_id == LearningUnitSourceModel.revision_id,
                    RevisionChunkModel.content_sha256 == LearningUnitSourceModel.content_sha256,
                )
                .order_by(RevisionChunkModel.ordinal, RevisionChunkModel.id)
            )
        )

        rows_by_unit: dict[
            str,
            list[
                tuple[
                    LearningUnitSourceModel,
                    DocumentModel,
                    RevisionChunkModel,
                    LearningUnitEvidenceSupplementModel | None,
                ]
            ],
        ] = {}
        for source, document, chunk, supplement in rows:
            rows_by_unit.setdefault(source.unit_id, []).append(
                (source, document, chunk, supplement)
            )

        result: dict[str, tuple[list[EvidenceReference], list[_EvidenceMaterial]]] = {}
        for unit_id in unit_ids:
            scope_ids = {unit_id}
            pending = [unit_id]
            while pending:
                parent_id = pending.pop()
                for child_id in children.get(parent_id, []):
                    if child_id not in scope_ids:
                        scope_ids.add(child_id)
                        pending.append(child_id)
            scoped_rows = [
                row for scoped_unit_id in scope_ids for row in rows_by_unit.get(scoped_unit_id, [])
            ]
            scoped_rows.sort(key=lambda row: (row[2].ordinal, row[2].id))
            result[unit_id] = self._evidence_from_rows(scoped_rows)
        return result

    @staticmethod
    def _evidence_from_rows(
        rows: list[
            tuple[
                LearningUnitSourceModel,
                DocumentModel,
                RevisionChunkModel,
                LearningUnitEvidenceSupplementModel | None,
            ]
        ],
    ) -> tuple[list[EvidenceReference], list[_EvidenceMaterial]]:
        refs: list[EvidenceReference] = []
        materials: list[_EvidenceMaterial] = []
        seen_chunks: dict[tuple[str, str, str], int] = {}
        for source, document, chunk, supplement in rows:
            chunk_key = (source.document_id, source.revision_id, source.chunk_id)
            text_value = chunk.text if supplement is None else supplement.text
            content_sha256 = (
                source.content_sha256 if supplement is None else supplement.content_sha256
            )
            material = _EvidenceMaterial(
                source_id=source.id,
                chunk_id=source.chunk_id,
                content_sha256=content_sha256,
                text=text_value,
                supplement_id=None if supplement is None else supplement.id,
                role=None if supplement is None else supplement.role,
            )
            existing_index = seen_chunks.get(chunk_key)
            if existing_index is not None:
                # A child and its parent can point at the same parsed chunk. Prefer a
                # user overlay whenever one is present, otherwise retain the first row.
                if materials[existing_index].supplement_id is None and material.supplement_id:
                    materials[existing_index] = material
                continue
            seen_chunks[chunk_key] = len(materials)
            locator = _locator(source.locator)
            refs.append(
                EvidenceReference(
                    document_id=source.document_id,
                    document_name=document.filename,
                    revision_id=source.revision_id,
                    chunk_id=source.chunk_id,
                    content_sha256=content_sha256,
                    locator=locator,
                    quote=text_value[: min(300, len(text_value))],
                )
            )
            materials.append(material)
        # A later duplicate can replace the material, so rebuild references from the
        # final selected material list to keep quote and hash aligned.
        if len(refs) == len(materials):
            for index, material in enumerate(materials):
                ref = refs[index]
                refs[index] = ref.model_copy(
                    update={
                        "content_sha256": material.content_sha256,
                        "quote": material.text[: min(300, len(material.text))],
                    }
                )
        return refs, materials

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
    ) -> tuple[list[EvidenceReference], list[_EvidenceMaterial]]:
        rows = list(
            await session.execute(
                select(
                    PracticeQuestionEvidenceModel,
                    DocumentModel,
                    RevisionChunkModel,
                    LearningUnitEvidenceSupplementModel,
                )
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
                .outerjoin(
                    LearningUnitEvidenceSupplementModel,
                    and_(
                        LearningUnitEvidenceSupplementModel.id
                        == PracticeQuestionEvidenceModel.supplement_id,
                        LearningUnitEvidenceSupplementModel.course_id
                        == PracticeQuestionEvidenceModel.course_id,
                        LearningUnitEvidenceSupplementModel.user_id
                        == PracticeQuestionEvidenceModel.user_id,
                        LearningUnitEvidenceSupplementModel.source_content_sha256
                        == RevisionChunkModel.content_sha256,
                        LearningUnitEvidenceSupplementModel.status == "active",
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
                    or_(
                        and_(
                            PracticeQuestionEvidenceModel.supplement_id.is_(None),
                            RevisionChunkModel.content_sha256
                            == PracticeQuestionEvidenceModel.content_sha256,
                        ),
                        and_(
                            PracticeQuestionEvidenceModel.supplement_id.is_not(None),
                            LearningUnitEvidenceSupplementModel.id.is_not(None),
                            LearningUnitEvidenceSupplementModel.content_sha256
                            == PracticeQuestionEvidenceModel.content_sha256,
                        ),
                    ),
                )
                .order_by(PracticeQuestionEvidenceModel.ordinal)
            )
        )
        refs: list[EvidenceReference] = []
        materials: list[_EvidenceMaterial] = []
        for evidence, document, chunk, supplement in rows:
            text_value = chunk.text if supplement is None else supplement.text
            if evidence.quote not in text_value:
                continue
            quote = text_value[: min(2_000, len(text_value))].strip()
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
            materials.append(
                _EvidenceMaterial(
                    source_id="",
                    chunk_id=chunk.id,
                    content_sha256=evidence.content_sha256,
                    text=text_value,
                    supplement_id=evidence.supplement_id,
                    role=None if supplement is None else supplement.role,
                )
            )
        return refs, materials

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
        learning_unit_ids = {question.learning_unit_id for _item, question in rows}
        unit_rows = list(
            await session.scalars(
                select(LearningUnitModel).where(
                    LearningUnitModel.user_id == session_model.user_id,
                    LearningUnitModel.course_id == session_model.course_id,
                    LearningUnitModel.id.in_(learning_unit_ids),
                )
            )
        )
        unit_by_id = {unit.id: unit for unit in unit_rows}
        child_labels_by_parent: dict[str, list[str]] = {}
        if unit_rows:
            children = list(
                await session.scalars(
                    select(LearningUnitModel).where(
                        LearningUnitModel.user_id == session_model.user_id,
                        LearningUnitModel.course_id == session_model.course_id,
                        LearningUnitModel.parent_id.in_(unit_by_id),
                    )
                )
            )
            for child in children:
                if child.parent_id is not None:
                    child_labels_by_parent.setdefault(child.parent_id, []).append(child.label)
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
            unit = unit_by_id.get(question.learning_unit_id)
            practice_mode = (
                practice_mode_for_unit(
                    unit.kind,
                    unit.label,
                    child_labels=child_labels_by_parent.get(unit.id, ()),
                    evidence_texts=(ref.quote for ref in evidence),
                )
                if unit is not None
                else LearningUnitPracticeMode.KNOWLEDGE_RECALL
            )
            questions.append(
                PracticeQuestionView(
                    id=question.id,
                    learning_unit_id=question.learning_unit_id,
                    question_type=question.question_type,
                    practice_mode=practice_mode,
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
                    grading_feedback=None if attempt is None else attempt.grading_feedback,
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
            grading_feedback=attempt.grading_feedback,
            evidence_refs=refs,
            mastery=MasteryUpdate(
                learning_unit_id=question.learning_unit_id,
                previous_level=previous,
                level=current_level,
                reason=attempt.feedback,
                next_review_at=attempt.next_review_at,
            ),
        )
