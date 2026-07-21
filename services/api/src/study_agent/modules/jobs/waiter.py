"""Injectable bounded wait used by Worker long polling."""

import asyncio
from typing import Protocol


class ClaimWaiter(Protocol):
    async def wait(self, timeout_seconds: float) -> None: ...


class AsyncioClaimWaiter:
    async def wait(self, timeout_seconds: float) -> None:
        await asyncio.sleep(timeout_seconds)
