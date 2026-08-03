"""Quality-routed PDF parsing across native, Docling, and Docling VLM backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from study_contracts import BlockType, PageQualityStatus
from study_worker.parsers.normalize import RawDocument, RawPage
from study_worker.parsers.protocols import ParseRequest, ParserExecutionError
from study_worker.parsers.quality import evaluate_page_quality
from study_worker.sandbox import Sandbox

_PDF_MEDIA_TYPE = "application/pdf"
_STRUCTURAL_TYPES = frozenset({BlockType.TABLE, BlockType.IMAGE, BlockType.FORMULA})


class PageParser(Protocol):
    async def parse(
        self,
        request: ParseRequest,
        *,
        sandbox: Sandbox,
        timeout_seconds: float,
    ) -> RawDocument: ...


@dataclass(frozen=True, slots=True)
class _Candidate:
    route: str
    reason: str
    document: RawDocument


class HybridPdfParser:
    """Keep cheap native results and escalate only pages that need more structure."""

    def __init__(
        self,
        *,
        native: PageParser,
        docling_standard: PageParser | None = None,
        docling_vlm: PageParser | None = None,
    ) -> None:
        self._native = native
        self._docling_standard = docling_standard
        self._docling_vlm = docling_vlm

    async def parse(
        self,
        request: ParseRequest,
        *,
        sandbox: Sandbox,
        timeout_seconds: float,
    ) -> RawDocument:
        if request.media_type != _PDF_MEDIA_TYPE:
            return await self._native.parse(
                request,
                sandbox=sandbox,
                timeout_seconds=timeout_seconds,
            )

        candidates: list[_Candidate] = []
        errors: list[ParserExecutionError] = []
        attempts = 0
        native_escalation_reason = "FAST_PARSE_FAILED"

        attempts += 1
        try:
            native = await self._native.parse(
                request,
                sandbox=sandbox,
                timeout_seconds=timeout_seconds,
            )
            native_quality_reason = _fallback_reason(native.pages[0])
            native_escalation_reason = native_quality_reason or "FAST_PARSE_ACCEPTED"
            candidates.append(_Candidate("fast", native_escalation_reason, native))
            if native_quality_reason is None:
                return _annotate(native, route="fast", reason="FAST_PARSE_ACCEPTED", tried=1)
        except ParserExecutionError as exc:
            errors.append(exc)

        standard_failed = False
        if self._docling_standard is not None:
            attempts += 1
            try:
                standard = await self._docling_standard.parse(
                    request,
                    sandbox=sandbox,
                    timeout_seconds=timeout_seconds,
                )
                standard_reason = _fallback_reason(standard.pages[0])
                candidates.append(
                    _Candidate(
                        "docling-standard",
                        native_escalation_reason if standard_reason is None else standard_reason,
                        standard,
                    )
                )
                best = _best_candidate(candidates)
                if _fallback_reason(best.document.pages[0]) is None:
                    return _annotate(
                        best.document,
                        route=best.route,
                        reason=best.reason,
                        tried=attempts,
                    )
            except ParserExecutionError as exc:
                errors.append(exc)
                standard_failed = True
        elif self._docling_vlm is None:
            if candidates:
                selected = _best_candidate(candidates)
                return _annotate(
                    selected.document,
                    route=selected.route,
                    reason="DOCLING_STANDARD_UNAVAILABLE",
                    tried=attempts,
                )
            raise errors[0]

        if self._docling_vlm is not None:
            attempts += 1
            try:
                vlm = await self._docling_vlm.parse(
                    request,
                    sandbox=sandbox,
                    timeout_seconds=timeout_seconds,
                )
                candidates.append(_Candidate("docling-vlm", "STRUCTURE_REQUIRES_VLM", vlm))
            except ParserExecutionError as exc:
                errors.append(exc)

        if not candidates:
            raise ParserExecutionError(
                "ENHANCED_PARSE_FAILED",
                retryable=any(error.retryable for error in errors),
            )
        selected = _best_candidate(candidates)
        reason = selected.reason
        if self._docling_vlm is None:
            reason = "DOCLING_STANDARD_FAILED" if standard_failed else "DOCLING_VLM_UNAVAILABLE"
        elif selected.route != "docling-vlm":
            reason = "DOCLING_VLM_FAILED"
        return _annotate(
            selected.document,
            route=selected.route,
            reason=reason,
            tried=attempts,
        )


def _fallback_reason(page: RawPage) -> str | None:
    quality = evaluate_page_quality(page)
    if quality.status is PageQualityStatus.FAILED:
        return "QUALITY_GATE_FAILED"
    if quality.status is PageQualityStatus.WARNING:
        return "LOW_TEXT_COVERAGE"
    unresolved = [
        block for block in page.blocks if block.type in _STRUCTURAL_TYPES and not block.text.strip()
    ]
    if unresolved:
        return "UNRESOLVED_STRUCTURAL_CONTENT"
    return None


def _candidate_score(candidate: _Candidate) -> tuple[int, int, int, int]:
    page = candidate.document.pages[0]
    quality = evaluate_page_quality(page)
    status_score = {
        PageQualityStatus.FAILED: 0,
        PageQualityStatus.WARNING: 1,
        PageQualityStatus.PASSED: 2,
    }[quality.status]
    resolved_structures = sum(
        1 for block in page.blocks if block.type in _STRUCTURAL_TYPES and bool(block.text.strip())
    )
    unresolved_structures = sum(
        1 for block in page.blocks if block.type in _STRUCTURAL_TYPES and not block.text.strip()
    )
    return (
        status_score,
        resolved_structures - unresolved_structures,
        min(quality.text_char_count, 20_000),
        len(page.blocks),
    )


def _best_candidate(candidates: list[_Candidate]) -> _Candidate:
    if not candidates:
        raise ValueError("at least one parse candidate is required")
    return max(candidates, key=_candidate_score)


def _annotate(document: RawDocument, *, route: str, reason: str, tried: int) -> RawDocument:
    pages: list[RawPage] = []
    for source_page in document.pages:
        page = source_page.model_copy(deep=True)
        page.metadata.update(
            {
                "parser_route": route,
                "parser_route_reason": reason,
                "parser_candidates_tried": tried,
            }
        )
        for block in page.blocks:
            block.metadata.update(
                {
                    "parser_route": route,
                    "parser_route_reason": reason,
                }
            )
        pages.append(page)
    return document.model_copy(update={"pages": pages})
