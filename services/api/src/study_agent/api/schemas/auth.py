"""HTTP contracts for local accounts and admin diagnostics."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=100)
    invite_code: SecretStr | None = Field(default=None, min_length=16, max_length=512)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=256)


class AccountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    email: str
    display_name: str
    role: Literal["admin", "user"]


class AdminAccountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    email: str
    display_name: str
    role: Literal["admin", "user"]
    status: Literal["active", "suspended"]
    admin_note: str | None
    created_at: datetime


class AdminAccountUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["admin", "user"] | None = None
    status: Literal["active", "suspended"] | None = None
    admin_note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_at_least_one_update(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one account field must be provided")
        if "role" in self.model_fields_set and self.role is None:
            raise ValueError("role cannot be null")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be null")
        return self


class AdminUsersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminAccountResponse]


class CreateInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expires_in_days: int = Field(default=7, ge=1, le=30)


class InvitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created_by_account_id: str
    used_by_account_id: str | None
    status: Literal["available", "used", "revoked", "expired"]
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None


class InvitationCreateResponse(InvitationResponse):
    code: str = Field(min_length=16, max_length=512)


class AdminInvitationsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[InvitationResponse]


class AdminTotalsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounts: int = Field(ge=0)
    active_sessions: int = Field(ge=0)
    courses: int = Field(ge=0)
    documents: int = Field(ge=0)
    notes: int = Field(ge=0)


class AdminRuntimeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_mode: Literal["local", "test", "production"]
    database: Literal["postgresql"]
    demo_lab_enabled: bool


class AdminDiagnosticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    totals: AdminTotalsResponse
    runtime: AdminRuntimeResponse


__all__ = [
    "AccountResponse",
    "AdminAccountResponse",
    "AdminAccountUpdateRequest",
    "AdminDiagnosticsResponse",
    "AdminInvitationsResponse",
    "AdminRuntimeResponse",
    "AdminTotalsResponse",
    "AdminUsersResponse",
    "CreateInvitationRequest",
    "InvitationCreateResponse",
    "InvitationResponse",
    "LoginRequest",
    "RegisterRequest",
]
