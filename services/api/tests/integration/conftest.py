import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg:///study_agent_test")


@pytest_asyncio.fixture(autouse=True)
async def clean_database(test_database_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(test_database_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "DO $$ DECLARE r RECORD; BEGIN "
                "FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP "
                "IF r.tablename <> 'alembic_version' THEN "
                "EXECUTE 'TRUNCATE TABLE ' || quote_ident(r.tablename) || ' CASCADE'; "
                "END IF; END LOOP; END $$;"
            )
        )
    await engine.dispose()
    yield
