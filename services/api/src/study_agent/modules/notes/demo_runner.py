"""Provider-free, in-process runner for the note workflow demonstration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.config import AppMode, Settings
from study_agent.infrastructure.db.models import (
    DocumentModel,
    DocumentRevisionModel,
    NoteContentVersionModel,
    NoteCoverageUnitModel,
    NoteCoverageUnitResultModel,
    NoteGenerationAttemptModel,
    NoteGenerationBatchModel,
    NoteGenerationEventModel,
    NoteGenerationInputModel,
    NoteGenerationItemModel,
    NoteGenerationOutputModel,
    NoteItemInputModel,
    NoteModel,
    NoteSourceModel,
    RevisionChunkModel,
)
from study_agent.infrastructure.db.models.core import new_id
from study_agent.infrastructure.db.session import Database
from study_agent.observability.trace import new_trace_id
from study_agent.providers.protocols import Clock
from study_contracts import NoteBatchStyle

_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_EXAM_POINTS_PER_PAGE = 2
_EXAM_POINTS_PER_NOTE = 12
_EXAM_EXCERPT_CHARS = 96
_OUTLINE_POINTS_PER_PAGE = 3
_OUTLINE_POINTS_PER_NOTE = 30
_OUTLINE_EXCERPT_CHARS = 72
_HIGH_VALUE_MARKERS = (
    "定义",
    "概念",
    "核心",
    "关键",
    "必须",
    "原则",
    "公式",
    "定理",
    "结论",
    "条件",
    "步骤",
    "原因",
    "结果",
    "区别",
    "作用",
    "特点",
    "目标",
    "方法",
    "注意",
)
_CJK_SENTENCE_ENDINGS = "\u3002\uff01\uff1f\uff1b"
_SOURCE_SENTENCE = re.compile(rf"[^{_CJK_SENTENCE_ENDINGS}!?;\n]+[{_CJK_SENTENCE_ENDINGS}!?;]?")


@dataclass(frozen=True, slots=True)
class _SourceChunk:
    input_id: str
    document_id: str
    revision_id: str
    deletion_epoch: int
    document_name: str
    media_type: str
    chunk_id: str
    ordinal: int
    text: str
    page_ordinal: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class _RenderEntry:
    chunk: _SourceChunk
    text: str
    source_index: int
    sentence_index: int = 0


@dataclass(frozen=True, slots=True)
class _RenderedEntry:
    chunk: _SourceChunk
    text: str
    ast_node_id: str


@dataclass(frozen=True, slots=True)
class _RenderedNote:
    body_markdown: str
    content_ast: dict[str, Any]
    entries: tuple[_RenderedEntry, ...]


@dataclass(frozen=True, slots=True)
class _CoverageDisposition:
    status: str
    reason_code: str | None
    evidence_set_sha256: str | None
    chunk_ids: tuple[str, ...]
    ast_node_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _FrozenInput:
    input_id: str
    document_id: str
    revision_id: str
    deletion_epoch: int
    content_sha256: str


@dataclass(frozen=True, slots=True)
class _Material:
    title: str
    style: NoteBatchStyle
    section_path: list[str]
    inputs: tuple[_FrozenInput, ...]
    chunks: tuple[_SourceChunk, ...]
    units: tuple[NoteCoverageUnitModel, ...]


class _SourceChangedError(RuntimeError):
    """The selected source no longer matches the batch's frozen snapshot."""


class DemoNoteRunner:
    """Run one merged batch in the API process without a provider or queue."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        clock: Clock,
        *,
        runner_id: str = "local-note-demo",
        phase_delay_seconds: float | None = None,
    ) -> None:
        normalized = runner_id.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("runner_id must be between 1 and 128 characters")
        self._database = database
        self._settings = settings
        self._clock = clock
        self._runner_id = normalized
        self._phase_delay_seconds = (
            self._settings.note_demo_phase_delay_seconds
            if phase_delay_seconds is None
            else max(0.0, min(5.0, phase_delay_seconds))
        )
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def schedule(self, batch_id: str) -> None:
        """Schedule work after the command transaction has committed."""

        if batch_id in self._tasks:
            return
        task = asyncio.create_task(self._run_batch(batch_id))
        self._tasks[batch_id] = task

        def _forget(done: asyncio.Task[None]) -> None:
            self._tasks.pop(batch_id, None)
            # ``_run_batch`` records failures durably.  Retrieving the result
            # keeps unexpected cancellation/errors from becoming warnings.
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        task.add_done_callback(_forget)

    async def recover_pending(self) -> int:
        """Resume durable local-demo batches left pending by a prior process."""

        if not self._settings.demo_lab_enabled or self._settings.app_mode not in {
            AppMode.LOCAL,
            AppMode.TEST,
        }:
            return 0
        async with self._database.worker_session(self._runner_id) as session:
            batch_ids = tuple(
                await session.scalars(
                    select(NoteGenerationBatchModel.id)
                    .where(
                        NoteGenerationBatchModel.status.in_(("queued", "running")),
                        NoteGenerationBatchModel.command_kind == "create",
                        NoteGenerationBatchModel.mode == "merged",
                    )
                    .order_by(NoteGenerationBatchModel.created_at, NoteGenerationBatchModel.id)
                )
            )
        for batch_id in batch_ids:
            self.schedule(batch_id)
        return len(batch_ids)

    async def shutdown(self) -> None:
        """Cancel and collect process-local tasks during application shutdown."""

        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def run_once(self, batch_id: str | None = None) -> str | None:
        """Run a selected batch, or the oldest queued batch, to completion.

        Tests and local tooling can call this method directly.  If a scheduled
        task already owns the selected batch, this waits for that task instead
        of starting duplicate work.
        """

        selected_id: str | None
        if batch_id is not None:
            scheduled = self._tasks.get(batch_id)
            if scheduled is not None and scheduled is not asyncio.current_task():
                await scheduled
                return batch_id
            selected_id = batch_id
        else:
            async with self._database.worker_session(self._runner_id) as session:
                selected_id = await session.scalar(
                    select(NoteGenerationBatchModel.id)
                    .where(
                        NoteGenerationBatchModel.status.in_(("queued", "running")),
                        NoteGenerationBatchModel.command_kind == "create",
                        NoteGenerationBatchModel.mode == "merged",
                    )
                    .order_by(NoteGenerationBatchModel.created_at, NoteGenerationBatchModel.id)
                    .limit(1)
                )
            if selected_id is None:
                return None
        await self._run_batch(selected_id)
        return selected_id

    async def _run_batch(self, batch_id: str) -> None:
        started = await self._start_batch(batch_id)
        if not started:
            return
        try:
            # Yield between durable phases so a client polling the snapshot can
            # observe real progress even though each local phase is fast.
            await asyncio.sleep(self._phase_delay_seconds)
            await self._set_phase(batch_id, "retrieving")
            material = await self._load_material(batch_id)
            await asyncio.sleep(self._phase_delay_seconds)
            await self._set_phase(batch_id, "generating")
            rendered = _render_demo_note(material)
            await asyncio.sleep(self._phase_delay_seconds)
            await self._set_phase(batch_id, "saving")
            await asyncio.sleep(self._phase_delay_seconds)
            await self._save_success(batch_id, material, rendered)
        except asyncio.CancelledError:
            raise
        except _SourceChangedError:
            await self._mark_failure(
                batch_id,
                failure_code="NOTE_SOURCE_CHANGED",
                failure_summary="所选资料在任务创建后发生变化, 请重新创建笔记任务。",
                retryable_in_new_batch=True,
            )
        except Exception:
            await self._mark_failure(
                batch_id,
                failure_code="DEMO_RUNNER_FAILED",
                failure_summary="本地演示运行失败。",
                retryable_in_new_batch=False,
            )

    async def _start_batch(self, batch_id: str) -> bool:
        async with self._database.worker_session(self._runner_id) as session:
            batch = await session.scalar(
                select(NoteGenerationBatchModel)
                .where(NoteGenerationBatchModel.id == batch_id)
                .with_for_update(of=NoteGenerationBatchModel)
            )
            if batch is None or batch.status in {
                "succeeded",
                "failed",
                "cancelled",
                "partial_success",
            }:
                return False
            item = await session.scalar(
                select(NoteGenerationItemModel)
                .where(
                    NoteGenerationItemModel.batch_id == batch.id,
                    NoteGenerationItemModel.user_id == batch.user_id,
                    NoteGenerationItemModel.course_id == batch.course_id,
                )
                .order_by(NoteGenerationItemModel.ordinal)
                .with_for_update(of=NoteGenerationItemModel)
            )
            if item is None or item.status in {"succeeded", "failed", "cancelled"}:
                return False
            now = self._now()
            if batch.status == "queued":
                batch.status = "running"
                batch.started_at = now
                batch.state_version += 1
                batch.updated_at = now
                await self._append_event(
                    session,
                    batch,
                    "note.batch.running",
                    {"status": batch.status},
                    now,
                )
            if item.attempt == 0:
                item.attempt = 1
                item.started_at = now
                item.phase = "validating_inputs"
                item.status = "leased"
                item.lease_owner_id = self._runner_id
                item.lease_version += 1
                item.lease_token_hash = hashlib.sha256(
                    f"{batch.id}:{item.id}:{self._runner_id}".encode()
                ).hexdigest()
                item.lease_expires_at = now + timedelta(
                    seconds=self._settings.note_runner_lease_seconds
                )
                item.heartbeat_at = now
                item.state_version += 1
                item.updated_at = now
                session.add(
                    NoteGenerationAttemptModel(
                        id=new_id(),
                        item_id=item.id,
                        batch_id=batch.id,
                        user_id=batch.user_id,
                        course_id=batch.course_id,
                        attempt=item.attempt,
                        runner_id=self._runner_id,
                        provider_alias="local-demo",
                        provider_model="source-derived-v1",
                        contract_version="1.0",
                        usage={},
                        started_at=now,
                        created_at=now,
                    )
                )
                await self._append_event(
                    session,
                    batch,
                    "note.item.leased",
                    {"item_id": item.id, "attempt": item.attempt},
                    now,
                )
                item.status = "running"
                item.state_version += 1
                await self._append_event(
                    session,
                    batch,
                    "note.item.running",
                    {"item_id": item.id, "phase": item.phase},
                    now,
                )
            return True

    async def _set_phase(self, batch_id: str, phase: str) -> None:
        async with self._database.worker_session(self._runner_id) as session:
            batch = await session.scalar(
                select(NoteGenerationBatchModel)
                .where(NoteGenerationBatchModel.id == batch_id)
                .with_for_update(of=NoteGenerationBatchModel)
            )
            if batch is None:
                raise RuntimeError("note batch disappeared")
            item = await session.scalar(
                select(NoteGenerationItemModel)
                .where(
                    NoteGenerationItemModel.batch_id == batch.id,
                    NoteGenerationItemModel.user_id == batch.user_id,
                    NoteGenerationItemModel.course_id == batch.course_id,
                )
                .order_by(NoteGenerationItemModel.ordinal)
                .with_for_update(of=NoteGenerationItemModel)
            )
            if item is None or item.status in {"succeeded", "failed", "cancelled"}:
                return
            item.phase = phase
            item.state_version += 1
            item.updated_at = self._now()
            await self._append_event(
                session,
                batch,
                "note.item.phase",
                {"item_id": item.id, "phase": phase},
                item.updated_at,
            )

    async def _load_material(self, batch_id: str) -> _Material:
        async with self._database.worker_session(self._runner_id) as session:
            batch = await session.scalar(
                select(NoteGenerationBatchModel).where(NoteGenerationBatchModel.id == batch_id)
            )
            if batch is None:
                raise RuntimeError("note batch disappeared")
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
            if not inputs:
                raise RuntimeError("note batch has no frozen inputs")
            units = tuple(
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
            chunks = list(
                await session.scalars(
                    select(RevisionChunkModel)
                    .where(
                        RevisionChunkModel.revision_id.in_([item.revision_id for item in inputs])
                    )
                    .order_by(
                        RevisionChunkModel.revision_id,
                        RevisionChunkModel.page_ordinal,
                        RevisionChunkModel.ordinal,
                        RevisionChunkModel.id,
                    )
                )
            )
            _assert_frozen_units_unchanged(inputs, units, chunks)
            chunks_by_revision: dict[str, list[RevisionChunkModel]] = {}
            for chunk in chunks:
                chunks_by_revision.setdefault(chunk.revision_id, []).append(chunk)
            source_chunks = tuple(
                _SourceChunk(
                    input_id=input_row.id,
                    document_id=input_row.document_id,
                    revision_id=chunk.revision_id,
                    deletion_epoch=input_row.deletion_epoch,
                    document_name=input_row.document_name,
                    media_type=input_row.media_type,
                    chunk_id=chunk.id,
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    page_ordinal=chunk.page_ordinal,
                    content_sha256=chunk.content_sha256,
                )
                for input_row in inputs
                for chunk in chunks_by_revision.get(input_row.revision_id, [])
            )
            if not any(chunk.text.strip() for chunk in source_chunks):
                raise RuntimeError("frozen revisions contain no substantive text")
            return _Material(
                title=batch.title or "合并课程笔记",
                style=NoteBatchStyle(batch.style),
                section_path=list(batch.section_path),
                inputs=tuple(
                    _FrozenInput(
                        input_id=input_row.id,
                        document_id=input_row.document_id,
                        revision_id=input_row.revision_id,
                        deletion_epoch=input_row.deletion_epoch,
                        content_sha256=input_row.content_sha256,
                    )
                    for input_row in inputs
                ),
                chunks=source_chunks,
                units=units,
            )

    async def _save_success(
        self,
        batch_id: str,
        material: _Material,
        rendered: _RenderedNote,
    ) -> None:
        async with self._database.worker_session(self._runner_id) as session:
            batch = await session.scalar(
                select(NoteGenerationBatchModel)
                .where(NoteGenerationBatchModel.id == batch_id)
                .with_for_update(of=NoteGenerationBatchModel)
            )
            if batch is None:
                raise RuntimeError("note batch disappeared")
            item = await session.scalar(
                select(NoteGenerationItemModel)
                .where(
                    NoteGenerationItemModel.batch_id == batch.id,
                    NoteGenerationItemModel.user_id == batch.user_id,
                    NoteGenerationItemModel.course_id == batch.course_id,
                )
                .order_by(NoteGenerationItemModel.ordinal)
                .with_for_update(of=NoteGenerationItemModel)
            )
            if item is None or item.status in {"succeeded", "failed", "cancelled"}:
                return
            await self._assert_frozen_sources_unchanged(session, batch, material)
            now = self._now()
            note_id = new_id()
            body_sha256 = _sha256(rendered.body_markdown)
            selected_chunks = _unique_rendered_chunks(rendered.entries)
            source_payload = [
                {
                    "chunk_id": chunk.chunk_id,
                    "content_sha256": chunk.content_sha256,
                    "revision_id": chunk.revision_id,
                }
                for chunk in selected_chunks
            ]
            source_set_sha256 = _canonical_hash({"sources": source_payload})
            rendered_entries_by_unit: dict[tuple[str, int], list[_RenderedEntry]] = {}
            for entry in rendered.entries:
                rendered_entries_by_unit.setdefault(
                    (entry.chunk.input_id, entry.chunk.page_ordinal), []
                ).append(entry)
            coverage_by_unit_id: dict[str, _CoverageDisposition] = {}
            for unit in material.units:
                page = _page_from_locator(unit.locator)
                unit_entries = rendered_entries_by_unit.get((unit.input_id, page), [])
                chunk_ids = _unique_values(entry.chunk.chunk_id for entry in unit_entries)
                ast_node_ids = tuple(entry.ast_node_id for entry in unit_entries)
                covered = bool(unit_entries)
                has_source_text = any(
                    chunk.input_id == unit.input_id
                    and chunk.page_ordinal == page
                    and chunk.text.strip()
                    for chunk in material.chunks
                )
                coverage_by_unit_id[unit.id] = _CoverageDisposition(
                    status="covered" if covered else "skipped",
                    reason_code=(
                        None
                        if covered
                        else ("not_selected_for_style" if has_source_text else "empty_source_unit")
                    ),
                    evidence_set_sha256=(
                        _canonical_hash({"chunks": list(chunk_ids)}) if covered else None
                    ),
                    chunk_ids=chunk_ids,
                    ast_node_ids=ast_node_ids,
                )
            coverage_payload = [
                {
                    "id": unit.id,
                    "input_id": unit.input_id,
                    "ordinal": unit.ordinal,
                    "status": coverage_by_unit_id[unit.id].status,
                    "reason_code": coverage_by_unit_id[unit.id].reason_code,
                    "chunk_ids": list(coverage_by_unit_id[unit.id].chunk_ids),
                    "ast_node_ids": list(coverage_by_unit_id[unit.id].ast_node_ids),
                }
                for unit in material.units
            ]
            coverage_manifest_sha256 = _canonical_hash({"units": coverage_payload})
            version_sha256 = _canonical_hash(
                {
                    "note_id": note_id,
                    "version": 1,
                    "body_sha256": body_sha256,
                    "source_set_sha256": source_set_sha256,
                    "coverage_manifest_sha256": coverage_manifest_sha256,
                }
            )

            session.add(
                NoteModel(
                    id=note_id,
                    user_id=batch.user_id,
                    course_id=batch.course_id,
                    section_path=material.section_path,
                    title=material.title,
                    body_markdown=rendered.body_markdown,
                    version=1,
                    generation=1,
                    generated_by_model=False,
                    status="ready",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            for chunk in selected_chunks:
                session.add(
                    NoteSourceModel(
                        id=new_id(),
                        note_id=note_id,
                        user_id=batch.user_id,
                        course_id=batch.course_id,
                        evidence_id=chunk.chunk_id,
                        document_id=chunk.document_id,
                        revision_id=chunk.revision_id,
                        chunk_id=chunk.chunk_id,
                        document_name=chunk.document_name,
                        document_deletion_epoch=chunk.deletion_epoch,
                        content_sha256=_valid_hash(chunk.content_sha256, chunk.chunk_id),
                        locator={
                            "kind": ("slide" if chunk.media_type == _PPTX else "page"),
                            "ordinal": chunk.page_ordinal,
                        },
                        quote=chunk.text.strip(),
                        bounding_boxes=[],
                        provenance=["local-demo/source-derived-v1"],
                        available=True,
                        created_at=now,
                    )
                )
            session.add(
                NoteContentVersionModel(
                    note_id=note_id,
                    version=1,
                    user_id=batch.user_id,
                    course_id=batch.course_id,
                    title=material.title,
                    section_path=material.section_path,
                    body_markdown=rendered.body_markdown,
                    content_ast=rendered.content_ast,
                    ast_schema_version="1.0",
                    parser_version="local-demo-v1",
                    body_sha256=body_sha256,
                    source_set_sha256=source_set_sha256,
                    coverage_manifest_sha256=coverage_manifest_sha256,
                    note_version_sha256=version_sha256,
                    created_by="generated",
                    created_at=now,
                )
            )
            await session.flush()
            session.add(
                NoteGenerationOutputModel(
                    id=new_id(),
                    batch_id=batch.id,
                    item_id=item.id,
                    user_id=batch.user_id,
                    course_id=batch.course_id,
                    note_id=note_id,
                    note_version=1,
                    created_at=now,
                )
            )
            await session.flush()

            attempt = await session.scalar(
                select(NoteGenerationAttemptModel)
                .where(
                    NoteGenerationAttemptModel.item_id == item.id,
                    NoteGenerationAttemptModel.attempt == item.attempt,
                    NoteGenerationAttemptModel.batch_id == batch.id,
                    NoteGenerationAttemptModel.user_id == batch.user_id,
                    NoteGenerationAttemptModel.course_id == batch.course_id,
                )
                .with_for_update(of=NoteGenerationAttemptModel)
            )
            if attempt is not None:
                attempt.completed_at = now
                attempt.usage = {
                    "source_chunks": len(material.chunks),
                    "rendered_source_chunks": len(selected_chunks),
                    "provider_calls": 0,
                }

            links = list(
                await session.scalars(
                    select(NoteItemInputModel).where(
                        NoteItemInputModel.item_id == item.id,
                        NoteItemInputModel.batch_id == batch.id,
                        NoteItemInputModel.user_id == batch.user_id,
                        NoteItemInputModel.course_id == batch.course_id,
                    )
                )
            )
            input_ids = {link.input_id for link in links}
            for unit in material.units:
                if unit.input_id not in input_ids:
                    continue
                disposition = coverage_by_unit_id[unit.id]
                session.add(
                    NoteCoverageUnitResultModel(
                        id=new_id(),
                        item_id=item.id,
                        attempt=item.attempt,
                        input_id=unit.input_id,
                        unit_id=unit.id,
                        batch_id=batch.id,
                        user_id=batch.user_id,
                        course_id=batch.course_id,
                        status=disposition.status,
                        reason_code=disposition.reason_code,
                        evidence_set_sha256=disposition.evidence_set_sha256,
                        ast_node_ids=list(disposition.ast_node_ids),
                        created_at=now,
                    )
                )

            item.status = "succeeded"
            item.phase = "saving"
            item.completed_at = now
            item.failure_code = None
            item.failure_summary = None
            item.retryable = False
            item.lease_owner_id = None
            item.lease_token_hash = None
            item.lease_expires_at = None
            item.heartbeat_at = None
            item.lease_version += 1
            item.state_version += 1
            item.updated_at = now
            batch.status = "succeeded"
            batch.completed_at = now
            batch.state_version += 1
            batch.updated_at = now
            await self._append_event(
                session,
                batch,
                "note.item.succeeded",
                {"item_id": item.id, "note_id": note_id, "note_version": 1},
                now,
            )
            await self._append_event(
                session,
                batch,
                "note.batch.succeeded",
                {"batch_id": batch.id, "completed_items": 1},
                now,
            )

    async def _assert_frozen_sources_unchanged(
        self,
        session: AsyncSession,
        batch: NoteGenerationBatchModel,
        material: _Material,
    ) -> None:
        documents = list(
            await session.scalars(
                select(DocumentModel)
                .where(
                    DocumentModel.id.in_([item.document_id for item in material.inputs]),
                    DocumentModel.user_id == batch.user_id,
                    DocumentModel.course_id == batch.course_id,
                )
                .with_for_update(of=DocumentModel)
            )
        )
        documents_by_id = {document.id: document for document in documents}
        if len(documents_by_id) != len(material.inputs):
            raise _SourceChangedError
        for frozen in material.inputs:
            document = documents_by_id[frozen.document_id]
            if (
                document.deleted_at is not None
                or document.status != "ready"
                or document.review_status != "approved"
                or document.corpus_role != "corpus"
                or document.deletion_epoch != frozen.deletion_epoch
                or document.active_revision_id != frozen.revision_id
            ):
                raise _SourceChangedError

        revisions = list(
            await session.scalars(
                select(DocumentRevisionModel)
                .where(DocumentRevisionModel.id.in_([item.revision_id for item in material.inputs]))
                .with_for_update(of=DocumentRevisionModel)
            )
        )
        revisions_by_id = {revision.id: revision for revision in revisions}
        if len(revisions_by_id) != len(material.inputs):
            raise _SourceChangedError
        for frozen in material.inputs:
            revision = revisions_by_id[frozen.revision_id]
            content_sha256 = revision.canonical_sha256
            if not _is_sha256(content_sha256):
                content_sha256 = _canonical_hash({"revision_id": revision.id})
            if (
                revision.document_id != frozen.document_id
                or content_sha256 != frozen.content_sha256
            ):
                raise _SourceChangedError

        current_chunks = list(
            await session.scalars(
                select(RevisionChunkModel)
                .where(RevisionChunkModel.revision_id.in_(revisions_by_id))
                .order_by(
                    RevisionChunkModel.revision_id,
                    RevisionChunkModel.page_ordinal,
                    RevisionChunkModel.ordinal,
                    RevisionChunkModel.id,
                )
                .with_for_update(of=RevisionChunkModel)
            )
        )
        current_by_revision: dict[str, list[tuple[str, str, str, int, int]]] = {}
        for current_chunk in current_chunks:
            current_by_revision.setdefault(current_chunk.revision_id, []).append(
                (
                    current_chunk.id,
                    current_chunk.content_sha256,
                    current_chunk.text,
                    current_chunk.page_ordinal,
                    current_chunk.ordinal,
                )
            )
        frozen_by_revision: dict[str, list[tuple[str, str, str, int, int]]] = {}
        for frozen_chunk in material.chunks:
            frozen_by_revision.setdefault(frozen_chunk.revision_id, []).append(
                (
                    frozen_chunk.chunk_id,
                    frozen_chunk.content_sha256,
                    frozen_chunk.text,
                    frozen_chunk.page_ordinal,
                    frozen_chunk.ordinal,
                )
            )
        if any(
            current_by_revision.get(frozen.revision_id, [])
            != frozen_by_revision.get(frozen.revision_id, [])
            for frozen in material.inputs
        ):
            raise _SourceChangedError

    async def _mark_failure(
        self,
        batch_id: str,
        *,
        failure_code: str,
        failure_summary: str,
        retryable_in_new_batch: bool,
    ) -> None:
        try:
            async with self._database.worker_session(self._runner_id) as session:
                batch = await session.scalar(
                    select(NoteGenerationBatchModel)
                    .where(NoteGenerationBatchModel.id == batch_id)
                    .with_for_update(of=NoteGenerationBatchModel)
                )
                if batch is None or batch.status in {
                    "succeeded",
                    "failed",
                    "cancelled",
                    "partial_success",
                }:
                    return
                item = await session.scalar(
                    select(NoteGenerationItemModel)
                    .where(
                        NoteGenerationItemModel.batch_id == batch.id,
                        NoteGenerationItemModel.user_id == batch.user_id,
                        NoteGenerationItemModel.course_id == batch.course_id,
                    )
                    .order_by(NoteGenerationItemModel.ordinal)
                    .with_for_update(of=NoteGenerationItemModel)
                )
                now = self._now()
                if item is not None:
                    item.status = "failed"
                    item.completed_at = now
                    item.failure_code = failure_code
                    item.failure_summary = failure_summary
                    item.retryable = retryable_in_new_batch
                    item.lease_owner_id = None
                    item.lease_token_hash = None
                    item.lease_expires_at = None
                    item.heartbeat_at = None
                    item.lease_version += 1
                    item.state_version += 1
                    item.updated_at = now
                    attempt = await session.scalar(
                        select(NoteGenerationAttemptModel).where(
                            NoteGenerationAttemptModel.item_id == item.id,
                            NoteGenerationAttemptModel.attempt == item.attempt,
                            NoteGenerationAttemptModel.batch_id == batch.id,
                            NoteGenerationAttemptModel.user_id == batch.user_id,
                            NoteGenerationAttemptModel.course_id == batch.course_id,
                        )
                    )
                    if attempt is not None:
                        attempt.completed_at = now
                        attempt.failure_code = failure_code
                        attempt.failure_summary = failure_summary
                    await self._append_event(
                        session,
                        batch,
                        "note.item.failed",
                        {"item_id": item.id, "failure_code": item.failure_code},
                        now,
                    )
                batch.status = "failed"
                batch.completed_at = now
                batch.state_version += 1
                batch.updated_at = now
                await self._append_event(
                    session,
                    batch,
                    "note.batch.failed",
                    {"batch_id": batch.id, "failure_code": failure_code},
                    now,
                )
        except Exception:
            # A process-local runner cannot repair a broken database boundary;
            # suppress secondary errors so the task itself does not leak data.
            return

    async def _append_event(
        self,
        session: Any,
        batch: NoteGenerationBatchModel,
        event_type: str,
        payload: dict[str, object],
        now: datetime,
    ) -> None:
        batch.event_sequence += 1
        batch.state_version += 1
        session.add(
            NoteGenerationEventModel(
                id=new_id(),
                batch_id=batch.id,
                user_id=batch.user_id,
                course_id=batch.course_id,
                sequence=batch.event_sequence,
                event_type=event_type,
                state_version=batch.state_version,
                payload=payload,
                trace_id=new_trace_id(),
                occurred_at=now,
                expires_at=now + timedelta(seconds=self._settings.note_event_retention_seconds),
            )
        )

    def _now(self) -> datetime:
        value = self._clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _assert_frozen_units_unchanged(
    inputs: list[NoteGenerationInputModel],
    units: tuple[NoteCoverageUnitModel, ...],
    chunks: list[RevisionChunkModel],
) -> None:
    inputs_by_id = {input_row.id: input_row for input_row in inputs}
    chunks_by_page: dict[tuple[str, int], list[RevisionChunkModel]] = {}
    for chunk in chunks:
        chunks_by_page.setdefault((chunk.revision_id, chunk.page_ordinal), []).append(chunk)

    frozen_pages: set[tuple[str, int]] = set()
    for unit in units:
        input_row = inputs_by_id.get(unit.input_id)
        if input_row is None:
            raise _SourceChangedError
        page_ordinal = _strict_page_from_locator(unit.locator)
        page_key = (input_row.revision_id, page_ordinal)
        frozen_pages.add(page_key)
        current_hash = _canonical_hash(
            {
                "revision_id": input_row.revision_id,
                "page_ordinal": page_ordinal,
                "chunk_hashes": [
                    chunk.content_sha256 for chunk in chunks_by_page.get(page_key, [])
                ],
            }
        )
        if current_hash != unit.content_sha256:
            raise _SourceChangedError

    if any((chunk.revision_id, chunk.page_ordinal) not in frozen_pages for chunk in chunks):
        raise _SourceChangedError


def _render_demo_note(material: _Material) -> _RenderedNote:
    style_label = {
        NoteBatchStyle.EXAM_FOCUS: "考前速记",
        NoteBatchStyle.OUTLINE: "结构提纲",
        NoteBatchStyle.COMPLETE: "完整讲义",
    }[material.style]
    lines = [f"# {material.title}", "", f"> 笔记模板: {style_label}", ""]
    ast_nodes: list[dict[str, Any]] = [
        {
            "id": "heading-title",
            "type": "heading",
            "text": material.title,
            "level": 1,
            "provenance": "system_generated",
        },
        {
            "id": "style-label",
            "type": "paragraph",
            "text": f"笔记模板: {style_label}",
            "provenance": "system_generated",
        },
    ]
    current_document_id: str | None = None
    current_page: tuple[str, int] | None = None
    document_index = 0
    page_index = 0
    node_index = 0
    rendered_entries: list[_RenderedEntry] = []
    for entry in _render_entries(material):
        chunk = entry.chunk
        if chunk.document_id != current_document_id:
            current_document_id = chunk.document_id
            current_page = None
            document_index += 1
            document_label = (
                f"{document_index}. {chunk.document_name}"
                if material.style is NoteBatchStyle.OUTLINE
                else chunk.document_name
            )
            lines.extend([f"## {document_label}", ""])
            ast_nodes.append(
                {
                    "id": f"heading-document-{document_index}",
                    "type": "heading",
                    "text": document_label,
                    "level": 2,
                    "provenance": "source_backed",
                }
            )
        page_key = (chunk.document_id, chunk.page_ordinal)
        if page_key != current_page:
            current_page = page_key
            page_index += 1
            page_label = (
                f"幻灯片 {chunk.page_ordinal}"
                if chunk.media_type == _PPTX
                else f"第 {chunk.page_ordinal} 页"
            )
            if material.style is NoteBatchStyle.OUTLINE:
                page_label = f"{document_index}.{chunk.page_ordinal} {page_label}"
            lines.extend([f"### {page_label}", ""])
            ast_nodes.append(
                {
                    "id": f"heading-page-{page_index}",
                    "type": "heading",
                    "text": page_label,
                    "level": 3,
                    "provenance": "source_backed",
                }
            )
        node_index += 1
        quote = entry.text
        if material.style is NoteBatchStyle.EXAM_FOCUS:
            lines.append(f"- {quote}")
        elif material.style is NoteBatchStyle.OUTLINE:
            lines.append(f"{node_index}. {quote}")
        else:
            lines.extend([quote, ""])
        ast_node_id = f"source-paragraph-{node_index}"
        ast_nodes.append(
            {
                "id": ast_node_id,
                "type": "paragraph",
                "text": quote,
                "provenance": "source_backed",
            }
        )
        rendered_entries.append(_RenderedEntry(chunk=chunk, text=quote, ast_node_id=ast_node_id))
    if node_index == 0:
        raise ValueError("cannot render an empty source-derived note")
    return _RenderedNote(
        body_markdown="\n".join(lines).strip(),
        content_ast={"schema_version": "1.0", "nodes": ast_nodes},
        entries=tuple(rendered_entries),
    )


def _unique_rendered_chunks(
    entries: tuple[_RenderedEntry, ...],
) -> tuple[_SourceChunk, ...]:
    chunks_by_id: dict[str, _SourceChunk] = {}
    for entry in entries:
        chunks_by_id.setdefault(entry.chunk.chunk_id, entry.chunk)
    return tuple(chunks_by_id.values())


def _unique_values(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _render_entries(material: _Material) -> tuple[_RenderEntry, ...]:
    if material.style is NoteBatchStyle.COMPLETE:
        return tuple(
            _RenderEntry(chunk=chunk, text=chunk.text.strip(), source_index=index)
            for index, chunk in enumerate(material.chunks)
            if chunk.text.strip()
        )
    if material.style is NoteBatchStyle.OUTLINE:
        return _outline_entries(material.chunks)
    return _exam_focus_entries(material.chunks)


def _outline_entries(chunks: tuple[_SourceChunk, ...]) -> tuple[_RenderEntry, ...]:
    entries: list[_RenderEntry] = []
    points_by_page: dict[tuple[str, int], int] = {}
    for source_index, chunk in enumerate(chunks):
        if len(entries) >= _OUTLINE_POINTS_PER_NOTE:
            break
        page_key = (chunk.document_id, chunk.page_ordinal)
        if points_by_page.get(page_key, 0) >= _OUTLINE_POINTS_PER_PAGE:
            continue
        sentences = _source_sentences(chunk.text)
        if not sentences:
            continue
        excerpt = _source_excerpt(sentences[0], _OUTLINE_EXCERPT_CHARS)
        if not excerpt:
            continue
        entries.append(_RenderEntry(chunk=chunk, text=excerpt, source_index=source_index))
        points_by_page[page_key] = points_by_page.get(page_key, 0) + 1
    return tuple(entries)


def _exam_focus_entries(chunks: tuple[_SourceChunk, ...]) -> tuple[_RenderEntry, ...]:
    candidates_by_page: dict[tuple[str, int], list[_RenderEntry]] = {}
    for source_index, chunk in enumerate(chunks):
        page_key = (chunk.document_id, chunk.page_ordinal)
        for sentence_index, sentence in enumerate(_source_sentences(chunk.text)):
            excerpt = _source_excerpt(sentence, _EXAM_EXCERPT_CHARS)
            if excerpt:
                candidates_by_page.setdefault(page_key, []).append(
                    _RenderEntry(
                        chunk=chunk,
                        text=excerpt,
                        source_index=source_index,
                        sentence_index=sentence_index,
                    )
                )

    selected: list[_RenderEntry] = []
    for candidates in candidates_by_page.values():
        candidates.sort(
            key=lambda entry: (
                -_exam_value_score(entry.text),
                entry.source_index,
                entry.sentence_index,
            )
        )
        selected.extend(candidates[:_EXAM_POINTS_PER_PAGE])
    selected.sort(
        key=lambda entry: (
            -_exam_value_score(entry.text),
            entry.source_index,
            entry.sentence_index,
        )
    )
    selected = selected[:_EXAM_POINTS_PER_NOTE]
    selected.sort(key=lambda entry: (entry.source_index, entry.sentence_index))
    return tuple(selected)


def _source_sentences(text: str) -> tuple[str, ...]:
    return tuple(
        sentence
        for match in _SOURCE_SENTENCE.finditer(text.strip())
        if (sentence := match.group(0).strip())
    )


def _source_excerpt(text: str, max_chars: int) -> str:
    normalized = text.strip()
    if len(normalized) <= max_chars:
        return normalized
    window = normalized[:max_chars]
    for separator in ("\u3002", "\uff1b", "\uff0c", ";", ",", " "):
        boundary = window.rfind(separator)
        if boundary >= max_chars // 2:
            return window[: boundary + 1].rstrip()
    return window.rstrip()


def _exam_value_score(text: str) -> int:
    marker_score = sum(text.count(marker) for marker in _HIGH_VALUE_MARKERS) * 10
    definition_score = 6 if any(marker in text for marker in ("是", "指", "称为")) else 0
    numeric_score = 3 if any(character.isdigit() for character in text) else 0
    length_score = min(len(text), _EXAM_EXCERPT_CHARS) // 16
    return marker_score + definition_score + numeric_score + length_score


def _page_from_locator(locator: str) -> int:
    try:
        return int(locator.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        return 1


def _strict_page_from_locator(locator: str) -> int:
    try:
        page_ordinal = int(locator.rsplit(":", 1)[1])
    except (ValueError, IndexError) as exc:
        raise _SourceChangedError from exc
    if page_ordinal < 1:
        raise _SourceChangedError
    return page_ordinal


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_hash(value: str, fallback: str) -> str:
    if _is_sha256(value):
        return value
    return _sha256(fallback)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


__all__ = ["DemoNoteRunner"]
