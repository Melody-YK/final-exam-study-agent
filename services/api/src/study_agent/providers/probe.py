"""Secret-safe runtime contract probes for configured model providers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal

from study_agent.providers.deepseek import CHAT_ENDPOINT_ALIAS, DeepSeekChatProvider
from study_agent.providers.embedding_openai import (
    EMBEDDING_ENDPOINT_ALIAS,
    OpenAICompatibleEmbeddingProvider,
)
from study_agent.providers.errors import ProviderError, ProviderErrorCode
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import EvidencePrompt, Passage


@dataclass(frozen=True, slots=True)
class CapabilityProbe:
    status: Literal["available", "not_configured", "unavailable"]
    endpoint_alias: str
    provider: str
    model: str | None
    dimensions: int | None
    capabilities: tuple[str, ...]
    elapsed_ms: int | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ProviderProbeReport:
    status: Literal["available", "partial", "unavailable"]
    checked_at: str
    embedding: CapabilityProbe
    chat: CapabilityProbe

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


async def probe_registry(registry: ProviderRegistry) -> ProviderProbeReport:
    """Probe each capability independently and retain only safe status metadata."""

    embedding = await _probe_embedding(registry)
    chat = await _probe_chat(registry)
    statuses = {embedding.status, chat.status}
    status: Literal["available", "partial", "unavailable"]
    if statuses == {"available"}:
        status = "available"
    elif "available" in statuses:
        status = "partial"
    else:
        status = "unavailable"
    return ProviderProbeReport(
        status=status,
        checked_at=datetime.now(UTC).isoformat(),
        embedding=embedding,
        chat=chat,
    )


async def _probe_embedding(registry: ProviderRegistry) -> CapabilityProbe:
    try:
        raw_provider = registry.embedding()
    except ProviderError as exc:
        if exc.code is not ProviderErrorCode.NOT_CONFIGURED:
            raise
        return _unavailable_capability(
            status="not_configured",
            endpoint_alias=EMBEDDING_ENDPOINT_ALIAS,
            provider="openai-compatible",
            error=exc,
        )
    if not isinstance(raw_provider, OpenAICompatibleEmbeddingProvider):
        raise TypeError("runtime embedding adapter is not probeable")

    started = perf_counter()
    try:
        contract = await raw_provider.probe()
    except ProviderError as exc:
        return _unavailable_capability(
            status="unavailable",
            endpoint_alias=EMBEDDING_ENDPOINT_ALIAS,
            provider="openai-compatible",
            model=raw_provider.model,
            error=exc,
            elapsed_ms=_elapsed_ms(started),
        )
    return CapabilityProbe(
        status="available",
        endpoint_alias=EMBEDDING_ENDPOINT_ALIAS,
        provider=contract.provider,
        model=contract.model,
        dimensions=contract.dimensions,
        capabilities=("batch", "query"),
        elapsed_ms=_elapsed_ms(started),
        error_code=None,
    )


async def _probe_chat(registry: ProviderRegistry) -> CapabilityProbe:
    try:
        raw_provider = registry.chat()
    except ProviderError as exc:
        if exc.code is not ProviderErrorCode.NOT_CONFIGURED:
            raise
        return _unavailable_capability(
            status="not_configured",
            endpoint_alias=CHAT_ENDPOINT_ALIAS,
            provider="deepseek",
            error=exc,
        )
    if not isinstance(raw_provider, DeepSeekChatProvider):
        raise TypeError("runtime chat adapter is not probeable")

    started = perf_counter()
    try:
        draft = await raw_provider.answer(
            EvidencePrompt(
                query="Return a provider health response as JSON.",
                passages=(
                    Passage(
                        id="provider-probe",
                        text="Synthetic contract probe; no user or course data is present.",
                    ),
                ),
            )
        )
    except ProviderError as exc:
        return _unavailable_capability(
            status="unavailable",
            endpoint_alias=CHAT_ENDPOINT_ALIAS,
            provider="deepseek",
            model=raw_provider.model,
            error=exc,
            elapsed_ms=_elapsed_ms(started),
        )
    capabilities = (
        ("json_output", "sse")
        if raw_provider.streaming
        else (
            "json_output",
            "non_stream",
        )
    )
    return CapabilityProbe(
        status="available",
        endpoint_alias=CHAT_ENDPOINT_ALIAS,
        provider="deepseek",
        model=draft.model,
        dimensions=None,
        capabilities=capabilities,
        elapsed_ms=_elapsed_ms(started),
        error_code=None,
    )


def _unavailable_capability(
    *,
    status: Literal["not_configured", "unavailable"],
    endpoint_alias: str,
    provider: str,
    error: ProviderError,
    model: str | None = None,
    elapsed_ms: int | None = None,
) -> CapabilityProbe:
    return CapabilityProbe(
        status=status,
        endpoint_alias=endpoint_alias,
        provider=provider,
        model=model,
        dimensions=None,
        capabilities=(),
        elapsed_ms=elapsed_ms,
        error_code=error.code.value,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1_000))
