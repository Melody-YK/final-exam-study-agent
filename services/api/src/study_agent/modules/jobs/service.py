"""Persistent parse job orchestration and Worker lease enforcement."""

from __future__ import annotations

from collections.abc import AsyncIterable
from datetime import datetime, timedelta
from typing import Protocol, cast

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    CourseModel,
    DocumentModel,
    JobArtifactModel,
    PageCheckpointModel,
    ParseJobModel,
    StoredObjectModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.ingestion.revisions import RevisionService
from study_agent.modules.jobs.events import append_job_event
from study_agent.modules.jobs.idempotency import WorkerIdempotency
from study_agent.modules.jobs.job_logging import log_job_event
from study_agent.modules.jobs.lease import (
    LeasePolicy,
    hash_lease_token,
    lease_token_matches,
    new_lease_token,
)
from study_agent.modules.jobs.repository import JobRepository
from study_agent.modules.jobs.state_machine import transition
from study_agent.observability.trace import new_trace_id
from study_agent.providers.protocols import Clock, ObjectMetadata, ObjectScope, UploadTarget
from study_agent.storage.local import StorageUploadTooLarge
from study_contracts import (
    JobArtifactReceipt,
    JobClaimRequest,
    JobCompleteRequest,
    JobFailRequest,
    JobHeartbeatRequest,
    JobSnapshot,
    JobStartRequest,
    JobStatus,
    LeaseCommand,
    PageCheckpointRequest,
    WorkerLease,
)


class JobStorage(Protocol):
    async def create_upload(self, scope: ObjectScope) -> UploadTarget: ...

    async def put_stream(
        self,
        object_key: str,
        chunks: AsyncIterable[bytes],
        content_type: str,
        *,
        max_bytes: int | None = None,
    ) -> ObjectMetadata: ...

    async def head(self, object_key: str) -> ObjectMetadata: ...

    async def read_bytes(self, object_key: str) -> bytes: ...

    async def delete(self, object_key: str) -> None: ...


def snapshot_from_model(job: ParseJobModel) -> JobSnapshot:
    return JobSnapshot(
        id=job.id,
        document_id=job.document_id,
        course_id=job.course_id,
        status=JobStatus(job.status),
        state_version=job.state_version,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        lease_version=job.lease_version,
        lease_expires_at=job.lease_expires_at,
        parser_profile=job.parser_profile,
        parser_schema_version="1.0",
        progress=job.progress,
        failure_code=job.failure_code,
        retryable=job.retryable,
        available_at=job.available_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


async def enqueue_parse_job(
    session: AsyncSession,
    document: DocumentModel,
    stored_object: StoredObjectModel,
    *,
    now: datetime,
    max_attempts: int,
    event_retention: timedelta,
) -> ParseJobModel:
    is_image = document.media_type.startswith("image/")
    job = ParseJobModel(
        id=new_trace_id(),
        user_id=document.user_id,
        course_id=document.course_id,
        document_id=document.id,
        stored_object_id=stored_object.id,
        job_type="parse",
        status=JobStatus.QUEUED.value,
        state_version=1,
        attempt=0,
        max_attempts=max_attempts,
        parser_profile="ocr-v1" if is_image else "native-v1",
        parser_schema_version="1.0",
        media_type=document.media_type,
        document_sha256=document.verified_sha256,
        document_deletion_epoch=document.deletion_epoch,
        input_size_bytes=stored_object.size_bytes,
        requires_ocr=is_image,
        requires_rendering=False,
        requested_pages=[],
        available_at=now,
        progress={},
        failed_pages=[],
        event_sequence=0,
    )
    session.add(job)
    await session.flush()
    document.status = "queued"
    append_job_event(
        session,
        job,
        "job.queued",
        {"status": JobStatus.QUEUED.value},
        now=now,
        retention=event_retention,
    )
    log_job_event(
        "job.queued",
        job_id=job.id,
        course_id=job.course_id,
        document_id=job.document_id,
        state=job.status,
        attempt=job.attempt,
    )
    return job


async def cancel_document_jobs(
    session: AsyncSession,
    document: DocumentModel,
    *,
    now: datetime,
    event_retention: timedelta,
) -> None:
    jobs = (
        await session.scalars(
            select(ParseJobModel)
            .where(
                ParseJobModel.document_id == document.id,
                ParseJobModel.status.in_(
                    tuple(
                        status.value
                        for status in JobStatus
                        if status
                        not in {
                            JobStatus.SUCCEEDED,
                            JobStatus.PARTIAL_FAILED,
                            JobStatus.FAILED,
                            JobStatus.CANCELLED,
                        }
                    )
                ),
            )
            .with_for_update(of=ParseJobModel)
        )
    ).all()
    for job in jobs:
        transition(JobStatus(job.status), JobStatus.CANCELLED)
        job.status = JobStatus.CANCELLED.value
        job.state_version += 1
        job.lease_version += 1
        job.lease_owner_id = None
        job.lease_token_hash = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.retryable = False
        append_job_event(
            session,
            job,
            "job.cancelled",
            {"reason": "document_deleted", "deletion_epoch": document.deletion_epoch},
            now=now,
            retention=event_retention,
        )
        log_job_event(
            "job.cancelled",
            job_id=job.id,
            course_id=job.course_id,
            document_id=job.document_id,
            state=job.status,
            attempt=job.attempt,
            lease_version=job.lease_version,
        )


class JobService:
    def __init__(
        self,
        database: Database,
        storage: JobStorage,
        clock: Clock,
        *,
        lease_policy: LeasePolicy,
        event_retention: timedelta,
    ) -> None:
        self._database = database
        self._storage = storage
        self._clock = clock
        self._lease_policy = lease_policy
        self._event_retention = event_retention
        self._repository = JobRepository()
        self._idempotency = WorkerIdempotency()
        self._revisions = RevisionService(storage)

    async def claim(self, request: JobClaimRequest) -> WorkerLease | None:
        now = self._clock.now()
        async with self._database.worker_session(request.worker_id) as session:
            while True:
                job = await self._repository.claim_candidate(session, request.capabilities, now)
                if job is None:
                    return None

                lease_requested_pages = await self._repository.remaining_requested_pages(
                    session, job
                )

                status = JobStatus(job.status)
                if status in {JobStatus.LEASED, JobStatus.PARSING, JobStatus.RETRY_WAIT}:
                    if job.attempt >= job.max_attempts:
                        job.status = JobStatus.FAILED.value
                        job.state_version += 1
                        job.failure_code = "MAX_ATTEMPTS_EXCEEDED"
                        job.retryable = False
                        self._clear_lease(job)
                        document = await session.get(DocumentModel, job.document_id)
                        if document is not None and document.deleted_at is None:
                            document.status = "failed"
                        append_job_event(
                            session,
                            job,
                            "job.failed",
                            {"code": "MAX_ATTEMPTS_EXCEEDED"},
                            now=now,
                            retention=self._event_retention,
                        )
                        log_job_event(
                            "job.failed",
                            job_id=job.id,
                            course_id=job.course_id,
                            document_id=job.document_id,
                            state=job.status,
                            attempt=job.attempt,
                            error_code="MAX_ATTEMPTS_EXCEEDED",
                        )
                        continue
                    transition(status, JobStatus.QUEUED)
                    job.status = JobStatus.QUEUED.value
                    job.state_version += 1
                    self._clear_lease(job)
                    append_job_event(
                        session,
                        job,
                        "job.requeued",
                        {
                            "reason": "lease_expired"
                            if status is not JobStatus.RETRY_WAIT
                            else "retry_due"
                        },
                        now=now,
                        retention=self._event_retention,
                    )

                transition(JobStatus(job.status), JobStatus.LEASED)
                raw_token = new_lease_token()
                job.status = JobStatus.LEASED.value
                job.state_version += 1
                job.requested_pages = lease_requested_pages
                job.attempt += 1
                job.lease_version += 1
                job.lease_owner_id = request.worker_id
                job.lease_token_hash = hash_lease_token(raw_token)
                job.lease_expires_at = now + self._lease_policy.ttl
                job.heartbeat_at = now
                append_job_event(
                    session,
                    job,
                    "job.leased",
                    {
                        "status": JobStatus.LEASED.value,
                        "attempt": job.attempt,
                        "lease_version": job.lease_version,
                    },
                    now=now,
                    retention=self._event_retention,
                )
                log_job_event(
                    "job.leased",
                    job_id=job.id,
                    course_id=job.course_id,
                    document_id=job.document_id,
                    state=job.status,
                    attempt=job.attempt,
                    worker_id=request.worker_id,
                    lease_version=job.lease_version,
                )
                await session.flush()
                return WorkerLease(
                    job_id=job.id,
                    job_type="parse",
                    course_id=job.course_id,
                    document_id=job.document_id,
                    document_sha256=job.document_sha256,
                    deletion_epoch=job.document_deletion_epoch,
                    media_type=job.media_type,
                    parser_profile=job.parser_profile,
                    parser_schema_version="1.0",
                    attempt=job.attempt,
                    lease_version=job.lease_version,
                    lease_token=raw_token,
                    lease_expires_at=job.lease_expires_at,
                    input_url=(f"/worker/v1/jobs/{job.id}/input?lease_version={job.lease_version}"),
                    artifact_upload_url=f"/worker/v1/jobs/{job.id}/artifacts",
                    requested_pages=lease_requested_pages,
                )

    async def public_snapshot(self, principal: Principal, job_id: str) -> JobSnapshot | None:
        async with self._database.session(principal) as session:
            job = await session.scalar(
                select(ParseJobModel)
                .join(
                    DocumentModel,
                    and_(
                        DocumentModel.id == ParseJobModel.document_id,
                        DocumentModel.course_id == ParseJobModel.course_id,
                        DocumentModel.user_id == ParseJobModel.user_id,
                    ),
                )
                .join(
                    CourseModel,
                    and_(
                        CourseModel.id == ParseJobModel.course_id,
                        CourseModel.user_id == ParseJobModel.user_id,
                    ),
                )
                .join(UserModel, UserModel.id == ParseJobModel.user_id)
                .where(
                    ParseJobModel.id == job_id,
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.deletion_epoch == ParseJobModel.document_deletion_epoch,
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            )
            return None if job is None else snapshot_from_model(job)

    async def input_object_key(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_version: int,
        attempt: int,
        deletion_epoch: int,
    ) -> tuple[str, str]:
        async with self._database.worker_session(worker_id) as session:
            job = await self._repository.worker_job_for_update(session, job_id)
            if job is None:
                raise self._lease_lost()
            await self._assert_active_lease(
                session,
                job,
                worker_id=worker_id,
                lease_token=lease_token,
                lease_version=lease_version,
                attempt=attempt,
                deletion_epoch=deletion_epoch,
            )
            stored_object = await session.get(StoredObjectModel, job.stored_object_id)
            if stored_object is None or stored_object.deleted_at is not None:
                raise self._lease_lost()
            return stored_object.object_key, stored_object.media_type

    async def upload_artifact(
        self,
        job_id: str,
        artifact_name: str,
        chunks: AsyncIterable[bytes],
        content_type: str,
        artifact_schema_version: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_version: int,
        attempt: int,
        deletion_epoch: int,
        max_bytes: int,
    ) -> JobArtifactReceipt:
        normalized_name = artifact_name.strip()
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
        if (
            not normalized_name
            or normalized_name in {".", ".."}
            or any(character not in allowed for character in normalized_name)
        ):
            raise ApiProblem(
                status=422,
                code=ProblemCode.INVALID_REQUEST,
                title="artifact_name 无效",
            )
        if artifact_schema_version != "1.0" or "/" not in content_type:
            raise ApiProblem(
                status=422,
                code=ProblemCode.INVALID_REQUEST,
                title="artifact 声明无效",
            )

        async with self._database.worker_session(worker_id) as session:
            job = await self._repository.worker_job_for_update(session, job_id)
            if job is None:
                raise self._lease_lost()
            await self._assert_active_lease(
                session,
                job,
                worker_id=worker_id,
                lease_token=lease_token,
                lease_version=lease_version,
                attempt=attempt,
                deletion_epoch=deletion_epoch,
            )
            existing = await self._artifact_by_name(session, job_id, attempt, normalized_name)
            if existing is not None:
                return self._artifact_receipt(existing)
            scope = ObjectScope(
                subject=f"job-{job.id}",
                course_id=job.course_id,
                purpose="job-artifact",
            )

        target = await self._storage.create_upload(scope)
        try:
            metadata = await self._storage.put_stream(
                target.object_key,
                chunks,
                content_type,
                max_bytes=max_bytes,
            )
        except StorageUploadTooLarge as exc:
            raise ApiProblem(
                status=413,
                code=ProblemCode.FILE_TOO_LARGE,
                title="Worker artifact 超过大小限制",
            ) from exc

        duplicate: JobArtifactModel | None = None
        try:
            async with self._database.worker_session(worker_id) as session:
                job = await self._repository.worker_job_for_update(session, job_id)
                if job is None:
                    raise self._lease_lost()
                await self._assert_active_lease(
                    session,
                    job,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    lease_version=lease_version,
                    attempt=attempt,
                    deletion_epoch=deletion_epoch,
                )
                duplicate = await self._artifact_by_name(session, job_id, attempt, normalized_name)
                if duplicate is None:
                    assert metadata.sha256 is not None
                    stored_object = StoredObjectModel(
                        id=new_trace_id(),
                        user_id=job.user_id,
                        course_id=job.course_id,
                        object_key=metadata.object_key,
                        purpose="job-artifact",
                        sha256=metadata.sha256,
                        size_bytes=metadata.size_bytes,
                        media_type=metadata.content_type,
                    )
                    session.add(stored_object)
                    await session.flush()
                    artifact = JobArtifactModel(
                        id=new_trace_id(),
                        job_id=job.id,
                        user_id=job.user_id,
                        course_id=job.course_id,
                        document_id=job.document_id,
                        stored_object_id=stored_object.id,
                        attempt=job.attempt,
                        deletion_epoch=job.document_deletion_epoch,
                        artifact_name=normalized_name,
                        artifact_schema_version=artifact_schema_version,
                        sha256=metadata.sha256,
                        size_bytes=metadata.size_bytes,
                        media_type=metadata.content_type,
                        status="available",
                    )
                    session.add(artifact)
                    await session.flush()
                    append_job_event(
                        session,
                        job,
                        "job.artifact_uploaded",
                        {
                            "artifact_ref": artifact.id,
                            "artifact_name": artifact.artifact_name,
                            "size_bytes": artifact.size_bytes,
                        },
                        now=self._clock.now(),
                        retention=self._event_retention,
                    )
                    return self._artifact_receipt(artifact)
        except Exception:
            await self._storage.delete(target.object_key)
            raise

        await self._storage.delete(target.object_key)
        assert duplicate is not None
        return self._artifact_receipt(duplicate)

    async def start(
        self, job_id: str, request: JobStartRequest, idempotency_key: str
    ) -> JobSnapshot:
        now = self._clock.now()
        operation = "job.start"
        async with self._database.worker_session(request.worker_id) as session:
            request_hash, replay = await self._command_replay(
                session, job_id, request, operation, idempotency_key, now=now
            )
            if replay is not None:
                return replay
            job = await self._locked_active_job(session, job_id, request)
            if JobStatus(job.status) is not JobStatus.LEASED:
                raise self._state_conflict("只有 LEASED Job 可以开始解析。")
            transition(JobStatus.LEASED, JobStatus.PARSING)
            job.status = JobStatus.PARSING.value
            job.state_version += 1
            job.heartbeat_at = now
            job.lease_expires_at = now + self._lease_policy.ttl
            document = await session.get(DocumentModel, job.document_id)
            assert document is not None
            document.status = "processing"
            append_job_event(
                session,
                job,
                "job.started",
                {"status": job.status, "attempt": job.attempt},
                now=now,
                retention=self._event_retention,
            )
            log_job_event(
                "job.started",
                job_id=job.id,
                course_id=job.course_id,
                document_id=job.document_id,
                state=job.status,
                attempt=job.attempt,
                worker_id=request.worker_id,
                lease_version=job.lease_version,
            )
            snapshot = await self._snapshot_after_flush(session, job)
            self._store_command(
                session,
                request,
                operation,
                idempotency_key,
                request_hash,
                snapshot,
                now,
            )
            return snapshot

    async def heartbeat(
        self, job_id: str, request: JobHeartbeatRequest, idempotency_key: str
    ) -> JobSnapshot:
        now = self._clock.now()
        operation = "job.heartbeat"
        async with self._database.worker_session(request.worker_id) as session:
            request_hash, replay = await self._command_replay(
                session, job_id, request, operation, idempotency_key, now=now
            )
            if replay is not None:
                return replay
            job = await self._locked_active_job(session, job_id, request)
            if JobStatus(job.status) is not JobStatus.PARSING:
                raise self._state_conflict("只有 PARSING Job 可以发送心跳。")
            progress = request.progress.model_dump(mode="json")
            progress_changed = progress != job.progress
            job.progress = progress
            job.heartbeat_at = now
            job.lease_expires_at = now + self._lease_policy.ttl
            if progress_changed:
                append_job_event(
                    session,
                    job,
                    "job.heartbeat",
                    {"progress": progress},
                    now=now,
                    retention=self._event_retention,
                )
                log_job_event(
                    "job.heartbeat",
                    job_id=job.id,
                    course_id=job.course_id,
                    document_id=job.document_id,
                    state=job.status,
                    attempt=job.attempt,
                    worker_id=request.worker_id,
                    lease_version=job.lease_version,
                )
            snapshot = await self._snapshot_after_flush(session, job)
            self._store_command(
                session,
                request,
                operation,
                idempotency_key,
                request_hash,
                snapshot,
                now,
            )
            return snapshot

    async def checkpoint(
        self,
        job_id: str,
        request: PageCheckpointRequest,
        idempotency_key: str,
    ) -> JobSnapshot:
        early_replay = await self._early_command_replay(
            job_id, request, "job.checkpoint", idempotency_key
        )
        if early_replay is not None:
            return early_replay
        await self._validate_artifact(
            job_id,
            request,
            artifact_ref=request.output_ref,
            expected_sha256=request.output_sha256,
            expected_size=request.output_size_bytes,
            expected_schema=request.output_schema_version,
        )
        now = self._clock.now()
        operation = "job.checkpoint"
        async with self._database.worker_session(request.worker_id) as session:
            request_hash, replay = await self._command_replay(
                session, job_id, request, operation, idempotency_key, now=now
            )
            if replay is not None:
                return replay
            job = await self._locked_active_job(session, job_id, request)
            if JobStatus(job.status) is not JobStatus.PARSING:
                raise self._state_conflict("只有 PARSING Job 可以提交页检查点。")
            artifact = await self._locked_validated_artifact(
                session,
                job,
                artifact_ref=request.output_ref,
                expected_sha256=request.output_sha256,
                expected_size=request.output_size_bytes,
                expected_schema=request.output_schema_version,
            )
            existing = await session.scalar(
                select(PageCheckpointModel).where(
                    PageCheckpointModel.job_id == job.id,
                    PageCheckpointModel.attempt == request.attempt,
                    PageCheckpointModel.page_ordinal == request.page_ordinal,
                )
            )
            if existing is not None:
                same = (
                    existing.output_ref == request.output_ref
                    and existing.output_sha256 == request.output_sha256
                    and existing.output_size_bytes == request.output_size_bytes
                    and existing.output_schema_version == request.output_schema_version
                    and existing.status == request.status
                )
                if not same:
                    raise self._state_conflict("相同页检查点已提交不同内容。")
            else:
                session.add(
                    PageCheckpointModel(
                        id=new_trace_id(),
                        job_id=job.id,
                        user_id=job.user_id,
                        course_id=job.course_id,
                        document_id=job.document_id,
                        attempt=request.attempt,
                        page_ordinal=request.page_ordinal,
                        lease_version=request.lease_version,
                        status=request.status,
                        output_ref=artifact.id,
                        output_sha256=request.output_sha256,
                        output_size_bytes=request.output_size_bytes,
                        output_schema_version=request.output_schema_version,
                        source_backend=request.source_backend,
                        source_version=request.source_version,
                        error_code=request.error_code,
                    )
                )
                append_job_event(
                    session,
                    job,
                    "job.page_checkpointed",
                    {"page_ordinal": request.page_ordinal, "status": request.status},
                    now=now,
                    retention=self._event_retention,
                )
                log_job_event(
                    "job.page_checkpointed",
                    job_id=job.id,
                    course_id=job.course_id,
                    document_id=job.document_id,
                    state=job.status,
                    attempt=job.attempt,
                    worker_id=request.worker_id,
                    lease_version=job.lease_version,
                    page_ordinal=request.page_ordinal,
                    error_code=request.error_code,
                )
            snapshot = await self._snapshot_after_flush(session, job)
            self._store_command(
                session,
                request,
                operation,
                idempotency_key,
                request_hash,
                snapshot,
                now,
            )
            return snapshot

    async def complete(
        self, job_id: str, request: JobCompleteRequest, idempotency_key: str
    ) -> JobSnapshot:
        early_replay = await self._early_command_replay(
            job_id, request, "job.complete", idempotency_key
        )
        if early_replay is not None:
            return early_replay
        await self._validate_artifact(
            job_id,
            request,
            artifact_ref=request.result_manifest_ref,
            expected_sha256=request.result_sha256,
            expected_size=request.result_size_bytes,
            expected_schema=request.manifest_schema_version,
        )
        now = self._clock.now()
        operation = "job.complete"
        async with self._database.worker_session(request.worker_id) as session:
            request_hash, replay = await self._command_replay(
                session, job_id, request, operation, idempotency_key, now=now
            )
            if replay is not None:
                return replay
            job = await self._locked_active_job(session, job_id, request)
            if JobStatus(job.status) is not JobStatus.PARSING:
                raise self._state_conflict("只有 PARSING Job 可以提交结果。")
            artifact = await self._locked_validated_artifact(
                session,
                job,
                artifact_ref=request.result_manifest_ref,
                expected_sha256=request.result_sha256,
                expected_size=request.result_size_bytes,
                expected_schema=request.manifest_schema_version,
            )
            outcome = await self._revisions.ingest_attempt(
                session,
                job,
                artifact,
                reported_page_count=request.page_count,
                reported_failed_pages=request.failed_pages,
            )
            job.result_manifest_ref = artifact.id
            job.result_sha256 = request.result_sha256
            job.result_page_count = outcome.total_page_count
            job.failed_pages = list(outcome.missing_page_ordinals)
            job.state_version += 1

            if outcome.complete:
                transition(JobStatus.PARSING, JobStatus.RESULT_SUBMITTED)
                transition(JobStatus.RESULT_SUBMITTED, JobStatus.VALIDATING)
                transition(JobStatus.VALIDATING, JobStatus.INDEXING)
                transition(JobStatus.INDEXING, JobStatus.SUCCEEDED)
                job.status = JobStatus.SUCCEEDED.value
                job.retryable = False
                job.failure_code = None
                job.failure_summary = None
                event_type = "job.succeeded"
                event_payload: dict[str, object] = {
                    "page_count": outcome.total_page_count,
                    "preview_revision_id": outcome.revision_id,
                    "index_status": "blocked",
                }
            else:
                retrying = job.attempt < job.max_attempts
                target = JobStatus.RETRY_WAIT if retrying else JobStatus.PARTIAL_FAILED
                transition(JobStatus.PARSING, target)
                job.status = target.value
                job.retryable = retrying
                job.failure_code = "INCOMPLETE_PAGE_COVERAGE"
                job.failure_summary = None
                job.requested_pages = list(outcome.missing_page_ordinals)
                if retrying and job.parser_profile == "native-v1":
                    ocr_required_pages = set(
                        (
                            await session.scalars(
                                select(PageCheckpointModel.page_ordinal).where(
                                    PageCheckpointModel.job_id == job.id,
                                    PageCheckpointModel.attempt == job.attempt,
                                    PageCheckpointModel.status == "failed",
                                    PageCheckpointModel.error_code == "OCR_REQUIRED",
                                    PageCheckpointModel.page_ordinal.in_(
                                        outcome.missing_page_ordinals
                                    ),
                                )
                            )
                        ).all()
                    )
                    if ocr_required_pages == set(outcome.missing_page_ordinals):
                        job.parser_profile = "ocr-v1"
                        job.requires_ocr = True
                if retrying:
                    job.available_at = now + self._lease_policy.retry_delay(job.attempt)
                event_type = "job.retry_scheduled" if retrying else "job.partial_failed"
                event_payload = {
                    "code": job.failure_code,
                    "failed_pages": list(outcome.missing_page_ordinals),
                    "retryable": retrying,
                }
            self._clear_lease(job)
            document = await session.get(DocumentModel, job.document_id)
            assert document is not None
            if not outcome.complete:
                document.status = "queued" if job.retryable else "partial_failed"
            append_job_event(
                session,
                job,
                event_type,
                event_payload,
                now=now,
                retention=self._event_retention,
            )
            log_job_event(
                event_type,
                job_id=job.id,
                course_id=job.course_id,
                document_id=job.document_id,
                state=job.status,
                attempt=job.attempt,
                worker_id=request.worker_id,
                lease_version=request.lease_version,
                error_code=job.failure_code,
            )
            snapshot = await self._snapshot_after_flush(session, job)
            self._store_command(
                session,
                request,
                operation,
                idempotency_key,
                request_hash,
                snapshot,
                now,
            )
            return snapshot

    async def fail(self, job_id: str, request: JobFailRequest, idempotency_key: str) -> JobSnapshot:
        now = self._clock.now()
        operation = "job.fail"
        async with self._database.worker_session(request.worker_id) as session:
            request_hash, replay = await self._command_replay(
                session, job_id, request, operation, idempotency_key, now=now
            )
            if replay is not None:
                return replay
            job = await self._locked_active_job(session, job_id, request)
            source = JobStatus(job.status)
            retrying = request.retryable and job.attempt < job.max_attempts
            target = JobStatus.RETRY_WAIT if retrying else JobStatus.FAILED
            transition(source, target)
            job.status = target.value
            job.state_version += 1
            job.failure_code = request.error_code
            job.failure_summary = request.error_summary
            job.retryable = retrying
            if retrying:
                job.available_at = now + self._lease_policy.retry_delay(job.attempt)
            self._clear_lease(job)
            document = await session.get(DocumentModel, job.document_id)
            assert document is not None
            document.status = "queued" if retrying else "failed"
            append_job_event(
                session,
                job,
                "job.retry_scheduled" if retrying else "job.failed",
                {"code": request.error_code, "retryable": retrying},
                now=now,
                retention=self._event_retention,
            )
            log_job_event(
                "job.retry_scheduled" if retrying else "job.failed",
                job_id=job.id,
                course_id=job.course_id,
                document_id=job.document_id,
                state=job.status,
                attempt=job.attempt,
                worker_id=request.worker_id,
                lease_version=request.lease_version,
                error_code=request.error_code,
            )
            snapshot = await self._snapshot_after_flush(session, job)
            self._store_command(
                session,
                request,
                operation,
                idempotency_key,
                request_hash,
                snapshot,
                now,
            )
            return snapshot

    async def _early_command_replay(
        self,
        job_id: str,
        request: LeaseCommand,
        operation: str,
        idempotency_key: str,
    ) -> JobSnapshot | None:
        now = self._clock.now()
        async with self._database.worker_session(request.worker_id) as session:
            _, replay = await self._command_replay(
                session,
                job_id,
                request,
                operation,
                idempotency_key,
                now=now,
            )
            return replay

    async def _command_replay(
        self,
        session: AsyncSession,
        job_id: str,
        request: LeaseCommand,
        operation: str,
        idempotency_key: str,
        *,
        now: datetime,
    ) -> tuple[str, JobSnapshot | None]:
        request_hash = self._idempotency.request_hash(
            {"job_id": job_id, "body": request.model_dump(mode="json")}
        )
        await self._idempotency.lock(session, request.worker_id, operation, idempotency_key)
        replay = await self._idempotency.replay_or_none(
            session,
            worker_id=request.worker_id,
            operation=operation,
            key=idempotency_key,
            request_hash=request_hash,
            now=now,
        )
        return request_hash, None if replay is None else JobSnapshot.model_validate(replay)

    def _store_command(
        self,
        session: AsyncSession,
        request: LeaseCommand,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        snapshot: JobSnapshot,
        now: datetime,
    ) -> None:
        self._idempotency.store(
            session,
            worker_id=request.worker_id,
            operation=operation,
            key=idempotency_key,
            request_hash=request_hash,
            response_body=snapshot.model_dump(mode="json"),
            now=now,
        )

    @staticmethod
    async def _snapshot_after_flush(session: AsyncSession, job: ParseJobModel) -> JobSnapshot:
        await session.flush()
        await session.refresh(job)
        return snapshot_from_model(job)

    async def _locked_active_job(
        self,
        session: AsyncSession,
        job_id: str,
        request: LeaseCommand,
    ) -> ParseJobModel:
        job = await self._repository.worker_job_for_update(session, job_id)
        if job is None:
            raise self._lease_lost()
        await self._assert_active_lease(
            session,
            job,
            worker_id=request.worker_id,
            lease_token=request.lease_token,
            lease_version=request.lease_version,
            attempt=request.attempt,
            deletion_epoch=request.deletion_epoch,
        )
        return job

    async def _validate_artifact(
        self,
        job_id: str,
        request: LeaseCommand,
        *,
        artifact_ref: str,
        expected_sha256: str,
        expected_size: int,
        expected_schema: str,
    ) -> None:
        async with self._database.worker_session(request.worker_id) as session:
            job = await self._locked_active_job(session, job_id, request)
            artifact = await self._artifact_by_ref(session, job, artifact_ref)
            if artifact is None:
                raise self._artifact_mismatch()
            stored_object = await session.get(StoredObjectModel, artifact.stored_object_id)
            if stored_object is None or stored_object.deleted_at is not None:
                raise self._artifact_mismatch()
            object_key = stored_object.object_key
            declared_matches = (
                artifact.sha256 == expected_sha256
                and artifact.size_bytes == expected_size
                and artifact.artifact_schema_version == expected_schema
            )
            if not declared_matches:
                raise self._artifact_mismatch()

        try:
            metadata = await self._storage.head(object_key)
        except FileNotFoundError as exc:
            raise self._artifact_mismatch() from exc
        if (
            metadata.sha256 != expected_sha256
            or metadata.size_bytes != expected_size
            or metadata.content_type != artifact.media_type
        ):
            raise self._artifact_mismatch()

    async def _artifact_by_ref(
        self,
        session: AsyncSession,
        job: ParseJobModel,
        artifact_ref: str,
    ) -> JobArtifactModel | None:
        return cast(
            JobArtifactModel | None,
            await session.scalar(
                select(JobArtifactModel).where(
                    JobArtifactModel.id == artifact_ref,
                    JobArtifactModel.job_id == job.id,
                    JobArtifactModel.user_id == job.user_id,
                    JobArtifactModel.course_id == job.course_id,
                    JobArtifactModel.document_id == job.document_id,
                    JobArtifactModel.attempt == job.attempt,
                    JobArtifactModel.deletion_epoch == job.document_deletion_epoch,
                    JobArtifactModel.status == "available",
                )
            ),
        )

    async def _locked_validated_artifact(
        self,
        session: AsyncSession,
        job: ParseJobModel,
        *,
        artifact_ref: str,
        expected_sha256: str,
        expected_size: int,
        expected_schema: str,
    ) -> JobArtifactModel:
        artifact = cast(
            JobArtifactModel | None,
            await session.scalar(
                select(JobArtifactModel)
                .where(
                    JobArtifactModel.id == artifact_ref,
                    JobArtifactModel.job_id == job.id,
                    JobArtifactModel.user_id == job.user_id,
                    JobArtifactModel.course_id == job.course_id,
                    JobArtifactModel.document_id == job.document_id,
                    JobArtifactModel.attempt == job.attempt,
                    JobArtifactModel.deletion_epoch == job.document_deletion_epoch,
                    JobArtifactModel.status == "available",
                )
                .with_for_update(of=JobArtifactModel)
            ),
        )
        if artifact is None:
            raise self._artifact_mismatch()
        stored_object = cast(
            StoredObjectModel | None,
            await session.scalar(
                select(StoredObjectModel)
                .where(
                    StoredObjectModel.id == artifact.stored_object_id,
                    StoredObjectModel.user_id == job.user_id,
                    StoredObjectModel.course_id == job.course_id,
                    StoredObjectModel.deleted_at.is_(None),
                )
                .with_for_update(of=StoredObjectModel)
            ),
        )
        declared_matches = (
            artifact.sha256 == expected_sha256
            and artifact.size_bytes == expected_size
            and artifact.artifact_schema_version == expected_schema
        )
        if stored_object is None or not declared_matches:
            raise self._artifact_mismatch()
        try:
            metadata = await self._storage.head(stored_object.object_key)
        except FileNotFoundError as exc:
            raise self._artifact_mismatch() from exc
        if (
            metadata.sha256 != expected_sha256
            or metadata.size_bytes != expected_size
            or metadata.content_type != artifact.media_type
        ):
            raise self._artifact_mismatch()
        return artifact

    async def _artifact_by_name(
        self,
        session: AsyncSession,
        job_id: str,
        attempt: int,
        artifact_name: str,
    ) -> JobArtifactModel | None:
        return cast(
            JobArtifactModel | None,
            await session.scalar(
                select(JobArtifactModel).where(
                    JobArtifactModel.job_id == job_id,
                    JobArtifactModel.attempt == attempt,
                    JobArtifactModel.artifact_name == artifact_name,
                    JobArtifactModel.status == "available",
                )
            ),
        )

    @staticmethod
    def _artifact_receipt(artifact: JobArtifactModel) -> JobArtifactReceipt:
        return JobArtifactReceipt(
            artifact_ref=artifact.id,
            artifact_name=artifact.artifact_name,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            media_type=artifact.media_type,
            artifact_schema_version="1.0",
        )

    async def _assert_active_lease(
        self,
        session: AsyncSession,
        job: ParseJobModel,
        *,
        worker_id: str,
        lease_token: str,
        lease_version: int,
        attempt: int,
        deletion_epoch: int,
    ) -> None:
        document = await session.get(DocumentModel, job.document_id)
        now = self._clock.now()
        valid = (
            document is not None
            and document.deleted_at is None
            and document.deletion_epoch == deletion_epoch
            and job.document_deletion_epoch == deletion_epoch
            and job.status in {JobStatus.LEASED.value, JobStatus.PARSING.value}
            and job.lease_owner_id == worker_id
            and job.lease_version == lease_version
            and job.attempt == attempt
            and job.lease_expires_at is not None
            and job.lease_expires_at > now
            and lease_token_matches(lease_token, job.lease_token_hash)
        )
        if not valid:
            raise self._lease_lost()

    @staticmethod
    def _clear_lease(job: ParseJobModel) -> None:
        job.lease_owner_id = None
        job.lease_token_hash = None
        job.lease_expires_at = None
        job.heartbeat_at = None

    @staticmethod
    def _lease_lost() -> ApiProblem:
        return ApiProblem(
            status=409,
            code=ProblemCode.LEASE_LOST,
            title="任务租约已失效",
            detail="该任务已被重新分配、过期或取消。",
        )

    @staticmethod
    def _state_conflict(detail: str) -> ApiProblem:
        return ApiProblem(
            status=409,
            code=ProblemCode.STATE_CONFLICT,
            title="任务状态冲突",
            detail=detail,
        )

    @staticmethod
    def _artifact_mismatch() -> ApiProblem:
        return ApiProblem(
            status=409,
            code=ProblemCode.HASH_MISMATCH,
            title="Worker artifact 校验失败",
            detail="artifact 的 scope、hash、size 或 schema 不匹配。",
        )
