"""Fail-closed runtime registry for real model provider adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Self

import httpx

from study_agent.config import Settings
from study_agent.providers.deepseek import DeepSeekChatProvider
from study_agent.providers.embedding_openai import OpenAICompatibleEmbeddingProvider
from study_agent.providers.errors import provider_not_configured
from study_agent.providers.protocols import ChatProvider, EmbeddingProvider

SUPPORTED_EMBEDDING_PROVIDERS = ("openai-compatible",)
SUPPORTED_CHAT_PROVIDERS = ("deepseek",)


class ProviderRegistry:
    """Own provider availability and one optional shared HTTP client."""

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider | None,
        chat_provider: ChatProvider | None,
        http_client: httpx.AsyncClient | None,
        owns_http_client: bool,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._chat_provider = chat_provider
        self._http_client = http_client
        self._owns_http_client = owns_http_client

    def __repr__(self) -> str:
        return (
            "ProviderRegistry("
            f"embedding_configured={self._embedding_provider is not None}, "
            f"chat_configured={self._chat_provider is not None})"
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object,
    ) -> None:
        await self.aclose()

    def embedding(self) -> EmbeddingProvider:
        if self._embedding_provider is None:
            raise provider_not_configured("embedding")
        return self._embedding_provider

    def chat(self) -> ChatProvider:
        if self._chat_provider is None:
            raise provider_not_configured("chat")
        return self._chat_provider

    async def aclose(self) -> None:
        if (
            self._owns_http_client
            and self._http_client is not None
            and not self._http_client.is_closed
        ):
            await self._http_client.aclose()


def build_provider_registry(
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> ProviderRegistry:
    """Construct only configured real adapters; missing capabilities stay unavailable."""

    if (
        settings.embedding_configured
        and settings.embedding_provider not in SUPPORTED_EMBEDDING_PROVIDERS
    ):
        raise provider_not_configured("embedding")
    if settings.chat_configured and settings.chat_provider not in SUPPORTED_CHAT_PROVIDERS:
        raise provider_not_configured("chat")
    if http_client is not None and http_client.is_closed:
        raise ValueError("injected provider HTTP client is closed")

    needs_http = settings.embedding_configured or settings.chat_configured
    shared_http = http_client
    owns_http = False
    if needs_http and shared_http is None:
        shared_http = httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
        )
        owns_http = True

    embedding_provider: EmbeddingProvider | None = None
    if settings.embedding_api_key is not None:
        embedding_provider = OpenAICompatibleEmbeddingProvider(
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            batch_size=settings.embedding_batch_size,
            timeout_seconds=settings.provider_timeout_seconds,
            max_attempts=settings.provider_max_attempts,
            retry_base_seconds=settings.provider_retry_base_seconds,
            retry_max_seconds=settings.provider_retry_max_seconds,
            max_response_bytes=settings.provider_max_response_bytes,
            http_client=shared_http,
            sleep=sleep,
        )

    chat_provider: ChatProvider | None = None
    if settings.deepseek_api_key is not None:
        chat_provider = DeepSeekChatProvider(
            api_key=settings.deepseek_api_key,
            base_url=settings.chat_base_url,
            model=settings.chat_model,
            stream=settings.chat_stream,
            timeout_seconds=settings.provider_timeout_seconds,
            max_attempts=settings.provider_max_attempts,
            retry_base_seconds=settings.provider_retry_base_seconds,
            retry_max_seconds=settings.provider_retry_max_seconds,
            max_response_bytes=settings.provider_max_response_bytes,
            max_stream_events=settings.provider_max_stream_events,
            max_stream_event_bytes=settings.provider_max_stream_event_bytes,
            max_answer_chars=settings.provider_max_answer_chars,
            http_client=shared_http,
            sleep=sleep,
        )

    return ProviderRegistry(
        embedding_provider=embedding_provider,
        chat_provider=chat_provider,
        http_client=shared_http,
        owns_http_client=owns_http,
    )
