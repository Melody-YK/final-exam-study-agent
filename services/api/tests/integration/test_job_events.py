from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import update

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.config import AppMode, Settings
from study_agent.identity.principal import LocalPrincipalProvider, Principal
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import JobEventModel
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.jobs.events import JobEventReader
from study_agent.storage.local import LocalStorage
from study_contracts import JobEventEnvelope


class FixedClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 19, 3, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


class ImmediateWaiter:
    async def wait(self, timeout_seconds: float) -> None:
        del timeout_seconds


@pytest.mark.integration
async def test_sse_replays_after_restart_and_reports_expired_history(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    clock = FixedClock()
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        worker_token=SecretStr("worker-secret"),
        job_event_retention_seconds=60,
    )
    database = Database(test_database_url)
    storage = LocalStorage(tmp_path)
    waiter = ImmediateWaiter()
    app = create_app(
        settings=settings,
        database=database,
        storage=storage,
        clock=clock,
        claim_waiter=waiter,
    )
    payload = b"%PDF-1.7\nevents"
    auth = {"Authorization": "Bearer worker-secret"}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        course = await client.post("/api/v1/courses", json={"title": "事件"})
        created = await client.post(
            f"/api/v1/courses/{course.json()['id']}/documents",
            json={
                "filename": "events.pdf",
                "media_type": "application/pdf",
                "size_bytes": len(payload),
                "sha256": sha256(payload).hexdigest(),
                "corpus_role": "corpus",
            },
        )
        document_id = created.json()["document"]["id"]
        upload = created.json()["upload"]
        await client.put(
            upload["url"], content=payload, headers={"Content-Type": "application/pdf"}
        )
        await client.post(
            f"/api/v1/documents/{document_id}/upload:complete",
            json={"upload_session_id": upload["id"]},
            headers={"Idempotency-Key": "enqueue-events"},
        )
        claim = await client.post(
            "/worker/v1/jobs:claim",
            json={
                "worker_id": "worker-events",
                "capabilities": {
                    "parser_profiles": ["native-v1"],
                    "media_types": ["application/pdf"],
                    "supports_rendering": True,
                    "max_input_bytes": 10_000,
                    "max_pages": 10,
                },
            },
            headers=auth,
        )
        lease = claim.json()["lease"]
        await client.post(
            f"/worker/v1/jobs/{lease['job_id']}:start",
            json={
                "worker_id": "worker-events",
                "lease_token": lease["lease_token"],
                "lease_version": lease["lease_version"],
                "attempt": lease["attempt"],
                "deletion_epoch": lease["deletion_epoch"],
            },
            headers={**auth, "Idempotency-Key": "start-events"},
        )
        job_id = lease["job_id"]

    await database.dispose()

    restarted_database = Database(test_database_url)
    restarted_app = create_app(
        settings=settings,
        database=restarted_database,
        storage=storage,
        clock=clock,
        claim_waiter=waiter,
    )
    async with AsyncClient(
        transport=ASGITransport(app=restarted_app), base_url="http://testserver"
    ) as client:
        replay = await client.get(f"/api/v1/parse-jobs/{job_id}/events?once=true")
        assert replay.status_code == 200
        assert replay.headers["content-type"].startswith("text/event-stream")
        assert "event: job.queued" in replay.text
        assert "event: job.leased" in replay.text
        assert "event: job.started" in replay.text
        assert ": heartbeat" in replay.text

        after_first = await client.get(
            f"/api/v1/parse-jobs/{job_id}/events?once=true",
            headers={"Last-Event-ID": "1"},
        )
        assert "id: 1\n" not in after_first.text
        assert "id: 2\n" in after_first.text

        original_events_after = JobEventReader.events_after
        calls = 0

        async def history_moves_after_connection(
            reader: JobEventReader,
            principal: Principal,
            requested_job_id: str,
            after_sequence: int,
        ) -> list[JobEventEnvelope] | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                return await original_events_after(
                    reader, principal, requested_job_id, after_sequence
                )
            raise ApiProblem(
                status=410,
                code=ProblemCode.EVENT_HISTORY_EXPIRED,
                title="任务事件历史已过期",
            )

        with patch.object(
            JobEventReader,
            "events_after",
            new=history_moves_after_connection,
        ):
            shifted_floor = await client.get(f"/api/v1/parse-jobs/{job_id}/events")
        assert shifted_floor.status_code == 200
        assert "event: stream.reset" in shifted_floor.text
        assert '"code":"EVENT_HISTORY_EXPIRED"' in shifted_floor.text

    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    async with restarted_database.session(principal) as session:
        await session.execute(
            update(JobEventModel).values(expires_at=clock.now() - timedelta(seconds=1))
        )

    async with AsyncClient(
        transport=ASGITransport(app=restarted_app), base_url="http://testserver"
    ) as client:
        expired = await client.get(f"/api/v1/parse-jobs/{job_id}/events?once=true")
        assert expired.status_code == 410
        assert expired.json()["code"] == "EVENT_HISTORY_EXPIRED"

    await restarted_database.dispose()
