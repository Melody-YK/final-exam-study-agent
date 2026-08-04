from study_agent.modules.answering.evidence_gate import EvidenceGate, EvidenceGateCode
from study_agent.modules.answering.types import AuthorizedEvidence
from study_contracts import Evidence, SourceLocator


def _candidate(*, score: float = 0.8) -> AuthorizedEvidence:
    return AuthorizedEvidence(
        evidence=Evidence(
            id="evidence-1",
            course_id="course-1",
            document_id="document-1",
            revision_id="revision-1",
            chunk_id="chunk-1",
            text="进程是资源分配的基本单位。",
            content_sha256="a" * 64,
            locator=SourceLocator(kind="slide", ordinal=4),
        ),
        document_name="进程管理.pptx",
        score=score,
        document_deletion_epoch=2,
        provenance=("pptx-native@1.0",),
    )


def test_no_active_index_abstains_before_evidence_is_considered() -> None:
    decision = EvidenceGate().evaluate(active_index=False, candidates=(_candidate(),))

    assert not decision.sufficient
    assert decision.code is EvidenceGateCode.INDEX_UNAVAILABLE


def test_empty_or_low_scoring_evidence_is_insufficient() -> None:
    gate = EvidenceGate(min_score=0.4)

    empty = gate.evaluate(active_index=True, candidates=())
    low_score = gate.evaluate(active_index=True, candidates=(_candidate(score=0.39),))

    assert empty.code is EvidenceGateCode.NO_CANDIDATES
    assert low_score.code is EvidenceGateCode.LOW_RELEVANCE


def test_default_gate_rejects_negligible_retrieval_score() -> None:
    decision = EvidenceGate().evaluate(
        active_index=True,
        candidates=(_candidate(score=0.019),),
    )

    assert decision.code is EvidenceGateCode.LOW_RELEVANCE


def test_gate_returns_only_the_bounded_authorized_candidate_set() -> None:
    candidates = tuple(_candidate(score=0.9 - index / 100) for index in range(10))

    decision = EvidenceGate(max_evidence=3).evaluate(active_index=True, candidates=candidates)

    assert decision.sufficient
    assert decision.candidates == candidates[:3]
