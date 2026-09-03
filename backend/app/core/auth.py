from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Membership, User, Workspace


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    workspace_id: str
    role: str
    user_name: str
    workspace_name: str
    auth_mode: str = "demo_headers"
    session_id: str | None = None


def get_db(request: Request):
    yield from request.app.state.database.session()


def resolve_auth_context(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_riffloom_user: str = Header(default="user_demo", alias="X-Riffloom-User"),
    x_riffloom_workspace: str = Header(
        default="ws_demo", alias="X-Riffloom-Workspace"
    ),
) -> AuthContext:
    with request.app.state.database.session_factory() as session:
        if request.app.state.settings.auth_mode == "invite_token":
            access_token = bearer_token(authorization)
            identity = request.app.state.auth_service.authenticate(
                session, access_token
            )
            return AuthContext(
                user_id=identity.user_id,
                workspace_id=identity.workspace_id,
                role=identity.role,
                user_name=identity.user_name,
                workspace_name=identity.workspace_name,
                auth_mode="invite_token",
                session_id=identity.session_id,
            )
        membership = session.scalar(
            select(Membership).where(
                Membership.user_id == x_riffloom_user,
                Membership.workspace_id == x_riffloom_workspace,
            )
        )
        if membership is None:
            raise AppError(
                code="WORKSPACE_ACCESS_DENIED",
                message="当前用户无权访问该 Workspace",
                status_code=403,
            )
        user = session.get(User, x_riffloom_user)
        workspace = session.get(Workspace, x_riffloom_workspace)
        if (
            user is None
            or workspace is None
            or user.status != "active"
            or workspace.status != "active"
        ):
            raise AppError(
                code="SESSION_INVALID",
                message="演示会话不存在或已失效",
                status_code=401,
            )
        return AuthContext(
            user_id=user.id,
            workspace_id=workspace.id,
            role=membership.role,
            user_name=user.name,
            workspace_name=workspace.name,
        )


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise AppError(
            code="AUTH_REQUIRED",
            message="请先使用邀请码登录",
            status_code=401,
        )
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise AppError(
            code="AUTH_HEADER_INVALID",
            message="Authorization 必须使用 Bearer 令牌",
            status_code=401,
        )
    return token.strip()
