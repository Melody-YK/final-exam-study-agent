"""Validated query citation source access."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from starlette.responses import RedirectResponse, StreamingResponse

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.identity.principal import Principal
from study_agent.identity.session import get_request_principal
from study_agent.infrastructure.db.session import Database
from study_agent.modules.answering.source_tokens import LocalReadTokenSigner
from study_agent.modules.answering.sources import (
    CitationPreviewUnavailable,
    CitationSource,
    CitationSourceService,
)
from study_agent.modules.sources import (
    SourcePreview,
    SourcePreviewService,
    SourcePreviewUnavailable,
    SourcePreviewUnavailableReason,
)
from study_agent.providers.protocols import Clock, ObjectStorage
from study_agent.storage.local import LocalStorage
from study_contracts import BoundingBox, SourceLocator

router = APIRouter(prefix="/api/v1", tags=["sources"])
_NOTE_SOURCE_SCOPE = "note-source"
_GRAPH_SOURCE_SCOPE = "knowledge-graph-source"


class CitationSourceResponse(BaseModel):
    citation_id: str
    document_id: str
    revision_id: str
    chunk_id: str
    document_name: str
    locator: SourceLocator
    quote: str
    bounding_boxes: list[BoundingBox]
    provenance: list[str]
    media_type: str
    read_url: str
    read_url_expires_at: datetime


class SourcePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    document_id: str
    revision_id: str
    chunk_id: str
    document_name: str
    locator: SourceLocator
    section_path: list[str]
    quote: str
    bounding_boxes: list[BoundingBox]
    provenance: list[str]
    media_type: str
    read_url: str
    read_url_expires_at: datetime


async def _principal(request: Request) -> Principal:
    return await get_request_principal(request)


def _response(source: CitationSource, *, read_url: str | None = None) -> CitationSourceResponse:
    return CitationSourceResponse(
        citation_id=source.citation_id,
        document_id=source.document_id,
        revision_id=source.revision_id,
        chunk_id=source.chunk_id,
        document_name=source.document_name,
        locator=source.locator,
        quote=source.quote,
        bounding_boxes=list(source.bounding_boxes),
        provenance=list(source.provenance),
        media_type=source.media_type,
        read_url=read_url or source.read_url,
        read_url_expires_at=source.read_url_expires_at,
    )


def _preview_response(
    source: SourcePreview,
    *,
    read_url: str | None = None,
) -> SourcePreviewResponse:
    return SourcePreviewResponse(
        source_id=source.source_id,
        document_id=source.document_id,
        revision_id=source.revision_id,
        chunk_id=source.chunk_id,
        document_name=source.document_name,
        locator=source.locator,
        section_path=list(source.section_path),
        quote=source.quote,
        bounding_boxes=list(source.bounding_boxes),
        provenance=list(source.provenance),
        media_type=source.media_type,
        read_url=read_url or source.read_url,
        read_url_expires_at=source.read_url_expires_at,
    )


def _local_content_url(
    request: Request,
    *,
    query_id: str,
    citation_id: str,
    expires_at: datetime,
) -> str:
    signer = cast(LocalReadTokenSigner, request.app.state.local_read_signer)
    grant = signer.sign(query_id, citation_id, expires_at=expires_at)
    query = urlencode({"expires": grant.expires, "signature": grant.signature})
    return f"/api/v1/queries/{query_id}/citations/{citation_id}/content?{query}"


def _validate_local_read_grant(
    request: Request,
    *,
    query_id: str,
    citation_id: str,
    expires: str | None,
    signature: str | None,
) -> None:
    try:
        parsed_expires = int(expires) if expires is not None else None
    except ValueError:
        parsed_expires = None
    signer = cast(LocalReadTokenSigner, request.app.state.local_read_signer)
    clock = cast(Clock, request.app.state.clock)
    if (
        parsed_expires is None
        or signature is None
        or not signer.verify(
            query_id,
            citation_id,
            expires=parsed_expires,
            signature=signature,
            now=clock.now(),
        )
    ):
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="来源链接无效或已过期",
        )


def _scoped_local_content_url(
    request: Request,
    *,
    scope: str,
    parent_id: str,
    source_id: str,
    path: str,
    expires_at: datetime,
) -> str:
    signer = cast(LocalReadTokenSigner, request.app.state.local_read_signer)
    grant = signer.sign_scoped(
        scope,
        parent_id,
        source_id,
        expires_at=expires_at,
    )
    query = urlencode({"expires": grant.expires, "signature": grant.signature})
    return f"{path}?{query}"


def _validate_scoped_local_read_grant(
    request: Request,
    *,
    scope: str,
    parent_id: str,
    source_id: str,
    expires: str | None,
    signature: str | None,
) -> None:
    try:
        parsed_expires = int(expires) if expires is not None else None
    except ValueError:
        parsed_expires = None
    signer = cast(LocalReadTokenSigner, request.app.state.local_read_signer)
    clock = cast(Clock, request.app.state.clock)
    if (
        parsed_expires is None
        or signature is None
        or not signer.verify_scoped(
            scope,
            parent_id,
            source_id,
            expires=parsed_expires,
            signature=signature,
            now=clock.now(),
        )
    ):
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="来源链接无效或已过期",
        )


def _preview_service(request: Request, storage: ObjectStorage) -> SourcePreviewService:
    return SourcePreviewService(
        cast(Database, request.app.state.database),
        storage,
    )


def _preview_unavailable(exc: SourcePreviewUnavailable) -> ApiProblem:
    if exc.reason is SourcePreviewUnavailableReason.RENDERED_PAGE_MISSING:
        return ApiProblem(
            status=409,
            code=ProblemCode.INDEX_UNAVAILABLE,
            title="PPTX 预览页不可用",
            detail="旧版 PPTX 如无已生成的预览页, 请先转换为 PDF 后重新上传。",
        )
    if exc.reason is SourcePreviewUnavailableReason.SOURCE_TOO_LARGE:
        return ApiProblem(
            status=409,
            code=ProblemCode.INDEX_UNAVAILABLE,
            title="Markdown 原文过大",
            detail="在线预览仅支持不超过 5 MB 的 Markdown; 请拆分后重新上传。",
        )
    if exc.reason is SourcePreviewUnavailableReason.UNSUPPORTED_MEDIA_TYPE:
        return ApiProblem(
            status=409,
            code=ProblemCode.INDEX_UNAVAILABLE,
            title="当前格式无法在线预览",
            detail="请将原资料转换为 PDF 或 Markdown 后重新上传。",
        )
    return ApiProblem(
        status=409,
        code=ProblemCode.INDEX_UNAVAILABLE,
        title="原始资料不可用",
        detail="原文件可能已丢失或存储暂时不可用, 请重新上传后再试。",
    )


def _graph_grant_source_id(revision_id: str, chunk_id: str) -> str:
    return f"{revision_id}\n{chunk_id}"


@router.get(
    "/queries/{query_id}/citations/{citation_id}",
    response_model=CitationSourceResponse,
)
async def get_citation_source(
    query_id: str,
    citation_id: str,
    request: Request,
) -> CitationSourceResponse:
    storage = cast(ObjectStorage, request.app.state.storage)
    try:
        source = await CitationSourceService(
            cast(Database, request.app.state.database),
            storage,
        ).get(await _principal(request), query_id, citation_id)
    except CitationPreviewUnavailable as exc:
        raise ApiProblem(
            status=409,
            code=ProblemCode.INDEX_UNAVAILABLE,
            title="幻灯片预览资源不可用",
        ) from exc
    if source is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="引用来源不存在或已失效",
        )
    local_url = None
    if isinstance(storage, LocalStorage):
        local_url = _local_content_url(
            request,
            query_id=query_id,
            citation_id=citation_id,
            expires_at=source.read_url_expires_at,
        )
    return _response(source, read_url=local_url)


@router.get(
    "/queries/{query_id}/citations/{citation_id}/content",
    name="get_citation_content",
    response_model=None,
)
async def get_citation_content(
    query_id: str,
    citation_id: str,
    request: Request,
    expires: str | None = None,
    signature: str | None = None,
) -> StreamingResponse | RedirectResponse:
    storage = cast(ObjectStorage, request.app.state.storage)
    if isinstance(storage, LocalStorage):
        _validate_local_read_grant(
            request,
            query_id=query_id,
            citation_id=citation_id,
            expires=expires,
            signature=signature,
        )
    try:
        source = await CitationSourceService(
            cast(Database, request.app.state.database),
            storage,
        ).get(await _principal(request), query_id, citation_id)
    except CitationPreviewUnavailable as exc:
        raise ApiProblem(
            status=409,
            code=ProblemCode.INDEX_UNAVAILABLE,
            title="幻灯片预览资源不可用",
        ) from exc
    if source is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="引用来源不存在或已失效",
        )
    if not isinstance(storage, LocalStorage):
        return RedirectResponse(source.read_url, status_code=307)
    return StreamingResponse(
        storage.stream_bytes(source.object_key),
        media_type=source.media_type,
        headers={"Cache-Control": "private, no-store"},
    )


@router.get(
    "/notes/{note_id}/sources/{source_id}/preview",
    response_model=SourcePreviewResponse,
)
async def get_note_source_preview(
    note_id: str,
    source_id: str,
    request: Request,
) -> SourcePreviewResponse:
    storage = cast(ObjectStorage, request.app.state.storage)
    try:
        source = await _preview_service(request, storage).get_note_source(
            await _principal(request),
            note_id,
            source_id,
        )
    except SourcePreviewUnavailable as exc:
        raise _preview_unavailable(exc) from exc
    if source is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="笔记来源不存在或已失效",
        )
    local_url = None
    if isinstance(storage, LocalStorage):
        local_url = _scoped_local_content_url(
            request,
            scope=_NOTE_SOURCE_SCOPE,
            parent_id=note_id,
            source_id=source_id,
            path=f"/api/v1/notes/{note_id}/sources/{source_id}/preview/content",
            expires_at=source.read_url_expires_at,
        )
    return _preview_response(source, read_url=local_url)


@router.get(
    "/notes/{note_id}/sources/{source_id}/preview/content",
    name="get_note_source_preview_content",
    response_model=None,
)
async def get_note_source_preview_content(
    note_id: str,
    source_id: str,
    request: Request,
    expires: str | None = None,
    signature: str | None = None,
) -> StreamingResponse | RedirectResponse:
    storage = cast(ObjectStorage, request.app.state.storage)
    if isinstance(storage, LocalStorage):
        _validate_scoped_local_read_grant(
            request,
            scope=_NOTE_SOURCE_SCOPE,
            parent_id=note_id,
            source_id=source_id,
            expires=expires,
            signature=signature,
        )
    try:
        source = await _preview_service(request, storage).get_note_source(
            await _principal(request),
            note_id,
            source_id,
        )
    except SourcePreviewUnavailable as exc:
        raise _preview_unavailable(exc) from exc
    if source is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="笔记来源不存在或已失效",
        )
    if not isinstance(storage, LocalStorage):
        return RedirectResponse(source.read_url, status_code=307)
    return StreamingResponse(
        storage.stream_bytes(source.object_key),
        media_type=source.media_type,
        headers={"Cache-Control": "private, no-store"},
    )


@router.get(
    "/courses/{course_id}/knowledge-graph/sources/{revision_id}/{chunk_id}/preview",
    response_model=SourcePreviewResponse,
)
async def get_graph_source_preview(
    course_id: str,
    revision_id: str,
    chunk_id: str,
    request: Request,
) -> SourcePreviewResponse:
    storage = cast(ObjectStorage, request.app.state.storage)
    try:
        source = await _preview_service(request, storage).get_graph_source(
            await _principal(request),
            course_id,
            revision_id,
            chunk_id,
        )
    except SourcePreviewUnavailable as exc:
        raise _preview_unavailable(exc) from exc
    if source is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="知识图谱来源不存在或已失效",
        )
    local_url = None
    if isinstance(storage, LocalStorage):
        local_url = _scoped_local_content_url(
            request,
            scope=_GRAPH_SOURCE_SCOPE,
            parent_id=course_id,
            source_id=_graph_grant_source_id(revision_id, chunk_id),
            path=(
                f"/api/v1/courses/{course_id}/knowledge-graph/sources/"
                f"{revision_id}/{chunk_id}/preview/content"
            ),
            expires_at=source.read_url_expires_at,
        )
    return _preview_response(source, read_url=local_url)


@router.get(
    "/courses/{course_id}/knowledge-graph/sources/{revision_id}/{chunk_id}/preview/content",
    name="get_graph_source_preview_content",
    response_model=None,
)
async def get_graph_source_preview_content(
    course_id: str,
    revision_id: str,
    chunk_id: str,
    request: Request,
    expires: str | None = None,
    signature: str | None = None,
) -> StreamingResponse | RedirectResponse:
    storage = cast(ObjectStorage, request.app.state.storage)
    if isinstance(storage, LocalStorage):
        _validate_scoped_local_read_grant(
            request,
            scope=_GRAPH_SOURCE_SCOPE,
            parent_id=course_id,
            source_id=_graph_grant_source_id(revision_id, chunk_id),
            expires=expires,
            signature=signature,
        )
    try:
        source = await _preview_service(request, storage).get_graph_source(
            await _principal(request),
            course_id,
            revision_id,
            chunk_id,
        )
    except SourcePreviewUnavailable as exc:
        raise _preview_unavailable(exc) from exc
    if source is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="知识图谱来源不存在或已失效",
        )
    if not isinstance(storage, LocalStorage):
        return RedirectResponse(source.read_url, status_code=307)
    return StreamingResponse(
        storage.stream_bytes(source.object_key),
        media_type=source.media_type,
        headers={"Cache-Control": "private, no-store"},
    )
