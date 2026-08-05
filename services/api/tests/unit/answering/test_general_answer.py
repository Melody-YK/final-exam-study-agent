import pytest

from study_agent.modules.answering.service import GeneralKnowledgeAnswerService
from study_agent.providers.protocols import (
    EvidencePrompt,
    JsonCompletionPrompt,
    StructuredAnswerDraft,
    StructuredJsonDraft,
)
from study_contracts import AnswerBasis, AnswerStatus


class _Provider:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests: list[JsonCompletionPrompt] = []

    async def answer(self, _request: EvidencePrompt) -> StructuredAnswerDraft:
        raise AssertionError("general fallback must use the JSON completion contract")

    async def complete_json(self, request: JsonCompletionPrompt) -> StructuredJsonDraft:
        self.requests.append(request)
        return StructuredJsonDraft(payload=self.payload, model="general-test")


@pytest.mark.asyncio
async def test_general_fallback_returns_marked_answer_without_citations() -> None:
    provider = _Provider(
        {
            "can_answer": True,
            "answer_markdown": "可以把进程理解成一个运行中的程序实例。",
            "reason": None,
        }
    )

    result = await GeneralKnowledgeAnswerService(lambda: provider).answer(
        query_id="query-1",
        question="什么是进程?",
        diagnostic="no_candidates",
    )

    assert result.answer is not None
    assert result.answer.status is AnswerStatus.ANSWERED
    assert result.answer.answer_basis is AnswerBasis.AI_GENERAL_KNOWLEDGE
    assert result.answer.claims == []
    assert result.answer.citations == []
    assert provider.requests[0].payload["retrieval_diagnostic"] == "no_candidates"


@pytest.mark.asyncio
async def test_course_specific_question_still_requires_a_source() -> None:
    provider = _Provider(
        {
            "can_answer": True,
            "answer_markdown": "不应调用模型",
            "reason": None,
        }
    )

    result = await GeneralKnowledgeAnswerService(lambda: provider).answer(
        query_id="query-2",
        question="那具体要求呢?",
        diagnostic="low_relevance",
        standalone_question="老师对这门课考试的具体要求是什么?",
    )

    assert result.answer is not None
    assert result.answer.status is AnswerStatus.ABSTAINED
    assert result.answer.refusal is not None
    assert result.answer.refusal.code == "COURSE_SOURCE_REQUIRED"
    assert provider.requests == []


@pytest.mark.asyncio
async def test_invalid_general_provider_payload_becomes_a_retryable_failure() -> None:
    provider = _Provider({"can_answer": True, "answer_markdown": ""})

    result = await GeneralKnowledgeAnswerService(lambda: provider).answer(
        query_id="query-3",
        question="为什么线程切换通常更轻量?",
        diagnostic="low_relevance",
    )

    assert result.answer is None
    assert result.failure_code == "PROVIDER_BAD_RESPONSE"
