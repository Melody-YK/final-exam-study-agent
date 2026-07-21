import pytest

from study_agent.identity.principal import AuthRequired, LocalPrincipalProvider


def test_local_identity_accepts_ipv4_and_ipv6_loopback() -> None:
    provider = LocalPrincipalProvider()

    assert provider.resolve("127.0.0.1").subject == "local-user"
    assert provider.resolve("::1").subject == "local-user"


def test_local_identity_rejects_non_loopback_clients() -> None:
    provider = LocalPrincipalProvider()

    with pytest.raises(AuthRequired):
        provider.resolve("192.168.1.20")
