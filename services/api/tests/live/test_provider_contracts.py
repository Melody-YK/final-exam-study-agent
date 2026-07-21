from __future__ import annotations

import os

import pytest

from study_agent.config import Settings
from study_agent.providers.factory import build_provider_registry
from study_agent.providers.probe import probe_registry

_LIVE_GATE_OPEN = (
    os.environ.get("RUN_LIVE_PROVIDER_TESTS") == "1"
    and os.environ.get("PROVIDER_CREDENTIALS_ROTATED") == "1"
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not _LIVE_GATE_OPEN,
        reason=(
            "external-blocked: requires RUN_LIVE_PROVIDER_TESTS=1 and "
            "PROVIDER_CREDENTIALS_ROTATED=1"
        ),
    ),
]


@pytest.mark.asyncio
async def test_rotated_runtime_secrets_satisfy_live_provider_contracts() -> None:
    # Deliberately bypass .env so a previously exposed local value cannot be loaded implicitly.
    settings = Settings(_env_file=None)
    if not settings.providers_configured:
        pytest.skip("external-blocked: rotated runtime provider secrets are not injected")

    registry = build_provider_registry(settings)
    try:
        report = await probe_registry(registry)
    finally:
        await registry.aclose()

    assert report.status == "available"
    assert report.embedding.status == "available"
    assert report.embedding.dimensions is not None
    assert report.chat.status == "available"
