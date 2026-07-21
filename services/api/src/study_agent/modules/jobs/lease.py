"""Lease token generation, hashing, validation, and backoff policy."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta


def new_lease_token() -> str:
    return secrets.token_urlsafe(32)


def hash_lease_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def lease_token_matches(token: str, expected_hash: str | None) -> bool:
    if expected_hash is None:
        return False
    return hmac.compare_digest(hash_lease_token(token), expected_hash)


@dataclass(frozen=True, slots=True)
class LeasePolicy:
    ttl: timedelta
    retry_base: timedelta

    def retry_delay(self, attempt: int) -> timedelta:
        multiplier = 2 ** max(0, attempt - 1)
        seconds = min(self.retry_base.total_seconds() * multiplier, 5 * 60)
        return timedelta(seconds=seconds)
