import asyncio

import pytest

from study_agent.modules.answering.planning import (
    CourseQueryPlanner,
    QueryIntent,
    fallback_query_plan,
)
from study_agent.providers.protocols import (
    ConversationContextTurn,
    EvidencePrompt,
    JsonCompletionPrompt,
    StructuredAnswerDraft,
    StructuredJsonDraft,
)


class PlanningProvider:
    def __init__(self, payload: dict[str, object], *, delay: float = 0.0) -> None:
        self.payload = payload
        self.delay = delay
        self.requests: list[JsonCompletionPrompt] = []

    async def answer(self, _request: EvidencePrompt) -> StructuredAnswerDraft:
        raise AssertionError("answer should not be called while planning")

    async def complete_json(self, request: JsonCompletionPrompt) -> StructuredJsonDraft:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        return StructuredJsonDraft(payload=self.payload, model="planner-test")


def _context(question: str = "什么是进程?") -> tuple[ConversationContextTurn, ...]:
    return (
        ConversationContextTurn(
            question=question,
            answer_markdown="历史回答不应成为检索证据。",
        ),
    )


def test_fallback_resolves_pronoun_and_preserves_current_comparison() -> None:
    plan = fallback_query_plan("它和线程有什么区别?", _context())

    assert plan.intent is QueryIntent.COMPARISON
    assert plan.standalone_question == "进程和线程有什么区别?"
    assert plan.search_queries[0] == plan.standalone_question
    assert "历史回答不应成为检索证据" not in " ".join(plan.search_queries)


def test_fallback_does_not_force_self_contained_new_question_onto_old_subject() -> None:
    plan = fallback_query_plan("什么是内存分页?", _context())

    assert plan.intent is QueryIntent.NEW_QUESTION
    assert plan.standalone_question == "什么是内存分页?"
    assert "进程" not in plan.standalone_question


@pytest.mark.asyncio
async def test_provider_plan_uses_strict_bounded_queries_and_current_question() -> None:
    provider = PlanningProvider(
        {
            "intent": "comparison",
            "standalone_question": "进程和线程有什么区别?",
            "search_queries": ["进程 线程 区别", "线程与进程 资源 调度"],
        }
    )
    planner = CourseQueryPlanner(lambda: provider, timeout_seconds=1.0)

    plan = await planner.plan("它和线程有什么区别?", _context())

    assert plan.provider_planned
    assert plan.intent is QueryIntent.COMPARISON
    assert plan.search_queries == (
        "进程和线程有什么区别?",
        "进程 线程 区别",
        "线程与进程 资源 调度",
    )
    assert provider.requests[0].payload["current_question"] == "它和线程有什么区别?"


@pytest.mark.asyncio
async def test_invalid_or_timed_out_provider_plan_falls_back_deterministically() -> None:
    invalid = PlanningProvider(
        {
            "intent": "comparison",
            "standalone_question": "进程和线程有什么区别?",
            "search_queries": [],
        }
    )
    delayed = PlanningProvider({}, delay=0.02)

    invalid_plan = await CourseQueryPlanner(lambda: invalid, timeout_seconds=1.0).plan(
        "它和线程有什么区别?",
        _context(),
    )
    timed_out_plan = await CourseQueryPlanner(lambda: delayed, timeout_seconds=0.001).plan(
        "它和线程有什么区别?",
        _context(),
    )

    assert not invalid_plan.provider_planned
    assert not timed_out_plan.provider_planned
    assert invalid_plan.standalone_question == "进程和线程有什么区别?"
    assert timed_out_plan == invalid_plan
