"""Transactional account registration, sessions, invitations, and admin controls."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.config import Settings
from study_agent.identity.principal import (
    LOCAL_PRINCIPAL_SUBJECT,
    AuthenticationMethod,
    Principal,
)
from study_agent.infrastructure.db.models import (
    AccountModel,
    AccountSessionModel,
    CourseModel,
    DocumentModel,
    NoteModel,
    RegistrationInvitationModel,
    UserModel,
)
from study_agent.infrastructure.db.models.core import new_id
from study_agent.infrastructure.db.session import Database
from study_agent.modules.auth.passwords import DEFAULT_PASSWORD_HASHER, DUMMY_PASSWORD_HASH
from study_agent.providers.protocols import Clock

SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
INVITATION_TTL_DAYS = 7
MAX_INVITATION_TTL_DAYS = 30
_AUTH_ACTOR = "account-service"
_REGISTRATION_LOCK = "study-agent:account-registration"
_ACCOUNT_ADMIN_LOCK = "study-agent:account-admin-mutation"
_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class AccountRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class InvitationStatus(StrEnum):
    AVAILABLE = "available"
    USED = "used"
    REVOKED = "revoked"
    EXPIRED = "expired"


class AccountServiceErrorCode(StrEnum):
    EMAIL_EXISTS = "EMAIL_EXISTS"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    FORBIDDEN = "FORBIDDEN"
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_INVITATION = "INVALID_INVITATION"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    CAPACITY_REACHED = "CAPACITY_REACHED"


class AccountServiceError(RuntimeError):
    def __init__(self, code: AccountServiceErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    user_id: str
    email: str
    display_name: str
    role: AccountRole
    status: AccountStatus
    admin_note: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    account: Account
    principal: Principal


@dataclass(frozen=True, slots=True)
class SessionGrant:
    account: Account
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RegistrationInvitation:
    id: str
    created_by_account_id: str
    used_by_account_id: str | None
    status: InvitationStatus
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class InvitationGrant:
    invitation: RegistrationInvitation
    code: str


@dataclass(frozen=True, slots=True)
class SeatUsage:
    active_accounts: int
    reserved_invitations: int

    @property
    def occupied_seats(self) -> int:
        return self.active_accounts + self.reserved_invitations


@dataclass(frozen=True, slots=True)
class AdminDiagnostics:
    accounts: int
    active_accounts: int
    account_capacity: int
    available_account_seats: int
    active_sessions: int
    courses: int
    documents: int
    notes: int
    app_mode: str
    database: str
    demo_lab_enabled: bool


class AccountService:
    def __init__(self, database: Database, clock: Clock, settings: Settings) -> None:
        self._database = database
        self._clock = clock
        self._settings = settings

    async def register(
        self,
        email: str,
        password: str,
        display_name: str,
        invite_code: str | None = None,
    ) -> SessionGrant:
        normalized_email = _normalize_email(email)
        normalized_name = _normalize_display_name(display_name)
        normalized_password = _validate_new_password(password)
        now = self._now()
        async with self._database.system_session(_AUTH_ACTOR) as session:
            await self._acquire_capacity_lock(session)
            is_first_account = (
                await session.scalar(select(func.count()).select_from(AccountModel)) == 0
            )
            invitation_model: RegistrationInvitationModel | None = None
            if not is_first_account:
                invitation_model = await self._valid_invitation(session, invite_code, now)

            duplicate = await session.scalar(
                select(AccountModel.id).where(AccountModel.email == normalized_email)
            )
            if duplicate is not None:
                raise AccountServiceError(
                    AccountServiceErrorCode.EMAIL_EXISTS,
                    "该邮箱已注册。",
                )

            seat_usage = await self._seat_usage(session, now)
            if (
                seat_usage.active_accounts >= self._settings.active_account_capacity
                or seat_usage.occupied_seats > self._settings.active_account_capacity
            ):
                self._raise_capacity_reached()

            password_hash = await asyncio.to_thread(
                DEFAULT_PASSWORD_HASHER.hash,
                normalized_password,
            )
            account_id = new_id()
            user = await self._registration_user(session, account_id, is_first_account)
            account_model = AccountModel(
                id=account_id,
                user_id=user.id,
                email=normalized_email,
                display_name=normalized_name,
                role=(AccountRole.ADMIN.value if is_first_account else AccountRole.USER.value),
                status=AccountStatus.ACTIVE.value,
                admin_note=None,
                password_hash=password_hash,
                created_at=now,
                updated_at=now,
            )
            session.add(account_model)
            await session.flush()
            if invitation_model is not None:
                invitation_model.used_at = now
                invitation_model.used_by_account_id = account_model.id
            token, expires_at = self._add_session(session, account_model.id, now)
            return SessionGrant(
                account=_account_from_model(account_model),
                token=token,
                expires_at=expires_at,
            )

    async def login(self, email: str, password: str) -> SessionGrant:
        normalized_email = _normalize_email(email)
        normalized_password = _validate_login_password(password)
        now = self._now()
        async with self._database.system_session(_AUTH_ACTOR) as session:
            account_model = await session.scalar(
                select(AccountModel)
                .where(AccountModel.email == normalized_email)
                .with_for_update(of=AccountModel)
            )
            encoded_hash = (
                DUMMY_PASSWORD_HASH if account_model is None else account_model.password_hash
            )
            password_matches = await asyncio.to_thread(
                DEFAULT_PASSWORD_HASHER.verify,
                normalized_password,
                encoded_hash,
            )
            if (
                account_model is None
                or account_model.disabled_at is not None
                or account_model.status != AccountStatus.ACTIVE.value
                or not password_matches
            ):
                raise AccountServiceError(
                    AccountServiceErrorCode.INVALID_CREDENTIALS,
                    "邮箱或密码错误。",
                )
            token, expires_at = self._add_session(session, account_model.id, now)
            return SessionGrant(
                account=_account_from_model(account_model),
                token=token,
                expires_at=expires_at,
            )

    async def logout(self, token: str | None) -> None:
        token_hash = _token_hash_or_none(token)
        if token_hash is None:
            return
        async with self._database.system_session(_AUTH_ACTOR) as session:
            model = await session.scalar(
                select(AccountSessionModel)
                .where(
                    AccountSessionModel.token_hash == token_hash,
                    AccountSessionModel.revoked_at.is_(None),
                )
                .with_for_update(of=AccountSessionModel)
            )
            if model is not None:
                model.revoked_at = self._now()

    async def resolve(self, token: str | None) -> AccountIdentity | None:
        token_hash = _token_hash_or_none(token)
        if token_hash is None:
            return None
        now = self._now()
        async with self._database.system_session(_AUTH_ACTOR) as session:
            row = (
                await session.execute(
                    select(AccountModel, UserModel)
                    .join(
                        AccountSessionModel,
                        AccountSessionModel.account_id == AccountModel.id,
                    )
                    .join(UserModel, UserModel.id == AccountModel.user_id)
                    .where(
                        AccountSessionModel.token_hash == token_hash,
                        AccountSessionModel.revoked_at.is_(None),
                        AccountSessionModel.expires_at > now,
                        AccountModel.disabled_at.is_(None),
                        AccountModel.status == AccountStatus.ACTIVE.value,
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            account_model, user_model = row
            account = _account_from_model(account_model)
            return AccountIdentity(
                account=account,
                principal=Principal(
                    subject=user_model.subject,
                    authentication_method=AuthenticationMethod(user_model.authentication_method),
                    scopes=(
                        frozenset({"admin"}) if account.role is AccountRole.ADMIN else frozenset()
                    ),
                ),
            )

    async def list_accounts(self, actor: AccountIdentity) -> list[Account]:
        _require_admin(actor)
        async with self._database.system_session(_AUTH_ACTOR) as session:
            models = list(
                await session.scalars(
                    select(AccountModel).order_by(AccountModel.created_at, AccountModel.id)
                )
            )
            return [_account_from_model(model) for model in models]

    async def update_account(
        self,
        actor: AccountIdentity,
        account_id: str,
        *,
        role: AccountRole | None,
        status: AccountStatus | None,
        admin_note: str | None,
        admin_note_provided: bool,
    ) -> Account:
        _require_admin(actor)
        normalized_note = _normalize_admin_note(admin_note) if admin_note_provided else None
        now = self._now()
        async with self._database.system_session(_AUTH_ACTOR) as session:
            if status is AccountStatus.ACTIVE:
                await self._acquire_capacity_lock(session)
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
                {"lock_name": _ACCOUNT_ADMIN_LOCK},
            )
            target = await session.scalar(
                select(AccountModel)
                .where(AccountModel.id == account_id)
                .with_for_update(of=AccountModel)
            )
            if target is None:
                raise AccountServiceError(
                    AccountServiceErrorCode.NOT_FOUND,
                    "账号不存在。",
                )

            effective_role = AccountRole(target.role) if role is None else role
            effective_status = AccountStatus(target.status) if status is None else status
            remains_active_admin = (
                effective_role is AccountRole.ADMIN and effective_status is AccountStatus.ACTIVE
            )
            if target.id == actor.account.id and not remains_active_admin:
                raise AccountServiceError(
                    AccountServiceErrorCode.CONFLICT,
                    "不能停用自己的账号或移除自己的管理员权限。",
                )

            currently_active_admin = (
                target.role == AccountRole.ADMIN.value
                and target.status == AccountStatus.ACTIVE.value
                and target.disabled_at is None
            )
            if currently_active_admin and not remains_active_admin:
                active_admins = await session.scalar(
                    select(func.count())
                    .select_from(AccountModel)
                    .where(
                        AccountModel.role == AccountRole.ADMIN.value,
                        AccountModel.status == AccountStatus.ACTIVE.value,
                        AccountModel.disabled_at.is_(None),
                    )
                )
                if (active_admins or 0) <= 1:
                    raise AccountServiceError(
                        AccountServiceErrorCode.CONFLICT,
                        "必须保留至少一个可用的管理员账号。",
                    )

            is_reactivation = (
                target.status == AccountStatus.SUSPENDED.value
                and effective_status is AccountStatus.ACTIVE
            )
            if is_reactivation:
                seat_usage = await self._seat_usage(session, now)
                if seat_usage.occupied_seats >= self._settings.active_account_capacity:
                    self._raise_capacity_reached()

            if role is not None:
                target.role = role.value
            if status is not None:
                target.status = status.value
                target.disabled_at = now if status is AccountStatus.SUSPENDED else None
            if admin_note_provided:
                target.admin_note = normalized_note
            if status is AccountStatus.SUSPENDED:
                await session.execute(
                    update(AccountSessionModel)
                    .where(
                        AccountSessionModel.account_id == target.id,
                        AccountSessionModel.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )
            await session.flush()
            return _account_from_model(target)

    async def create_invitation(
        self,
        actor: AccountIdentity,
        expires_in_days: int = INVITATION_TTL_DAYS,
    ) -> InvitationGrant:
        _require_admin(actor)
        if not 1 <= expires_in_days <= MAX_INVITATION_TTL_DAYS:
            raise AccountServiceError(
                AccountServiceErrorCode.INVALID_INPUT,
                f"邀请有效期必须为 1 到 {MAX_INVITATION_TTL_DAYS} 天。",
            )
        now = self._now()
        code = secrets.token_urlsafe(24)
        model = RegistrationInvitationModel(
            id=new_id(),
            code_hash=_hash_invitation_code(code),
            created_by_account_id=actor.account.id,
            expires_at=now + timedelta(days=expires_in_days),
            created_at=now,
        )
        async with self._database.system_session(_AUTH_ACTOR) as session:
            await self._acquire_capacity_lock(session)
            seat_usage = await self._seat_usage(session, now)
            if seat_usage.occupied_seats >= self._settings.active_account_capacity:
                self._raise_capacity_reached()
            session.add(model)
            await session.flush()
            invitation = _invitation_from_model(model, now)
        return InvitationGrant(invitation=invitation, code=code)

    async def list_invitations(self, actor: AccountIdentity) -> list[RegistrationInvitation]:
        _require_admin(actor)
        now = self._now()
        async with self._database.system_session(_AUTH_ACTOR) as session:
            models = list(
                await session.scalars(
                    select(RegistrationInvitationModel).order_by(
                        RegistrationInvitationModel.created_at.desc(),
                        RegistrationInvitationModel.id.desc(),
                    )
                )
            )
        return [_invitation_from_model(model, now) for model in models]

    async def revoke_invitation(self, actor: AccountIdentity, invitation_id: str) -> None:
        _require_admin(actor)
        now = self._now()
        async with self._database.system_session(_AUTH_ACTOR) as session:
            model = await session.scalar(
                select(RegistrationInvitationModel)
                .where(RegistrationInvitationModel.id == invitation_id)
                .with_for_update(of=RegistrationInvitationModel)
            )
            if model is None:
                raise AccountServiceError(
                    AccountServiceErrorCode.NOT_FOUND,
                    "邀请不存在。",
                )
            if model.used_at is not None:
                raise AccountServiceError(
                    AccountServiceErrorCode.CONFLICT,
                    "已使用的邀请不能撤销。",
                )
            if model.revoked_at is None:
                model.revoked_at = now

    async def diagnostics(self, actor: AccountIdentity) -> AdminDiagnostics:
        _require_admin(actor)
        now = self._now()
        async with self._database.system_session(_AUTH_ACTOR) as session:
            seat_usage = await self._seat_usage(session, now)
            accounts = await session.scalar(select(func.count()).select_from(AccountModel))
            active_sessions = await session.scalar(
                select(func.count())
                .select_from(AccountSessionModel)
                .where(
                    AccountSessionModel.revoked_at.is_(None),
                    AccountSessionModel.expires_at > now,
                )
            )
            courses = await session.scalar(select(func.count()).select_from(CourseModel))
            documents = await session.scalar(select(func.count()).select_from(DocumentModel))
            notes = await session.scalar(select(func.count()).select_from(NoteModel))
        return AdminDiagnostics(
            accounts=accounts or 0,
            active_accounts=seat_usage.active_accounts,
            account_capacity=self._settings.active_account_capacity,
            available_account_seats=max(
                self._settings.active_account_capacity - seat_usage.occupied_seats,
                0,
            ),
            active_sessions=active_sessions or 0,
            courses=courses or 0,
            documents=documents or 0,
            notes=notes or 0,
            app_mode=self._settings.app_mode.value,
            database="postgresql",
            demo_lab_enabled=self._settings.demo_lab_enabled,
        )

    async def _acquire_capacity_lock(self, session: AsyncSession) -> None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": _REGISTRATION_LOCK},
        )

    async def _seat_usage(self, session: AsyncSession, now: datetime) -> SeatUsage:
        active_accounts = (
            select(func.count())
            .select_from(AccountModel)
            .where(AccountModel.status == AccountStatus.ACTIVE.value)
            .scalar_subquery()
        )
        reserved_invitations = (
            select(func.count())
            .select_from(RegistrationInvitationModel)
            .where(
                RegistrationInvitationModel.used_at.is_(None),
                RegistrationInvitationModel.revoked_at.is_(None),
                RegistrationInvitationModel.expires_at > now,
            )
            .scalar_subquery()
        )
        row = (
            await session.execute(
                select(
                    active_accounts.label("active_accounts"),
                    reserved_invitations.label("reserved_invitations"),
                )
            )
        ).one()
        return SeatUsage(
            active_accounts=int(row.active_accounts),
            reserved_invitations=int(row.reserved_invitations),
        )

    @staticmethod
    def _raise_capacity_reached() -> None:
        raise AccountServiceError(
            AccountServiceErrorCode.CAPACITY_REACHED,
            "账号容量已满, 请先停用账号或撤销未使用的邀请码。",
        )

    async def _registration_user(
        self,
        session: AsyncSession,
        account_id: str,
        is_first_account: bool,
    ) -> UserModel:
        if is_first_account:
            user = await session.scalar(
                select(UserModel)
                .where(
                    UserModel.subject == LOCAL_PRINCIPAL_SUBJECT,
                    UserModel.authentication_method == AuthenticationMethod.LOCAL.value,
                )
                .with_for_update(of=UserModel)
            )
            if user is not None:
                return user
            subject = LOCAL_PRINCIPAL_SUBJECT
        else:
            subject = f"account:{account_id}"
        user = UserModel(
            id=new_id(),
            subject=subject,
            authentication_method=AuthenticationMethod.LOCAL.value,
        )
        session.add(user)
        await session.flush()
        return user

    async def _valid_invitation(
        self,
        session: AsyncSession,
        invite_code: str | None,
        now: datetime,
    ) -> RegistrationInvitationModel:
        code_hash = _invitation_code_hash_or_none(invite_code)
        if code_hash is None:
            raise AccountServiceError(
                AccountServiceErrorCode.INVALID_INVITATION,
                "需要有效且未使用的注册邀请。",
            )
        model = await session.scalar(
            select(RegistrationInvitationModel)
            .where(RegistrationInvitationModel.code_hash == code_hash)
            .with_for_update(of=RegistrationInvitationModel)
        )
        if (
            model is None
            or model.used_at is not None
            or model.revoked_at is not None
            or model.expires_at <= now
        ):
            raise AccountServiceError(
                AccountServiceErrorCode.INVALID_INVITATION,
                "需要有效且未使用的注册邀请。",
            )
        return model

    def _add_session(
        self,
        session: AsyncSession,
        account_id: str,
        now: datetime,
    ) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(seconds=SESSION_TTL_SECONDS)
        session.add(
            AccountSessionModel(
                id=new_id(),
                account_id=account_id,
                token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
                expires_at=expires_at,
                created_at=now,
            )
        )
        return token, expires_at

    def _now(self) -> datetime:
        value = self._clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _account_from_model(model: AccountModel) -> Account:
    return Account(
        id=model.id,
        user_id=model.user_id,
        email=model.email,
        display_name=model.display_name,
        role=AccountRole(model.role),
        status=AccountStatus(model.status),
        admin_note=model.admin_note,
        created_at=model.created_at,
    )


def _invitation_from_model(
    model: RegistrationInvitationModel,
    now: datetime,
) -> RegistrationInvitation:
    if model.used_at is not None:
        status = InvitationStatus.USED
    elif model.revoked_at is not None:
        status = InvitationStatus.REVOKED
    elif model.expires_at <= now:
        status = InvitationStatus.EXPIRED
    else:
        status = InvitationStatus.AVAILABLE
    return RegistrationInvitation(
        id=model.id,
        created_by_account_id=model.created_by_account_id,
        used_by_account_id=model.used_by_account_id,
        status=status,
        created_at=model.created_at,
        expires_at=model.expires_at,
        used_at=model.used_at,
        revoked_at=model.revoked_at,
    )


def _normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    if len(normalized) > 320 or not _EMAIL_PATTERN.fullmatch(normalized):
        raise AccountServiceError(
            AccountServiceErrorCode.INVALID_INPUT,
            "邮箱格式无效。",
        )
    return normalized


def _normalize_display_name(value: str) -> str:
    normalized = value.strip()
    if not 1 <= len(normalized) <= 100:
        raise AccountServiceError(
            AccountServiceErrorCode.INVALID_INPUT,
            "显示名称长度必须为 1 到 100 个字符。",
        )
    return normalized


def _normalize_admin_note(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 1000:
        raise AccountServiceError(
            AccountServiceErrorCode.INVALID_INPUT,
            "管理员备注不能超过 1000 个字符。",
        )
    return normalized


def _validate_new_password(value: str) -> str:
    if not 8 <= len(value) <= 256:
        raise AccountServiceError(
            AccountServiceErrorCode.INVALID_INPUT,
            "密码长度必须为 8 到 256 个字符。",
        )
    return value


def _validate_login_password(value: str) -> str:
    if not 1 <= len(value) <= 256:
        raise AccountServiceError(
            AccountServiceErrorCode.INVALID_CREDENTIALS,
            "邮箱或密码错误。",
        )
    return value


def _token_hash_or_none(token: str | None) -> str | None:
    if token is None or not 32 <= len(token) <= 512:
        return None
    try:
        encoded = token.encode("ascii")
    except UnicodeEncodeError:
        return None
    return hashlib.sha256(encoded).hexdigest()


def _invitation_code_hash_or_none(code: str | None) -> str | None:
    if code is None:
        return None
    normalized = code.strip()
    if not 16 <= len(normalized) <= 512:
        return None
    try:
        return _hash_invitation_code(normalized)
    except UnicodeEncodeError:
        return None


def _hash_invitation_code(code: str) -> str:
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def _require_admin(actor: AccountIdentity) -> None:
    if (
        actor.account.role is not AccountRole.ADMIN
        or actor.account.status is not AccountStatus.ACTIVE
    ):
        raise AccountServiceError(
            AccountServiceErrorCode.FORBIDDEN,
            "需要管理员权限。",
        )


__all__ = [
    "INVITATION_TTL_DAYS",
    "MAX_INVITATION_TTL_DAYS",
    "SESSION_TTL_SECONDS",
    "Account",
    "AccountIdentity",
    "AccountRole",
    "AccountService",
    "AccountServiceError",
    "AccountServiceErrorCode",
    "AccountStatus",
    "AdminDiagnostics",
    "InvitationGrant",
    "InvitationStatus",
    "RegistrationInvitation",
    "SessionGrant",
]
