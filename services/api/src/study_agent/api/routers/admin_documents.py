"""Administrator-only document review endpoints."""

from __future__ import annotations

from typing import Annotated, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.api.schemas.admin_documents import (
    AdminDocumentResponse,
    AdminDocumentReviewRequest,
    AdminDocumentsResponse,
)
from study_agent.api.schemas.admin_documents import (
    DocumentReviewStatus as DocumentReviewStatusContract,
)
from study_agent.config import Settings
from study_agent.identity.session import get_session_account
from study_agent.infrastructure.db.session import Database
from study_agent.modules.admin.document_review import (
    AdminDocument,
    AdminDocumentReviewService,
    DocumentReviewError,
    DocumentReviewErrorCode,
    DocumentReviewStatus,
)
from study_agent.modules.auth.service import AccountIdentity
from study_agent.providers.protocols import Clock
from study_agent.storage.local import LocalStorage

router = APIRouter(prefix="/api/v1/admin/documents", tags=["admin-documents"])
SessionAccount = Annotated[AccountIdentity, Depends(get_session_account)]


@router.get("", response_model=AdminDocumentsResponse)
async def list_admin_documents(
    request: Request,
    identity: SessionAccount,
    review_status: DocumentReviewStatusContract | None = None,
) -> AdminDocumentsResponse:
    try:
        documents = await _service(request).list_documents(
            identity,
            None if review_status is None else DocumentReviewStatus(review_status),
        )
    except DocumentReviewError as exc:
        raise _problem(exc) from exc
    return AdminDocumentsResponse(items=[_response(document) for document in documents])


@router.get("/{document_id}/content", response_model=None)
async def get_admin_document_content(
    document_id: str,
    request: Request,
    identity: SessionAccount,
) -> StreamingResponse:
    try:
        content = await _service(request).get_content(identity, document_id)
    except DocumentReviewError as exc:
        raise _problem(exc) from exc

    storage = cast(LocalStorage, request.app.state.storage)
    try:
        await storage.head(content.object_key)
    except FileNotFoundError as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="资料原文件不可用",
        ) from exc
    encoded_filename = quote(content.filename, safe="")
    return StreamingResponse(
        storage.stream_bytes(content.object_key),
        media_type=content.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
            "Content-Length": str(content.size_bytes),
        },
    )


@router.post("/{document_id}/review", response_model=AdminDocumentResponse)
async def review_admin_document(
    document_id: str,
    payload: AdminDocumentReviewRequest,
    request: Request,
    identity: SessionAccount,
) -> AdminDocumentResponse:
    try:
        document = await _service(request).review(
            identity,
            document_id,
            review_status=DocumentReviewStatus(payload.review_status),
            review_note=payload.review_note,
        )
    except DocumentReviewError as exc:
        raise _problem(exc) from exc
    return _response(document)


def _service(request: Request) -> AdminDocumentReviewService:
    return AdminDocumentReviewService(
        cast(Database, request.app.state.database),
        cast(Clock, request.app.state.clock),
        cast(Settings, request.app.state.settings),
    )


def _response(document: AdminDocument) -> AdminDocumentResponse:
    return AdminDocumentResponse(
        id=document.id,
        course_id=document.course_id,
        course_title=document.course_title,
        owner_account_id=document.owner_account_id,
        owner_email=document.owner_email,
        owner_display_name=document.owner_display_name,
        owner_subject=document.owner_subject,
        filename=document.filename,
        media_type=document.media_type,
        size_bytes=document.size_bytes,
        corpus_role=document.corpus_role,
        status=document.status,
        page_count=document.page_count,
        review_status=document.review_status.value,
        review_note=document.review_note,
        reviewed_by_account_id=document.reviewed_by_account_id,
        reviewed_by_email=document.reviewed_by_email,
        reviewed_at=document.reviewed_at,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _problem(exc: DocumentReviewError) -> ApiProblem:
    mapping: dict[DocumentReviewErrorCode, tuple[int, ProblemCode, str]] = {
        DocumentReviewErrorCode.FORBIDDEN: (
            403,
            ProblemCode.AUTH_FORBIDDEN,
            "需要管理员权限",
        ),
        DocumentReviewErrorCode.NOT_FOUND: (
            404,
            ProblemCode.RESOURCE_NOT_FOUND,
            "资料不存在",
        ),
        DocumentReviewErrorCode.CONFLICT: (
            409,
            ProblemCode.STATE_CONFLICT,
            "资料审核状态冲突",
        ),
    }
    status_code, code, title = mapping[exc.code]
    return ApiProblem(
        status=status_code,
        code=code,
        title=title,
        detail=exc.detail,
    )


__all__ = ["router"]
