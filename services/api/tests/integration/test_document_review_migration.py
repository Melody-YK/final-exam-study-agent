from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from study_agent.infrastructure.db.migrations import downgrade_database, upgrade_database


@pytest.mark.integration
async def test_0012_backfills_existing_documents_and_constrains_new_reviews(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    try:
        await downgrade_database(test_database_url, "20260724_0011")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users (id, subject, authentication_method) "
                        "VALUES ('review-user', 'review-subject', 'local')"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO accounts "
                        "(id, user_id, email, display_name, role, status, password_hash) "
                        "VALUES ('review-account', 'review-user', 'review@example.com', "
                        "'Reviewer', 'admin', 'active', 'hash')"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO courses (id, user_id, title, lifecycle, row_version) "
                        "VALUES ('review-course', 'review-user', 'Review course', 'active', 1)"
                    )
                )
                await _insert_document(connection, suffix="legacy")
        finally:
            await engine.dispose()

        await upgrade_database(test_database_url, "20260724_0012")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.begin() as connection:
                legacy_status = await connection.scalar(
                    text("SELECT review_status FROM documents WHERE id = 'review-document-legacy'")
                )
                await _insert_document(connection, suffix="new")
                new_status = await connection.scalar(
                    text("SELECT review_status FROM documents WHERE id = 'review-document-new'")
                )

            assert legacy_status == "approved"
            assert new_status == "pending"

            with pytest.raises(IntegrityError, match="ck_documents_review_status"):
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE documents SET review_status = 'unknown' "
                            "WHERE id = 'review-document-new'"
                        )
                    )

            with pytest.raises(IntegrityError, match="ck_documents_review_state"):
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "UPDATE documents SET review_note = 'not valid while pending' "
                            "WHERE id = 'review-document-new'"
                        )
                    )

            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE documents SET review_status = 'rejected', "
                        "review_note = 'Unsupported material', "
                        "reviewed_by_account_id = 'review-account', reviewed_at = now() "
                        "WHERE id = 'review-document-new'"
                    )
                )
                rejected = (
                    await connection.execute(
                        text(
                            "SELECT review_status, review_note, reviewed_by_account_id "
                            "FROM documents WHERE id = 'review-document-new'"
                        )
                    )
                ).one()
            assert rejected == ("rejected", "Unsupported material", "review-account")
        finally:
            await engine.dispose()

        await downgrade_database(test_database_url, "20260724_0011")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                remaining_columns = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema = 'public' AND table_name = 'documents' "
                                "AND column_name LIKE 'review%'"
                            )
                        )
                    ).scalars()
                )
            assert remaining_columns == set()
        finally:
            await engine.dispose()
    finally:
        await upgrade_database(test_database_url)


async def _insert_document(connection: AsyncConnection, *, suffix: str) -> None:
    await connection.execute(
        text(
            "INSERT INTO stored_objects "
            "(id, user_id, course_id, object_key, purpose, sha256, size_bytes, media_type) "
            "VALUES (:object_id, 'review-user', 'review-course', :object_key, 'original', "
            ":sha256, 16, 'application/pdf')"
        ),
        {
            "object_id": f"review-object-{suffix}",
            "object_key": f"review/{suffix}.pdf",
            "sha256": ("0" if suffix == "legacy" else "1") * 64,
        },
    )
    await connection.execute(
        text(
            "INSERT INTO documents "
            "(id, user_id, course_id, stored_object_id, filename, media_type, corpus_role, "
            "verified_sha256, status, deletion_epoch) "
            "VALUES (:document_id, 'review-user', 'review-course', :object_id, :filename, "
            "'application/pdf', 'corpus', :sha256, 'ready', 0)"
        ),
        {
            "document_id": f"review-document-{suffix}",
            "object_id": f"review-object-{suffix}",
            "filename": f"{suffix}.pdf",
            "sha256": ("0" if suffix == "legacy" else "1") * 64,
        },
    )
