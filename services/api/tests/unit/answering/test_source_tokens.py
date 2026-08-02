from datetime import UTC, datetime, timedelta

from study_agent.modules.answering.source_tokens import LocalReadTokenSigner


def test_local_read_grant_rejects_tampering_and_expiry() -> None:
    signer = LocalReadTokenSigner(b"source-token-test-key-material!!")
    now = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
    grant = signer.sign("query-1", "citation-1", expires_at=now + timedelta(minutes=5))

    assert signer.verify(
        "query-1",
        "citation-1",
        expires=grant.expires,
        signature=grant.signature,
        now=now,
    )
    assert not signer.verify(
        "query-1",
        "citation-2",
        expires=grant.expires,
        signature=grant.signature,
        now=now,
    )
    assert not signer.verify(
        "query-1",
        "citation-1",
        expires=grant.expires,
        signature=grant.signature,
        now=now + timedelta(minutes=6),
    )


def test_scoped_local_read_grant_cannot_be_replayed_across_source_routes() -> None:
    signer = LocalReadTokenSigner(b"source-token-test-key-material!!")
    now = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
    grant = signer.sign_scoped(
        "note-source",
        "note-1",
        "source-1",
        expires_at=now + timedelta(minutes=5),
    )

    assert signer.verify_scoped(
        "note-source",
        "note-1",
        "source-1",
        expires=grant.expires,
        signature=grant.signature,
        now=now,
    )
    assert not signer.verify_scoped(
        "knowledge-graph-source",
        "note-1",
        "source-1",
        expires=grant.expires,
        signature=grant.signature,
        now=now,
    )
    assert not signer.verify_scoped(
        "note-source",
        "note-2",
        "source-1",
        expires=grant.expires,
        signature=grant.signature,
        now=now,
    )
    assert not signer.verify_scoped(
        "note-source",
        "note-1",
        "source-2",
        expires=grant.expires,
        signature=grant.signature,
        now=now,
    )
    assert not signer.verify_scoped(
        "note-source",
        "note-1",
        "source-1",
        expires=grant.expires,
        signature=grant.signature,
        now=now + timedelta(minutes=6),
    )
    changed_signature = grant.signature[:-1] + ("A" if grant.signature[-1] != "A" else "B")
    assert not signer.verify_scoped(
        "note-source",
        "note-1",
        "source-1",
        expires=grant.expires,
        signature=changed_signature,
        now=now,
    )
