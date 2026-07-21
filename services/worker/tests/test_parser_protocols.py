from datetime import UTC, datetime
from pathlib import Path

import pytest

from study_contracts import (
    Asset,
    AssetType,
    BoundingBox,
    Page,
    ParseResultManifest,
    SourceLocator,
)
from study_worker.parsers import Clock, Parser, ParserCapability, ParseRequest, ParserResult


def _asset(*, asset_id: str = "asset-1", ordinal: int = 1) -> Asset:
    return Asset(
        id=asset_id,
        type=AssetType.RENDERED_PAGE,
        locator=SourceLocator(kind="page", ordinal=ordinal),
        bbox_norm=BoundingBox(x=0.0, y=0.0, width=1.0, height=1.0),
        object_ref=f"derived/{asset_id}.png",
        media_type="image/png",
        sha256="a" * 64,
        source_backend="pdf-native",
        source_version="1.0",
        raw_result_ref=f"raw/{asset_id}.json",
    )


def _manifest() -> ParseResultManifest:
    return ParseResultManifest(
        document_sha256="b" * 64,
        parser_profile="pdf-native-v1",
        pages=[
            Page(
                ordinal=1,
                width=1200,
                height=1600,
                source_backend="pdf-native",
                source_version="1.0",
                raw_result_ref="opaque-page-receipt",
            )
        ],
    )


def test_parser_capability_rejects_empty_or_malformed_media_types() -> None:
    with pytest.raises(ValueError, match="media_types must not be empty"):
        ParserCapability(
            profile="native-v1",
            source_backend="native",
            source_version="1.0",
            media_types=frozenset(),
        )
    with pytest.raises(ValueError, match="type/subtype"):
        ParserCapability(
            profile="native-v1",
            source_backend="native",
            source_version="1.0",
            media_types=frozenset({"pdf"}),
        )


def test_parse_request_rejects_duplicate_or_invalid_page_ordinals(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        ParseRequest(
            job_id="job-1",
            document_id="document-1",
            document_sha256="c" * 64,
            media_type="application/pdf",
            input_path=tmp_path / "input.pdf",
            output_dir=tmp_path / "output",
            requested_pages=(0,),
        )
    with pytest.raises(ValueError, match="unique"):
        ParseRequest(
            job_id="job-1",
            document_id="document-1",
            document_sha256="c" * 64,
            media_type="application/pdf",
            input_path=tmp_path / "input.pdf",
            output_dir=tmp_path / "output",
            requested_pages=(1, 1),
        )


def test_parser_result_rejects_duplicate_or_unknown_asset_locations() -> None:
    with pytest.raises(ValueError, match="identifiers must be unique"):
        ParserResult(manifest=_manifest(), assets=(_asset(), _asset()))
    with pytest.raises(ValueError, match="page present"):
        ParserResult(manifest=_manifest(), assets=(_asset(ordinal=2),))


def test_parser_and_clock_are_runtime_checkable_protocols() -> None:
    class FixedClock:
        def now(self) -> datetime:
            return datetime(2026, 7, 19, tzinfo=UTC)

    class ContractParser:
        @property
        def capability(self) -> ParserCapability:
            return ParserCapability(
                profile="contract-only",
                source_backend="test-contract",
                source_version="1.0",
                media_types=frozenset({"application/pdf"}),
            )

        async def parse(self, request: ParseRequest) -> ParserResult:
            del request
            return ParserResult(manifest=_manifest())

    assert isinstance(FixedClock(), Clock)
    assert isinstance(ContractParser(), Parser)
