"""Versioned standard-library password hashing for local accounts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScryptPasswordHasher:
    n: int = 2**14
    r: int = 8
    p: int = 1
    salt_bytes: int = 16
    key_bytes: int = 32
    max_memory_bytes: int = 64 * 1024 * 1024

    def hash(self, password: str) -> str:
        salt = secrets.token_bytes(self.salt_bytes)
        digest = self._derive(password, salt)
        return f"$scrypt${self.n}${self.r}${self.p}${_encode(salt)}${_encode(digest)}"

    def verify(self, password: str, encoded_hash: str) -> bool:
        try:
            empty, scheme, n_text, r_text, p_text, salt_text, digest_text = encoded_hash.split("$")
            if empty or scheme != "scrypt":
                return False
            if (int(n_text), int(r_text), int(p_text)) != (self.n, self.r, self.p):
                return False
            salt = _decode(salt_text)
            expected = _decode(digest_text)
            if len(salt) != self.salt_bytes or len(expected) != self.key_bytes:
                return False
            actual = self._derive(password, salt)
        except (TypeError, ValueError):
            return False
        return hmac.compare_digest(actual, expected)

    def _derive(self, password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=self.n,
            r=self.r,
            p=self.p,
            maxmem=self.max_memory_bytes,
            dklen=self.key_bytes,
        )


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


DEFAULT_PASSWORD_HASHER = ScryptPasswordHasher()
DUMMY_PASSWORD_HASH = DEFAULT_PASSWORD_HASHER.hash("invalid-session-password")

__all__ = ["DEFAULT_PASSWORD_HASHER", "DUMMY_PASSWORD_HASH", "ScryptPasswordHasher"]
