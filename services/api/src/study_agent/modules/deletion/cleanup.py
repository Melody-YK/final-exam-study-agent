"""Idempotent physical cleanup for logically deleted document dependencies."""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import delete, select

from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    AnswerDependencyModel,
    ChunkEmbeddingModel,
    CourseModel,
    DeletionJobModel,
    DocumentModel,
    DocumentRevisionModel,
    JobArtifactModel,
    LexicalManifestModel,
    NoteSourceModel,
    ParseJobModel,
    QueryRunModel,
    RetrievalSnapshotModel,
    StoredObjectModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.providers.protocols import ObjectStorage


class _CleanupFailed(RuntimeError):
    pass


class DeletionCleanupService:
    def __init__(
        self,
        database: Database,
        *,
        lexical_root: Path,
        storage: ObjectStorage | None = None,
    ) -> None:
        self._database = database
        self._lexical_root = lexical_root.expanduser().resolve()
        self._storage = storage

    async def cleanup(self, principal: Principal, deletion_id: str) -> bool:
        try:
            return await self._cleanup(principal, deletion_id)
        except Exception:
            await self._mark_failed(principal, deletion_id)
            return False

    async def _cleanup(self, principal: Principal, deletion_id: str) -> bool:
        now = datetime.now(UTC)
        async with self._database.session(principal) as session:
            job = cast(
                DeletionJobModel | None,
                await session.scalar(
                    select(DeletionJobModel)
                    .join(UserModel, UserModel.id == DeletionJobModel.user_id)
                    .where(
                        DeletionJobModel.id == deletion_id,
                        UserModel.subject == principal.subject,
                        UserModel.authentication_method == principal.authentication_method.value,
                    )
                    .with_for_update(of=DeletionJobModel)
                ),
            )
            if job is None or job.target_type != "document":
                return False
            document = cast(
                DocumentModel | None,
                await session.scalar(
                    select(DocumentModel)
                    .where(DocumentModel.id == job.target_id)
                    .with_for_update(of=DocumentModel)
                ),
            )
            if (
                document is None
                or document.deleted_at is None
                or document.deletion_epoch != job.deletion_epoch
            ):
                return False

            dependencies = list(
                await session.scalars(
                    select(AnswerDependencyModel)
                    .where(AnswerDependencyModel.document_id == document.id)
                    .with_for_update(of=AnswerDependencyModel)
                )
            )
            note_sources = list(
                await session.scalars(
                    select(NoteSourceModel)
                    .where(NoteSourceModel.document_id == document.id)
                    .with_for_update(of=NoteSourceModel)
                )
            )
            query_ids = {item.query_id for item in dependencies}
            for dependency in dependencies:
                dependency.available = False
                dependency.invalidated_reason = "SOURCE_DELETED"
                dependency.invalidated_at = now
                dependency.quote = ""
                dependency.bounding_boxes = []
                dependency.provenance = []
            for source in note_sources:
                source.available = False
                source.unavailable_reason = "SOURCE_DELETED"
                source.invalidated_at = now
                source.quote = ""
                source.bounding_boxes = []
                source.provenance = []

            snapshots = list(
                await session.scalars(
                    select(RetrievalSnapshotModel)
                    .where(RetrievalSnapshotModel.course_id == document.course_id)
                    .with_for_update(of=RetrievalSnapshotModel)
                )
            )
            for snapshot in snapshots:
                removed = [
                    item
                    for item in snapshot.evidence_payload
                    if self._payload_document_id(item) == document.id
                ]
                if not removed:
                    continue
                query_ids.add(snapshot.query_id)
                removed_revisions = {
                    revision_id
                    for item in removed
                    if (revision_id := self._payload_revision_id(item)) is not None
                }
                snapshot.evidence_payload = [
                    item
                    for item in snapshot.evidence_payload
                    if self._payload_document_id(item) != document.id
                ]
                snapshot.candidate_count = len(snapshot.evidence_payload)
                snapshot.active_revision_ids = [
                    revision_id
                    for revision_id in snapshot.active_revision_ids
                    if revision_id not in removed_revisions
                ]
                snapshot.document_epochs = {
                    key: value
                    for key, value in snapshot.document_epochs.items()
                    if key != document.id
                }

            if query_ids:
                queries = list(
                    await session.scalars(
                        select(QueryRunModel)
                        .where(QueryRunModel.id.in_(query_ids))
                        .with_for_update(of=QueryRunModel)
                    )
                )
                for query in queries:
                    query.status = "invalidated"
                    query.answer_markdown = ""
                    query.claims = []
                    query.citations = []
                    query.refusal_code = "SOURCE_DELETED"
                    query.refusal_message = "回答所依赖的资料已删除。"
                    query.failure_code = None

            manifests = list(
                await session.scalars(
                    select(LexicalManifestModel)
                    .where(LexicalManifestModel.course_id == document.course_id)
                    .with_for_update(of=LexicalManifestModel)
                )
            )
            affected = [manifest for manifest in manifests if document.id in manifest.document_ids]
            course = await session.get(CourseModel, document.course_id, with_for_update=True)
            if course is not None and any(
                manifest.id == course.active_lexical_index_id for manifest in affected
            ):
                course.active_lexical_index_id = None
                await session.flush()
            for manifest in affected:
                await self._remove_lexical_path(manifest.storage_path)
                await session.delete(manifest)

            artifact_rows = (
                (
                    await session.execute(
                        select(JobArtifactModel, StoredObjectModel)
                        .join(
                            StoredObjectModel,
                            StoredObjectModel.id == JobArtifactModel.stored_object_id,
                        )
                        .where(JobArtifactModel.document_id == document.id)
                        .with_for_update(of=JobArtifactModel)
                    )
                )
                .tuples()
                .all()
            )
            if artifact_rows and self._storage is None:
                raise _CleanupFailed("artifact storage cleanup is unavailable")
            for _artifact, stored_object in artifact_rows:
                assert self._storage is not None
                await self._storage.delete(stored_object.object_key)
            artifact_object_ids = {stored_object.id for _, stored_object in artifact_rows}
            await session.execute(
                delete(ParseJobModel).where(ParseJobModel.document_id == document.id)
            )
            if artifact_object_ids:
                await session.flush()
                await session.execute(
                    delete(StoredObjectModel).where(StoredObjectModel.id.in_(artifact_object_ids))
                )

            await session.execute(
                delete(ChunkEmbeddingModel).where(ChunkEmbeddingModel.document_id == document.id)
            )
            await session.execute(
                delete(DocumentRevisionModel).where(
                    DocumentRevisionModel.document_id == document.id
                )
            )
            if job.last_error_code != "STORAGE_DELETE_FAILED":
                job.status = "completed"
                job.completed_at = now
                job.last_error_code = None
            return True

    async def _mark_failed(self, principal: Principal, deletion_id: str) -> None:
        async with self._database.session(principal) as session:
            job = cast(
                DeletionJobModel | None,
                await session.scalar(
                    select(DeletionJobModel)
                    .join(UserModel, UserModel.id == DeletionJobModel.user_id)
                    .where(
                        DeletionJobModel.id == deletion_id,
                        UserModel.subject == principal.subject,
                        UserModel.authentication_method == principal.authentication_method.value,
                    )
                    .with_for_update(of=DeletionJobModel)
                ),
            )
            if job is None:
                return
            job.attempt_count += 1
            job.status = "retry_wait"
            job.available_at = datetime.now(UTC) + timedelta(seconds=30)
            job.last_error_code = "DERIVED_CLEANUP_FAILED"
            job.completed_at = None

    async def _remove_lexical_path(self, raw_path: str) -> None:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_relative_to(self._lexical_root) or path == self._lexical_root:
            raise _CleanupFailed("lexical path is outside the configured root")

        def remove() -> None:
            if path.exists():
                shutil.rmtree(path)

        await asyncio.to_thread(remove)

    @staticmethod
    def _payload_document_id(item: dict[str, Any]) -> str | None:
        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            return None
        value = evidence.get("document_id")
        return value if isinstance(value, str) else None

    @staticmethod
    def _payload_revision_id(item: dict[str, Any]) -> str | None:
        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            return None
        value = evidence.get("revision_id")
        return value if isinstance(value, str) else None
