import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
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
NOTE_WORKFLOW_TABLES = {
    "note_command_dedup",
    "note_generation_batches",
    "note_generation_items",
    "note_generation_attempts",
    "note_generation_inputs",
    "note_generation_outputs",
    "note_generation_events",
    "note_coverage_units",
    "note_item_inputs",
    "note_coverage_unit_results",
    "note_content_versions",
    "note_version_source_snapshots",
    "note_version_source_payloads",
    "note_version_source_links",
    "note_version_coverage",
    "note_version_coverage_units",
    "note_source_state_overlays",
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


async def _legacy_note_bytes(database_url: str, note_id: str) -> bytes:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(
                text(
                    "SELECT convert_to(row_to_json(notes)::text, 'UTF8') "
                    "FROM notes WHERE id = :note_id"
                ),
                {"note_id": note_id},
            )
        assert isinstance(value, bytes)
        return value
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

    assert extension is not None
    extension_version = tuple(int(part) for part in extension.split("."))
    assert extension_version[:2] == (0, 8)
    assert extension_version >= (0, 8, 5)
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
async def test_note_batch_style_migration_backfills_existing_batches_without_leaving_default(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    user_id = str(uuid4())
    course_id = str(uuid4())
    batch_id = str(uuid4())

    try:
        await downgrade_database(test_database_url, "20260723_0009")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users (id, subject, authentication_method) "
                        "VALUES (:id, :subject, 'local')"
                    ),
                    {"id": user_id, "subject": f"style-migration-{user_id}"},
                )
                await connection.execute(
                    text(
                        "INSERT INTO courses (id, user_id, title, lifecycle, row_version) "
                        "VALUES (:id, :user_id, 'Style migration', 'active', 1)"
                    ),
                    {"id": course_id, "user_id": user_id},
                )
                await connection.execute(
                    text(
                        "INSERT INTO note_generation_batches "
                        "(id, user_id, course_id, command_kind, mode, section_path, status, "
                        "state_version, event_sequence, cancel_epoch) "
                        "VALUES (:id, :user_id, :course_id, 'create', 'merged', "
                        "CAST(:section_path AS jsonb), 'queued', 1, 0, 0)"
                    ),
                    {
                        "id": batch_id,
                        "user_id": user_id,
                        "course_id": course_id,
                        "section_path": json.dumps(["Legacy"]),
                    },
                )
        finally:
            await engine.dispose()

        await upgrade_database(test_database_url, "20260723_0010")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                style = await connection.scalar(
                    text("SELECT style FROM note_generation_batches WHERE id = :id"),
                    {"id": batch_id},
                )
                column_default = await connection.scalar(
                    text(
                        "SELECT column_default FROM information_schema.columns "
                        "WHERE table_schema = 'public' "
                        "AND table_name = 'note_generation_batches' AND column_name = 'style'"
                    )
                )
            assert style == "exam_focus"
            assert column_default is None
            with pytest.raises(IntegrityError, match="ck_note_generation_batches_style"):
                async with engine.begin() as connection:
                    await connection.execute(
                        text("UPDATE note_generation_batches SET style = 'invalid' WHERE id = :id"),
                        {"id": batch_id},
                    )
        finally:
            await engine.dispose()
    finally:
        await upgrade_database(test_database_url)


@pytest.mark.integration
async def test_note_workflow_migrations_preserve_legacy_note_across_0007_round_trip(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    user_id = str(uuid4())
    course_id = str(uuid4())
    note_id = str(uuid4())

    try:
        await downgrade_database(test_database_url, "20260721_0007")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users (id, subject, authentication_method) "
                        "VALUES (:id, :subject, 'local')"
                    ),
                    {"id": user_id, "subject": f"legacy-note-{user_id}"},
                )
                await connection.execute(
                    text(
                        "INSERT INTO courses "
                        "(id, user_id, title, lifecycle, row_version) "
                        "VALUES (:id, :user_id, 'Legacy course', 'active', 1)"
                    ),
                    {"id": course_id, "user_id": user_id},
                )
                await connection.execute(
                    text(
                        "INSERT INTO notes "
                        "(id, user_id, course_id, section_path, title, body_markdown, version, "
                        "generation, generated_by_model, status) "
                        "VALUES (:id, :user_id, :course_id, CAST(:section_path AS jsonb), "
                        "'Legacy note', 'Legacy body', 1, 1, false, 'ready')"
                    ),
                    {
                        "id": note_id,
                        "user_id": user_id,
                        "course_id": course_id,
                        "section_path": json.dumps(["Legacy"]),
                    },
                )
        finally:
            await engine.dispose()

        before = await _legacy_note_bytes(test_database_url, note_id)
        await upgrade_database(test_database_url, "7102eb21ee91")
        assert await _legacy_note_bytes(test_database_url, note_id) == before
        await upgrade_database(test_database_url, "20260722_0008")
        assert await _legacy_note_bytes(test_database_url, note_id) == before

        await downgrade_database(test_database_url, "20260721_0007")
        assert NOTE_WORKFLOW_TABLES.isdisjoint(await _public_tables(test_database_url))
        assert await _legacy_note_bytes(test_database_url, note_id) == before
    finally:
        await upgrade_database(test_database_url)


@pytest.mark.integration
async def test_0008_downgrade_keeps_latest_output_when_note_has_multiple_versions(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    await downgrade_database(test_database_url, "20260722_0008")
    user_id = str(uuid4())
    course_id = str(uuid4())
    note_id = str(uuid4())
    batch_id = str(uuid4())
    first_item_id = str(uuid4())
    latest_item_id = str(uuid4())
    first_output_id = str(uuid4())
    latest_output_id = str(uuid4())
    section_path = json.dumps(["Migration"])
    sha256 = "a" * 64
    engine = create_async_engine(test_database_url)

    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, subject, authentication_method) "
                    "VALUES (:id, :subject, 'local')"
                ),
                {"id": user_id, "subject": f"output-downgrade-{user_id}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO courses (id, user_id, title, lifecycle, row_version) "
                    "VALUES (:id, :user_id, 'Output downgrade', 'active', 1)"
                ),
                {"id": course_id, "user_id": user_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO notes "
                    "(id, user_id, course_id, section_path, title, body_markdown, version, "
                    "generation, generated_by_model, status) "
                    "VALUES (:id, :user_id, :course_id, CAST(:section_path AS jsonb), "
                    "'Migration note', 'Latest body', 2, 2, false, 'ready')"
                ),
                {
                    "id": note_id,
                    "user_id": user_id,
                    "course_id": course_id,
                    "section_path": section_path,
                },
            )
            for version in (1, 2):
                await connection.execute(
                    text(
                        "INSERT INTO note_content_versions "
                        "(note_id, version, user_id, course_id, title, section_path, "
                        "body_markdown, content_ast, ast_schema_version, parser_version, "
                        "body_sha256, source_set_sha256, coverage_manifest_sha256, "
                        "note_version_sha256, created_by) "
                        "VALUES (:note_id, :version, :user_id, :course_id, 'Migration note', "
                        "CAST(:section_path AS jsonb), :body, CAST('{}' AS jsonb), '1.0', "
                        "'migration-test', :sha256, :sha256, :sha256, :sha256, 'generated')"
                    ),
                    {
                        "note_id": note_id,
                        "version": version,
                        "user_id": user_id,
                        "course_id": course_id,
                        "section_path": section_path,
                        "body": f"Version {version}",
                        "sha256": sha256,
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO note_generation_batches "
                    "(id, user_id, course_id, mode, status, state_version, event_sequence, "
                    "cancel_epoch, command_kind, section_path) "
                    "VALUES (:id, :user_id, :course_id, 'merged', 'queued', 1, 0, 0, "
                    "'create', CAST(:section_path AS jsonb))"
                ),
                {
                    "id": batch_id,
                    "user_id": user_id,
                    "course_id": course_id,
                    "section_path": section_path,
                },
            )
            for ordinal, item_id in enumerate((first_item_id, latest_item_id), start=1):
                await connection.execute(
                    text(
                        "INSERT INTO note_generation_items "
                        "(id, batch_id, user_id, course_id, ordinal, status, state_version, "
                        "attempt, max_attempts, lease_version, cancel_epoch) "
                        "VALUES (:id, :batch_id, :user_id, :course_id, :ordinal, 'queued', "
                        "1, 0, 1, 0, 0)"
                    ),
                    {
                        "id": item_id,
                        "batch_id": batch_id,
                        "user_id": user_id,
                        "course_id": course_id,
                        "ordinal": ordinal,
                    },
                )
            for output_id, item_id, version in (
                (first_output_id, first_item_id, 1),
                (latest_output_id, latest_item_id, 2),
            ):
                await connection.execute(
                    text(
                        "INSERT INTO note_generation_outputs "
                        "(id, batch_id, item_id, user_id, course_id, note_id, note_version) "
                        "VALUES (:id, :batch_id, :item_id, :user_id, :course_id, :note_id, "
                        ":note_version)"
                    ),
                    {
                        "id": output_id,
                        "batch_id": batch_id,
                        "item_id": item_id,
                        "user_id": user_id,
                        "course_id": course_id,
                        "note_id": note_id,
                        "note_version": version,
                    },
                )
    finally:
        await engine.dispose()

    try:
        await downgrade_database(test_database_url, "7102eb21ee91")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                output_ids = list(
                    (
                        await connection.execute(
                            text(
                                "SELECT id FROM note_generation_outputs "
                                "WHERE note_id = :note_id ORDER BY id"
                            ),
                            {"note_id": note_id},
                        )
                    ).scalars()
                )
                head = await connection.scalar(text("SELECT version_num FROM alembic_version"))

            assert head == "7102eb21ee91"
            assert output_ids == [latest_output_id]
            with pytest.raises(IntegrityError, match="uq_note_generation_outputs_note"):
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO note_generation_outputs "
                            "(id, batch_id, item_id, user_id, course_id, note_id) "
                            "VALUES (:id, :batch_id, :item_id, :user_id, :course_id, :note_id)"
                        ),
                        {
                            "id": str(uuid4()),
                            "batch_id": batch_id,
                            "item_id": first_item_id,
                            "user_id": user_id,
                            "course_id": course_id,
                            "note_id": note_id,
                        },
                    )
        finally:
            await engine.dispose()
    finally:
        await upgrade_database(test_database_url)


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
