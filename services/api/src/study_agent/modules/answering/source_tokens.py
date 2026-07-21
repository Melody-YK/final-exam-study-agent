"""Short-lived, tamper-evident grants for local citation content URLs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class LocalReadGrant:
    expires: int
    signature: str


class LocalReadTokenSigner:
    def __init__(self, key: bytes | None = None) -> None:
        resolved = key or secrets.token_bytes(32)
        if len(resolved) < 32:
            raise ValueError("local read signing key must contain at least 32 bytes")
        self._key = resolved

    def sign(self, query_id: str, citation_id: str, *, expires_at: datetime) -> LocalReadGrant:
        if expires_at.tzinfo is None:
            raise ValueError("local read expiry must be timezone-aware")
        expires = int(expires_at.timestamp())
        return LocalReadGrant(
            expires=expires,
            signature=self._signature(query_id, citation_id, expires),
        )

    def verify(
        self,
        query_id: str,
        citation_id: str,
        *,
        expires: int,
        signature: str,
        now: datetime,
    ) -> bool:
        if now.tzinfo is None or expires < 0 or expires < int(now.timestamp()):
            return False
        if len(signature) != 43:
            return False
        expected = self._signature(query_id, citation_id, expires)
        return hmac.compare_digest(signature, expected)

    def _signature(self, query_id: str, citation_id: str, expires: int) -> str:
        message = json.dumps(
            ["v1", query_id, citation_id, expires],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        digest = hmac.new(self._key, message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
