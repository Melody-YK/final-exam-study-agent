"""Small request trace context independent of the web framework."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import uuid4

_trace_id: ContextVar[str | None] = ContextVar("study_agent_trace_id", default=None)


def new_trace_id() -> str:
    return uuid4().hex


def get_trace_id() -> str | None:
    return _trace_id.get()


def set_trace_id(trace_id: str) -> Token[str | None]:
    normalized = trace_id.strip()
    if not normalized:
        raise ValueError("trace_id must not be blank")
    return _trace_id.set(normalized)


def reset_trace_id(token: Token[str | None]) -> None:
    _trace_id.reset(token)


@contextmanager
def trace_context(trace_id: str | None = None) -> Iterator[str]:
    current = trace_id or new_trace_id()
    token = set_trace_id(current)
    try:
        yield current
    finally:
        reset_trace_id(token)
