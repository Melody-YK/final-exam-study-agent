from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select

from study_agent.config import AppMode, Settings
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    DocumentModel,
    DocumentRevisionModel,
    IndexJobModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.storage.local import LocalStorage


@dataclass(frozen=True, slots=True)
class ReviewContext:
    database: Database
    admin_client: AsyncClient
    user_client: AsyncClient
    admin_account_id: str
    user_account_id: str
    course_id: str
    preview_document_id: str
    pending_document_id: str
    preview_payload: bytes


@pytest.mark.integration
async def test_admin_document_queue_is_cross_user_and_forbidden_to_regular_users(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    async with _review_context(test_database_url, tmp_path) as context:
        denied_list = await context.user_client.get("/api/v1/admin/documents")
        denied_content = await context.user_client.get(
            f"/api/v1/admin/documents/{context.preview_document_id}/content"
        )
        denied_review = await context.user_client.post(
            f"/api/v1/admin/documents/{context.preview_document_id}/review",
            json={"review_status": "approved"},
        )
        assert denied_list.status_code == 403
        assert denied_content.status_code == 403
        assert denied_review.status_code == 403
        assert denied_list.json()["code"] == "AUTH_FORBIDDEN"

        pending = await context.admin_client.get(
            "/api/v1/admin/documents",
            params={"review_status": "pending"},
        )
        assert pending.status_code == 200
        items = pending.json()["items"]
        assert {item["id"] for item in items} == {
            context.preview_document_id,
            context.pending_document_id,
        }
        preview = next(item for item in items if item["id"] == context.preview_document_id)
        assert preview["owner_account_id"] == context.user_account_id
        assert preview["owner_email"] == "user@example.com"
        assert preview["course_id"] == context.course_id
        assert preview["course_title"] == "用户课程"
        assert preview["page_count"] == 7
        assert preview["review_status"] == "pending"
        assert preview["review_note"] is None
        assert preview["reviewed_by_account_id"] is None
        assert preview["reviewed_at"] is None

        empty_rejected = await context.admin_client.get(
            "/api/v1/admin/documents",
            params={"review_status": "rejected"},
        )
        assert empty_rejected.status_code == 200
        assert empty_rejected.json() == {"items": []}

        user_documents = await context.user_client.get(
            f"/api/v1/courses/{context.course_id}/documents"
        )
        assert user_documents.status_code == 200
        assert all("review_note" not in item for item in user_documents.json())


@pytest.mark.integration
async def test_admin_can_stream_the_original_document_bytes(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    async with _review_context(test_database_url, tmp_path) as context:
        response = await context.admin_client.get(
            f"/api/v1/admin/documents/{context.preview_document_id}/content"
        )

        assert response.status_code == 200
        assert response.content == context.preview_payload
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-length"] == str(len(context.preview_payload))
        assert response.headers["content-disposition"].startswith(
            "inline; filename*=UTF-8''preview.pdf"
        )


@pytest.mark.integration
async def test_admin_cannot_approve_an_upload_before_its_bytes_are_complete(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    async with _review_context(test_database_url, tmp_path) as context:
        payload = b"%PDF-1.7\nincomplete"
        declaration = await context.user_client.post(
            f"/api/v1/courses/{context.course_id}/documents",
            json={
                "filename": "incomplete.pdf",
                "media_type": "application/pdf",
                "size_bytes": len(payload),
                "sha256": sha256(payload).hexdigest(),
                "corpus_role": "corpus",
            },
        )
        assert declaration.status_code == 201

        response = await context.admin_client.post(
            f"/api/v1/admin/documents/{declaration.json()['document']['id']}/review",
            json={"review_status": "approved"},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "STATE_CONFLICT"


@pytest.mark.integration
async def test_admin_review_decisions_are_idempotent_and_cannot_be_reversed(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    async with _review_context(test_database_url, tmp_path) as context:
        approved = await context.admin_client.post(
            f"/api/v1/admin/documents/{context.preview_document_id}/review",
            json={"review_status": "approved", "review_note": "  内容已核验  "},
        )
        assert approved.status_code == 200
        assert approved.json()["review_status"] == "approved"
        assert approved.json()["review_note"] == "内容已核验"
        assert approved.json()["reviewed_by_account_id"] == context.admin_account_id
        assert approved.json()["reviewed_by_email"] == "admin@example.com"
        assert approved.json()["reviewed_at"] is not None

        approved_replay = await context.admin_client.post(
            f"/api/v1/admin/documents/{context.preview_document_id}/review",
            json={"review_status": "approved", "review_note": "不应覆盖原备注"},
        )
        assert approved_replay.status_code == 200
        assert approved_replay.json() == approved.json()

        approve_reversal = await context.admin_client.post(
            f"/api/v1/admin/documents/{context.preview_document_id}/review",
            json={"review_status": "rejected", "review_note": "反向决定"},
        )
        assert approve_reversal.status_code == 409
        assert approve_reversal.json()["code"] == "STATE_CONFLICT"

        missing_rejection_note = await context.admin_client.post(
            f"/api/v1/admin/documents/{context.pending_document_id}/review",
            json={"review_status": "rejected"},
        )
        assert missing_rejection_note.status_code == 422

        rejected = await context.admin_client.post(
            f"/api/v1/admin/documents/{context.pending_document_id}/review",
            json={"review_status": "rejected", "review_note": "  文件内容不完整  "},
        )
        assert rejected.status_code == 200
        assert rejected.json()["review_status"] == "rejected"
        assert rejected.json()["review_note"] == "文件内容不完整"

        rejected_replay = await context.admin_client.post(
            f"/api/v1/admin/documents/{context.pending_document_id}/review",
            json={"review_status": "rejected", "review_note": "另一条备注"},
        )
        assert rejected_replay.status_code == 200
        assert rejected_replay.json() == rejected.json()

        reject_reversal = await context.admin_client.post(
            f"/api/v1/admin/documents/{context.pending_document_id}/review",
            json={"review_status": "approved"},
        )
        assert reject_reversal.status_code == 409
        assert reject_reversal.json()["code"] == "STATE_CONFLICT"

        async with context.database.system_session("review-test") as session:
            index_jobs = await session.scalar(
                select(func.count())
                .select_from(IndexJobModel)
                .where(IndexJobModel.document_id == context.preview_document_id)
            )
            persisted = await session.get(DocumentModel, context.pending_document_id)
        assert index_jobs == 1
        assert persisted is not None
        assert persisted.review_status == "rejected"
        assert persisted.review_note == "文件内容不完整"


@asynccontextmanager
async def _review_context(
    test_database_url: str,
    storage_root: Path,
) -> AsyncIterator[ReviewContext]:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.LOCAL,
        database_url=SecretStr(test_database_url),
        local_storage_root=storage_root,
        lexical_index_root=storage_root / "lexical",
        note_demo_phase_delay_seconds=0,
    )
    app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(storage_root),
    )
    transport = ASGITransport(app=app)
    try:
        async with (
            AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1",
            ) as admin_client,
            AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1",
            ) as user_client,
        ):
            registered_admin = await admin_client.post(
                "/api/v1/auth/register",
                json={
                    "email": "admin@example.com",
                    "password": "correct horse battery staple",
                    "display_name": "管理员",
                },
            )
            assert registered_admin.status_code == 201
            invitation = await admin_client.post("/api/v1/admin/invitations", json={})
            assert invitation.status_code == 201
            registered_user = await user_client.post(
                "/api/v1/auth/register",
                json={
                    "email": "user@example.com",
                    "password": "another secure password",
                    "display_name": "普通用户",
                    "invite_code": invitation.json()["code"],
                },
            )
            assert registered_user.status_code == 201
            course = await user_client.post(
                "/api/v1/courses",
                json={"title": "用户课程"},
            )
            assert course.status_code == 201
            course_id = course.json()["id"]

            preview_payload = b"%PDF-1.7\nadmin review preview"
            preview_document_id = await _upload_document(
                user_client,
                course_id,
                filename="preview.pdf",
                payload=preview_payload,
            )
            pending_document_id = await _upload_document(
                user_client,
                course_id,
                filename="pending.pdf",
                payload=b"%PDF-1.7\nadmin review pending",
            )
            await _add_preview_revision(database, preview_document_id)

            yield ReviewContext(
                database=database,
                admin_client=admin_client,
                user_client=user_client,
                admin_account_id=registered_admin.json()["id"],
                user_account_id=registered_user.json()["id"],
                course_id=course_id,
                preview_document_id=preview_document_id,
                pending_document_id=pending_document_id,
                preview_payload=preview_payload,
            )
    finally:
        await database.dispose()


async def _upload_document(
    client: AsyncClient,
    course_id: str,
    *,
    filename: str,
    payload: bytes,
) -> str:
    declaration = await client.post(
        f"/api/v1/courses/{course_id}/documents",
        json={
            "filename": filename,
            "media_type": "application/pdf",
            "size_bytes": len(payload),
            "sha256": sha256(payload).hexdigest(),
            "corpus_role": "corpus",
        },
    )
    assert declaration.status_code == 201
    document_id = declaration.json()["document"]["id"]
    upload = declaration.json()["upload"]
    uploaded = await client.put(
        upload["url"],
        content=payload,
        headers={"Content-Type": "application/pdf"},
    )
    assert uploaded.status_code == 200
    completed = await client.post(
        f"/api/v1/documents/{document_id}/upload:complete",
        json={"upload_session_id": upload["id"]},
        headers={"Idempotency-Key": f"complete-{document_id}"},
    )
    assert completed.status_code == 202
    assert completed.json()["review_status"] == "pending"
    return document_id


async def _add_preview_revision(database: Database, document_id: str) -> None:
    async with database.system_session("review-test-seed") as session:
        document = await session.get(DocumentModel, document_id, with_for_update=True)
        assert document is not None
        revision_id = str(uuid4())
        session.add(
            DocumentRevisionModel(
                id=revision_id,
                document_id=document.id,
                ordinal=1,
                manifest={},
                canonical_sha256=uuid4().hex * 2,
                total_page_count=7,
                parser_profile="native-v1",
                parser_schema_version="1.0",
                chunker_version="section-page-v1",
                quality_status="passed",
            )
        )
        await session.flush()
        document.preview_revision_id = revision_id
        document.status = "parsed_index_blocked"
