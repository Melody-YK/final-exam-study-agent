"""Deterministic page-level quality gates for native parser output."""

from __future__ import annotations

from study_contracts import (
    PageIssue,
    PageIssueSeverity,
    PageQuality,
    PageQualityStatus,
)
from study_worker.parsers.normalize import RawPage


def evaluate_page_quality(raw_page: RawPage, *, low_text_threshold: int = 8) -> PageQuality:
    if low_text_threshold <= 0:
        raise ValueError("low_text_threshold must be positive")
    text_char_count = sum(len(block.text.strip()) for block in raw_page.blocks)
    block_count = len(raw_page.blocks)
    if not raw_page.native_text_present or text_char_count == 0:
        return PageQuality(
            status=PageQualityStatus.FAILED,
            text_layer="none",
            requires_ocr=True,
            text_char_count=text_char_count,
            block_count=block_count,
            issues=[
                PageIssue(
                    code="OCR_REQUIRED",
                    severity=PageIssueSeverity.ERROR,
                    retryable=True,
                    message="No usable native text layer was detected.",
                )
            ],
        )
    if text_char_count < low_text_threshold:
        return PageQuality(
            status=PageQualityStatus.WARNING,
            text_layer="native",
            requires_ocr=False,
            text_char_count=text_char_count,
            block_count=block_count,
            issues=[
                PageIssue(
                    code="LOW_TEXT_COVERAGE",
                    severity=PageIssueSeverity.WARNING,
                    retryable=False,
                    message="The native text layer contains very little text.",
                )
            ],
        )
    return PageQuality(
        status=PageQualityStatus.PASSED,
        text_layer="native",
        requires_ocr=False,
        text_char_count=text_char_count,
        block_count=block_count,
    )
