from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.config import Settings
from app.core.database import Database
from app.models import AuditLog, InvitationCode, Membership, User, Workspace
from app.services.auth_service import InviteTokenAuthService, issue_invitation_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="管理 Riffloom Pilot 工作区、成员邀请码和管理员登录密钥。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser(
        "bootstrap",
        help="仅在空数据库中创建首个工作区、管理员和可重复使用的登录密钥",
    )
    bootstrap.add_argument("--workspace-id", required=True)
    bootstrap.add_argument("--workspace-name", required=True)
    bootstrap.add_argument("--admin-id", required=True)
    bootstrap.add_argument("--admin-name", required=True)
    bootstrap.add_argument("--expires-hours", required=True, type=int)
    bootstrap.add_argument(
        "--confirmation",
        required=True,
        choices=["bootstrap-empty-pilot-auth"],
    )

    issue = subparsers.add_parser(
        "issue",
        help="由现有管理员为一个成员生成邀请码（明文只输出一次）",
    )
    issue.add_argument("--workspace-id", required=True)
    issue.add_argument("--admin-id", required=True)
    issue.add_argument("--user-id", required=True)
    issue.add_argument("--user-name", required=True)
    issue.add_argument("--role", required=True, choices=["editor", "lead", "admin"])
    issue.add_argument("--expires-hours", required=True, type=int)
    issue.add_argument("--max-uses", type=int, default=1)

    revoke = subparsers.add_parser("revoke", help="撤销一个邀请码")
    revoke.add_argument("--workspace-id", required=True)
    revoke.add_argument("--admin-id", required=True)
    revoke.add_argument("--invitation-id", required=True)

    listing = subparsers.add_parser("list", help="列出邀请码元数据（不显示 hash 或明文）")
    listing.add_argument("--workspace-id", required=True)
    listing.add_argument("--admin-id", required=True)
    return parser


def _auth_service(settings: Settings) -> InviteTokenAuthService:
    if settings.auth_mode != "invite_token":
        raise SystemExit("请先将 RIFFLOOM_AUTH_MODE 设置为 invite_token")
    return InviteTokenAuthService(
        secret=settings.auth_secret,
        session_ttl_seconds=settings.auth_session_ttl_seconds,
        last_seen_update_seconds=settings.auth_last_seen_update_seconds,
    )


def _validate_id(value: str, label: str) -> str:
    normalized = value.strip()
    if not 2 <= len(normalized) <= 64 or any(char.isspace() for char in normalized):
        raise SystemExit(f"{label} 必须为 2～64 位且不能包含空白字符")
    return normalized


def _expires_at(hours: int) -> datetime:
    if not 1 <= hours <= 30 * 24:
        raise SystemExit("邀请码有效期必须在 1 小时到 30 天之间")
    return datetime.now(UTC) + timedelta(hours=hours)


def _require_admin(session, workspace_id: str, admin_id: str) -> None:
    membership = session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == admin_id,
        )
    )
    if membership is None or membership.role != "admin":
        raise SystemExit("指定操作者不是该 Workspace 管理员")


def _print_invitation(invitation: InvitationCode, raw_code: str) -> None:
    print(
        json.dumps(
            {
                "invitation_id": invitation.id,
                "invitation_code": raw_code,
                "workspace_id": invitation.workspace_id,
                "user_id": invitation.user_id,
                "max_uses": invitation.max_uses,
                "expires_at": invitation.expires_at.isoformat(),
                "warning": "邀请码明文只显示这一次；请通过私密渠道交给对应成员。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _bootstrap(session, args, auth_service: InviteTokenAuthService) -> None:
    workspace_count = session.scalar(select(func.count()).select_from(Workspace)) or 0
    membership_count = session.scalar(select(func.count()).select_from(Membership)) or 0
    if workspace_count or membership_count:
        raise SystemExit("bootstrap 只允许在没有 Workspace 和成员的空数据库中执行")
    workspace_id = _validate_id(args.workspace_id, "workspace-id")
    admin_id = _validate_id(args.admin_id, "admin-id")
    workspace = Workspace(
        id=workspace_id,
        name=args.workspace_name.strip(),
        status="active",
        config={},
    )
    admin = User(id=admin_id, name=args.admin_name.strip(), status="active")
    session.add_all([workspace, admin])
    session.flush()
    session.add(
        Membership(
            workspace_id=workspace_id,
            user_id=admin_id,
            role="admin",
        )
    )
    session.commit()
    invitation, raw_code = issue_invitation_code(
        session,
        auth_service=auth_service,
        workspace_id=workspace_id,
        user_id=admin_id,
        created_by=admin_id,
        expires_at=_expires_at(args.expires_hours),
        max_uses=0,
    )
    _print_invitation(invitation, raw_code)


def _issue(session, args, auth_service: InviteTokenAuthService) -> None:
    workspace_id = _validate_id(args.workspace_id, "workspace-id")
    admin_id = _validate_id(args.admin_id, "admin-id")
    user_id = _validate_id(args.user_id, "user-id")
    _require_admin(session, workspace_id, admin_id)
    user = session.get(User, user_id)
    if user is None:
        user = User(id=user_id, name=args.user_name.strip(), status="active")
        session.add(user)
        session.flush()
    elif user.name != args.user_name.strip() or user.status != "active":
        raise SystemExit("现有用户的姓名或状态与本次签发参数不一致")
    membership = session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
        )
    )
    if membership is None:
        session.add(
            Membership(
                workspace_id=workspace_id,
                user_id=user_id,
                role=args.role,
            )
        )
        session.commit()
    elif membership.role != args.role:
        raise SystemExit("现有成员角色与本次签发参数不一致；请先通过成员管理修改角色")
    invitation, raw_code = issue_invitation_code(
        session,
        auth_service=auth_service,
        workspace_id=workspace_id,
        user_id=user_id,
        created_by=admin_id,
        expires_at=_expires_at(args.expires_hours),
        max_uses=args.max_uses,
    )
    _print_invitation(invitation, raw_code)


def _revoke(session, args) -> None:
    workspace_id = _validate_id(args.workspace_id, "workspace-id")
    admin_id = _validate_id(args.admin_id, "admin-id")
    _require_admin(session, workspace_id, admin_id)
    invitation = session.get(InvitationCode, args.invitation_id)
    if invitation is None or invitation.workspace_id != workspace_id:
        raise SystemExit("指定邀请码不存在")
    if invitation.status != "revoked":
        now = datetime.now(UTC)
        invitation.status = "revoked"
        invitation.revoked_at = now
        invitation.updated_at = now
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                user_id=admin_id,
                action="auth.invitation_revoked",
                entity_type="InvitationCode",
                entity_id=invitation.id,
                details={},
            )
        )
        session.commit()
    print(json.dumps({"invitation_id": invitation.id, "status": "revoked"}, ensure_ascii=False))


def _list(session, args) -> None:
    workspace_id = _validate_id(args.workspace_id, "workspace-id")
    admin_id = _validate_id(args.admin_id, "admin-id")
    _require_admin(session, workspace_id, admin_id)
    invitations = session.scalars(
        select(InvitationCode)
        .where(InvitationCode.workspace_id == workspace_id)
        .order_by(InvitationCode.created_at.desc())
    ).all()
    print(
        json.dumps(
            [
                {
                    "invitation_id": invitation.id,
                    "user_id": invitation.user_id,
                    "status": invitation.status,
                    "use_count": invitation.use_count,
                    "max_uses": invitation.max_uses,
                    "expires_at": invitation.expires_at.isoformat(),
                    "last_used_at": invitation.last_used_at.isoformat()
                    if invitation.last_used_at
                    else None,
                }
                for invitation in invitations
            ],
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    args = _parser().parse_args()
    settings = Settings.from_env()
    auth_service = _auth_service(settings)
    database = Database(settings.database_url)
    try:
        with database.session_factory() as session:
            if args.command == "bootstrap":
                _bootstrap(session, args, auth_service)
            elif args.command == "issue":
                _issue(session, args, auth_service)
            elif args.command == "revoke":
                _revoke(session, args)
            else:
                _list(session, args)
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
