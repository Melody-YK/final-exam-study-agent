"""User-managed course-scoped learner memories."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.identity.principal import Principal
from study_agent.identity.session import get_request_principal
from study_agent.infrastructure.db.session import Database
from study_agent.modules.answering.memory import (
    LearnerMemoryRepository,
    LearnerMemorySnapshot,
    LearnerMemoryType,
)
from study_agent.providers.protocols import Clock

router = APIRouter(prefix="/api/v1", tags=["learner-memories"])


class LearnerMemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_type: LearnerMemoryType
    content: str = Field(min_length=1, max_length=1_000)


class LearnerMemoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_type: LearnerMemoryType
    content: str = Field(min_length=1, max_length=1_000)


class LearnerMemoryResponse(BaseModel):
    id: str
    course_id: str
    memory_type: LearnerMemoryType
    content: str
    confidence: float
    source_kind: str
    last_confirmed_at: datetime
    created_at: datetime
    updated_at: datetime


async def _principal(request: Request) -> Principal:
    return await get_request_principal(request)


def _repository(request: Request) -> LearnerMemoryRepository:
    return LearnerMemoryRepository(
        cast(Database, request.app.state.database),
        cast(Clock, request.app.state.clock),
    )


def _response(snapshot: LearnerMemorySnapshot) -> LearnerMemoryResponse:
    return LearnerMemoryResponse(
        id=snapshot.id,
        course_id=snapshot.course_id,
        memory_type=snapshot.memory_type,
        content=snapshot.content,
        confidence=snapshot.confidence,
        source_kind=snapshot.source_kind,
        last_confirmed_at=snapshot.last_confirmed_at,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


@router.get(
    "/courses/{course_id}/learner-memories",
    response_model=list[LearnerMemoryResponse],
)
async def list_learner_memories(
    course_id: str,
    request: Request,
) -> list[LearnerMemoryResponse]:
    try:
        memories = await _repository(request).list(await _principal(request), course_id)
    except LookupError as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="课程不存在",
        ) from exc
    return [_response(memory) for memory in memories]


@router.post(
    "/courses/{course_id}/learner-memories",
    response_model=LearnerMemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_learner_memory(
    course_id: str,
    payload: LearnerMemoryCreate,
    request: Request,
) -> LearnerMemoryResponse:
    try:
        memory = await _repository(request).create(
            await _principal(request),
            course_id,
            payload.memory_type,
            payload.content,
        )
    except ValueError as exc:
        raise ApiProblem(
            status=422,
            code=ProblemCode.INVALID_REQUEST,
            title="学习记忆无效",
        ) from exc
    except LookupError as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="课程不存在",
        ) from exc
    return _response(memory)


@router.put(
    "/learner-memories/{memory_id}",
    response_model=LearnerMemoryResponse,
)
async def update_learner_memory(
    memory_id: str,
    payload: LearnerMemoryPatch,
    request: Request,
) -> LearnerMemoryResponse:
    try:
        memory = await _repository(request).update(
            await _principal(request),
            memory_id,
            memory_type=payload.memory_type,
            content=payload.content,
        )
    except ValueError as exc:
        raise ApiProblem(
            status=422,
            code=ProblemCode.INVALID_REQUEST,
            title="学习记忆无效或重复",
        ) from exc
    if memory is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="学习记忆不存在",
        )
    return _response(memory)


@router.delete(
    "/learner-memories/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_learner_memory(memory_id: str, request: Request) -> Response:
    deleted = await _repository(request).delete(await _principal(request), memory_id)
    if not deleted:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="学习记忆不存在",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
