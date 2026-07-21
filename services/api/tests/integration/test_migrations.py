from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from study_agent.identity.principal import AuthenticationMethod, Principal
from study_agent.infrastructure.db.migrations import downgrade_database, upgrade_database
from study_agent.infrastructure.db.models import (
    DocumentModel,
    DocumentRevisionModel,
    ParseJobModel,
    StoredObjectModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.courses.repository import CourseRepository

CORE_TABLES = {
    "courses",
    "deletion_jobs",
    "document_revisions",
    "documents",
    "idempotency_records",
    "job_artifacts",
    "job_events",
    "outbox_events",
    "page_checkpoints",
    "parse_attempt_results",
    "parse_jobs",
    "revision_assets",
    "revision_blocks",
    "revision_chunks",
    "revision_pages",
    "stored_objects",
    "upload_sessions",
    "users",
}


async def _public_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return set(
                (
                    await connection.execute(
                        text(
                            "SELECT tablename FROM pg_tables "
                            "WHERE schemaname = 'public' ORDER BY tablename"
                        )
                    )
                ).scalars()
            )
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_migrations_create_core_tables_and_vector_extension(test_database_url: str) -> None:
    await upgrade_database(test_database_url)
    engine = create_async_engine(test_database_url)

    async with engine.connect() as connection:
        extension = await connection.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        tables = set(
            (
                await connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' ORDER BY tablename"
                    )
                )
            ).scalars()
        )
        constraints = set(
            (
                await connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conname IN ('fk_revision_assets_page', 'fk_revision_chunks_page')"
                    )
                )
            ).scalars()
        )

    await engine.dispose()

    assert extension == "0.8.5"
    assert tables >= CORE_TABLES
    assert constraints == {"fk_revision_assets_page", "fk_revision_chunks_page"}


@pytest.mark.integration
async def test_migrations_round_trip_from_head_to_base_and_back(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    try:
        await downgrade_database(test_database_url)
        assert CORE_TABLES.isdisjoint(await _public_tables(test_database_url))
    finally:
        await upgrade_database(test_database_url)

    assert await _public_tables(test_database_url) >= CORE_TABLES


@pytest.mark.integration
async def test_downgrade_to_p3_nulls_real_revision_job_references_before_drop(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject="migration-owner", authentication_method=AuthenticationMethod.LOCAL
    )
    course = await CourseRepository(database).create(principal, "迁移测试")
    object_id = str(uuid4())
    document_id = str(uuid4())
    job_id = str(uuid4())
    revision_id = str(uuid4())
    now = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
    async with database.session(principal) as session:
        session.add(
            StoredObjectModel(
                id=object_id,
                user_id=course.user_id,
                course_id=course.id,
                object_key="migration/course/original/object",
                purpose="original",
                sha256="a" * 64,
                size_bytes=10,
                media_type="application/pdf",
            )
        )
        await session.flush()
        session.add(
            DocumentModel(
                id=document_id,
                user_id=course.user_id,
                course_id=course.id,
                stored_object_id=object_id,
                filename="migration.pdf",
                media_type="application/pdf",
                corpus_role="corpus",
                verified_sha256="a" * 64,
                status="processing",
                deletion_epoch=0,
            )
        )
        await session.flush()
        session.add(
            ParseJobModel(
                id=job_id,
                user_id=course.user_id,
                course_id=course.id,
                document_id=document_id,
                stored_object_id=object_id,
                parser_profile="native-v1",
                parser_schema_version="1.0",
                media_type="application/pdf",
                document_sha256="a" * 64,
                document_deletion_epoch=0,
                input_size_bytes=10,
                available_at=now,
            )
        )
        await session.flush()
        session.add(
            DocumentRevisionModel(
                id=revision_id,
                document_id=document_id,
                ordinal=1,
                parse_job_id=job_id,
                manifest={},
                parser_profile="native-v1",
                parser_schema_version="1.0",
                quality_status="passed",
            )
        )
    await database.dispose()

    try:
        await downgrade_database(test_database_url, "20260719_0002")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                parse_job_id = await connection.scalar(
                    text("SELECT parse_job_id FROM document_revisions WHERE id = :revision_id"),
                    {"revision_id": revision_id},
                )
                parse_jobs_table = await connection.scalar(
                    text("SELECT to_regclass('public.parse_jobs')")
                )
            assert parse_job_id is None
            assert parse_jobs_table is None
        finally:
            await engine.dispose()
    finally:
        await upgrade_database(test_database_url)
