"""Run a Secret-safe live Embedding-to-answer smoke on synthetic evidence only."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path

from study_agent.config import Settings
from study_agent.modules.answering.service import TrustedAnswerService
from study_agent.modules.answering.types import AuthorizedEvidence
from study_agent.providers.factory import build_provider_registry
from study_contracts import AnswerStatus, Evidence, SourceLocator


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("embedding provider returned a zero vector")
    return numerator / (left_norm * right_norm)


async def _sources_current() -> bool:
    return True


async def _run() -> dict[str, object]:
    if (
        os.environ.get("RUN_LIVE_PROVIDER_TESTS") != "1"
        or os.environ.get("PROVIDER_CREDENTIALS_ROTATED") != "1"
    ):
        raise RuntimeError("live Provider safety gates are closed")
    settings = Settings(_env_file=None)
    if not settings.providers_configured:
        raise RuntimeError("runtime Provider secrets are not configured")
    registry = build_provider_registry(settings)
    try:
        embedding = registry.embedding()
        synthetic_passages = [
            "信号量用于协调并发任务对共享资源的访问。",
            "磁盘调度算法决定存储请求的服务顺序。",
        ]
        vectors = await embedding.embed_documents(synthetic_passages)
        query_vector = await embedding.embed_query("什么机制用于协调并发访问?")
        scores = [_cosine(query_vector, vector) for vector in vectors]
        selected = max(range(len(scores)), key=scores.__getitem__)
        if selected != 0:
            raise RuntimeError("live embedding retrieval selected the synthetic distractor")

        authorized = AuthorizedEvidence(
            evidence=Evidence(
                id="synthetic-evidence-1",
                course_id="synthetic-course",
                document_id="synthetic-document",
                revision_id="synthetic-revision",
                chunk_id="synthetic-chunk",
                text=synthetic_passages[0],
                content_sha256="a" * 64,
                locator=SourceLocator(kind="page", ordinal=1),
            ),
            document_name="synthetic-course-note.pdf",
            score=scores[0],
            document_deletion_epoch=0,
            provenance=("self-authored-live-smoke@1",),
        )
        execution = await TrustedAnswerService(
            registry.chat,
            timeout_seconds=settings.provider_timeout_seconds,
            max_validation_attempts=2,
        ).answer(
            query_id="synthetic-live-query",
            question="根据资料, 什么机制用于协调并发访问?",
            active_index=True,
            candidates=(authorized,),
            sources_are_current=_sources_current,
        )
        if execution.answer is None or execution.answer.status is not AnswerStatus.ANSWERED:
            raise RuntimeError(
                "live answer did not satisfy the evidence-bound contract: "
                + str(execution.failure_code or "abstained")
            )
        if len(execution.answer.citations) != 1:
            raise RuntimeError("live answer did not return exactly one authorized citation")
        return {
            "schema_version": "1.0",
            "embedding_batch_count": len(vectors),
            "embedding_dimensions": len(query_vector),
            "synthetic_retrieval_selected_expected": True,
            "answer_status": execution.answer.status.value,
            "citation_count": len(execution.answer.citations),
            "usage_present": bool(execution.usage),
            "provider_response_id_present": execution.provider_response_id is not None,
            "contains_raw_text": False,
            "contains_secret_values": False,
            "production_readiness": "not-assessed",
        }
    finally:
        await registry.aclose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".local/evidence/provider-live-rag-smoke.json"),
    )
    arguments = parser.parse_args()
    report = asyncio.run(_run())
    target = arguments.output.expanduser().absolute()
    if target.is_symlink():
        raise ValueError("output must not be a symlink")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    os.chmod(target, 0o600)
    print("live_rag_smoke=passed")
    print(f"embedding_dimensions={report['embedding_dimensions']}")
    print(f"answer_status={report['answer_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
