"""Build text-free reports from precomputed public rankings or local observations."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Literal

from evals.rag.report import (
    CitationObservation,
    RagCaseObservation,
    build_rag_report,
    write_report,
)
from study_agent.modules.retrieval.rrf import RankedCandidate, reciprocal_rank_fusion

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SEED = _ROOT / "evals" / "fixtures" / "public" / "rag-seed-v1.jsonl"


def fixture_observations(
    seed_path: Path,
    mode: Literal["test-double", "no-provider"],
) -> list[RagCaseObservation]:
    observations: list[RagCaseObservation] = []
    for line in seed_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("record_type") != "query":
            continue
        if mode == "no-provider":
            observations.append(
                RagCaseObservation(
                    query_id=record["id"],
                    provider_boundary="no-provider",
                    external_provider_called=False,
                    retrieved_chunk_ids=[],
                    citations=[],
                    abstained=True,
                    refusal_code="PROVIDER_NOT_CONFIGURED",
                    latency_ms=0,
                )
            )
            continue
        dense = [
            RankedCandidate(
                chunk_id=item["chunk_id"],
                rank=rank,
                score=float(item["score"]),
            )
            for rank, item in enumerate(record["dense"], start=1)
        ]
        lexical = [
            RankedCandidate(
                chunk_id=item["chunk_id"],
                rank=rank,
                score=float(item["score"]),
            )
            for rank, item in enumerate(record["bm25"], start=1)
        ]
        ranked = [
            item.chunk_id for item in reciprocal_rank_fusion(dense=dense, lexical=lexical, k=60)
        ]
        expect_abstain = bool(record["expect_abstain"])
        relevant = set(record["relevant_chunk_ids"])
        observations.append(
            RagCaseObservation(
                query_id=record["id"],
                provider_boundary="test-double",
                external_provider_called=False,
                retrieved_chunk_ids=ranked,
                citations=(
                    []
                    if expect_abstain
                    else [
                        CitationObservation(chunk_id=chunk_id, supported=True)
                        for chunk_id in ranked
                        if chunk_id in relevant
                    ]
                ),
                abstained=expect_abstain,
                refusal_code="INSUFFICIENT_EVIDENCE" if expect_abstain else None,
                latency_ms=0,
            )
        )
    return observations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("test-double", "no-provider", "live-provider"),
        required=True,
    )
    parser.add_argument("--seed", type=Path, default=_DEFAULT_SEED)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    mode = arguments.mode
    credentials_rotated = os.environ.get("PROVIDER_CREDENTIALS_ROTATED") == "1"
    if mode == "live-provider":
        if (
            os.environ.get("RUN_LIVE_PROVIDER_TESTS") != "1"
            or not credentials_rotated
            or arguments.observations is None
        ):
            print(
                "external-blocked: live report requires explicit live gate, rotated credentials, "
                "and local observations"
            )
            return 77
        observations = [
            RagCaseObservation.model_validate(item)
            for item in json.loads(arguments.observations.read_text(encoding="utf-8"))
        ]
    else:
        observations = fixture_observations(arguments.seed, mode)
    report = build_rag_report(
        arguments.seed,
        observations,
        credentials_rotated=credentials_rotated if mode == "live-provider" else False,
    )
    output = arguments.output or (_ROOT / ".local" / "evals" / "rag" / mode / "report.json")
    write_report(report, output)
    print(f"wrote text-free {mode} report: {output}")
    if mode == "test-double":
        print("partial: precomputed fixture protocol is not application E2E evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
