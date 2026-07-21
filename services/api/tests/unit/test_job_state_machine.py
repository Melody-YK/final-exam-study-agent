import pytest

from study_agent.modules.jobs.state_machine import (
    InvalidJobTransition,
    allowed_targets,
    transition,
)
from study_contracts import JobStatus


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (JobStatus.QUEUED, JobStatus.LEASED),
        (JobStatus.LEASED, JobStatus.PARSING),
        (JobStatus.PARSING, JobStatus.RESULT_SUBMITTED),
        (JobStatus.LEASED, JobStatus.RETRY_WAIT),
        (JobStatus.PARSING, JobStatus.RETRY_WAIT),
        (JobStatus.RETRY_WAIT, JobStatus.QUEUED),
        (JobStatus.RESULT_SUBMITTED, JobStatus.VALIDATING),
        (JobStatus.VALIDATING, JobStatus.INDEXING),
        (JobStatus.INDEXING, JobStatus.SUCCEEDED),
    ],
)
def test_state_machine_accepts_planned_transitions(source: JobStatus, target: JobStatus) -> None:
    assert transition(source, target) is target


@pytest.mark.parametrize(
    "source",
    [
        JobStatus.QUEUED,
        JobStatus.LEASED,
        JobStatus.PARSING,
        JobStatus.RETRY_WAIT,
        JobStatus.RESULT_SUBMITTED,
        JobStatus.VALIDATING,
        JobStatus.INDEXING,
    ],
)
def test_any_non_terminal_state_can_be_cancelled(source: JobStatus) -> None:
    assert transition(source, JobStatus.CANCELLED) is JobStatus.CANCELLED


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (JobStatus.QUEUED, JobStatus.SUCCEEDED),
        (JobStatus.PARSING, JobStatus.LEASED),
        (JobStatus.SUCCEEDED, JobStatus.QUEUED),
        (JobStatus.CANCELLED, JobStatus.PARSING),
    ],
)
def test_state_machine_rejects_illegal_transitions(source: JobStatus, target: JobStatus) -> None:
    with pytest.raises(InvalidJobTransition):
        transition(source, target)


def test_every_status_pair_matches_the_declared_transition_matrix() -> None:
    expected = {
        JobStatus.QUEUED: {JobStatus.LEASED, JobStatus.CANCELLED},
        JobStatus.LEASED: {
            JobStatus.PARSING,
            JobStatus.QUEUED,
            JobStatus.RETRY_WAIT,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        },
        JobStatus.PARSING: {
            JobStatus.QUEUED,
            JobStatus.RESULT_SUBMITTED,
            JobStatus.RETRY_WAIT,
            JobStatus.PARTIAL_FAILED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        },
        JobStatus.RESULT_SUBMITTED: {
            JobStatus.VALIDATING,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        },
        JobStatus.VALIDATING: {
            JobStatus.INDEXING,
            JobStatus.PARTIAL_FAILED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        },
        JobStatus.INDEXING: {
            JobStatus.SUCCEEDED,
            JobStatus.PARTIAL_FAILED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        },
        JobStatus.RETRY_WAIT: {
            JobStatus.QUEUED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        },
        JobStatus.SUCCEEDED: set(),
        JobStatus.PARTIAL_FAILED: set(),
        JobStatus.FAILED: set(),
        JobStatus.CANCELLED: set(),
    }
    for source in JobStatus:
        assert allowed_targets(source) == expected[source]
        for target in JobStatus:
            if target in expected[source]:
                assert transition(source, target) is target
            else:
                with pytest.raises(InvalidJobTransition):
                    transition(source, target)
