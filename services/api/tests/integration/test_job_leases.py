import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import LocalPrincipalProvider
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import DocumentModel, ParseJobModel, StoredObjectModel
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.jobs.service import enqueue_parse_job
from study_agent.storage.local import LocalStorage


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 19, 4, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class RecordingWaiter:
    def __init__(self, database: "TrackingDatabase") -> None:
        self._database = database
        self.timeouts: list[float] = []

    async def wait(self, timeout_seconds: float) -> None:
        assert self._database.active_worker_sessions == 0
        self.timeouts.append(timeout_seconds)


class TrackingDatabase(Database):
    def __init__(self, database_url: str) -> None:
        super().__init__(database_url)
        self.active_worker_sessions = 0

    @asynccontextmanager
    async def worker_session(self, worker_id: str) -> AsyncIterator[AsyncSession]:
        async with super().worker_session(worker_id) as session:
            self.active_worker_sessions += 1
            try:
                yield session
            finally:
                self.active_worker_sessions -= 1


async def _enqueue_pdf(client: AsyncClient, suffix: str = "one") -> tuple[str, bytes]:
    payload = f"%PDF-1.7\npersistent job {suffix}".encode()
    course = await client.post("/api/v1/courses", json={"title": f"操作系统-{suffix}"})
    created = await client.post(
        f"/api/v1/courses/{course.json()['id']}/documents",
        json={
            "filename": f"chapter-{suffix}.pdf",
            "media_type": "application/pdf",
            "size_bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
            "corpus_role": "corpus",
        },
    )
    document = created.json()["document"]
    upload = created.json()["upload"]
    await client.put(
        upload["url"],
        content=payload,
        headers={"Content-Type": "application/pdf"},
    )
    completed = await client.post(
        f"/api/v1/documents/{document['id']}/upload:complete",
        json={"upload_session_id": upload["id"]},
        headers={"Idempotency-Key": f"complete-and-enqueue-{suffix}"},
    )
    assert completed.status_code == 202
    assert completed.json()["status"] == "queued"
    return document["id"], payload


def _claim_body(worker_id: str, *, media_type: str = "application/pdf") -> dict[str, object]:
    return {
        "worker_id": worker_id,
        "capabilities": {
            "parser_profiles": ["native-v1"],
            "media_types": [media_type],
            "supports_ocr": False,
            "supports_rendering": True,
            "max_input_bytes": 10_000,
            "max_pages": 100,
        },
    }


@pytest.mark.integration
async def test_upload_enqueues_and_only_one_capable_worker_claims(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
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
    )
    headers = {"Authorization": "Bearer worker-secret"}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        document_id, payload = await _enqueue_pdf(client)

        incapable = await client.post(
            "/worker/v1/jobs:claim",
            json=_claim_body("image-worker", media_type="image/png"),
            headers=headers,
        )
        assert incapable.status_code == 200
        assert incapable.json()["lease"] is None

        zero_capability = await client.post(
            "/worker/v1/jobs:claim",
            json={
                "worker_id": "idle-worker",
                "capabilities": {
                    "parser_profiles": [],
                    "media_types": [],
                    "max_input_bytes": 10_000,
                    "max_pages": 100,
                },
            },
            headers=headers,
        )
        assert zero_capability.status_code == 200
        assert zero_capability.json()["lease"] is None

        claimed = await client.post(
            "/worker/v1/jobs:claim",
            json=_claim_body("worker-1"),
            headers=headers,
        )
        assert claimed.status_code == 200
        lease = claimed.json()["lease"]
        assert lease["job_type"] == "parse"
        assert lease["attempt"] == 1
        assert lease["lease_version"] == 1
        assert len(lease["lease_token"]) >= 24

        input_headers = {
            **headers,
            "X-Worker-ID": "worker-1",
            "X-Lease-Token": lease["lease_token"],
            "X-Attempt": str(lease["attempt"]),
            "X-Deletion-Epoch": str(lease["deletion_epoch"]),
        }
        input_response = await client.get(lease["input_url"], headers=input_headers)
        assert input_response.status_code == 200
        assert input_response.content == payload
        wrong_token = await client.get(
            lease["input_url"],
            headers={**input_headers, "X-Lease-Token": "wrong-token-with-enough-entropy"},
        )
        assert wrong_token.status_code == 409
        assert wrong_token.json()["code"] == "LEASE_LOST"

        second = await client.post(
            "/worker/v1/jobs:claim",
            json=_claim_body("worker-2"),
            headers=headers,
        )
        assert second.status_code == 200
        assert second.json()["lease"] is None

        snapshot = await client.get(f"/api/v1/parse-jobs/{lease['job_id']}")
        assert snapshot.status_code == 200
        assert snapshot.json()["status"] == "leased"
        principal = LocalPrincipalProvider().resolve("127.0.0.1")
        async with database.session(principal) as session:
            leased_job = await session.scalar(select(ParseJobModel))
            assert leased_job is not None
            assert leased_job.lease_token_hash is not None
            assert leased_job.lease_token_hash != lease["lease_token"]

        deleted = await client.delete(
            f"/api/v1/documents/{document_id}",
            headers={"Idempotency-Key": "delete-leased-document"},
        )
        assert deleted.status_code == 202
        late_input = await client.get(lease["input_url"], headers=input_headers)
        assert late_input.status_code == 409
        assert late_input.json()["code"] == "LEASE_LOST"

    async with database.session(principal) as session:
        job = await session.scalar(select(ParseJobModel))
        assert job is None

    await database.dispose()


@pytest.mark.integration
async def test_expired_lease_is_reclaimed_and_old_worker_is_rejected(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    clock = MutableClock()
    database = Database(test_database_url)
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
        await _enqueue_pdf(client, "expiry")
        first = (
            await client.post(
                "/worker/v1/jobs:claim",
                json=_claim_body("worker-old"),
                headers=auth,
            )
        ).json()["lease"]
        clock.advance(timedelta(seconds=6))
        second = (
            await client.post(
                "/worker/v1/jobs:claim",
                json=_claim_body("worker-new"),
                headers=auth,
            )
        ).json()["lease"]
        assert second["job_id"] == first["job_id"]
        assert second["attempt"] == 2
        assert second["lease_version"] == 2
        assert second["lease_token"] != first["lease_token"]

        late_start = await client.post(
            f"/worker/v1/jobs/{first['job_id']}:start",
            json={
                "worker_id": "worker-old",
                "lease_token": first["lease_token"],
                "lease_version": first["lease_version"],
                "attempt": first["attempt"],
                "deletion_epoch": first["deletion_epoch"],
            },
            headers={**auth, "Idempotency-Key": "late-start"},
        )
        assert late_start.status_code == 409
        assert late_start.json()["code"] == "LEASE_LOST"

    await database.dispose()


@pytest.mark.integration
async def test_two_workers_claim_distinct_jobs_concurrently(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
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
    )
    auth = {"Authorization": "Bearer worker-secret"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as setup_client:
        await _enqueue_pdf(setup_client, "concurrent-a")
        await _enqueue_pdf(setup_client, "concurrent-b")

    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as first_client,
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as second_client,
    ):
        first_response, second_response = await asyncio.gather(
            first_client.post(
                "/worker/v1/jobs:claim",
                json=_claim_body("worker-a"),
                headers=auth,
            ),
            second_client.post(
                "/worker/v1/jobs:claim",
                json=_claim_body("worker-b"),
                headers=auth,
            ),
        )
        leases = [first_response.json()["lease"], second_response.json()["lease"]]
        assert all(lease is not None for lease in leases)
        assert len({lease["job_id"] for lease in leases}) == 2

    await database.dispose()


@pytest.mark.integration
async def test_queued_job_survives_api_database_reconstruction(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        worker_token=SecretStr("worker-secret"),
    )
    storage = LocalStorage(tmp_path)
    first_database = Database(test_database_url)
    first_app = create_app(settings=settings, database=first_database, storage=storage)
    async with AsyncClient(
        transport=ASGITransport(app=first_app), base_url="http://testserver"
    ) as client:
        await _enqueue_pdf(client, "restart")
    await first_database.dispose()

    restarted_database = Database(test_database_url)
    restarted_app = create_app(
        settings=settings,
        database=restarted_database,
        storage=storage,
    )
    async with AsyncClient(
        transport=ASGITransport(app=restarted_app), base_url="http://testserver"
    ) as client:
        claimed = await client.post(
            "/worker/v1/jobs:claim",
            json=_claim_body("worker-after-restart"),
            headers={"Authorization": "Bearer worker-secret"},
        )
        assert claimed.status_code == 200
        assert claimed.json()["lease"] is not None

    await restarted_database.dispose()


@pytest.mark.integration
async def test_database_allows_only_one_nonterminal_job_per_document_under_concurrency(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
    )
    app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
    )
    payload = b"%PDF-1.7\nconcurrent enqueue"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        course = await client.post("/api/v1/courses", json={"title": "并发入队"})
        created = await client.post(
            f"/api/v1/courses/{course.json()['id']}/documents",
            json={
                "filename": "enqueue.pdf",
                "media_type": "application/pdf",
                "size_bytes": len(payload),
                "sha256": sha256(payload).hexdigest(),
                "corpus_role": "corpus",
            },
        )
        document_id = created.json()["document"]["id"]

    principal = LocalPrincipalProvider().resolve("127.0.0.1")

    async def enqueue_once() -> None:
        async with database.session(principal) as session:
            document = await session.get(DocumentModel, document_id)
            assert document is not None
            stored_object = await session.get(StoredObjectModel, document.stored_object_id)
            assert stored_object is not None
            await enqueue_parse_job(
                session,
                document,
                stored_object,
                now=datetime(2026, 7, 19, 5, 0, tzinfo=UTC),
                max_attempts=3,
                event_retention=timedelta(hours=1),
            )

    results = await asyncio.gather(enqueue_once(), enqueue_once(), return_exceptions=True)
    assert sum(isinstance(result, IntegrityError) for result in results) == 1
    assert sum(result is None for result in results) == 1
    async with database.session(principal) as session:
        assert await session.scalar(select(func.count()).select_from(ParseJobModel)) == 1

    await database.dispose()


@pytest.mark.integration
async def test_claim_ignores_job_with_stale_document_deletion_epoch(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
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
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await _enqueue_pdf(client, "stale-epoch")
        principal = LocalPrincipalProvider().resolve("127.0.0.1")
        async with database.session(principal) as session:
            job = await session.scalar(select(ParseJobModel))
            assert job is not None
            job.document_deletion_epoch += 1

        claim = await client.post(
            "/worker/v1/jobs:claim",
            json=_claim_body("worker-stale-epoch"),
            headers={"Authorization": "Bearer worker-secret"},
        )
        assert claim.status_code == 200
        assert claim.json()["lease"] is None

    await database.dispose()


@pytest.mark.integration
async def test_reclaim_after_restart_requests_only_pages_without_successful_checkpoints(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    clock = MutableClock()
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        worker_token=SecretStr("worker-secret"),
        worker_lease_seconds=5,
    )
    storage = LocalStorage(tmp_path)
    first_database = Database(test_database_url)
    first_app = create_app(
        settings=settings,
        database=first_database,
        storage=storage,
        clock=clock,
    )
    auth = {"Authorization": "Bearer worker-secret"}
    async with AsyncClient(
        transport=ASGITransport(app=first_app), base_url="http://testserver"
    ) as client:
        await _enqueue_pdf(client, "resume-pages")
        lease = (
            await client.post(
                "/worker/v1/jobs:claim",
                json=_claim_body("worker-before-restart"),
                headers=auth,
            )
        ).json()["lease"]
        command = {
            "worker_id": "worker-before-restart",
            "lease_token": lease["lease_token"],
            "lease_version": lease["lease_version"],
            "attempt": lease["attempt"],
            "deletion_epoch": lease["deletion_epoch"],
        }
        await client.post(
            f"/worker/v1/jobs/{lease['job_id']}:start",
            json=command,
            headers={**auth, "Idempotency-Key": "start-resume-pages"},
        )
        await client.put(
            f"/worker/v1/jobs/{lease['job_id']}/heartbeat",
            json={
                **command,
                "progress": {"phase": "parse", "completed_pages": 1, "total_pages": 3},
            },
            headers={**auth, "Idempotency-Key": "heartbeat-resume-pages"},
        )
        artifact_payload = b'{"page":1}'
        artifact = await client.put(
            f"{lease['artifact_upload_url']}/page-1?artifact_schema_version=1.0",
            content=artifact_payload,
            headers={
                **auth,
                "X-Worker-ID": "worker-before-restart",
                "X-Lease-Token": lease["lease_token"],
                "X-Lease-Version": str(lease["lease_version"]),
                "X-Attempt": str(lease["attempt"]),
                "X-Deletion-Epoch": str(lease["deletion_epoch"]),
                "Content-Type": "application/json",
            },
        )
        receipt = artifact.json()
        checkpoint = await client.put(
            f"/worker/v1/jobs/{lease['job_id']}/pages/1/checkpoint",
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
            headers={**auth, "Idempotency-Key": "checkpoint-resume-page-1"},
        )
        assert checkpoint.status_code == 200

    await first_database.dispose()
    clock.advance(timedelta(seconds=6))

    restarted_database = Database(test_database_url)
    restarted_app = create_app(
        settings=settings,
        database=restarted_database,
        storage=storage,
        clock=clock,
    )
    async with AsyncClient(
        transport=ASGITransport(app=restarted_app), base_url="http://testserver"
    ) as client:
        reclaimed = await client.post(
            "/worker/v1/jobs:claim",
            json=_claim_body("worker-after-restart"),
            headers=auth,
        )
        assert reclaimed.status_code == 200
        assert reclaimed.json()["lease"]["attempt"] == 2
        assert reclaimed.json()["lease"]["requested_pages"] == [2, 3]

    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    async with restarted_database.session(principal) as session:
        reclaimed_job = await session.scalar(select(ParseJobModel))
        assert reclaimed_job is not None
        assert reclaimed_job.requested_pages == [2, 3]

    await restarted_database.dispose()


@pytest.mark.integration
async def test_max_attempt_expiry_marks_job_and_document_failed(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    clock = MutableClock()
    database = Database(test_database_url)
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        worker_token=SecretStr("worker-secret"),
        worker_lease_seconds=5,
        job_max_attempts=1,
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
        document_id, _ = await _enqueue_pdf(client, "max-attempts")
        lease = (
            await client.post(
                "/worker/v1/jobs:claim",
                json=_claim_body("worker-max-attempts"),
                headers=auth,
            )
        ).json()["lease"]
        clock.advance(timedelta(seconds=6))
        exhausted = await client.post(
            "/worker/v1/jobs:claim",
            json=_claim_body("worker-after-exhaustion"),
            headers=auth,
        )
        assert exhausted.json()["lease"] is None
        job = await client.get(f"/api/v1/parse-jobs/{lease['job_id']}")
        document = await client.get(f"/api/v1/documents/{document_id}")
        assert job.json()["status"] == "failed"
        assert job.json()["failure_code"] == "MAX_ATTEMPTS_EXCEEDED"
        assert document.json()["status"] == "failed"

    await database.dispose()


@pytest.mark.integration
async def test_claim_long_poll_waits_without_holding_a_worker_transaction(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = TrackingDatabase(test_database_url)
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        worker_token=SecretStr("worker-secret"),
    )
    waiter = RecordingWaiter(database)
    app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        claim_waiter=waiter,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/worker/v1/jobs:claim?wait_seconds=0.15",
            json=_claim_body("worker-long-poll"),
            headers={"Authorization": "Bearer worker-secret"},
        )
    assert response.status_code == 200
    assert response.json()["lease"] is None
    assert len(waiter.timeouts) == 3
    assert sum(waiter.timeouts) == pytest.approx(0.15)
    await database.dispose()
