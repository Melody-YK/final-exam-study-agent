"""Feature-gated reranking with an unchanged-RRF failure fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from study_agent.providers.errors import ProviderError
from study_agent.providers.protocols import Passage, RerankProvider


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    chunk_id: str
    text: str
    fused_score: float
    rerank_score: float | None = None


@dataclass(frozen=True, slots=True)
class RerankOutcome:
    candidates: tuple[RerankCandidate, ...]
    applied: bool
    fallback_reason: str | None


class RerankService:
    def __init__(
        self,
        *,
        enabled: bool = False,
        provider: RerankProvider | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("reranker timeout must be positive")
        self._enabled = enabled
        self._provider = provider
        self._timeout_seconds = timeout_seconds

    async def apply(
        self,
        query: str,
        candidates: tuple[RerankCandidate, ...],
    ) -> RerankOutcome:
        if not self._enabled:
            return RerankOutcome(candidates, applied=False, fallback_reason="disabled")
        if self._provider is None:
            return RerankOutcome(
                candidates,
                applied=False,
                fallback_reason="provider_not_configured",
            )
        passages = [Passage(id=item.chunk_id, text=item.text) for item in candidates]
        try:
            scores = await asyncio.wait_for(
                self._provider.rerank(query, passages),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            return RerankOutcome(candidates, applied=False, fallback_reason="timeout")
        except ProviderError:
            return RerankOutcome(candidates, applied=False, fallback_reason="provider_error")

        by_id: dict[str, float] = {}
        expected_ids = {item.chunk_id for item in candidates}
        for score in scores:
            if score.passage_id in by_id or score.passage_id not in expected_ids:
                return RerankOutcome(
                    candidates,
                    applied=False,
                    fallback_reason="invalid_response",
                )
            by_id[score.passage_id] = score.score
        if set(by_id) != expected_ids:
            return RerankOutcome(
                candidates,
                applied=False,
                fallback_reason="invalid_response",
            )

        original_order = {item.chunk_id: index for index, item in enumerate(candidates)}
        reranked = [replace(item, rerank_score=by_id[item.chunk_id]) for item in candidates]
        reranked.sort(
            key=lambda item: (
                -by_id[item.chunk_id],
                original_order[item.chunk_id],
                item.chunk_id,
            )
        )
        return RerankOutcome(tuple(reranked), applied=True, fallback_reason=None)
