"""Normalized provider failures that never retain upstream bodies or credentials."""

from __future__ import annotations

from enum import StrEnum


class ProviderErrorCode(StrEnum):
    """Stable failure categories shared by all model transports."""

    NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    TIMEOUT = "PROVIDER_TIMEOUT"
    RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    REQUEST_REJECTED = "PROVIDER_REQUEST_REJECTED"
    BAD_RESPONSE = "PROVIDER_BAD_RESPONSE"
    EMBEDDING_MODEL_CHANGED = "EMBEDDING_MODEL_CHANGED"
    EMBEDDING_DIMENSION_CHANGED = "EMBEDDING_DIMENSION_CHANGED"


_SAFE_MESSAGES: dict[ProviderErrorCode, str] = {
    ProviderErrorCode.NOT_CONFIGURED: "provider capability is not configured",
    ProviderErrorCode.TIMEOUT: "provider request timed out",
    ProviderErrorCode.RATE_LIMITED: "provider rate limit was reached",
    ProviderErrorCode.UNAVAILABLE: "provider is temporarily unavailable",
    ProviderErrorCode.REQUEST_REJECTED: "provider rejected the request",
    ProviderErrorCode.BAD_RESPONSE: "provider returned an invalid response",
    ProviderErrorCode.EMBEDDING_MODEL_CHANGED: "embedding model identity changed",
    ProviderErrorCode.EMBEDDING_DIMENSION_CHANGED: "embedding dimension changed",
}


class ProviderError(RuntimeError):
    """A secret-safe error suitable for domain and observability boundaries."""

    def __init__(
        self,
        code: ProviderErrorCode,
        *,
        provider: str,
        retryable: bool,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(f"{code.value}: {_SAFE_MESSAGES[code]}")
        self.code = code
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds

    def __repr__(self) -> str:
        return (
            "ProviderError("
            f"code={self.code.value!r}, provider={self.provider!r}, "
            f"retryable={self.retryable!r}, status_code={self.status_code!r})"
        )


def provider_not_configured(capability: str) -> ProviderError:
    return ProviderError(
        ProviderErrorCode.NOT_CONFIGURED,
        provider=capability,
        retryable=False,
    )
