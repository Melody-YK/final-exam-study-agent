from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import func, select

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import LocalPrincipalProvider
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    DocumentModel,
    DocumentRevisionModel,
    ParseAttemptResultModel,
    ParseJobModel,
    RevisionBlockModel,
    RevisionChunkModel,
    RevisionPageModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.storage.local import LocalStorage
from study_contracts import (
    PARSE_ATTEMPT_MEDIA_TYPE,
    PARSE_PAGE_MEDIA_TYPE,
    PARSER_RAW_MEDIA_TYPE,
    Block,
    BlockType,
    BoundingBox,
    Page,
    PageIssue,
    PageIssueSeverity,
    PageQuality,
    PageQualityStatus,
    ParseAttemptResult,
    ParseResultBundle,
    canonical_json_bytes,
    canonical_sha256,
)

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


def _settings(
    database_url: str,
    storage_root: Path,
    *,
    max_attempts: int = 3,
) -> Settings:
    return Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(database_url),
        local_storage_root=storage_root,
        worker_token=SecretStr("worker-secret"),
        job_max_attempts=max_attempts,
        job_retry_base_seconds=1,
    )


async def _enqueue(
    client: AsyncClient,
    *,
    payload: bytes = b"%PDF-1.7\nrevision ingestion",
    media_type: str = "application/pdf",
    filename: str = "revision.pdf",
) -> str:
    course = await client.post("/api/v1/courses", json={"title": "操作系统"})
    created = await client.post(
        f"/api/v1/courses/{course.json()['id']}/documents",
        json={
            "filename": filename,
            "media_type": media_type,
            "size_bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
            "corpus_role": "corpus",
        },
    )
    document = created.json()["document"]
    upload = created.json()["upload"]
    await client.put(upload["url"], content=payload, headers={"Content-Type": media_type})
    completed = await client.post(
        f"/api/v1/documents/{document['id']}/upload:complete",
        json={"upload_session_id": upload["id"]},
        headers={"Idempotency-Key": f"enqueue-{document['id']}"},
    )
    assert completed.status_code == 202
    return str(document["id"])


async def _claim(
    client: AsyncClient,
    worker_id: str,
    *,
    media_type: str = "application/pdf",
    supports_rendering: bool = False,
    parser_profile: str = "native-v1",
    supports_ocr: bool = False,
) -> dict[str, object]:
    response = await client.post(
        "/worker/v1/jobs:claim",
        json={
            "worker_id": worker_id,
            "capabilities": {
                "parser_profiles": [parser_profile],
                "media_types": [media_type],
                "supports_ocr": supports_ocr,
                "supports_rendering": supports_rendering,
                "max_input_bytes": 10_000,
                "max_pages": 100,
            },
        },
        headers={"Authorization": "Bearer worker-secret"},
    )
    assert response.status_code == 200
    lease = response.json()["lease"]
    assert lease is not None
    return lease


def _command(lease: dict[str, object], worker_id: str) -> dict[str, object]:
    return {
        "worker_id": worker_id,
        "lease_token": lease["lease_token"],
        "lease_version": lease["lease_version"],
        "attempt": lease["attempt"],
        "deletion_epoch": lease["deletion_epoch"],
    }


def _artifact_headers(lease: dict[str, object], worker_id: str, media_type: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer worker-secret",
        "X-Worker-ID": worker_id,
        "X-Lease-Token": str(lease["lease_token"]),
        "X-Lease-Version": str(lease["lease_version"]),
        "X-Attempt": str(lease["attempt"]),
        "X-Deletion-Epoch": str(lease["deletion_epoch"]),
        "Content-Type": media_type,
    }


async def _start(client: AsyncClient, lease: dict[str, object], worker_id: str) -> None:
    response = await client.post(
        f"/worker/v1/jobs/{lease['job_id']}:start",
        json=_command(lease, worker_id),
        headers={
            "Authorization": "Bearer worker-secret",
            "Idempotency-Key": f"start-{lease['attempt']}-{worker_id}",
        },
    )
    assert response.status_code == 200


async def _upload_artifact(
    client: AsyncClient,
    lease: dict[str, object],
    worker_id: str,
    *,
    name: str,
    payload: bytes,
    media_type: str,
) -> dict[str, object]:
    response = await client.put(
        f"{lease['artifact_upload_url']}/{name}?artifact_schema_version=1.0",
        content=payload,
        headers=_artifact_headers(lease, worker_id, media_type),
    )
    assert response.status_code == 201
    return response.json()


def _page(
    ordinal: int,
    raw_ref: str,
    *,
    failed: bool = False,
    source_backend: str = "pdf-native",
    source_version: str = "1.0",
    failure_code: str = "OCR_REQUIRED",
) -> Page:
    if failed:
        return Page(
            ordinal=ordinal,
            width=1200,
            height=1600,
            source_backend=source_backend,
            source_version=source_version,
            raw_result_ref=raw_ref,
            quality=PageQuality(
                status=PageQualityStatus.FAILED,
                text_layer="none",
                requires_ocr=failure_code == "OCR_REQUIRED",
                text_char_count=0,
                block_count=0,
                issues=[
                    PageIssue(
                        code=failure_code,
                        severity=PageIssueSeverity.ERROR,
                        retryable=True,
                        message="The self-authored page failed its parser gate.",
                    )
                ],
            ),
        )
    text = f"第 {ordinal} 页进程管理"
    block = Block(
        id=f"page-{ordinal}-block-0",
        type=BlockType.TITLE,
        text=text,
        bbox_norm=BoundingBox(x=0.1, y=0.1, width=0.8, height=0.1),
        reading_order=0,
        confidence=1,
        source_backend=source_backend,
        source_version=source_version,
        raw_result_ref=raw_ref,
        section_path=[text],
    )
    return Page(
        ordinal=ordinal,
        width=1200,
        height=1600,
        source_backend=source_backend,
        source_version=source_version,
        raw_result_ref=raw_ref,
        blocks=[block],
        quality=(
            PageQuality(
                status=PageQualityStatus.WARNING,
                text_layer="ocr",
                text_char_count=len(text),
                block_count=1,
                issues=[
                    PageIssue(
                        code="OCR_BENCHMARK_PENDING",
                        severity=PageIssueSeverity.WARNING,
                        retryable=False,
                        message="Self-authored OCR output has not passed a live quality gate.",
                    )
                ],
            )
            if source_backend == "paddleocr-general"
            else PageQuality(
                status=PageQualityStatus.PASSED,
                text_layer="native",
                text_char_count=len(text),
                block_count=1,
            )
        ),
    )


async def _checkpoint_page(
    client: AsyncClient,
    lease: dict[str, object],
    worker_id: str,
    ordinal: int,
    *,
    failed: bool = False,
    failure_code: str = "OCR_REQUIRED",
) -> Page:
    attempt = int(lease["attempt"])
    is_ocr = lease["parser_profile"] == "ocr-v1"
    source_backend = "paddleocr-general" if is_ocr else "pdf-native"
    source_version = "3.7.0-test" if is_ocr else "1.0"
    raw = await _upload_artifact(
        client,
        lease,
        worker_id,
        name=f"raw-a{attempt}-p{ordinal}.json",
        payload=canonical_json_bytes({"page": ordinal, "attempt": attempt}),
        media_type=PARSER_RAW_MEDIA_TYPE,
    )
    page = _page(
        ordinal,
        str(raw["artifact_ref"]),
        failed=failed,
        source_backend=source_backend,
        source_version=source_version,
        failure_code=failure_code,
    )
    normalized = await _upload_artifact(
        client,
        lease,
        worker_id,
        name=f"page-a{attempt}-p{ordinal}.json",
        payload=canonical_json_bytes(page.model_dump(mode="json")),
        media_type=PARSE_PAGE_MEDIA_TYPE,
    )
    command = _command(lease, worker_id)
    checkpoint = await client.put(
        f"/worker/v1/jobs/{lease['job_id']}/pages/{ordinal}/checkpoint",
        json={
            **command,
            "page_ordinal": ordinal,
            "status": "failed" if failed else "succeeded",
            "output_ref": normalized["artifact_ref"],
            "output_sha256": normalized["sha256"],
            "output_size_bytes": normalized["size_bytes"],
            "output_schema_version": "1.0",
            "source_backend": source_backend,
            "source_version": source_version,
            "error_code": failure_code if failed else None,
        },
        headers={
            "Authorization": "Bearer worker-secret",
            "Idempotency-Key": f"checkpoint-a{attempt}-p{ordinal}",
        },
    )
    assert checkpoint.status_code == 200
    return page


async def _complete_attempt(
    client: AsyncClient,
    lease: dict[str, object],
    worker_id: str,
    *,
    total_page_count: int,
    requested: list[int],
    pages: list[Page],
    failed_pages: list[int],
) -> Response:
    is_ocr = lease["parser_profile"] == "ocr-v1"
    attempt_payload = {
        "schema_version": "1.0",
        "document_sha256": lease["document_sha256"],
        "parser_profile": lease["parser_profile"],
        "source_backend": "paddleocr-general" if is_ocr else "pdf-native",
        "source_version": "3.7.0-test" if is_ocr else "1.0",
        "total_page_count": total_page_count,
        "requested_page_ordinals": requested,
        "covered_page_ordinals": [page.ordinal for page in pages],
        "pages": [page.model_dump(mode="json") for page in pages],
        "assets": [],
    }
    attempt = ParseAttemptResult(
        **attempt_payload,
        canonical_sha256=canonical_sha256(attempt_payload),
    )
    receipt = await _upload_artifact(
        client,
        lease,
        worker_id,
        name=f"attempt-{lease['attempt']}.json",
        payload=canonical_json_bytes(attempt.model_dump(mode="json")),
        media_type=PARSE_ATTEMPT_MEDIA_TYPE,
    )
    return await client.post(
        f"/worker/v1/jobs/{lease['job_id']}:complete",
        json={
            **_command(lease, worker_id),
            "result_manifest_ref": receipt["artifact_ref"],
            "result_sha256": receipt["sha256"],
            "result_size_bytes": receipt["size_bytes"],
            "manifest_schema_version": "1.0",
            "page_count": total_page_count,
            "failed_pages": failed_pages,
        },
        headers={
            "Authorization": "Bearer worker-secret",
            "Idempotency-Key": f"complete-attempt-{lease['attempt']}",
        },
    )


@pytest.mark.integration
async def test_retry_attempts_create_preview_without_mutating_active_revision(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    clock = MutableClock()
    app = create_app(
        settings=_settings(test_database_url, tmp_path),
        database=database,
        storage=LocalStorage(tmp_path),
        clock=clock,
    )
    principal = LocalPrincipalProvider().resolve("127.0.0.1")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        document_id = await _enqueue(client)
        active_revision_id = "00000000-0000-0000-0000-000000000001"
        async with database.session(principal) as session:
            document = await session.get(DocumentModel, document_id)
            assert document is not None
            session.add(
                DocumentRevisionModel(
                    id=active_revision_id,
                    document_id=document_id,
                    ordinal=1,
                    manifest={"legacy": True},
                    canonical_sha256="0" * 64,
                    total_page_count=1,
                    parser_profile="native-v1",
                    parser_schema_version="1.0",
                    chunker_version="section-page-v1",
                    quality_status="passed",
                )
            )
            await session.flush()
            document.active_revision_id = active_revision_id

        first = await _claim(client, "worker-1")
        assert first["requested_pages"] == []
        await _start(client, first, "worker-1")
        first_page = await _checkpoint_page(client, first, "worker-1", 1)
        failed_page = await _checkpoint_page(client, first, "worker-1", 2, failed=True)
        incomplete = await _complete_attempt(
            client,
            first,
            "worker-1",
            total_page_count=2,
            requested=[1, 2],
            pages=[first_page, failed_page],
            failed_pages=[2],
        )
        assert incomplete.status_code == 200
        assert incomplete.json()["status"] == "retry_wait"
        assert incomplete.json()["parser_profile"] == "ocr-v1"
        document_after_first = await client.get(f"/api/v1/documents/{document_id}")
        assert document_after_first.json()["preview_revision_id"] is None
        assert document_after_first.json()["active_revision_id"] == active_revision_id

        clock.advance(timedelta(seconds=1))
        second = await _claim(
            client,
            "worker-2",
            parser_profile="ocr-v1",
            supports_ocr=True,
        )
        assert second["attempt"] == 2
        assert second["requested_pages"] == [2]
        assert second["parser_profile"] == "ocr-v1"
        await _start(client, second, "worker-2")
        second_page = await _checkpoint_page(client, second, "worker-2", 2)
        completed = await _complete_attempt(
            client,
            second,
            "worker-2",
            total_page_count=2,
            requested=[2],
            pages=[second_page],
            failed_pages=[],
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "succeeded"
        document_response = await client.get(f"/api/v1/documents/{document_id}")
        document_payload = document_response.json()
        assert document_payload["status"] == "parsed_index_blocked"
        assert document_payload["active_revision_id"] == active_revision_id
        assert document_payload["preview_revision_id"] != active_revision_id

    async with database.session(principal) as session:
        job = await session.scalar(select(ParseJobModel))
        document = await session.get(DocumentModel, document_id)
        revisions = list(
            await session.scalars(
                select(DocumentRevisionModel).order_by(DocumentRevisionModel.ordinal)
            )
        )
        assert job is not None
        assert document is not None
        assert job.requested_pages == [2]
        assert document.active_revision_id == active_revision_id
        assert document.preview_revision_id == revisions[-1].id
        bundle = ParseResultBundle.model_validate(revisions[-1].manifest)
        assert bundle.source_backend == "mixed"
        assert bundle.source_version == "mixed"
        assert [page.source_backend for page in bundle.pages] == [
            "pdf-native",
            "paddleocr-general",
        ]
        assert await session.scalar(select(func.count()).select_from(ParseAttemptResultModel)) == 2
        assert await session.scalar(select(func.count()).select_from(RevisionPageModel)) == 2
        assert await session.scalar(select(func.count()).select_from(RevisionBlockModel)) == 2
        assert await session.scalar(select(func.count()).select_from(RevisionChunkModel)) == 2

    await database.dispose()


@pytest.mark.integration
async def test_non_ocr_page_failure_retries_only_missing_page_on_native_profile(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    clock = MutableClock()
    app = create_app(
        settings=_settings(test_database_url, tmp_path),
        database=database,
        storage=LocalStorage(tmp_path),
        clock=clock,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await _enqueue(client, filename="native-retry.pdf")
        first = await _claim(client, "native-worker-1")
        await _start(client, first, "native-worker-1")
        page_one = await _checkpoint_page(client, first, "native-worker-1", 1)
        page_two = await _checkpoint_page(
            client,
            first,
            "native-worker-1",
            2,
            failed=True,
            failure_code="PDF_PAGE_PARSE_FAILED",
        )
        incomplete = await _complete_attempt(
            client,
            first,
            "native-worker-1",
            total_page_count=2,
            requested=[1, 2],
            pages=[page_one, page_two],
            failed_pages=[2],
        )
        assert incomplete.status_code == 200
        assert incomplete.json()["status"] == "retry_wait"
        assert incomplete.json()["parser_profile"] == "native-v1"

        clock.advance(timedelta(seconds=1))
        second = await _claim(client, "native-worker-2")
        assert second["parser_profile"] == "native-v1"
        assert second["requested_pages"] == [2]

    await database.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("schema_version", "canonical_hash", "expected_status", "expected_code"),
    [
        ("1.0", "bad", 409, "HASH_MISMATCH"),
        ("2.0", "valid", 422, "INVALID_REQUEST"),
    ],
)
async def test_complete_rejects_bad_attempt_hash_or_schema(
    test_database_url: str,
    tmp_path: Path,
    schema_version: str,
    canonical_hash: str,
    expected_status: int,
    expected_code: str,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    app = create_app(
        settings=_settings(test_database_url, tmp_path),
        database=database,
        storage=LocalStorage(tmp_path),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await _enqueue(client)
        lease = await _claim(client, "worker-invalid")
        await _start(client, lease, "worker-invalid")
        payload = {
            "schema_version": schema_version,
            "document_sha256": lease["document_sha256"],
            "parser_profile": "native-v1",
            "source_backend": "pdf-native",
            "source_version": "1.0",
            "total_page_count": 1,
            "requested_page_ordinals": [1],
            "covered_page_ordinals": [],
            "pages": [],
            "assets": [],
        }
        payload["canonical_sha256"] = (
            "b" * 64 if canonical_hash == "bad" else canonical_sha256(payload)
        )
        receipt = await _upload_artifact(
            client,
            lease,
            "worker-invalid",
            name="attempt-invalid.json",
            payload=canonical_json_bytes(payload),
            media_type=PARSE_ATTEMPT_MEDIA_TYPE,
        )
        response = await client.post(
            f"/worker/v1/jobs/{lease['job_id']}:complete",
            json={
                **_command(lease, "worker-invalid"),
                "result_manifest_ref": receipt["artifact_ref"],
                "result_sha256": receipt["sha256"],
                "result_size_bytes": receipt["size_bytes"],
                "manifest_schema_version": "1.0",
                "page_count": 1,
                "failed_pages": [1],
            },
            headers={
                "Authorization": "Bearer worker-secret",
                "Idempotency-Key": f"invalid-{schema_version}-{canonical_hash}",
            },
        )
        assert response.status_code == expected_status
        assert response.json()["code"] == expected_code

    await database.dispose()


@pytest.mark.integration
async def test_exhausted_incomplete_coverage_never_creates_preview(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    app = create_app(
        settings=_settings(test_database_url, tmp_path, max_attempts=1),
        database=database,
        storage=LocalStorage(tmp_path),
    )
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        document_id = await _enqueue(client)
        lease = await _claim(client, "worker-partial")
        await _start(client, lease, "worker-partial")
        page = await _checkpoint_page(client, lease, "worker-partial", 1)
        completed = await _complete_attempt(
            client,
            lease,
            "worker-partial",
            total_page_count=2,
            requested=[1, 2],
            pages=[page],
            failed_pages=[2],
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "partial_failed"

    async with database.session(principal) as session:
        document = await session.get(DocumentModel, document_id)
        assert document is not None
        assert document.preview_revision_id is None
        assert document.active_revision_id is None
        assert document.status == "partial_failed"
        assert await session.scalar(select(func.count()).select_from(DocumentRevisionModel)) == 0

    await database.dispose()


@pytest.mark.integration
async def test_native_pptx_claim_does_not_require_rendering_capability(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    app = create_app(
        settings=_settings(test_database_url, tmp_path),
        database=database,
        storage=LocalStorage(tmp_path),
    )
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await _enqueue(
            client,
            payload=b"PK\x03\x04native-pptx-structure",
            media_type=PPTX_MEDIA_TYPE,
            filename="slides.pptx",
        )
        lease = await _claim(
            client,
            "worker-without-libreoffice",
            media_type=PPTX_MEDIA_TYPE,
            supports_rendering=False,
        )
        assert lease["media_type"] == PPTX_MEDIA_TYPE

    async with database.session(principal) as session:
        job = await session.scalar(select(ParseJobModel))
        assert job is not None
        assert job.requires_rendering is False

    await database.dispose()
