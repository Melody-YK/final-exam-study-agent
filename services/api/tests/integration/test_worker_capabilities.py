from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from pydantic import SecretStr

from study_agent.config import AppMode, Settings
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.storage.local import LocalStorage


def _self_authored_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 24), "white").save(output, format="PNG")
    return output.getvalue()


def _claim_body(
    worker_id: str,
    *,
    profile: str,
    supports_ocr: bool,
) -> dict[str, object]:
    return {
        "worker_id": worker_id,
        "capabilities": {
            "parser_profiles": [profile],
            "media_types": ["image/png"],
            "supports_ocr": supports_ocr,
            "supports_rendering": False,
            "max_input_bytes": 1_000_000,
            "max_pages": 10,
        },
    }


@pytest.mark.integration
async def test_image_job_claim_requires_consistent_ocr_capability_and_allowed_profile(
    test_database_url: str,
    tmp_path: Path,
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
    payload = _self_authored_png()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        course = await client.post("/api/v1/courses", json={"title": "Self-authored OCR"})
        created = await client.post(
            f"/api/v1/courses/{course.json()['id']}/documents",
            json={
                "filename": "self-authored.png",
                "media_type": "image/png",
                "size_bytes": len(payload),
                "sha256": sha256(payload).hexdigest(),
                "corpus_role": "corpus",
            },
        )
        upload = created.json()["upload"]
        await client.put(upload["url"], content=payload, headers={"Content-Type": "image/png"})
        completed = await client.post(
            f"/api/v1/documents/{created.json()['document']['id']}/upload:complete",
            json={"upload_session_id": upload["id"]},
            headers={"Idempotency-Key": "enqueue-self-authored-ocr"},
        )
        assert completed.status_code == 202

        inconsistent = await client.post(
            "/worker/v1/jobs:claim",
            json=_claim_body("inconsistent", profile="ocr-v1", supports_ocr=False),
            headers=auth,
        )
        assert inconsistent.status_code == 200
        assert inconsistent.json()["lease"] is None

        for profile in ("mineru-v1", "paid-ocr-v1"):
            disabled = await client.post(
                "/worker/v1/jobs:claim",
                json=_claim_body(f"disabled-{profile}", profile=profile, supports_ocr=True),
                headers=auth,
            )
            assert disabled.status_code == 200
            assert disabled.json()["lease"] is None

        native_only = await client.post(
            "/worker/v1/jobs:claim",
            json=_claim_body("native-only", profile="native-v1", supports_ocr=False),
            headers=auth,
        )
        assert native_only.status_code == 200
        assert native_only.json()["lease"] is None

        capable = await client.post(
            "/worker/v1/jobs:claim",
            json=_claim_body("ocr-worker", profile="ocr-v1", supports_ocr=True),
            headers=auth,
        )
        assert capable.status_code == 200
        lease = capable.json()["lease"]
        assert lease is not None
        assert lease["parser_profile"] == "ocr-v1"
        assert lease["media_type"] == "image/png"

    await database.dispose()
