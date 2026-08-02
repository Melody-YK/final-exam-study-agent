from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from services.api.tests.integration.retrieval_helpers import seed_document_revision
from study_agent.config import AppMode, Settings
from study_agent.identity.session import SESSION_COOKIE_NAME
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import AccountModel, CourseModel, NoteModel
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.storage.local import LocalStorage


@pytest.mark.integration
async def test_admin_content_is_cross_user_read_only_and_forbidden_to_regular_users(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.LOCAL,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        lexical_index_root=tmp_path / "lexical",
        course_terms=("进程", "调度"),
        note_demo_phase_delay_seconds=0,
    )
    app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
    )
    transport = ASGITransport(app=app)
    try:
        async with (
            AsyncClient(transport=transport, base_url="http://127.0.0.1") as admin_client,
            AsyncClient(transport=transport, base_url="http://127.0.0.1") as user_client,
        ):
            admin = await admin_client.post(
                "/api/v1/auth/register",
                json={
                    "email": "admin-content@example.com",
                    "password": "correct horse battery staple",
                    "display_name": "内容管理员",
                },
            )
            assert admin.status_code == 201
            admin_token = admin_client.cookies.get(SESSION_COOKIE_NAME)
            assert admin_token is not None

            invitation = await admin_client.post("/api/v1/admin/invitations", json={})
            assert invitation.status_code == 201
            user = await user_client.post(
                "/api/v1/auth/register",
                json={
                    "email": "student-content@example.com",
                    "password": "another secure password",
                    "display_name": "内容学生",
                    "invite_code": invitation.json()["code"],
                },
            )
            assert user.status_code == 201
            course_response = await user_client.post(
                "/api/v1/courses",
                json={"title": "操作系统"},
            )
            assert course_response.status_code == 201
            course_id = course_response.json()["id"]

            async with database.system_session("admin-content-test-seed") as session:
                course = await session.get(CourseModel, course_id)
                assert course is not None
                admin_account = await session.get(AccountModel, admin.json()["id"])
                assert admin_account is not None
                session.add(
                    CourseModel(
                        id="admin-hidden-course",
                        user_id=admin_account.user_id,
                        title="管理员历史课程",
                        lifecycle="active",
                    )
                )
                seeded = await seed_document_revision(
                    session,
                    user_id=course.user_id,
                    course_id=course.id,
                    text_chunks=("进程 调度 进程", "调度 进程"),
                    active=True,
                    preview=False,
                )
                session.add(
                    NoteModel(
                        id="admin-visible-note",
                        user_id=course.user_id,
                        course_id=course.id,
                        section_path=["进程管理", "调度"],
                        title="进程调度笔记",
                        body_markdown="# 进程调度\n\n调度器负责选择下一个进程。",
                        version=3,
                        generation=2,
                        generated_by_model=True,
                        status="ready",
                    )
                )

            admin_courses = await admin_client.get("/api/v1/admin/courses")
            admin_notes = await admin_client.get(f"/api/v1/admin/courses/{course_id}/notes")
            admin_graph = await admin_client.get(
                f"/api/v1/admin/courses/{course_id}/knowledge-graph"
            )

            assert admin_courses.status_code == 200
            assert admin_courses.json()["items"] == [
                {
                    "id": course_id,
                    "title": "操作系统",
                    "lifecycle": "active",
                    "owner_account_id": user.json()["id"],
                    "owner_email": "student-content@example.com",
                    "owner_display_name": "内容学生",
                    "owner_subject": f"account:{user.json()['id']}",
                    "document_count": 1,
                    "note_count": 1,
                    "created_at": admin_courses.json()["items"][0]["created_at"],
                    "updated_at": admin_courses.json()["items"][0]["updated_at"],
                }
            ]
            assert admin_notes.status_code == 200
            assert admin_notes.json()["items"] == [
                {
                    "id": "admin-visible-note",
                    "course_id": course_id,
                    "section_path": ["进程管理", "调度"],
                    "title": "进程调度笔记",
                    "body_markdown": "# 进程调度\n\n调度器负责选择下一个进程。",
                    "version": 3,
                    "generation": 2,
                    "generated_by_model": True,
                    "status": "ready",
                    "created_at": admin_notes.json()["items"][0]["created_at"],
                    "updated_at": admin_notes.json()["items"][0]["updated_at"],
                }
            ]
            assert admin_graph.status_code == 200
            assert admin_graph.json()["course_id"] == course_id
            assert admin_graph.json()["active_document_count"] == 1
            assert admin_graph.json()["nodes"][0]["label"] == "操作系统"
            assert {
                node["label"] for node in admin_graph.json()["nodes"] if node["kind"] == "concept"
            } == {"进程", "调度"}
            assert seeded.document_id in {
                node["document_id"]
                for node in admin_graph.json()["nodes"]
                if node["kind"] == "document"
            }

            denied_courses = await user_client.get("/api/v1/admin/courses")
            denied_notes = await user_client.get(f"/api/v1/admin/courses/{course_id}/notes")
            denied_graph = await user_client.get(
                f"/api/v1/admin/courses/{course_id}/knowledge-graph"
            )
            assert [
                denied_courses.status_code,
                denied_notes.status_code,
                denied_graph.status_code,
            ] == [
                403,
                403,
                403,
            ]
            assert all(
                response.json()["code"] == "AUTH_FORBIDDEN"
                for response in (denied_courses, denied_notes, denied_graph)
            )

            admin_client.cookies.set(SESSION_COOKIE_NAME, admin_token)
            blocked_create = await admin_client.post(
                "/api/v1/courses",
                json={"title": "管理员课程"},
            )
            blocked_read = await admin_client.get(f"/api/v1/courses/{course_id}")
            assert blocked_create.status_code == 403
            assert blocked_create.json()["code"] == "AUTH_FORBIDDEN"
            assert blocked_read.status_code == 403
            assert blocked_read.json()["code"] == "AUTH_FORBIDDEN"

            missing_notes = await admin_client.get("/api/v1/admin/courses/missing-course/notes")
            missing_graph = await admin_client.get(
                "/api/v1/admin/courses/missing-course/knowledge-graph"
            )
            hidden_admin_notes = await admin_client.get(
                "/api/v1/admin/courses/admin-hidden-course/notes"
            )
            hidden_admin_graph = await admin_client.get(
                "/api/v1/admin/courses/admin-hidden-course/knowledge-graph"
            )
            assert missing_notes.status_code == 404
            assert missing_notes.json()["code"] == "RESOURCE_NOT_FOUND"
            assert missing_graph.status_code == 404
            assert missing_graph.json()["code"] == "RESOURCE_NOT_FOUND"
            assert hidden_admin_notes.status_code == 404
            assert hidden_admin_notes.json()["code"] == "RESOURCE_NOT_FOUND"
            assert hidden_admin_graph.status_code == 404
            assert hidden_admin_graph.json()["code"] == "RESOURCE_NOT_FOUND"
    finally:
        await database.dispose()
