"""Runtime entry point for the single-concurrency retrieval index runner."""

from __future__ import annotations

import asyncio
import os
import socket

from study_agent.config import Settings
from study_agent.infrastructure.db.session import Database
from study_agent.modules.ingestion.index_repository import PostgresIndexRepository
from study_agent.modules.ingestion.index_runner import IndexRunner
from study_agent.modules.retrieval.bm25_index import Bm25IndexStore
from study_agent.modules.retrieval.tokenizer import ChineseTokenizer
from study_agent.providers.errors import ProviderError
from study_agent.providers.factory import build_provider_registry
from study_agent.providers.protocols import EmbeddingProvider


async def serve(settings: Settings) -> None:
    database = Database(settings.database_url.get_secret_value())
    registry = build_provider_registry(settings)
    try:
        provider: EmbeddingProvider | None
        try:
            provider = registry.embedding()
        except ProviderError:
            provider = None
        runner_id = f"{socket.gethostname()}:{os.getpid()}"
        repository = PostgresIndexRepository(
            database,
            runner_id=runner_id,
            requested_provider=settings.embedding_provider,
            requested_model=settings.embedding_model,
            contract_version="1",
        )
        runner = IndexRunner(
            repository,
            Bm25IndexStore(
                settings.lexical_index_root,
                ChineseTokenizer(settings.course_terms),
            ),
            provider=provider,
            batch_size=settings.embedding_batch_size,
        )
        await runner.resume_provider_blocked()
        while True:
            outcome = await runner.run_once()
            if outcome is None:
                await asyncio.sleep(settings.index_runner_poll_seconds)
    finally:
        await registry.aclose()
        await database.dispose()


def main() -> None:
    asyncio.run(serve(Settings()))


if __name__ == "__main__":
    main()
