from structlog.testing import capture_logs

from study_agent.modules.jobs.job_logging import JOB_LOG_FIELDS, log_job_event


def test_job_log_uses_allow_list_and_never_accepts_private_payload_fields() -> None:
    with capture_logs() as logs:
        log_job_event(
            "job.checkpointed",
            job_id="job-1",
            course_id="course-1",
            document_id="document-1",
            state="parsing",
            attempt=2,
            worker_id="worker-1",
            lease_version=3,
            page_ordinal=4,
            error_code="PARSER_TIMEOUT",
        )

    assert len(logs) == 1
    assert set(logs[0]) <= JOB_LOG_FIELDS
    serialized = repr(logs[0]).lower()
    assert "lease_token" not in serialized
    assert "object_key" not in serialized
    assert "payload" not in serialized
    assert "private" not in serialized
