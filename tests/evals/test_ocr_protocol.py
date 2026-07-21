from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.ocr.manifest import OcrEvalEntry, OcrEvalManifest
from evals.ocr.metrics import (
    ResourceObservation,
    bbox_iou,
    character_error_rate,
    reading_order_score,
    summarize_resources,
)
from scripts.build_private_eval_manifest import build_manifest


def test_ocr_metrics_cover_text_order_bbox_and_resources() -> None:
    assert character_error_rate("操作系统", "操作系統") == pytest.approx(0.25)
    assert character_error_rate("", "") == 0
    assert character_error_rate("", "extra") == 1
    assert reading_order_score(["a", "b", "c"], ["a", "c", "b"]) == pytest.approx(2 / 3)
    assert bbox_iou((0, 0, 0.5, 0.5), (0.25, 0.25, 0.5, 0.5)) == pytest.approx(1 / 7)

    summary = summarize_resources(
        [
            ResourceObservation(duration_ms=10, peak_rss_bytes=100, cache_hit=False),
            ResourceObservation(duration_ms=20, peak_rss_bytes=200, cache_hit=True),
            ResourceObservation(duration_ms=30, peak_rss_bytes=150, cache_hit=True),
        ]
    )
    assert summary["duration_p50_ms"] == 20
    assert summary["duration_p95_ms"] == 30
    assert summary["peak_rss_bytes"] == 200
    assert summary["cache_hit_rate"] == pytest.approx(2 / 3)


def test_private_manifest_records_paths_and_hashes_without_copying_files(tmp_path: Path) -> None:
    source = tmp_path / "private"
    source.mkdir()
    document = source / "self-authored.png"
    document.write_bytes(b"self-authored-fixture")
    (source / "ignored.txt").write_text("not an OCR input", encoding="utf-8")

    manifest = build_manifest(source, split="test")

    assert len(manifest.entries) == 1
    entry = manifest.entries[0]
    assert entry.source_path == str(document.resolve())
    assert entry.privacy == "private-authorized"
    assert entry.license_status == "private-use-only"
    assert entry.size_bytes == len(b"self-authored-fixture")
    assert len(entry.sha256) == 64
    assert sorted(path.name for path in source.iterdir()) == ["ignored.txt", "self-authored.png"]


def test_manifest_rejects_duplicate_content_and_public_claim_for_private_path(
    tmp_path: Path,
) -> None:
    entry = OcrEvalEntry(
        id="sample-1",
        source_path=str((tmp_path / "sample.pdf").resolve()),
        sha256="a" * 64,
        size_bytes=1,
        media_type="application/pdf",
        split="test",
        privacy="private-authorized",
        license_status="private-use-only",
    )
    with pytest.raises(ValidationError, match="hashes must be unique|duplicate"):
        OcrEvalManifest(
            generated_at=datetime.now(UTC),
            entries=[entry, entry.model_copy(update={"id": "sample-2"})],
        )

    with pytest.raises(ValidationError, match="private-use-only"):
        OcrEvalEntry(
            **entry.model_dump(exclude={"license_status"}),
            license_status="open-license",
        )
