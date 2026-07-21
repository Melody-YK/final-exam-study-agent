from study_agent.modules.retrieval.rrf import RankedCandidate, reciprocal_rank_fusion


def test_rrf_combines_routes_and_keeps_route_evidence() -> None:
    dense = [
        RankedCandidate(chunk_id="a", rank=1, score=0.9),
        RankedCandidate(chunk_id="b", rank=2, score=0.8),
    ]
    lexical = [
        RankedCandidate(chunk_id="b", rank=1, score=4.0),
        RankedCandidate(chunk_id="c", rank=2, score=3.0),
    ]

    fused = reciprocal_rank_fusion(dense=dense, lexical=lexical, k=60)

    assert [item.chunk_id for item in fused] == ["b", "a", "c"]
    assert fused[0].dense_rank == 2
    assert fused[0].lexical_rank == 1
    assert fused[0].dense_score == 0.8
    assert fused[0].lexical_score == 4.0


def test_rrf_tie_break_is_stable_by_best_rank_then_chunk_id() -> None:
    dense = [RankedCandidate(chunk_id="b", rank=1, score=0.9)]
    lexical = [RankedCandidate(chunk_id="a", rank=1, score=5.0)]

    repeated = [
        [item.chunk_id for item in reciprocal_rank_fusion(dense=dense, lexical=lexical, k=60)]
        for _ in range(5)
    ]

    assert repeated == [["a", "b"]] * 5


def test_rrf_rejects_duplicate_or_nonpositive_ranks() -> None:
    duplicate = [
        RankedCandidate(chunk_id="a", rank=1, score=1.0),
        RankedCandidate(chunk_id="a", rank=2, score=0.5),
    ]

    try:
        reciprocal_rank_fusion(dense=duplicate, lexical=[], k=60)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate candidates must be rejected")

    try:
        RankedCandidate(chunk_id="a", rank=0, score=1.0)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("rank zero must be rejected")
