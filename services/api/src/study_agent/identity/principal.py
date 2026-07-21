"""Identity values and fail-closed authentication boundaries."""

from __future__ import annotations

from enum import StrEnum
from ipaddress import ip_address
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class AuthenticationMethod(StrEnum):
    LOCAL = "local"
    OIDC = "oidc"


class AuthRequired(PermissionError):
    """Raised when no trusted principal can be established."""

    code = "AUTH_REQUIRED"

    def __init__(self, message: str = "authentication is required") -> None:
        super().__init__(message)


class Principal(BaseModel):
    """A trusted actor identity created only by an identity adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str = Field(min_length=1, max_length=255)
    authentication_method: AuthenticationMethod
    scopes: frozenset[str] = Field(default_factory=frozenset)

    @field_validator("subject")
    @classmethod
    def normalize_subject(cls, value: str) -> str:
        subject = value.strip()
        if not subject:
            raise ValueError("subject must not be blank")
        return subject


class CourseScope(BaseModel):
    """The principal-bound scope required for course data access."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    principal: Principal
    course_id: str = Field(min_length=1, max_length=255)

    @field_validator("course_id")
    @classmethod
    def normalize_course_id(cls, value: str) -> str:
        course_id = value.strip()
        if not course_id:
            raise ValueError("course_id must not be blank")
        return course_id

    @property
    def subject(self) -> str:
        return self.principal.subject


@runtime_checkable
class PrincipalProvider(Protocol):
    """Resolves a trusted principal from a verified client address."""

    def resolve(self, client_host: str) -> Principal: ...


@runtime_checkable
class AuthAdapter(Protocol):
    """Production authentication adapter contract.

    Implementations validate the credential and must raise ``AuthRequired``
    for missing, malformed, expired, or otherwise untrusted credentials.
    """

    async def authenticate(self, credential: SecretStr | None) -> Principal: ...


class LocalPrincipalProvider:
    """Single-user identity provider restricted to literal loopback IPs."""

    _principal = Principal(
        subject="local-user",
        authentication_method=AuthenticationMethod.LOCAL,
    )

    def resolve(self, client_host: str) -> Principal:
        try:
            address = ip_address(client_host)
        except ValueError as exc:
            raise AuthRequired() from exc

        if not address.is_loopback:
            raise AuthRequired()
        return self._principal
