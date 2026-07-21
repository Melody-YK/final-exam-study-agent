"""Text-free RAG, citation, and refusal evaluation report contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Literal, Self, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_LOCAL_REPORT_ROOTS = (
    _REPOSITORY_ROOT / ".local" / "evals",
    _REPOSITORY_ROOT / "evals" / "reports" / "generated",
)
_MACHINE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
type ProviderBoundary = Literal["test-double", "no-provider", "live-provider"]
type DatasetSplit = Literal["train", "validation", "test"]


class _SeedQuery(TypedDict):
    relevant_chunk_ids: list[str]
    expect_abstain: bool
    split: DatasetSplit


class ReportContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CitationObservation(ReportContract):
    chunk_id: str = Field(min_length=1, max_length=255)
    supported: bool


class RagCaseObservation(ReportContract):
    schema_version: Literal["1.0"] = "1.0"
    query_id: str = Field(min_length=1, max_length=128)
    provider_boundary: ProviderBoundary
    external_provider_called: bool
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    citations: list[CitationObservation] = Field(default_factory=list)
    abstained: bool
    refusal_code: str | None = None
    latency_ms: float = Field(ge=0)

    @field_validator("query_id")
    @classmethod
    def query_id_must_be_machine_token(cls, value: str) -> str:
        if _MACHINE_TOKEN.fullmatch(value) is None:
            raise ValueError("query_id must be a machine token")
        return value

    @field_validator("retrieved_chunk_ids")
    @classmethod
    def retrieved_ids_must_be_unique(cls, value: list[str]) -> list[str]:
        if value != list(dict.fromkeys(value)) or any(
            not item or len(item) > 255 for item in value
        ):
            raise ValueError("retrieved chunk ids must be ordered unique identifiers")
        return value

    @field_validator("refusal_code")
    @classmethod
    def refusal_code_must_be_machine_token(cls, value: str | None) -> str | None:
        if value is not None and _MACHINE_TOKEN.fullmatch(value) is None:
            raise ValueError("refusal_code must be a machine token")
        return value

    @field_validator("latency_ms")
    @classmethod
    def latency_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("latency must be finite")
        return value

    @model_validator(mode="after")
    def boundaries_and_citations_must_be_consistent(self) -> Self:
        if self.provider_boundary in {"test-double", "no-provider"}:
            if self.external_provider_called:
                raise ValueError("non-live reports cannot record an external provider call")
        elif not self.external_provider_called:
            raise ValueError("live-provider observations require an external provider call")
        if self.provider_boundary == "no-provider" and not self.abstained:
            raise ValueError("no-provider observations must abstain")
        citation_ids = [item.chunk_id for item in self.citations]
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("citation chunk ids must be unique")
        if not set(citation_ids).issubset(self.retrieved_chunk_ids):
            raise ValueError("citations must reference retrieved chunks")
        if self.abstained:
            if self.citations or self.refusal_code is None:
                raise ValueError("abstention requires a refusal code and no citations")
        elif self.refusal_code is not None:
            raise ValueError("answered observations cannot include a refusal code")
        return self


class RagCaseResult(ReportContract):
    case_key: str = Field(pattern=r"^[0-9a-f]{16}$")
    retrieved_count: int = Field(ge=0)
    relevant_count: int = Field(ge=0)
    supported_citation_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    abstained: bool
    expected_abstain: bool
    latency_ms: float = Field(ge=0)
    failure_codes: list[str]


class RagMetrics(ReportContract):
    recall_at_k: float = Field(ge=0, le=1)
    mean_reciprocal_rank: float = Field(ge=0, le=1)
    citation_support_rate: float = Field(ge=0, le=1)
    citation_coverage_rate: float = Field(ge=0, le=1)
    abstention_accuracy: float = Field(ge=0, le=1)
    latency_mean_ms: float = Field(ge=0)
    latency_p50_ms: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)


class RagEvaluationReport(ReportContract):
    schema_version: Literal["1.0"] = "1.0"
    generated_at: datetime
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_splits: list[DatasetSplit] = Field(min_length=1)
    provider_boundary: ProviderBoundary
    live_provider_verified: bool
    credentials_rotated: bool
    contains_raw_text: Literal[False] = False
    production_readiness: Literal["not-assessed"] = "not-assessed"
    cases: list[RagCaseResult]
    metrics: RagMetrics


def build_rag_report(
    seed_path: Path,
    observations: list[RagCaseObservation],
    *,
    credentials_rotated: bool = False,
    limit: int = 8,
    generated_at: datetime | None = None,
) -> RagEvaluationReport:
    if limit <= 0:
        raise ValueError("limit must be positive")
    seed_file = seed_path.expanduser().resolve(strict=True)
    seed_bytes = seed_file.read_bytes()
    queries = _load_seed_queries(seed_bytes)
    by_query = {item.query_id: item for item in observations}
    if len(by_query) != len(observations):
        raise ValueError("observation query ids must be unique")
    if set(by_query) != set(queries):
        raise ValueError("observations must cover the public seed exactly")
    boundaries = {item.provider_boundary for item in observations}
    if len(boundaries) != 1:
        raise ValueError("provider boundaries must be reported separately")
    boundary = boundaries.pop()
    if boundary == "live-provider" and not credentials_rotated:
        raise ValueError("live-provider report requires rotated credentials")
    if boundary != "live-provider" and credentials_rotated:
        raise ValueError("credentials metadata belongs only to live-provider reports")

    answerable_recall: list[float] = []
    reciprocal_ranks: list[float] = []
    all_citations: list[CitationObservation] = []
    answered_cases = 0
    answered_with_citations = 0
    abstention_matches = 0
    latencies: list[float] = []
    cases: list[RagCaseResult] = []
    for query_id in sorted(queries):
        expected = queries[query_id]
        observation = by_query[query_id]
        relevant = set(expected["relevant_chunk_ids"])
        top = observation.retrieved_chunk_ids[:limit]
        failures: list[str] = []
        if relevant:
            answerable_recall.append(len(relevant.intersection(top)) / len(relevant))
            first = next(
                (rank for rank, chunk_id in enumerate(top, start=1) if chunk_id in relevant),
                None,
            )
            reciprocal_ranks.append(0.0 if first is None else 1.0 / first)
            if first is None:
                failures.append("RELEVANT_EVIDENCE_MISSED")
        expected_abstain = bool(expected["expect_abstain"])
        if observation.abstained == expected_abstain:
            abstention_matches += 1
        else:
            failures.append("ABSTENTION_MISMATCH")
        if not observation.abstained:
            answered_cases += 1
            if observation.citations:
                answered_with_citations += 1
            else:
                failures.append("CITATION_MISSING")
        unsupported = [item for item in observation.citations if not item.supported]
        if unsupported:
            failures.append("CITATION_UNSUPPORTED")
        all_citations.extend(observation.citations)
        latencies.append(observation.latency_ms)
        cases.append(
            RagCaseResult(
                case_key=hashlib.sha256(query_id.encode("utf-8")).hexdigest()[:16],
                retrieved_count=len(observation.retrieved_chunk_ids),
                relevant_count=len(relevant),
                supported_citation_count=len(observation.citations) - len(unsupported),
                citation_count=len(observation.citations),
                abstained=observation.abstained,
                expected_abstain=expected_abstain,
                latency_ms=observation.latency_ms,
                failure_codes=failures,
            )
        )
    sorted_latencies = sorted(latencies)
    metrics = RagMetrics(
        recall_at_k=mean(answerable_recall) if answerable_recall else 0.0,
        mean_reciprocal_rank=mean(reciprocal_ranks) if reciprocal_ranks else 0.0,
        citation_support_rate=(
            sum(item.supported for item in all_citations) / len(all_citations)
            if all_citations
            else 0.0
        ),
        citation_coverage_rate=(
            answered_with_citations / answered_cases if answered_cases else 0.0
        ),
        abstention_accuracy=abstention_matches / len(observations),
        latency_mean_ms=mean(latencies),
        latency_p50_ms=_nearest_rank(sorted_latencies, 0.50),
        latency_p95_ms=_nearest_rank(sorted_latencies, 0.95),
    )
    return RagEvaluationReport(
        generated_at=generated_at or datetime.now(UTC),
        dataset_sha256=hashlib.sha256(seed_bytes).hexdigest(),
        dataset_splits=sorted({item["split"] for item in queries.values()}),
        provider_boundary=boundary,
        live_provider_verified=boundary == "live-provider" and credentials_rotated,
        credentials_rotated=credentials_rotated,
        cases=cases,
        metrics=metrics,
    )


def write_report(report: RagEvaluationReport, output: Path) -> Path:
    requested = output.expanduser().absolute()
    if requested.is_symlink():
        raise ValueError("RAG report output must not be a symlink")
    destination = requested.resolve(strict=False)
    if _is_within(destination, _REPOSITORY_ROOT) and not any(
        _is_within(destination, root) for root in _LOCAL_REPORT_ROOTS
    ):
        raise ValueError("RAG reports inside the repository must use an ignored output root")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(destination)
    destination.chmod(0o600)
    return destination


def _load_seed_queries(seed_bytes: bytes) -> dict[str, _SeedQuery]:
    queries: dict[str, _SeedQuery] = {}
    chunks: set[str] = set()
    records: list[dict[str, object]] = []
    for line in seed_bytes.decode("utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
            raise ValueError("public seed is invalid")
        records.append(payload)
        if payload.get("record_type") == "chunk" and isinstance(payload.get("id"), str):
            chunks.add(payload["id"])
    for payload in records:
        if payload.get("record_type") != "query":
            continue
        query_id = payload.get("id")
        relevant = payload.get("relevant_chunk_ids")
        expect_abstain = payload.get("expect_abstain")
        split = payload.get("split")
        if (
            not isinstance(query_id, str)
            or not isinstance(relevant, list)
            or any(not isinstance(item, str) for item in relevant)
            or not set(relevant).issubset(chunks)
            or not isinstance(expect_abstain, bool)
            or not isinstance(split, str)
            or split not in {"train", "validation", "test"}
        ):
            raise ValueError("public seed query is invalid")
        queries[query_id] = {
            "relevant_chunk_ids": relevant,
            "expect_abstain": expect_abstain,
            "split": cast(DatasetSplit, split),
        }
    if not queries:
        raise ValueError("public seed contains no queries")
    return queries


def _nearest_rank(values: list[float], percentile: float) -> float:
    return values[max(0, math.ceil(percentile * len(values)) - 1)]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
