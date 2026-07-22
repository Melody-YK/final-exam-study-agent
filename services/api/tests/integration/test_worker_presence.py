from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from study_agent.config import AppMode, Settings
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.storage.local import LocalStorage


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


@pytest.mark.integration
async def test_capabilities_follow_recent_authenticated_worker_claims(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    clock = MutableClock(datetime(2026, 7, 19, 7, 0, tzinfo=UTC))
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        lexical_index_root=tmp_path / "lexical",
        worker_token=SecretStr("worker-presence-test-token"),
        worker_presence_ttl_seconds=45,
        embedding_api_key=SecretStr("embedding-test-key"),
        deepseek_api_key=SecretStr("chat-test-key"),
        note_async_workflow_enabled=True,
        note_runner_enabled=True,
        note_docx_export_enabled=True,
        note_export_runner_enabled=True,
        note_docx_renderer_enabled=True,
        note_numeric_eta_enabled=True,
    )
    app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        clock=clock,
    )
    claim = {
        "schema_version": "1.0",
        "worker_id": "worker-presence-test",
        "capabilities": {
            "schema_version": "1.0",
            "parser_profiles": ["native-v1", "ocr-v1"],
            "media_types": ["application/pdf", "image/png"],
            "supports_ocr": True,
            "supports_rendering": False,
            "max_input_bytes": 1_000_000,
            "max_pages": 100,
        },
    }
    headers = {"Authorization": "Bearer worker-presence-test-token"}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        before = await client.get("/api/v1/capabilities")
        claimed = await client.post(
            "/worker/v1/jobs:claim?wait_seconds=0",
            json=claim,
            headers=headers,
        )
        online = await client.get("/api/v1/capabilities")
        clock.advance(timedelta(seconds=46))
        expired = await client.get("/api/v1/capabilities")

    assert before.json()["native_parser"]["status"] == "worker_required"
    note_workflow = before.json()["note_workflow"]
    assert note_workflow["enabled"] is True
    for capability in ("generation", "export", "eta"):
        assert note_workflow[capability]["status"] == "available"
        assert note_workflow[capability]["error_code"] is None
    assert claimed.status_code == 200
    assert claimed.json()["lease"] is None
    assert online.json()["native_parser"]["status"] == "available"
    assert online.json()["ocr_parser"]["status"] == "available"
    assert online.json()["note_workflow"] == note_workflow
    assert expired.json()["native_parser"]["status"] == "worker_required"
    assert expired.json()["ocr_parser"]["status"] == "worker_required"
    assert expired.json()["note_workflow"] == note_workflow
    await database.dispose()
