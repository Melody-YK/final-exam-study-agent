from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.resources.preflight import (
    TWO_GIB_BYTES,
    ResourcePreflightObservation,
    build_resource_report,
    write_resource_report,
)


def _observations() -> list[ResourcePreflightObservation]:
    components = (
        "static-web",
        "api-single-uvicorn",
        "postgres-small-pool",
        "index-runner",
        "exact-pgvector",
        "bm25-mmap",
    )
    return [
        ResourcePreflightObservation(
            component=component,
            sample=1,
            latency_ms=float(index + 1),
            rss_bytes=(index + 1) * 10 * 1024 * 1024,
            outcome="succeeded",
            memory_limit_bytes=TWO_GIB_BYTES,
        )
        for index, component in enumerate(components)
    ]


def test_resource_report_is_local_only_and_records_percentiles() -> None:
    report = build_resource_report(
        _observations(),
        generated_at=datetime(2026, 7, 19, tzinfo=UTC),
    )

    assert report.status == "passed-local-preflight"
    assert report.local_equivalent_only is True
    assert report.production_capacity_verified is False
    assert report.memory_limit_bytes == TWO_GIB_BYTES
    assert report.latency_p50_ms == 3
    assert report.latency_p95_ms == 6
    assert report.peak_rss_bytes == 60 * 1024 * 1024


def test_resource_report_fails_locally_on_oom_without_claiming_production() -> None:
    observations = _observations()
    observations[-1] = observations[-1].model_copy(update={"outcome": "oom"})

    report = build_resource_report(observations)

    assert report.status == "failed-local-preflight"
    assert report.production_capacity_verified is False
    assert report.failure_counts == {"oom": 1}


def test_observation_requires_exact_two_gib_equivalent_limit() -> None:
    with pytest.raises(ValidationError, match="2147483648"):
        ResourcePreflightObservation(
            component="bm25-mmap",
            sample=1,
            latency_ms=1,
            rss_bytes=1,
            outcome="succeeded",
            memory_limit_bytes=1024,
        )


def test_resource_report_rejects_normalized_path_outside_ignored_root(
    workspace_root: Path,
) -> None:
    report = build_resource_report(_observations())
    disguised_output = workspace_root / ".local" / "evals" / ".." / ".." / "resource-report.json"

    with pytest.raises(ValueError, match="ignored output root"):
        write_resource_report(report, disguised_output)
