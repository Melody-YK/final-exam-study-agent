import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import LocalPrincipalProvider
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import PageCheckpointModel, ParseJobModel
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.providers.protocols import ObjectMetadata
from study_agent.storage.local import LocalStorage
from study_contracts import (
    PARSE_ATTEMPT_MEDIA_TYPE,
    PARSE_PAGE_MEDIA_TYPE,
    PARSER_RAW_MEDIA_TYPE,
    Block,
    BlockType,
    BoundingBox,
    Page,
    PageQuality,
    PageQualityStatus,
    ParseAttemptResult,
    canonical_json_bytes,
    canonical_sha256,
)


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class SecondArtifactHeadMismatchStorage(LocalStorage):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.artifact_head_calls = 0

    async def head(self, object_key: str) -> ObjectMetadata:
        metadata = await super().head(object_key)
        if "job-artifact" not in object_key:
            return metadata
        self.artifact_head_calls += 1
        if self.artifact_head_calls < 2:
            return metadata
        return ObjectMetadata(
            object_key=metadata.object_key,
            size_bytes=metadata.size_bytes + 1,
            content_type=metadata.content_type,
            sha256=metadata.sha256,
            etag=metadata.etag,
        )


async def _create_and_claim(client: AsyncClient) -> tuple[dict[str, object], str]:
    payload = b"%PDF-1.7\nworker commands"
    course = await client.post("/api/v1/courses", json={"title": "数据库"})
    created = await client.post(
        f"/api/v1/courses/{course.json()['id']}/documents",
        json={
            "filename": "chapter.pdf",
            "media_type": "application/pdf",
            "size_bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
            "corpus_role": "corpus",
        },
    )
    document_id = created.json()["document"]["id"]
    upload = created.json()["upload"]
    await client.put(upload["url"], content=payload, headers={"Content-Type": "application/pdf"})
    await client.post(
        f"/api/v1/documents/{document_id}/upload:complete",
        json={"upload_session_id": upload["id"]},
        headers={"Idempotency-Key": "enqueue-worker-command-job"},
    )
    claimed = await client.post(
        "/worker/v1/jobs:claim",
        json={
            "worker_id": "worker-1",
            "capabilities": {
                "parser_profiles": ["native-v1"],
                "media_types": ["application/pdf"],
                "supports_rendering": True,
                "max_input_bytes": 10_000,
                "max_pages": 100,
            },
        },
        headers={"Authorization": "Bearer worker-secret"},
    )
    return claimed.json()["lease"], document_id


def _lease_body(lease: dict[str, object]) -> dict[str, object]:
    return {
        "worker_id": "worker-1",
        "lease_token": lease["lease_token"],
        "lease_version": lease["lease_version"],
        "attempt": lease["attempt"],
        "deletion_epoch": lease["deletion_epoch"],
    }


def _artifact_headers(
    lease: dict[str, object], media_type: str = "application/json"
) -> dict[str, str]:
    return {
        "Authorization": "Bearer worker-secret",
        "X-Worker-ID": "worker-1",
        "X-Lease-Token": str(lease["lease_token"]),
        "X-Lease-Version": str(lease["lease_version"]),
        "X-Attempt": str(lease["attempt"]),
        "X-Deletion-Epoch": str(lease["deletion_epoch"]),
        "Content-Type": media_type,
    }


@pytest.mark.integration
async def test_worker_commands_are_leased_idempotent_and_artifact_scoped(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    clock = MutableClock(datetime(2026, 7, 19, 2, 0, tzinfo=UTC))
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        worker_token=SecretStr("worker-secret"),
        worker_lease_seconds=5,
    )
    app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        clock=clock,
    )
    auth = {"Authorization": "Bearer worker-secret"}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        lease, document_id = await _create_and_claim(client)
        job_id = str(lease["job_id"])
        command = _lease_body(lease)

        missing_idempotency = await client.post(
            f"/worker/v1/jobs/{job_id}:start",
            json=command,
            headers=auth,
        )
        assert missing_idempotency.status_code == 422
        assert missing_idempotency.headers["content-type"].startswith("application/problem+json")
        assert missing_idempotency.json()["code"] == "INVALID_REQUEST"

        started = await client.post(
            f"/worker/v1/jobs/{job_id}:start",
            json=command,
            headers={**auth, "Idempotency-Key": "start-job"},
        )
        assert started.status_code == 200
        assert started.json()["status"] == "parsing"
        replayed_start = await client.post(
            f"/worker/v1/jobs/{job_id}:start",
            json=command,
            headers={**auth, "Idempotency-Key": "start-job"},
        )
        assert replayed_start.json() == started.json()

        clock.advance(timedelta(seconds=3))
        heartbeat = await client.put(
            f"/worker/v1/jobs/{job_id}/heartbeat",
            json={
                **command,
                "progress": {"phase": "parse", "completed_pages": 1, "total_pages": 2},
            },
            headers={**auth, "Idempotency-Key": "heartbeat-1"},
        )
        assert heartbeat.status_code == 200
        assert heartbeat.json()["progress"]["completed_pages"] == 1

        raw_payload = canonical_json_bytes({"page": 1, "text": "进程管理"})
        raw_artifact = await client.put(
            f"{lease['artifact_upload_url']}/raw-page-1?artifact_schema_version=1.0",
            content=raw_payload,
            headers=_artifact_headers(lease, PARSER_RAW_MEDIA_TYPE),
        )
        raw = raw_artifact.json()
        text = "进程管理"
        normalized_page = Page(
            ordinal=1,
            width=1200,
            height=1600,
            source_backend="pdf-native",
            source_version="1.0",
            raw_result_ref=raw["artifact_ref"],
            blocks=[
                Block(
                    id="page-1-block-0",
                    type=BlockType.TITLE,
                    text=text,
                    bbox_norm=BoundingBox(x=0.1, y=0.1, width=0.8, height=0.1),
                    reading_order=0,
                    confidence=1,
                    source_backend="pdf-native",
                    source_version="1.0",
                    raw_result_ref=raw["artifact_ref"],
                    section_path=[text],
                )
            ],
            quality=PageQuality(
                status=PageQualityStatus.PASSED,
                text_layer="native",
                text_char_count=len(text),
                block_count=1,
            ),
        )
        page_payload = canonical_json_bytes(normalized_page.model_dump(mode="json"))
        page_artifact = await client.put(
            f"{lease['artifact_upload_url']}/page-1?artifact_schema_version=1.0",
            content=page_payload,
            headers=_artifact_headers(lease, PARSE_PAGE_MEDIA_TYPE),
        )
        assert page_artifact.status_code == 201
        page = page_artifact.json()

        bad_checkpoint = await client.put(
            f"/worker/v1/jobs/{job_id}/pages/1/checkpoint",
            json={
                **command,
                "page_ordinal": 1,
                "status": "succeeded",
                "output_ref": page["artifact_ref"],
                "output_sha256": page["sha256"],
                "output_size_bytes": page["size_bytes"] + 1,
                "output_schema_version": "1.0",
                "source_backend": "pdf-native",
                "source_version": "1.0",
            },
            headers={**auth, "Idempotency-Key": "checkpoint-page-1"},
        )
        assert bad_checkpoint.status_code == 409
        assert bad_checkpoint.json()["code"] == "HASH_MISMATCH"

        checkpoint_body = {
            **command,
            "page_ordinal": 1,
            "status": "succeeded",
            "output_ref": page["artifact_ref"],
            "output_sha256": page["sha256"],
            "output_size_bytes": page["size_bytes"],
            "output_schema_version": "1.0",
            "source_backend": "pdf-native",
            "source_version": "1.0",
        }
        checkpoint, checkpoint_replay = await asyncio.gather(
            client.put(
                f"/worker/v1/jobs/{job_id}/pages/1/checkpoint",
                json=checkpoint_body,
                headers={**auth, "Idempotency-Key": "checkpoint-page-1"},
            ),
            client.put(
                f"/worker/v1/jobs/{job_id}/pages/1/checkpoint",
                json=checkpoint_body,
                headers={**auth, "Idempotency-Key": "checkpoint-page-1"},
            ),
        )
        assert checkpoint.status_code == 200
        assert checkpoint_replay.status_code == 200
        assert checkpoint_replay.json() == checkpoint.json()

        attempt_payload = {
            "schema_version": "1.0",
            "document_sha256": lease["document_sha256"],
            "parser_profile": "native-v1",
            "source_backend": "pdf-native",
            "source_version": "1.0",
            "total_page_count": 1,
            "requested_page_ordinals": [1],
            "covered_page_ordinals": [1],
            "pages": [normalized_page.model_dump(mode="json")],
            "assets": [],
        }
        attempt = ParseAttemptResult(
            **attempt_payload,
            canonical_sha256=canonical_sha256(attempt_payload),
        )
        manifest_payload = canonical_json_bytes(attempt.model_dump(mode="json"))
        manifest_artifact = await client.put(
            f"{lease['artifact_upload_url']}/manifest?artifact_schema_version=1.0",
            content=manifest_payload,
            headers=_artifact_headers(lease, PARSE_ATTEMPT_MEDIA_TYPE),
        )
        manifest = manifest_artifact.json()
        complete_body = {
            **command,
            "result_manifest_ref": manifest["artifact_ref"],
            "result_sha256": manifest["sha256"],
            "result_size_bytes": manifest["size_bytes"],
            "manifest_schema_version": "1.0",
            "page_count": 1,
            "failed_pages": [],
        }
        completed = await client.post(
            f"/worker/v1/jobs/{job_id}:complete",
            json=complete_body,
            headers={**auth, "Idempotency-Key": "complete-job"},
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "succeeded"
        assert completed.json()["progress"] == {
            "phase": "completed",
            "completed_pages": 1,
            "total_pages": 1,
        }
        completed_replay = await client.post(
            f"/worker/v1/jobs/{job_id}:complete",
            json=complete_body,
            headers={**auth, "Idempotency-Key": "complete-job"},
        )
        assert completed_replay.json() == completed.json()
        checkpoint_after_lease_closed = await client.put(
            f"/worker/v1/jobs/{job_id}/pages/1/checkpoint",
            json=checkpoint_body,
            headers={**auth, "Idempotency-Key": "checkpoint-page-1"},
        )
        assert checkpoint_after_lease_closed.json() == checkpoint.json()

        late_heartbeat = await client.put(
            f"/worker/v1/jobs/{job_id}/heartbeat",
            json={
                **command,
                "progress": {"phase": "late", "completed_pages": 2, "total_pages": 2},
            },
            headers={**auth, "Idempotency-Key": "late-heartbeat"},
        )
        assert late_heartbeat.status_code == 409
        assert late_heartbeat.json()["code"] == "LEASE_LOST"

        document = await client.get(f"/api/v1/documents/{document_id}")
        assert document.json()["status"] == "parsed_index_blocked"

    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    async with database.session(principal) as session:
        job = await session.scalar(select(ParseJobModel))
        assert job is not None
        assert job.lease_token_hash is None
        assert job.result_manifest_ref == manifest["artifact_ref"]
        checkpoint_count = await session.scalar(
            select(func.count()).select_from(PageCheckpointModel)
        )
        assert checkpoint_count == 1

    await database.dispose()


@pytest.mark.integration
async def test_retryable_failure_waits_for_backoff_and_invalidates_old_lease(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    clock = MutableClock(datetime(2026, 7, 19, 6, 0, tzinfo=UTC))
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        worker_token=SecretStr("worker-secret"),
        worker_lease_seconds=10,
        job_retry_base_seconds=5,
    )
    app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        clock=clock,
    )
    auth = {"Authorization": "Bearer worker-secret"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        lease, _ = await _create_and_claim(client)
        job_id = str(lease["job_id"])
        command = _lease_body(lease)
        await client.post(
            f"/worker/v1/jobs/{job_id}:start",
            json=command,
            headers={**auth, "Idempotency-Key": "start-before-retry"},
        )
        failed = await client.post(
            f"/worker/v1/jobs/{job_id}:fail",
            json={
                **command,
                "error_code": "PARSER_TIMEOUT",
                "retryable": True,
                "error_summary": "parser timed out",
            },
            headers={**auth, "Idempotency-Key": "retryable-failure"},
        )
        assert failed.status_code == 200
        assert failed.json()["status"] == "retry_wait"

        too_early = await client.post(
            "/worker/v1/jobs:claim",
            json={
                "worker_id": "worker-2",
                "capabilities": {
                    "parser_profiles": ["native-v1"],
                    "media_types": ["application/pdf"],
                    "supports_rendering": True,
                    "max_input_bytes": 10_000,
                    "max_pages": 100,
                },
            },
            headers=auth,
        )
        assert too_early.json()["lease"] is None

        clock.advance(timedelta(seconds=5))
        reclaimed = await client.post(
            "/worker/v1/jobs:claim",
            json={
                "worker_id": "worker-2",
                "capabilities": {
                    "parser_profiles": ["native-v1"],
                    "media_types": ["application/pdf"],
                    "supports_rendering": True,
                    "max_input_bytes": 10_000,
                    "max_pages": 100,
                },
            },
            headers=auth,
        )
        new_lease = reclaimed.json()["lease"]
        assert new_lease["attempt"] == 2
        assert new_lease["lease_version"] == 2

        late = await client.put(
            f"/worker/v1/jobs/{job_id}/heartbeat",
            json={
                **command,
                "progress": {"phase": "late", "completed_pages": 0},
            },
            headers={**auth, "Idempotency-Key": "old-lease-heartbeat"},
        )
        assert late.status_code == 409
        assert late.json()["code"] == "LEASE_LOST"

    await database.dispose()


@pytest.mark.integration
async def test_checkpoint_rechecks_artifact_under_final_transaction_lock(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    clock = MutableClock(datetime(2026, 7, 19, 7, 0, tzinfo=UTC))
    storage = SecondArtifactHeadMismatchStorage(tmp_path)
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        worker_token=SecretStr("worker-secret"),
    )
    app = create_app(
        settings=settings,
        database=database,
        storage=storage,
        clock=clock,
    )
    auth = {"Authorization": "Bearer worker-secret"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        lease, _ = await _create_and_claim(client)
        job_id = str(lease["job_id"])
        command = _lease_body(lease)
        await client.post(
            f"/worker/v1/jobs/{job_id}:start",
            json=command,
            headers={**auth, "Idempotency-Key": "start-final-head"},
        )
        payload = b'{"page":1}'
        artifact = await client.put(
            f"{lease['artifact_upload_url']}/page-1?artifact_schema_version=1.0",
            content=payload,
            headers=_artifact_headers(lease),
        )
        receipt = artifact.json()
        checkpoint = await client.put(
            f"/worker/v1/jobs/{job_id}/pages/1/checkpoint",
            json={
                **command,
                "page_ordinal": 1,
                "status": "succeeded",
                "output_ref": receipt["artifact_ref"],
                "output_sha256": receipt["sha256"],
                "output_size_bytes": receipt["size_bytes"],
                "output_schema_version": "1.0",
                "source_backend": "pdf-native",
                "source_version": "1.0",
            },
            headers={**auth, "Idempotency-Key": "checkpoint-final-head"},
        )
        assert checkpoint.status_code == 409
        assert checkpoint.json()["code"] == "HASH_MISMATCH"
        assert storage.artifact_head_calls >= 2

    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    async with database.session(principal) as session:
        assert await session.scalar(select(func.count()).select_from(PageCheckpointModel)) == 0
    await database.dispose()


@pytest.mark.integration
async def test_document_delete_rejects_late_complete_before_result_activation(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    clock = MutableClock(datetime(2026, 7, 19, 8, 30, tzinfo=UTC))
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        worker_token=SecretStr("worker-secret"),
    )
    app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        clock=clock,
    )
    auth = {"Authorization": "Bearer worker-secret"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        lease, document_id = await _create_and_claim(client)
        job_id = str(lease["job_id"])
        command = _lease_body(lease)
        await client.post(
            f"/worker/v1/jobs/{job_id}:start",
            json=command,
            headers={**auth, "Idempotency-Key": "start-before-delete"},
        )
        manifest_payload = b'{"schema_version":"1.0","pages":[]}'
        artifact = await client.put(
            f"{lease['artifact_upload_url']}/manifest?artifact_schema_version=1.0",
            content=manifest_payload,
            headers=_artifact_headers(lease),
        )
        receipt = artifact.json()
        deleted = await client.delete(
            f"/api/v1/documents/{document_id}",
            headers={"Idempotency-Key": "delete-before-late-complete"},
        )
        assert deleted.status_code == 202
        late_complete = await client.post(
            f"/worker/v1/jobs/{job_id}:complete",
            json={
                **command,
                "result_manifest_ref": receipt["artifact_ref"],
                "result_sha256": receipt["sha256"],
                "result_size_bytes": receipt["size_bytes"],
                "manifest_schema_version": "1.0",
                "page_count": 0,
                "failed_pages": [],
            },
            headers={**auth, "Idempotency-Key": "late-complete-after-delete"},
        )
        assert late_complete.status_code == 409
        assert late_complete.json()["code"] == "LEASE_LOST"

    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    async with database.session(principal) as session:
        job = await session.scalar(select(ParseJobModel))
        assert job is None
    await database.dispose()
