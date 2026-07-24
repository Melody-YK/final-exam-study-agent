from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from study_agent.infrastructure.db.migrations import downgrade_database, upgrade_database

ANSWERING_TABLES = {
    "answer_dependencies",
    "conversations",
    "notes",
    "note_sources",
    "query_events",
    "query_runs",
    "retrieval_snapshots",
}


@pytest.mark.integration
async def test_head_creates_conversation_query_note_and_dependency_schema(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    engine = create_async_engine(test_database_url)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
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
                        text("SELECT conname FROM pg_constraint WHERE conname = ANY(:names)"),
                        {
                            "names": [
                                "ck_query_runs_status",
                                "ck_query_events_sequence_positive",
                                "ck_notes_version_positive",
                                "uq_note_sources_note_evidence",
                                "uq_answer_dependencies_query_evidence",
                                "fk_query_runs_conversation_scope",
                            ]
                        },
                    )
                ).scalars()
            )
    finally:
        await engine.dispose()

    assert version == "20260724_0012"
    assert tables >= ANSWERING_TABLES
    assert constraints == {
        "ck_query_runs_status",
        "ck_query_events_sequence_positive",
        "ck_notes_version_positive",
        "uq_note_sources_note_evidence",
        "uq_answer_dependencies_query_evidence",
        "fk_query_runs_conversation_scope",
    }


@pytest.mark.integration
async def test_head_downgrades_cleanly_to_0005(test_database_url: str) -> None:
    await upgrade_database(test_database_url)
    try:
        await downgrade_database(test_database_url, "20260719_0005")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                remaining = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT tablename FROM pg_tables "
                                "WHERE schemaname = 'public' AND tablename = ANY(:tables)"
                            ),
                            {"tables": sorted(ANSWERING_TABLES)},
                        )
                    ).scalars()
                )
        finally:
            await engine.dispose()
        assert version == "20260719_0005"
        assert remaining == set()
    finally:
        await upgrade_database(test_database_url)


@pytest.mark.integration
async def test_0007_migrates_existing_queries_into_one_legacy_conversation_per_course(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    try:
        await downgrade_database(test_database_url, "20260719_0006")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO users (id, subject, authentication_method) VALUES "
                        "('user-legacy', 'legacy-subject', 'local')"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO courses (id, user_id, title, lifecycle) VALUES "
                        "('course-a', 'user-legacy', '课程 A', 'active'), "
                        "('course-b', 'user-legacy', '课程 B', 'active')"
                    )
                )
                for query_id, course_id, question, created_at in (
                    (
                        "query-a1",
                        "course-a",
                        "问题 A1",
                        datetime.fromisoformat("2026-07-20T01:00:00+08:00"),
                    ),
                    (
                        "query-a2",
                        "course-a",
                        "问题 A2",
                        datetime.fromisoformat("2026-07-20T02:00:00+08:00"),
                    ),
                    (
                        "query-b1",
                        "course-b",
                        "问题 B1",
                        datetime.fromisoformat("2026-07-20T03:00:00+08:00"),
                    ),
                ):
                    await connection.execute(
                        text(
                            "INSERT INTO query_runs ("
                            "id, user_id, course_id, question, question_sha256, status, "
                            "answer_schema_version, answer_markdown, trace_id, event_sequence, "
                            "created_at, updated_at"
                            ") VALUES ("
                            ":id, 'user-legacy', :course_id, :question, :question_sha256, "
                            "'answered', '1.0', '历史回答', :trace_id, 0, :created_at, :created_at"
                            ")"
                        ),
                        {
                            "id": query_id,
                            "course_id": course_id,
                            "question": question,
                            "question_sha256": query_id.ljust(64, "0"),
                            "trace_id": f"trace-{query_id}",
                            "created_at": created_at,
                        },
                    )
        finally:
            await engine.dispose()

        await upgrade_database(test_database_url)
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                rows = (
                    await connection.execute(
                        text(
                            "SELECT q.course_id, count(DISTINCT q.conversation_id) AS groups, "
                            "min(c.title) AS title, count(*) AS turns "
                            "FROM query_runs AS q "
                            "JOIN conversations AS c ON c.id = q.conversation_id "
                            "AND c.course_id = q.course_id AND c.user_id = q.user_id "
                            "GROUP BY q.course_id ORDER BY q.course_id"
                        )
                    )
                ).all()
        finally:
            await engine.dispose()

        assert rows == [
            ("course-a", 1, "历史问答", 2),
            ("course-b", 1, "历史问答", 1),
        ]

        await downgrade_database(test_database_url, "20260719_0006")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                query_count = await connection.scalar(text("SELECT count(*) FROM query_runs"))
                conversation_column = await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'query_runs' "
                        "AND column_name = 'conversation_id'"
                    )
                )
        finally:
            await engine.dispose()
        assert query_count == 3
        assert conversation_column == 0

        await upgrade_database(test_database_url)
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                regrouped = (
                    await connection.execute(
                        text(
                            "SELECT q.course_id, count(DISTINCT q.conversation_id), count(*) "
                            "FROM query_runs AS q GROUP BY q.course_id ORDER BY q.course_id"
                        )
                    )
                ).all()
        finally:
            await engine.dispose()
        assert regrouped == [("course-a", 1, 2), ("course-b", 1, 1)]
    finally:
        await upgrade_database(test_database_url)
