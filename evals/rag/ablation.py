"""Compare precomputed Dense, BM25, RRF, and Rerank rankings offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from study_agent.modules.retrieval.rrf import RankedCandidate, reciprocal_rank_fusion

MODES = ("dense", "bm25", "rrf", "rerank")
type DatasetSplit = Literal["train", "validation", "test"]
_DATASET_SPLITS = frozenset({"train", "validation", "test"})
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_LOCAL_REPORT_ROOTS = (
    _REPOSITORY_ROOT / ".local" / "evals",
    _REPOSITORY_ROOT / "evals" / "reports" / "generated",
)


@dataclass(frozen=True, slots=True)
class AblationMetrics:
    recall_at_k: float
    mean_reciprocal_rank: float
    evaluated_queries: int


def _ranked(raw: object, route: str) -> list[RankedCandidate]:
    if not isinstance(raw, list):
        raise ValueError(f"{route} candidates must be a list")
    result: list[RankedCandidate] = []
    for rank, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{route} candidate must be an object")
        result.append(
            RankedCandidate(
                chunk_id=str(item["chunk_id"]),
                rank=int(item.get("rank", rank)),
                score=float(item["score"]),
            )
        )
    result.sort(key=lambda item: (item.rank, item.chunk_id))
    return result


def _reranked(raw: object) -> list[str]:
    candidates = _ranked(raw, "rerank")
    return [
        item.chunk_id
        for item in sorted(candidates, key=lambda item: (-item.score, item.rank, item.chunk_id))
    ]


def evaluate_ablation(
    rows: Iterable[dict[str, Any]],
    *,
    limit: int = 10,
    rrf_k: int = 60,
) -> dict[str, AblationMetrics]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    aggregates = {mode: {"recall": 0.0, "rr": 0.0, "queries": 0.0} for mode in MODES}
    for row in rows:
        if row.get("record_type") == "chunk":
            continue
        relevant_raw = row.get("relevant_chunk_ids")
        if relevant_raw == [] and row.get("expect_abstain") is True:
            continue
        if not isinstance(relevant_raw, list) or not relevant_raw:
            raise ValueError("each query requires relevant_chunk_ids")
        relevant = {str(item) for item in relevant_raw}
        dense = _ranked(row.get("dense", []), "dense")
        lexical = _ranked(row.get("bm25", []), "bm25")
        rankings = {
            "dense": [item.chunk_id for item in dense],
            "bm25": [item.chunk_id for item in lexical],
            "rrf": [
                item.chunk_id
                for item in reciprocal_rank_fusion(dense=dense, lexical=lexical, k=rrf_k)
            ],
            "rerank": _reranked(row.get("rerank", [])),
        }
        for mode, ranking in rankings.items():
            top = ranking[:limit]
            aggregates[mode]["recall"] += len(relevant.intersection(top)) / len(relevant)
            first_relevant = next(
                (rank for rank, chunk_id in enumerate(top, start=1) if chunk_id in relevant),
                None,
            )
            aggregates[mode]["rr"] += 0.0 if first_relevant is None else 1.0 / first_relevant
            aggregates[mode]["queries"] += 1
    return {
        mode: AblationMetrics(
            recall_at_k=(values["recall"] / values["queries"] if values["queries"] else 0.0),
            mean_reciprocal_rank=(values["rr"] / values["queries"] if values["queries"] else 0.0),
            evaluated_queries=int(values["queries"]),
        )
        for mode, values in aggregates.items()
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"line {line_number} must contain a JSON object")
        rows.append(payload)
    return rows


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run retrieval ablations from JSONL rankings")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--rrf-k", type=int, default=60)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = args.input.expanduser().resolve(strict=True)
    source_bytes = source.read_bytes()
    rows = _read_jsonl(source)
    metrics = evaluate_ablation(rows, limit=args.limit, rrf_k=args.rrf_k)
    raw_splits = [row.get("split") for row in rows if row.get("record_type") == "query"]
    if any(not isinstance(split, str) or split not in _DATASET_SPLITS for split in raw_splits):
        raise ValueError("query dataset split is invalid")
    dataset_splits = sorted({cast(DatasetSplit, split) for split in raw_splits})
    payload = {
        "schema_version": "1.0",
        "dataset_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "dataset_splits": dataset_splits,
        "provider_boundary": "test-double",
        "contains_raw_text": False,
        "production_readiness": "not-assessed",
        "limit": args.limit,
        "rrf_k": args.rrf_k,
        "modes": {
            mode: {
                "recall_at_k": result.recall_at_k,
                "mean_reciprocal_rank": result.mean_reciprocal_rank,
                "evaluated_queries": result.evaluated_queries,
            }
            for mode, result in metrics.items()
        },
    }
    rendered = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        _write_report(rendered, args.output)
    return 0


def _write_report(rendered: str, output: Path) -> Path:
    requested = output.expanduser().absolute()
    if requested.is_symlink():
        raise ValueError("ablation report output must not be a symlink")
    destination = requested.resolve(strict=False)
    if _is_within(destination, _REPOSITORY_ROOT) and not any(
        _is_within(destination, root) for root in _LOCAL_REPORT_ROOTS
    ):
        raise ValueError("ablation reports inside the repository must use an ignored output root")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(destination)
    destination.chmod(0o600)
    return destination


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
