"""Aggregate 2 GiB-equivalent local observations without production claims."""

from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TWO_GIB_BYTES: Literal[2147483648] = 2_147_483_648
type Component = Literal[
    "static-web",
    "api-single-uvicorn",
    "postgres-small-pool",
    "index-runner",
    "exact-pgvector",
    "bm25-mmap",
]
type Outcome = Literal["succeeded", "timeout", "oom", "error"]
_REQUIRED_COMPONENTS = frozenset(
    {
        "static-web",
        "api-single-uvicorn",
        "postgres-small-pool",
        "index-runner",
        "exact-pgvector",
        "bm25-mmap",
    }
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_LOCAL_REPORT_ROOTS = (
    _REPOSITORY_ROOT / ".local" / "evals",
    _REPOSITORY_ROOT / "evals" / "reports" / "generated",
)


class ResourceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourcePreflightObservation(ResourceContract):
    schema_version: Literal["1.0"] = "1.0"
    component: Component
    sample: int = Field(ge=1)
    latency_ms: float = Field(ge=0)
    rss_bytes: int = Field(ge=0, le=TWO_GIB_BYTES)
    outcome: Outcome
    memory_limit_bytes: Literal[2147483648]

    @field_validator("latency_ms")
    @classmethod
    def latency_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("latency must be finite")
        return value


class ComponentResourceSummary(ResourceContract):
    sample_count: int = Field(ge=1)
    latency_mean_ms: float = Field(ge=0)
    latency_p50_ms: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    peak_rss_bytes: int = Field(ge=0)
    failure_count: int = Field(ge=0)


class ResourcePreflightReport(ResourceContract):
    schema_version: Literal["1.0"] = "1.0"
    generated_at: datetime
    status: Literal["passed-local-preflight", "failed-local-preflight"]
    memory_limit_bytes: Literal[2147483648] = TWO_GIB_BYTES
    local_equivalent_only: Literal[True] = True
    production_capacity_verified: Literal[False] = False
    production_readiness: Literal["not-assessed"] = "not-assessed"
    latency_mean_ms: float = Field(ge=0)
    latency_p50_ms: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    peak_rss_bytes: int = Field(ge=0)
    failure_counts: dict[str, int]
    components: dict[str, ComponentResourceSummary]


def build_resource_report(
    observations: list[ResourcePreflightObservation],
    *,
    generated_at: datetime | None = None,
) -> ResourcePreflightReport:
    if not observations:
        raise ValueError("resource preflight requires observations")
    components = {item.component for item in observations}
    if components != _REQUIRED_COMPONENTS:
        missing = sorted(_REQUIRED_COMPONENTS - components)
        extra = sorted(components - _REQUIRED_COMPONENTS)
        raise ValueError(f"resource preflight component mismatch: missing={missing} extra={extra}")
    sample_keys = [(item.component, item.sample) for item in observations]
    if len(sample_keys) != len(set(sample_keys)):
        raise ValueError("resource observation component/sample pairs must be unique")

    component_summaries: dict[str, ComponentResourceSummary] = {}
    for component in sorted(_REQUIRED_COMPONENTS):
        group = [item for item in observations if item.component == component]
        latencies = sorted(item.latency_ms for item in group)
        component_summaries[component] = ComponentResourceSummary(
            sample_count=len(group),
            latency_mean_ms=mean(latencies),
            latency_p50_ms=_nearest_rank(latencies, 0.50),
            latency_p95_ms=_nearest_rank(latencies, 0.95),
            peak_rss_bytes=max(item.rss_bytes for item in group),
            failure_count=sum(item.outcome != "succeeded" for item in group),
        )
    latencies = sorted(item.latency_ms for item in observations)
    failure_counts = dict(
        sorted(
            Counter(item.outcome for item in observations if item.outcome != "succeeded").items()
        )
    )
    return ResourcePreflightReport(
        generated_at=generated_at or datetime.now(UTC),
        status=("failed-local-preflight" if failure_counts else "passed-local-preflight"),
        latency_mean_ms=mean(latencies),
        latency_p50_ms=_nearest_rank(latencies, 0.50),
        latency_p95_ms=_nearest_rank(latencies, 0.95),
        peak_rss_bytes=max(item.rss_bytes for item in observations),
        failure_counts=failure_counts,
        components=component_summaries,
    )


def write_resource_report(report: ResourcePreflightReport, output: Path) -> Path:
    requested = output.expanduser().absolute()
    if requested.is_symlink():
        raise ValueError("resource report output must not be a symlink")
    destination = requested.resolve(strict=False)
    if _is_within(destination, _REPOSITORY_ROOT) and not any(
        _is_within(destination, root) for root in _LOCAL_REPORT_ROOTS
    ):
        raise ValueError("resource reports inside the repository must use an ignored output root")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(destination)
    destination.chmod(0o600)
    return destination


def _nearest_rank(values: list[float], percentile: float) -> float:
    return values[max(0, math.ceil(percentile * len(values)) - 1)]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
