"""Evidence-backed note generation and optimistic Markdown editing."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.config import Settings
from study_agent.identity.principal import Principal
from study_agent.identity.session import get_request_principal
from study_agent.infrastructure.db.session import Database
from study_agent.modules.answering.retrieval import QueryEvidence
from study_agent.modules.notes.service import (
    NoteGenerationError,
    NoteKnowledgePointSnapshot,
    NoteRepository,
    NoteService,
    NoteSnapshot,
    NoteSourceSnapshot,
    NoteVersionConflict,
    NoteVersionNotFound,
    NoteWorkflowRegenerationRequired,
)
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import Clock
from study_contracts import BoundingBox, SourceLocator

router = APIRouter(prefix="/api/v1", tags=["notes"])
_ETAG = re.compile(r'^"([1-9][0-9]*)"$')
_MAX_IMPORTED_MARKDOWN_BYTES = 1_000_000


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_path: list[str] = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=255)


class NotePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    body_markdown: str | None = Field(default=None, min_length=1, max_length=1_000_000)


class NoteImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_path: list[str] = Field(default_factory=lambda: ["未分类"], min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=255)
    body_markdown: str = Field(min_length=1, max_length=1_000_000)

    @field_validator("section_path")
    @classmethod
    def normalize_section_path(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        return normalized or ["未分类"]

    @field_validator("title", "body_markdown")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise ValueError("imported note text must not be blank")
        if len(normalized.encode("utf-8")) > _MAX_IMPORTED_MARKDOWN_BYTES:
            raise ValueError("imported note body must not exceed 1 MB")
        return normalized


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


class NoteKnowledgePointResponse(BaseModel):
    id: str
    text: str
    source_ids: list[str]


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
    origin_batch_id: str | None
    sources: list[NoteSourceResponse]
    knowledge_points: list[NoteKnowledgePointResponse]
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


def _knowledge_point_response(
    point: NoteKnowledgePointSnapshot,
) -> NoteKnowledgePointResponse:
    return NoteKnowledgePointResponse(
        id=point.id,
        text=point.text,
        source_ids=list(point.source_ids),
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
        origin_batch_id=snapshot.origin_batch_id,
        sources=[_source_response(source) for source in snapshot.sources],
        knowledge_points=[_knowledge_point_response(point) for point in snapshot.knowledge_points],
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


def _version(if_match: str) -> int:
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


@router.post(
    "/courses/{course_id}/notes/import",
    response_model=NoteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_note(
    course_id: str,
    payload: NoteImport,
    request: Request,
    response: Response,
) -> NoteResponse:
    try:
        snapshot = await _repository(request).import_note(
            await _principal(request),
            course_id,
            section_path=tuple(payload.section_path),
            title=payload.title,
            body_markdown=payload.body_markdown,
        )
    except LookupError as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="课程不存在",
        ) from exc
    _etag(response, snapshot.version)
    return _response(snapshot)


@router.patch("/notes/{note_id}", response_model=NoteResponse)
async def update_note(
    note_id: str,
    payload: NotePatch,
    request: Request,
    response: Response,
    if_match: Annotated[str, Header(alias="If-Match")],
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
    except NoteVersionNotFound as exc:
        raise ApiProblem(
            status=409,
            code=ProblemCode.NOTE_VERSION_NOT_FOUND,
            title="笔记版本快照缺失",
            detail="当前工作流笔记缺少可用于后续操作的版本快照。",
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
    except NoteWorkflowRegenerationRequired as exc:
        raise ApiProblem(
            status=409,
            code=ProblemCode.STATE_CONFLICT,
            title="工作流笔记需要批次重新生成",
            detail="该笔记由持久批次工作流管理, 请使用批次重新生成。",
        ) from exc
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
