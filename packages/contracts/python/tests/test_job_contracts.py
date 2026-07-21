from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from study_contracts import (
    JobCompleteRequest,
    JobEventEnvelope,
    JobStatus,
    PageCheckpointRequest,
    WorkerCapabilities,
    WorkerLease,
)


def _lease() -> WorkerLease:
    return WorkerLease(
        job_id="job-1",
        job_type="parse",
        course_id="course-1",
        document_id="document-1",
        document_sha256="a" * 64,
        deletion_epoch=0,
        media_type="application/pdf",
        parser_profile="native-v1",
        parser_schema_version="1.0",
        attempt=1,
        lease_version=1,
        lease_token="secret-lease-token-with-enough-entropy",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        input_url="/worker/v1/jobs/job-1/input?lease_version=1",
        artifact_upload_url="/worker/v1/jobs/job-1/artifacts",
    )


def test_worker_lease_round_trips_without_exposing_a_token_hash() -> None:
    lease = _lease()

    restored = WorkerLease.model_validate_json(lease.model_dump_json())

    assert restored == lease
    assert restored.schema_version == "1.0"
    assert "token_hash" not in restored.model_dump(mode="json")


def test_worker_capabilities_require_unique_profiles_and_media_types() -> None:
    capability = WorkerCapabilities(
        parser_profiles=["native-v1"],
        media_types=["application/pdf"],
        max_input_bytes=1024,
        max_pages=10,
    )
    assert capability.parser_profiles == ["native-v1"]

    with pytest.raises(ValidationError):
        WorkerCapabilities(
            parser_profiles=["native-v1", "native-v1"],
            media_types=["application/pdf"],
            max_input_bytes=1024,
            max_pages=10,
        )


def test_zero_capability_worker_can_be_represented_without_advertising_fake_handlers() -> None:
    capability = WorkerCapabilities(
        max_input_bytes=1024,
        max_pages=10,
    )

    assert capability.parser_profiles == []
    assert capability.media_types == []


def test_checkpoint_and_complete_reject_invalid_page_sets() -> None:
    lease = _lease()
    lease_fields = {
        "worker_id": "worker-1",
        "lease_token": lease.lease_token,
        "lease_version": lease.lease_version,
        "attempt": lease.attempt,
        "deletion_epoch": lease.deletion_epoch,
    }

    with pytest.raises(ValidationError):
        PageCheckpointRequest(
            **lease_fields,
            status="succeeded",
            output_ref="derived/job-1/page-0.json",
            output_sha256="b" * 64,
            output_size_bytes=10,
            output_schema_version="1.0",
            source_backend="pdf-native",
            source_version="1.0",
            page_ordinal=0,
        )
    with pytest.raises(ValidationError):
        JobCompleteRequest(
            **lease_fields,
            result_manifest_ref="derived/job-1/manifest.json",
            result_sha256="c" * 64,
            result_size_bytes=100,
            manifest_schema_version="1.0",
            page_count=2,
            failed_pages=[1, 1],
        )


def test_job_event_envelope_is_versioned_and_ordered() -> None:
    event = JobEventEnvelope(
        sequence=4,
        occurred_at=datetime.now(UTC),
        trace_id="trace-1",
        event_type="job.heartbeat",
        data={"status": JobStatus.PARSING.value},
    )

    assert event.stream_version == "1"
    assert event.sequence == 4
