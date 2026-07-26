"""Principal-scoped document upload and deletion services."""

from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.identity.principal import CourseScope, Principal
from study_agent.infrastructure.db.models import (
    CourseModel,
    DeletionJobModel,
    DocumentModel,
    OutboxEventModel,
    StoredObjectModel,
    UploadSessionModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.courses.manifest import CorpusRole, ManifestPolicy
from study_agent.modules.courses.upload_validation import (
    MARKDOWN_MEDIA_TYPE,
    UploadValidator,
    ValidatedUpload,
)
from study_agent.modules.idempotency import IdempotencyService
from study_agent.modules.jobs.clock import SystemClock
from study_agent.modules.jobs.service import cancel_document_jobs, enqueue_parse_job
from study_agent.providers.protocols import Clock, ObjectMetadata, ObjectScope, UploadTarget
from study_agent.storage.local import StorageUploadTooLarge

type DocumentReviewStatus = Literal["pending", "approved", "rejected"]


@dataclass(frozen=True, slots=True)
class Document:
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

    @property
    def indexable(self) -> bool:
        return self.review_status == "approved" and ManifestPolicy.is_indexable(self.corpus_role)


@dataclass(frozen=True, slots=True)
class UploadSession:
    id: str
    document: Document
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class UploadReceipt:
    upload_session_id: str
    status: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DeletionSnapshot:
    id: str
    target_type: str
    target_id: str
    deletion_epoch: int
    status: str
    attempt_count: int
    completed_at: datetime | None


class BinaryStorage(Protocol):
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

    async def read_prefix(self, object_key: str, size: int = 16) -> bytes: ...

    async def delete(self, object_key: str) -> None: ...


type UploadRow = tuple[
    UploadSessionModel,
    DocumentModel,
    CourseModel,
    UserModel,
    StoredObjectModel,
]


def _document_from_model(model: DocumentModel) -> Document:
    return Document(
        id=model.id,
        course_id=model.course_id,
        filename=model.filename,
        media_type=model.media_type,
        corpus_role=CorpusRole(model.corpus_role),
        verified_sha256=model.verified_sha256,
        status=model.status,
        review_status=cast(DocumentReviewStatus, model.review_status),
        preview_revision_id=model.preview_revision_id,
        active_revision_id=model.active_revision_id,
        deletion_epoch=model.deletion_epoch,
    )


def _document_payload(document: Document) -> dict[str, Any]:
    return {
        "id": document.id,
        "course_id": document.course_id,
        "filename": document.filename,
        "media_type": document.media_type,
        "corpus_role": document.corpus_role.value,
        "verified_sha256": document.verified_sha256,
        "status": document.status,
        "review_status": document.review_status,
        "preview_revision_id": document.preview_revision_id,
        "active_revision_id": document.active_revision_id,
        "deletion_epoch": document.deletion_epoch,
    }


def _document_from_payload(payload: dict[str, Any]) -> Document:
    return Document(
        id=str(payload["id"]),
        course_id=str(payload["course_id"]),
        filename=str(payload["filename"]),
        media_type=str(payload["media_type"]),
        corpus_role=CorpusRole(str(payload["corpus_role"])),
        verified_sha256=str(payload["verified_sha256"]),
        status=str(payload["status"]),
        review_status=cast(DocumentReviewStatus, str(payload.get("review_status", "approved"))),
        preview_revision_id=(
            None
            if payload.get("preview_revision_id") is None
            else str(payload["preview_revision_id"])
        ),
        active_revision_id=(
            None
            if payload.get("active_revision_id") is None
            else str(payload["active_revision_id"])
        ),
        deletion_epoch=int(payload["deletion_epoch"]),
    )


class DocumentService:
    def __init__(
        self,
        database: Database,
        storage: BinaryStorage,
        *,
        clock: Clock | None = None,
        job_max_attempts: int = 3,
        job_event_retention: timedelta = timedelta(hours=24),
    ) -> None:
        self._database = database
        self._storage = storage
        self._idempotency = IdempotencyService()
        self._clock = clock or SystemClock()
        self._job_max_attempts = job_max_attempts
        self._job_event_retention = job_event_retention

    async def create_upload(
        self,
        scope: CourseScope,
        upload: ValidatedUpload,
        role: CorpusRole,
    ) -> UploadSession:
        target = await self._storage.create_upload(
            ObjectScope(
                subject=scope.subject,
                course_id=scope.course_id,
                purpose="original",
            )
        )
        object_id = str(uuid4())
        document_id = str(uuid4())
        upload_session_id = str(uuid4())

        try:
            async with self._database.session(scope.principal) as session:
                course = await session.scalar(
                    select(CourseModel)
                    .join(UserModel, UserModel.id == CourseModel.user_id)
                    .where(
                        CourseModel.id == scope.course_id,
                        CourseModel.deleted_at.is_(None),
                        UserModel.subject == scope.subject,
                        UserModel.authentication_method
                        == scope.principal.authentication_method.value,
                    )
                    .with_for_update(of=CourseModel)
                )
                if course is None:
                    raise self._not_found("课程不存在")

                duplicate = await session.scalar(
                    select(DocumentModel.id).where(
                        DocumentModel.course_id == course.id,
                        DocumentModel.user_id == course.user_id,
                        DocumentModel.verified_sha256 == upload.sha256,
                        DocumentModel.corpus_role == role.value,
                        DocumentModel.deleted_at.is_(None),
                    )
                )
                if duplicate is not None:
                    raise self._duplicate()

                stored_object = StoredObjectModel(
                    id=object_id,
                    user_id=course.user_id,
                    course_id=course.id,
                    object_key=target.object_key,
                    purpose="original",
                    sha256=upload.sha256,
                    size_bytes=upload.size_bytes,
                    media_type=upload.media_type,
                )
                session.add(stored_object)
                await session.flush()

                model = DocumentModel(
                    id=document_id,
                    user_id=course.user_id,
                    course_id=course.id,
                    stored_object_id=object_id,
                    filename=upload.filename,
                    media_type=upload.media_type,
                    corpus_role=role.value,
                    verified_sha256=upload.sha256,
                    status="uploading",
                    review_status="pending",
                    deletion_epoch=0,
                )
                session.add(model)
                await session.flush()

                session.add(
                    UploadSessionModel(
                        id=upload_session_id,
                        user_id=course.user_id,
                        course_id=course.id,
                        stored_object_id=object_id,
                        document_id=document_id,
                        object_key=target.object_key,
                        expected_sha256=upload.sha256,
                        expected_size=upload.size_bytes,
                        media_type=upload.media_type,
                        status="pending",
                        expires_at=target.expires_at,
                    )
                )
                await session.flush()
                return UploadSession(
                    id=upload_session_id,
                    document=_document_from_model(model),
                    expires_at=target.expires_at,
                )
        except IntegrityError as exc:
            raise self._duplicate() from exc

    async def upload_stream(
        self,
        principal: Principal,
        upload_session_id: str,
        chunks: AsyncIterable[bytes],
        content_type: str,
    ) -> UploadReceipt:
        async with self._database.session(principal) as session:
            row = await self._upload_row(
                session, principal, upload_session_id=upload_session_id, lock=True
            )
            if row is None:
                raise self._not_found("上传会话不存在")
            upload_session, _, _, _, stored_object = row
            self._ensure_upload_not_expired(upload_session)
            if upload_session.status == "receiving":
                raise self._state_conflict("上传会话正在接收数据。")
            if upload_session.status == "completed":
                raise self._state_conflict("上传会话已经完成。")
            if content_type.lower().strip() != upload_session.media_type:
                raise ApiProblem(
                    status=415,
                    code=ProblemCode.UNSUPPORTED_MEDIA_TYPE,
                    title="上传类型不匹配",
                    detail=(
                        "请使用 PDF、Markdown、JPG 或 PNG 文件, 并保持上传类型与文件扩展名一致。"
                    ),
                )
            upload_session.status = "receiving"
            object_key = stored_object.object_key
            expected_size = upload_session.expected_size
            expected_type = upload_session.media_type

        try:
            metadata = await self._storage.put_stream(
                object_key,
                chunks,
                expected_type,
                max_bytes=expected_size,
            )
        except StorageUploadTooLarge as exc:
            await self._reset_receiving_upload(principal, upload_session_id)
            raise ApiProblem(
                status=413,
                code=ProblemCode.FILE_TOO_LARGE,
                title="上传文件超过声明大小",
            ) from exc
        except Exception:
            await self._reset_receiving_upload(principal, upload_session_id)
            raise

        async with self._database.session(principal) as session:
            row = await self._upload_row(
                session, principal, upload_session_id=upload_session_id, lock=True
            )
            if row is None:
                await self._storage.delete(object_key)
                raise self._not_found("上传会话不存在")
            upload_session, _, _, _, _ = row
            if upload_session.status != "receiving":
                await self._storage.delete(object_key)
                raise self._state_conflict("上传会话状态已变化。")
            upload_session.status = "uploaded"

        assert metadata.sha256 is not None
        return UploadReceipt(
            upload_session_id=upload_session_id,
            status="uploaded",
            size_bytes=metadata.size_bytes,
            sha256=metadata.sha256,
        )

    async def complete_upload(
        self,
        principal: Principal,
        document_id: str,
        upload_session_id: str,
        idempotency_key: str,
        validator: UploadValidator,
    ) -> Document:
        operation = "document.upload_complete"
        request_hash = self._idempotency.request_hash(
            {"document_id": document_id, "upload_session_id": upload_session_id}
        )

        async with self._database.session(principal) as session:
            await self._idempotency.lock(
                session,
                principal,
                operation=operation,
                key=idempotency_key,
            )
            replay = await self._idempotency.replay_or_none(
                session,
                principal,
                operation=operation,
                key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return _document_from_payload(replay.response_body)
            row = await self._upload_row(
                session,
                principal,
                upload_session_id=upload_session_id,
                document_id=document_id,
                lock=True,
            )
            if row is None:
                raise self._not_found("资料或上传会话不存在")
            upload_session, document, _, _, stored_object = row
            if upload_session.status == "completed":
                completed = _document_from_model(document)
                self._store_completion_response(
                    session,
                    principal,
                    operation,
                    idempotency_key,
                    request_hash,
                    completed,
                )
                return completed
            if upload_session.status != "uploaded":
                raise self._state_conflict("对象尚未完成上传。")
            self._ensure_upload_not_expired(upload_session)
            declaration = ValidatedUpload(
                filename=document.filename,
                media_type=upload_session.media_type,
                size_bytes=upload_session.expected_size,
                sha256=upload_session.expected_sha256,
            )
            object_key = stored_object.object_key

        try:
            metadata = await self._storage.head(object_key)
            inspection_size = (
                declaration.size_bytes if declaration.media_type == MARKDOWN_MEDIA_TYPE else 16
            )
            prefix = await self._storage.read_prefix(object_key, size=inspection_size)
        except FileNotFoundError as exc:
            raise self._state_conflict("上传对象不存在。") from exc
        validator.verify_stored(declaration, metadata, prefix)

        async with self._database.session(principal) as session:
            await self._idempotency.lock(
                session,
                principal,
                operation=operation,
                key=idempotency_key,
            )
            replay = await self._idempotency.replay_or_none(
                session,
                principal,
                operation=operation,
                key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return _document_from_payload(replay.response_body)
            row = await self._upload_row(
                session,
                principal,
                upload_session_id=upload_session_id,
                document_id=document_id,
                lock=True,
            )
            if row is None:
                raise self._not_found("资料或上传会话不存在")
            upload_session, document, _, _, stored_object = row
            if upload_session.status == "completed":
                completed = _document_from_model(document)
                self._store_completion_response(
                    session,
                    principal,
                    operation,
                    idempotency_key,
                    request_hash,
                    completed,
                )
                return completed
            if upload_session.status != "uploaded":
                raise self._state_conflict("上传会话状态已变化。")

            assert metadata.sha256 is not None
            stored_object.sha256 = metadata.sha256
            stored_object.size_bytes = metadata.size_bytes
            stored_object.media_type = metadata.content_type
            document.verified_sha256 = metadata.sha256
            document.status = "uploaded"
            upload_session.status = "completed"
            upload_session.completed_at = datetime.now(UTC)
            if document.corpus_role != CorpusRole.EXCLUDED.value:
                await enqueue_parse_job(
                    session,
                    document,
                    stored_object,
                    now=self._clock.now(),
                    max_attempts=self._job_max_attempts,
                    event_retention=self._job_event_retention,
                )
            completed = _document_from_model(document)
            self._store_completion_response(
                session,
                principal,
                operation,
                idempotency_key,
                request_hash,
                completed,
            )
            await session.flush()
            return completed

    async def get(self, principal: Principal, document_id: str) -> Document | None:
        async with self._database.session(principal) as session:
            model = await session.scalar(
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
            )
            return None if model is None else _document_from_model(model)

    async def delete(
        self,
        principal: Principal,
        document_id: str,
        idempotency_key: str,
    ) -> str:
        now = datetime.now(UTC)
        deletion_job_id = str(uuid4())
        outbox_event_id = str(uuid4())
        operation = "document.delete"
        request_hash = self._idempotency.request_hash({"document_id": document_id})
        async with self._database.session(principal) as session:
            await self._idempotency.lock(
                session,
                principal,
                operation=operation,
                key=idempotency_key,
            )
            replay = await self._idempotency.replay_or_none(
                session,
                principal,
                operation=operation,
                key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return str(replay.response_body["deletion_id"])
            row = (
                (
                    await session.execute(
                        select(DocumentModel, CourseModel, UserModel, StoredObjectModel)
                        .join(CourseModel, CourseModel.id == DocumentModel.course_id)
                        .join(UserModel, UserModel.id == CourseModel.user_id)
                        .join(
                            StoredObjectModel,
                            StoredObjectModel.id == DocumentModel.stored_object_id,
                        )
                        .where(
                            DocumentModel.id == document_id,
                            DocumentModel.deleted_at.is_(None),
                            CourseModel.deleted_at.is_(None),
                            UserModel.subject == principal.subject,
                            UserModel.authentication_method
                            == principal.authentication_method.value,
                        )
                        .with_for_update(of=DocumentModel)
                    )
                )
                .tuples()
                .first()
            )
            if row is None:
                raise self._not_found("资料不存在")
            document, course, _, stored_object = row
            document.deleted_at = now
            document.status = "deleting"
            document.deletion_epoch += 1
            document.preview_revision_id = None
            document.active_revision_id = None
            stored_object.deleted_at = now
            await cancel_document_jobs(
                session,
                document,
                now=self._clock.now(),
                event_retention=self._job_event_retention,
            )
            upload_sessions = (
                await session.scalars(
                    select(UploadSessionModel).where(UploadSessionModel.document_id == document.id)
                )
            ).all()
            for upload_session in upload_sessions:
                upload_session.status = "cancelled"

            session.add(
                DeletionJobModel(
                    id=deletion_job_id,
                    user_id=course.user_id,
                    target_type="document",
                    target_id=document.id,
                    deletion_epoch=document.deletion_epoch,
                    status="pending",
                )
            )
            session.add(
                OutboxEventModel(
                    id=outbox_event_id,
                    aggregate_type="document",
                    aggregate_id=document.id,
                    event_type="document.deletion_requested",
                    payload={
                        "deletion_job_id": deletion_job_id,
                        "deletion_epoch": document.deletion_epoch,
                    },
                    status="pending",
                )
            )
            self._idempotency.store(
                session,
                principal,
                operation=operation,
                key=idempotency_key,
                request_hash=request_hash,
                response_status=202,
                response_body={"deletion_id": deletion_job_id},
            )
            object_key = stored_object.object_key

        await self._attempt_local_cleanup(
            principal,
            deletion_job_id=deletion_job_id,
            outbox_event_id=outbox_event_id,
            object_key=object_key,
        )
        return deletion_job_id

    async def get_deletion(self, principal: Principal, deletion_id: str) -> DeletionSnapshot | None:
        async with self._database.session(principal) as session:
            job = await session.scalar(
                select(DeletionJobModel)
                .join(UserModel, UserModel.id == DeletionJobModel.user_id)
                .where(
                    DeletionJobModel.id == deletion_id,
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            )
            if job is None:
                return None
            return DeletionSnapshot(
                id=job.id,
                target_type=job.target_type,
                target_id=job.target_id,
                deletion_epoch=job.deletion_epoch,
                status=job.status,
                attempt_count=job.attempt_count,
                completed_at=job.completed_at,
            )

    async def _upload_row(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        upload_session_id: str,
        document_id: str | None = None,
        lock: bool,
    ) -> UploadRow | None:
        if lock:
            scoped_document_id = await session.scalar(
                select(UploadSessionModel.document_id)
                .join(DocumentModel, DocumentModel.id == UploadSessionModel.document_id)
                .join(CourseModel, CourseModel.id == DocumentModel.course_id)
                .join(UserModel, UserModel.id == CourseModel.user_id)
                .where(
                    UploadSessionModel.id == upload_session_id,
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            )
            if scoped_document_id is None or (
                document_id is not None and scoped_document_id != document_id
            ):
                return None
            locked_document_id = await session.scalar(
                select(DocumentModel.id)
                .where(DocumentModel.id == scoped_document_id)
                .with_for_update(of=DocumentModel)
            )
            if locked_document_id is None:
                return None

        statement = (
            select(
                UploadSessionModel,
                DocumentModel,
                CourseModel,
                UserModel,
                StoredObjectModel,
            )
            .join(DocumentModel, DocumentModel.id == UploadSessionModel.document_id)
            .join(CourseModel, CourseModel.id == DocumentModel.course_id)
            .join(UserModel, UserModel.id == CourseModel.user_id)
            .join(
                StoredObjectModel,
                StoredObjectModel.id == UploadSessionModel.stored_object_id,
            )
            .where(
                UploadSessionModel.id == upload_session_id,
                DocumentModel.deleted_at.is_(None),
                CourseModel.deleted_at.is_(None),
                UserModel.subject == principal.subject,
                UserModel.authentication_method == principal.authentication_method.value,
            )
        )
        if document_id is not None:
            statement = statement.where(DocumentModel.id == document_id)
        if lock:
            statement = statement.with_for_update(of=UploadSessionModel)
        return (await session.execute(statement)).tuples().first()

    async def _reset_receiving_upload(self, principal: Principal, upload_session_id: str) -> None:
        async with self._database.session(principal) as session:
            row = await self._upload_row(
                session, principal, upload_session_id=upload_session_id, lock=True
            )
            if row is not None and row[0].status == "receiving":
                row[0].status = "pending"

    async def _attempt_local_cleanup(
        self,
        principal: Principal,
        *,
        deletion_job_id: str,
        outbox_event_id: str,
        object_key: str,
    ) -> None:
        failure = False
        try:
            await self._storage.delete(object_key)
        except Exception:
            failure = True

        now = datetime.now(UTC)
        async with self._database.session(principal) as session:
            job = await session.scalar(
                select(DeletionJobModel)
                .join(UserModel, UserModel.id == DeletionJobModel.user_id)
                .where(
                    DeletionJobModel.id == deletion_job_id,
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
                .with_for_update(of=DeletionJobModel)
            )
            event = await session.get(OutboxEventModel, outbox_event_id)
            if job is None or event is None:
                return
            job.attempt_count += 1
            event.attempt_count += 1
            if failure:
                job.status = "retry_wait"
                job.available_at = now + timedelta(seconds=30)
                job.last_error_code = "STORAGE_DELETE_FAILED"
                event.status = "retry_wait"
                event.available_at = job.available_at
                event.last_error_code = "STORAGE_DELETE_FAILED"
            else:
                job.status = "completed"
                job.completed_at = now
                job.last_error_code = None
                event.status = "published"
                event.published_at = now
                event.last_error_code = None

    def _store_completion_response(
        self,
        session: AsyncSession,
        principal: Principal,
        operation: str,
        key: str,
        request_hash: str,
        document: Document,
    ) -> None:
        self._idempotency.store(
            session,
            principal,
            operation=operation,
            key=key,
            request_hash=request_hash,
            response_status=202,
            response_body=_document_payload(document),
        )

    @staticmethod
    def _ensure_upload_not_expired(upload_session: UploadSessionModel) -> None:
        if upload_session.expires_at <= datetime.now(UTC):
            raise ApiProblem(
                status=409,
                code=ProblemCode.STATE_CONFLICT,
                title="上传会话已过期",
            )

    @staticmethod
    def _not_found(title: str) -> ApiProblem:
        return ApiProblem(status=404, code=ProblemCode.RESOURCE_NOT_FOUND, title=title)

    @staticmethod
    def _state_conflict(detail: str) -> ApiProblem:
        return ApiProblem(
            status=409,
            code=ProblemCode.STATE_CONFLICT,
            title="上传状态冲突",
            detail=detail,
        )

    @staticmethod
    def _duplicate() -> ApiProblem:
        return ApiProblem(
            status=409,
            code=ProblemCode.DOCUMENT_DUPLICATE,
            title="资料已存在",
            detail="相同内容与语料角色的资料已经上传。",
        )
