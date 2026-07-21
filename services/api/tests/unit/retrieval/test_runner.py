from __future__ import annotations

import asyncio
from pathlib import Path
from typing import NoReturn

import pytest

from study_agent import runner
from study_agent.config import AppMode, Settings
from study_agent.providers.errors import ProviderError, ProviderErrorCode


class StopRunner(RuntimeError):
    pass


@pytest.mark.parametrize("provider_available", [True, False])
@pytest.mark.asyncio
async def test_runner_builds_local_indexes_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider_available: bool
) -> None:
    events: list[object] = []
    provider = object()

    class FakeDatabase:
        def __init__(self, url: str) -> None:
            events.append(("database", url))

        async def dispose(self) -> None:
            events.append("database-closed")

    class FakeRegistry:
        def embedding(self) -> object:
            if not provider_available:
                raise ProviderError(
                    ProviderErrorCode.NOT_CONFIGURED, provider="embedding", retryable=False
                )
            return provider

        async def aclose(self) -> None:
            events.append("registry-closed")

    class FakeIndexRunner:
        def __init__(
            self,
            _repository: object,
            _lexical: object,
            *,
            provider: object | None,
            batch_size: int,
        ) -> None:
            events.append(("runner", provider, batch_size))

        async def resume_provider_blocked(self) -> int:
            events.append("resumed")
            return 0

        async def run_once(self) -> None:
            events.append("idle")
            return None

    async def stop_after_idle(_seconds: float) -> NoReturn:
        raise StopRunner

    monkeypatch.setattr(runner, "Database", FakeDatabase)
    monkeypatch.setattr(runner, "build_provider_registry", lambda _settings: FakeRegistry())
    monkeypatch.setattr(runner, "PostgresIndexRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(runner, "Bm25IndexStore", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(runner, "ChineseTokenizer", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(runner, "IndexRunner", FakeIndexRunner)
    monkeypatch.setattr(asyncio, "sleep", stop_after_idle)
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        lexical_index_root=tmp_path,
        embedding_batch_size=7,
    )

    with pytest.raises(StopRunner):
        await runner.serve(settings)

    expected_provider = provider if provider_available else None
    assert ("runner", expected_provider, 7) in events
    assert events[-2:] == ["registry-closed", "database-closed"]
