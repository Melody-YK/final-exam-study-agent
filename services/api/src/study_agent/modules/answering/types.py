"""Answering-domain values that retain source authorization context."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from study_contracts import Evidence, StructuredAnswer


@dataclass(frozen=True, slots=True)
class ConceptEvidenceAnchor:
    """One graph occurrence selected as retrieval context."""

    document_id: str
    revision_id: str
    chunk_id: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.document_id, self.revision_id, self.chunk_id)):
            raise ValueError("concept evidence anchor values must not be blank")


@dataclass(frozen=True, slots=True)
class ConceptEvidenceContext:
    """Principal-scoped graph context that may seed retrieval."""

    label: str
    anchors: tuple[ConceptEvidenceAnchor, ...]

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("concept label must not be blank")
        if not self.anchors:
            raise ValueError("concept evidence context requires anchors")
        chunk_ids = [anchor.chunk_id for anchor in self.anchors]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("concept evidence anchors must be unique")


@dataclass(frozen=True, slots=True)
class AuthorizedEvidence:
    """One current, principal-scoped retrieval result offered to the model."""

    evidence: Evidence
    document_name: str
    score: float
    document_deletion_epoch: int
    provenance: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.document_name.strip():
            raise ValueError("document_name must not be blank")
        if not isfinite(self.score):
            raise ValueError("evidence score must be finite")
        if self.document_deletion_epoch < 0:
            raise ValueError("document deletion epoch must not be negative")


@dataclass(frozen=True, slots=True)
class AnswerExecution:
    """A safe answer or a normalized failure, never partial untrusted text."""

    answer: StructuredAnswer | None
    failure_code: str | None = None
    provider: str | None = None
    model: str | None = None
    provider_response_id: str | None = None
    usage: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.answer is None) == (self.failure_code is None):
            raise ValueError("execution requires exactly one of answer or failure_code")
