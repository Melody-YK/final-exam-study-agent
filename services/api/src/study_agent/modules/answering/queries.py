"""Transactional query persistence and trusted-answer orchestration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    AnswerDependencyModel,
    ConversationModel,
    CourseModel,
    DocumentModel,
    QueryRunModel,
    RetrievalSnapshotModel,
    RevisionChunkModel,
    UserModel,
)
from study_agent.infrastructure.db.models.core import new_id
from study_agent.infrastructure.db.session import Database
from study_agent.modules.answering.events import append_query_event
from study_agent.modules.answering.retrieval import QueryEvidence, RetrievedEvidence
from study_agent.modules.answering.service import TrustedAnswerService
from study_agent.modules.answering.types import AnswerExecution, AuthorizedEvidence
from study_agent.observability.trace import get_trace_id, new_trace_id
from study_agent.providers.errors import ProviderError, ProviderErrorCode
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import Clock, ConversationContextTurn
from study_contracts import AnswerStatus, Refusal, StructuredAnswer


@dataclass(frozen=True, slots=True)
class QueryTrace:
    trace_id: str
    retrieval_snapshot_id: str | None
    retrieval_trace_id: str | None


@dataclass(frozen=True, slots=True)
class QuerySnapshot:
    id: str
    course_id: str
    conversation_id: str
    question: str
    status: str
    answer: StructuredAnswer | None
    failure_code: str | None
    usage: dict[str, int]
    trace: QueryTrace
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ConversationSnapshot:
    id: str
    course_id: str
    title: str
    turn_count: int
    latest_query_id: str | None
    latest_question: str | None
    created_at: datetime
    updated_at: datetime


DEFAULT_CONVERSATION_TITLE = "新会话"
AUTO_TITLE_MAX_LENGTH = 60
CONVERSATION_CONTEXT_TURNS = 4
CONVERSATION_CONTEXT_MAX_CHARS = 6_000
CONVERSATION_CONTEXT_QUESTION_MAX_CHARS = 1_000
CONVERSATION_CONTEXT_ANSWER_MAX_CHARS = 1_500


def _question_title(question: str) -> str:
    normalized = " ".join(question.split())
    if len(normalized) <= AUTO_TITLE_MAX_LENGTH:
        return normalized
    return f"{normalized[: AUTO_TITLE_MAX_LENGTH - 1].rstrip()}…"


def _bounded_text(value: str, limit: int) -> str:
    normalized = value.strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip()


def _contextual_retrieval_query(
    question: str,
    context: tuple[ConversationContextTurn, ...],
) -> str:
    normalized = question.strip()
    if not context:
        return normalized
    lines = ["[NON_EVIDENCE_CONVERSATION_CONTEXT]"]
    for turn in context:
        lines.append(f"User: {turn.question}")
    lines.extend(("[CURRENT_QUESTION]", normalized))
    return "\n".join(lines)


def _abstained(query_id: str, code: str, message: str) -> StructuredAnswer:
    return StructuredAnswer(
        query_id=query_id,
        status=AnswerStatus.ABSTAINED,
        answer_markdown="",
        refusal=Refusal(code=code, message=message),
    )


class QueryRepository:
    def __init__(self, database: Database, clock: Clock, *, event_retention: timedelta) -> None:
        self._database = database
        self._clock = clock
        self._event_retention = event_retention

    async def create(
        self,
        principal: Principal,
        course_id: str,
        question: str,
        document_ids: frozenset[str] | None,
        *,
        conversation_id: str | None = None,
    ) -> str:
        normalized = question.strip()
        if not normalized:
            raise ValueError("question must not be blank")
        query_id = new_id()
        async with self._database.session(principal) as session:
            course = await self._owned_course(
                session,
                principal,
                course_id,
                for_update=True,
            )
            if course is None:
                raise LookupError("course is unavailable")
            conversation = await self._resolve_conversation(
                session,
                course,
                conversation_id,
            )
            if document_ids is not None:
                available = set(
                    await session.scalars(
                        select(DocumentModel.id).where(
                            DocumentModel.id.in_(document_ids),
                            DocumentModel.user_id == course.user_id,
                            DocumentModel.course_id == course.id,
                            DocumentModel.deleted_at.is_(None),
                            DocumentModel.corpus_role == "corpus",
                            DocumentModel.status == "ready",
                            DocumentModel.review_status == "approved",
                            DocumentModel.active_revision_id.is_not(None),
                        )
                    )
                )
                if available != set(document_ids):
                    raise LookupError("document scope is unavailable")
            query = QueryRunModel(
                id=query_id,
                user_id=course.user_id,
                course_id=course.id,
                conversation_id=conversation.id,
                question=normalized,
                question_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                requested_document_ids=sorted(document_ids or ()),
                status="pending",
                answer_schema_version="1.0",
                answer_markdown="",
                claims=[],
                citations=[],
                usage={},
                trace_id=get_trace_id() or new_trace_id(),
                event_sequence=0,
            )
            session.add(query)
            now = self._clock.now()
            if conversation.auto_title_pending:
                conversation.title = _question_title(normalized)
                conversation.auto_title_pending = False
            conversation.updated_at = now
            await session.flush()
            append_query_event(
                session,
                query,
                "query.created",
                {"status": "pending", "conversation_id": conversation.id},
                now=now,
                retention=self._event_retention,
            )
        return query_id

    async def start_retrieval(self, principal: Principal, query_id: str) -> None:
        async with self._database.session(principal) as session:
            query = await self._locked(session, principal, query_id)
            query.status = "retrieving"
            append_query_event(
                session,
                query,
                "retrieval.started",
                {"status": "retrieving"},
                now=self._clock.now(),
                retention=self._event_retention,
            )

    async def save_retrieval(
        self,
        principal: Principal,
        query_id: str,
        retrieved: RetrievedEvidence,
    ) -> str:
        snapshot_id = new_id()
        async with self._database.session(principal) as session:
            query = await self._locked(session, principal, query_id)
            session.add(
                RetrievalSnapshotModel(
                    id=snapshot_id,
                    query_id=query.id,
                    user_id=query.user_id,
                    course_id=query.course_id,
                    retrieval_trace_id=retrieved.retrieval_trace_id,
                    active_lexical_index_id=retrieved.active_lexical_index_id,
                    active_revision_ids=sorted(
                        {item.evidence.revision_id for item in retrieved.candidates}
                    ),
                    document_epochs={
                        item.evidence.document_id: item.document_deletion_epoch
                        for item in retrieved.candidates
                    },
                    evidence_payload=[
                        self._evidence_payload(item) for item in retrieved.candidates
                    ],
                    candidate_count=len(retrieved.candidates),
                )
            )
            append_query_event(
                session,
                query,
                "retrieval.completed",
                {
                    "active_index": retrieved.active_index,
                    "candidate_count": len(retrieved.candidates),
                    "retrieval_snapshot_id": snapshot_id,
                    "retrieval_trace_id": retrieved.retrieval_trace_id,
                },
                now=self._clock.now(),
                retention=self._event_retention,
            )
        return snapshot_id

    async def start_generation(self, principal: Principal, query_id: str) -> None:
        async with self._database.session(principal) as session:
            query = await self._locked(session, principal, query_id)
            query.status = "generating"
            append_query_event(
                session,
                query,
                "generation.started",
                {"status": "generating"},
                now=self._clock.now(),
                retention=self._event_retention,
            )

    async def finalize(
        self,
        principal: Principal,
        query_id: str,
        snapshot_id: str | None,
        retrieved: RetrievedEvidence | None,
        execution: AnswerExecution,
    ) -> QuerySnapshot:
        now = self._clock.now()
        async with self._database.session(principal) as session:
            query = await self._locked(session, principal, query_id)
            if query.status == "invalidated":
                return await self._snapshot(session, query)
            resolved = execution
            if execution.answer is not None and execution.answer.status is AnswerStatus.ANSWERED:
                current = await self._sources_current_in_session(
                    session,
                    query,
                    retrieved,
                )
                if not current:
                    resolved = AnswerExecution(
                        answer=_abstained(
                            query.id,
                            "SOURCE_CHANGED",
                            "回答保存前资料版本或删除状态发生变化, 请重试。",
                        ),
                        model=execution.model,
                        provider_response_id=execution.provider_response_id,
                        usage=dict(execution.usage),
                    )
            if resolved.answer is None:
                query.status = "failed"
                query.failure_code = resolved.failure_code
                query.provider_alias = resolved.provider
                query.provider_model = resolved.model
                query.completed_at = now
                append_query_event(
                    session,
                    query,
                    "query.failed",
                    {"status": "failed", "code": resolved.failure_code},
                    now=now,
                    retention=self._event_retention,
                )
            else:
                answer = resolved.answer
                query.status = answer.status.value
                query.answer_markdown = answer.answer_markdown
                query.claims = [claim.model_dump(mode="json") for claim in answer.claims]
                query.citations = [
                    citation.model_dump(mode="json") for citation in answer.citations
                ]
                query.refusal_code = answer.refusal.code if answer.refusal else None
                query.refusal_message = answer.refusal.message if answer.refusal else None
                query.failure_code = None
                query.provider_alias = resolved.provider
                query.provider_model = resolved.model
                query.provider_response_id = resolved.provider_response_id
                query.usage = dict(resolved.usage)
                query.completed_at = now
                if answer.status is AnswerStatus.ANSWERED:
                    if snapshot_id is None or retrieved is None:
                        raise RuntimeError("answered query requires a retrieval snapshot")
                    by_id = {item.evidence.id: item for item in retrieved.candidates}
                    for citation in answer.citations:
                        source = by_id[citation.id]
                        session.add(
                            AnswerDependencyModel(
                                id=new_id(),
                                query_id=query.id,
                                retrieval_snapshot_id=snapshot_id,
                                user_id=query.user_id,
                                course_id=query.course_id,
                                evidence_id=citation.id,
                                document_id=citation.document_id,
                                revision_id=citation.revision_id,
                                chunk_id=citation.chunk_id,
                                document_name=citation.document_name,
                                document_deletion_epoch=source.document_deletion_epoch,
                                content_sha256=source.evidence.content_sha256,
                                locator=citation.locator.model_dump(mode="json"),
                                quote=citation.quote,
                                bounding_boxes=[
                                    box.model_dump(mode="json") for box in citation.bounding_boxes
                                ],
                                provenance=list(source.provenance),
                                available=True,
                            )
                        )
                    append_query_event(
                        session,
                        query,
                        "answer.delta",
                        {"delta": answer.answer_markdown, "aggregated": True},
                        now=now,
                        retention=self._event_retention,
                    )
                append_query_event(
                    session,
                    query,
                    "query.completed",
                    {"status": answer.status.value},
                    now=now,
                    retention=self._event_retention,
                )
            await session.flush()
            return await self._snapshot(session, query)

    async def get(self, principal: Principal, query_id: str) -> QuerySnapshot | None:
        async with self._database.session(principal) as session:
            query = await self._scoped(session, principal, query_id)
            if query is None:
                return None
            return await self._snapshot(session, query)

    async def list_for_course(
        self,
        principal: Principal,
        course_id: str,
        *,
        limit: int,
    ) -> tuple[QuerySnapshot, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        async with self._database.session(principal) as session:
            course = cast(
                CourseModel | None,
                await session.scalar(
                    select(CourseModel)
                    .join(UserModel, UserModel.id == CourseModel.user_id)
                    .where(
                        CourseModel.id == course_id,
                        CourseModel.deleted_at.is_(None),
                        UserModel.subject == principal.subject,
                        UserModel.authentication_method == principal.authentication_method.value,
                    )
                ),
            )
            if course is None:
                raise LookupError("course is unavailable")
            queries = (
                await session.scalars(
                    select(QueryRunModel)
                    .where(
                        QueryRunModel.user_id == course.user_id,
                        QueryRunModel.course_id == course.id,
                    )
                    .order_by(QueryRunModel.created_at.desc(), QueryRunModel.id.desc())
                    .limit(limit)
                )
            ).all()
            if not queries:
                return ()
            retrievals = (
                await session.scalars(
                    select(RetrievalSnapshotModel).where(
                        RetrievalSnapshotModel.query_id.in_(query.id for query in queries)
                    )
                )
            ).all()
            retrieval_by_query = {retrieval.query_id: retrieval for retrieval in retrievals}
            return tuple(
                self._snapshot_from_model(query, retrieval_by_query.get(query.id))
                for query in queries
            )

    async def create_conversation(
        self,
        principal: Principal,
        course_id: str,
        title: str | None,
    ) -> ConversationSnapshot:
        normalized_title = title.strip() if title is not None else DEFAULT_CONVERSATION_TITLE
        if not normalized_title:
            raise ValueError("title must not be blank")
        if len(normalized_title) > 255:
            raise ValueError("title must not exceed 255 characters")
        now = self._clock.now()
        async with self._database.session(principal) as session:
            course = await self._owned_course(session, principal, course_id)
            if course is None:
                raise LookupError("course is unavailable")
            conversation = ConversationModel(
                id=new_id(),
                user_id=course.user_id,
                course_id=course.id,
                title=normalized_title,
                auto_title_pending=title is None,
                created_at=now,
                updated_at=now,
            )
            session.add(conversation)
            await session.flush()
            return self._conversation_snapshot(conversation, 0, None)

    async def list_conversations(
        self,
        principal: Principal,
        course_id: str,
        *,
        limit: int,
    ) -> tuple[ConversationSnapshot, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        async with self._database.session(principal) as session:
            course = await self._owned_course(session, principal, course_id)
            if course is None:
                raise LookupError("course is unavailable")
            conversations = (
                await session.scalars(
                    select(ConversationModel)
                    .where(
                        ConversationModel.user_id == course.user_id,
                        ConversationModel.course_id == course.id,
                    )
                    .order_by(
                        ConversationModel.updated_at.desc(),
                        ConversationModel.id.desc(),
                    )
                    .limit(limit)
                )
            ).all()
            if not conversations:
                return ()
            conversation_ids = [conversation.id for conversation in conversations]
            count_rows = await session.execute(
                select(QueryRunModel.conversation_id, func.count(QueryRunModel.id))
                .where(QueryRunModel.conversation_id.in_(conversation_ids))
                .group_by(QueryRunModel.conversation_id)
            )
            counts = {conversation_id: count for conversation_id, count in count_rows}
            latest_rows = await session.execute(
                select(
                    QueryRunModel.conversation_id,
                    QueryRunModel.id,
                    QueryRunModel.question,
                )
                .where(QueryRunModel.conversation_id.in_(conversation_ids))
                .distinct(QueryRunModel.conversation_id)
                .order_by(
                    QueryRunModel.conversation_id,
                    QueryRunModel.created_at.desc(),
                    QueryRunModel.id.desc(),
                )
            )
            latest = {
                conversation_id: (query_id, question)
                for conversation_id, query_id, question in latest_rows
            }
            return tuple(
                self._conversation_snapshot(
                    conversation,
                    counts.get(conversation.id, 0),
                    latest.get(conversation.id),
                )
                for conversation in conversations
            )

    async def list_for_conversation(
        self,
        principal: Principal,
        conversation_id: str,
        *,
        limit: int,
    ) -> tuple[QuerySnapshot, ...] | None:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        async with self._database.session(principal) as session:
            conversation = await self._scoped_conversation(
                session,
                principal,
                conversation_id,
            )
            if conversation is None:
                return None
            queries = list(
                (
                    await session.scalars(
                        select(QueryRunModel)
                        .where(
                            QueryRunModel.user_id == conversation.user_id,
                            QueryRunModel.course_id == conversation.course_id,
                            QueryRunModel.conversation_id == conversation.id,
                        )
                        .order_by(QueryRunModel.created_at.desc(), QueryRunModel.id.desc())
                        .limit(limit)
                    )
                ).all()
            )
            if not queries:
                return ()
            queries.reverse()
            retrievals = (
                await session.scalars(
                    select(RetrievalSnapshotModel).where(
                        RetrievalSnapshotModel.query_id.in_(query.id for query in queries)
                    )
                )
            ).all()
            retrieval_by_query = {retrieval.query_id: retrieval for retrieval in retrievals}
            return tuple(
                self._snapshot_from_model(query, retrieval_by_query.get(query.id))
                for query in queries
            )

    async def recent_context(
        self,
        principal: Principal,
        query_id: str,
        *,
        max_turns: int = CONVERSATION_CONTEXT_TURNS,
        max_chars: int = CONVERSATION_CONTEXT_MAX_CHARS,
    ) -> tuple[ConversationContextTurn, ...]:
        if max_turns <= 0 or max_chars <= 0:
            raise ValueError("conversation context limits must be positive")
        async with self._database.session(principal) as session:
            current = await self._scoped(session, principal, query_id)
            if current is None:
                raise LookupError("query is unavailable")
            queries = (
                await session.scalars(
                    select(QueryRunModel)
                    .where(
                        QueryRunModel.user_id == current.user_id,
                        QueryRunModel.course_id == current.course_id,
                        QueryRunModel.conversation_id == current.conversation_id,
                        QueryRunModel.id != current.id,
                        or_(
                            QueryRunModel.created_at < current.created_at,
                            and_(
                                QueryRunModel.created_at == current.created_at,
                                QueryRunModel.id < current.id,
                            ),
                        ),
                        QueryRunModel.status.in_(("answered", "abstained", "failed")),
                    )
                    .order_by(QueryRunModel.created_at.desc(), QueryRunModel.id.desc())
                    .limit(max_turns)
                )
            ).all()
            answered_ids = [query.id for query in queries if query.status == "answered"]
            dependencies = (
                (
                    await session.scalars(
                        select(AnswerDependencyModel).where(
                            AnswerDependencyModel.query_id.in_(answered_ids)
                        )
                    )
                ).all()
                if answered_ids
                else []
            )
            document_ids = {dependency.document_id for dependency in dependencies}
            documents = (
                (
                    await session.scalars(
                        select(DocumentModel).where(
                            DocumentModel.id.in_(document_ids),
                            DocumentModel.user_id == current.user_id,
                            DocumentModel.course_id == current.course_id,
                        )
                    )
                ).all()
                if document_ids
                else []
            )
            chunk_keys = {
                (dependency.chunk_id, dependency.revision_id) for dependency in dependencies
            }
            chunks = (
                (
                    await session.scalars(
                        select(RevisionChunkModel).where(
                            RevisionChunkModel.id.in_(key[0] for key in chunk_keys),
                            RevisionChunkModel.revision_id.in_(key[1] for key in chunk_keys),
                        )
                    )
                ).all()
                if chunk_keys
                else []
            )

        dependencies_by_query: dict[str, list[AnswerDependencyModel]] = {}
        for dependency in dependencies:
            dependencies_by_query.setdefault(dependency.query_id, []).append(dependency)
        documents_by_id = {document.id: document for document in documents}
        chunks_by_key = {(chunk.id, chunk.revision_id): chunk for chunk in chunks}

        remaining = max_chars
        turns: list[ConversationContextTurn] = []
        for query in queries:
            question = _bounded_text(
                query.question,
                min(CONVERSATION_CONTEXT_QUESTION_MAX_CHARS, remaining),
            )
            if not question:
                continue
            remaining -= len(question)
            answer: str | None = None
            if query.status == "answered" and remaining > 0:
                query_dependencies = dependencies_by_query.get(query.id, [])
                expected_citations = len(query.citations)
                sources_current = bool(query_dependencies) and (
                    len(query_dependencies) == expected_citations
                    and all(
                        self._context_dependency_is_current(
                            dependency,
                            documents_by_id,
                            chunks_by_key,
                        )
                        for dependency in query_dependencies
                    )
                )
                if sources_current:
                    answer = (
                        _bounded_text(
                            query.answer_markdown,
                            min(CONVERSATION_CONTEXT_ANSWER_MAX_CHARS, remaining),
                        )
                        or None
                    )
                    if answer is not None:
                        remaining -= len(answer)
            turns.append(
                ConversationContextTurn(
                    question=question,
                    answer_markdown=answer,
                )
            )
            if remaining <= 0:
                break
        turns.reverse()
        return tuple(turns)

    @staticmethod
    def _context_dependency_is_current(
        dependency: AnswerDependencyModel,
        documents: dict[str, DocumentModel],
        chunks: dict[tuple[str, str], RevisionChunkModel],
    ) -> bool:
        document = documents.get(dependency.document_id)
        chunk = chunks.get((dependency.chunk_id, dependency.revision_id))
        return bool(
            dependency.available
            and document is not None
            and document.deleted_at is None
            and document.review_status == "approved"
            and document.deletion_epoch == dependency.document_deletion_epoch
            and document.active_revision_id == dependency.revision_id
            and chunk is not None
            and chunk.content_sha256 == dependency.content_sha256
        )

    async def _resolve_conversation(
        self,
        session: AsyncSession,
        course: CourseModel,
        conversation_id: str | None,
    ) -> ConversationModel:
        statement = select(ConversationModel).where(
            ConversationModel.user_id == course.user_id,
            ConversationModel.course_id == course.id,
        )
        if conversation_id is not None:
            statement = statement.where(ConversationModel.id == conversation_id)
        else:
            statement = statement.order_by(
                ConversationModel.updated_at.desc(),
                ConversationModel.id.desc(),
            ).limit(1)
        conversation = cast(
            ConversationModel | None,
            await session.scalar(statement.with_for_update(of=ConversationModel)),
        )
        if conversation is not None:
            return conversation
        if conversation_id is not None:
            raise LookupError("conversation is unavailable")
        now = self._clock.now()
        conversation = ConversationModel(
            id=new_id(),
            user_id=course.user_id,
            course_id=course.id,
            title=DEFAULT_CONVERSATION_TITLE,
            auto_title_pending=True,
            created_at=now,
            updated_at=now,
        )
        session.add(conversation)
        await session.flush()
        return conversation

    @staticmethod
    async def _owned_course(
        session: AsyncSession,
        principal: Principal,
        course_id: str,
        *,
        for_update: bool = False,
    ) -> CourseModel | None:
        statement = (
            select(CourseModel)
            .join(UserModel, UserModel.id == CourseModel.user_id)
            .where(
                CourseModel.id == course_id,
                CourseModel.deleted_at.is_(None),
                UserModel.subject == principal.subject,
                UserModel.authentication_method == principal.authentication_method.value,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=CourseModel)
        return cast(CourseModel | None, await session.scalar(statement))

    @staticmethod
    async def _scoped_conversation(
        session: AsyncSession,
        principal: Principal,
        conversation_id: str,
    ) -> ConversationModel | None:
        return cast(
            ConversationModel | None,
            await session.scalar(
                select(ConversationModel)
                .join(CourseModel, CourseModel.id == ConversationModel.course_id)
                .join(UserModel, UserModel.id == ConversationModel.user_id)
                .where(
                    ConversationModel.id == conversation_id,
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            ),
        )

    @staticmethod
    def _conversation_snapshot(
        conversation: ConversationModel,
        turn_count: int,
        latest: tuple[str, str] | None,
    ) -> ConversationSnapshot:
        return ConversationSnapshot(
            id=conversation.id,
            course_id=conversation.course_id,
            title=conversation.title,
            turn_count=turn_count,
            latest_query_id=latest[0] if latest else None,
            latest_question=latest[1] if latest else None,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )

    async def _scoped(
        self,
        session: AsyncSession,
        principal: Principal,
        query_id: str,
    ) -> QueryRunModel | None:
        return cast(
            QueryRunModel | None,
            await session.scalar(
                select(QueryRunModel)
                .join(CourseModel, CourseModel.id == QueryRunModel.course_id)
                .join(UserModel, UserModel.id == QueryRunModel.user_id)
                .where(
                    QueryRunModel.id == query_id,
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            ),
        )

    async def _locked(
        self,
        session: AsyncSession,
        principal: Principal,
        query_id: str,
    ) -> QueryRunModel:
        query = cast(
            QueryRunModel | None,
            await session.scalar(
                select(QueryRunModel)
                .join(UserModel, UserModel.id == QueryRunModel.user_id)
                .where(
                    QueryRunModel.id == query_id,
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
                .with_for_update(of=QueryRunModel)
            ),
        )
        if query is None:
            raise LookupError("query is unavailable")
        return query

    async def _snapshot(
        self,
        session: AsyncSession,
        query: QueryRunModel,
    ) -> QuerySnapshot:
        retrieval = await session.scalar(
            select(RetrievalSnapshotModel).where(RetrievalSnapshotModel.query_id == query.id)
        )
        return self._snapshot_from_model(query, retrieval)

    @staticmethod
    def _snapshot_from_model(
        query: QueryRunModel,
        retrieval: RetrievalSnapshotModel | None,
    ) -> QuerySnapshot:
        answer: StructuredAnswer | None = None
        if query.status in {AnswerStatus.ANSWERED.value, AnswerStatus.ABSTAINED.value}:
            answer = StructuredAnswer(
                query_id=query.id,
                status=AnswerStatus(query.status),
                answer_markdown=query.answer_markdown,
                claims=query.claims,
                citations=query.citations,
                refusal=(
                    Refusal(code=query.refusal_code, message=query.refusal_message)
                    if query.refusal_code is not None and query.refusal_message is not None
                    else None
                ),
            )
        return QuerySnapshot(
            id=query.id,
            course_id=query.course_id,
            conversation_id=query.conversation_id,
            question=query.question,
            status=query.status,
            answer=answer,
            failure_code=query.failure_code,
            usage=dict(query.usage),
            trace=QueryTrace(
                trace_id=query.trace_id,
                retrieval_snapshot_id=retrieval.id if retrieval else None,
                retrieval_trace_id=retrieval.retrieval_trace_id if retrieval else None,
            ),
            created_at=query.created_at,
            completed_at=query.completed_at,
        )

    async def _sources_current_in_session(
        self,
        session: AsyncSession,
        query: QueryRunModel,
        retrieved: RetrievedEvidence | None,
    ) -> bool:
        if retrieved is None or not retrieved.candidates:
            return False
        active_manifest = await session.scalar(
            select(CourseModel.active_lexical_index_id)
            .where(CourseModel.id == query.course_id, CourseModel.deleted_at.is_(None))
            .with_for_update(of=CourseModel)
        )
        if active_manifest != retrieved.active_lexical_index_id:
            return False
        rows = (
            await session.execute(
                select(
                    RevisionChunkModel.id,
                    RevisionChunkModel.content_sha256,
                    DocumentModel.id.label("document_id"),
                    DocumentModel.active_revision_id,
                    DocumentModel.deletion_epoch,
                )
                .join(
                    DocumentModel,
                    and_(
                        DocumentModel.active_revision_id == RevisionChunkModel.revision_id,
                        DocumentModel.course_id == query.course_id,
                        DocumentModel.user_id == query.user_id,
                    ),
                )
                .where(
                    RevisionChunkModel.id.in_(
                        tuple(item.evidence.chunk_id for item in retrieved.candidates)
                    ),
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.review_status == "approved",
                )
                .with_for_update(of=DocumentModel)
            )
        ).all()
        by_chunk = {str(row.id): row for row in rows}
        for item in retrieved.candidates:
            row = by_chunk.get(item.evidence.chunk_id)
            if row is None:
                return False
            if str(row.document_id) != item.evidence.document_id:
                return False
            if str(row.active_revision_id) != item.evidence.revision_id:
                return False
            if int(row.deletion_epoch) != item.document_deletion_epoch:
                return False
            if str(row.content_sha256) != item.evidence.content_sha256:
                return False
        return True

    @staticmethod
    def _evidence_payload(item: AuthorizedEvidence) -> dict[str, object]:
        return {
            "evidence": item.evidence.model_dump(mode="json"),
            "document_name": item.document_name,
            "score": item.score,
            "document_deletion_epoch": item.document_deletion_epoch,
            "provenance": list(item.provenance),
        }


class QueryService:
    def __init__(
        self,
        repository: QueryRepository,
        evidence: QueryEvidence,
        registry: ProviderRegistry,
        *,
        timeout_seconds: float,
    ) -> None:
        self._repository = repository
        self._evidence = evidence
        self._answering = TrustedAnswerService(
            registry.chat,
            timeout_seconds=timeout_seconds,
        )

    async def execute(
        self,
        principal: Principal,
        course_id: str,
        question: str,
        *,
        document_ids: frozenset[str] | None = None,
        conversation_id: str | None = None,
    ) -> QuerySnapshot:
        query_id = await self._repository.create(
            principal,
            course_id,
            question,
            document_ids,
            conversation_id=conversation_id,
        )
        await self._repository.start_retrieval(principal, query_id)
        conversation_context = await self._repository.recent_context(principal, query_id)
        retrieval_question = _contextual_retrieval_query(question, conversation_context)
        try:
            retrieved = await self._evidence.retrieve(
                principal,
                course_id,
                retrieval_question,
                document_ids=document_ids,
            )
        except ProviderError as exc:
            return await self._repository.finalize(
                principal,
                query_id,
                None,
                None,
                AnswerExecution(
                    answer=None,
                    failure_code=exc.code.value,
                    provider=exc.provider,
                ),
            )
        except LookupError:
            return await self._repository.finalize(
                principal,
                query_id,
                None,
                None,
                AnswerExecution(
                    answer=None,
                    failure_code=ProviderErrorCode.UNAVAILABLE.value,
                ),
            )
        snapshot_id = await self._repository.save_retrieval(principal, query_id, retrieved)
        if retrieved.active_index and retrieved.candidates:
            await self._repository.start_generation(principal, query_id)
        execution = await self._answering.answer(
            query_id=query_id,
            question=question,
            active_index=retrieved.active_index,
            candidates=retrieved.candidates,
            sources_are_current=lambda: self._evidence.sources_are_current(
                principal,
                course_id,
                retrieved.active_lexical_index_id,
                retrieved.candidates,
            ),
            conversation_context=conversation_context,
        )
        return await self._repository.finalize(
            principal,
            query_id,
            snapshot_id,
            retrieved,
            execution,
        )
