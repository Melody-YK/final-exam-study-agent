"""Deterministic page-level quality gates for native parser output."""

from __future__ import annotations

from collections import Counter

from study_contracts import (
    PageIssue,
    PageIssueSeverity,
    PageQuality,
    PageQualityStatus,
)
from study_worker.parsers.normalize import RawPage

_MIN_CJK_CHAR_COUNT = 24
_MAX_CJK_UNIQUE_CHAR_COUNT = 5
_MAX_CJK_DIVERSITY_RATIO = 0.12
_MIN_DOMINANT_CJK_SHARE = 0.45
_MIN_REPEATED_CJK_RUN = 6
_EXTREME_DOMINANT_CJK_SHARE = 0.85


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
    if raw_page.metadata.get("text_mapping_corrupted") is True:
        return PageQuality(
            status=PageQualityStatus.FAILED,
            text_layer="native",
            requires_ocr=True,
            text_char_count=text_char_count,
            block_count=block_count,
            issues=[
                PageIssue(
                    code="OCR_REQUIRED",
                    severity=PageIssueSeverity.ERROR,
                    retryable=True,
                    message="The native text layer contains a corrupted CJK character mapping.",
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


def native_text_mapping_is_corrupted(text: str) -> bool:
    """Detect a collapsed PDF CJK mapping without penalizing ordinary prose."""

    cjk_chars = [char for char in text if _is_cjk_ideograph(char)]
    cjk_char_count = len(cjk_chars)
    if cjk_char_count < _MIN_CJK_CHAR_COUNT:
        return False

    counts = Counter(cjk_chars)
    unique_char_count = len(counts)
    if (
        unique_char_count > _MAX_CJK_UNIQUE_CHAR_COUNT
        or unique_char_count / cjk_char_count > _MAX_CJK_DIVERSITY_RATIO
    ):
        return False

    dominant_share = counts.most_common(1)[0][1] / cjk_char_count
    if dominant_share < _MIN_DOMINANT_CJK_SHARE:
        return False

    return (
        dominant_share >= _EXTREME_DOMINANT_CJK_SHARE
        or _longest_repeated_cjk_run(text) >= _MIN_REPEATED_CJK_RUN
    )


def _longest_repeated_cjk_run(text: str) -> int:
    longest = 0
    current = 0
    previous = ""
    for char in text:
        if _is_cjk_ideograph(char):
            current = current + 1 if char == previous else 1
            longest = max(longest, current)
        else:
            current = 0
        previous = char
    return longest


def _is_cjk_ideograph(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )
