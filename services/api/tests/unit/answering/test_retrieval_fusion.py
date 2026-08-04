from study_agent.modules.answering.queries import _fuse_retrieval_results
from study_agent.modules.answering.retrieval import RetrievedEvidence
from study_agent.modules.answering.types import AuthorizedEvidence
from study_contracts import Evidence, SourceLocator


def _candidate(chunk_id: str, score: float) -> AuthorizedEvidence:
    return AuthorizedEvidence(
        evidence=Evidence(
            id=chunk_id,
            course_id="course-1",
            document_id="document-1",
            revision_id="revision-1",
            chunk_id=chunk_id,
            text=f"{chunk_id} content",
            content_sha256=chunk_id[0] * 64,
            locator=SourceLocator(kind="page", ordinal=1),
        ),
        document_name="course.pdf",
        score=score,
        document_deletion_epoch=0,
        provenance=("pdf-native@1",),
    )


def _result(manifest: str, *candidates: AuthorizedEvidence) -> RetrievedEvidence:
    return RetrievedEvidence(
        active_index=True,
        candidates=candidates,
        retrieval_trace_id=f"trace-{manifest}",
        active_lexical_index_id=manifest,
    )


def test_fusion_deduplicates_chunks_and_prioritizes_repeated_eligible_hits() -> None:
    fused = _fuse_retrieval_results(
        [
            _result("index-1", _candidate("a", 0.8), _candidate("b", 0.7)),
            _result("index-1", _candidate("b", 0.75), _candidate("c", 0.9)),
        ],
        min_score=0.02,
    )

    assert [item.evidence.chunk_id for item in fused.candidates] == ["b", "a", "c"]
    assert fused.candidates[0].score == 0.75
    assert sum(item.evidence.chunk_id == "b" for item in fused.candidates) == 1


def test_fusion_discards_results_from_an_obsolete_manifest() -> None:
    fused = _fuse_retrieval_results(
        [
            _result("index-old", _candidate("a", 0.9)),
            _result("index-new", _candidate("b", 0.8)),
        ],
        min_score=0.02,
    )

    assert fused.active_lexical_index_id == "index-new"
    assert [item.evidence.chunk_id for item in fused.candidates] == ["b"]
