"""Allow-listed conversation telemetry without learner or course content."""

from __future__ import annotations

import structlog

from study_agent.observability.trace import get_trace_id, new_trace_id

CONVERSATION_LOG_FIELDS = frozenset(
    {
        "context_turn_count",
        "conversation_id",
        "conversation_type",
        "course_id",
        "diagnostic",
        "duration_ms",
        "event",
        "intent",
        "memory_count",
        "message_count",
        "retrieval_round_count",
        "status",
        "trace_id",
    }
)


def log_conversation_event(
    event: str,
    *,
    course_id: str,
    conversation_id: str,
    conversation_type: str,
    status: str,
    intent: str | None = None,
    diagnostic: str | None = None,
    retrieval_round_count: int | None = None,
    context_turn_count: int | None = None,
    memory_count: int | None = None,
    message_count: int | None = None,
    duration_ms: int | None = None,
) -> None:
    fields: dict[str, object] = {
        "course_id": course_id,
        "conversation_id": conversation_id,
        "conversation_type": conversation_type,
        "status": status,
        "trace_id": get_trace_id() or new_trace_id(),
    }
    optional = {
        "intent": intent,
        "diagnostic": diagnostic,
        "retrieval_round_count": retrieval_round_count,
        "context_turn_count": context_turn_count,
        "memory_count": memory_count,
        "message_count": message_count,
        "duration_ms": duration_ms,
    }
    fields.update({key: value for key, value in optional.items() if value is not None})
    structlog.get_logger("study_agent.conversations").info(event, **fields)


__all__ = ["CONVERSATION_LOG_FIELDS", "log_conversation_event"]
