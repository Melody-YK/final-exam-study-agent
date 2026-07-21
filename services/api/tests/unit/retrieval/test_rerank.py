import asyncio

import pytest

from study_agent.modules.retrieval.rerank import RerankCandidate, RerankService
from study_agent.providers.protocols import Passage, RerankScore


class RecordingReranker:
    def __init__(self, *, delay: float = 0) -> None:
        self.delay = delay
        self.calls = 0

    async def rerank(self, query: str, passages: list[Passage]) -> list[RerankScore]:
        self.calls += 1
        await asyncio.sleep(self.delay)
        assert query == "进程调度"
        return [
            RerankScore(passage_id=passages[-1].id, score=0.9),
            RerankScore(passage_id=passages[0].id, score=0.1),
        ]


def _candidates() -> tuple[RerankCandidate, ...]:
    return (
        RerankCandidate(chunk_id="a", text="时间片", fused_score=0.04),
        RerankCandidate(chunk_id="b", text="优先级", fused_score=0.03),
    )


@pytest.mark.asyncio
async def test_reranker_default_off_does_not_call_provider_or_change_rrf() -> None:
    provider = RecordingReranker()
    service = RerankService(enabled=False, provider=provider, timeout_seconds=1)

    outcome = await service.apply("进程调度", _candidates())

    assert outcome.candidates == _candidates()
    assert outcome.applied is False
    assert outcome.fallback_reason == "disabled"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_reranker_timeout_falls_back_to_unchanged_rrf() -> None:
    provider = RecordingReranker(delay=0.05)
    service = RerankService(enabled=True, provider=provider, timeout_seconds=0.001)

    outcome = await service.apply("进程调度", _candidates())

    assert outcome.candidates == _candidates()
    assert outcome.applied is False
    assert outcome.fallback_reason == "timeout"


@pytest.mark.asyncio
async def test_reranker_applies_complete_scores_with_stable_ties() -> None:
    provider = RecordingReranker()
    service = RerankService(enabled=True, provider=provider, timeout_seconds=1)

    outcome = await service.apply("进程调度", _candidates())

    assert [item.chunk_id for item in outcome.candidates] == ["b", "a"]
    assert [item.rerank_score for item in outcome.candidates] == [0.9, 0.1]
    assert outcome.applied is True
    assert outcome.fallback_reason is None
