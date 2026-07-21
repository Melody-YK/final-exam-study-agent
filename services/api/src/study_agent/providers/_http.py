"""Shared retry and error normalization for provider HTTP transports."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import httpx

from study_agent.providers.errors import ProviderError, ProviderErrorCode

type Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must not be negative")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be at least base_delay_seconds")


def status_error(response: httpx.Response, *, provider: str) -> ProviderError | None:
    status = response.status_code
    if 200 <= status < 300:
        return None
    retry_after = _retry_after_seconds(response.headers)
    if status == 429:
        return ProviderError(
            ProviderErrorCode.RATE_LIMITED,
            provider=provider,
            retryable=True,
            status_code=status,
            retry_after_seconds=retry_after,
        )
    if status >= 500:
        return ProviderError(
            ProviderErrorCode.UNAVAILABLE,
            provider=provider,
            retryable=True,
            status_code=status,
            retry_after_seconds=retry_after,
        )
    return ProviderError(
        ProviderErrorCode.REQUEST_REJECTED,
        provider=provider,
        retryable=False,
        status_code=status,
    )


def transport_error(exc: httpx.RequestError, *, provider: str) -> ProviderError:
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(
            ProviderErrorCode.TIMEOUT,
            provider=provider,
            retryable=True,
        )
    return ProviderError(
        ProviderErrorCode.UNAVAILABLE,
        provider=provider,
        retryable=True,
    )


def bad_response(*, provider: str, retryable: bool = False) -> ProviderError:
    return ProviderError(
        ProviderErrorCode.BAD_RESPONSE,
        provider=provider,
        retryable=retryable,
    )


def can_retry(error: ProviderError, *, attempt: int, policy: RetryPolicy) -> bool:
    return error.retryable and attempt < policy.max_attempts


async def wait_before_retry(
    error: ProviderError,
    *,
    attempt: int,
    policy: RetryPolicy,
    sleep: Sleep,
) -> None:
    exponential = policy.base_delay_seconds * (2 ** (attempt - 1))
    delay = error.retry_after_seconds if error.retry_after_seconds is not None else exponential
    await sleep(min(delay, policy.max_delay_seconds))


async def request_json(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, object],
    timeout_seconds: float,
    provider: str,
    retry_policy: RetryPolicy,
    max_response_bytes: int = 8 * 1024 * 1024,
    sleep: Sleep = asyncio.sleep,
) -> object:
    if max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be positive")
    for attempt in range(1, retry_policy.max_attempts + 1):
        try:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            ) as response:
                if response.headers.get("Content-Length") is not None:
                    try:
                        declared_size = int(response.headers["Content-Length"])
                    except ValueError:
                        declared_size = 0
                    if declared_size > max_response_bytes:
                        raise bad_response(provider=provider)
                status_failure = status_error(response, provider=provider)
                if status_failure is None:
                    body = await _read_limited_response(
                        response,
                        max_response_bytes,
                        provider=provider,
                    )
                    try:
                        return json.loads(body)
                    except (TypeError, ValueError):
                        raise bad_response(provider=provider) from None
                error = status_failure
        except httpx.RequestError as exc:
            error = transport_error(exc, provider=provider)
        if not can_retry(error, attempt=attempt, policy=retry_policy):
            raise error from None
        await wait_before_retry(
            error,
            attempt=attempt,
            policy=retry_policy,
            sleep=sleep,
        )
    raise AssertionError("retry loop exhausted without returning or raising")


async def _read_limited_response(
    response: httpx.Response,
    max_bytes: int,
    *,
    provider: str,
) -> bytes:
    body = bytearray()
    async for block in response.aiter_bytes():
        body.extend(block)
        if len(body) > max_bytes:
            raise bad_response(provider=provider)
    return bytes(body)


def _retry_after_seconds(headers: httpx.Headers) -> float | None:
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if parsed < 0:
        return None
    return min(parsed, 60.0)
