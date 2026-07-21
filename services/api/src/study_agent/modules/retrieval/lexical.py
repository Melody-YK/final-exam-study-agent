"""Course-scoped access to active immutable BM25S versions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from sqlalchemy import and_, select

from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    LexicalManifestModel,
    RevisionChunkModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.retrieval.bm25_index import (
    Bm25IndexStore,
    BuiltLexicalIndex,
    LexicalHit,
    LoadedBm25Index,
)


@dataclass(frozen=True, slots=True)
class LexicalResult:
    manifest_id: str
    hits: tuple[LexicalHit, ...]


class LexicalRetriever:
    def __init__(self, database: Database, store: Bm25IndexStore) -> None:
        self._database = database
        self._store = store
        self._loaded: dict[str, LoadedBm25Index] = {}
        self._load_lock = asyncio.Lock()

    async def retrieve(
        self,
        principal: Principal,
        course_id: str,
        query: str,
        *,
        document_ids: frozenset[str] | None = None,
        limit: int = 20,
    ) -> LexicalResult:
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self._database.session(principal) as session:
            manifest = cast(
                LexicalManifestModel | None,
                await session.scalar(
                    select(LexicalManifestModel)
                    .join(
                        CourseModel,
                        and_(
                            CourseModel.id == LexicalManifestModel.course_id,
                            CourseModel.active_lexical_index_id == LexicalManifestModel.id,
                        ),
                    )
                    .join(UserModel, UserModel.id == CourseModel.user_id)
                    .where(
                        UserModel.subject == principal.subject,
                        UserModel.authentication_method == principal.authentication_method.value,
                        CourseModel.id == course_id,
                        CourseModel.deleted_at.is_(None),
                        LexicalManifestModel.user_id == CourseModel.user_id,
                        LexicalManifestModel.status == "active",
                    )
                ),
            )
        if manifest is None:
            raise LookupError("active lexical index is unavailable")
        if document_ids is not None and not document_ids:
            return LexicalResult(manifest.id, ())
        loaded = await self._load(manifest)
        raw_hits = await asyncio.to_thread(
            loaded.search,
            query,
            limit=limit,
            document_ids=document_ids,
        )
        if not raw_hits:
            return LexicalResult(manifest.id, ())

        raw_ids = [item.chunk_id for item in raw_hits]
        async with self._database.session(principal) as session:
            allowed_rows = (
                await session.execute(
                    select(
                        RevisionChunkModel.id,
                        DocumentModel.id.label("document_id"),
                        DocumentRevisionModel.id.label("revision_id"),
                    )
                    .join(
                        DocumentRevisionModel,
                        DocumentRevisionModel.id == RevisionChunkModel.revision_id,
                    )
                    .join(
                        DocumentModel,
                        and_(
                            DocumentModel.id == DocumentRevisionModel.document_id,
                            DocumentModel.active_revision_id == DocumentRevisionModel.id,
                        ),
                    )
                    .join(CourseModel, CourseModel.id == DocumentModel.course_id)
                    .join(UserModel, UserModel.id == CourseModel.user_id)
                    .where(
                        RevisionChunkModel.id.in_(raw_ids),
                        UserModel.subject == principal.subject,
                        UserModel.authentication_method == principal.authentication_method.value,
                        CourseModel.id == course_id,
                        CourseModel.deleted_at.is_(None),
                        DocumentModel.deleted_at.is_(None),
                        DocumentModel.corpus_role == "corpus",
                    )
                )
            ).all()
        allowed = {
            str(row.id): (str(row.document_id), str(row.revision_id)) for row in allowed_rows
        }
        hits = tuple(
            item
            for item in raw_hits
            if allowed.get(item.chunk_id) == (item.document_id, item.revision_id)
        )
        return LexicalResult(manifest.id, hits)

    async def _load(self, manifest: LexicalManifestModel) -> LoadedBm25Index:
        cached = self._loaded.get(manifest.id)
        if cached is not None:
            return cached
        async with self._load_lock:
            cached = self._loaded.get(manifest.id)
            if cached is not None:
                return cached
            built = BuiltLexicalIndex(
                version_id=manifest.version_id,
                storage_path=Path(manifest.storage_path),
                manifest_hash=manifest.manifest_hash,
                document_set_hash=manifest.document_set_hash,
                tokenizer_version=manifest.tokenizer_version,
                dictionary_hash=manifest.dictionary_hash,
                chunk_count=manifest.chunk_count,
                document_ids=tuple(manifest.document_ids),
                revision_ids=tuple(manifest.revision_ids),
            )
            loaded = await asyncio.to_thread(self._store.load, built)
            self._loaded[manifest.id] = loaded
            return loaded
