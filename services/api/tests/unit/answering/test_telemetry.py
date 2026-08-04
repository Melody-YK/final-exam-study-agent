from study_agent.modules.answering.telemetry import CONVERSATION_LOG_FIELDS


def test_conversation_telemetry_allowlist_excludes_private_content() -> None:
    assert CONVERSATION_LOG_FIELDS.isdisjoint(
        {"question", "answer", "content", "message", "course_text", "provider_response"}
    )
