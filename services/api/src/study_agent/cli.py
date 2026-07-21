"""Operational command line entrypoints for the API package."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import NoReturn

from pydantic import ValidationError

from study_agent.config import Settings
from study_agent.providers.errors import ProviderError
from study_agent.providers.factory import build_provider_registry
from study_agent.providers.probe import ProviderProbeReport, probe_registry


async def _run_provider_probe(settings: Settings) -> ProviderProbeReport:
    registry = build_provider_registry(settings)
    try:
        report = await probe_registry(registry)
        return report
    finally:
        await registry.aclose()


def main() -> None:
    """Probe configured providers without printing credentials or private payloads."""

    if not _live_gate_open():
        _exit_with_error("LIVE_PROVIDER_PROBE_DISABLED", 2)
    try:
        # Do not load .env here: only explicitly injected, attested runtime secrets are eligible.
        settings = Settings(_env_file=None)
    except ValidationError:
        _exit_with_error("INVALID_CONFIGURATION", 2)
    try:
        report = asyncio.run(_run_provider_probe(settings))
    except ProviderError as exc:
        _exit_with_error(exc.code.value, 3, retryable=exc.retryable)
    except Exception:
        _exit_with_error("PROVIDER_PROBE_FAILED", 3)
    print(report.to_json())
    if report.status != "available":
        raise SystemExit(3)


def _live_gate_open() -> bool:
    return (
        os.environ.get("RUN_LIVE_PROVIDER_TESTS") == "1"
        and os.environ.get("PROVIDER_CREDENTIALS_ROTATED") == "1"
    )


def _exit_with_error(code: str, exit_code: int, *, retryable: bool = False) -> NoReturn:
    print(
        json.dumps(
            {"status": "unavailable", "code": code, "retryable": retryable},
            separators=(",", ":"),
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
