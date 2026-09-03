from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import (
    AuthSession,
    AuditLog,
    InvitationCode,
    Membership,
    User,
    Workspace,
)


@dataclass(frozen=True)
class AuthenticatedIdentity:
    session_id: str
    user_id: str
    user_name: str
    workspace_id: str
    workspace_name: str
    role: str


@dataclass(frozen=True)
class RedeemedToken:
    access_token: str
    expires_at: datetime
    identity: AuthenticatedIdentity


class InviteTokenAuthService:
    token_prefix = "rfs_"
    invitation_prefix = "rfi_"

    def __init__(
        self,
        *,
        secret: str | None,
        session_ttl_seconds: int,
        last_seen_update_seconds: int,
    ):
        if not secret or len(secret.encode("utf-8")) < 32:
            raise RuntimeError(
                "invite_token 模式要求 RIFFLOOM_AUTH_SECRET 至少 32 字节"
            )
        if not 300 <= session_ttl_seconds <= 30 * 24 * 60 * 60:
            raise RuntimeError("认证会话有效期必须在 5 分钟到 30 天之间")
        if not 60 <= last_seen_update_seconds <= session_ttl_seconds:
            raise RuntimeError("会话活跃时间写入间隔配置无效")
        self._secret = secret.encode("utf-8")
        self.session_ttl_seconds = session_ttl_seconds
        self.last_seen_update_seconds = last_seen_update_seconds

    def secret_hash(self, value: str) -> str:
        return hmac.new(
            self._secret,
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def redeem(self, session: Session, invitation_code: str) -> RedeemedToken:
        raw_code = invitation_code.strip()
        if not 12 <= len(raw_code) <= 200:
            raise self._invalid_credentials()
        now = datetime.now(UTC)
        invitation = session.scalar(
            select(InvitationCode).where(
                InvitationCode.code_hash == self.secret_hash(raw_code)
            )
        )
        if not self._invitation_is_usable(invitation, now):
            raise self._invalid_credentials()
        assert invitation is not None
        identity = self._load_identity(
            session,
            session_id="pending",
            user_id=invitation.user_id,
            workspace_id=invitation.workspace_id,
        )

        conditions = [
            InvitationCode.id == invitation.id,
            InvitationCode.status == "active",
            InvitationCode.revoked_at.is_(None),
            InvitationCode.use_count == invitation.use_count,
            InvitationCode.expires_at > now,
        ]
        if invitation.max_uses > 0:
            conditions.append(InvitationCode.use_count < invitation.max_uses)
        claim_statement = (
            update(InvitationCode)
            .where(*conditions)
            .values(
                use_count=invitation.use_count + 1,
                last_used_at=now,
                updated_at=now,
            )
            # SQLite returns timezone-naive datetimes.  Let the database evaluate
            # the expiry predicate instead of SQLAlchemy comparing it in Python.
            .execution_options(synchronize_session=False)
        )
        claimed = session.execute(claim_statement)
        if claimed.rowcount != 1:
            session.rollback()
            raise self._invalid_credentials()

        access_token = self.token_prefix + secrets.token_urlsafe(32)
        expires_at = now + timedelta(seconds=self.session_ttl_seconds)
        auth_session = AuthSession(
            workspace_id=invitation.workspace_id,
            user_id=invitation.user_id,
            invitation_id=invitation.id,
            token_hash=self.secret_hash(access_token),
            status="active",
            expires_at=expires_at,
            last_seen_at=now,
        )
        session.add(auth_session)
        session.flush()
        session.add(
            AuditLog(
                workspace_id=invitation.workspace_id,
                user_id=invitation.user_id,
                action="auth.invitation_redeemed",
                entity_type="AuthSession",
                entity_id=auth_session.id,
                details={"invitation_id": invitation.id},
            )
        )
        session.commit()
        return RedeemedToken(
            access_token=access_token,
            expires_at=expires_at,
            identity=AuthenticatedIdentity(
                session_id=auth_session.id,
                user_id=identity.user_id,
                user_name=identity.user_name,
                workspace_id=identity.workspace_id,
                workspace_name=identity.workspace_name,
                role=identity.role,
            ),
        )

    def authenticate(self, session: Session, access_token: str) -> AuthenticatedIdentity:
        raw_token = access_token.strip()
        if not raw_token.startswith(self.token_prefix) or len(raw_token) > 200:
            raise self._invalid_session()
        auth_session = session.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == self.secret_hash(raw_token)
            )
        )
        now = datetime.now(UTC)
        if (
            auth_session is None
            or auth_session.status != "active"
            or auth_session.revoked_at is not None
            or _as_utc(auth_session.expires_at) <= now
        ):
            if auth_session is not None and auth_session.status == "active":
                auth_session.status = "expired"
                auth_session.updated_at = now
                session.commit()
            raise self._invalid_session()

        identity = self._load_identity(
            session,
            session_id=auth_session.id,
            user_id=auth_session.user_id,
            workspace_id=auth_session.workspace_id,
        )
        if _as_utc(auth_session.last_seen_at) + timedelta(
            seconds=self.last_seen_update_seconds
        ) <= now:
            auth_session.last_seen_at = now
            auth_session.updated_at = now
            session.commit()
        return identity

    def revoke(self, session: Session, access_token: str) -> None:
        auth_session = session.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == self.secret_hash(access_token.strip())
            )
        )
        if auth_session is None or auth_session.status != "active":
            raise self._invalid_session()
        now = datetime.now(UTC)
        auth_session.status = "revoked"
        auth_session.revoked_at = now
        auth_session.updated_at = now
        session.add(
            AuditLog(
                workspace_id=auth_session.workspace_id,
                user_id=auth_session.user_id,
                action="auth.session_revoked",
                entity_type="AuthSession",
                entity_id=auth_session.id,
                details={},
            )
        )
        session.commit()

    @staticmethod
    def _invitation_is_usable(
        invitation: InvitationCode | None, now: datetime
    ) -> bool:
        if (
            invitation is None
            or invitation.status != "active"
            or invitation.revoked_at is not None
            or _as_utc(invitation.expires_at) <= now
        ):
            return False
        return invitation.max_uses == 0 or invitation.use_count < invitation.max_uses

    @staticmethod
    def _load_identity(
        session: Session,
        *,
        session_id: str,
        user_id: str,
        workspace_id: str,
    ) -> AuthenticatedIdentity:
        membership = session.scalar(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.workspace_id == workspace_id,
            )
        )
        user = session.get(User, user_id)
        workspace = session.get(Workspace, workspace_id)
        if (
            membership is None
            or user is None
            or workspace is None
            or user.status != "active"
            or workspace.status != "active"
        ):
            raise InviteTokenAuthService._invalid_session()
        return AuthenticatedIdentity(
            session_id=session_id,
            user_id=user.id,
            user_name=user.name,
            workspace_id=workspace.id,
            workspace_name=workspace.name,
            role=membership.role,
        )

    @staticmethod
    def _invalid_credentials() -> AppError:
        return AppError(
            code="AUTH_CREDENTIAL_INVALID",
            message="邀请码无效、已过期或已达到使用上限",
            status_code=401,
        )

    @staticmethod
    def _invalid_session() -> AppError:
        return AppError(
            code="AUTH_SESSION_INVALID",
            message="登录会话不存在、已过期或已撤销",
            status_code=401,
        )


def issue_invitation_code(
    session: Session,
    *,
    auth_service: InviteTokenAuthService,
    workspace_id: str,
    user_id: str,
    created_by: str,
    expires_at: datetime,
    max_uses: int,
    raw_code: str | None = None,
) -> tuple[InvitationCode, str]:
    if max_uses < 0:
        raise ValueError("邀请码使用上限不能为负数；0 表示撤销前不限次数")
    expires_at = _as_utc(expires_at)
    if expires_at <= datetime.now(UTC):
        raise ValueError("邀请码过期时间必须晚于当前时间")
    workspace = session.get(Workspace, workspace_id)
    target_user = session.get(User, user_id)
    if workspace is None or workspace.status != "active":
        raise ValueError("邀请码必须绑定有效的 Workspace")
    if target_user is None or target_user.status != "active":
        raise ValueError("邀请码必须绑定有效用户")
    membership = session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
        )
    )
    if membership is None:
        raise ValueError("邀请码必须绑定现有 Workspace 成员")
    creator_membership = session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == created_by,
        )
    )
    if creator_membership is None or creator_membership.role != "admin":
        raise ValueError("只有当前 Workspace 管理员可以创建邀请码")
    code = raw_code or (
        InviteTokenAuthService.invitation_prefix + secrets.token_urlsafe(24)
    )
    if not 12 <= len(code) <= 200:
        raise ValueError("邀请码长度必须在 12 到 200 个字符之间")
    invitation = InvitationCode(
        workspace_id=workspace_id,
        user_id=user_id,
        code_hash=auth_service.secret_hash(code),
        status="active",
        max_uses=max_uses,
        use_count=0,
        expires_at=expires_at,
        created_by=created_by,
    )
    session.add(invitation)
    session.flush()
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            user_id=created_by,
            action="auth.invitation_issued",
            entity_type="InvitationCode",
            entity_id=invitation.id,
            details={"target_user_id": user_id, "max_uses": max_uses},
        )
    )
    session.commit()
    return invitation, code


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
