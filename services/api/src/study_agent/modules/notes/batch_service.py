"""Principal-scoped control plane for the local note-workflow demo."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import cast

from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    NoteCommandDedupModel,
    NoteContentVersionModel,
    NoteCoverageUnitModel,
    NoteCoverageUnitResultModel,
    NoteGenerationBatchModel,
    NoteGenerationEventModel,
    NoteGenerationInputModel,
    NoteGenerationItemModel,
    NoteGenerationOutputModel,
    NoteItemInputModel,
    NoteModel,
    RevisionChunkModel,
    UserModel,
)
from study_agent.infrastructure.db.models.core import new_id
from study_agent.infrastructure.db.session import Database
from study_agent.observability.trace import get_trace_id, new_trace_id
from study_agent.providers.protocols import Clock
from study_contracts import (
    CoverageUnitSnapshot,
    CoverageUnitStatus,
    CoverageUnitType,
    EtaUnavailableReason,
    MergedNoteBatchRequest,
    NoteBatchCommandKind,
    NoteBatchMode,
    NoteBatchSnapshot,
    NoteBatchStatus,
    NoteBatchStyle,
    NoteInputSnapshot,
    NoteItemSnapshot,
    NoteItemStatus,
    PerDocumentNoteBatchRequest,
)

_PDF = "application/pdf"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_PPT = "application/vnd.ms-powerpoint"
_TERMINAL_ITEM_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_CREATE_COMMAND_SCOPE = "note-batch:create"
_REGENERATION_COMMAND_SCOPE = "note-batch:regeneration"


class NoteBatchServiceErrorCode(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    INVALID_REQUEST = "INVALID_REQUEST"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    DOCUMENT_NOT_READY = "DOCUMENT_NOT_READY"
    REQUEST_LIMIT_EXCEEDED = "REQUEST_LIMIT_EXCEEDED"
    DEMO_UNAVAILABLE = "DEMO_UNAVAILABLE"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    VERSION_NOT_FOUND = "VERSION_NOT_FOUND"


class NoteBatchServiceError(RuntimeError):
    def __init__(self, code: NoteBatchServiceErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class _FrozenUnit:
    ordinal: int
    unit_type: str
    locator: str
    content_sha256: str
    is_substantive: bool


@dataclass(frozen=True, slots=True)
class _FrozenInput:
    document_id: str
    revision_id: str
    deletion_epoch: int
    document_name: str
    media_type: str
    content_sha256: str
    index_manifest_at_submit: str
    units: tuple[_FrozenUnit, ...]


class NoteBatchService:
    """Create and inspect durable merged batches without invoking a provider."""

    def __init__(self, database: Database, settings: Settings, clock: Clock) -> None:
        self._database = database
        self._settings = settings
        self._clock = clock

    async def create_batch(
        self,
        principal: Principal,
        course_id: str,
        request: MergedNoteBatchRequest | PerDocumentNoteBatchRequest,
        idempotency_key: str,
    ) -> NoteBatchSnapshot:
        if not isinstance(request, MergedNoteBatchRequest):
            raise NoteBatchServiceError(
                NoteBatchServiceErrorCode.INVALID_REQUEST,
                "当前演示仅支持 merged 模式。",
            )
        self._require_local_demo()
        key = _normalize_idempotency_key(idempotency_key)
        if len(request.document_ids) > self._settings.note_batch_max_documents:
            raise NoteBatchServiceError(
                NoteBatchServiceErrorCode.REQUEST_LIMIT_EXCEEDED,
                "选择的资料数量超过当前批次上限。",
            )

        section_path = tuple(request.section_path or ["未分类"])
        semantic_request: dict[str, object] = {
            "course_id": course_id,
            "document_ids": list(request.document_ids),
            "mode": NoteBatchMode.MERGED.value,
            "style": request.style.value,
            "section_path": list(section_path),
            "title": request.title,
        }
        request_hash = _canonical_hash(semantic_request)
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()

        async with self._database.session(principal) as session:
            course = await self._course_for_principal(session, principal, course_id)
            if course is None:
                raise _not_found()
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
                {
                    "lock_name": (
                        f"note-demo:{course.user_id}:{course.id}:{_CREATE_COMMAND_SCOPE}:{key_hash}"
                    )
                },
            )
            replay = await self._replay_or_none(
                session,
                course=course,
                command_scope=_CREATE_COMMAND_SCOPE,
                key_hash=key_hash,
                request_hash=request_hash,
            )
            if replay is not None:
                return await self._snapshot(session, replay)

            frozen_inputs = await self._freeze_inputs(
                session,
                course,
                tuple(request.document_ids),
            )
            return await self._persist_batch(
                session,
                course=course,
                frozen_inputs=frozen_inputs,
                command_kind=NoteBatchCommandKind.CREATE.value,
                style=request.style.value,
                title=request.title,
                section_path=section_path,
                target_note_id=None,
                target_note_version=None,
                target_note_version_sha256=None,
                command_scope=_CREATE_COMMAND_SCOPE,
                key_hash=key_hash,
                request_hash=request_hash,
            )

    async def create_regeneration_batch(
        self,
        principal: Principal,
        note_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> NoteBatchSnapshot:
        self._require_local_demo()
        if expected_version < 1:
            raise NoteBatchServiceError(
                NoteBatchServiceErrorCode.INVALID_REQUEST,
                "If-Match 必须指定正整数版本。",
            )
        key = _normalize_idempotency_key(idempotency_key)
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        request_hash = _canonical_hash(
            {
                "note_id": note_id,
                "target_note_version": expected_version,
            }
        )

        async with self._database.session(principal) as session:
            note_hint = await self._note_for_principal(session, principal, note_id)
            if note_hint is None:
                raise _not_found()
            course = await self._course_for_principal(
                session,
                principal,
                note_hint.course_id,
            )
            if course is None:
                raise _not_found()
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
                {
                    "lock_name": (
                        f"note-demo:{course.user_id}:{course.id}:"
                        f"{_REGENERATION_COMMAND_SCOPE}:{key_hash}"
                    )
                },
            )
            replay = await self._replay_or_none(
                session,
                course=course,
                command_scope=_REGENERATION_COMMAND_SCOPE,
                key_hash=key_hash,
                request_hash=request_hash,
            )
            if replay is not None:
                return await self._snapshot(session, replay)

            note = await self._note_for_principal(
                session,
                principal,
                note_id,
                for_update=True,
            )
            if note is None:
                raise _not_found()
            if note.version != expected_version:
                raise NoteBatchServiceError(
                    NoteBatchServiceErrorCode.VERSION_CONFLICT,
                    f"当前版本为 {note.version}",
                )
            target_version = await session.scalar(
                select(NoteContentVersionModel).where(
                    NoteContentVersionModel.note_id == note.id,
                    NoteContentVersionModel.version == expected_version,
                    NoteContentVersionModel.user_id == note.user_id,
                    NoteContentVersionModel.course_id == note.course_id,
                )
            )
            if target_version is None:
                raise NoteBatchServiceError(
                    NoteBatchServiceErrorCode.VERSION_NOT_FOUND,
                    "当前笔记版本缺少不可变快照, 无法安全重新生成。",
                )

            source_batch = await session.scalar(
                select(NoteGenerationBatchModel)
                .join(
                    NoteGenerationOutputModel,
                    and_(
                        NoteGenerationOutputModel.batch_id == NoteGenerationBatchModel.id,
                        NoteGenerationOutputModel.user_id == NoteGenerationBatchModel.user_id,
                        NoteGenerationOutputModel.course_id == NoteGenerationBatchModel.course_id,
                    ),
                )
                .where(
                    NoteGenerationOutputModel.note_id == note.id,
                    NoteGenerationOutputModel.user_id == note.user_id,
                    NoteGenerationOutputModel.course_id == note.course_id,
                    NoteGenerationOutputModel.note_version <= expected_version,
                    NoteGenerationBatchModel.command_kind.in_(
                        (
                            NoteBatchCommandKind.CREATE.value,
                            NoteBatchCommandKind.REGENERATION.value,
                        )
                    ),
                    NoteGenerationBatchModel.mode == NoteBatchMode.MERGED.value,
                    NoteGenerationBatchModel.status == NoteBatchStatus.SUCCEEDED.value,
                )
                .order_by(
                    NoteGenerationOutputModel.note_version.desc(),
                    NoteGenerationOutputModel.created_at.desc(),
                    NoteGenerationOutputModel.id.desc(),
                )
                .limit(1)
            )
            if source_batch is None:
                raise NoteBatchServiceError(
                    NoteBatchServiceErrorCode.INVALID_REQUEST,
                    "仅支持重新生成由当前批次工作流创建的笔记。",
                )
            document_ids = tuple(
                await session.scalars(
                    select(NoteGenerationInputModel.document_id)
                    .where(
                        NoteGenerationInputModel.batch_id == source_batch.id,
                        NoteGenerationInputModel.user_id == source_batch.user_id,
                        NoteGenerationInputModel.course_id == source_batch.course_id,
                    )
                    .order_by(NoteGenerationInputModel.ordinal)
                )
            )
            if not document_ids:
                raise NoteBatchServiceError(
                    NoteBatchServiceErrorCode.INVALID_REQUEST,
                    "原始笔记任务缺少资料快照。",
                )
            if len(document_ids) > self._settings.note_batch_max_documents:
                raise NoteBatchServiceError(
                    NoteBatchServiceErrorCode.REQUEST_LIMIT_EXCEEDED,
                    "原始笔记的资料数量超过当前批次上限。",
                )
            frozen_inputs = await self._freeze_inputs(session, course, document_ids)
            return await self._persist_batch(
                session,
                course=course,
                frozen_inputs=frozen_inputs,
                command_kind=NoteBatchCommandKind.REGENERATION.value,
                style=source_batch.style,
                title=note.title,
                section_path=tuple(note.section_path),
                target_note_id=note.id,
                target_note_version=expected_version,
                target_note_version_sha256=target_version.note_version_sha256,
                command_scope=_REGENERATION_COMMAND_SCOPE,
                key_hash=key_hash,
                request_hash=request_hash,
            )

    async def _persist_batch(
        self,
        session: AsyncSession,
        *,
        course: CourseModel,
        frozen_inputs: tuple[_FrozenInput, ...],
        command_kind: str,
        style: str,
        title: str | None,
        section_path: tuple[str, ...],
        target_note_id: str | None,
        target_note_version: int | None,
        target_note_version_sha256: str | None,
        command_scope: str,
        key_hash: str,
        request_hash: str,
    ) -> NoteBatchSnapshot:
        now = self._now()
        batch = NoteGenerationBatchModel(
            id=new_id(),
            user_id=course.user_id,
            course_id=course.id,
            command_kind=command_kind,
            mode=NoteBatchMode.MERGED.value,
            style=style,
            retry_of_batch_id=None,
            title=title,
            title_prefix=None,
            section_path=list(section_path),
            target_note_id=target_note_id,
            target_note_version=target_note_version,
            target_note_version_sha256=target_note_version_sha256,
            status=NoteBatchStatus.QUEUED.value,
            state_version=1,
            event_sequence=0,
            cancel_epoch=0,
            created_at=now,
            updated_at=now,
        )
        session.add(batch)
        await session.flush()

        input_rows: list[NoteGenerationInputModel] = []
        for ordinal, frozen in enumerate(frozen_inputs, start=1):
            input_row = NoteGenerationInputModel(
                id=new_id(),
                batch_id=batch.id,
                user_id=batch.user_id,
                course_id=batch.course_id,
                ordinal=ordinal,
                document_id=frozen.document_id,
                revision_id=frozen.revision_id,
                deletion_epoch=frozen.deletion_epoch,
                document_name=frozen.document_name,
                media_type=frozen.media_type,
                content_sha256=frozen.content_sha256,
                index_manifest_at_submit=frozen.index_manifest_at_submit,
                created_at=now,
            )
            session.add(input_row)
            input_rows.append(input_row)
        await session.flush()

        for frozen, input_row in zip(frozen_inputs, input_rows, strict=True):
            for unit in frozen.units:
                session.add(
                    NoteCoverageUnitModel(
                        id=new_id(),
                        input_id=input_row.id,
                        batch_id=batch.id,
                        user_id=batch.user_id,
                        course_id=batch.course_id,
                        ordinal=unit.ordinal,
                        unit_type=unit.unit_type,
                        locator=unit.locator,
                        content_sha256=unit.content_sha256,
                        is_substantive=unit.is_substantive,
                        created_at=now,
                    )
                )

        item = NoteGenerationItemModel(
            id=new_id(),
            batch_id=batch.id,
            user_id=batch.user_id,
            course_id=batch.course_id,
            ordinal=1,
            status=NoteItemStatus.QUEUED.value,
            phase=None,
            state_version=1,
            attempt=0,
            max_attempts=1,
            available_at=now,
            lease_version=0,
            cancel_epoch=0,
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        await session.flush()
        for ordinal, input_row in enumerate(input_rows, start=1):
            session.add(
                NoteItemInputModel(
                    id=new_id(),
                    item_id=item.id,
                    input_id=input_row.id,
                    batch_id=batch.id,
                    user_id=batch.user_id,
                    course_id=batch.course_id,
                    ordinal=ordinal,
                    created_at=now,
                )
            )

        await self._append_event(
            session,
            batch,
            "note.batch.created",
            {
                "batch_id": batch.id,
                "status": batch.status,
                "command_kind": batch.command_kind,
            },
            now,
        )
        session.add(
            NoteCommandDedupModel(
                id=new_id(),
                user_id=batch.user_id,
                course_id=batch.course_id,
                command_scope=command_scope,
                key_hash=key_hash,
                request_hash=request_hash,
                result_type="note_batch",
                result_id=batch.id,
                response_status=202,
                expires_at=now
                + timedelta(seconds=self._settings.note_command_dedup_retention_seconds),
                created_at=now,
            )
        )
        await session.flush()
        return await self._snapshot(session, batch)

    async def get_batch(self, principal: Principal, batch_id: str) -> NoteBatchSnapshot:
        async with self._database.session(principal) as session:
            batch = await self._batch_for_principal(session, principal, batch_id)
            if batch is None:
                raise _not_found()
            return await self._snapshot(session, batch)

    async def _freeze_inputs(
        self,
        session: AsyncSession,
        course: CourseModel,
        document_ids: Sequence[str],
    ) -> tuple[_FrozenInput, ...]:
        if not document_ids or len(document_ids) != len(set(document_ids)):
            raise NoteBatchServiceError(
                NoteBatchServiceErrorCode.INVALID_REQUEST,
                "document_ids 必须非空且不能重复。",
            )
        documents = list(
            await session.scalars(
                select(DocumentModel)
                .where(
                    DocumentModel.user_id == course.user_id,
                    DocumentModel.course_id == course.id,
                    DocumentModel.id.in_(document_ids),
                    DocumentModel.deleted_at.is_(None),
                )
                .with_for_update(of=DocumentModel)
            )
        )
        documents_by_id = {document.id: document for document in documents}
        if len(documents_by_id) != len(document_ids):
            raise _not_found()
        ordered_documents = [documents_by_id[document_id] for document_id in document_ids]
        for document in ordered_documents:
            if document.filename.lower().endswith(".ppt") or document.media_type == _PPT:
                raise NoteBatchServiceError(
                    NoteBatchServiceErrorCode.UNSUPPORTED_MEDIA_TYPE,
                    "暂不支持旧版 .ppt, 请先转换为 .pptx。",
                )
            if document.media_type not in {_PDF, _PPTX}:
                raise NoteBatchServiceError(
                    NoteBatchServiceErrorCode.UNSUPPORTED_MEDIA_TYPE,
                    "当前演示仅支持 PDF 和 PPTX 资料。",
                )
            if (
                document.corpus_role != "corpus"
                or document.status != "ready"
                or document.review_status != "approved"
                or document.active_revision_id is None
            ):
                raise _document_not_ready()

        revision_ids = [cast(str, document.active_revision_id) for document in ordered_documents]
        revisions = list(
            await session.scalars(
                select(DocumentRevisionModel)
                .where(DocumentRevisionModel.id.in_(revision_ids))
                .with_for_update(of=DocumentRevisionModel)
            )
        )
        revisions_by_id = {revision.id: revision for revision in revisions}
        if len(revisions_by_id) != len(revision_ids):
            raise _document_not_ready()
        if any(
            revisions_by_id[revision_id].document_id != document.id
            for revision_id, document in zip(revision_ids, ordered_documents, strict=True)
        ):
            raise _document_not_ready()

        chunks = list(
            await session.scalars(
                select(RevisionChunkModel)
                .where(RevisionChunkModel.revision_id.in_(revision_ids))
                .order_by(
                    RevisionChunkModel.revision_id,
                    RevisionChunkModel.page_ordinal,
                    RevisionChunkModel.ordinal,
                    RevisionChunkModel.id,
                )
            )
        )
        chunks_by_revision_page: dict[tuple[str, int], list[RevisionChunkModel]] = defaultdict(list)
        for chunk in chunks:
            chunks_by_revision_page[(chunk.revision_id, chunk.page_ordinal)].append(chunk)
        if any(
            not any(chunk.revision_id == revision_id and chunk.text.strip() for chunk in chunks)
            for revision_id in revision_ids
        ):
            raise _document_not_ready()

        total_pages = sum(
            revisions_by_id[revision_id].total_page_count for revision_id in revision_ids
        )
        total_tokens = sum(chunk.token_count_estimate for chunk in chunks)
        if total_pages > self._settings.note_batch_max_pages:
            raise _limit_error("所选资料总页数超过当前批次上限。")
        if total_tokens > self._settings.note_batch_max_estimated_tokens:
            raise _limit_error("所选资料的估算文本量超过当前批次上限。")

        manifest_hash = _canonical_hash(
            {
                "kind": "local-source-revisions",
                "revision_ids": revision_ids,
                "content_hashes": [
                    revisions_by_id[revision_id].canonical_sha256 for revision_id in revision_ids
                ],
            }
        )
        frozen_inputs: list[_FrozenInput] = []
        total_units = 0
        for document in ordered_documents:
            revision = revisions_by_id[cast(str, document.active_revision_id)]
            unit_type = "slide" if document.media_type == _PPTX else "pdf_page_window"
            locator_prefix = "slide" if document.media_type == _PPTX else "page"
            units: list[_FrozenUnit] = []
            for page_ordinal in range(1, revision.total_page_count + 1):
                page_chunks = chunks_by_revision_page.get((revision.id, page_ordinal), [])
                units.append(
                    _FrozenUnit(
                        ordinal=page_ordinal,
                        unit_type=unit_type,
                        locator=f"{locator_prefix}:{page_ordinal}",
                        content_sha256=_canonical_hash(
                            {
                                "revision_id": revision.id,
                                "page_ordinal": page_ordinal,
                                "chunk_hashes": [chunk.content_sha256 for chunk in page_chunks],
                            }
                        ),
                        is_substantive=any(chunk.text.strip() for chunk in page_chunks),
                    )
                )
            total_units += len(units)
            frozen_inputs.append(
                _FrozenInput(
                    document_id=document.id,
                    revision_id=revision.id,
                    deletion_epoch=document.deletion_epoch,
                    document_name=document.filename,
                    media_type=document.media_type,
                    content_sha256=_valid_hash_or_digest(
                        revision.canonical_sha256,
                        {"revision_id": revision.id},
                    ),
                    index_manifest_at_submit=manifest_hash,
                    units=tuple(units),
                )
            )
        if total_units > self._settings.note_batch_max_coverage_units:
            raise _limit_error("所选资料的覆盖单元数量超过当前批次上限。")
        return tuple(frozen_inputs)

    async def _replay_or_none(
        self,
        session: AsyncSession,
        *,
        course: CourseModel,
        command_scope: str,
        key_hash: str,
        request_hash: str,
    ) -> NoteGenerationBatchModel | None:
        record = await session.scalar(
            select(NoteCommandDedupModel)
            .where(
                NoteCommandDedupModel.user_id == course.user_id,
                NoteCommandDedupModel.course_id == course.id,
                NoteCommandDedupModel.command_scope == command_scope,
                NoteCommandDedupModel.key_hash == key_hash,
            )
            .with_for_update(of=NoteCommandDedupModel)
        )
        if record is None:
            return None
        if record.request_hash != request_hash:
            raise NoteBatchServiceError(
                NoteBatchServiceErrorCode.IDEMPOTENCY_CONFLICT,
                "同一个 Idempotency-Key 已用于不同请求。",
            )
        if record.expires_at <= self._now():
            await session.delete(record)
            await session.flush()
            return None
        if record.result_type != "note_batch":
            raise NoteBatchServiceError(
                NoteBatchServiceErrorCode.IDEMPOTENCY_CONFLICT,
                "幂等记录类型与当前请求不一致。",
            )
        batch = await session.scalar(
            select(NoteGenerationBatchModel).where(
                NoteGenerationBatchModel.id == record.result_id,
                NoteGenerationBatchModel.user_id == course.user_id,
                NoteGenerationBatchModel.course_id == course.id,
            )
        )
        if batch is None:
            raise _not_found()
        return batch

    async def _snapshot(
        self,
        session: AsyncSession,
        batch: NoteGenerationBatchModel,
    ) -> NoteBatchSnapshot:
        await session.flush()
        inputs = list(
            await session.scalars(
                select(NoteGenerationInputModel)
                .where(
                    NoteGenerationInputModel.batch_id == batch.id,
                    NoteGenerationInputModel.user_id == batch.user_id,
                    NoteGenerationInputModel.course_id == batch.course_id,
                )
                .order_by(NoteGenerationInputModel.ordinal)
            )
        )
        units = list(
            await session.scalars(
                select(NoteCoverageUnitModel)
                .join(
                    NoteGenerationInputModel,
                    (NoteGenerationInputModel.id == NoteCoverageUnitModel.input_id)
                    & (NoteGenerationInputModel.batch_id == NoteCoverageUnitModel.batch_id)
                    & (NoteGenerationInputModel.user_id == NoteCoverageUnitModel.user_id)
                    & (NoteGenerationInputModel.course_id == NoteCoverageUnitModel.course_id),
                )
                .where(
                    NoteCoverageUnitModel.batch_id == batch.id,
                    NoteCoverageUnitModel.user_id == batch.user_id,
                    NoteCoverageUnitModel.course_id == batch.course_id,
                )
                .order_by(
                    NoteGenerationInputModel.ordinal,
                    NoteCoverageUnitModel.ordinal,
                )
            )
        )
        items = list(
            await session.scalars(
                select(NoteGenerationItemModel)
                .where(
                    NoteGenerationItemModel.batch_id == batch.id,
                    NoteGenerationItemModel.user_id == batch.user_id,
                    NoteGenerationItemModel.course_id == batch.course_id,
                )
                .order_by(NoteGenerationItemModel.ordinal)
            )
        )
        links = list(
            await session.scalars(
                select(NoteItemInputModel)
                .where(
                    NoteItemInputModel.batch_id == batch.id,
                    NoteItemInputModel.user_id == batch.user_id,
                    NoteItemInputModel.course_id == batch.course_id,
                )
                .order_by(NoteItemInputModel.item_id, NoteItemInputModel.ordinal)
            )
        )
        outputs = list(
            await session.scalars(
                select(NoteGenerationOutputModel).where(
                    NoteGenerationOutputModel.batch_id == batch.id,
                    NoteGenerationOutputModel.user_id == batch.user_id,
                    NoteGenerationOutputModel.course_id == batch.course_id,
                )
            )
        )
        results = list(
            await session.scalars(
                select(NoteCoverageUnitResultModel)
                .where(
                    NoteCoverageUnitResultModel.batch_id == batch.id,
                    NoteCoverageUnitResultModel.user_id == batch.user_id,
                    NoteCoverageUnitResultModel.course_id == batch.course_id,
                )
                .order_by(
                    NoteCoverageUnitResultModel.unit_id,
                    NoteCoverageUnitResultModel.attempt.desc(),
                )
            )
        )
        input_ids_by_item: dict[str, list[str]] = defaultdict(list)
        for link in links:
            input_ids_by_item[link.item_id].append(link.input_id)
        note_id_by_item = {output.item_id: output.note_id for output in outputs}
        result_by_unit: dict[str, NoteCoverageUnitResultModel] = {}
        for result in results:
            result_by_unit.setdefault(result.unit_id, result)

        now = self._now()
        item_snapshots: list[NoteItemSnapshot] = []
        for item in items:
            terminal = item.status in _TERMINAL_ITEM_STATUSES
            if terminal:
                eta_reason = EtaUnavailableReason.TERMINAL
            elif item.status == NoteItemStatus.QUEUED.value:
                eta_reason = EtaUnavailableReason.NOT_STARTED
            else:
                eta_reason = EtaUnavailableReason.INSUFFICIENT_HISTORY
            elapsed_from = item.started_at or item.created_at
            elapsed_to = item.completed_at or now
            item_snapshots.append(
                NoteItemSnapshot(
                    id=item.id,
                    input_ids=input_ids_by_item[item.id],
                    status=NoteItemStatus(item.status),
                    phase=item.phase,
                    elapsed_seconds=max(0, int((elapsed_to - elapsed_from).total_seconds())),
                    eta=None,
                    eta_unavailable_reason=eta_reason,
                    attempt=item.attempt,
                    note_id=note_id_by_item.get(item.id),
                    failure_code=item.failure_code,
                    retryable_in_new_batch=bool(item.retryable),
                )
            )

        coverage_snapshots: list[CoverageUnitSnapshot] = []
        for unit in units:
            unit_result = result_by_unit.get(unit.id)
            coverage_snapshots.append(
                CoverageUnitSnapshot(
                    id=unit.id,
                    input_id=unit.input_id,
                    ordinal=unit.ordinal,
                    unit_type=CoverageUnitType(unit.unit_type),
                    locator=unit.locator,
                    status=(
                        CoverageUnitStatus.PENDING
                        if unit_result is None
                        else CoverageUnitStatus(unit_result.status)
                    ),
                    reason_code=None if unit_result is None else unit_result.reason_code,
                )
            )

        return NoteBatchSnapshot(
            id=batch.id,
            command_kind=NoteBatchCommandKind(batch.command_kind),
            retry_of_batch_id=batch.retry_of_batch_id,
            course_id=batch.course_id,
            mode=NoteBatchMode(batch.mode),
            style=NoteBatchStyle(batch.style),
            title=batch.title,
            title_prefix=batch.title_prefix,
            section_path=list(batch.section_path),
            target_note_id=batch.target_note_id,
            target_note_version=batch.target_note_version,
            target_note_version_sha256=batch.target_note_version_sha256,
            status=NoteBatchStatus(batch.status),
            completed_items=sum(item.status in _TERMINAL_ITEM_STATUSES for item in items),
            total_items=len(items),
            inputs=[
                NoteInputSnapshot(
                    id=input_row.id,
                    ordinal=input_row.ordinal,
                    document_id=input_row.document_id,
                    revision_id=input_row.revision_id,
                    deletion_epoch=input_row.deletion_epoch,
                    document_name=input_row.document_name,
                    media_type=input_row.media_type,
                    content_sha256=input_row.content_sha256,
                    index_manifest_at_submit=input_row.index_manifest_at_submit,
                )
                for input_row in inputs
            ],
            coverage_units=coverage_snapshots,
            items=item_snapshots,
            last_event_sequence=batch.event_sequence,
            created_at=batch.created_at,
            started_at=batch.started_at,
            completed_at=batch.completed_at,
        )

    async def _course_for_principal(
        self,
        session: AsyncSession,
        principal: Principal,
        course_id: str,
    ) -> CourseModel | None:
        return cast(
            CourseModel | None,
            await session.scalar(
                select(CourseModel)
                .join(UserModel, UserModel.id == CourseModel.user_id)
                .where(
                    CourseModel.id == course_id,
                    CourseModel.lifecycle == "active",
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
                .with_for_update(of=CourseModel)
            ),
        )

    async def _note_for_principal(
        self,
        session: AsyncSession,
        principal: Principal,
        note_id: str,
        *,
        for_update: bool = False,
    ) -> NoteModel | None:
        statement = (
            select(NoteModel)
            .join(
                CourseModel,
                (CourseModel.id == NoteModel.course_id)
                & (CourseModel.user_id == NoteModel.user_id),
            )
            .join(UserModel, UserModel.id == NoteModel.user_id)
            .where(
                NoteModel.id == note_id,
                CourseModel.lifecycle == "active",
                CourseModel.deleted_at.is_(None),
                UserModel.subject == principal.subject,
                UserModel.authentication_method == principal.authentication_method.value,
            )
        )
        if for_update:
            statement = statement.with_for_update(of=NoteModel)
        return cast(NoteModel | None, await session.scalar(statement))

    async def _batch_for_principal(
        self,
        session: AsyncSession,
        principal: Principal,
        batch_id: str,
    ) -> NoteGenerationBatchModel | None:
        return cast(
            NoteGenerationBatchModel | None,
            await session.scalar(
                select(NoteGenerationBatchModel)
                .join(
                    CourseModel,
                    (CourseModel.id == NoteGenerationBatchModel.course_id)
                    & (CourseModel.user_id == NoteGenerationBatchModel.user_id),
                )
                .join(UserModel, UserModel.id == CourseModel.user_id)
                .where(
                    NoteGenerationBatchModel.id == batch_id,
                    NoteGenerationBatchModel.command_kind.in_(
                        (
                            NoteBatchCommandKind.CREATE.value,
                            NoteBatchCommandKind.REGENERATION.value,
                        )
                    ),
                    NoteGenerationBatchModel.mode == NoteBatchMode.MERGED.value,
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            ),
        )

    async def _append_event(
        self,
        session: AsyncSession,
        batch: NoteGenerationBatchModel,
        event_type: str,
        payload: Mapping[str, object],
        now: datetime,
    ) -> None:
        batch.event_sequence += 1
        batch.state_version += 1
        batch.updated_at = now
        session.add(
            NoteGenerationEventModel(
                id=new_id(),
                batch_id=batch.id,
                user_id=batch.user_id,
                course_id=batch.course_id,
                sequence=batch.event_sequence,
                event_type=event_type,
                state_version=batch.state_version,
                payload=dict(payload),
                trace_id=get_trace_id() or new_trace_id(),
                occurred_at=now,
                expires_at=now + timedelta(seconds=self._settings.note_event_retention_seconds),
            )
        )

    def _require_local_demo(self) -> None:
        if not self._settings.demo_lab_enabled or self._settings.app_mode not in {
            AppMode.LOCAL,
            AppMode.TEST,
        }:
            raise NoteBatchServiceError(
                NoteBatchServiceErrorCode.DEMO_UNAVAILABLE,
                "本地来源派生笔记演示未启用。",
            )

    def _now(self) -> datetime:
        value = self._clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _normalize_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 512:
        raise NoteBatchServiceError(
            NoteBatchServiceErrorCode.INVALID_REQUEST,
            "Idempotency-Key 必须为非空字符串。",
        )
    return normalized


def _canonical_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_hash_or_digest(value: str, payload: Mapping[str, object]) -> str:
    if len(value) == 64 and all(character in "0123456789abcdef" for character in value):
        return value
    return _canonical_hash(payload)


def _not_found() -> NoteBatchServiceError:
    return NoteBatchServiceError(
        NoteBatchServiceErrorCode.NOT_FOUND,
        "请求的课程、资料或批次不存在。",
    )


def _document_not_ready() -> NoteBatchServiceError:
    return NoteBatchServiceError(
        NoteBatchServiceErrorCode.DOCUMENT_NOT_READY,
        "所选资料尚未完成解析, 或当前修订没有可用文本。",
    )


def _limit_error(detail: str) -> NoteBatchServiceError:
    return NoteBatchServiceError(NoteBatchServiceErrorCode.REQUEST_LIMIT_EXCEEDED, detail)


__all__ = [
    "NoteBatchService",
    "NoteBatchServiceError",
    "NoteBatchServiceErrorCode",
]
