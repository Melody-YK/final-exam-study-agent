from hashlib import sha256
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import LocalPrincipalProvider
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import OutboxEventModel
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.storage.local import LocalStorage


class FailingDeleteStorage(LocalStorage):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.delete_calls: list[str] = []

    async def delete(self, object_key: str) -> None:
        self.delete_calls.append(object_key)
        raise OSError("simulated private storage failure")


@pytest.mark.integration
async def test_delete_failure_keeps_document_hidden_and_cleanup_retryable(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    storage = FailingDeleteStorage(tmp_path)
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
    )
    app = create_app(settings=settings, database=database, storage=storage)
    payload = b"%PDF-1.7\ncleanup failure"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        course = await client.post("/api/v1/courses", json={"title": "网络"})
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
        document = created.json()["document"]
        upload = created.json()["upload"]
        await client.put(
            upload["url"],
            content=payload,
            headers={"Content-Type": "application/pdf"},
        )
        await client.post(
            f"/api/v1/documents/{document['id']}/upload:complete",
            json={"upload_session_id": upload["id"]},
            headers={"Idempotency-Key": "complete-before-delete-failure"},
        )

        deleted = await client.delete(
            f"/api/v1/documents/{document['id']}",
            headers={"Idempotency-Key": "delete-with-storage-failure"},
        )
        assert deleted.status_code == 202
        deletion_id = deleted.json()["deletion_id"]
        assert (await client.get(f"/api/v1/documents/{document['id']}")).status_code == 404

        status_response = await client.get(f"/api/v1/deletions/{deletion_id}")
        assert status_response.status_code == 200
        snapshot = status_response.json()
        assert snapshot["status"] == "retry_wait"
        assert snapshot["attempt_count"] == 1
        assert "simulated private storage failure" not in status_response.text

    assert len(storage.delete_calls) == 1
    await storage.head(storage.delete_calls[0])

    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    async with database.session(principal) as session:
        events = (await session.scalars(select(OutboxEventModel))).all()
        assert len(events) == 1
        assert events[0].status == "retry_wait"
        assert events[0].attempt_count == 1
        assert events[0].last_error_code == "STORAGE_DELETE_FAILED"
        assert events[0].last_error_detail is None

    await database.dispose()
