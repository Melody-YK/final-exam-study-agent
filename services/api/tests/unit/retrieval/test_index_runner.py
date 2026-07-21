import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from study_agent.modules.ingestion.index_runner import (
    BuiltLexicalIndex,
    EmbeddingModelIdentity,
    IndexChunk,
    IndexRunner,
    IndexRunStatus,
    IndexWork,
    LexicalDocument,
)
from study_agent.providers.protocols import EmbeddingContract


class TestEmbeddingProvider:
    __test__ = False

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.gate = gate
        self.active = 0
        self.max_active = 0

    async def probe(self) -> EmbeddingContract:
        return EmbeddingContract(provider="test", model="tiny", dimensions=2)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.gate is not None:
            await self.gate.wait()
        self.active -= 1
        return [[float(index + 1), 0.5] for index, _text in enumerate(texts)]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.5]


def _work(job_id: str = "job-1") -> IndexWork:
    return IndexWork(
        job_id=job_id,
        attempt=1,
        user_id="user-1",
        course_id="course-1",
        document_id="document-1",
        revision_id="revision-1",
        requested_provider="test",
        requested_model="tiny",
        contract_version="1",
    )


class MemoryRepository:
    def __init__(self, works: Sequence[IndexWork]) -> None:
        self.works = list(works)
        self.blocked: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str]] = []
        self.embeddings: dict[str, list[list[float]]] = {}
        self.activated: list[str] = []
        self.resumed = 0

    async def claim(self) -> IndexWork | None:
        return self.works.pop(0) if self.works else None

    async def block_provider(self, work: IndexWork, code: str) -> None:
        self.blocked.append((work.job_id, code))

    async def fail(self, work: IndexWork, code: str) -> None:
        self.failed.append((work.job_id, code))

    async def ensure_model(
        self, work: IndexWork, contract: EmbeddingContract
    ) -> EmbeddingModelIdentity:
        return EmbeddingModelIdentity(
            id="model-1",
            provider=contract.provider,
            model=contract.model,
            dimensions=contract.dimensions,
            distance="cosine",
            contract_version=work.contract_version,
        )

    async def chunks(self, work: IndexWork) -> list[IndexChunk]:
        return [
            IndexChunk(id="a", text="进程", content_sha256="a" * 64),
            IndexChunk(id="b", text="线程", content_sha256="b" * 64),
        ]

    async def replace_embeddings(
        self,
        work: IndexWork,
        model: EmbeddingModelIdentity,
        chunks: Sequence[IndexChunk],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        assert model.dimensions == 2
        assert [chunk.id for chunk in chunks] == ["a", "b"]
        self.embeddings[work.job_id] = [list(vector) for vector in vectors]

    async def lexical_documents(self, work: IndexWork) -> list[LexicalDocument]:
        return [
            LexicalDocument(
                chunk_id="a",
                user_id=work.user_id,
                course_id=work.course_id,
                document_id=work.document_id,
                revision_id=work.revision_id,
                text="进程",
                content_sha256="a" * 64,
            )
        ]

    async def register_lexical(self, work: IndexWork, built: BuiltLexicalIndex) -> str:
        assert built.document_set_hash
        return f"manifest-{work.job_id}"

    async def activate(
        self,
        work: IndexWork,
        model: EmbeddingModelIdentity,
        manifest_id: str,
    ) -> None:
        assert model.id == "model-1"
        assert manifest_id == f"manifest-{work.job_id}"
        self.activated.append(work.job_id)

    async def resume_provider_blocked(self) -> int:
        self.resumed += 1
        return 1


class MemoryLexicalStore:
    def build(self, documents: Sequence[LexicalDocument], *, version_id: str) -> BuiltLexicalIndex:
        assert documents
        return BuiltLexicalIndex(
            version_id=version_id,
            storage_path=Path(f"/tmp/{version_id}"),
            manifest_hash="c" * 64,
            document_set_hash="d" * 64,
            tokenizer_version="jieba-test",
            dictionary_hash="e" * 64,
            chunk_count=len(documents),
            document_ids=tuple(sorted({item.document_id for item in documents})),
            revision_ids=tuple(sorted({item.revision_id for item in documents})),
        )


@pytest.mark.asyncio
async def test_missing_provider_persists_explicit_blocked_state() -> None:
    repository = MemoryRepository([_work()])
    runner = IndexRunner(repository, MemoryLexicalStore(), provider=None, batch_size=2)

    outcome = await runner.run_once()

    assert outcome is not None
    assert outcome.status is IndexRunStatus.BLOCKED_PROVIDER
    assert repository.blocked == [("job-1", "PROVIDER_NOT_CONFIGURED")]
    assert repository.embeddings == {}


@pytest.mark.asyncio
async def test_configured_provider_can_explicitly_resume_without_reparse() -> None:
    repository = MemoryRepository([_work()])
    runner = IndexRunner(
        repository,
        MemoryLexicalStore(),
        provider=TestEmbeddingProvider(),
        batch_size=1,
    )

    assert await runner.resume_provider_blocked() == 1
    outcome = await runner.run_once()

    assert outcome is not None
    assert outcome.status is IndexRunStatus.SUCCEEDED
    assert repository.embeddings["job-1"] == [[1.0, 0.5], [1.0, 0.5]]
    assert repository.activated == ["job-1"]


@pytest.mark.asyncio
async def test_one_runner_never_executes_two_jobs_concurrently() -> None:
    gate = asyncio.Event()
    provider = TestEmbeddingProvider(gate)
    repository = MemoryRepository([_work("job-1"), _work("job-2")])
    runner = IndexRunner(repository, MemoryLexicalStore(), provider=provider, batch_size=2)

    first = asyncio.create_task(runner.run_once())
    second = asyncio.create_task(runner.run_once())
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(first, second)

    assert provider.max_active == 1
    assert repository.activated == ["job-1", "job-2"]
