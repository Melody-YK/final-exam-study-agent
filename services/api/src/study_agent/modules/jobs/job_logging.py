"""Allow-listed structured logging for job control-plane transitions."""

from __future__ import annotations

import structlog

from study_agent.observability.trace import get_trace_id, new_trace_id

JOB_LOG_FIELDS = frozenset(
    {
        "attempt",
        "course_id",
        "document_id",
        "duration_ms",
        "error_code",
        "event",
        "job_id",
        "lease_version",
        "log_level",
        "page_ordinal",
        "state",
        "trace_id",
        "worker_id",
    }
)


def log_job_event(
    event: str,
    *,
    job_id: str,
    course_id: str,
    document_id: str,
    state: str,
    attempt: int,
    worker_id: str | None = None,
    lease_version: int | None = None,
    page_ordinal: int | None = None,
    error_code: str | None = None,
    duration_ms: int | None = None,
) -> None:
    fields: dict[str, object] = {
        "job_id": job_id,
        "course_id": course_id,
        "document_id": document_id,
        "state": state,
        "attempt": attempt,
        "trace_id": get_trace_id() or new_trace_id(),
    }
    optional = {
        "worker_id": worker_id,
        "lease_version": lease_version,
        "page_ordinal": page_ordinal,
        "error_code": error_code,
        "duration_ms": duration_ms,
    }
    fields.update({key: value for key, value in optional.items() if value is not None})
    structlog.get_logger("study_agent.jobs").info(event, **fields)
