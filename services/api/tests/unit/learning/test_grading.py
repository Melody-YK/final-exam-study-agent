# ruff: noqa: RUF001
from typing import cast

import pytest

from study_agent.modules.learning.grading import (
    ConstructedAnswerGrader,
    build_constructed_grading_prompt,
    normalize_constructed_answer,
)
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import ChatProvider, JsonCompletionPrompt, StructuredJsonDraft
from study_contracts import QuestionType


class _GradingProvider:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = payloads
        self.requests: list[JsonCompletionPrompt] = []

    async def complete_json(self, request: JsonCompletionPrompt) -> StructuredJsonDraft:
        self.requests.append(request)
        return StructuredJsonDraft(payload=self.payloads.pop(0), model="test-grader")


def _grader(provider: _GradingProvider) -> ConstructedAnswerGrader:
    registry = ProviderRegistry(
        embedding_provider=None,
        chat_provider=cast(ChatProvider, provider),
        http_client=None,
        owns_http_client=False,
    )
    return ConstructedAnswerGrader(registry, timeout_seconds=1)


def test_constructed_answer_normalization_preserves_lines() -> None:
    assert normalize_constructed_answer(" 390 = 3 * 128 + 6  \r\n  页内偏移为 6  ") == (
        "390 = 3 * 128 + 6\n页内偏移为 6"
    )


def test_constructed_grading_prompt_is_semantic_and_evidence_bound() -> None:
    prompt = build_constructed_grading_prompt(
        question_type=QuestionType.CALCULATION,
        prompt="页面大小为128字节，逻辑地址为390，求页号和页内偏移。",
        reference_answer="页号3，页内偏移6字节。",
        solution="390 = 3×128 + 6。",
        submitted_answer="页号3，偏移6。",
        evidence_texts=("逻辑地址除以页面大小得到页号和页内偏移。",),
    )

    assert prompt.response_schema_version == "constructed-answer-grade-1.0"
    assert "不能只按字符串是否相同判分" in prompt.system_prompt
    assert prompt.payload["evidence"] == [
        {"id": "E1", "text": "逻辑地址除以页面大小得到页号和页内偏移。"}
    ]


@pytest.mark.asyncio
async def test_constructed_grader_retries_invalid_output_and_returns_feedback() -> None:
    provider = _GradingProvider(
        [
            {"answer": "correct"},
            {"verdict": "correct", "feedback": "列式、页号和页内偏移均正确。"},
        ]
    )

    result = await _grader(provider).grade(
        question_type=QuestionType.CALCULATION,
        prompt="页面大小为128字节，逻辑地址为390，求页号和页内偏移。",
        reference_answer="页号3，页内偏移6字节。",
        solution="390 = 3×128 + 6。",
        submitted_answer="  页号为3，偏移为6字节。 ",
        evidence_texts=("逻辑地址除以页面大小得到页号和页内偏移。",),
    )

    assert result.correct is True
    assert result.score == 1
    assert result.submitted_answer == "页号为3,偏移为6字节。"
    assert result.feedback == "列式、页号和页内偏移均正确。"
    assert len(provider.requests) == 2
    assert provider.requests[1].payload["retry_reason"] == "invalid_output"
