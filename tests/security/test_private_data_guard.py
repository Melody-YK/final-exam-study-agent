from __future__ import annotations

import json
from pathlib import Path

from scripts.check_private_data import (
    scan_workspace,
    verify_public_manifest,
    verify_rag_seed,
)


def test_guard_rejects_private_paths_secrets_and_answer_documents(tmp_path: Path) -> None:
    private = Path("学校/course.pdf")
    private_path = tmp_path / private
    private_path.parent.mkdir()
    private_path.write_bytes(b"private")

    secret = Path("config.txt")
    (tmp_path / secret).write_text("sk-" + "A" * 32, encoding="utf-8")
    answer = Path("evals/fixtures/public/final-answers.pdf")
    answer_path = tmp_path / answer
    answer_path.parent.mkdir(parents=True)
    answer_path.write_bytes(b"self-authored answer sentinel")

    findings = scan_workspace(tmp_path, [private, secret, answer])
    codes = {finding.code for finding in findings}

    assert "PRIVATE_PATH" in codes
    assert "OPENAI_STYLE_KEY" in codes
    assert "ANSWER_CONTAMINATION" in codes


def test_guard_accepts_source_code_and_verified_self_authored_manifest(
    workspace_root: Path,
    tmp_path: Path,
) -> None:
    safe = Path("src/example.py")
    safe_path = tmp_path / safe
    safe_path.parent.mkdir()
    safe_path.write_text("value = 'not a credential'\n", encoding="utf-8")

    assert scan_workspace(tmp_path, [safe]) == []
    assert verify_public_manifest(workspace_root) == []


def test_rag_seed_guard_rejects_duplicate_content_answer_leak_and_dangling_ids(
    tmp_path: Path,
) -> None:
    seed = tmp_path / "bad-seed.jsonl"
    records = [
        {
            "schema_version": "1.0",
            "record_type": "chunk",
            "id": "chunk-a",
            "document_id": "doc-a",
            "revision_id": "revision-a",
            "corpus_role": "corpus",
            "text": "duplicate text",
            "gold_answer": "must never enter corpus",
        },
        {
            "schema_version": "1.0",
            "record_type": "chunk",
            "id": "chunk-b",
            "document_id": "doc-a",
            "revision_id": "revision-a",
            "corpus_role": "corpus",
            "text": "duplicate text",
        },
        {
            "schema_version": "1.0",
            "record_type": "query",
            "id": "query-a",
            "split": "test",
            "query": "question",
            "relevant_chunk_ids": ["chunk-missing"],
            "expect_abstain": False,
            "dense": [],
            "bm25": [],
            "rerank": [],
        },
        {
            "schema_version": "1.0",
            "record_type": "query",
            "id": "query-invalid-split",
            "split": "private",
            "query": "another question",
            "relevant_chunk_ids": ["chunk-a"],
            "expect_abstain": False,
            "dense": [],
            "bm25": [],
            "rerank": [],
        },
    ]
    seed.write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )

    codes = {finding.code for finding in verify_rag_seed(seed, "bad-seed.jsonl")}

    assert "PUBLIC_SEED_INVALID" in codes
    assert "PUBLIC_SEED_DUPLICATE_CONTENT" in codes
    assert "ANSWER_CONTAMINATION" in codes
    assert "PUBLIC_SEED_DANGLING_CHUNK" in codes
