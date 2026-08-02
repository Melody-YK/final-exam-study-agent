from unittest.mock import AsyncMock

import pytest

from study_agent.identity.principal import AuthenticationMethod, Principal
from study_agent.modules.answering.retrieval import PostgresQueryEvidence, _ActiveIndex
from study_agent.modules.answering.types import (
    AuthorizedEvidence,
    ConceptEvidenceAnchor,
    ConceptEvidenceContext,
)
from study_agent.modules.ingestion.index_runner import EmbeddingModelIdentity
from study_agent.modules.retrieval.hybrid import Evidence as RetrievalEvidence
from study_agent.modules.retrieval.hybrid import EvidenceSet
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import EmbeddingContract
from study_contracts import Evidence, SourceLocator


class TestEmbeddingProvider:
    async def probe(self) -> EmbeddingContract:
        return EmbeddingContract(provider="test", model="tiny", dimensions=2)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


class RecordingHybridRetriever:
    def __init__(self, evidence: tuple[RetrievalEvidence, ...]) -> None:
        self.evidence = evidence
        self.limits: list[int] = []

    async def retrieve(
        self,
        principal: Principal,
        course_id: str,
        query: str,
        *,
        query_vector: list[float],
        model: EmbeddingModelIdentity,
        document_ids: frozenset[str] | None,
        limit: int,
    ) -> EvidenceSet:
        del principal, query, query_vector, model, document_ids
        self.limits.append(limit)
        return EvidenceSet(
            trace_id="trace-1",
            course_id=course_id,
            document_ids=tuple(item.document_id for item in self.evidence),
            evidence=self.evidence,
        )


def _retrieval_evidence(chunk_id: str, *, score: float) -> RetrievalEvidence:
    return RetrievalEvidence(
        chunk_id=chunk_id,
        course_id="course-1",
        document_id=f"document-{chunk_id}",
        revision_id=f"revision-{chunk_id}",
        text=f"{chunk_id} 包含进程概念",
        locator_kind="page",
        page_ordinal=1,
        section_path=(),
        fused_score=score,
        dense_rank=None,
        dense_score=None,
        lexical_rank=None,
        lexical_score=None,
        rerank_score=None,
    )


def _authorized(source: RetrievalEvidence) -> AuthorizedEvidence:
    return AuthorizedEvidence(
        evidence=Evidence(
            id=source.chunk_id,
            course_id=source.course_id,
            document_id=source.document_id,
            revision_id=source.revision_id,
            chunk_id=source.chunk_id,
            text=source.text,
            content_sha256="c" * 64,
            locator=SourceLocator(kind=source.locator_kind, ordinal=source.page_ordinal),
        ),
        document_name=f"{source.document_id}.pdf",
        score=source.fused_score,
        document_deletion_epoch=0,
        provenance=("pdf-native@1.1",),
    )


def _build_retriever(
    monkeypatch: pytest.MonkeyPatch,
    hybrid_evidence: tuple[RetrievalEvidence, ...],
) -> tuple[PostgresQueryEvidence, RecordingHybridRetriever, AsyncMock]:
    hybrid = RecordingHybridRetriever(hybrid_evidence)
    registry = ProviderRegistry(
        embedding_provider=TestEmbeddingProvider(),
        chat_provider=None,
        http_client=None,
        owns_http_client=False,
    )
    retriever = PostgresQueryEvidence(object(), registry, hybrid)  # type: ignore[arg-type]
    monkeypatch.setattr(
        retriever,
        "_active_index",
        AsyncMock(return_value=_ActiveIndex(manifest_id="manifest-1")),
    )
    monkeypatch.setattr(
        retriever,
        "_model_identity",
        AsyncMock(
            return_value=EmbeddingModelIdentity(
                id="model-1",
                provider="test",
                model="tiny",
                dimensions=2,
                distance="cosine",
                contract_version="1",
            )
        ),
    )

    async def authorize(
        principal: Principal,
        course_id: str,
        evidence: tuple[RetrievalEvidence, ...],
    ) -> tuple[AuthorizedEvidence, ...]:
        del principal, course_id
        return tuple(_authorized(item) for item in evidence)

    monkeypatch.setattr(retriever, "_authorize", AsyncMock(side_effect=authorize))
    anchor_evidence = AsyncMock()
    monkeypatch.setattr(retriever, "_anchor_evidence", anchor_evidence)
    return retriever, hybrid, anchor_evidence


@pytest.mark.asyncio
async def test_concept_anchors_are_prioritized_deduplicated_and_fill_candidate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hybrid_evidence = (
        _retrieval_evidence("shared", score=0.9),
        *(_retrieval_evidence(f"hybrid-{index}", score=0.8 - index / 100) for index in range(1, 8)),
    )
    retriever, hybrid, anchor_evidence = _build_retriever(monkeypatch, hybrid_evidence)
    anchor_evidence.return_value = (
        _retrieval_evidence("anchor-only", score=1.0),
        _retrieval_evidence("shared", score=1.0),
    )
    principal = Principal(
        subject="owner",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    context = ConceptEvidenceContext(
        label="进程",
        anchors=(
            ConceptEvidenceAnchor(
                document_id="document-anchor-only",
                revision_id="revision-anchor-only",
                chunk_id="anchor-only",
            ),
            ConceptEvidenceAnchor(
                document_id="document-shared",
                revision_id="revision-shared",
                chunk_id="shared",
            ),
        ),
    )

    result = await retriever.retrieve(
        principal,
        "course-1",
        "什么是进程?",
        document_ids=frozenset({"document-anchor-only", "document-shared"}),
        concept_context=context,
    )

    assert [item.evidence.chunk_id for item in result.candidates] == [
        "anchor-only",
        "shared",
        "hybrid-1",
        "hybrid-2",
        "hybrid-3",
        "hybrid-4",
        "hybrid-5",
        "hybrid-6",
    ]
    assert len(result.candidates) == 8
    assert sum(item.evidence.chunk_id == "shared" for item in result.candidates) == 1
    assert result.candidates[1].score == 1.0
    assert result.retrieval_trace_id == "trace-1"
    assert result.active_lexical_index_id == "manifest-1"
    assert hybrid.limits == [8]
    anchor_evidence.assert_awaited_once_with(
        principal,
        "course-1",
        context,
        document_ids=frozenset({"document-anchor-only", "document-shared"}),
    )


@pytest.mark.asyncio
async def test_missing_concept_context_preserves_hybrid_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hybrid_evidence = (
        _retrieval_evidence("hybrid-1", score=0.7),
        _retrieval_evidence("hybrid-2", score=0.6),
    )
    retriever, hybrid, anchor_evidence = _build_retriever(monkeypatch, hybrid_evidence)
    principal = Principal(
        subject="owner",
        authentication_method=AuthenticationMethod.LOCAL,
    )

    result = await retriever.retrieve(
        principal,
        "course-1",
        "什么是进程?",
        document_ids=None,
    )

    assert [item.evidence.chunk_id for item in result.candidates] == ["hybrid-1", "hybrid-2"]
    assert [item.score for item in result.candidates] == [0.7, 0.6]
    assert hybrid.limits == [8]
    anchor_evidence.assert_not_awaited()
