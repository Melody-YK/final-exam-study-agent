from study_agent.modules.auth.passwords import ScryptPasswordHasher


def test_scrypt_password_hash_is_salted_and_rejects_malformed_values() -> None:
    hasher = ScryptPasswordHasher(n=2**10, max_memory_bytes=8 * 1024 * 1024)

    first = hasher.hash("long enough password")
    second = hasher.hash("long enough password")

    assert first != second
    assert "long enough password" not in first
    assert hasher.verify("long enough password", first) is True
    assert hasher.verify("wrong password", first) is False
    assert hasher.verify("long enough password", "not-a-valid-hash") is False
