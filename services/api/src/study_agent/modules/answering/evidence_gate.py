"""Deterministic evidence sufficiency checks before any chat invocation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from study_agent.modules.answering.types import AuthorizedEvidence


class EvidenceGateCode(StrEnum):
    INDEX_UNAVAILABLE = "INDEX_UNAVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    SUFFICIENT = "SUFFICIENT"


_MESSAGES = {
    EvidenceGateCode.INDEX_UNAVAILABLE: "当前课程还没有可用的活动索引。",
    EvidenceGateCode.INSUFFICIENT_EVIDENCE: "当前课件中没有足够依据回答该问题。",
    EvidenceGateCode.SUFFICIENT: "证据充分。",
}


@dataclass(frozen=True, slots=True)
class EvidenceGateDecision:
    sufficient: bool
    code: EvidenceGateCode
    message: str
    candidates: tuple[AuthorizedEvidence, ...] = ()


class EvidenceGate:
    def __init__(self, *, min_score: float = 0.02, max_evidence: int = 8) -> None:
        if not 0.0 <= min_score <= 1.0:
            raise ValueError("min_score must be between zero and one")
        if max_evidence <= 0:
            raise ValueError("max_evidence must be positive")
        self._min_score = min_score
        self._max_evidence = max_evidence

    def evaluate(
        self,
        *,
        active_index: bool,
        candidates: tuple[AuthorizedEvidence, ...],
    ) -> EvidenceGateDecision:
        if not active_index:
            code = EvidenceGateCode.INDEX_UNAVAILABLE
            return EvidenceGateDecision(False, code, _MESSAGES[code])

        eligible = tuple(item for item in candidates if item.score >= self._min_score)
        if not eligible:
            code = EvidenceGateCode.INSUFFICIENT_EVIDENCE
            return EvidenceGateDecision(False, code, _MESSAGES[code])

        code = EvidenceGateCode.SUFFICIENT
        return EvidenceGateDecision(
            True,
            code,
            _MESSAGES[code],
            candidates=eligible[: self._max_evidence],
        )
