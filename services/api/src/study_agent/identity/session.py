"""Request authentication backed by the local HttpOnly session cookie."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.config import AppMode, Settings
from study_agent.identity.principal import Principal, PrincipalProvider
from study_agent.infrastructure.db.session import Database
from study_agent.modules.auth.service import AccountIdentity, AccountService
from study_agent.providers.protocols import Clock

SESSION_COOKIE_NAME = "study_session"


def account_service_from_request(request: Request) -> AccountService:
    return AccountService(
        cast(Database, request.app.state.database),
        cast(Clock, request.app.state.clock),
        cast(Settings, request.app.state.settings),
    )


async def get_session_account(request: Request) -> AccountIdentity:
    identity = await account_service_from_request(request).resolve(
        request.cookies.get(SESSION_COOKIE_NAME)
    )
    if identity is None:
        raise _auth_required()
    return identity


async def get_request_principal(request: Request) -> Principal:
    """Resolve a Principal while preserving injected providers in TEST mode."""

    settings = cast(Settings, request.app.state.settings)
    if settings.app_mode is AppMode.TEST:
        if request.client is None:
            raise _auth_required()
        provider = cast(PrincipalProvider, request.app.state.principal_provider)
        try:
            return provider.resolve(request.client.host)
        except PermissionError as exc:
            raise _auth_required() from exc
    return (await get_session_account(request)).principal


def _auth_required() -> ApiProblem:
    return ApiProblem(
        status=401,
        code=ProblemCode.AUTH_REQUIRED,
        title="需要身份验证",
    )


__all__ = [
    "SESSION_COOKIE_NAME",
    "account_service_from_request",
    "get_request_principal",
    "get_session_account",
]
