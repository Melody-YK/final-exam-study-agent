from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import CourseScope, LocalPrincipalProvider, Principal
from study_agent.identity.session import (
    SESSION_COOKIE_NAME,
    get_request_principal,
)
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    AccountModel,
    AccountSessionModel,
    RegistrationInvitationModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.auth.service import (
    AccountRole,
    AccountService,
    AccountServiceError,
    AccountServiceErrorCode,
)
from study_agent.modules.courses.repository import CourseRepository
from study_agent.modules.jobs.clock import SystemClock
from study_agent.storage.local import LocalStorage


def _settings(database_url: str, root: Path, *, app_mode: AppMode = AppMode.TEST) -> Settings:
    return Settings(
        _env_file=None,
        app_mode=app_mode,
        database_url=SecretStr(database_url),
        local_storage_root=root,
        lexical_index_root=root / "lexical",
        note_demo_phase_delay_seconds=0,
    )


def _auth_app(database: Database, settings: Settings, root: Path):
    app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(root),
    )

    @app.get("/api/v1/test/session-principal")
    async def session_principal(
        principal: Annotated[Principal, Depends(get_request_principal)],
    ) -> dict[str, object]:
        return {"subject": principal.subject, "scopes": sorted(principal.scopes)}

    return app


@pytest.mark.integration
async def test_first_account_inherits_local_courses_and_admin_role_is_enforced(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    local_principal = LocalPrincipalProvider().resolve("127.0.0.1")
    legacy_course = await CourseRepository(database).create(local_principal, "历史课程")
    settings = _settings(test_database_url, tmp_path, app_mode=AppMode.LOCAL)
    app = _auth_app(database, settings, tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1",
        ) as client:
            unauthenticated = await client.get("/api/v1/test/session-principal")
            unauthenticated_course = await client.get(f"/api/v1/courses/{legacy_course.id}")
            assert unauthenticated.status_code == 401
            assert unauthenticated_course.status_code == 401

            registered_admin = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "Admin@Example.com",
                    "password": "correct horse battery staple",
                    "display_name": "  管理员  ",
                },
            )
            assert registered_admin.status_code == 201
            assert registered_admin.json() == {
                "id": registered_admin.json()["id"],
                "email": "admin@example.com",
                "display_name": "管理员",
                "role": "admin",
            }
            assert "token" not in registered_admin.text.lower()
            set_cookie = registered_admin.headers["set-cookie"].lower()
            assert "study_session=" in set_cookie
            assert "httponly" in set_cookie
            assert "samesite=lax" in set_cookie
            admin_token = client.cookies.get(SESSION_COOKIE_NAME)
            assert admin_token is not None

            me = await client.get("/api/v1/auth/me")
            protected = await client.get("/api/v1/test/session-principal")
            inherited_course = await client.get(f"/api/v1/courses/{legacy_course.id}")
            inherited_courses = await client.get("/api/v1/courses")
            assert me.json() == registered_admin.json()
            assert protected.status_code == 200
            assert protected.json() == {"subject": "local-user", "scopes": ["admin"]}
            assert inherited_course.status_code == 200
            assert inherited_course.json()["title"] == "历史课程"
            assert inherited_courses.status_code == 200
            assert inherited_courses.json() == [
                {
                    "id": legacy_course.id,
                    "title": "历史课程",
                    "lifecycle": "active",
                }
            ]

            invitation = await client.post(
                "/api/v1/admin/invitations",
                json={},
            )
            assert invitation.status_code == 201
            invite_code = invitation.json()["code"]

            registered_user = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "user@example.com",
                    "password": "another secure password",
                    "display_name": "普通用户",
                    "invite_code": invite_code,
                },
            )
            assert registered_user.status_code == 201
            assert registered_user.json()["role"] == "user"
            assert set(registered_user.json()) == {"id", "email", "display_name", "role"}
            user_courses = await client.get("/api/v1/courses")
            assert user_courses.status_code == 200
            assert user_courses.json() == []
            denied_users = await client.get("/api/v1/admin/users")
            denied_diagnostics = await client.get("/api/v1/admin/diagnostics")
            assert denied_users.status_code == 403
            assert denied_users.json()["code"] == "AUTH_FORBIDDEN"
            assert denied_diagnostics.status_code == 403

            logged_in_admin = await client.post(
                "/api/v1/auth/login",
                json={"email": "admin@example.com", "password": "correct horse battery staple"},
            )
            assert logged_in_admin.status_code == 200
            assert logged_in_admin.json() == registered_admin.json()
            users = await client.get("/api/v1/admin/users")
            diagnostics = await client.get("/api/v1/admin/diagnostics")
            assert users.status_code == 200
            assert [item["role"] for item in users.json()["items"]] == ["admin", "user"]
            assert diagnostics.status_code == 200
            assert diagnostics.json()["totals"] == {
                "accounts": 2,
                "active_sessions": 3,
                "courses": 1,
                "documents": 0,
                "notes": 0,
            }
            assert diagnostics.json()["runtime"] == {
                "app_mode": "local",
                "database": "postgresql",
                "demo_lab_enabled": True,
            }

        async with database.system_session("auth-test") as session:
            admin = await session.scalar(
                select(AccountModel).where(AccountModel.email == "admin@example.com")
            )
            assert admin is not None
            assert admin.user_id == legacy_course.user_id
            assert admin.password_hash.startswith("$scrypt$")
            assert "correct horse" not in admin.password_hash
            persisted_session = await session.scalar(
                select(AccountSessionModel).where(AccountSessionModel.account_id == admin.id)
            )
            assert persisted_session is not None
            assert persisted_session.token_hash != admin_token

        identity = await AccountService(database, SystemClock(), settings).resolve(admin_token)
        assert identity is not None
        inherited = await CourseRepository(database).get(
            CourseScope(principal=identity.principal, course_id=legacy_course.id)
        )
        assert inherited is not None
        assert inherited.id == legacy_course.id
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_duplicate_email_is_rejected_after_normalization(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    settings = _settings(test_database_url, tmp_path)
    app = _auth_app(database, settings, tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            first = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "student@example.com",
                    "password": "first secure password",
                    "display_name": "Student",
                },
            )
            invitation = await client.post("/api/v1/admin/invitations", json={})
            assert invitation.status_code == 201
            duplicate = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "  STUDENT@example.com ",
                    "password": "second secure password",
                    "display_name": "Other",
                    "invite_code": invitation.json()["code"],
                },
            )
        assert first.status_code == 201
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "ACCOUNT_EMAIL_EXISTS"
        async with database.system_session("auth-test") as session:
            assert await session.scalar(select(func.count()).select_from(AccountModel)) == 1
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_missing_forged_expired_and_revoked_sessions_fail_closed(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    settings = _settings(test_database_url, tmp_path)
    app = _auth_app(database, settings, tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            missing = await client.get("/api/v1/auth/me")
            client.cookies.set(SESSION_COOKIE_NAME, "x" * 43)
            forged = await client.get("/api/v1/auth/me")
            client.cookies.clear()
            registered = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "session@example.com",
                    "password": "session password",
                    "display_name": "Session User",
                },
            )
            expired_token = client.cookies.get(SESSION_COOKIE_NAME)
            assert registered.status_code == 201
            assert expired_token is not None

            async with database.system_session("auth-test") as session:
                persisted = await session.scalar(
                    select(AccountSessionModel).where(
                        AccountSessionModel.token_hash
                        == hashlib.sha256(expired_token.encode("ascii")).hexdigest()
                    )
                )
                assert persisted is not None
                persisted.created_at = datetime.now(UTC) - timedelta(days=2)
                persisted.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            expired = await client.get("/api/v1/auth/me")

            logged_in = await client.post(
                "/api/v1/auth/login",
                json={"email": "session@example.com", "password": "session password"},
            )
            revoked_token = client.cookies.get(SESSION_COOKIE_NAME)
            assert logged_in.status_code == 200
            assert revoked_token is not None
            logged_out = await client.post("/api/v1/auth/logout")
            client.cookies.set(SESSION_COOKIE_NAME, revoked_token)
            revoked = await client.get("/api/v1/auth/me")
            wrong_password = await client.post(
                "/api/v1/auth/login",
                json={"email": "session@example.com", "password": "wrong password"},
            )

        for response in (missing, forged, expired, revoked, wrong_password):
            assert response.status_code == 401
            assert response.json()["code"] == "AUTH_REQUIRED"
        assert logged_out.status_code == 204
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_test_mode_request_principal_preserves_injected_local_provider(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    app = _auth_app(database, _settings(test_database_url, tmp_path), tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/v1/test/session-principal")
        assert response.status_code == 200
        assert response.json() == {"subject": "local-user", "scopes": []}
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_registration_invitations_are_one_time_revocable_and_hash_only(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    settings = _settings(test_database_url, tmp_path)
    app = _auth_app(database, settings, tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            admin = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "invite-admin@example.com",
                    "password": "invite admin password",
                    "display_name": "Invite Admin",
                },
            )
            assert admin.status_code == 201

            missing = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "missing@example.com",
                    "password": "missing invite password",
                    "display_name": "Missing Invite",
                },
            )
            forged = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "forged@example.com",
                    "password": "forged invite password",
                    "display_name": "Forged Invite",
                    "invite_code": "x" * 32,
                },
            )
            for response in (missing, forged):
                assert response.status_code == 403
                assert response.json()["code"] == "AUTH_FORBIDDEN"

            created = await client.post("/api/v1/admin/invitations", json={})
            assert created.status_code == 201
            created_body = created.json()
            invite_code = created_body["code"]
            assert created_body["status"] == "available"
            assert datetime.fromisoformat(created_body["expires_at"]) - datetime.fromisoformat(
                created_body["created_at"]
            ) == timedelta(days=7)

            available = await client.get("/api/v1/admin/invitations")
            assert available.status_code == 200
            available_item = available.json()["items"][0]
            assert available_item["id"] == created_body["id"]
            assert "code" not in available_item
            assert "code_hash" not in available_item
            assert invite_code not in available.text

            user = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "invited@example.com",
                    "password": "invited user password",
                    "display_name": "Invited User",
                    "invite_code": invite_code,
                },
            )
            assert user.status_code == 201
            assert set(user.json()) == {"id", "email", "display_name", "role"}
            user_id = user.json()["id"]

            denied = await client.get("/api/v1/admin/invitations")
            assert denied.status_code == 403
            logged_in_admin = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "invite-admin@example.com",
                    "password": "invite admin password",
                },
            )
            assert logged_in_admin.status_code == 200

            used = await client.get("/api/v1/admin/invitations")
            used_item = used.json()["items"][0]
            assert used_item["status"] == "used"
            assert used_item["used_by_account_id"] == user_id
            assert "code" not in used_item

            reused = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "reuse@example.com",
                    "password": "reused invite password",
                    "display_name": "Reuse",
                    "invite_code": invite_code,
                },
            )
            assert reused.status_code == 403

            revocable = await client.post(
                "/api/v1/admin/invitations",
                json={"expires_in_days": 3},
            )
            revoked_code = revocable.json()["code"]
            revoked_id = revocable.json()["id"]
            assert (
                await client.delete(f"/api/v1/admin/invitations/{revoked_id}")
            ).status_code == 204
            assert (
                await client.delete(f"/api/v1/admin/invitations/{revoked_id}")
            ).status_code == 204
            revoked_registration = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "revoked@example.com",
                    "password": "revoked invite password",
                    "display_name": "Revoked",
                    "invite_code": revoked_code,
                },
            )
            assert revoked_registration.status_code == 403

        async with database.system_session("auth-test") as session:
            persisted = await session.scalar(
                select(RegistrationInvitationModel).where(
                    RegistrationInvitationModel.id == created_body["id"]
                )
            )
            assert persisted is not None
            assert persisted.code_hash == hashlib.sha256(invite_code.encode("ascii")).hexdigest()
            assert persisted.code_hash != invite_code
            assert persisted.used_by_account_id == user_id
            assert persisted.used_at is not None
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_admin_account_controls_revoke_sessions_and_preserve_an_active_admin(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    settings = _settings(test_database_url, tmp_path)
    app = _auth_app(database, settings, tmp_path)
    service = AccountService(database, SystemClock(), settings)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            admin = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "control-admin@example.com",
                    "password": "control admin password",
                    "display_name": "Control Admin",
                },
            )
            admin_id = admin.json()["id"]
            invitation = await client.post("/api/v1/admin/invitations", json={})
            user = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "controlled@example.com",
                    "password": "controlled user password",
                    "display_name": "Controlled User",
                    "invite_code": invitation.json()["code"],
                },
            )
            user_id = user.json()["id"]
            user_token = client.cookies.get(SESSION_COOKIE_NAME)
            assert user_token is not None

            logged_in_admin = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "control-admin@example.com",
                    "password": "control admin password",
                },
            )
            assert logged_in_admin.status_code == 200
            admin_token = client.cookies.get(SESSION_COOKIE_NAME)
            assert admin_token is not None

            empty_update = await client.patch(f"/api/v1/admin/users/{user_id}", json={})
            assert empty_update.status_code == 422
            noted = await client.patch(
                f"/api/v1/admin/users/{user_id}",
                json={"admin_note": "  上传内容需人工复核  "},
            )
            assert noted.status_code == 200
            assert noted.json()["status"] == "active"
            assert noted.json()["admin_note"] == "上传内容需人工复核"

            users = await client.get("/api/v1/admin/users")
            target = next(item for item in users.json()["items"] if item["id"] == user_id)
            assert target["admin_note"] == "上传内容需人工复核"
            assert target["status"] == "active"

            self_demote = await client.patch(
                f"/api/v1/admin/users/{admin_id}",
                json={"role": "user"},
            )
            self_suspend = await client.patch(
                f"/api/v1/admin/users/{admin_id}",
                json={"status": "suspended"},
            )
            for response in (self_demote, self_suspend):
                assert response.status_code == 409
                assert response.json()["code"] == "STATE_CONFLICT"

            suspended = await client.patch(
                f"/api/v1/admin/users/{user_id}",
                json={"status": "suspended"},
            )
            assert suspended.status_code == 200
            assert suspended.json()["status"] == "suspended"

            client.cookies.clear()
            client.cookies.set(SESSION_COOKIE_NAME, user_token)
            assert (await client.get("/api/v1/auth/me")).status_code == 401
            denied_login = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "controlled@example.com",
                    "password": "controlled user password",
                },
            )
            assert denied_login.status_code == 401

            client.cookies.clear()
            client.cookies.set(SESSION_COOKIE_NAME, admin_token)
            activated = await client.patch(
                f"/api/v1/admin/users/{user_id}",
                json={"status": "active", "admin_note": None},
            )
            assert activated.status_code == 200
            assert activated.json()["admin_note"] is None

            public_login = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "controlled@example.com",
                    "password": "controlled user password",
                },
            )
            assert public_login.status_code == 200
            assert set(public_login.json()) == {"id", "email", "display_name", "role"}
            public_me = await client.get("/api/v1/auth/me")
            assert set(public_me.json()) == {"id", "email", "display_name", "role"}

            client.cookies.clear()
            await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "control-admin@example.com",
                    "password": "control admin password",
                },
            )
            current_admin_token = client.cookies.get(SESSION_COOKIE_NAME)
            assert current_admin_token is not None
            promoted = await client.patch(
                f"/api/v1/admin/users/{user_id}",
                json={"role": "admin"},
            )
            assert promoted.status_code == 200
            assert promoted.json()["role"] == "admin"

        async with database.system_session("auth-test") as session:
            user_sessions = list(
                await session.scalars(
                    select(AccountSessionModel).where(AccountSessionModel.account_id == user_id)
                )
            )
            assert any(item.revoked_at is not None for item in user_sessions)

        stale_actor = await service.resolve(current_admin_token)
        assert stale_actor is not None
        async with database.system_session("auth-test") as session:
            original_admin = await session.scalar(
                select(AccountModel).where(AccountModel.id == admin_id)
            )
            assert original_admin is not None
            original_admin.status = "suspended"
            original_admin.disabled_at = datetime.now(UTC)

        with pytest.raises(AccountServiceError) as exc_info:
            await service.update_account(
                stale_actor,
                user_id,
                role=AccountRole.USER,
                status=None,
                admin_note=None,
                admin_note_provided=False,
            )
        assert exc_info.value.code is AccountServiceErrorCode.CONFLICT
    finally:
        await database.dispose()
