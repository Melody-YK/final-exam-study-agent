"""Ephemeral worker liveness derived from authenticated claim requests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from study_contracts import JobClaimRequest


@dataclass(frozen=True, slots=True)
class WorkerAvailability:
    native_parser: bool
    ocr_parser: bool


@dataclass(frozen=True, slots=True)
class _Presence:
    parser_profiles: frozenset[str]
    supports_ocr: bool
    seen_at: datetime


class WorkerPresenceRegistry:
    """Keep only recent capability facts; worker identifiers never leave this registry."""

    def __init__(self, *, max_workers: int = 1_024) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        self._max_workers = max_workers
        self._workers: dict[str, _Presence] = {}
        self._lock = asyncio.Lock()

    async def record(self, request: JobClaimRequest, *, now: datetime) -> None:
        async with self._lock:
            self._remove_stale(now, max_age=timedelta(hours=1))
            if request.worker_id not in self._workers and len(self._workers) >= self._max_workers:
                oldest = min(self._workers, key=lambda worker_id: self._workers[worker_id].seen_at)
                del self._workers[oldest]
            self._workers[request.worker_id] = _Presence(
                parser_profiles=frozenset(request.capabilities.parser_profiles),
                supports_ocr=request.capabilities.supports_ocr,
                seen_at=now,
            )

    async def availability(self, *, now: datetime, max_age: timedelta) -> WorkerAvailability:
        if max_age <= timedelta(0):
            raise ValueError("presence max_age must be positive")
        async with self._lock:
            self._remove_stale(now, max_age=max_age)
            presences = tuple(self._workers.values())
        return WorkerAvailability(
            native_parser=any("native-v1" in presence.parser_profiles for presence in presences),
            ocr_parser=any(
                "ocr-v1" in presence.parser_profiles and presence.supports_ocr
                for presence in presences
            ),
        )

    def _remove_stale(self, now: datetime, *, max_age: timedelta) -> None:
        cutoff = now - max_age
        stale = [worker_id for worker_id, value in self._workers.items() if value.seen_at < cutoff]
        for worker_id in stale:
            del self._workers[worker_id]
