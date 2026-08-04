"""Deterministic evidence sufficiency checks before any chat invocation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from study_agent.modules.answering.types import AuthorizedEvidence


class EvidenceGateCode(StrEnum):
    INDEX_UNAVAILABLE = "INDEX_UNAVAILABLE"
    NO_CANDIDATES = "NO_CANDIDATES"
    LOW_RELEVANCE = "LOW_RELEVANCE"
    SUFFICIENT = "SUFFICIENT"


_MESSAGES = {
    EvidenceGateCode.INDEX_UNAVAILABLE: "当前课程资料还未完成索引, 请等待处理完成后重试。",
    EvidenceGateCode.NO_CANDIDATES: "没有检索到相关课程内容, 请换个问法或补充对应章节资料。",
    EvidenceGateCode.LOW_RELEVANCE: (
        "检索到的课程内容与问题关联不足, 请补充包含该概念或例题的资料。"
    ),
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

    @property
    def min_score(self) -> float:
        return self._min_score

    def evaluate(
        self,
        *,
        active_index: bool,
        candidates: tuple[AuthorizedEvidence, ...],
    ) -> EvidenceGateDecision:
        if not active_index:
            code = EvidenceGateCode.INDEX_UNAVAILABLE
            return EvidenceGateDecision(False, code, _MESSAGES[code])

        if not candidates:
            code = EvidenceGateCode.NO_CANDIDATES
            return EvidenceGateDecision(False, code, _MESSAGES[code])

        eligible = tuple(item for item in candidates if item.score >= self._min_score)
        if not eligible:
            code = EvidenceGateCode.LOW_RELEVANCE
            return EvidenceGateDecision(False, code, _MESSAGES[code])

        code = EvidenceGateCode.SUFFICIENT
        return EvidenceGateDecision(
            True,
            code,
            _MESSAGES[code],
            candidates=eligible[: self._max_evidence],
        )
