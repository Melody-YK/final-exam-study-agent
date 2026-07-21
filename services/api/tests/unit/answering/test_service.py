import pytest

from study_agent.modules.answering.service import TrustedAnswerService
from study_agent.modules.answering.types import AuthorizedEvidence
from study_agent.providers.errors import ProviderError, ProviderErrorCode
from study_agent.providers.protocols import EvidencePrompt, StructuredAnswerDraft
from study_contracts import AnswerStatus, Evidence, SourceLocator


def _candidate() -> AuthorizedEvidence:
    return AuthorizedEvidence(
        evidence=Evidence(
            id="evidence-1",
            course_id="course-1",
            document_id="document-1",
            revision_id="revision-1",
            chunk_id="chunk-1",
            text="进程是资源分配的基本单位。",
            content_sha256="c" * 64,
            locator=SourceLocator(kind="page", ordinal=2),
        ),
        document_name="chapter.pdf",
        score=0.9,
        document_deletion_epoch=1,
        provenance=("pdf-native@1.0",),
    )


def _valid_payload() -> dict[str, object]:
    return {
        "status": "answered",
        "answer_markdown": "进程是资源分配的基本单位。",
        "claims": [
            {"id": "claim-1", "text": "进程是资源分配的基本单位。", "citation_ids": ["evidence-1"]}
        ],
        "citations": [
            {
                "id": "evidence-1",
                "document_id": "document-1",
                "revision_id": "revision-1",
                "chunk_id": "chunk-1",
                "document_name": "chapter.pdf",
                "locator": {"kind": "page", "ordinal": 2},
                "quote": "进程是资源分配的基本单位",
                "bounding_boxes": [],
            }
        ],
    }


class RecordingProvider:
    def __init__(self, outcome: StructuredAnswerDraft | Exception) -> None:
        self.outcome = outcome
        self.requests: list[EvidencePrompt] = []

    async def answer(self, request: EvidencePrompt) -> StructuredAnswerDraft:
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


async def _current() -> bool:
    return True


@pytest.mark.asyncio
async def test_no_active_index_abstains_without_resolving_or_calling_provider() -> None:
    resolutions = 0

    def provider_factory() -> RecordingProvider:
        nonlocal resolutions
        resolutions += 1
        raise AssertionError("provider must not be resolved")

    result = await TrustedAnswerService(provider_factory).answer(
        query_id="query-1",
        question="什么是进程?",
        active_index=False,
        candidates=(),
        sources_are_current=_current,
    )

    assert resolutions == 0
    assert result.answer is not None
    assert result.answer.status is AnswerStatus.ABSTAINED
    assert result.answer.refusal is not None
    assert result.answer.refusal.code == "INDEX_UNAVAILABLE"


@pytest.mark.asyncio
async def test_answer_is_returned_only_after_post_provider_source_recheck() -> None:
    provider = RecordingProvider(StructuredAnswerDraft(payload=_valid_payload(), model="test-chat"))
    checks = 0

    async def changed() -> bool:
        nonlocal checks
        checks += 1
        return False

    result = await TrustedAnswerService(lambda: provider).answer(
        query_id="query-2",
        question="什么是进程?",
        active_index=True,
        candidates=(_candidate(),),
        sources_are_current=changed,
    )

    assert len(provider.requests) == 1
    assert checks == 1
    assert result.answer is not None
    assert result.answer.status is AnswerStatus.ABSTAINED
    assert result.answer.refusal is not None
    assert result.answer.refusal.code == "SOURCE_CHANGED"


@pytest.mark.asyncio
async def test_provider_timeout_and_invalid_payload_are_normalized_without_answer_text() -> None:
    timeout = RecordingProvider(
        ProviderError(
            ProviderErrorCode.TIMEOUT,
            provider="test-chat",
            retryable=True,
        )
    )
    invalid = RecordingProvider(
        StructuredAnswerDraft(payload={"answer_markdown": "unsupported"}, model="test-chat")
    )

    timeout_result = await TrustedAnswerService(lambda: timeout).answer(
        query_id="query-3",
        question="问题",
        active_index=True,
        candidates=(_candidate(),),
        sources_are_current=_current,
    )
    invalid_result = await TrustedAnswerService(lambda: invalid).answer(
        query_id="query-4",
        question="问题",
        active_index=True,
        candidates=(_candidate(),),
        sources_are_current=_current,
    )

    assert timeout_result.answer is None
    assert timeout_result.failure_code == "PROVIDER_TIMEOUT"
    assert invalid_result.answer is None
    assert invalid_result.failure_code == "PROVIDER_BAD_RESPONSE"


@pytest.mark.asyncio
async def test_invalid_citation_exhaustion_abstains_instead_of_using_external_knowledge() -> None:
    payload = _valid_payload()
    payload["citations"][0]["chunk_id"] = "forged"  # type: ignore[index]
    provider = RecordingProvider(StructuredAnswerDraft(payload=payload, model="test-chat"))

    result = await TrustedAnswerService(
        lambda: provider,
        max_validation_attempts=2,
    ).answer(
        query_id="query-5",
        question="问题",
        active_index=True,
        candidates=(_candidate(),),
        sources_are_current=_current,
    )

    assert len(provider.requests) == 2
    assert result.answer is not None
    assert result.answer.status is AnswerStatus.ABSTAINED
    assert result.answer.refusal is not None
    assert result.answer.refusal.code == "INVALID_CITATION"
    assert result.answer.answer_markdown == ""


@pytest.mark.asyncio
async def test_provider_factory_error_is_normalized() -> None:
    def not_configured() -> RecordingProvider:
        raise ProviderError(
            ProviderErrorCode.NOT_CONFIGURED,
            provider="chat",
            retryable=False,
        )

    result = await TrustedAnswerService(not_configured).answer(
        query_id="query-6",
        question="问题",
        active_index=True,
        candidates=(_candidate(),),
        sources_are_current=_current,
    )

    assert result.answer is None
    assert result.failure_code == "PROVIDER_NOT_CONFIGURED"
