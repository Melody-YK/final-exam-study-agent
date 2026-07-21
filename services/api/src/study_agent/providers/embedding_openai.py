"""OpenAI-compatible embedding transport with batch and dimension validation."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from typing import Self

import httpx
from pydantic import SecretStr

from study_agent.providers._http import RetryPolicy, request_json
from study_agent.providers.errors import ProviderError, ProviderErrorCode
from study_agent.providers.protocols import EmbeddingContract

DEFAULT_EMBEDDING_BASE_URL = "https://router.tumuer.me/v1"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_ENDPOINT_ALIAS = "embedding-openai-compatible"
_PROVIDER_NAME = "openai-compatible"
_PROBE_INPUT = "provider-contract-probe"


class OpenAICompatibleEmbeddingProvider:
    """Real embedding adapter for the OpenAI ``/embeddings`` contract."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str = DEFAULT_EMBEDDING_BASE_URL,
        model: str = DEFAULT_EMBEDDING_MODEL,
        dimensions: int | None = None,
        batch_size: int = 64,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 0.5,
        retry_max_seconds: float = 8.0,
        max_response_bytes: int = 8 * 1024 * 1024,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not base_url.strip() or not model.strip():
            raise ValueError("provider base URL and model must not be blank")
        if dimensions is not None and dimensions <= 0:
            raise ValueError("dimensions must be positive")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._model = model.strip()
        self._configured_dimensions = dimensions
        self._observed_dimensions = dimensions
        self._observed_model: str | None = None
        self._batch_size = batch_size
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._retry_policy = RetryPolicy(
            max_attempts=max_attempts,
            base_delay_seconds=retry_base_seconds,
            max_delay_seconds=retry_max_seconds,
        )
        self._sleep = sleep or asyncio.sleep
        self._http = http_client or httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_http = http_client is None

    @property
    def endpoint_alias(self) -> str:
        return EMBEDDING_ENDPOINT_ALIAS

    @property
    def model(self) -> str:
        return self._model

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http and not self._http.is_closed:
            await self._http.aclose()

    async def probe(self) -> EmbeddingContract:
        vectors, response_model = await self._embed_batch([_PROBE_INPUT])
        return EmbeddingContract(
            provider=_PROVIDER_NAME,
            model=response_model,
            dimensions=len(vectors[0]),
            supports_batch=True,
        )

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), self._batch_size):
            batch_vectors, _response_model = await self._embed_batch(
                texts[offset : offset + self._batch_size]
            )
            vectors.extend(batch_vectors)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        vectors, _response_model = await self._embed_batch([text])
        return vectors[0]

    async def _embed_batch(self, texts: list[str]) -> tuple[list[list[float]], str]:
        payload: dict[str, object] = {"input": texts, "model": self._model}
        if self._configured_dimensions is not None:
            payload["dimensions"] = self._configured_dimensions
        body = await request_json(
            self._http,
            url=self._url,
            headers=self._headers(),
            payload=payload,
            timeout_seconds=self._timeout_seconds,
            provider=_PROVIDER_NAME,
            retry_policy=self._retry_policy,
            max_response_bytes=self._max_response_bytes,
            sleep=self._sleep,
        )
        return self._parse_response(body, expected_count=len(texts))

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

    def _parse_response(
        self,
        body: object,
        *,
        expected_count: int,
    ) -> tuple[list[list[float]], str]:
        if not isinstance(body, Mapping):
            raise self._bad_response()
        raw_data = body.get("data")
        if not isinstance(raw_data, list) or len(raw_data) != expected_count:
            raise self._bad_response()

        indexed: dict[int, list[float]] = {}
        for item in raw_data:
            if not isinstance(item, Mapping):
                raise self._bad_response()
            index = item.get("index")
            raw_vector = item.get("embedding")
            if type(index) is not int or not isinstance(raw_vector, list):
                raise self._bad_response()
            if index < 0 or index >= expected_count or index in indexed:
                raise self._bad_response()
            vector = self._parse_vector(raw_vector)
            indexed[index] = vector

        if set(indexed) != set(range(expected_count)):
            raise self._bad_response()
        vectors = [indexed[index] for index in range(expected_count)]
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1:
            raise self._dimension_error()
        observed = dimensions.pop()
        if self._observed_dimensions is not None and observed != self._observed_dimensions:
            raise self._dimension_error()
        self._observed_dimensions = observed

        if "model" not in body:
            model = self._model
        else:
            response_model = body.get("model")
            if not isinstance(response_model, str) or not response_model.strip():
                raise self._bad_response()
            model = response_model.strip()
        if not model:
            raise self._bad_response()
        if self._observed_model is not None and model != self._observed_model:
            raise self._model_error()
        self._observed_model = model
        return vectors, model

    def _parse_vector(self, raw_vector: list[object]) -> list[float]:
        if not raw_vector:
            raise self._bad_response()
        vector: list[float] = []
        for value in raw_vector:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise self._bad_response()
            normalized = float(value)
            if not math.isfinite(normalized):
                raise self._bad_response()
            vector.append(normalized)
        return vector

    @staticmethod
    def _bad_response() -> ProviderError:
        return ProviderError(
            ProviderErrorCode.BAD_RESPONSE,
            provider=_PROVIDER_NAME,
            retryable=False,
        )

    @staticmethod
    def _dimension_error() -> ProviderError:
        return ProviderError(
            ProviderErrorCode.EMBEDDING_DIMENSION_CHANGED,
            provider=_PROVIDER_NAME,
            retryable=False,
        )

    @staticmethod
    def _model_error() -> ProviderError:
        return ProviderError(
            ProviderErrorCode.EMBEDDING_MODEL_CHANGED,
            provider=_PROVIDER_NAME,
            retryable=False,
        )
