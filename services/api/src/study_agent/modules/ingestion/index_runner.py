"""Recoverable single-concurrency orchestration for preview indexing."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Protocol

from study_agent.modules.retrieval.bm25_index import BuiltLexicalIndex, LexicalDocument
from study_agent.providers.errors import ProviderError, ProviderErrorCode
from study_agent.providers.protocols import EmbeddingContract, EmbeddingProvider


@dataclass(frozen=True, slots=True)
class IndexWork:
    job_id: str
    attempt: int
    user_id: str
    course_id: str
    document_id: str
    revision_id: str
    requested_provider: str
    requested_model: str
    contract_version: str


@dataclass(frozen=True, slots=True)
class IndexChunk:
    id: str
    text: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class EmbeddingModelIdentity:
    id: str
    provider: str
    model: str
    dimensions: int
    distance: str
    contract_version: str


class IndexRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    BLOCKED_PROVIDER = "index_blocked_provider"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IndexRunOutcome:
    job_id: str
    status: IndexRunStatus
    code: str | None = None


class IndexRepository(Protocol):
    async def claim(self) -> IndexWork | None: ...

    async def block_provider(self, work: IndexWork, code: str) -> None: ...

    async def fail(self, work: IndexWork, code: str) -> None: ...

    async def ensure_model(
        self,
        work: IndexWork,
        contract: EmbeddingContract,
    ) -> EmbeddingModelIdentity: ...

    async def chunks(self, work: IndexWork) -> list[IndexChunk]: ...

    async def replace_embeddings(
        self,
        work: IndexWork,
        model: EmbeddingModelIdentity,
        chunks: Sequence[IndexChunk],
        vectors: Sequence[Sequence[float]],
    ) -> None: ...

    async def lexical_documents(self, work: IndexWork) -> list[LexicalDocument]: ...

    async def register_lexical(
        self,
        work: IndexWork,
        built: BuiltLexicalIndex,
    ) -> str: ...

    async def activate(
        self,
        work: IndexWork,
        model: EmbeddingModelIdentity,
        manifest_id: str,
    ) -> None: ...

    async def resume_provider_blocked(self) -> int: ...


class LexicalIndexBuilder(Protocol):
    def build(
        self,
        documents: Sequence[LexicalDocument],
        *,
        version_id: str,
    ) -> BuiltLexicalIndex: ...


class IndexRunner:
    """Runs one job at a time; all external capabilities are explicit injections."""

    def __init__(
        self,
        repository: IndexRepository,
        lexical_store: LexicalIndexBuilder,
        *,
        provider: EmbeddingProvider | None,
        batch_size: int = 64,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("embedding batch size must be positive")
        self._repository = repository
        self._lexical_store = lexical_store
        self._provider = provider
        self._batch_size = batch_size
        self._execution_lock = asyncio.Lock()

    async def resume_provider_blocked(self) -> int:
        if self._provider is None:
            return 0
        return await self._repository.resume_provider_blocked()

    async def run_once(self) -> IndexRunOutcome | None:
        async with self._execution_lock:
            work = await self._repository.claim()
            if work is None:
                return None
            if self._provider is None:
                return await self._block_provider(work, ProviderErrorCode.NOT_CONFIGURED.value)
            try:
                contract = await self._provider.probe()
                self._validate_contract(work, contract)
                model = await self._repository.ensure_model(work, contract)
                chunks = await self._repository.chunks(work)
                if not chunks:
                    raise ValueError("preview revision has no chunks")
                vectors: list[list[float]] = []
                for start in range(0, len(chunks), self._batch_size):
                    batch = chunks[start : start + self._batch_size]
                    embedded = await self._provider.embed_documents([item.text for item in batch])
                    self._validate_vectors(embedded, len(batch), contract.dimensions)
                    vectors.extend(embedded)
                await self._repository.replace_embeddings(work, model, chunks, vectors)
                lexical_documents = await self._repository.lexical_documents(work)
                built = await asyncio.to_thread(
                    self._lexical_store.build,
                    lexical_documents,
                    version_id=f"{work.job_id}-attempt-{work.attempt}",
                )
                manifest_id = await self._repository.register_lexical(work, built)
                await self._repository.activate(work, model, manifest_id)
                return IndexRunOutcome(work.job_id, IndexRunStatus.SUCCEEDED)
            except ProviderError as exc:
                return await self._block_provider(work, exc.code.value)
            except (OSError, RuntimeError, ValueError) as exc:
                code = self._failure_code(exc)
                await self._repository.fail(work, code)
                return IndexRunOutcome(work.job_id, IndexRunStatus.FAILED, code)

    async def _block_provider(self, work: IndexWork, code: str) -> IndexRunOutcome:
        await self._repository.block_provider(work, code)
        return IndexRunOutcome(work.job_id, IndexRunStatus.BLOCKED_PROVIDER, code)

    @staticmethod
    def _validate_contract(work: IndexWork, contract: EmbeddingContract) -> None:
        if contract.provider != work.requested_provider:
            raise ProviderError(
                ProviderErrorCode.EMBEDDING_MODEL_CHANGED,
                provider=contract.provider,
                retryable=False,
            )
        if contract.model != work.requested_model:
            raise ProviderError(
                ProviderErrorCode.EMBEDDING_MODEL_CHANGED,
                provider=contract.provider,
                retryable=False,
            )

    @staticmethod
    def _validate_vectors(
        vectors: Sequence[Sequence[float]],
        expected_count: int,
        expected_dimensions: int,
    ) -> None:
        if len(vectors) != expected_count:
            raise ValueError("embedding response count mismatch")
        if any(len(vector) != expected_dimensions for vector in vectors):
            raise ProviderError(
                ProviderErrorCode.EMBEDDING_DIMENSION_CHANGED,
                provider="embedding",
                retryable=False,
            )
        if any(not isfinite(float(value)) for vector in vectors for value in vector):
            raise ValueError("embedding response contains non-finite values")

    @staticmethod
    def _failure_code(exc: BaseException) -> str:
        if isinstance(exc, ValueError) and "no chunks" in str(exc):
            return "INDEX_EMPTY_REVISION"
        return "INDEX_UNAVAILABLE"


__all__ = [
    "BuiltLexicalIndex",
    "EmbeddingModelIdentity",
    "IndexChunk",
    "IndexRepository",
    "IndexRunOutcome",
    "IndexRunStatus",
    "IndexRunner",
    "IndexWork",
    "LexicalDocument",
]
