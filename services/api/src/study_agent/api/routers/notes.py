"""Evidence-backed note generation and optimistic Markdown editing."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.config import Settings
from study_agent.identity.principal import Principal
from study_agent.identity.session import get_request_principal
from study_agent.infrastructure.db.session import Database
from study_agent.modules.answering.retrieval import QueryEvidence
from study_agent.modules.notes.service import (
    NoteGenerationError,
    NoteRepository,
    NoteService,
    NoteSnapshot,
    NoteSourceSnapshot,
    NoteVersionConflict,
)
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import Clock
from study_contracts import BoundingBox, SourceLocator

router = APIRouter(prefix="/api/v1", tags=["notes"])
_ETAG = re.compile(r'^"([1-9][0-9]*)"$')


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_path: list[str] = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=255)


class NotePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    body_markdown: str | None = Field(default=None, min_length=1, max_length=1_000_000)


class NoteSourceResponse(BaseModel):
    id: str
    evidence_id: str
    document_id: str
    revision_id: str
    chunk_id: str
    document_name: str
    locator: SourceLocator
    quote: str
    bounding_boxes: list[BoundingBox]
    provenance: list[str]
    available: bool
    stale: bool
    unavailable_reason: str | None


class NoteResponse(BaseModel):
    id: str
    course_id: str
    section_path: list[str]
    title: str
    body_markdown: str
    version: int
    generation: int
    generated_by_model: bool
    status: str
    sources: list[NoteSourceResponse]
    created_at: datetime
    updated_at: datetime


async def _principal(request: Request) -> Principal:
    return await get_request_principal(request)


def _repository(request: Request) -> NoteRepository:
    return NoteRepository(cast(Database, request.app.state.database))


def _service(request: Request) -> NoteService:
    settings = cast(Settings, request.app.state.settings)
    return NoteService(
        _repository(request),
        cast(QueryEvidence, request.app.state.query_evidence),
        cast(ProviderRegistry, request.app.state.provider_registry),
        cast(Clock, request.app.state.clock),
        timeout_seconds=settings.provider_timeout_seconds,
    )


def _source_response(source: NoteSourceSnapshot) -> NoteSourceResponse:
    return NoteSourceResponse(
        id=source.id,
        evidence_id=source.evidence_id,
        document_id=source.document_id,
        revision_id=source.revision_id,
        chunk_id=source.chunk_id,
        document_name=source.document_name,
        locator=source.locator,
        quote=source.quote,
        bounding_boxes=list(source.bounding_boxes),
        provenance=list(source.provenance),
        available=source.available,
        stale=source.stale,
        unavailable_reason=source.unavailable_reason,
    )


def _response(snapshot: NoteSnapshot) -> NoteResponse:
    return NoteResponse(
        id=snapshot.id,
        course_id=snapshot.course_id,
        section_path=list(snapshot.section_path),
        title=snapshot.title,
        body_markdown=snapshot.body_markdown,
        version=snapshot.version,
        generation=snapshot.generation,
        generated_by_model=snapshot.generated_by_model,
        status=snapshot.status,
        sources=[_source_response(source) for source in snapshot.sources],
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


def _version(if_match: str | None) -> int:
    if if_match is None:
        raise ApiProblem(
            status=428,
            code=ProblemCode.PRECONDITION_REQUIRED,
            title="需要 If-Match",
        )
    match = _ETAG.fullmatch(if_match.strip())
    if match is None:
        raise ApiProblem(
            status=422,
            code=ProblemCode.INVALID_REQUEST,
            title="If-Match 无效",
        )
    return int(match.group(1))


def _etag(response: Response, version: int) -> None:
    response.headers["ETag"] = f'"{version}"'


def _generation_problem(exc: NoteGenerationError) -> ApiProblem:
    provider_failure = exc.code.startswith("PROVIDER_")
    return ApiProblem(
        status=503 if provider_failure else 409,
        code=(
            ProblemCode.PROVIDER_BAD_RESPONSE if provider_failure else ProblemCode.INDEX_UNAVAILABLE
        ),
        title="笔记生成失败",
        detail=exc.code,
        retryable=provider_failure,
    )


@router.post(
    "/courses/{course_id}/notes",
    response_model=NoteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_note(
    course_id: str,
    payload: NoteCreate,
    request: Request,
    response: Response,
) -> NoteResponse:
    try:
        snapshot = await _service(request).create(
            await _principal(request),
            course_id,
            tuple(item.strip() for item in payload.section_path),
            payload.title,
        )
    except NoteGenerationError as exc:
        raise _generation_problem(exc) from exc
    except LookupError as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="课程不存在",
        ) from exc
    _etag(response, snapshot.version)
    return _response(snapshot)


@router.get("/notes/{note_id}", response_model=NoteResponse)
async def get_note(note_id: str, request: Request, response: Response) -> NoteResponse:
    snapshot = await _repository(request).get(await _principal(request), note_id)
    if snapshot is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="笔记不存在",
        )
    _etag(response, snapshot.version)
    return _response(snapshot)


@router.get("/courses/{course_id}/notes", response_model=list[NoteResponse])
async def list_notes(course_id: str, request: Request) -> list[NoteResponse]:
    try:
        snapshots = await _repository(request).list_for_course(
            await _principal(request),
            course_id,
        )
    except LookupError as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="课程不存在",
        ) from exc
    return [_response(snapshot) for snapshot in snapshots]


@router.patch("/notes/{note_id}", response_model=NoteResponse)
async def update_note(
    note_id: str,
    payload: NotePatch,
    request: Request,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> NoteResponse:
    try:
        snapshot = await _repository(request).update(
            await _principal(request),
            note_id,
            expected_version=_version(if_match),
            title=payload.title,
            body_markdown=payload.body_markdown,
        )
    except NoteVersionConflict as exc:
        raise ApiProblem(
            status=412,
            code=ProblemCode.VERSION_CONFLICT,
            title="笔记版本冲突",
            detail=f"当前版本为 {exc.current_version}",
        ) from exc
    except LookupError as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="笔记不存在",
        ) from exc
    _etag(response, snapshot.version)
    return _response(snapshot)


@router.post("/notes/{note_id}/regenerate", response_model=NoteResponse)
async def regenerate_note(note_id: str, request: Request, response: Response) -> NoteResponse:
    try:
        snapshot = await _service(request).regenerate(await _principal(request), note_id)
    except NoteGenerationError as exc:
        raise _generation_problem(exc) from exc
    except NoteVersionConflict as exc:
        raise ApiProblem(
            status=412,
            code=ProblemCode.VERSION_CONFLICT,
            title="笔记版本冲突",
            detail=f"当前版本为 {exc.current_version}",
        ) from exc
    except LookupError as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="笔记不存在",
        ) from exc
    _etag(response, snapshot.version)
    return _response(snapshot)
