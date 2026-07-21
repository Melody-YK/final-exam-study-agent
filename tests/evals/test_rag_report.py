from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.rag.report import (
    CitationObservation,
    RagCaseObservation,
    build_rag_report,
    write_report,
)


def _seed(workspace_root: Path) -> Path:
    return workspace_root / "evals/fixtures/public/rag-seed-v1.jsonl"


def _test_observations() -> list[RagCaseObservation]:
    return [
        RagCaseObservation(
            query_id="query-process",
            provider_boundary="test-double",
            external_provider_called=False,
            retrieved_chunk_ids=["chunk-process", "chunk-paging"],
            citations=[CitationObservation(chunk_id="chunk-process", supported=True)],
            abstained=False,
            latency_ms=12.5,
        ),
        RagCaseObservation(
            query_id="query-deadlock",
            provider_boundary="test-double",
            external_provider_called=False,
            retrieved_chunk_ids=["chunk-deadlock"],
            citations=[CitationObservation(chunk_id="chunk-deadlock", supported=True)],
            abstained=False,
            latency_ms=10.0,
        ),
        RagCaseObservation(
            query_id="query-networking",
            provider_boundary="test-double",
            external_provider_called=False,
            retrieved_chunk_ids=[],
            citations=[],
            abstained=True,
            refusal_code="INSUFFICIENT_EVIDENCE",
            latency_ms=3.0,
        ),
    ]


def test_rag_report_is_text_free_and_separates_test_double_from_live(
    workspace_root: Path,
    tmp_path: Path,
) -> None:
    report = build_rag_report(
        _seed(workspace_root),
        _test_observations(),
        generated_at=datetime(2026, 7, 19, tzinfo=UTC),
    )
    serialized = report.model_dump_json()

    assert report.provider_boundary == "test-double"
    assert report.dataset_splits == ["test"]
    assert report.live_provider_verified is False
    assert report.contains_raw_text is False
    assert report.metrics.recall_at_k == 1
    assert report.metrics.mean_reciprocal_rank == 1
    assert report.metrics.citation_support_rate == 1
    assert report.metrics.abstention_accuracy == 1
    assert "Which machine state" not in serialized
    assert "A process is a program" not in serialized

    output = write_report(report, tmp_path / "rag-report.json")
    assert output.stat().st_mode & 0o777 == 0o600


def test_live_report_requires_rotated_credentials_and_real_call_boundary(
    workspace_root: Path,
) -> None:
    live = [
        item.model_copy(
            update={
                "provider_boundary": "live-provider",
                "external_provider_called": True,
            }
        )
        for item in _test_observations()
    ]

    with pytest.raises(ValueError, match="rotated credentials"):
        build_rag_report(_seed(workspace_root), live, credentials_rotated=False)


def test_rag_report_rejects_normalized_path_outside_ignored_root(
    workspace_root: Path,
) -> None:
    report = build_rag_report(_seed(workspace_root), _test_observations())
    disguised_output = workspace_root / ".local" / "evals" / ".." / ".." / "rag-report.json"

    with pytest.raises(ValueError, match="ignored output root"):
        write_report(report, disguised_output)


def test_no_provider_report_cannot_claim_answered_cases(workspace_root: Path) -> None:
    no_provider = [
        item.model_copy(
            update={
                "provider_boundary": "no-provider",
                "external_provider_called": False,
                "retrieved_chunk_ids": [],
                "citations": [],
                "abstained": True,
                "refusal_code": "PROVIDER_NOT_CONFIGURED",
            }
        )
        for item in _test_observations()
    ]

    report = build_rag_report(_seed(workspace_root), no_provider)

    assert report.provider_boundary == "no-provider"
    assert report.live_provider_verified is False
    assert report.metrics.abstention_accuracy == pytest.approx(1 / 3)
