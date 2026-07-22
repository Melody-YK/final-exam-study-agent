from study_agent.modules.answering.queries import _contextual_retrieval_query
from study_agent.providers.protocols import ConversationContextTurn


def test_contextual_retrieval_query_uses_history_questions_without_answers() -> None:
    rendered = _contextual_retrieval_query(
        "它和线程有什么区别?",
        (
            ConversationContextTurn(
                question="什么是进程?",
                answer_markdown="进程是资源分配的基本单位。",
            ),
        ),
    )

    assert rendered == (
        "[NON_EVIDENCE_CONVERSATION_CONTEXT]\n"
        "User: 什么是进程?\n"
        "[CURRENT_QUESTION]\n"
        "它和线程有什么区别?"
    )
    assert "进程是资源分配的基本单位。" not in rendered
