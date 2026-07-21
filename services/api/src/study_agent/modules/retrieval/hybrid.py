"""Dense/BM25/RRF retrieval producing an auditable EvidenceSet."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, Protocol

from sqlalchemy import and_, select

from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    RevisionChunkModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.ingestion.index_runner import EmbeddingModelIdentity
from study_agent.modules.retrieval.dense import DenseHit, DenseRetriever
from study_agent.modules.retrieval.lexical import LexicalResult, LexicalRetriever
from study_agent.modules.retrieval.rerank import RerankCandidate, RerankService
from study_agent.modules.retrieval.rrf import (
    FusedCandidate,
    RankedCandidate,
    reciprocal_rank_fusion,
)
from study_agent.modules.retrieval.trace import RetrievalTraceDraft, TraceStore


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    chunk_id: str
    course_id: str
    document_id: str
    revision_id: str
    text: str
    locator_kind: str
    page_ordinal: int
    section_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Evidence:
    chunk_id: str
    course_id: str
    document_id: str
    revision_id: str
    text: str
    locator_kind: str
    page_ordinal: int
    section_path: tuple[str, ...]
    fused_score: float
    dense_rank: int | None
    dense_score: float | None
    lexical_rank: int | None
    lexical_score: float | None
    rerank_score: float | None


@dataclass(frozen=True, slots=True)
class EvidenceSet:
    trace_id: str
    course_id: str
    document_ids: tuple[str, ...]
    evidence: tuple[Evidence, ...]


class EvidenceRepository(Protocol):
    async def load(
        self,
        principal: Principal,
        course_id: str,
        chunk_ids: tuple[str, ...],
    ) -> dict[str, EvidenceSource]: ...


class PostgresEvidenceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def load(
        self,
        principal: Principal,
        course_id: str,
        chunk_ids: tuple[str, ...],
    ) -> dict[str, EvidenceSource]:
        if not chunk_ids:
            return {}
        statement = (
            select(
                RevisionChunkModel.id,
                DocumentModel.course_id,
                DocumentModel.id.label("document_id"),
                DocumentRevisionModel.id.label("revision_id"),
                RevisionChunkModel.text,
                RevisionChunkModel.locator_kind,
                RevisionChunkModel.page_ordinal,
                RevisionChunkModel.section_path,
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
                RevisionChunkModel.id.in_(chunk_ids),
                CourseModel.id == course_id,
                CourseModel.deleted_at.is_(None),
                DocumentModel.deleted_at.is_(None),
                DocumentModel.corpus_role == "corpus",
                UserModel.subject == principal.subject,
                UserModel.authentication_method == principal.authentication_method.value,
            )
        )
        async with self._database.session(principal) as session:
            rows = (await session.execute(statement)).all()
        return {
            str(row.id): EvidenceSource(
                chunk_id=str(row.id),
                course_id=str(row.course_id),
                document_id=str(row.document_id),
                revision_id=str(row.revision_id),
                text=str(row.text),
                locator_kind=str(row.locator_kind),
                page_ordinal=int(row.page_ordinal),
                section_path=tuple(row.section_path),
            )
            for row in rows
        }


class HybridRetriever:
    def __init__(
        self,
        *,
        dense: DenseRetriever,
        lexical: LexicalRetriever,
        evidence: EvidenceRepository,
        traces: TraceStore,
        reranker: RerankService,
        rrf_k: int = 60,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError("RRF k must be positive")
        self._dense = dense
        self._lexical = lexical
        self._evidence = evidence
        self._traces = traces
        self._reranker = reranker
        self._rrf_k = rrf_k

    async def retrieve(
        self,
        principal: Principal,
        course_id: str,
        query: str,
        *,
        query_vector: list[float] | None,
        model: EmbeddingModelIdentity | None,
        document_ids: frozenset[str] | None = None,
        limit: int = 10,
        mode: Literal["dense", "lexical", "rrf"] = "rrf",
    ) -> EvidenceSet:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if mode in {"dense", "rrf"} and (query_vector is None or model is None):
            raise ValueError("dense retrieval requires a query vector and model identity")
        route_limit = max(limit * 4, limit)
        total_start = time.perf_counter()

        dense_hits: tuple[DenseHit, ...] = ()
        dense_ms = 0.0
        if mode in {"dense", "rrf"}:
            dense_start = time.perf_counter()
            assert query_vector is not None and model is not None
            dense_hits = await self._dense.retrieve(
                principal,
                course_id,
                query_vector,
                model=model,
                document_ids=document_ids,
                limit=route_limit,
            )
            dense_ms = (time.perf_counter() - dense_start) * 1000

        lexical_result = LexicalResult(manifest_id="", hits=())
        lexical_ms = 0.0
        if mode in {"lexical", "rrf"}:
            lexical_start = time.perf_counter()
            lexical_result = await self._lexical.retrieve(
                principal,
                course_id,
                query,
                document_ids=document_ids,
                limit=route_limit,
            )
            lexical_ms = (time.perf_counter() - lexical_start) * 1000

        dense_ranked = [
            RankedCandidate(chunk_id=item.chunk_id, rank=rank, score=item.score)
            for rank, item in enumerate(dense_hits, start=1)
        ]
        lexical_ranked = [
            RankedCandidate(chunk_id=item.chunk_id, rank=rank, score=item.score)
            for rank, item in enumerate(lexical_result.hits, start=1)
        ]
        fusion_start = time.perf_counter()
        fused = reciprocal_rank_fusion(
            dense=dense_ranked,
            lexical=lexical_ranked,
            k=self._rrf_k,
            limit=route_limit,
        )
        fusion_ms = (time.perf_counter() - fusion_start) * 1000
        sources = await self._evidence.load(
            principal,
            course_id,
            tuple(item.chunk_id for item in fused),
        )
        fused = tuple(item for item in fused if item.chunk_id in sources)

        rerank_start = time.perf_counter()
        rerank_outcome = await self._reranker.apply(
            query,
            tuple(
                RerankCandidate(
                    chunk_id=item.chunk_id,
                    text=sources[item.chunk_id].text,
                    fused_score=item.fused_score,
                )
                for item in fused
            ),
        )
        rerank_ms = (time.perf_counter() - rerank_start) * 1000
        fused_by_id = {item.chunk_id: item for item in fused}
        evidence = tuple(
            self._evidence_item(
                sources[item.chunk_id],
                fused_by_id[item.chunk_id],
                item.rerank_score,
            )
            for item in rerank_outcome.candidates[:limit]
        )
        timings = {
            "dense": dense_ms,
            "lexical": lexical_ms,
            "fusion": fusion_ms,
            "rerank": rerank_ms,
            "total": (time.perf_counter() - total_start) * 1000,
        }
        trace_id = await self._traces.save(
            principal,
            RetrievalTraceDraft(
                query=query,
                course_id=course_id,
                document_ids=tuple(sorted(document_ids or ())),
                mode="rerank" if rerank_outcome.applied else mode,
                model=model if mode in {"dense", "rrf"} else None,
                lexical_manifest_id=(
                    lexical_result.manifest_id if mode in {"lexical", "rrf"} else None
                ),
                rrf_k=self._rrf_k,
                dense_candidates=self._route_trace(dense_ranked),
                lexical_candidates=self._route_trace(lexical_ranked),
                fused_candidates=[self._fused_trace(item) for item in fused],
                rerank_candidates=[
                    {
                        "chunk_id": item.chunk_id,
                        "rank": rank,
                        "score": item.rerank_score,
                    }
                    for rank, item in enumerate(rerank_outcome.candidates, start=1)
                ]
                if rerank_outcome.applied
                else [],
                timings_ms=timings,
                reranker_applied=rerank_outcome.applied,
                reranker_fallback_code=rerank_outcome.fallback_reason,
            ),
        )
        return EvidenceSet(
            trace_id=trace_id,
            course_id=course_id,
            document_ids=tuple(sorted({item.document_id for item in evidence})),
            evidence=evidence,
        )

    @staticmethod
    def _evidence_item(
        source: EvidenceSource,
        fused: FusedCandidate,
        rerank_score: float | None,
    ) -> Evidence:
        return Evidence(
            chunk_id=source.chunk_id,
            course_id=source.course_id,
            document_id=source.document_id,
            revision_id=source.revision_id,
            text=source.text,
            locator_kind=source.locator_kind,
            page_ordinal=source.page_ordinal,
            section_path=source.section_path,
            fused_score=fused.fused_score,
            dense_rank=fused.dense_rank,
            dense_score=fused.dense_score,
            lexical_rank=fused.lexical_rank,
            lexical_score=fused.lexical_score,
            rerank_score=rerank_score,
        )

    @staticmethod
    def _route_trace(candidates: list[RankedCandidate]) -> list[dict[str, object]]:
        return [
            {"chunk_id": item.chunk_id, "rank": item.rank, "score": item.score}
            for item in candidates
        ]

    @staticmethod
    def _fused_trace(candidate: FusedCandidate) -> dict[str, object]:
        return {
            "chunk_id": candidate.chunk_id,
            "score": candidate.fused_score,
            "dense_rank": candidate.dense_rank,
            "dense_score": candidate.dense_score,
            "lexical_rank": candidate.lexical_rank,
            "lexical_score": candidate.lexical_score,
        }
