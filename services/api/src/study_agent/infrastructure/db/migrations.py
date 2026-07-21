"""Programmatic Alembic entry points used by tests and local tooling."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

_API_ROOT = Path(__file__).resolve().parents[4]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


async def _run(database_url: str, revision: str, *, downgrade: bool) -> None:
    engine = create_async_engine(database_url)
    config = _alembic_config(database_url)

    def invoke(connection: Connection) -> None:
        config.attributes["connection"] = connection
        if downgrade:
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(invoke)
    finally:
        await engine.dispose()


async def upgrade_database(database_url: str, revision: str = "head") -> None:
    """Upgrade a database without nesting an event loop."""

    await _run(database_url, revision, downgrade=False)


async def downgrade_database(database_url: str, revision: str = "base") -> None:
    """Downgrade an expendable local/test database."""

    await _run(database_url, revision, downgrade=True)
