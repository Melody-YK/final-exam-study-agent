from typing import cast

import pytest

from study_agent.modules.learning.tutor import PracticeTutor, TutorEvidence, TutorGenerationError
from study_agent.providers.errors import ProviderErrorCode
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import (
    ChatProvider,
    JsonCompletionPrompt,
    LearnerMemoryContext,
    StructuredJsonDraft,
)
from study_contracts import (
    EvidenceReference,
    PracticeTutorIntent,
    PracticeTutorMode,
    PracticeTutorTurn,
    QuestionOption,
    QuestionType,
)


def _evidence() -> TutorEvidence:
    return TutorEvidence(
        reference=EvidenceReference(
            document_id="document-1",
            document_name="操作系统.pdf",
            revision_id="revision-1",
            chunk_id="chunk-1",
            content_sha256="a" * 64,
            locator={"kind": "page", "ordinal": 3},
            quote="进程是资源分配的基本单位。",
        ),
        text="进程是资源分配的基本单位, 线程是处理器调度的基本单位。",
    )


class _TutorProvider:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = payloads
        self.requests: list[JsonCompletionPrompt] = []

    async def complete_json(self, request: JsonCompletionPrompt) -> StructuredJsonDraft:
        self.requests.append(request)
        return StructuredJsonDraft(payload=self.payloads.pop(0), model="test-tutor")


def _tutor(provider: _TutorProvider) -> PracticeTutor:
    registry = ProviderRegistry(
        embedding_provider=None,
        chat_provider=cast(ChatProvider, provider),
        http_client=None,
        owns_http_client=False,
    )
    return PracticeTutor(registry, timeout_seconds=1)


@pytest.mark.asyncio
async def test_hint_mode_omits_answer_and_returns_authorized_evidence() -> None:
    provider = _TutorProvider(
        [{"answer_markdown": "先区分资源归属与执行调度分别描述谁。", "evidence_ids": ["E1"]}]
    )

    result = await _tutor(provider).answer(
        mode=PracticeTutorMode.HINT,
        question_type=QuestionType.SINGLE_CHOICE,
        prompt="进程是什么?",
        options=[
            QuestionOption(id="a", label="资源分配的基本单位"),
            QuestionOption(id="b", label="处理器调度的基本单位"),
        ],
        correct_answer="a",
        explanation="进程负责资源分配。",
        submitted_answer=None,
        message="给我一点提示",
        history=[],
        evidence=(_evidence(),),
    )

    assert result.mode is PracticeTutorMode.HINT
    assert result.evidence_refs[0].document_name == "操作系统.pdf"
    question_payload = provider.requests[0].payload["question"]
    assert isinstance(question_payload, dict)
    assert "correct_answer" not in question_payload
    assert "stored_explanation" not in question_payload


@pytest.mark.asyncio
async def test_current_example_request_overrides_an_old_answer_attempt() -> None:
    provider = _TutorProvider(
        [{"answer_markdown": "可以类比公司分配预算和员工执行任务。", "evidence_ids": ["E1"]}]
    )

    result = await _tutor(provider).answer(
        mode=PracticeTutorMode.HINT,
        question_type=QuestionType.SINGLE_CHOICE,
        prompt="进程是什么?",
        options=[
            QuestionOption(id="a", label="资源分配的基本单位"),
            QuestionOption(id="b", label="处理器调度的基本单位"),
        ],
        correct_answer="a",
        explanation="进程负责资源分配。",
        submitted_answer=None,
        message="你能给个例子吗?",
        history=[
            PracticeTutorTurn(role="user", content="123"),
            PracticeTutorTurn(role="assistant", content="先说明你的判断依据。"),
        ],
        evidence=(_evidence(),),
        conversation_summary="较早对话询问过进程定义。",
        learner_memories=(
            LearnerMemoryContext(memory_type="preference", content="我喜欢先看例子"),
        ),
    )

    assert result.intent is PracticeTutorIntent.EXAMPLE
    assert provider.requests[0].payload["current_intent"] == "example"
    assert provider.requests[0].payload["current_message"] == "你能给个例子吗?"
    assert provider.requests[0].payload["conversation_history"] == [
        {"role": "user", "content": "123"},
        {"role": "assistant", "content": "先说明你的判断依据。"},
    ]
    assert provider.requests[0].payload["conversation_summary"] == "较早对话询问过进程定义。"
    assert provider.requests[0].payload["learner_memories"] == [
        {"memory_type": "preference", "content": "我喜欢先看例子"}
    ]


@pytest.mark.asyncio
async def test_hint_mode_retries_when_provider_reveals_correct_option() -> None:
    provider = _TutorProvider(
        [
            {"answer_markdown": "答案是 A。", "evidence_ids": ["E1"]},
            {"answer_markdown": "回到定义, 比较题干强调的是资源还是调度。", "evidence_ids": ["E1"]},
        ]
    )

    result = await _tutor(provider).answer(
        mode=PracticeTutorMode.HINT,
        question_type=QuestionType.SINGLE_CHOICE,
        prompt="进程是什么?",
        options=[
            QuestionOption(id="a", label="资源分配的基本单位"),
            QuestionOption(id="b", label="处理器调度的基本单位"),
        ],
        correct_answer="a",
        explanation="进程负责资源分配。",
        submitted_answer=None,
        message="答案是什么",
        history=[],
        evidence=(_evidence(),),
    )

    assert len(provider.requests) == 2
    assert result.answer_markdown.startswith("回到定义")


@pytest.mark.asyncio
async def test_calculation_hint_has_no_options_and_retries_a_revealed_result() -> None:
    provider = _TutorProvider(
        [
            {"answer_markdown": "因此结果为3。", "evidence_ids": ["E1"]},
            {
                "answer_markdown": "先用逻辑地址除以页面大小, 再分别看商和余数。",
                "evidence_ids": ["E1"],
            },
        ]
    )

    result = await _tutor(provider).answer(
        mode=PracticeTutorMode.HINT,
        question_type=QuestionType.CALCULATION,
        prompt="页面大小为128字节, 逻辑地址为390, 求页号和页内偏移。",
        options=[],
        correct_answer="页号为3, 页内偏移为6字节。",
        explanation="390 = 3 * 128 + 6。",
        submitted_answer=None,
        message="给我一个思路",
        history=[],
        evidence=(_evidence(),),
    )

    assert result.answer_markdown.startswith("先用逻辑地址")
    assert len(provider.requests) == 2
    question_payload = provider.requests[1].payload["question"]
    assert isinstance(question_payload, dict)
    assert "options" not in question_payload
    assert "correct_answer" not in question_payload


@pytest.mark.asyncio
async def test_tutor_rejects_evidence_outside_the_question() -> None:
    provider = _TutorProvider(
        [
            {"answer_markdown": "依据另一条材料。", "evidence_ids": ["E9"]},
            {"answer_markdown": "仍引用另一条材料。", "evidence_ids": ["E9"]},
        ]
    )

    with pytest.raises(TutorGenerationError) as caught:
        await _tutor(provider).answer(
            mode=PracticeTutorMode.REVIEW,
            question_type=QuestionType.SINGLE_CHOICE,
            prompt="进程是什么?",
            options=[
                QuestionOption(id="a", label="资源分配的基本单位"),
                QuestionOption(id="b", label="处理器调度的基本单位"),
            ],
            correct_answer="a",
            explanation="进程负责资源分配。",
            submitted_answer="b",
            message="为什么错了",
            history=[],
            evidence=(_evidence(),),
        )

    assert caught.value.code is ProviderErrorCode.BAD_RESPONSE
