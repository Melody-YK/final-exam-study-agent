"""Secret-safe persistence for reproducible retrieval traces."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, cast

from sqlalchemy import select

from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import CourseModel, RetrievalTraceModel, UserModel
from study_agent.infrastructure.db.models.core import new_id
from study_agent.infrastructure.db.session import Database
from study_agent.modules.ingestion.index_runner import EmbeddingModelIdentity


@dataclass(frozen=True, slots=True)
class RetrievalTraceDraft:
    query: str
    course_id: str
    document_ids: tuple[str, ...]
    mode: str
    model: EmbeddingModelIdentity | None
    lexical_manifest_id: str | None
    rrf_k: int
    dense_candidates: list[dict[str, object]]
    lexical_candidates: list[dict[str, object]]
    fused_candidates: list[dict[str, object]]
    rerank_candidates: list[dict[str, object]]
    timings_ms: dict[str, float]
    reranker_applied: bool
    reranker_fallback_code: str | None


class TraceStore(Protocol):
    async def save(self, principal: Principal, draft: RetrievalTraceDraft) -> str: ...


class PostgresTraceStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def save(self, principal: Principal, draft: RetrievalTraceDraft) -> str:
        trace_id = new_id()
        async with self._database.session(principal) as session:
            course = cast(
                CourseModel | None,
                await session.scalar(
                    select(CourseModel)
                    .join(UserModel, UserModel.id == CourseModel.user_id)
                    .where(
                        CourseModel.id == draft.course_id,
                        CourseModel.deleted_at.is_(None),
                        UserModel.subject == principal.subject,
                        UserModel.authentication_method == principal.authentication_method.value,
                    )
                ),
            )
            if course is None:
                raise LookupError("retrieval trace course scope is unavailable")
            session.add(
                RetrievalTraceModel(
                    id=trace_id,
                    user_id=course.user_id,
                    course_id=course.id,
                    query_sha256=hashlib.sha256(draft.query.encode("utf-8")).hexdigest(),
                    mode=draft.mode,
                    scope_document_ids=list(draft.document_ids),
                    embedding_model_id=draft.model.id if draft.model else None,
                    dimensions=draft.model.dimensions if draft.model else None,
                    lexical_manifest_id=draft.lexical_manifest_id,
                    rrf_k=draft.rrf_k,
                    dense_candidates=draft.dense_candidates,
                    lexical_candidates=draft.lexical_candidates,
                    fused_candidates=draft.fused_candidates,
                    rerank_candidates=draft.rerank_candidates,
                    timings_ms=draft.timings_ms,
                    reranker_applied=draft.reranker_applied,
                    reranker_fallback_code=draft.reranker_fallback_code,
                )
            )
        return trace_id
