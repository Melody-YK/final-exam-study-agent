import pytest
from pydantic import ValidationError

from study_contracts import (
    Asset,
    AssetType,
    Block,
    BlockType,
    BoundingBox,
    Page,
    PageIssue,
    PageIssueSeverity,
    PageQuality,
    PageQualityStatus,
    ParseAttemptResult,
    ParseResultBundle,
    SourceLocator,
    canonical_sha256,
)


def _block(block_id: str = "block-1", text: str = "进程管理") -> Block:
    return Block(
        id=block_id,
        type=BlockType.TITLE,
        text=text,
        bbox_norm=BoundingBox(x=0.1, y=0.1, width=0.8, height=0.1),
        reading_order=0,
        confidence=0.99,
        source_backend="pdf-native",
        source_version="1.0",
        raw_result_ref="raw/page-1.json",
        section_path=["进程管理"],
    )


def _bundle_payload() -> dict[str, object]:
    page = Page(
        ordinal=1,
        width=1200,
        height=1600,
        source_kind="page",
        source_backend="pdf-native",
        source_version="1.0",
        raw_result_ref="raw/page-1.json",
        blocks=[_block()],
        quality=PageQuality(
            status=PageQualityStatus.PASSED,
            text_layer="native",
            text_char_count=4,
            block_count=1,
        ),
    )
    return {
        "schema_version": "1.0",
        "document_sha256": "a" * 64,
        "parser_profile": "native-v1",
        "source_backend": "pdf-native",
        "source_version": "1.0",
        "pages": [page.model_dump(mode="json")],
        "assets": [],
    }


def _bundle() -> ParseResultBundle:
    payload = _bundle_payload()
    return ParseResultBundle(**payload, canonical_sha256=canonical_sha256(payload))


def test_parse_result_bundle_round_trips_with_verified_canonical_hash() -> None:
    bundle = _bundle()

    restored = ParseResultBundle.model_validate_json(bundle.model_dump_json())

    assert restored == bundle
    assert restored.pages[0].quality.status is PageQualityStatus.PASSED
    assert restored.canonical_sha256 == canonical_sha256(
        restored.model_dump(mode="json", exclude={"canonical_sha256"})
    )


def test_parse_result_bundle_accepts_mixed_page_provenance_only_with_mixed_summary() -> None:
    payload = _bundle_payload()
    raw_ref = "raw/page-2-ocr.json"
    text = "self-authored OCR text"
    ocr_page = Page(
        ordinal=2,
        width=1200,
        height=1600,
        source_backend="paddleocr-general",
        source_version="3.7.0-test",
        raw_result_ref=raw_ref,
        blocks=[
            Block(
                id="page-2-block-0",
                type=BlockType.PARAGRAPH,
                text=text,
                bbox_norm=BoundingBox(x=0.1, y=0.2, width=0.8, height=0.1),
                reading_order=0,
                confidence=0.9,
                source_backend="paddleocr-general",
                source_version="3.7.0-test",
                raw_result_ref=raw_ref,
            )
        ],
        quality=PageQuality(
            status=PageQualityStatus.WARNING,
            text_layer="ocr",
            text_char_count=len(text),
            block_count=1,
            issues=[
                PageIssue(
                    code="OCR_BENCHMARK_PENDING",
                    severity=PageIssueSeverity.WARNING,
                    message="Self-authored OCR output is not a live quality claim.",
                )
            ],
        ),
    )
    mixed_payload = {
        **payload,
        "source_backend": "mixed",
        "source_version": "mixed",
        "pages": [payload["pages"][0], ocr_page.model_dump(mode="json")],
    }

    bundle = ParseResultBundle(
        **mixed_payload,
        canonical_sha256=canonical_sha256(mixed_payload),
    )

    assert [page.source_backend for page in bundle.pages] == [
        "pdf-native",
        "paddleocr-general",
    ]
    invalid_summary = {
        **mixed_payload,
        "source_backend": "pdf-native",
        "source_version": "1.0",
    }
    with pytest.raises(ValidationError, match="summarize"):
        ParseResultBundle(
            **invalid_summary,
            canonical_sha256=canonical_sha256(invalid_summary),
        )


def test_canonical_hash_is_independent_of_mapping_key_order() -> None:
    first = {"schema_version": "1.0", "payload": {"b": 2, "a": 1}}
    second = {"payload": {"a": 1, "b": 2}, "schema_version": "1.0"}

    assert canonical_sha256(first) == canonical_sha256(second)


def test_bundle_rejects_tampering_duplicate_ids_and_quality_count_drift() -> None:
    payload = _bundle_payload()
    with pytest.raises(ValidationError, match="canonical"):
        ParseResultBundle(**payload, canonical_sha256="b" * 64)

    duplicate_block = _block()
    duplicate_block.raw_result_ref = "raw/page-2.json"
    duplicate_page = Page(
        ordinal=2,
        width=1200,
        height=1600,
        source_kind="page",
        source_backend="pdf-native",
        source_version="1.0",
        raw_result_ref="raw/page-2.json",
        blocks=[duplicate_block],
        quality=PageQuality(
            status=PageQualityStatus.PASSED,
            text_layer="native",
            text_char_count=4,
            block_count=1,
        ),
    )
    duplicate_payload = {**payload, "pages": [payload["pages"][0], duplicate_page]}
    with pytest.raises(ValidationError, match="block identifiers"):
        ParseResultBundle(
            **duplicate_payload,
            canonical_sha256=canonical_sha256(duplicate_payload),
        )

    with pytest.raises(ValidationError, match="text_char_count"):
        Page(
            ordinal=1,
            width=1200,
            height=1600,
            source_kind="page",
            source_backend="pdf-native",
            source_version="1.0",
            raw_result_ref="raw/page-1.json",
            blocks=[_block()],
            quality=PageQuality(
                status=PageQualityStatus.PASSED,
                text_layer="native",
                text_char_count=99,
                block_count=1,
            ),
        )


def test_page_quality_requires_failed_issue_for_failed_page() -> None:
    with pytest.raises(ValidationError):
        PageQuality(
            status=PageQualityStatus.FAILED,
            text_layer="unknown",
            text_char_count=0,
            block_count=0,
        )

    quality = PageQuality(
        status=PageQualityStatus.FAILED,
        text_layer="unknown",
        text_char_count=0,
        block_count=0,
        issues=[
            PageIssue(
                code="NO_TEXT_LAYER",
                severity=PageIssueSeverity.ERROR,
                retryable=True,
                message="未检测到可用文本层。",
            )
        ],
    )
    assert quality.issues[0].retryable is True


def test_page_rejects_block_with_different_parser_provenance() -> None:
    block = _block()
    block.source_backend = "pptx-native"

    with pytest.raises(ValidationError, match="provenance"):
        Page(
            ordinal=1,
            width=1200,
            height=1600,
            source_backend="pdf-native",
            source_version="1.0",
            raw_result_ref="raw/page-1.json",
            blocks=[block],
        )


def test_page_quality_exposes_stable_requires_ocr_state() -> None:
    quality = PageQuality(
        status=PageQualityStatus.WARNING,
        text_layer="none",
        requires_ocr=True,
        text_char_count=0,
        block_count=0,
        issues=[
            PageIssue(
                code="OCR_REQUIRED",
                severity=PageIssueSeverity.WARNING,
                retryable=True,
                message="该页需要可用的 OCR capability。",
            )
        ],
    )

    assert quality.requires_ocr is True
    with pytest.raises(ValidationError, match="cannot require OCR"):
        PageQuality(
            status=PageQualityStatus.PASSED,
            text_layer="none",
            requires_ocr=True,
            text_char_count=0,
            block_count=0,
        )


def test_bundle_requires_contiguous_ordered_pages() -> None:
    payload = _bundle_payload()
    second_page = Page(
        ordinal=3,
        width=1200,
        height=1600,
        source_kind="page",
        source_backend="pdf-native",
        source_version="1.0",
        raw_result_ref="raw/page-3.json",
        blocks=[_block("block-3", "内存管理")],
        quality=PageQuality(
            status=PageQualityStatus.PASSED,
            text_layer="native",
            text_char_count=4,
            block_count=1,
        ),
    )
    non_contiguous = {**payload, "pages": [payload["pages"][0], second_page]}

    with pytest.raises(ValidationError, match="contiguous"):
        ParseResultBundle(
            **non_contiguous,
            canonical_sha256=canonical_sha256(non_contiguous),
        )


def test_parse_attempt_accepts_an_ordered_page_subset_with_canonical_hash() -> None:
    payload = _bundle_payload()
    page = Page.model_validate(payload["pages"][0])
    page.ordinal = 2
    page.raw_result_ref = "raw/page-2.json"
    page.blocks[0].id = "block-2"
    page.blocks[0].raw_result_ref = "raw/page-2.json"
    attempt_payload = {
        "schema_version": "1.0",
        "document_sha256": "a" * 64,
        "parser_profile": "native-v1",
        "source_backend": "pdf-native",
        "source_version": "1.0",
        "total_page_count": 3,
        "requested_page_ordinals": [2, 3],
        "covered_page_ordinals": [2],
        "pages": [page.model_dump(mode="json")],
        "assets": [],
    }

    attempt = ParseAttemptResult(
        **attempt_payload,
        canonical_sha256=canonical_sha256(attempt_payload),
    )

    assert attempt.total_page_count == 3
    assert attempt.covered_page_ordinals == [2]
    assert ParseAttemptResult.model_validate_json(attempt.model_dump_json()) == attempt


def test_parse_attempt_accepts_mixed_page_backends_with_a_mixed_summary() -> None:
    payload = _bundle_payload()
    first_page = Page.model_validate(payload["pages"][0])
    second_page = first_page.model_copy(deep=True)
    second_page.ordinal = 2
    second_page.source_backend = "docling-vlm"
    second_page.source_version = "2.117.0"
    second_page.raw_result_ref = "raw/page-2-docling.json"
    second_page.blocks[0].id = "block-2"
    second_page.blocks[0].source_backend = "docling-vlm"
    second_page.blocks[0].source_version = "2.117.0"
    second_page.blocks[0].raw_result_ref = second_page.raw_result_ref
    attempt_payload = {
        "schema_version": "1.0",
        "document_sha256": "a" * 64,
        "parser_profile": "native-v1",
        "source_backend": "mixed",
        "source_version": "mixed",
        "total_page_count": 2,
        "requested_page_ordinals": [1, 2],
        "covered_page_ordinals": [1, 2],
        "pages": [
            first_page.model_dump(mode="json"),
            second_page.model_dump(mode="json"),
        ],
        "assets": [],
    }

    attempt = ParseAttemptResult(
        **attempt_payload,
        canonical_sha256=canonical_sha256(attempt_payload),
    )

    assert attempt.source_backend == "mixed"
    assert [page.source_backend for page in attempt.pages] == ["pdf-native", "docling-vlm"]

    invalid_summary = {
        **attempt_payload,
        "source_backend": "pdf-native",
        "source_version": "1.0",
    }
    with pytest.raises(ValidationError, match="summarize"):
        ParseAttemptResult(
            **invalid_summary,
            canonical_sha256=canonical_sha256(invalid_summary),
        )


def test_parse_attempt_preserves_parser_provenance_when_no_page_was_covered() -> None:
    attempt_payload = {
        "schema_version": "1.0",
        "document_sha256": "a" * 64,
        "parser_profile": "native-v1",
        "source_backend": "pdf-native",
        "source_version": "1.0",
        "total_page_count": 1,
        "requested_page_ordinals": [1],
        "covered_page_ordinals": [],
        "pages": [],
        "assets": [],
    }

    attempt = ParseAttemptResult(
        **attempt_payload,
        canonical_sha256=canonical_sha256(attempt_payload),
    )

    assert attempt.source_backend == "pdf-native"
    assert attempt.covered_page_ordinals == []


def test_parse_attempt_rejects_bad_coverage_order_and_hash() -> None:
    payload = _bundle_payload()
    page = payload["pages"][0]
    base = {
        "schema_version": "1.0",
        "document_sha256": "a" * 64,
        "parser_profile": "native-v1",
        "source_backend": "pdf-native",
        "source_version": "1.0",
        "total_page_count": 2,
        "requested_page_ordinals": [1, 2],
        "covered_page_ordinals": [1],
        "pages": [page],
        "assets": [],
    }

    with pytest.raises(ValidationError, match="canonical"):
        ParseAttemptResult(**base, canonical_sha256="b" * 64)

    unordered = {**base, "requested_page_ordinals": [2, 1]}
    with pytest.raises(ValidationError, match="ordered and unique"):
        ParseAttemptResult(
            **unordered,
            canonical_sha256=canonical_sha256(unordered),
        )

    mismatched = {**base, "covered_page_ordinals": [2]}
    with pytest.raises(ValidationError, match="exactly match"):
        ParseAttemptResult(
            **mismatched,
            canonical_sha256=canonical_sha256(mismatched),
        )


def test_complete_bundle_still_rejects_a_valid_attempt_subset() -> None:
    payload = _bundle_payload()
    page = Page.model_validate(payload["pages"][0])
    page.ordinal = 2
    page.raw_result_ref = "raw/page-2.json"
    page.blocks[0].id = "block-2"
    page.blocks[0].raw_result_ref = "raw/page-2.json"
    incomplete_bundle = {
        **payload,
        "pages": [page.model_dump(mode="json")],
    }

    with pytest.raises(ValidationError, match="contiguous"):
        ParseResultBundle(
            **incomplete_bundle,
            canonical_sha256=canonical_sha256(incomplete_bundle),
        )


def test_attempt_rechecks_mutated_nested_block_provenance_and_raw_ref() -> None:
    payload = _bundle_payload()
    page = Page.model_validate(payload["pages"][0])
    page.blocks[0].source_backend = "pptx-native"
    attempt_payload = {
        "schema_version": "1.0",
        "document_sha256": "a" * 64,
        "parser_profile": "native-v1",
        "source_backend": "pdf-native",
        "source_version": "1.0",
        "total_page_count": 1,
        "requested_page_ordinals": [1],
        "covered_page_ordinals": [1],
        "pages": [page],
        "assets": [],
    }

    with pytest.raises(ValidationError, match="block parser provenance"):
        ParseAttemptResult(
            **attempt_payload,
            canonical_sha256=canonical_sha256(
                {**attempt_payload, "pages": [page.model_dump(mode="json")]}
            ),
        )

    bundle_payload = {
        **_bundle_payload(),
        "pages": [page],
    }
    with pytest.raises(ValidationError, match="block parser provenance"):
        ParseResultBundle(
            **bundle_payload,
            canonical_sha256=canonical_sha256(
                {**bundle_payload, "pages": [page.model_dump(mode="json")]}
            ),
        )


def test_attempt_rejects_asset_provenance_or_raw_ref_from_another_page() -> None:
    payload = _bundle_payload()
    page = Page.model_validate(payload["pages"][0])
    asset = Asset(
        id="asset-1",
        type=AssetType.IMAGE,
        locator=SourceLocator(kind="page", ordinal=1),
        bbox_norm=BoundingBox(x=0.1, y=0.1, width=0.2, height=0.2),
        object_ref="asset-artifact-1",
        media_type="image/png",
        sha256="c" * 64,
        source_backend="pdf-native",
        source_version="1.0",
        raw_result_ref="raw/from-another-page.json",
    )
    attempt_payload = {
        "schema_version": "1.0",
        "document_sha256": "a" * 64,
        "parser_profile": "native-v1",
        "source_backend": "pdf-native",
        "source_version": "1.0",
        "total_page_count": 1,
        "requested_page_ordinals": [1],
        "covered_page_ordinals": [1],
        "pages": [page.model_dump(mode="json")],
        "assets": [asset.model_dump(mode="json")],
    }

    with pytest.raises(ValidationError, match="asset provenance"):
        ParseAttemptResult(
            **attempt_payload,
            canonical_sha256=canonical_sha256(attempt_payload),
        )
