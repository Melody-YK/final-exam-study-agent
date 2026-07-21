"""Exercise the live API-to-Paddle ingestion path with a self-authored PNG."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, select

from study_agent.identity.principal import (  # type: ignore[import-untyped]
    AuthenticationMethod,
    Principal,
)
from study_agent.infrastructure.db.models import (  # type: ignore[import-untyped]
    DocumentModel,
    JobArtifactModel,
    PageCheckpointModel,
    ParseAttemptResultModel,
    ParseJobModel,
    RevisionBlockModel,
    RevisionPageModel,
)
from study_agent.infrastructure.db.session import Database  # type: ignore[import-untyped]

_ROOT = Path(__file__).resolve().parents[1]
_PRINCIPAL = Principal(
    subject="local-user",
    authentication_method=AuthenticationMethod.LOCAL,
)


def _self_authored_png() -> bytes:
    image = Image.new("RGB", (1600, 1000), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=84)
    for index, line in enumerate(
        (
            "STUDY AGENT OCR",
            "LIVE INGESTION 2026",
            "CHECKPOINT ARTIFACT PREVIEW",
        )
    ):
        draw.text((100, 130 + index * 250), line, fill="black", font=font)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _require(response: httpx.Response, expected: int) -> dict[str, Any]:
    if response.status_code != expected:
        raise RuntimeError(f"local OCR ingestion API returned HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("local OCR ingestion API response is invalid")
    return payload


async def _enqueue_and_wait(base_url: str, payload: bytes, timeout_seconds: float) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    async with httpx.AsyncClient(base_url=base_url, timeout=30, trust_env=False) as client:
        course = _require(
            await client.post(
                "/api/v1/courses",
                json={"title": f"Self-authored OCR smoke {digest[:12]}"},
            ),
            201,
        )
        created = _require(
            await client.post(
                f"/api/v1/courses/{course['id']}/documents",
                json={
                    "filename": "self-authored-ocr-smoke.png",
                    "media_type": "image/png",
                    "size_bytes": len(payload),
                    "sha256": digest,
                    "corpus_role": "corpus",
                },
            ),
            201,
        )
        document = created.get("document")
        upload = created.get("upload")
        if not isinstance(document, dict) or not isinstance(upload, dict):
            raise RuntimeError("local OCR ingestion upload session is invalid")
        _require(
            await client.put(
                str(upload["url"]),
                content=payload,
                headers={"Content-Type": "image/png"},
            ),
            200,
        )
        _require(
            await client.post(
                f"/api/v1/documents/{document['id']}/upload:complete",
                json={"upload_session_id": upload["id"]},
                headers={"Idempotency-Key": f"ocr-smoke-{document['id']}"},
            ),
            202,
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            current = _require(
                await client.get(f"/api/v1/documents/{document['id']}"),
                200,
            )
            if current.get("status") == "parsed_index_blocked":
                revision_id = current.get("preview_revision_id")
                if isinstance(revision_id, str) and revision_id:
                    return str(document["id"])
            if current.get("status") in {"failed", "partial_failed"}:
                raise RuntimeError("local OCR ingestion job failed")
            await asyncio.sleep(1)
    raise TimeoutError("local OCR ingestion did not reach preview revision in time")


async def _evidence(database: Database, document_id: str, source_sha256: str) -> dict[str, object]:
    async with database.session(_PRINCIPAL) as session:
        document = await session.scalar(
            select(DocumentModel).where(
                DocumentModel.id == document_id,
                DocumentModel.deleted_at.is_(None),
            )
        )
        if document is None or document.preview_revision_id is None:
            raise RuntimeError("local OCR preview revision is unavailable")
        revision_id = document.preview_revision_id
        job = await session.scalar(
            select(ParseJobModel)
            .where(ParseJobModel.document_id == document_id)
            .order_by(ParseJobModel.created_at.desc())
            .limit(1)
        )
        if job is None:
            raise RuntimeError("local OCR parse job is unavailable")
        page_backends = list(
            await session.scalars(
                select(RevisionPageModel.source_backend).where(
                    RevisionPageModel.revision_id == revision_id
                )
            )
        )
        page_versions = list(
            await session.scalars(
                select(RevisionPageModel.source_version).where(
                    RevisionPageModel.revision_id == revision_id
                )
            )
        )
        raw_refs = list(
            await session.scalars(
                select(RevisionPageModel.raw_result_ref).where(
                    RevisionPageModel.revision_id == revision_id
                )
            )
        )
        checkpoint_backends = list(
            await session.scalars(
                select(PageCheckpointModel.source_backend).where(
                    PageCheckpointModel.job_id == job.id
                )
            )
        )
        attempt_backends = list(
            await session.scalars(
                select(ParseAttemptResultModel.source_backend).where(
                    ParseAttemptResultModel.job_id == job.id
                )
            )
        )
        artifact_count = int(
            await session.scalar(
                select(func.count())
                .select_from(JobArtifactModel)
                .where(JobArtifactModel.job_id == job.id)
            )
            or 0
        )
        block_count = int(
            await session.scalar(
                select(func.count())
                .select_from(RevisionBlockModel)
                .where(RevisionBlockModel.revision_id == revision_id)
            )
            or 0
        )
    backend = "paddleocr-general"
    passed = (
        document.status == "parsed_index_blocked"
        and job.status == "succeeded"
        and job.parser_profile == "ocr-v1"
        and page_backends == [backend]
        and checkpoint_backends == [backend]
        and attempt_backends == [backend]
        and len(page_versions) == 1
        and bool(page_versions[0])
        and len(raw_refs) == 1
        and bool(raw_refs[0])
        and artifact_count >= 2
        and block_count >= 1
    )
    if not passed:
        raise RuntimeError("local OCR ingestion provenance verification failed")
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed-live-local-ingestion",
        "source_sha256": source_sha256,
        "document_status": document.status,
        "job_status": job.status,
        "parser_profile": job.parser_profile,
        "source_backend": backend,
        "page_count": len(page_backends),
        "block_count": block_count,
        "checkpoint_count": len(checkpoint_backends),
        "artifact_count": artifact_count,
        "preview_revision_created": True,
        "runtime_model_version_recorded": True,
        "raw_result_ref_recorded": True,
        "contains_raw_text": False,
        "contains_source_paths": False,
        "contains_object_keys": False,
        "contains_secret_values": False,
        "production_readiness": "not-assessed",
    }


def _write_private(path: Path, payload: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if path.is_symlink():
        raise ValueError("OCR ingestion evidence must not be a symlink")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


async def _run(arguments: argparse.Namespace) -> None:
    source = _self_authored_png()
    source_sha256 = hashlib.sha256(source).hexdigest()
    document_id = await _enqueue_and_wait(arguments.base_url, source, arguments.timeout_seconds)
    database = Database(arguments.database_url)
    try:
        report = await _evidence(database, document_id, source_sha256)
    finally:
        await database.dispose()
    _write_private(arguments.output, report)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--database-url",
        default="postgresql+asyncpg://study_agent@127.0.0.1:54329/study_agent",
    )
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument(
        "--output",
        type=Path,
        default=_ROOT / ".local/evidence/ocr-ingestion-smoke.json",
    )
    arguments = parser.parse_args()
    asyncio.run(_run(arguments))
    print("live_ocr_ingestion_smoke=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
