"""OpenAI-compatible multimodal JSON completion transport."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable, Mapping

import httpx
from pydantic import SecretStr

from study_agent.providers._http import (
    RetryPolicy,
    bad_response,
    request_json,
)
from study_agent.providers.protocols import (
    StructuredJsonDraft,
    VisionJsonCompletionPrompt,
)

VISION_ENDPOINT_ALIAS = "vision-openai-compatible"
_PROVIDER_NAME = VISION_ENDPOINT_ALIAS
_MAX_TOKENS = 4096
_TEMPERATURE = 0.1
_USAGE_KEYS = {
    "prompt_tokens": "input_tokens",
    "completion_tokens": "output_tokens",
    "total_tokens": "total_tokens",
    "prompt_cache_hit_tokens": "cache_hit_tokens",
    "prompt_cache_miss_tokens": "cache_miss_tokens",
}
_IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


class OpenAICompatibleVisionProvider:
    """Small, bounded adapter for vision-capable OpenAI-compatible endpoints."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 0.5,
        retry_max_seconds: float = 8.0,
        max_response_bytes: int = 8 * 1024 * 1024,
        max_image_bytes: int = 10 * 1024 * 1024,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not base_url.strip() or not model.strip():
            raise ValueError("provider base URL and model must not be blank")
        if timeout_seconds <= 0 or max_image_bytes <= 0:
            raise ValueError("vision provider limits must be positive")
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model.strip()
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._max_image_bytes = max_image_bytes
        self._retry_policy = RetryPolicy(
            max_attempts=max_attempts,
            base_delay_seconds=retry_base_seconds,
            max_delay_seconds=retry_max_seconds,
        )
        self._http = http_client or httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_http = http_client is None
        self._sleep = sleep or asyncio.sleep

    @property
    def endpoint_alias(self) -> str:
        return _PROVIDER_NAME

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        if self._owns_http and not self._http.is_closed:
            await self._http.aclose()

    async def complete_json(self, request: VisionJsonCompletionPrompt) -> StructuredJsonDraft:
        if not request.images:
            raise bad_response(provider=_PROVIDER_NAME)
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "response_schema_version": request.response_schema_version,
                        "request": request.payload,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        ]
        for image in request.images:
            if image.media_type not in _IMAGE_MEDIA_TYPES or not image.data:
                raise bad_response(provider=_PROVIDER_NAME)
            if len(image.data) > self._max_image_bytes:
                raise bad_response(provider=_PROVIDER_NAME)
            encoded = base64.b64encode(image.data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image.media_type};base64,{encoded}",
                        "detail": image.detail,
                    },
                }
            )
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
            "max_tokens": _MAX_TOKENS,
            "temperature": _TEMPERATURE,
        }
        body = await request_json(
            self._http,
            url=self._url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_seconds=self._timeout_seconds,
            provider=_PROVIDER_NAME,
            retry_policy=self._retry_policy,
            max_response_bytes=self._max_response_bytes,
            sleep=self._sleep,
        )
        if not isinstance(body, Mapping):
            raise bad_response(provider=_PROVIDER_NAME)
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise bad_response(provider=_PROVIDER_NAME)
        first = choices[0]
        if not isinstance(first, Mapping):
            raise bad_response(provider=_PROVIDER_NAME)
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise bad_response(provider=_PROVIDER_NAME)
        raw_content = message.get("content")
        if not isinstance(raw_content, str):
            raise bad_response(provider=_PROVIDER_NAME)
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            raise bad_response(provider=_PROVIDER_NAME) from None
        if not isinstance(parsed, dict):
            raise bad_response(provider=_PROVIDER_NAME)
        return StructuredJsonDraft(
            payload=parsed,
            model=self._response_model(body),
            provider_response_id=self._response_id(body),
            usage=self._normalize_usage(body.get("usage")),
        )

    def _response_model(self, body: Mapping[object, object]) -> str:
        raw_model = body.get("model", self._model)
        if not isinstance(raw_model, str) or not raw_model.strip():
            raise bad_response(provider=_PROVIDER_NAME)
        return raw_model.strip()

    @staticmethod
    def _response_id(body: Mapping[object, object]) -> str | None:
        raw_id = body.get("id")
        if raw_id is None:
            return None
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise bad_response(provider=_PROVIDER_NAME)
        return raw_id.strip()

    @staticmethod
    def _normalize_usage(raw_usage: object) -> dict[str, int]:
        if raw_usage is None:
            return {}
        if not isinstance(raw_usage, Mapping):
            raise bad_response(provider=_PROVIDER_NAME)
        normalized: dict[str, int] = {}
        for upstream_key, canonical_key in _USAGE_KEYS.items():
            value = raw_usage.get(upstream_key)
            if value is None:
                continue
            if type(value) is not int or value < 0:
                raise bad_response(provider=_PROVIDER_NAME)
            normalized[canonical_key] = value
        return normalized
