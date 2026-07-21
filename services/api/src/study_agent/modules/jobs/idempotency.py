"""Worker command idempotency without persisting raw lease tokens."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.infrastructure.db.models import IdempotencyRecordModel
from study_agent.observability.trace import new_trace_id


class WorkerIdempotency:
    def __init__(self, retention: timedelta = timedelta(hours=24)) -> None:
        self._retention = retention

    @staticmethod
    def request_hash(payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    async def lock(session: AsyncSession, worker_id: str, operation: str, key: str) -> None:
        lock_name = f"worker:{worker_id}:{operation}:{key}"
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": lock_name},
        )

    async def replay_or_none(
        self,
        session: AsyncSession,
        *,
        worker_id: str,
        operation: str,
        key: str,
        request_hash: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        record = cast(
            IdempotencyRecordModel | None,
            await session.scalar(
                select(IdempotencyRecordModel)
                .where(
                    IdempotencyRecordModel.actor_subject == worker_id,
                    IdempotencyRecordModel.actor_authentication_method == "worker",
                    IdempotencyRecordModel.operation == operation,
                    IdempotencyRecordModel.idempotency_key == key,
                )
                .with_for_update(of=IdempotencyRecordModel)
            ),
        )
        if record is None:
            return None
        if record.expires_at <= now:
            await session.delete(record)
            await session.flush()
            return None
        if record.request_hash != request_hash:
            raise ApiProblem(
                status=409,
                code=ProblemCode.IDEMPOTENCY_CONFLICT,
                title="幂等键冲突",
                detail="相同 Idempotency-Key 已用于不同 Worker 命令。",
            )
        return record.response_body

    def store(
        self,
        session: AsyncSession,
        *,
        worker_id: str,
        operation: str,
        key: str,
        request_hash: str,
        response_body: dict[str, Any],
        now: datetime,
    ) -> None:
        session.add(
            IdempotencyRecordModel(
                id=new_trace_id(),
                actor_subject=worker_id,
                actor_authentication_method="worker",
                operation=operation,
                idempotency_key=key,
                request_hash=request_hash,
                response_status=200,
                response_body=response_body,
                expires_at=now + self._retention,
            )
        )
