"""Bounded, durable in-process runner for practice-question batches."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Protocol

from sqlalchemy import or_, select

from study_agent.config import Settings
from study_agent.infrastructure.db.models import PracticeBatchModel
from study_agent.infrastructure.db.models.core import new_id
from study_agent.infrastructure.db.session import Database
from study_agent.providers.protocols import Clock


class RetryableLearningBatchError(RuntimeError):
    """A batch can be retried without manufacturing a question."""


class LearningBatchProcessor(Protocol):
    async def process_batch(self, batch_id: str, runner_id: str) -> None: ...


class LearningBatchRunner:
    """Own bounded local tasks while persisting all control-plane transitions."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        clock: Clock,
        processor: LearningBatchProcessor,
    ) -> None:
        self._database = database
        self._settings = settings
        self._clock = clock
        self._processor = processor
        self._runner_id = f"practice-runner-{new_id()}"
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False

    def schedule(self, batch_id: str) -> None:
        if self._closed or batch_id in self._tasks:
            return
        task = asyncio.create_task(self._run_batch(batch_id), name=f"practice-batch:{batch_id}")
        self._tasks[batch_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(batch_id, None))

    async def recover_pending(self) -> None:
        if not self._settings.practice_runner_enabled:
            return
        async with self._database.worker_session(self._runner_id) as session:
            batch_ids = list(
                await session.scalars(
                    select(PracticeBatchModel.id)
                    .where(
                        PracticeBatchModel.status.in_(("queued", "running")),
                        or_(
                            PracticeBatchModel.status == "queued",
                            PracticeBatchModel.lease_expires_at < self._clock.now(),
                            PracticeBatchModel.lease_expires_at.is_(None),
                        ),
                    )
                    .order_by(PracticeBatchModel.created_at, PracticeBatchModel.id)
                    .limit(self._settings.practice_max_active_batches_per_user * 10)
                )
            )
        for batch_id in batch_ids:
            self.schedule(batch_id)

    async def run_once(self, batch_id: str | None = None) -> str | None:
        if batch_id is not None:
            await self._run_batch(batch_id)
            return batch_id
        async with self._database.worker_session(self._runner_id) as session:
            selected = await session.scalar(
                select(PracticeBatchModel.id)
                .where(
                    PracticeBatchModel.status == "queued",
                    PracticeBatchModel.lease_expires_at.is_(None),
                )
                .order_by(PracticeBatchModel.created_at, PracticeBatchModel.id)
                .limit(1)
            )
        if selected is None:
            return None
        await self._run_batch(selected)
        return selected

    async def shutdown(self) -> None:
        self._closed = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run_batch(self, batch_id: str) -> None:
        if not await self._claim(batch_id):
            return
        heartbeat = asyncio.create_task(
            self._heartbeat(batch_id), name=f"practice-heartbeat:{batch_id}"
        )
        try:
            await self._processor.process_batch(batch_id, self._runner_id)
        except asyncio.CancelledError:
            raise
        except RetryableLearningBatchError:
            await self._retry_or_fail(batch_id, "PROVIDER_RETRYABLE")
        except Exception:
            await self._fail(batch_id, "PRACTICE_RUNNER_FAILED")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _claim(self, batch_id: str) -> bool:
        now = self._clock.now()
        lease = now + timedelta(seconds=self._settings.practice_runner_lease_seconds)
        async with self._database.worker_session(self._runner_id) as session:
            batch = await session.scalar(
                select(PracticeBatchModel)
                .where(
                    PracticeBatchModel.id == batch_id,
                    PracticeBatchModel.status.in_(("queued", "running")),
                    or_(
                        PracticeBatchModel.status == "queued",
                        PracticeBatchModel.runner_id == self._runner_id,
                        PracticeBatchModel.lease_expires_at < now,
                        PracticeBatchModel.lease_expires_at.is_(None),
                    ),
                )
                .with_for_update()
            )
            if batch is None:
                return False
            batch.status = "running"
            batch.phase = "validating_inputs"
            batch.runner_id = self._runner_id
            batch.lease_expires_at = lease
            batch.attempt_count += 1
            batch.state_version += 1
            if batch.started_at is None:
                batch.started_at = now
            return True

    async def _heartbeat(self, batch_id: str) -> None:
        interval = max(1.0, self._settings.practice_runner_lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            async with self._database.worker_session(self._runner_id) as session:
                batch = await session.scalar(
                    select(PracticeBatchModel).where(
                        PracticeBatchModel.id == batch_id,
                        PracticeBatchModel.status == "running",
                        PracticeBatchModel.runner_id == self._runner_id,
                    )
                )
                if batch is None:
                    return
                batch.lease_expires_at = self._clock.now() + timedelta(
                    seconds=self._settings.practice_runner_lease_seconds
                )

    async def _retry_or_fail(self, batch_id: str, failure_code: str) -> None:
        async with self._database.worker_session(self._runner_id) as session:
            batch = await session.scalar(
                select(PracticeBatchModel)
                .where(
                    PracticeBatchModel.id == batch_id,
                    PracticeBatchModel.runner_id == self._runner_id,
                )
                .with_for_update()
            )
            if batch is None:
                return
            if batch.attempt_count < self._settings.practice_generation_max_attempts:
                batch.status = "queued"
                batch.phase = "generating"
                batch.runner_id = None
                batch.lease_expires_at = None
                batch.failure_code = failure_code
                batch.state_version += 1
                return
            batch.status = "failed"
            batch.phase = "generating"
            batch.failure_code = failure_code
            batch.runner_id = None
            batch.lease_expires_at = None
            batch.completed_at = self._clock.now()
            batch.state_version += 1

    async def _fail(self, batch_id: str, failure_code: str) -> None:
        async with self._database.worker_session(self._runner_id) as session:
            batch = await session.scalar(
                select(PracticeBatchModel)
                .where(
                    PracticeBatchModel.id == batch_id,
                    PracticeBatchModel.runner_id == self._runner_id,
                )
                .with_for_update()
            )
            if batch is None:
                return
            batch.status = "failed"
            batch.failure_code = failure_code
            batch.runner_id = None
            batch.lease_expires_at = None
            batch.completed_at = self._clock.now()
            batch.state_version += 1
