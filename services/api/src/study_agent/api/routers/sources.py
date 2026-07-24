"""Validated query citation source access."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from pydantic import BaseModel
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
from study_agent.providers.protocols import Clock, ObjectStorage
from study_agent.storage.local import LocalStorage
from study_contracts import BoundingBox, SourceLocator

router = APIRouter(prefix="/api/v1", tags=["sources"])


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
