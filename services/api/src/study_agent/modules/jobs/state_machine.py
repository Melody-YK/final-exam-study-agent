"""Legal ParseJob state transitions."""

from study_contracts import JobStatus


class InvalidJobTransition(ValueError):
    def __init__(self, source: JobStatus, target: JobStatus) -> None:
        super().__init__(f"illegal job transition: {source.value} -> {target.value}")
        self.source = source
        self.target = target


_ALLOWED: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.LEASED, JobStatus.CANCELLED}),
    JobStatus.LEASED: frozenset(
        {
            JobStatus.PARSING,
            JobStatus.QUEUED,
            JobStatus.RETRY_WAIT,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
    ),
    JobStatus.PARSING: frozenset(
        {
            JobStatus.QUEUED,
            JobStatus.RESULT_SUBMITTED,
            JobStatus.RETRY_WAIT,
            JobStatus.PARTIAL_FAILED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
    ),
    JobStatus.RESULT_SUBMITTED: frozenset(
        {JobStatus.VALIDATING, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.VALIDATING: frozenset(
        {
            JobStatus.INDEXING,
            JobStatus.PARTIAL_FAILED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
    ),
    JobStatus.INDEXING: frozenset(
        {
            JobStatus.SUCCEEDED,
            JobStatus.PARTIAL_FAILED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
    ),
    JobStatus.RETRY_WAIT: frozenset({JobStatus.QUEUED, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.PARTIAL_FAILED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


def transition(source: JobStatus, target: JobStatus) -> JobStatus:
    if target not in _ALLOWED[source]:
        raise InvalidJobTransition(source, target)
    return target


def allowed_targets(source: JobStatus) -> frozenset[JobStatus]:
    return _ALLOWED[source]


def is_terminal(status: JobStatus) -> bool:
    return not _ALLOWED[status]
