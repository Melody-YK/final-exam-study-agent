"""Vendor-neutral provider and storage protocols.

Only contracts live here. Runtime adapters are constructed explicitly in a
later layer, which keeps deterministic fakes out of application registration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class EmbeddingContract:
    provider: str
    model: str
    dimensions: int
    supports_batch: bool = True

    def __post_init__(self) -> None:
        if self.dimensions <= 0:
            raise ValueError("embedding dimensions must be positive")


@dataclass(frozen=True, slots=True)
class Passage:
    id: str
    text: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConversationContextTurn:
    question: str
    answer_markdown: str | None = None


@dataclass(frozen=True, slots=True)
class EvidencePrompt:
    query: str
    passages: tuple[Passage, ...]
    conversation_context: tuple[ConversationContextTurn, ...] = ()
    response_schema_version: str = "1.0"


@dataclass(frozen=True, slots=True)
class StructuredAnswerDraft:
    payload: dict[str, object]
    model: str
    provider_response_id: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RerankScore:
    passage_id: str
    score: float


@dataclass(frozen=True, slots=True)
class ObjectScope:
    subject: str
    course_id: str
    purpose: str


@dataclass(frozen=True, slots=True)
class UploadTarget:
    object_key: str
    url: str
    expires_at: datetime
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    object_key: str
    size_bytes: int
    content_type: str
    sha256: str | None = None
    etag: str | None = None


@dataclass(frozen=True, slots=True)
class SignedUrl:
    url: str
    expires_at: datetime


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def probe(self) -> EmbeddingContract: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class ChatProvider(Protocol):
    async def answer(self, request: EvidencePrompt) -> StructuredAnswerDraft: ...


@runtime_checkable
class RerankProvider(Protocol):
    async def rerank(self, query: str, passages: list[Passage]) -> list[RerankScore]: ...


@runtime_checkable
class ObjectStorage(Protocol):
    async def create_upload(self, scope: ObjectScope) -> UploadTarget: ...

    async def head(self, object_key: str) -> ObjectMetadata: ...

    async def sign_read(self, object_key: str) -> SignedUrl: ...

    async def delete(self, object_key: str) -> None: ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...
