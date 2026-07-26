"""Cookie-session authentication and local admin endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from study_agent.api.errors import ApiProblem, ProblemCode, ProblemDetails
from study_agent.api.schemas.auth import (
    AccountResponse,
    AdminAccountResponse,
    AdminAccountUpdateRequest,
    AdminDiagnosticsResponse,
    AdminInvitationsResponse,
    AdminRuntimeResponse,
    AdminTotalsResponse,
    AdminUsersResponse,
    CreateInvitationRequest,
    InvitationCreateResponse,
    InvitationResponse,
    LoginRequest,
    RegisterRequest,
)
from study_agent.identity.session import (
    SESSION_COOKIE_NAME,
    account_service_from_request,
    get_session_account,
)
from study_agent.modules.auth.service import (
    SESSION_TTL_SECONDS,
    Account,
    AccountIdentity,
    AccountRole,
    AccountServiceError,
    AccountServiceErrorCode,
    AccountStatus,
    AdminDiagnostics,
    InvitationGrant,
    RegistrationInvitation,
    SessionGrant,
)

router = APIRouter(prefix="/api/v1", tags=["auth"])
SessionAccount = Annotated[AccountIdentity, Depends(get_session_account)]


@router.post(
    "/auth/register",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ProblemDetails, "description": "邮箱或账号容量冲突"}},
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
) -> AccountResponse:
    try:
        grant = await account_service_from_request(request).register(
            payload.email,
            payload.password.get_secret_value(),
            payload.display_name,
            (None if payload.invite_code is None else payload.invite_code.get_secret_value()),
        )
    except AccountServiceError as exc:
        raise _problem(exc) from exc
    _set_session_cookie(request, response, grant)
    return _account_response(grant.account)


@router.post("/auth/login", response_model=AccountResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
) -> AccountResponse:
    try:
        grant = await account_service_from_request(request).login(
            payload.email,
            payload.password.get_secret_value(),
        )
    except AccountServiceError as exc:
        raise _problem(exc) from exc
    _set_session_cookie(request, response, grant)
    return _account_response(grant.account)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    await account_service_from_request(request).logout(request.cookies.get(SESSION_COOKIE_NAME))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/auth/me", response_model=AccountResponse)
async def me(identity: SessionAccount) -> AccountResponse:
    return _account_response(identity.account)


@router.get("/admin/users", response_model=AdminUsersResponse)
async def list_users(request: Request, identity: SessionAccount) -> AdminUsersResponse:
    try:
        accounts = await account_service_from_request(request).list_accounts(identity)
    except AccountServiceError as exc:
        raise _problem(exc) from exc
    return AdminUsersResponse(items=[_admin_account_response(account) for account in accounts])


@router.patch(
    "/admin/users/{account_id}",
    response_model=AdminAccountResponse,
    responses={409: {"model": ProblemDetails, "description": "账号状态或容量冲突"}},
)
async def update_user(
    account_id: str,
    payload: AdminAccountUpdateRequest,
    request: Request,
    identity: SessionAccount,
) -> AdminAccountResponse:
    try:
        account = await account_service_from_request(request).update_account(
            identity,
            account_id,
            role=None if payload.role is None else AccountRole(payload.role),
            status=None if payload.status is None else AccountStatus(payload.status),
            admin_note=payload.admin_note,
            admin_note_provided="admin_note" in payload.model_fields_set,
        )
    except AccountServiceError as exc:
        raise _problem(exc) from exc
    return _admin_account_response(account)


@router.post(
    "/admin/invitations",
    response_model=InvitationCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ProblemDetails, "description": "账号容量已满"}},
)
async def create_invitation(
    payload: CreateInvitationRequest,
    request: Request,
    identity: SessionAccount,
) -> InvitationCreateResponse:
    try:
        grant = await account_service_from_request(request).create_invitation(
            identity,
            payload.expires_in_days,
        )
    except AccountServiceError as exc:
        raise _problem(exc) from exc
    return _invitation_create_response(grant)


@router.get("/admin/invitations", response_model=AdminInvitationsResponse)
async def list_invitations(
    request: Request,
    identity: SessionAccount,
) -> AdminInvitationsResponse:
    try:
        invitations = await account_service_from_request(request).list_invitations(identity)
    except AccountServiceError as exc:
        raise _problem(exc) from exc
    return AdminInvitationsResponse(
        items=[_invitation_response(invitation) for invitation in invitations]
    )


@router.delete(
    "/admin/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invitation(
    invitation_id: str,
    request: Request,
    identity: SessionAccount,
) -> Response:
    try:
        await account_service_from_request(request).revoke_invitation(identity, invitation_id)
    except AccountServiceError as exc:
        raise _problem(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/admin/diagnostics", response_model=AdminDiagnosticsResponse)
async def diagnostics(request: Request, identity: SessionAccount) -> AdminDiagnosticsResponse:
    try:
        snapshot = await account_service_from_request(request).diagnostics(identity)
    except AccountServiceError as exc:
        raise _problem(exc) from exc
    return _diagnostics_response(snapshot)


def _set_session_cookie(request: Request, response: Response, grant: SessionGrant) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        grant.token,
        max_age=SESSION_TTL_SECONDS,
        expires=grant.expires_at,
        path="/",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )


def _account_response(account: Account) -> AccountResponse:
    return AccountResponse(
        id=account.id,
        email=account.email,
        display_name=account.display_name,
        role=account.role.value,
    )


def _admin_account_response(account: Account) -> AdminAccountResponse:
    return AdminAccountResponse(
        id=account.id,
        email=account.email,
        display_name=account.display_name,
        role=account.role.value,
        status=account.status.value,
        admin_note=account.admin_note,
        created_at=account.created_at,
    )


def _invitation_response(invitation: RegistrationInvitation) -> InvitationResponse:
    return InvitationResponse(
        id=invitation.id,
        created_by_account_id=invitation.created_by_account_id,
        used_by_account_id=invitation.used_by_account_id,
        status=invitation.status.value,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        used_at=invitation.used_at,
        revoked_at=invitation.revoked_at,
    )


def _invitation_create_response(grant: InvitationGrant) -> InvitationCreateResponse:
    invitation = _invitation_response(grant.invitation)
    return InvitationCreateResponse(**invitation.model_dump(), code=grant.code)


def _diagnostics_response(snapshot: AdminDiagnostics) -> AdminDiagnosticsResponse:
    return AdminDiagnosticsResponse(
        totals=AdminTotalsResponse(
            accounts=snapshot.accounts,
            active_sessions=snapshot.active_sessions,
            courses=snapshot.courses,
            documents=snapshot.documents,
            notes=snapshot.notes,
        ),
        runtime=AdminRuntimeResponse(
            app_mode=snapshot.app_mode,
            database=snapshot.database,
            demo_lab_enabled=snapshot.demo_lab_enabled,
        ),
        active_accounts=snapshot.active_accounts,
        account_capacity=snapshot.account_capacity,
        available_account_seats=snapshot.available_account_seats,
    )


def _problem(exc: AccountServiceError) -> ApiProblem:
    mapping: dict[AccountServiceErrorCode, tuple[int, ProblemCode, str]] = {
        AccountServiceErrorCode.EMAIL_EXISTS: (
            409,
            ProblemCode.ACCOUNT_EMAIL_EXISTS,
            "邮箱已注册",
        ),
        AccountServiceErrorCode.INVALID_CREDENTIALS: (
            401,
            ProblemCode.AUTH_REQUIRED,
            "邮箱或密码错误",
        ),
        AccountServiceErrorCode.FORBIDDEN: (
            403,
            ProblemCode.AUTH_FORBIDDEN,
            "需要管理员权限",
        ),
        AccountServiceErrorCode.INVALID_INPUT: (
            422,
            ProblemCode.INVALID_REQUEST,
            "请求参数无效",
        ),
        AccountServiceErrorCode.INVALID_INVITATION: (
            403,
            ProblemCode.AUTH_FORBIDDEN,
            "注册邀请无效",
        ),
        AccountServiceErrorCode.NOT_FOUND: (
            404,
            ProblemCode.RESOURCE_NOT_FOUND,
            "资源不存在",
        ),
        AccountServiceErrorCode.CONFLICT: (
            409,
            ProblemCode.STATE_CONFLICT,
            "账号或邀请状态冲突",
        ),
        AccountServiceErrorCode.CAPACITY_REACHED: (
            409,
            ProblemCode.ACCOUNT_CAPACITY_REACHED,
            "账号容量已满",
        ),
    }
    status_code, code, title = mapping[exc.code]
    return ApiProblem(
        status=status_code,
        code=code,
        title=title,
        detail=exc.detail,
    )


__all__ = ["router"]
