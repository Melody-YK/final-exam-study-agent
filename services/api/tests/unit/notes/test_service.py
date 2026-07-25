from datetime import UTC, datetime

import pytest

from study_agent.modules.answering.retrieval import RetrievedEvidence
from study_agent.modules.answering.types import AuthorizedEvidence
from study_agent.modules.jobs.clock import SystemClock
from study_agent.modules.notes.service import NoteGenerationError, NoteService, NoteSnapshot
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import EvidencePrompt, StructuredAnswerDraft
from study_contracts import Evidence, SourceLocator


class FakeEvidence:
    def __init__(self, result: RetrievedEvidence) -> None:
        self.result = result

    async def retrieve(
        self,
        _principal: object,
        _course_id: str,
        _question: str,
        *,
        document_ids: frozenset[str] | None,
    ) -> RetrievedEvidence:
        assert document_ids is None
        return self.result

    async def sources_are_current(self, *_args: object) -> bool:
        return True


class RecordingProvider:
    def __init__(self) -> None:
        self.requests: list[EvidencePrompt] = []

    async def answer(self, request: EvidencePrompt) -> StructuredAnswerDraft:
        self.requests.append(request)
        passage = request.passages[0]
        metadata = passage.metadata
        return StructuredAnswerDraft(
            model="test-chat",
            payload={
                "status": "answered",
                "answer_markdown": "## 进程\n\n进程是资源分配的基本单位。",
                "claims": [
                    {
                        "id": "claim-1",
                        "text": "进程是资源分配的基本单位。",
                        "citation_ids": [passage.id],
                    }
                ],
                "citations": [
                    {
                        "id": passage.id,
                        "document_id": metadata["document_id"],
                        "revision_id": metadata["revision_id"],
                        "chunk_id": metadata["chunk_id"],
                        "document_name": metadata["document_name"],
                        "locator": metadata["locator"],
                        "quote": "进程是资源分配的基本单位",
                        "bounding_boxes": metadata["bounding_boxes"],
                    }
                ],
            },
        )


class RecordingRepository:
    def __init__(self) -> None:
        self.answer_markdown: str | None = None

    async def create_generated(
        self,
        _principal: object,
        course_id: str,
        section_path: tuple[str, ...],
        title: str,
        answer: object,
        _retrieved: RetrievedEvidence,
    ) -> NoteSnapshot:
        self.answer_markdown = answer.answer_markdown  # type: ignore[attr-defined]
        now = datetime.now(UTC)
        return NoteSnapshot(
            id="note-1",
            course_id=course_id,
            section_path=section_path,
            title=title,
            body_markdown=self.answer_markdown,
            version=1,
            generation=1,
            generated_by_model=True,
            status="ready",
            origin_batch_id=None,
            sources=(),
            created_at=now,
            updated_at=now,
        )


def _candidate() -> AuthorizedEvidence:
    return AuthorizedEvidence(
        evidence=Evidence(
            id="evidence-1",
            course_id="course-1",
            document_id="document-1",
            revision_id="revision-1",
            chunk_id="chunk-1",
            text="进程是资源分配的基本单位。",
            content_sha256="f" * 64,
            locator=SourceLocator(kind="page", ordinal=1),
        ),
        document_name="chapter.pdf",
        score=0.9,
        document_deletion_epoch=0,
        provenance=("pdf-native@1",),
    )


def _registry(provider: RecordingProvider) -> ProviderRegistry:
    return ProviderRegistry(
        embedding_provider=None,
        chat_provider=provider,
        http_client=None,
        owns_http_client=False,
    )


@pytest.mark.asyncio
async def test_note_generation_reuses_trusted_answering_and_evidence_sources() -> None:
    provider = RecordingProvider()
    repository = RecordingRepository()
    evidence = FakeEvidence(
        RetrievedEvidence(
            active_index=True,
            candidates=(_candidate(),),
            active_lexical_index_id="manifest-1",
        )
    )
    service = NoteService(
        repository,  # type: ignore[arg-type]
        evidence,  # type: ignore[arg-type]
        _registry(provider),
        SystemClock(),
        timeout_seconds=1,
    )

    note = await service.create(
        object(),  # type: ignore[arg-type]
        "course-1",
        ("进程管理",),
        "进程",
    )

    assert note.body_markdown.startswith("## 进程")
    assert repository.answer_markdown == note.body_markdown
    assert len(provider.requests) == 1
    assert provider.requests[0].passages[0].metadata["trust_boundary"] == "untrusted_evidence"


@pytest.mark.asyncio
async def test_note_generation_without_active_index_never_calls_chat() -> None:
    provider = RecordingProvider()
    repository = RecordingRepository()
    service = NoteService(
        repository,  # type: ignore[arg-type]
        FakeEvidence(RetrievedEvidence(active_index=False, candidates=())),  # type: ignore[arg-type]
        _registry(provider),
        SystemClock(),
        timeout_seconds=1,
    )

    with pytest.raises(NoteGenerationError, match="INDEX_UNAVAILABLE"):
        await service.create(
            object(),  # type: ignore[arg-type]
            "course-1",
            ("进程管理",),
            "进程",
        )

    assert provider.requests == []
