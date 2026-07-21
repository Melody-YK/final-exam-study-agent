import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from study_agent.infrastructure.db.migrations import downgrade_database, upgrade_database

ANSWERING_TABLES = {
    "answer_dependencies",
    "notes",
    "note_sources",
    "query_events",
    "query_runs",
    "retrieval_snapshots",
}


@pytest.mark.integration
async def test_0006_creates_query_note_and_dependency_schema(test_database_url: str) -> None:
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
                            ]
                        },
                    )
                ).scalars()
            )
    finally:
        await engine.dispose()

    assert version == "20260719_0006"
    assert tables >= ANSWERING_TABLES
    assert constraints == {
        "ck_query_runs_status",
        "ck_query_events_sequence_positive",
        "ck_notes_version_positive",
        "uq_note_sources_note_evidence",
        "uq_answer_dependencies_query_evidence",
    }


@pytest.mark.integration
async def test_0006_downgrades_cleanly_to_0005(test_database_url: str) -> None:
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
