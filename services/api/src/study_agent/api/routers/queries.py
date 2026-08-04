"""Trusted query creation, snapshots, and recoverable SSE."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import StreamingResponse

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.config import Settings
from study_agent.identity.principal import Principal
from study_agent.identity.session import get_request_principal
from study_agent.infrastructure.db.session import Database
from study_agent.modules.answering.events import QueryEventReader
from study_agent.modules.answering.memory import LearnerMemoryRepository
from study_agent.modules.answering.queries import (
    ConversationSnapshot,
    QueryRepository,
    QueryService,
    QuerySnapshot,
    QueryTrace,
)
from study_agent.modules.answering.retrieval import QueryEvidence
from study_agent.modules.answering.types import ConceptEvidenceAnchor, ConceptEvidenceContext
from study_agent.modules.jobs.waiter import ClaimWaiter
from study_agent.observability.trace import new_trace_id
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import Clock
from study_contracts import JobEventEnvelope, StructuredAnswer

router = APIRouter(prefix="/api/v1", tags=["queries"])


class QueryConceptAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=36)
    revision_id: str = Field(min_length=1, max_length=36)
    chunk_id: str = Field(min_length=1, max_length=255)


class QueryConceptContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=1024)
    anchors: list[QueryConceptAnchor] = Field(min_length=1, max_length=4)


class QueryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=8_000)
    document_ids: list[str] | None = Field(default=None, max_length=100)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=36)
    concept_context: QueryConceptContext | None = None


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)


class ConversationResponse(BaseModel):
    id: str
    course_id: str
    title: str
    turn_count: int
    latest_query_id: str | None
    latest_question: str | None
    created_at: datetime
    updated_at: datetime


class QueryTraceResponse(BaseModel):
    trace_id: str
    retrieval_snapshot_id: str | None
    retrieval_trace_id: str | None


class RetrievalRoundResponse(BaseModel):
    query: str
    active_index: bool
    candidate_count: int
    eligible_count: int
    retrieval_trace_id: str | None
    active_lexical_index_id: str | None


class QueryResponse(BaseModel):
    id: str
    course_id: str
    conversation_id: str
    question: str
    status: str
    answer: StructuredAnswer | None
    failure_code: str | None
    usage: dict[str, int]
    query_intent: str | None
    standalone_question: str | None
    retrieval_rounds: list[RetrievalRoundResponse]
    retrieval_diagnostic: str | None
    trace: QueryTraceResponse
    created_at: datetime
    completed_at: datetime | None


async def _principal(request: Request) -> Principal:
    return await get_request_principal(request)


def _repository(request: Request) -> QueryRepository:
    settings = cast(Settings, request.app.state.settings)
    return QueryRepository(
        cast(Database, request.app.state.database),
        cast(Clock, request.app.state.clock),
        event_retention=timedelta(seconds=settings.job_event_retention_seconds),
    )


def _service(request: Request) -> QueryService:
    settings = cast(Settings, request.app.state.settings)
    return QueryService(
        _repository(request),
        cast(QueryEvidence, request.app.state.query_evidence),
        cast(ProviderRegistry, request.app.state.provider_registry),
        LearnerMemoryRepository(
            cast(Database, request.app.state.database),
            cast(Clock, request.app.state.clock),
        ),
        timeout_seconds=settings.provider_timeout_seconds,
    )


def _response(snapshot: QuerySnapshot) -> QueryResponse:
    trace: QueryTrace = snapshot.trace
    return QueryResponse(
        id=snapshot.id,
        course_id=snapshot.course_id,
        conversation_id=snapshot.conversation_id,
        question=snapshot.question,
        status=snapshot.status,
        answer=snapshot.answer,
        failure_code=snapshot.failure_code,
        usage=snapshot.usage,
        query_intent=snapshot.query_intent,
        standalone_question=snapshot.standalone_question,
        retrieval_rounds=[
            RetrievalRoundResponse(
                query=round_.query,
                active_index=round_.active_index,
                candidate_count=round_.candidate_count,
                eligible_count=round_.eligible_count,
                retrieval_trace_id=round_.retrieval_trace_id,
                active_lexical_index_id=round_.active_lexical_index_id,
            )
            for round_ in snapshot.retrieval_rounds
        ],
        retrieval_diagnostic=snapshot.retrieval_diagnostic,
        trace=QueryTraceResponse(
            trace_id=trace.trace_id,
            retrieval_snapshot_id=trace.retrieval_snapshot_id,
            retrieval_trace_id=trace.retrieval_trace_id,
        ),
        created_at=snapshot.created_at,
        completed_at=snapshot.completed_at,
    )


def _conversation_response(snapshot: ConversationSnapshot) -> ConversationResponse:
    return ConversationResponse(
        id=snapshot.id,
        course_id=snapshot.course_id,
        title=snapshot.title,
        turn_count=snapshot.turn_count,
        latest_query_id=snapshot.latest_query_id,
        latest_question=snapshot.latest_question,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


@router.post(
    "/courses/{course_id}/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    course_id: str,
    payload: ConversationCreate,
    request: Request,
) -> ConversationResponse:
    try:
        conversation = await _repository(request).create_conversation(
            await _principal(request),
            course_id,
            payload.title,
        )
    except ValueError as exc:
        raise ApiProblem(
            status=422,
            code=ProblemCode.INVALID_REQUEST,
            title="会话标题无效",
        ) from exc
    except LookupError as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="课程不存在",
        ) from exc
    return _conversation_response(conversation)


@router.get(
    "/courses/{course_id}/conversations",
    response_model=list[ConversationResponse],
)
async def list_conversations(
    course_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ConversationResponse]:
    try:
        conversations = await _repository(request).list_conversations(
            await _principal(request),
            course_id,
            limit=limit,
        )
    except LookupError as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="课程不存在",
        ) from exc
    return [_conversation_response(conversation) for conversation in conversations]


@router.get(
    "/conversations/{conversation_id}/queries",
    response_model=list[QueryResponse],
)
async def list_conversation_queries(
    conversation_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[QueryResponse]:
    snapshots = await _repository(request).list_for_conversation(
        await _principal(request),
        conversation_id,
        limit=limit,
    )
    if snapshots is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="会话不存在",
        )
    return [_response(snapshot) for snapshot in snapshots]


@router.post(
    "/courses/{course_id}/queries",
    response_model=QueryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_query(
    course_id: str,
    payload: QueryCreate,
    request: Request,
) -> QueryResponse:
    document_ids = None
    if payload.document_ids is not None:
        if len(payload.document_ids) != len(set(payload.document_ids)):
            raise ApiProblem(
                status=422,
                code=ProblemCode.INVALID_REQUEST,
                title="document_ids 不能重复",
            )
        document_ids = frozenset(payload.document_ids)
    concept_context = None
    if payload.concept_context is not None:
        chunk_ids = [anchor.chunk_id for anchor in payload.concept_context.anchors]
        normalized_label = payload.concept_context.label.strip()
        blank_anchor = any(
            not anchor.document_id.strip()
            or not anchor.revision_id.strip()
            or not anchor.chunk_id.strip()
            for anchor in payload.concept_context.anchors
        )
        if not normalized_label or blank_anchor or len(chunk_ids) != len(set(chunk_ids)):
            raise ApiProblem(
                status=422,
                code=ProblemCode.INVALID_REQUEST,
                title="concept_context 无效",
            )
        concept_context = ConceptEvidenceContext(
            label=normalized_label,
            anchors=tuple(
                ConceptEvidenceAnchor(
                    document_id=anchor.document_id,
                    revision_id=anchor.revision_id,
                    chunk_id=anchor.chunk_id,
                )
                for anchor in payload.concept_context.anchors
            ),
        )
    try:
        snapshot = await _service(request).execute(
            await _principal(request),
            course_id,
            payload.question,
            document_ids=document_ids,
            conversation_id=payload.conversation_id,
            concept_context=concept_context,
        )
    except LookupError as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="课程或资料不存在",
        ) from exc
    return _response(snapshot)


@router.get("/courses/{course_id}/queries", response_model=list[QueryResponse])
async def list_queries(
    course_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[QueryResponse]:
    try:
        snapshots = await _repository(request).list_for_course(
            await _principal(request),
            course_id,
            limit=limit,
        )
    except LookupError as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="课程不存在",
        ) from exc
    return [_response(snapshot) for snapshot in snapshots]


@router.get("/queries/{query_id}", response_model=QueryResponse)
async def get_query(query_id: str, request: Request) -> QueryResponse:
    snapshot = await _repository(request).get(await _principal(request), query_id)
    if snapshot is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="问答不存在",
        )
    return _response(snapshot)


@router.get("/queries/{query_id}/events")
async def stream_query_events(
    query_id: str,
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    once: Annotated[bool, Query()] = False,
) -> StreamingResponse:
    try:
        after_sequence = 0 if last_event_id is None else int(last_event_id)
    except ValueError as exc:
        raise ApiProblem(
            status=422,
            code=ProblemCode.INVALID_REQUEST,
            title="Last-Event-ID 无效",
        ) from exc
    if after_sequence < 0:
        raise ApiProblem(
            status=422,
            code=ProblemCode.INVALID_REQUEST,
            title="Last-Event-ID 无效",
        )
    principal = await _principal(request)
    reader = QueryEventReader(
        cast(Database, request.app.state.database),
        cast(Clock, request.app.state.clock),
    )
    initial = await reader.events_after(principal, query_id, after_sequence)
    if initial is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="问答不存在",
        )
    heartbeat_seconds = cast(Settings, request.app.state.settings).sse_heartbeat_seconds

    async def generate() -> AsyncIterator[str]:
        nonlocal after_sequence
        events = initial
        while True:
            for event in events:
                after_sequence = event.sequence
                yield (
                    f"id: {event.sequence}\n"
                    f"event: {event.event_type}\n"
                    f"data: {event.model_dump_json()}\n\n"
                )
            yield ": heartbeat\n\n"
            if once or await request.is_disconnected():
                return
            await cast(ClaimWaiter, request.app.state.claim_waiter).wait(heartbeat_seconds)
            try:
                next_events = await reader.events_after(principal, query_id, after_sequence)
            except ApiProblem as exc:
                if exc.code is not ProblemCode.EVENT_HISTORY_EXPIRED:
                    raise
                reset = JobEventEnvelope(
                    sequence=max(1, after_sequence),
                    occurred_at=cast(Clock, request.app.state.clock).now(),
                    trace_id=new_trace_id(),
                    event_type="stream.reset",
                    data={
                        "code": ProblemCode.EVENT_HISTORY_EXPIRED.value,
                        "action": "read_snapshot",
                    },
                )
                yield f"event: stream.reset\ndata: {reset.model_dump_json()}\n\n"
                return
            if next_events is None:
                return
            events = next_events

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
