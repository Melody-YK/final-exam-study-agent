"""Principal-bound asynchronous database sessions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from study_agent.identity.principal import Principal


class Database:
    """Own the async engine while denying unscoped application sessions.

    Each transaction records its trusted principal both in ``session.info`` and
    in transaction-local PostgreSQL settings. Repositories still include the
    subject in every ownership query; the setting is a foundation for future
    row-level-security policies, not a replacement for explicit predicates.
    """

    def __init__(self, database_url: str) -> None:
        self._engine = create_async_engine(database_url, pool_pre_ping=True)
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @asynccontextmanager
    async def transaction(self, principal: Principal) -> AsyncIterator[AsyncSession]:
        if not isinstance(principal, Principal):
            raise TypeError("a trusted Principal is required for database access")

        async with self._session_factory.begin() as session:
            session.info["principal"] = principal
            await session.execute(
                select(
                    func.set_config("study_agent.principal_subject", principal.subject, True),
                    func.set_config(
                        "study_agent.authentication_method",
                        principal.authentication_method.value,
                        True,
                    ),
                )
            )
            yield session

    @asynccontextmanager
    async def session(self, principal: Principal) -> AsyncIterator[AsyncSession]:
        """Alias exposing the principal-bound transaction boundary."""

        async with self.transaction(principal) as session:
            yield session

    @asynccontextmanager
    async def worker_transaction(self, worker_id: str) -> AsyncIterator[AsyncSession]:
        normalized_worker_id = worker_id.strip()
        if not normalized_worker_id or len(normalized_worker_id) > 128:
            raise ValueError("worker_id must be between 1 and 128 characters")

        async with self._session_factory.begin() as session:
            session.info["worker_id"] = normalized_worker_id
            await session.execute(
                select(
                    func.set_config("study_agent.actor_type", "worker", True),
                    func.set_config("study_agent.worker_id", normalized_worker_id, True),
                )
            )
            yield session

    @asynccontextmanager
    async def worker_session(self, worker_id: str) -> AsyncIterator[AsyncSession]:
        """Database boundary available only after Worker API authentication."""

        async with self.worker_transaction(worker_id) as session:
            yield session

    async def dispose(self) -> None:
        await self._engine.dispose()
