"""Read-only workspace projections for the student Web application."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Annotated, Literal, Self, cast

from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.config import Settings
from study_agent.identity.principal import Principal, PrincipalProvider
from study_agent.infrastructure.db.models import (
    AnswerDependencyModel,
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    EmbeddingModelModel,
    LexicalManifestModel,
    ParseJobModel,
    QueryRunModel,
    RetrievalSnapshotModel,
    RetrievalTraceModel,
    RevisionPageModel,
    StoredObjectModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.courses.manifest import CorpusRole, ManifestPolicy
from study_agent.modules.idempotency import IdempotencyService
from study_agent.modules.jobs.presence import WorkerPresenceRegistry
from study_agent.modules.jobs.service import enqueue_parse_job
from study_agent.providers.protocols import Clock
from study_contracts import JobStatus

router = APIRouter(prefix="/api/v1", tags=["workspace"])


class WorkspaceDocumentResponse(BaseModel):
    id: str
    course_id: str
    filename: str
    media_type: str
    corpus_role: CorpusRole
    verified_sha256: str
    status: str
    preview_revision_id: str | None
    active_revision_id: str | None
    deletion_epoch: int
    indexable: bool
    page_count: int | None
    parse_job_id: str | None
    progress: dict[str, object]
    failed_pages: list[int]
    updated_at: datetime
    error_code: str | None


class CapabilityResponse(BaseModel):
    status: Literal["available", "not_configured", "unavailable", "worker_required"]
    label: str
    error_code: str | None = None


class RuntimeCapabilitiesResponse(BaseModel):
    provider: CapabilityResponse
    embedding: CapabilityResponse
    native_parser: CapabilityResponse
    ocr_parser: CapabilityResponse
    demo_lab_enabled: bool


class ParseRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failed_pages: list[int] | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def failed_pages_must_be_ordered(self) -> Self:
        if self.failed_pages is not None and (
            self.failed_pages != sorted(set(self.failed_pages))
            or any(page < 1 for page in self.failed_pages)
        ):
            raise ValueError("failed_pages must be ordered unique positive ordinals")
        return self


class LabCandidateResponse(BaseModel):
    chunk_id: str
    route: Literal["dense", "lexical", "rrf", "rerank"]
    rank: int
    score: float


class LabUsageResponse(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None


class LabTraceResponse(BaseModel):
    trace_id: str
    mode: str
    revision_id: str | None = None
    parser_backend: str | None = None
    tokenizer_version: str | None = None
    embedding_model: str | None = None
    candidates: list[LabCandidateResponse]
    citation_validation: str | None = None
    refusal_reason: str | None = None
    timings_ms: dict[str, float]
    usage: LabUsageResponse | None = None


def _principal(request: Request) -> Principal:
    if request.client is None:
        raise ApiProblem(status=401, code=ProblemCode.AUTH_REQUIRED, title="需要身份验证")
    try:
        provider = cast(PrincipalProvider, request.app.state.principal_provider)
        return provider.resolve(request.client.host)
    except PermissionError as exc:
        raise ApiProblem(
            status=401,
            code=ProblemCode.AUTH_REQUIRED,
            title="需要身份验证",
        ) from exc


def _database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _clock(request: Request) -> Clock:
    return cast(Clock, request.app.state.clock)


def _worker_presence(request: Request) -> WorkerPresenceRegistry:
    return cast(WorkerPresenceRegistry, request.app.state.worker_presence)


@router.get("/capabilities", response_model=RuntimeCapabilitiesResponse)
async def get_runtime_capabilities(request: Request) -> RuntimeCapabilitiesResponse:
    settings = _settings(request)
    availability = await _worker_presence(request).availability(
        now=_clock(request).now(),
        max_age=timedelta(seconds=settings.worker_presence_ttl_seconds),
    )
    chat_status: Literal["available", "not_configured"] = (
        "available" if settings.chat_configured else "not_configured"
    )
    embedding_status: Literal["available", "not_configured"] = (
        "available" if settings.embedding_configured else "not_configured"
    )
    return RuntimeCapabilitiesResponse(
        provider=CapabilityResponse(
            status=chat_status,
            label="已配置, 等待请求时验证" if settings.chat_configured else "未配置回答模型",
            error_code=None if settings.chat_configured else ProblemCode.PROVIDER_NOT_CONFIGURED,
        ),
        embedding=CapabilityResponse(
            status=embedding_status,
            label=(
                "已配置, 等待请求时验证" if settings.embedding_configured else "未配置 Embedding"
            ),
            error_code=(
                None if settings.embedding_configured else ProblemCode.PROVIDER_NOT_CONFIGURED
            ),
        ),
        native_parser=CapabilityResponse(
            status="available" if availability.native_parser else "worker_required",
            label=(
                "PDF / PPTX 原生解析 Worker 在线"
                if availability.native_parser
                else "需要已验证的本地原生解析 Worker"
            ),
            error_code=None if availability.native_parser else "NATIVE_WORKER_REQUIRED",
        ),
        ocr_parser=CapabilityResponse(
            status="available" if availability.ocr_parser else "worker_required",
            label=(
                "本地 OCR Worker 在线" if availability.ocr_parser else "需要已验证的本地 OCR Worker"
            ),
            error_code=None if availability.ocr_parser else "OCR_WORKER_REQUIRED",
        ),
        demo_lab_enabled=settings.demo_lab_enabled,
    )


@router.get(
    "/courses/{course_id}/documents",
    response_model=list[WorkspaceDocumentResponse],
)
async def list_course_documents(
    course_id: str,
    request: Request,
) -> list[WorkspaceDocumentResponse]:
    principal = _principal(request)
    database = _database(request)
    revision = aliased(DocumentRevisionModel)
    latest_job_id = (
        select(ParseJobModel.id)
        .where(ParseJobModel.document_id == DocumentModel.id)
        .order_by(ParseJobModel.created_at.desc(), ParseJobModel.id.desc())
        .limit(1)
        .correlate(DocumentModel)
        .scalar_subquery()
    )
    selected_revision_id = func.coalesce(
        DocumentModel.active_revision_id,
        DocumentModel.preview_revision_id,
    )
    statement = (
        select(DocumentModel, revision.total_page_count, ParseJobModel)
        .join(CourseModel, CourseModel.id == DocumentModel.course_id)
        .join(UserModel, UserModel.id == CourseModel.user_id)
        .outerjoin(revision, revision.id == selected_revision_id)
        .outerjoin(ParseJobModel, ParseJobModel.id == latest_job_id)
        .where(
            CourseModel.id == course_id,
            CourseModel.deleted_at.is_(None),
            DocumentModel.deleted_at.is_(None),
            UserModel.subject == principal.subject,
            UserModel.authentication_method == principal.authentication_method.value,
        )
        .order_by(DocumentModel.updated_at.desc(), DocumentModel.id)
    )
    async with database.session(principal) as session:
        course_exists = await session.scalar(
            select(CourseModel.id)
            .join(UserModel, UserModel.id == CourseModel.user_id)
            .where(
                CourseModel.id == course_id,
                CourseModel.deleted_at.is_(None),
                UserModel.subject == principal.subject,
                UserModel.authentication_method == principal.authentication_method.value,
            )
        )
        if course_exists is None:
            raise ApiProblem(
                status=404,
                code=ProblemCode.RESOURCE_NOT_FOUND,
                title="课程不存在",
            )
        rows = (await session.execute(statement)).all()
    return [
        WorkspaceDocumentResponse(
            id=document.id,
            course_id=document.course_id,
            filename=document.filename,
            media_type=document.media_type,
            corpus_role=CorpusRole(document.corpus_role),
            verified_sha256=document.verified_sha256,
            status=document.status,
            preview_revision_id=document.preview_revision_id,
            active_revision_id=document.active_revision_id,
            deletion_epoch=document.deletion_epoch,
            indexable=ManifestPolicy.is_indexable(CorpusRole(document.corpus_role)),
            page_count=page_count,
            parse_job_id=None if job is None else job.id,
            progress={} if job is None else dict(job.progress),
            failed_pages=([] if job is None else list(job.failed_pages or job.requested_pages)),
            updated_at=document.updated_at,
            error_code=None if job is None else job.failure_code,
        )
        for document, page_count, job in rows
    ]


@router.post(
    "/documents/{document_id}/parse-jobs",
    response_model=WorkspaceDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_document_parse(
    document_id: str,
    payload: ParseRetryRequest,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ],
) -> WorkspaceDocumentResponse:
    principal = _principal(request)
    database = _database(request)
    settings = _settings(request)
    idempotency = IdempotencyService()
    operation = f"retry-document-parse:{document_id}"
    request_hash = idempotency.request_hash(payload.model_dump(mode="json"))
    async with database.session(principal) as session:
        await idempotency.lock(
            session,
            principal,
            operation=operation,
            key=idempotency_key,
        )
        replay = await idempotency.replay_or_none(
            session,
            principal,
            operation=operation,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            still_visible = await session.scalar(
                select(DocumentModel.id)
                .join(CourseModel, CourseModel.id == DocumentModel.course_id)
                .join(UserModel, UserModel.id == CourseModel.user_id)
                .where(
                    DocumentModel.id == document_id,
                    DocumentModel.deleted_at.is_(None),
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            )
            if still_visible is None:
                raise ApiProblem(
                    status=404,
                    code=ProblemCode.RESOURCE_NOT_FOUND,
                    title="资料不存在",
                )
            return WorkspaceDocumentResponse.model_validate(replay.response_body)

        document = await session.scalar(
            select(DocumentModel)
            .join(CourseModel, CourseModel.id == DocumentModel.course_id)
            .join(UserModel, UserModel.id == CourseModel.user_id)
            .where(
                DocumentModel.id == document_id,
                DocumentModel.deleted_at.is_(None),
                CourseModel.deleted_at.is_(None),
                UserModel.subject == principal.subject,
                UserModel.authentication_method == principal.authentication_method.value,
            )
            .with_for_update(of=DocumentModel)
        )
        if document is None:
            raise ApiProblem(
                status=404,
                code=ProblemCode.RESOURCE_NOT_FOUND,
                title="资料不存在",
            )
        latest_job = await session.scalar(
            select(ParseJobModel)
            .where(ParseJobModel.document_id == document.id)
            .order_by(ParseJobModel.created_at.desc(), ParseJobModel.id.desc())
            .limit(1)
            .with_for_update(of=ParseJobModel)
        )
        terminal_statuses = {
            JobStatus.SUCCEEDED.value,
            JobStatus.PARTIAL_FAILED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
        }
        if latest_job is not None and latest_job.status not in terminal_statuses:
            raise ApiProblem(
                status=409,
                code=ProblemCode.STATE_CONFLICT,
                title="资料已有进行中的解析任务",
            )
        stored_object = await session.get(StoredObjectModel, document.stored_object_id)
        if stored_object is None or stored_object.deleted_at is not None:
            raise ApiProblem(
                status=409,
                code=ProblemCode.RESOURCE_DELETING,
                title="资料原文件不可用",
            )

        if payload.failed_pages:
            revision_id = document.preview_revision_id or document.active_revision_id
            revision = (
                None
                if revision_id is None
                else await session.get(DocumentRevisionModel, revision_id)
            )
            if revision is not None and payload.failed_pages[-1] > revision.total_page_count:
                raise ApiProblem(
                    status=422,
                    code=ProblemCode.INVALID_REQUEST,
                    title="失败页超出资料范围",
                )
        try:
            async with session.begin_nested():
                job = await enqueue_parse_job(
                    session,
                    document,
                    stored_object,
                    now=_clock(request).now(),
                    max_attempts=settings.job_max_attempts,
                    event_retention=timedelta(seconds=settings.job_event_retention_seconds),
                )
                if payload.failed_pages:
                    job.requested_pages = list(payload.failed_pages)
                if latest_job is not None and latest_job.parser_profile == "ocr-v1":
                    job.parser_profile = "ocr-v1"
                    job.requires_ocr = True
                await session.flush()
        except IntegrityError as exc:
            raise ApiProblem(
                status=409,
                code=ProblemCode.STATE_CONFLICT,
                title="资料已有进行中的解析任务",
            ) from exc
        await session.refresh(document)
        response = WorkspaceDocumentResponse(
            id=document.id,
            course_id=document.course_id,
            filename=document.filename,
            media_type=document.media_type,
            corpus_role=CorpusRole(document.corpus_role),
            verified_sha256=document.verified_sha256,
            status=document.status,
            preview_revision_id=document.preview_revision_id,
            active_revision_id=document.active_revision_id,
            deletion_epoch=document.deletion_epoch,
            indexable=ManifestPolicy.is_indexable(CorpusRole(document.corpus_role)),
            page_count=None,
            parse_job_id=job.id,
            progress=dict(job.progress),
            failed_pages=list(job.requested_pages),
            updated_at=document.updated_at,
            error_code=None,
        )
        idempotency.store(
            session,
            principal,
            operation=operation,
            key=idempotency_key,
            request_hash=request_hash,
            response_status=status.HTTP_202_ACCEPTED,
            response_body=response.model_dump(mode="json"),
        )
        return response


def _candidate_rows(
    values: list[dict[str, object]],
    route: Literal["dense", "lexical", "rrf", "rerank"],
) -> list[LabCandidateResponse]:
    candidates: list[LabCandidateResponse] = []
    for fallback_rank, value in enumerate(values[:20], start=1):
        raw_chunk_id = str(value.get("chunk_id", ""))
        if not raw_chunk_id:
            continue
        raw_score = value.get("score", 0.0)
        raw_rank = value.get("rank", fallback_rank)
        candidates.append(
            LabCandidateResponse(
                chunk_id=hashlib.sha256(raw_chunk_id.encode("utf-8")).hexdigest()[:12],
                route=route,
                rank=int(raw_rank) if isinstance(raw_rank, int) else fallback_rank,
                score=float(raw_score) if isinstance(raw_score, int | float) else 0.0,
            )
        )
    return candidates


def _single_revision_id(snapshot: RetrievalSnapshotModel | None) -> str | None:
    if snapshot is None or len(snapshot.active_revision_ids) != 1:
        return None
    revision_id = snapshot.active_revision_ids[0]
    return revision_id if isinstance(revision_id, str) and revision_id else None


def _lab_usage(query: QueryRunModel | None) -> LabUsageResponse | None:
    if query is None:
        return None
    input_tokens = query.usage.get("input_tokens")
    output_tokens = query.usage.get("output_tokens")
    return LabUsageResponse(
        input_tokens=(
            input_tokens
            if isinstance(input_tokens, int) and not isinstance(input_tokens, bool)
            else None
        ),
        output_tokens=(
            output_tokens
            if isinstance(output_tokens, int) and not isinstance(output_tokens, bool)
            else None
        ),
        estimated_cost=(
            query.cost_microusd / 1_000_000 if query.cost_microusd is not None else None
        ),
    )


def _citation_validation(
    query: QueryRunModel | None,
    dependencies: list[AnswerDependencyModel],
) -> str | None:
    if query is None or query.status not in {"answered", "invalidated"}:
        return None
    citation_ids = {
        citation_id
        for citation in query.citations
        if isinstance(citation, dict)
        and isinstance((citation_id := citation.get("id")), str)
        and citation_id
    }
    dependency_ids = {dependency.evidence_id for dependency in dependencies}
    if not citation_ids or citation_ids != dependency_ids:
        return None
    if any(not dependency.available for dependency in dependencies):
        return "invalidated"
    return "passed"


@router.get("/courses/{course_id}/lab/trace", response_model=LabTraceResponse)
async def get_latest_lab_trace(course_id: str, request: Request) -> LabTraceResponse:
    if not _settings(request).demo_lab_enabled:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="工程 Lab 未启用",
        )
    principal = _principal(request)
    database = _database(request)
    async with database.session(principal) as session:
        row = (
            await session.execute(
                select(RetrievalTraceModel, EmbeddingModelModel.model_name)
                .outerjoin(
                    EmbeddingModelModel,
                    EmbeddingModelModel.id == RetrievalTraceModel.embedding_model_id,
                )
                .join(CourseModel, CourseModel.id == RetrievalTraceModel.course_id)
                .join(UserModel, UserModel.id == CourseModel.user_id)
                .where(
                    CourseModel.id == course_id,
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
                .order_by(RetrievalTraceModel.created_at.desc(), RetrievalTraceModel.id.desc())
                .limit(1)
            )
        ).first()
        if row is None:
            raise ApiProblem(
                status=404,
                code=ProblemCode.INDEX_UNAVAILABLE,
                title="暂无检索 Trace",
            )
        trace, embedding_model_name = row
        snapshot = await session.scalar(
            select(RetrievalSnapshotModel)
            .where(
                RetrievalSnapshotModel.retrieval_trace_id == trace.id,
                RetrievalSnapshotModel.course_id == course_id,
                RetrievalSnapshotModel.user_id == trace.user_id,
            )
            .order_by(
                RetrievalSnapshotModel.created_at.desc(),
                RetrievalSnapshotModel.id.desc(),
            )
            .limit(1)
        )
        query = (
            await session.scalar(
                select(QueryRunModel).where(
                    QueryRunModel.id == snapshot.query_id,
                    QueryRunModel.course_id == course_id,
                    QueryRunModel.user_id == trace.user_id,
                )
            )
            if snapshot is not None
            else None
        )
        dependencies = (
            list(
                await session.scalars(
                    select(AnswerDependencyModel).where(
                        AnswerDependencyModel.query_id == query.id,
                        AnswerDependencyModel.course_id == course_id,
                        AnswerDependencyModel.user_id == trace.user_id,
                    )
                )
            )
            if query is not None
            else []
        )
        lexical_manifest = (
            await session.scalar(
                select(LexicalManifestModel).where(
                    LexicalManifestModel.id == trace.lexical_manifest_id,
                    LexicalManifestModel.course_id == course_id,
                    LexicalManifestModel.user_id == trace.user_id,
                )
            )
            if trace.lexical_manifest_id is not None
            else None
        )
        snapshot_revision_id = _single_revision_id(snapshot)
        revision_id = (
            await session.scalar(
                select(DocumentRevisionModel.id)
                .join(DocumentModel, DocumentModel.id == DocumentRevisionModel.document_id)
                .where(
                    DocumentRevisionModel.id == snapshot_revision_id,
                    DocumentModel.course_id == course_id,
                    DocumentModel.user_id == trace.user_id,
                    DocumentModel.deleted_at.is_(None),
                )
            )
            if snapshot_revision_id is not None
            else None
        )
        parser_backends = (
            list(
                await session.scalars(
                    select(RevisionPageModel.source_backend)
                    .join(
                        DocumentRevisionModel,
                        DocumentRevisionModel.id == RevisionPageModel.revision_id,
                    )
                    .join(DocumentModel, DocumentModel.id == DocumentRevisionModel.document_id)
                    .where(RevisionPageModel.revision_id == revision_id)
                    .where(
                        DocumentModel.course_id == course_id,
                        DocumentModel.user_id == trace.user_id,
                        DocumentModel.deleted_at.is_(None),
                    )
                    .distinct()
                    .order_by(RevisionPageModel.source_backend)
                )
            )
            if revision_id is not None
            else []
        )
        parser_backend = parser_backends[0] if len(parser_backends) == 1 else None
    candidates = [
        *_candidate_rows(trace.dense_candidates, "dense"),
        *_candidate_rows(trace.lexical_candidates, "lexical"),
        *_candidate_rows(trace.fused_candidates, "rrf"),
        *_candidate_rows(trace.rerank_candidates, "rerank"),
    ]
    return LabTraceResponse(
        trace_id=trace.id,
        mode=trace.mode,
        revision_id=revision_id,
        parser_backend=parser_backend,
        tokenizer_version=(
            lexical_manifest.tokenizer_version if lexical_manifest is not None else None
        ),
        embedding_model=embedding_model_name,
        candidates=candidates,
        citation_validation=_citation_validation(query, dependencies),
        refusal_reason=query.refusal_code if query is not None else None,
        timings_ms={key: float(value) for key, value in trace.timings_ms.items()},
        usage=_lab_usage(query),
    )
