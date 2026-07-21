"""Principal-scoped idempotency records used inside domain transactions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import IdempotencyRecordModel


class IdempotencyService:
    def __init__(self, *, retention: timedelta = timedelta(hours=24)) -> None:
        self._retention = retention

    @staticmethod
    def request_hash(payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    async def lock(
        session: AsyncSession,
        principal: Principal,
        *,
        operation: str,
        key: str,
    ) -> None:
        lock_name = ":".join(
            (
                principal.authentication_method.value,
                principal.subject,
                operation,
                key,
            )
        )
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": lock_name},
        )

    async def replay_or_none(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        operation: str,
        key: str,
        request_hash: str,
    ) -> IdempotencyRecordModel | None:
        record = await session.scalar(
            select(IdempotencyRecordModel)
            .where(
                IdempotencyRecordModel.actor_subject == principal.subject,
                IdempotencyRecordModel.actor_authentication_method
                == principal.authentication_method.value,
                IdempotencyRecordModel.operation == operation,
                IdempotencyRecordModel.idempotency_key == key,
            )
            .with_for_update()
        )
        if record is None:
            return None
        if record.expires_at <= datetime.now(UTC):
            await session.delete(record)
            await session.flush()
            return None
        if record.request_hash != request_hash:
            raise ApiProblem(
                status=409,
                code=ProblemCode.IDEMPOTENCY_CONFLICT,
                title="幂等键冲突",
                detail="相同 Idempotency-Key 已用于不同请求。",
            )
        return record

    def store(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        operation: str,
        key: str,
        request_hash: str,
        response_status: int,
        response_body: dict[str, Any],
    ) -> None:
        session.add(
            IdempotencyRecordModel(
                id=str(uuid4()),
                actor_subject=principal.subject,
                actor_authentication_method=principal.authentication_method.value,
                operation=operation,
                idempotency_key=key,
                request_hash=request_hash,
                response_status=response_status,
                response_body=response_body,
                expires_at=datetime.now(UTC) + self._retention,
            )
        )
