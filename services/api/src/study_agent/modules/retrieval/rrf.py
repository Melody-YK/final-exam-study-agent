"""Stable reciprocal-rank fusion for dense and lexical candidates."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    chunk_id: str
    rank: int
    score: float

    def __post_init__(self) -> None:
        if not self.chunk_id:
            raise ValueError("chunk_id must not be empty")
        if self.rank <= 0:
            raise ValueError("rank must be positive")
        if not isfinite(self.score):
            raise ValueError("score must be finite")


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    chunk_id: str
    fused_score: float
    dense_rank: int | None
    dense_score: float | None
    lexical_rank: int | None
    lexical_score: float | None


def _by_chunk(candidates: list[RankedCandidate], route: str) -> dict[str, RankedCandidate]:
    result: dict[str, RankedCandidate] = {}
    for candidate in candidates:
        if candidate.chunk_id in result:
            raise ValueError(f"duplicate {route} candidate: {candidate.chunk_id}")
        result[candidate.chunk_id] = candidate
    return result


def reciprocal_rank_fusion(
    *,
    dense: list[RankedCandidate],
    lexical: list[RankedCandidate],
    k: int = 60,
    limit: int | None = None,
) -> tuple[FusedCandidate, ...]:
    """Fuse routes and make ties reproducible across runs and database plans."""

    if k <= 0:
        raise ValueError("RRF k must be positive")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    dense_by_chunk = _by_chunk(dense, "dense")
    lexical_by_chunk = _by_chunk(lexical, "lexical")
    fused: list[FusedCandidate] = []
    for chunk_id in dense_by_chunk.keys() | lexical_by_chunk.keys():
        dense_candidate = dense_by_chunk.get(chunk_id)
        lexical_candidate = lexical_by_chunk.get(chunk_id)
        score = sum(
            1.0 / (k + candidate.rank)
            for candidate in (dense_candidate, lexical_candidate)
            if candidate is not None
        )
        fused.append(
            FusedCandidate(
                chunk_id=chunk_id,
                fused_score=score,
                dense_rank=dense_candidate.rank if dense_candidate else None,
                dense_score=dense_candidate.score if dense_candidate else None,
                lexical_rank=lexical_candidate.rank if lexical_candidate else None,
                lexical_score=lexical_candidate.score if lexical_candidate else None,
            )
        )

    def tie_break(candidate: FusedCandidate) -> tuple[float, int, str]:
        ranks = [
            rank for rank in (candidate.dense_rank, candidate.lexical_rank) if rank is not None
        ]
        return (-candidate.fused_score, min(ranks), candidate.chunk_id)

    fused.sort(key=tie_break)
    return tuple(fused[:limit])
