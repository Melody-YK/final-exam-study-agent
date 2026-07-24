from datetime import datetime, timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, ConfigDict, Field

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.config import Settings
from study_agent.identity.principal import CourseScope, Principal
from study_agent.identity.session import get_request_principal
from study_agent.infrastructure.db.session import Database
from study_agent.modules.courses.documents import (
    DeletionSnapshot,
    Document,
    DocumentReviewStatus,
    DocumentService,
    UploadReceipt,
)
from study_agent.modules.courses.manifest import CorpusRole
from study_agent.modules.courses.repository import Course, CourseRepository
from study_agent.modules.courses.upload_validation import (
    UploadRejected,
    UploadRejectionReason,
    UploadValidator,
)
from study_agent.modules.deletion.cleanup import DeletionCleanupService
from study_agent.providers.protocols import Clock
from study_agent.storage.local import LocalStorage

router = APIRouter(prefix="/api/v1", tags=["courses"])


class CourseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=255)


class CourseResponse(BaseModel):
    id: str
    title: str
    lifecycle: str


class DocumentResponse(BaseModel):
    id: str
    course_id: str
    filename: str
    media_type: str
    corpus_role: CorpusRole
    verified_sha256: str
    status: str
    review_status: DocumentReviewStatus
    preview_revision_id: str | None
    active_revision_id: str | None
    deletion_epoch: int
    indexable: bool


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=1024)
    media_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(min_length=64, max_length=64)
    corpus_role: CorpusRole


class UploadSessionResponse(BaseModel):
    id: str
    url: str
    expires_at: datetime


class DocumentUploadCreated(BaseModel):
    document: DocumentResponse
    upload: UploadSessionResponse


class UploadReceiptResponse(BaseModel):
    upload_session_id: str
    status: str
    size_bytes: int
    sha256: str


class UploadCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_session_id: str = Field(min_length=1, max_length=255)


class DeletionAccepted(BaseModel):
    deletion_id: str
    status: str = "pending"


class DeletionResponse(BaseModel):
    id: str
    target_type: str
    target_id: str
    deletion_epoch: int
    status: str
    attempt_count: int
    completed_at: datetime | None


async def _principal(request: Request) -> Principal:
    return await get_request_principal(request)


def _database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


def _storage(request: Request) -> LocalStorage:
    return cast(LocalStorage, request.app.state.storage)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _clock(request: Request) -> Clock:
    return cast(Clock, request.app.state.clock)


def _document_service(request: Request) -> DocumentService:
    settings = _settings(request)
    return DocumentService(
        _database(request),
        _storage(request),
        clock=_clock(request),
        job_max_attempts=settings.job_max_attempts,
        job_event_retention=timedelta(seconds=settings.job_event_retention_seconds),
    )


def _course_response(course: Course) -> CourseResponse:
    return CourseResponse(id=course.id, title=course.title, lifecycle=course.lifecycle)


def _document_response(document: Document) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        course_id=document.course_id,
        filename=document.filename,
        media_type=document.media_type,
        corpus_role=document.corpus_role,
        verified_sha256=document.verified_sha256,
        status=document.status,
        review_status=document.review_status,
        preview_revision_id=document.preview_revision_id,
        active_revision_id=document.active_revision_id,
        deletion_epoch=document.deletion_epoch,
        indexable=document.indexable,
    )


def _upload_receipt_response(receipt: UploadReceipt) -> UploadReceiptResponse:
    return UploadReceiptResponse(
        upload_session_id=receipt.upload_session_id,
        status=receipt.status,
        size_bytes=receipt.size_bytes,
        sha256=receipt.sha256,
    )


def _deletion_response(snapshot: DeletionSnapshot) -> DeletionResponse:
    return DeletionResponse(
        id=snapshot.id,
        target_type=snapshot.target_type,
        target_id=snapshot.target_id,
        deletion_epoch=snapshot.deletion_epoch,
        status=snapshot.status,
        attempt_count=snapshot.attempt_count,
        completed_at=snapshot.completed_at,
    )


def _upload_problem(exc: UploadRejected) -> ApiProblem:
    if exc.reason is UploadRejectionReason.TOO_LARGE:
        return ApiProblem(
            status=413,
            code=ProblemCode.FILE_TOO_LARGE,
            title="文件超过上传限制",
            detail=str(exc),
        )
    if exc.reason is UploadRejectionReason.HASH_MISMATCH:
        return ApiProblem(
            status=409,
            code=ProblemCode.HASH_MISMATCH,
            title="文件哈希不匹配",
            detail=str(exc),
        )
    if exc.reason is UploadRejectionReason.SIZE_MISMATCH:
        return ApiProblem(
            status=409,
            code=ProblemCode.STATE_CONFLICT,
            title="文件大小不匹配",
            detail=str(exc),
        )
    if exc.reason is UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE:
        return ApiProblem(
            status=415,
            code=ProblemCode.UNSUPPORTED_MEDIA_TYPE,
            title="文件类型不受支持",
            detail=str(exc),
        )
    return ApiProblem(
        status=422,
        code=ProblemCode.INVALID_REQUEST,
        title="上传声明无效",
        detail=str(exc),
    )


@router.post("/courses", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
async def create_course(payload: CourseCreate, request: Request) -> CourseResponse:
    course = await CourseRepository(_database(request)).create(
        await _principal(request), payload.title
    )
    return _course_response(course)


@router.get("/courses", response_model=list[CourseResponse])
async def list_courses(request: Request) -> list[CourseResponse]:
    courses = await CourseRepository(_database(request)).list_for_principal(
        await _principal(request)
    )
    return [_course_response(course) for course in courses]


@router.get("/courses/{course_id}", response_model=CourseResponse)
async def get_course(course_id: str, request: Request) -> CourseResponse:
    principal = await _principal(request)
    course = await CourseRepository(_database(request)).get(
        CourseScope(principal=principal, course_id=course_id)
    )
    if course is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="课程不存在",
        )
    return _course_response(course)


@router.post(
    "/courses/{course_id}/documents",
    response_model=DocumentUploadCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_upload(
    course_id: str,
    payload: DocumentCreate,
    request: Request,
) -> DocumentUploadCreated:
    settings = _settings(request)
    try:
        validated = UploadValidator(settings.max_upload_bytes).validate_declaration(
            payload.filename,
            payload.media_type,
            payload.size_bytes,
            payload.sha256,
        )
    except UploadRejected as exc:
        raise _upload_problem(exc) from exc

    principal = await _principal(request)
    upload_session = await _document_service(request).create_upload(
        CourseScope(principal=principal, course_id=course_id),
        validated,
        payload.corpus_role,
    )
    return DocumentUploadCreated(
        document=_document_response(upload_session.document),
        upload=UploadSessionResponse(
            id=upload_session.id,
            url=f"/api/v1/uploads/{upload_session.id}",
            expires_at=upload_session.expires_at,
        ),
    )


@router.put("/uploads/{upload_session_id}", response_model=UploadReceiptResponse)
async def put_upload(upload_session_id: str, request: Request) -> UploadReceiptResponse:
    receipt = await _document_service(request).upload_stream(
        await _principal(request),
        upload_session_id,
        request.stream(),
        request.headers.get("content-type", ""),
    )
    return _upload_receipt_response(receipt)


@router.post(
    "/documents/{document_id}/upload:complete",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def complete_document_upload(
    document_id: str,
    payload: UploadCompleteRequest,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ],
) -> DocumentResponse:
    try:
        document = await _document_service(request).complete_upload(
            await _principal(request),
            document_id,
            payload.upload_session_id,
            idempotency_key,
            UploadValidator(_settings(request).max_upload_bytes),
        )
    except UploadRejected as exc:
        raise _upload_problem(exc) from exc
    return _document_response(document)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: str, request: Request) -> DocumentResponse:
    document = await _document_service(request).get(await _principal(request), document_id)
    if document is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="资料不存在",
        )
    return _document_response(document)


@router.delete(
    "/documents/{document_id}",
    response_model=DeletionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_document(
    document_id: str,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ],
) -> DeletionAccepted:
    principal = await _principal(request)
    deletion_id = await _document_service(request).delete(principal, document_id, idempotency_key)
    await DeletionCleanupService(
        _database(request),
        lexical_root=_settings(request).lexical_index_root,
        storage=_storage(request),
    ).cleanup(principal, deletion_id)
    return DeletionAccepted(deletion_id=deletion_id)


@router.get("/deletions/{deletion_id}", response_model=DeletionResponse)
async def get_deletion(deletion_id: str, request: Request) -> DeletionResponse:
    deletion = await _document_service(request).get_deletion(await _principal(request), deletion_id)
    if deletion is None:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="删除任务不存在",
        )
    return _deletion_response(deletion)
