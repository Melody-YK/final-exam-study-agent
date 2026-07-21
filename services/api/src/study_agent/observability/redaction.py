"""Non-mutating redaction for structured logs and trace attributes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import cast

from pydantic import SecretBytes, SecretStr

REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "body",
        "client_secret",
        "content",
        "cookie",
        "credentials",
        "database_url",
        "deepseek_api_key",
        "embedding_api_key",
        "error",
        "exc_info",
        "exception",
        "failure",
        "lease_token",
        "object_key",
        "password",
        "passages",
        "private_key",
        "prompt",
        "proxy_authorization",
        "query",
        "refresh_token",
        "request_body",
        "request_content",
        "response_body",
        "response_content",
        "secret",
        "set_cookie",
        "signed_url",
        "stack",
        "stack_info",
        "token",
        "worker_token",
    }
)
_SENSITIVE_SUFFIXES = (
    "_access_token",
    "_api_key",
    "_authorization",
    "_client_secret",
    "_credentials",
    "_database_url",
    "_lease_token",
    "_object_key",
    "_password",
    "_private_key",
    "_refresh_token",
    "_secret",
    "_signed_url",
    "_worker_token",
)
_KEY_SEPARATOR = re.compile(r"[^a-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _normalize_key(key: str) -> str:
    snake_like = _CAMEL_BOUNDARY.sub("_", key)
    return _KEY_SEPARATOR.sub("_", snake_like.lower()).strip("_")


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = _normalize_key(key)
    return normalized in _SENSITIVE_KEYS or normalized.endswith(_SENSITIVE_SUFFIXES)


def _redact(value: object) -> object:
    if isinstance(value, SecretStr | SecretBytes):
        return REDACTED
    if isinstance(value, BaseException):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            key: REDACTED if _is_sensitive_key(key) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    if isinstance(value, set):
        return {_redact(item) for item in value}
    if isinstance(value, frozenset):
        return frozenset(_redact(item) for item in value)
    return value


def redact[T](value: T) -> T:
    """Return a recursively redacted copy while preserving common containers."""

    return cast(T, _redact(value))


def redact_event(
    _logger: object,
    _method_name: str,
    event_dict: dict[str, object],
) -> dict[str, object]:
    """Structlog-compatible processor that redacts the complete event tree."""

    return redact(event_dict)
