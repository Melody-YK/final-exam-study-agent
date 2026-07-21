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
