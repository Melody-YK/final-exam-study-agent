from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from evals.ocr.manifest import OcrEvalEntry, OcrEvalManifest
from evals.ocr.run_benchmark import ExternalObservation, run_benchmark, write_report
from study_contracts import (
    Block,
    BlockType,
    BoundingBox,
    Page,
    PageIssue,
    PageIssueSeverity,
    PageQuality,
    PageQualityStatus,
)


def _prediction() -> Page:
    text = "Self-authored scheduling text"
    return Page(
        ordinal=1,
        width=1000,
        height=500,
        source_backend="paddleocr-general",
        source_version="3.7.0-test",
        raw_result_ref="opaque-local-receipt",
        blocks=[
            Block(
                id="page-1-block-0",
                type=BlockType.PARAGRAPH,
                text=text,
                bbox_norm=BoundingBox(x=0.1, y=0.2, width=0.4, height=0.2),
                reading_order=0,
                confidence=0.9,
                source_backend="paddleocr-general",
                source_version="3.7.0-test",
                raw_result_ref="opaque-local-receipt",
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
                    message="Self-authored output for protocol testing only.",
                )
            ],
        ),
    )


def test_benchmark_reads_external_manifest_and_emits_no_text_or_paths(tmp_path: Path) -> None:
    source = tmp_path / "self-authored.png"
    source.write_bytes(b"self-authored-public-image-placeholder")
    gold = tmp_path / "self-authored.gold.json"
    gold.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "pages": [
                    {
                        "schema_version": "1.0",
                        "page_ordinal": 1,
                        "text": "Self-authored scheduling text",
                        "blocks": [
                            {
                                "id": "gold-block-1",
                                "text": "Self-authored scheduling text",
                                "reading_order": 0,
                                "bbox_norm": {
                                    "x": 0.1,
                                    "y": 0.2,
                                    "width": 0.4,
                                    "height": 0.2,
                                },
                                "kind": "text",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    entry = OcrEvalEntry(
        id="self-authored-case",
        source_path=str(source.resolve()),
        sha256=sha256(source.read_bytes()).hexdigest(),
        size_bytes=source.stat().st_size,
        media_type="image/png",
        split="test",
        privacy="public",
        license_status="self-authored",
        gold_path=str(gold.resolve()),
    )
    manifest = OcrEvalManifest(
        generated_at=datetime(2026, 7, 19, tzinfo=UTC),
        entries=[entry],
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    observations = tmp_path / "observations"
    observations.mkdir()
    observation = ExternalObservation(
        entry_id=entry.id,
        parser_backend="paddleocr-general",
        parser_version="3.7.0-test",
        dependency_versions={"paddleocr": "3.7.0", "paddlepaddle": "3.3.1"},
        duration_ms=125.5,
        peak_rss_bytes=256 * 1024 * 1024,
        cache_hit=True,
        pages=[_prediction()],
    )
    (observations / f"{entry.id}.json").write_text(
        observation.model_dump_json(),
        encoding="utf-8",
    )

    report = run_benchmark(
        manifest_path.resolve(),
        observations.resolve(),
        generated_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
    )
    serialized = report.model_dump_json()

    assert report.contains_raw_text is False
    assert report.live_ocr_verified is False
    assert report.cases[0].quality is not None
    assert report.cases[0].quality.character_error_rate == 0
    assert report.cases[0].quality.reading_order_score == 1
    assert report.cases[0].quality.bbox_iou_mean == 1
    assert report.resources["duration_p50_ms"] == 125.5
    assert report.resources["peak_rss_bytes"] == 256 * 1024 * 1024
    assert "Self-authored scheduling text" not in serialized
    assert str(source) not in serialized
    assert str(gold) not in serialized
    assert '"text"' not in serialized
    output = write_report(report, tmp_path / "report.json")
    assert os.stat(output).st_mode & 0o777 == 0o600


def test_benchmark_rejects_tracked_raw_observation_directory(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        OcrEvalManifest(
            generated_at=datetime(2026, 7, 19, tzinfo=UTC),
            entries=[],
        ).model_dump_json(),
        encoding="utf-8",
    )
    tracked_directory = Path(__file__).resolve().parents[2] / "evals" / "ocr"

    with pytest.raises(ValueError, match="ignored root"):
        run_benchmark(manifest.resolve(), tracked_directory)


def test_observation_rejects_free_form_text_in_version_metadata() -> None:
    with pytest.raises(ValueError, match="version"):
        ExternalObservation(
            entry_id="self-authored-case",
            parser_backend="paddleocr-general",
            parser_version="recognized raw text",
            duration_ms=1,
            peak_rss_bytes=1,
            cache_hit=False,
        )


def test_benchmark_marks_only_explicit_live_model_observations_as_live(
    tmp_path: Path,
) -> None:
    source = tmp_path / "self-authored.png"
    source.write_bytes(b"self-authored-public-image-placeholder")
    entry = OcrEvalEntry(
        id="self-authored-live-case",
        source_path=str(source.resolve()),
        sha256=sha256(source.read_bytes()).hexdigest(),
        size_bytes=source.stat().st_size,
        media_type="image/png",
        split="test",
        privacy="public",
        license_status="self-authored",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        OcrEvalManifest(
            generated_at=datetime(2026, 7, 19, tzinfo=UTC),
            entries=[entry],
        ).model_dump_json(),
        encoding="utf-8",
    )
    observations = tmp_path / "observations"
    observations.mkdir()
    (observations / f"{entry.id}.json").write_text(
        ExternalObservation(
            entry_id=entry.id,
            execution_mode="live-model",
            parser_backend="paddleocr-general",
            parser_version="3.7.0",
            dependency_versions={"paddleocr": "3.7.0", "paddlepaddle": "3.3.1"},
            duration_ms=125.5,
            peak_rss_bytes=256 * 1024 * 1024,
            cache_hit=True,
            pages=[_prediction()],
        ).model_dump_json(),
        encoding="utf-8",
    )

    report = run_benchmark(manifest_path.resolve(), observations.resolve())

    assert report.live_ocr_verified is True
