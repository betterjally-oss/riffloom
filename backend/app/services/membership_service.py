from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.auth import AuthContext
from app.core.errors import AppError
from app.core.permissions import PermissionAction, require_permission
from app.models import (
    AuthSession,
    InvitationCode,
    Membership,
    PilotMemberWorkspace,
    User,
    Workspace,
    new_id,
)
from app.schemas.api import WorkspaceMemberInvitationRead, WorkspaceMemberRead
from app.services.auth_service import InviteTokenAuthService, issue_invitation_code
from app.services.task_service import add_audit


def _member_read(
    membership: Membership, user: User, *, is_isolated: bool = False
) -> WorkspaceMemberRead:
    return WorkspaceMemberRead(
        membership_id=membership.id,
        user_id=user.id,
        user_name=user.name,
        role=membership.role,
        status=user.status,
        is_isolated=is_isolated,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


def _workspace_member(
    session: Session, context: AuthContext, membership_id: str
) -> tuple[Membership, User, bool]:
    row = session.execute(
        select(Membership, User, PilotMemberWorkspace)
        .join(User, User.id == Membership.user_id)
        .outerjoin(
            PilotMemberWorkspace,
            and_(
                PilotMemberWorkspace.workspace_id == Membership.workspace_id,
                PilotMemberWorkspace.member_user_id == User.id,
            ),
        )
        .where(
            Membership.id == membership_id,
            or_(
                Membership.workspace_id == context.workspace_id,
                PilotMemberWorkspace.parent_workspace_id == context.workspace_id,
            ),
        )
    ).one_or_none()
    if row is None:
        raise AppError("MEMBERSHIP_NOT_FOUND", "当前 Workspace 中不存在该成员关系", 404)
    membership, user, managed_workspace = row
    return membership, user, managed_workspace is not None


def _active_admin_count(session: Session, workspace_id: str) -> int:
    return int(
        session.scalar(
            select(func.count(Membership.id))
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.workspace_id == workspace_id,
                Membership.role == "admin",
                User.status == "active",
            )
        )
        or 0
    )


def list_workspace_members(
    session: Session, context: AuthContext
) -> list[WorkspaceMemberRead]:
    require_permission(context, PermissionAction.WORKSPACE_MEMBERS_READ)
    rows = session.execute(
        select(Membership, User, PilotMemberWorkspace)
        .join(User, User.id == Membership.user_id)
        .outerjoin(
            PilotMemberWorkspace,
            and_(
                PilotMemberWorkspace.workspace_id == Membership.workspace_id,
                PilotMemberWorkspace.member_user_id == User.id,
            ),
        )
        .where(
            or_(
                Membership.workspace_id == context.workspace_id,
                PilotMemberWorkspace.parent_workspace_id == context.workspace_id,
            )
        )
        .order_by(Membership.created_at, Membership.id)
    ).all()
    return [
        _member_read(
            membership,
            user,
            is_isolated=managed_workspace is not None,
        )
        for membership, user, managed_workspace in rows
    ]


def _ensure_workspace_admin(
    session: Session, *, workspace_id: str, user_id: str
) -> None:
    membership = session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
        )
    )
    if membership is None:
        session.add(
            Membership(workspace_id=workspace_id, user_id=user_id, role="admin")
        )
    elif membership.role != "admin":
        membership.role = "admin"
    session.flush()


def _revoke_workspace_access(
    session: Session, *, workspace_id: str, user_id: str
) -> None:
    now = datetime.now(UTC)
    session.execute(
        update(AuthSession)
        .where(
            AuthSession.workspace_id == workspace_id,
            AuthSession.user_id == user_id,
            AuthSession.status == "active",
        )
        .values(status="revoked", revoked_at=now, updated_at=now)
    )
    session.execute(
        update(InvitationCode)
        .where(
            InvitationCode.workspace_id == workspace_id,
            InvitationCode.user_id == user_id,
            InvitationCode.status == "active",
        )
        .values(status="revoked", revoked_at=now, updated_at=now)
    )


def _isolate_workspace_member(
    session: Session,
    context: AuthContext,
    membership: Membership,
    user: User,
) -> None:
    if membership.user_id == context.user_id:
        return
    previous_workspace_id = membership.workspace_id
    workspace = Workspace(
        id=new_id("ws"),
        name=f"{user.name} · 独立空间",
        status="active",
        config={},
    )
    session.add(workspace)
    session.flush()
    session.add(
        PilotMemberWorkspace(
            workspace_id=workspace.id,
            parent_workspace_id=context.workspace_id,
            member_user_id=user.id,
        )
    )
    _ensure_workspace_admin(
        session,
        workspace_id=workspace.id,
        user_id=context.user_id,
    )
    _revoke_workspace_access(
        session,
        workspace_id=previous_workspace_id,
        user_id=user.id,
    )
    membership.workspace_id = workspace.id
    session.flush()
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="workspace.member_isolated",
        entity_type="Membership",
        entity_id=membership.id,
        details={
            "target_user_id": user.id,
            "target_workspace_id": workspace.id,
        },
    )


def update_workspace_member(
    session: Session,
    context: AuthContext,
    membership_id: str,
    role: str | None,
    status: str | None,
) -> WorkspaceMemberRead:
    require_permission(context, PermissionAction.WORKSPACE_MEMBERS_CHANGE)
    membership, user, is_isolated = _workspace_member(session, context, membership_id)
    next_role = role or membership.role
    next_status = status or user.status
    if membership.role == next_role and user.status == next_status:
        return _member_read(membership, user, is_isolated=is_isolated)
    if is_isolated and next_role == "admin":
        raise AppError(
            "PILOT_MEMBER_ROLE_INVALID",
            "独立测试成员只能使用内容成员或内容负责人角色",
            409,
        )
    if (
        membership.role == "admin"
        and user.status == "active"
        and (next_role != "admin" or next_status != "active")
    ):
        if _active_admin_count(session, membership.workspace_id) <= 1:
            raise AppError(
                "LAST_ADMIN_REQUIRED",
                "工作区至少需要保留一名管理员",
                409,
            )

    if user.status != next_status and next_status == "disabled":
        membership_count = int(
            session.scalar(
                select(func.count(Membership.id)).where(Membership.user_id == user.id)
            )
            or 0
        )
        if membership_count > 1:
            raise AppError(
                "MEMBER_HAS_OTHER_WORKSPACES",
                "该账号还属于其他 Workspace，不能在此处全局停用",
                409,
            )

    previous_role = membership.role
    previous_status = user.status
    membership.role = next_role
    user.status = next_status
    if previous_status != next_status and next_status == "disabled":
        _revoke_workspace_access(
            session,
            workspace_id=membership.workspace_id,
            user_id=user.id,
        )
    if previous_role != next_role:
        add_audit(
            session,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            action="workspace.member_role_changed",
            entity_type="Membership",
            entity_id=membership.id,
            details={
                "target_user_id": membership.user_id,
                "from_role": previous_role,
                "to_role": next_role,
            },
        )
    if previous_status != next_status:
        add_audit(
            session,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            action="workspace.member_status_changed",
            entity_type="Membership",
            entity_id=membership.id,
            details={
                "target_user_id": membership.user_id,
                "from_status": previous_status,
                "to_status": next_status,
            },
        )
    session.commit()
    session.refresh(membership)
    return _member_read(membership, user, is_isolated=is_isolated)


def issue_workspace_member_invitation(
    session: Session,
    context: AuthContext,
    auth_service: InviteTokenAuthService,
    *,
    membership_id: str,
    expires_in_hours: int,
) -> WorkspaceMemberInvitationRead:
    require_permission(context, PermissionAction.WORKSPACE_MEMBERS_CHANGE)
    membership, user, is_isolated = _workspace_member(session, context, membership_id)
    if user.status != "active":
        raise AppError("MEMBER_DISABLED", "请先启用该成员，再生成邀请码", 409)
    if not is_isolated and membership.role == "admin" and user.id != context.user_id:
        raise AppError(
            "PILOT_MEMBER_ROLE_INVALID",
            "请先将测试成员调整为内容成员或内容负责人，再迁移到独立空间",
            409,
        )
    if not is_isolated and membership.user_id != context.user_id:
        _isolate_workspace_member(session, context, membership, user)
        is_isolated = True
    if is_isolated:
        _ensure_workspace_admin(
            session,
            workspace_id=membership.workspace_id,
            user_id=context.user_id,
        )
    now = datetime.now(UTC)
    session.execute(
        update(InvitationCode)
        .where(
            InvitationCode.workspace_id == membership.workspace_id,
            InvitationCode.user_id == user.id,
            InvitationCode.status == "active",
        )
        .values(status="revoked", revoked_at=now, updated_at=now)
    )
    invitation, raw_code = issue_invitation_code(
        session,
        auth_service=auth_service,
        workspace_id=membership.workspace_id,
        user_id=user.id,
        created_by=context.user_id,
        expires_at=now + timedelta(hours=expires_in_hours),
        max_uses=(
            0
            if membership.user_id == context.user_id and membership.role == "admin"
            else 1
        ),
    )
    return WorkspaceMemberInvitationRead(
        member=_member_read(membership, user, is_isolated=is_isolated),
        invitation_id=invitation.id,
        invitation_code=raw_code,
        expires_at=invitation.expires_at,
    )


def create_workspace_member(
    session: Session,
    context: AuthContext,
    auth_service: InviteTokenAuthService,
    *,
    user_name: str,
    role: str,
    expires_in_hours: int,
) -> WorkspaceMemberInvitationRead:
    require_permission(context, PermissionAction.WORKSPACE_MEMBERS_CHANGE)
    user = User(id=new_id("user"), name=user_name, status="active")
    session.add(user)
    session.flush()
    membership = Membership(
        workspace_id=context.workspace_id,
        user_id=user.id,
        role=role,
    )
    session.add(membership)
    session.flush()
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="workspace.member_created",
        entity_type="Membership",
        entity_id=membership.id,
        details={"target_user_id": user.id, "role": role},
    )
    return issue_workspace_member_invitation(
        session,
        context,
        auth_service,
        membership_id=membership.id,
        expires_in_hours=expires_in_hours,
    )
