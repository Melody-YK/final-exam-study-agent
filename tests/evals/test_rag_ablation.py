import json
from pathlib import Path

import pytest

from evals.rag.ablation import evaluate_ablation, main


def _rows() -> list[dict[str, object]]:
    return [
        {
            "query_id": "q1",
            "relevant_chunk_ids": ["b"],
            "dense": [
                {"chunk_id": "a", "score": 0.9},
                {"chunk_id": "b", "score": 0.8},
            ],
            "bm25": [
                {"chunk_id": "b", "score": 5.0},
                {"chunk_id": "c", "score": 4.0},
            ],
            "rerank": [
                {"chunk_id": "a", "score": 0.1},
                {"chunk_id": "b", "score": 0.9},
            ],
        }
    ]


def test_ablation_compares_all_routes_without_provider_calls() -> None:
    result = evaluate_ablation(_rows(), limit=2, rrf_k=60)

    assert set(result) == {"dense", "bm25", "rrf", "rerank"}
    assert result["dense"].mean_reciprocal_rank == 0.5
    assert result["bm25"].mean_reciprocal_rank == 1.0
    assert result["rrf"].mean_reciprocal_rank == 1.0
    assert result["rerank"].mean_reciprocal_rank == 1.0


def test_ablation_cli_writes_machine_readable_report(tmp_path: Path) -> None:
    source = tmp_path / "rankings.jsonl"
    output = tmp_path / "report.json"
    source.write_text(json.dumps(_rows()[0], ensure_ascii=False) + "\n", encoding="utf-8")

    assert main([str(source), "--output", str(output), "--limit", "2"]) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "1.0"
    assert report["provider_boundary"] == "test-double"
    assert report["contains_raw_text"] is False
    assert report["production_readiness"] == "not-assessed"
    assert report["modes"]["rrf"]["evaluated_queries"] == 1
    assert output.stat().st_mode & 0o777 == 0o600


def test_ablation_cli_accepts_mixed_public_seed(
    workspace_root: Path,
    tmp_path: Path,
) -> None:
    source = workspace_root / "evals/fixtures/public/rag-seed-v1.jsonl"
    output = tmp_path / "public-seed-ablation.json"

    assert main([str(source), "--output", str(output), "--limit", "2"]) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["dataset_splits"] == ["test"]
    assert report["modes"]["dense"]["evaluated_queries"] == 2


def test_ablation_cli_rejects_free_text_split(tmp_path: Path) -> None:
    row = {
        **_rows()[0],
        "record_type": "query",
        "split": "private course notes",
    }
    source = tmp_path / "invalid-split.jsonl"
    source.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="dataset split is invalid"):
        main([str(source)])
