"""Explicit, course-scoped learner memory with user-managed lifecycle."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    CourseModel,
    LearnerMemoryModel,
    UserModel,
)
from study_agent.infrastructure.db.models.core import new_id
from study_agent.infrastructure.db.session import Database
from study_agent.providers.protocols import Clock

MAX_MEMORY_CONTENT_CHARS = 1_000
MAX_EXTRACTED_MEMORIES = 3


class LearnerMemoryType(StrEnum):
    PREFERENCE = "preference"
    CONFIRMED_MISCONCEPTION = "confirmed_misconception"
    LEARNING_GOAL = "learning_goal"


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    memory_type: LearnerMemoryType
    content: str


@dataclass(frozen=True, slots=True)
class LearnerMemorySnapshot:
    id: str
    course_id: str
    memory_type: LearnerMemoryType
    content: str
    confidence: float
    source_kind: str
    last_confirmed_at: datetime
    created_at: datetime
    updated_at: datetime


def _normalize_content(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).strip()


def _content_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _classify_explicit_statement(statement: str) -> LearnerMemoryType | None:
    if (
        ("我总是" in statement and any(word in statement for word in ("混淆", "搞混")))
        or ("我之前以为" in statement and any(word in statement for word in ("现在", "其实")))
        or "我容易把" in statement
    ):
        return LearnerMemoryType.CONFIRMED_MISCONCEPTION
    if any(marker in statement for marker in ("我的目标是", "我想学会", "我需要掌握")):
        return LearnerMemoryType.LEARNING_GOAL
    if any(
        marker in statement for marker in ("我喜欢", "我更喜欢", "我希望你", "请一直", "以后请")
    ):
        return LearnerMemoryType.PREFERENCE
    return None


def extract_explicit_memories(message: str) -> tuple[MemoryCandidate, ...]:
    """Extract only statements whose wording explicitly identifies learner-owned facts."""

    normalized = _normalize_content(message)
    if not normalized:
        return ()
    statements = re.split(r"[\u3002\uff01\uff1f;\n]+", normalized)
    candidates: list[MemoryCandidate] = []
    seen: set[tuple[LearnerMemoryType, str]] = set()
    for statement in statements:
        content = statement.strip(" ,.!?")[:MAX_MEMORY_CONTENT_CHARS].strip()
        memory_type = _classify_explicit_statement(content)
        if memory_type is None or not content:
            continue
        key = (memory_type, content)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(MemoryCandidate(memory_type=memory_type, content=content))
        if len(candidates) >= MAX_EXTRACTED_MEMORIES:
            break
    return tuple(candidates)


async def upsert_explicit_memories(
    session: AsyncSession,
    *,
    user_id: str,
    course_id: str,
    message: str,
    now: datetime,
    source_message_id: str | None = None,
    source_query_id: str | None = None,
) -> int:
    if source_message_id is not None and source_query_id is not None:
        raise ValueError("learner memory supports only one source reference")
    candidates = extract_explicit_memories(message)
    for candidate in candidates:
        statement = insert(LearnerMemoryModel).values(
            id=new_id(),
            user_id=user_id,
            course_id=course_id,
            memory_type=candidate.memory_type.value,
            content=candidate.content,
            content_sha256=_content_sha256(candidate.content),
            confidence=1.0,
            source_kind="explicit_user",
            source_message_id=source_message_id,
            source_query_id=source_query_id,
            last_confirmed_at=now,
            created_at=now,
            updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_learner_memories_content",
            set_={
                "confidence": 1.0,
                "source_kind": "explicit_user",
                "source_message_id": source_message_id,
                "source_query_id": source_query_id,
                "last_confirmed_at": now,
                "updated_at": now,
            },
        )
        await session.execute(statement)
    return len(candidates)


def _memory_snapshot(memory: LearnerMemoryModel) -> LearnerMemorySnapshot:
    return LearnerMemorySnapshot(
        id=memory.id,
        course_id=memory.course_id,
        memory_type=LearnerMemoryType(memory.memory_type),
        content=memory.content,
        confidence=memory.confidence,
        source_kind=memory.source_kind,
        last_confirmed_at=memory.last_confirmed_at,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


def _question_fragments(question: str) -> set[str]:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", question).casefold())
    return {compact[index : index + 2] for index in range(max(0, len(compact) - 1))}


async def relevant_memories_in_session(
    session: AsyncSession,
    *,
    user_id: str,
    course_id: str,
    question: str,
    limit: int = 4,
) -> tuple[LearnerMemorySnapshot, ...]:
    if limit <= 0:
        raise ValueError("memory limit must be positive")
    rows = (
        await session.scalars(
            select(LearnerMemoryModel)
            .where(
                LearnerMemoryModel.user_id == user_id,
                LearnerMemoryModel.course_id == course_id,
            )
            .order_by(LearnerMemoryModel.updated_at.desc(), LearnerMemoryModel.id.desc())
            .limit(40)
        )
    ).all()
    memories = tuple(_memory_snapshot(memory) for memory in rows)
    fragments = _question_fragments(question)

    def rank(memory: LearnerMemorySnapshot) -> tuple[int, float, datetime, str]:
        overlap = sum(fragment in memory.content.casefold() for fragment in fragments)
        base = {
            LearnerMemoryType.PREFERENCE: 3,
            LearnerMemoryType.LEARNING_GOAL: 2,
            LearnerMemoryType.CONFIRMED_MISCONCEPTION: 0,
        }[memory.memory_type]
        return (base + overlap, memory.confidence, memory.updated_at, memory.id)

    eligible = [
        memory
        for memory in memories
        if memory.memory_type is not LearnerMemoryType.CONFIRMED_MISCONCEPTION
        or any(fragment in memory.content.casefold() for fragment in fragments)
    ]
    return tuple(sorted(eligible, key=rank, reverse=True)[:limit])


class LearnerMemoryRepository:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    async def list(
        self,
        principal: Principal,
        course_id: str,
    ) -> tuple[LearnerMemorySnapshot, ...]:
        async with self._database.session(principal) as session:
            user_id = await self._course_user_id(session, principal, course_id)
            if user_id is None:
                raise LookupError("course is unavailable")
            memories = (
                await session.scalars(
                    select(LearnerMemoryModel)
                    .where(
                        LearnerMemoryModel.user_id == user_id,
                        LearnerMemoryModel.course_id == course_id,
                    )
                    .order_by(
                        LearnerMemoryModel.updated_at.desc(),
                        LearnerMemoryModel.id.desc(),
                    )
                )
            ).all()
            return tuple(_memory_snapshot(memory) for memory in memories)

    async def relevant(
        self,
        principal: Principal,
        course_id: str,
        question: str,
        *,
        limit: int = 4,
    ) -> tuple[LearnerMemorySnapshot, ...]:
        async with self._database.session(principal) as session:
            user_id = await self._course_user_id(session, principal, course_id)
            if user_id is None:
                raise LookupError("course is unavailable")
            return await relevant_memories_in_session(
                session,
                user_id=user_id,
                course_id=course_id,
                question=question,
                limit=limit,
            )

    async def create(
        self,
        principal: Principal,
        course_id: str,
        memory_type: LearnerMemoryType,
        content: str,
    ) -> LearnerMemorySnapshot:
        normalized = _normalize_content(content)
        if not normalized or len(normalized) > MAX_MEMORY_CONTENT_CHARS:
            raise ValueError("memory content is invalid")
        digest = _content_sha256(normalized)
        now = self._clock.now()
        async with self._database.session(principal) as session:
            user_id = await self._course_user_id(session, principal, course_id)
            if user_id is None:
                raise LookupError("course is unavailable")
            existing = await session.scalar(
                select(LearnerMemoryModel).where(
                    LearnerMemoryModel.user_id == user_id,
                    LearnerMemoryModel.course_id == course_id,
                    LearnerMemoryModel.memory_type == memory_type.value,
                    LearnerMemoryModel.content_sha256 == digest,
                )
            )
            if existing is not None:
                return _memory_snapshot(existing)
            memory = LearnerMemoryModel(
                id=new_id(),
                user_id=user_id,
                course_id=course_id,
                memory_type=memory_type.value,
                content=normalized,
                content_sha256=digest,
                confidence=1.0,
                source_kind="manual",
                last_confirmed_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(memory)
            await session.flush()
            return _memory_snapshot(memory)

    async def update(
        self,
        principal: Principal,
        memory_id: str,
        *,
        memory_type: LearnerMemoryType,
        content: str,
    ) -> LearnerMemorySnapshot | None:
        normalized = _normalize_content(content)
        if not normalized or len(normalized) > MAX_MEMORY_CONTENT_CHARS:
            raise ValueError("memory content is invalid")
        digest = _content_sha256(normalized)
        async with self._database.session(principal) as session:
            memory = await self._scoped_memory(session, principal, memory_id, lock=True)
            if memory is None:
                return None
            duplicate = await session.scalar(
                select(LearnerMemoryModel.id).where(
                    LearnerMemoryModel.user_id == memory.user_id,
                    LearnerMemoryModel.course_id == memory.course_id,
                    LearnerMemoryModel.memory_type == memory_type.value,
                    LearnerMemoryModel.content_sha256 == digest,
                    LearnerMemoryModel.id != memory.id,
                )
            )
            if duplicate is not None:
                raise ValueError("an equivalent memory already exists")
            now = self._clock.now()
            memory.memory_type = memory_type.value
            memory.content = normalized
            memory.content_sha256 = digest
            memory.confidence = 1.0
            memory.source_kind = "manual"
            memory.source_message_id = None
            memory.source_query_id = None
            memory.last_confirmed_at = now
            memory.updated_at = now
            await session.flush()
            return _memory_snapshot(memory)

    async def delete(self, principal: Principal, memory_id: str) -> bool:
        async with self._database.session(principal) as session:
            memory = await self._scoped_memory(session, principal, memory_id, lock=True)
            if memory is None:
                return False
            await session.delete(memory)
            return True

    @staticmethod
    async def _course_user_id(
        session: AsyncSession,
        principal: Principal,
        course_id: str,
    ) -> str | None:
        return cast(
            str | None,
            await session.scalar(
                select(CourseModel.user_id)
                .join(UserModel, UserModel.id == CourseModel.user_id)
                .where(
                    CourseModel.id == course_id,
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            ),
        )

    @staticmethod
    async def _scoped_memory(
        session: AsyncSession,
        principal: Principal,
        memory_id: str,
        *,
        lock: bool,
    ) -> LearnerMemoryModel | None:
        statement = (
            select(LearnerMemoryModel)
            .join(CourseModel, CourseModel.id == LearnerMemoryModel.course_id)
            .join(UserModel, UserModel.id == LearnerMemoryModel.user_id)
            .where(
                LearnerMemoryModel.id == memory_id,
                CourseModel.deleted_at.is_(None),
                UserModel.subject == principal.subject,
                UserModel.authentication_method == principal.authentication_method.value,
            )
        )
        if lock:
            statement = statement.with_for_update(of=LearnerMemoryModel)
        return cast(LearnerMemoryModel | None, await session.scalar(statement))


__all__ = [
    "LearnerMemoryRepository",
    "LearnerMemorySnapshot",
    "LearnerMemoryType",
    "MemoryCandidate",
    "extract_explicit_memories",
    "relevant_memories_in_session",
    "upsert_explicit_memories",
]
