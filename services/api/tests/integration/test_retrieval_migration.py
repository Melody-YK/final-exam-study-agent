import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from study_agent.infrastructure.db.migrations import downgrade_database, upgrade_database

RETRIEVAL_TABLES = {
    "chunk_embeddings",
    "embedding_models",
    "index_jobs",
    "lexical_manifests",
    "retrieval_traces",
}


@pytest.mark.integration
async def test_0005_creates_generic_vector_schema_without_ann_index(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    engine = create_async_engine(test_database_url)
    try:
        async with engine.connect() as connection:
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
            vector_type = await connection.scalar(
                text(
                    "SELECT format_type(a.atttypid, a.atttypmod) "
                    "FROM pg_attribute a "
                    "JOIN pg_class c ON c.oid = a.attrelid "
                    "WHERE c.relname = 'chunk_embeddings' "
                    "AND a.attname = 'embedding'"
                )
            )
            ann_indexes = list(
                (
                    await connection.execute(
                        text(
                            "SELECT indexdef FROM pg_indexes "
                            "WHERE schemaname = 'public' "
                            "AND tablename = 'chunk_embeddings' "
                            "AND (indexdef ILIKE '%hnsw%' OR indexdef ILIKE '%ivfflat%')"
                        )
                    )
                ).scalars()
            )
            constraints = set(
                (
                    await connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint WHERE conname IN "
                            "('fk_chunk_embeddings_model_dimension', "
                            "'fk_chunk_embeddings_chunk_revision', "
                            "'ck_chunk_embeddings_vector_dimensions', "
                            "'fk_courses_active_lexical_manifest')"
                        )
                    )
                ).scalars()
            )
    finally:
        await engine.dispose()

    assert tables >= RETRIEVAL_TABLES
    assert vector_type == "vector"
    assert ann_indexes == []
    assert constraints == {
        "fk_chunk_embeddings_model_dimension",
        "fk_chunk_embeddings_chunk_revision",
        "ck_chunk_embeddings_vector_dimensions",
        "fk_courses_active_lexical_manifest",
    }


@pytest.mark.integration
async def test_0005_downgrades_to_0004_and_upgrades_back_to_head(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    try:
        await downgrade_database(test_database_url, "20260719_0004")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                remaining = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT tablename FROM pg_tables "
                                "WHERE schemaname = 'public' "
                                "AND tablename = ANY(:tables)"
                            ),
                            {"tables": sorted(RETRIEVAL_TABLES)},
                        )
                    ).scalars()
                )
                chunk_constraint = await connection.scalar(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conname = 'uq_revision_chunks_id_revision'"
                    )
                )
        finally:
            await engine.dispose()
        assert version == "20260719_0004"
        assert remaining == set()
        assert chunk_constraint is None

        await upgrade_database(test_database_url, "20260719_0005")
        engine = create_async_engine(test_database_url)
        try:
            async with engine.connect() as connection:
                p7_version = await connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                restored = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT tablename FROM pg_tables "
                                "WHERE schemaname = 'public' "
                                "AND tablename = ANY(:tables)"
                            ),
                            {"tables": sorted(RETRIEVAL_TABLES)},
                        )
                    ).scalars()
                )
        finally:
            await engine.dispose()
        assert p7_version == "20260719_0005"
        assert restored == RETRIEVAL_TABLES
    finally:
        await upgrade_database(test_database_url)
