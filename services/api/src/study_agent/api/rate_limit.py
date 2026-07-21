"""Bounded in-process abuse guard for expensive API entry points."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    """Enforce a per-key sliding-window request ceiling in one API process."""

    def __init__(
        self,
        *,
        window_seconds: float = 60.0,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if window_seconds <= 0 or max_keys <= 0:
            raise ValueError("rate limit bounds must be positive")
        self._window_seconds = window_seconds
        self._max_keys = max_keys
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str, limit: int) -> tuple[bool, int]:
        if not key or limit <= 0:
            raise ValueError("rate limit key and limit must be valid")
        async with self._lock:
            now = self._clock()
            cutoff = now - self._window_seconds
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= self._max_keys:
                    self._prune_empty(cutoff)
                if len(self._events) >= self._max_keys:
                    return False, max(1, int(self._window_seconds))
                events = deque()
                self._events[key] = events
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(events[0] + self._window_seconds - now) + 1)
                return False, retry_after
            events.append(now)
            return True, 0

    def _prune_empty(self, cutoff: float) -> None:
        for key, events in tuple(self._events.items()):
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                del self._events[key]
