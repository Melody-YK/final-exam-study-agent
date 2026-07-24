import asyncio
from hashlib import sha256
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import (
    AuthenticationMethod,
    LocalPrincipalProvider,
    Principal,
)
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    DocumentModel,
    ParseJobModel,
    UploadSessionModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.storage.local import LocalStorage


class StaticPrincipalProvider:
    def __init__(self, principal: Principal) -> None:
        self._principal = principal

    def resolve(self, client_host: str) -> Principal:
        del client_host
        return self._principal


def _settings(test_database_url: str, storage_root: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=storage_root,
    )


def _declaration(
    payload: bytes,
    *,
    filename: str = "chapter.pdf",
    media_type: str = "application/pdf",
    corpus_role: str = "corpus",
    declared_sha256: str | None = None,
    declared_size: int | None = None,
) -> dict[str, object]:
    return {
        "filename": filename,
        "media_type": media_type,
        "size_bytes": len(payload) if declared_size is None else declared_size,
        "sha256": sha256(payload).hexdigest() if declared_sha256 is None else declared_sha256,
        "corpus_role": corpus_role,
    }


@pytest.mark.integration
async def test_local_course_upload_dedup_and_delete_flow(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    storage = LocalStorage(tmp_path)
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
    )
    app = create_app(settings=settings, database=database, storage=storage)
    payload = b"%PDF-1.7\ncontent"
    digest = sha256(payload).hexdigest()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        course_response = await client.post("/api/v1/courses", json={"title": "操作系统"})
        assert course_response.status_code == 201
        course_id = course_response.json()["id"]

        create_document_response = await client.post(
            f"/api/v1/courses/{course_id}/documents",
            json={
                "filename": "chapter.pdf",
                "media_type": "application/pdf",
                "size_bytes": len(payload),
                "sha256": digest,
                "corpus_role": "corpus",
            },
        )
        assert create_document_response.status_code == 201
        created = create_document_response.json()
        document = created["document"]
        upload = created["upload"]
        assert document["status"] == "uploading"

        put_response = await client.put(
            upload["url"],
            content=payload,
            headers={"Content-Type": "application/pdf"},
        )
        assert put_response.status_code == 200

        complete_response = await client.post(
            f"/api/v1/documents/{document['id']}/upload:complete",
            json={"upload_session_id": upload["id"]},
            headers={"Idempotency-Key": "complete-chapter-v1"},
        )
        assert complete_response.status_code == 202
        document = complete_response.json()
        assert document["status"] == "queued"
        assert document["review_status"] == "pending"
        assert document["corpus_role"] == "corpus"
        assert document["indexable"] is False

        complete_replay = await client.post(
            f"/api/v1/documents/{document['id']}/upload:complete",
            json={"upload_session_id": upload["id"]},
            headers={"Idempotency-Key": "complete-chapter-v1"},
        )
        assert complete_replay.status_code == 202
        assert complete_replay.json() == document

        complete_conflict = await client.post(
            f"/api/v1/documents/{document['id']}/upload:complete",
            json={"upload_session_id": "different-upload-session"},
            headers={"Idempotency-Key": "complete-chapter-v1"},
        )
        assert complete_conflict.status_code == 409
        assert complete_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

        duplicate = await client.post(
            f"/api/v1/courses/{course_id}/documents",
            json={
                "filename": "chapter.pdf",
                "media_type": "application/pdf",
                "size_bytes": len(payload),
                "sha256": digest,
                "corpus_role": "corpus",
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "DOCUMENT_DUPLICATE"

        other_payload = b"%PDF-1.7\nother content"
        other_document_response = await client.post(
            f"/api/v1/courses/{course_id}/documents",
            json={
                "filename": "other.pdf",
                "media_type": "application/pdf",
                "size_bytes": len(other_payload),
                "sha256": sha256(other_payload).hexdigest(),
                "corpus_role": "corpus",
            },
        )
        assert other_document_response.status_code == 201
        other_document_id = other_document_response.json()["document"]["id"]

        delete_response = await client.delete(
            f"/api/v1/documents/{document['id']}",
            headers={"Idempotency-Key": "delete-chapter-v1"},
        )
        assert delete_response.status_code == 202
        deletion_id = delete_response.json()["deletion_id"]

        delete_replay = await client.delete(
            f"/api/v1/documents/{document['id']}",
            headers={"Idempotency-Key": "delete-chapter-v1"},
        )
        assert delete_replay.status_code == 202
        assert delete_replay.json()["deletion_id"] == deletion_id

        delete_conflict = await client.delete(
            f"/api/v1/documents/{other_document_id}",
            headers={"Idempotency-Key": "delete-chapter-v1"},
        )
        assert delete_conflict.status_code == 409
        assert delete_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"

        missing = await client.get(f"/api/v1/documents/{document['id']}")
        assert missing.status_code == 404

        deletion = await client.get(f"/api/v1/deletions/{deletion_id}")
        assert deletion.status_code == 200
        assert deletion.json()["status"] == "completed"

    await database.dispose()


@pytest.mark.integration
async def test_upload_complete_rejects_untrusted_metadata_and_allows_corrected_retry(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    storage = LocalStorage(tmp_path)
    app = create_app(
        settings=_settings(test_database_url, tmp_path),
        database=database,
        storage=storage,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        course = await client.post("/api/v1/courses", json={"title": "数据库"})
        course_id = course.json()["id"]

        forged_type = await client.post(
            f"/api/v1/courses/{course_id}/documents",
            json=_declaration(
                b"\x89PNG\r\n\x1a\n",
                filename="chapter.pdf",
                media_type="image/png",
            ),
        )
        assert forged_type.status_code == 415
        assert forged_type.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"

        expected_payload = b"%PDF-1.7\ncorrect"
        corrupt_payload = b"%PDF-1.7\ncorrupt"
        created = await client.post(
            f"/api/v1/courses/{course_id}/documents",
            json=_declaration(
                expected_payload,
                declared_sha256=sha256(expected_payload).hexdigest(),
            ),
        )
        document = created.json()["document"]
        upload = created.json()["upload"]
        assert len(corrupt_payload) == len(expected_payload)
        assert (
            await client.put(
                upload["url"],
                content=corrupt_payload,
                headers={"Content-Type": "application/pdf"},
            )
        ).status_code == 200

        hash_mismatch = await client.post(
            f"/api/v1/documents/{document['id']}/upload:complete",
            json={"upload_session_id": upload["id"]},
            headers={"Idempotency-Key": "correctable-upload"},
        )
        assert hash_mismatch.status_code == 409
        assert hash_mismatch.json()["code"] == "HASH_MISMATCH"

        retry_put = await client.put(
            upload["url"],
            content=expected_payload,
            headers={"Content-Type": "application/pdf"},
        )
        assert retry_put.status_code == 200
        corrected = await client.post(
            f"/api/v1/documents/{document['id']}/upload:complete",
            json={"upload_session_id": upload["id"]},
            headers={"Idempotency-Key": "correctable-upload"},
        )
        assert corrected.status_code == 202

        fake_pptx = b"not an OOXML presentation"
        pptx_created = await client.post(
            f"/api/v1/courses/{course_id}/documents",
            json=_declaration(
                fake_pptx,
                filename="slides.pptx",
                media_type=(
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                ),
            ),
        )
        pptx_document = pptx_created.json()["document"]
        pptx_upload = pptx_created.json()["upload"]
        await client.put(
            pptx_upload["url"],
            content=fake_pptx,
            headers={
                "Content-Type": (
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                )
            },
        )
        bad_magic = await client.post(
            f"/api/v1/documents/{pptx_document['id']}/upload:complete",
            json={"upload_session_id": pptx_upload["id"]},
            headers={"Idempotency-Key": "bad-pptx-magic"},
        )
        assert bad_magic.status_code == 415
        assert bad_magic.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"

        short_payload = b"%PDF-short"
        short_created = await client.post(
            f"/api/v1/courses/{course_id}/documents",
            json=_declaration(short_payload, declared_size=len(short_payload) + 1),
        )
        short_document = short_created.json()["document"]
        short_upload = short_created.json()["upload"]
        await client.put(
            short_upload["url"],
            content=short_payload,
            headers={"Content-Type": "application/pdf"},
        )
        size_mismatch = await client.post(
            f"/api/v1/documents/{short_document['id']}/upload:complete",
            json={"upload_session_id": short_upload["id"]},
            headers={"Idempotency-Key": "short-upload"},
        )
        assert size_mismatch.status_code == 409
        assert size_mismatch.json()["code"] == "STATE_CONFLICT"

    await database.dispose()


@pytest.mark.integration
async def test_document_and_upload_resources_are_not_enumerable_across_principals(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    storage = LocalStorage(tmp_path)
    settings = _settings(test_database_url, tmp_path)
    owner = Principal(subject="owner", authentication_method=AuthenticationMethod.LOCAL)
    stranger = Principal(subject="stranger", authentication_method=AuthenticationMethod.LOCAL)
    owner_app = create_app(
        settings=settings,
        database=database,
        storage=storage,
        principal_provider=StaticPrincipalProvider(owner),
    )
    stranger_app = create_app(
        settings=settings,
        database=database,
        storage=storage,
        principal_provider=StaticPrincipalProvider(stranger),
    )
    payload = b"%PDF-1.7\nprivate"

    async with (
        AsyncClient(
            transport=ASGITransport(app=owner_app), base_url="http://testserver"
        ) as owner_client,
        AsyncClient(
            transport=ASGITransport(app=stranger_app), base_url="http://testserver"
        ) as stranger_client,
    ):
        course = await owner_client.post("/api/v1/courses", json={"title": "编译原理"})
        created = await owner_client.post(
            f"/api/v1/courses/{course.json()['id']}/documents",
            json=_declaration(payload),
        )
        document = created.json()["document"]
        upload = created.json()["upload"]

        assert (await stranger_client.get(f"/api/v1/documents/{document['id']}")).status_code == 404
        assert (
            await stranger_client.put(
                upload["url"],
                content=payload,
                headers={"Content-Type": "application/pdf"},
            )
        ).status_code == 404
        assert (
            await stranger_client.post(
                f"/api/v1/documents/{document['id']}/upload:complete",
                json={"upload_session_id": upload["id"]},
                headers={"Idempotency-Key": "stranger-complete"},
            )
        ).status_code == 404
        assert (
            await stranger_client.delete(
                f"/api/v1/documents/{document['id']}",
                headers={"Idempotency-Key": "stranger-delete"},
            )
        ).status_code == 404

        await owner_client.put(
            upload["url"],
            content=payload,
            headers={"Content-Type": "application/pdf"},
        )
        await owner_client.post(
            f"/api/v1/documents/{document['id']}/upload:complete",
            json={"upload_session_id": upload["id"]},
            headers={"Idempotency-Key": "owner-complete"},
        )
        deleted = await owner_client.delete(
            f"/api/v1/documents/{document['id']}",
            headers={"Idempotency-Key": "owner-delete"},
        )
        deletion_id = deleted.json()["deletion_id"]
        assert (await stranger_client.get(f"/api/v1/deletions/{deletion_id}")).status_code == 404

    await database.dispose()


@pytest.mark.integration
async def test_complete_and_delete_concurrency_has_consistent_lock_order_and_terminal_state(
    test_database_url: str, tmp_path: Path
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    storage = LocalStorage(tmp_path)
    settings = _settings(test_database_url, tmp_path)
    app = create_app(settings=settings, database=database, storage=storage)
    payload = b"%PDF-1.7\ncomplete-delete-race"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as setup_client:
        course = await setup_client.post("/api/v1/courses", json={"title": "并发删除"})
        created = await setup_client.post(
            f"/api/v1/courses/{course.json()['id']}/documents",
            json=_declaration(payload, filename="race.pdf"),
        )
        document = created.json()["document"]
        upload = created.json()["upload"]
        await setup_client.put(
            upload["url"],
            content=payload,
            headers={"Content-Type": "application/pdf"},
        )

    async with (
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as complete_client,
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as delete_client,
    ):
        complete_response, delete_response = await asyncio.wait_for(
            asyncio.gather(
                complete_client.post(
                    f"/api/v1/documents/{document['id']}/upload:complete",
                    json={"upload_session_id": upload["id"]},
                    headers={"Idempotency-Key": "race-complete"},
                ),
                delete_client.delete(
                    f"/api/v1/documents/{document['id']}",
                    headers={"Idempotency-Key": "race-delete"},
                ),
            ),
            timeout=5,
        )
        assert complete_response.status_code in {202, 404, 409}
        assert delete_response.status_code == 202
        assert complete_response.status_code < 500
        assert (await complete_client.get(f"/api/v1/documents/{document['id']}")).status_code == 404

    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    async with database.session(principal) as session:
        stored_document = await session.get(DocumentModel, document["id"])
        stored_upload = await session.get(UploadSessionModel, upload["id"])
        jobs = (
            await session.scalars(
                select(ParseJobModel).where(ParseJobModel.document_id == document["id"])
            )
        ).all()
        assert stored_document is not None
        assert stored_document.deleted_at is not None
        assert stored_upload is not None
        assert stored_upload.status == "cancelled"
        assert all(job.status == "cancelled" for job in jobs)

    await database.dispose()
