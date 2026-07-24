"""Local account and revocable browser-session persistence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from study_agent.infrastructure.db.base import Base
from study_agent.infrastructure.db.models.core import TimestampMixin, new_id


class AccountModel(TimestampMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("email", name="uq_accounts_email"),
        UniqueConstraint("user_id", name="uq_accounts_user"),
        CheckConstraint("role IN ('admin', 'user')", name="ck_accounts_role"),
        CheckConstraint("status IN ('active', 'suspended')", name="ck_accounts_status"),
        CheckConstraint(
            "(status = 'active' AND disabled_at IS NULL) OR "
            "(status = 'suspended' AND disabled_at IS NOT NULL)",
            name="ck_accounts_status_disabled",
        ),
        CheckConstraint(
            "email = lower(btrim(email)) AND length(email) BETWEEN 3 AND 320 "
            "AND position('@' IN email) > 1",
            name="ck_accounts_email_normalized",
        ),
        CheckConstraint(
            "display_name = btrim(display_name) AND length(display_name) BETWEEN 1 AND 100",
            name="ck_accounts_display_name",
        ),
        CheckConstraint(
            "admin_note IS NULL OR (admin_note = btrim(admin_note) "
            "AND length(admin_note) BETWEEN 1 AND 1000)",
            name="ck_accounts_admin_note",
        ),
        Index("ix_accounts_role_created", "role", "created_at", "id"),
        Index("ix_accounts_status_role", "status", "role"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    admin_note: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountSessionModel(Base):
    __tablename__ = "account_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_account_sessions_token_hash"),
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_account_sessions_token_hash",
        ),
        CheckConstraint("expires_at > created_at", name="ck_account_sessions_expiry"),
        Index(
            "ix_account_sessions_account_active",
            "account_id",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RegistrationInvitationModel(Base):
    __tablename__ = "registration_invitations"
    __table_args__ = (
        UniqueConstraint("code_hash", name="uq_registration_invitations_code_hash"),
        UniqueConstraint("used_by_account_id", name="uq_registration_invitations_used_by"),
        CheckConstraint(
            "code_hash ~ '^[0-9a-f]{64}$'",
            name="ck_registration_invitations_code_hash",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_registration_invitations_expiry",
        ),
        CheckConstraint(
            "used_at IS NULL OR (used_at >= created_at AND used_at < expires_at)",
            name="ck_registration_invitations_used_at",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_registration_invitations_revoked_at",
        ),
        CheckConstraint(
            "(used_at IS NULL AND used_by_account_id IS NULL) OR "
            "(used_at IS NOT NULL AND used_by_account_id IS NOT NULL)",
            name="ck_registration_invitations_used_by",
        ),
        CheckConstraint(
            "used_at IS NULL OR revoked_at IS NULL",
            name="ck_registration_invitations_terminal_state",
        ),
        Index(
            "ix_registration_invitations_active",
            "expires_at",
            "created_at",
            "id",
            postgresql_where=text("used_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    used_by_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["AccountModel", "AccountSessionModel", "RegistrationInvitationModel"]
