from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from study_agent.infrastructure.db.migrations import downgrade_database, upgrade_database


@pytest.mark.integration
async def test_0009_creates_constrained_accounts_and_sessions_and_downgrades_cleanly(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    try:
        await downgrade_database(test_database_url, "20260722_0008")
        await upgrade_database(test_database_url, "20260723_0009")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                tables = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT tablename FROM pg_tables "
                                "WHERE schemaname = 'public' "
                                "AND tablename IN ('accounts', 'account_sessions')"
                            )
                        )
                    ).scalars()
                )
                constraints = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT conname FROM pg_constraint "
                                "WHERE conname IN "
                                "('fk_accounts_user', 'uq_accounts_email', 'uq_accounts_user', "
                                "'ck_accounts_role', 'ck_accounts_email_normalized', "
                                "'fk_account_sessions_account', "
                                "'uq_account_sessions_token_hash', "
                                "'ck_account_sessions_token_hash', "
                                "'ck_account_sessions_expiry')"
                            )
                        )
                    ).scalars()
                )
        finally:
            await engine.dispose()

        assert version == "20260723_0009"
        assert tables == {"accounts", "account_sessions"}
        assert constraints == {
            "fk_accounts_user",
            "uq_accounts_email",
            "uq_accounts_user",
            "ck_accounts_role",
            "ck_accounts_email_normalized",
            "fk_account_sessions_account",
            "uq_account_sessions_token_hash",
            "ck_account_sessions_token_hash",
            "ck_account_sessions_expiry",
        }

        await downgrade_database(test_database_url, "20260722_0008")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                remaining = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT tablename FROM pg_tables "
                                "WHERE schemaname = 'public' "
                                "AND tablename IN ('accounts', 'account_sessions')"
                            )
                        )
                    ).scalars()
                )
        finally:
            await engine.dispose()
        assert remaining == set()
    finally:
        await upgrade_database(test_database_url)


@pytest.mark.integration
async def test_0011_adds_constrained_invitations_and_backfills_account_status(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    try:
        await downgrade_database(test_database_url, "20260723_0010")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, subject, authentication_method) VALUES "
                        "('legacy-user', 'legacy-subject', 'local')"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO accounts "
                        "(id, user_id, email, display_name, role, password_hash, disabled_at) "
                        "VALUES ('legacy-account', 'legacy-user', 'legacy@example.com', "
                        "'Legacy', 'user', 'legacy-hash', now())"
                    )
                )
        finally:
            await engine.dispose()

        await upgrade_database(test_database_url, "20260724_0011")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                columns = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema = 'public' AND table_name = 'accounts' "
                                "AND column_name IN ('status', 'admin_note')"
                            )
                        )
                    ).scalars()
                )
                invitation_table = await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                        "AND tablename = 'registration_invitations'"
                    )
                )
                constraints = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT conname FROM pg_constraint WHERE conname IN ("
                                "'ck_accounts_status', 'ck_accounts_status_disabled', "
                                "'ck_accounts_admin_note', "
                                "'ck_registration_invitations_code_hash', "
                                "'ck_registration_invitations_expiry', "
                                "'ck_registration_invitations_used_at', "
                                "'ck_registration_invitations_revoked_at', "
                                "'ck_registration_invitations_used_by', "
                                "'ck_registration_invitations_terminal_state', "
                                "'fk_registration_invitations_created_by', "
                                "'fk_registration_invitations_used_by', "
                                "'uq_registration_invitations_code_hash', "
                                "'uq_registration_invitations_used_by')"
                            )
                        )
                    ).scalars()
                )
                indexes = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
                                "AND indexname IN ('ix_accounts_status_role', "
                                "'ix_registration_invitations_active')"
                            )
                        )
                    ).scalars()
                )
                backfilled_status = await connection.scalar(
                    text("SELECT status FROM accounts WHERE id = 'legacy-account'")
                )
        finally:
            await engine.dispose()

        assert version == "20260724_0011"
        assert columns == {"status", "admin_note"}
        assert invitation_table == 1
        assert constraints == {
            "ck_accounts_status",
            "ck_accounts_status_disabled",
            "ck_accounts_admin_note",
            "ck_registration_invitations_code_hash",
            "ck_registration_invitations_expiry",
            "ck_registration_invitations_used_at",
            "ck_registration_invitations_revoked_at",
            "ck_registration_invitations_used_by",
            "ck_registration_invitations_terminal_state",
            "fk_registration_invitations_created_by",
            "fk_registration_invitations_used_by",
            "uq_registration_invitations_code_hash",
            "uq_registration_invitations_used_by",
        }
        assert indexes == {
            "ix_accounts_status_role",
            "ix_registration_invitations_active",
        }
        assert backfilled_status == "suspended"

        await downgrade_database(test_database_url, "20260723_0010")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                remaining_table = await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                        "AND tablename = 'registration_invitations'"
                    )
                )
                remaining_columns = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema = 'public' AND table_name = 'accounts' "
                                "AND column_name IN ('status', 'admin_note')"
                            )
                        )
                    ).scalars()
                )
        finally:
            await engine.dispose()
        assert remaining_table == 0
        assert remaining_columns == set()
    finally:
        await upgrade_database(test_database_url)
