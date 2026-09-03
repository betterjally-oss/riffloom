from __future__ import annotations

from enum import StrEnum

from app.core.auth import AuthContext
from app.core.errors import AppError


class PermissionAction(StrEnum):
    TASK_CREATE = "task.create"
    COLLECTION_CREATE = "collection.create"
    CREATION_ADOPT = "creation.adopt"
    CREATION_EDIT = "creation.edit"
    LIBRARY_DELETE = "library.delete"
    TREND_CREATE = "trend.create"
    COVER_CREATE = "cover.create"
    COVER_READ = "cover.read"
    COVER_SAVE = "cover.save"
    FEISHU_CONNECTION_MANAGE = "feishu.connection.manage"
    FEISHU_BINDING_MANAGE = "feishu.binding.manage"
    FEISHU_SYNC_VIEW = "feishu.sync.view"
    FEISHU_SYNC_RUN = "feishu.sync.run"
    WORKSPACE_MEMBERS_READ = "workspace.members.read"
    WORKSPACE_MEMBERS_CHANGE = "workspace.members.change"
    PILOT_METRICS_READ = "pilot.metrics.read"


_AUTHORING_ROLES = frozenset({"editor", "lead", "admin"})
_ROLE_GRANTS: dict[PermissionAction, frozenset[str]] = {
    PermissionAction.TASK_CREATE: _AUTHORING_ROLES,
    PermissionAction.COLLECTION_CREATE: _AUTHORING_ROLES,
    PermissionAction.CREATION_ADOPT: _AUTHORING_ROLES,
    PermissionAction.CREATION_EDIT: _AUTHORING_ROLES,
    PermissionAction.LIBRARY_DELETE: _AUTHORING_ROLES,
    PermissionAction.TREND_CREATE: _AUTHORING_ROLES,
    PermissionAction.COVER_CREATE: _AUTHORING_ROLES,
    PermissionAction.COVER_READ: _AUTHORING_ROLES,
    PermissionAction.COVER_SAVE: _AUTHORING_ROLES,
    PermissionAction.FEISHU_CONNECTION_MANAGE: _AUTHORING_ROLES,
    PermissionAction.FEISHU_BINDING_MANAGE: _AUTHORING_ROLES,
    PermissionAction.FEISHU_SYNC_VIEW: _AUTHORING_ROLES,
    PermissionAction.FEISHU_SYNC_RUN: frozenset({"lead", "admin"}),
    PermissionAction.WORKSPACE_MEMBERS_READ: frozenset({"admin"}),
    PermissionAction.WORKSPACE_MEMBERS_CHANGE: frozenset({"admin"}),
    PermissionAction.PILOT_METRICS_READ: frozenset({"admin"}),
}


def can(role: str, action: PermissionAction) -> bool:
    return role in _ROLE_GRANTS[action]


def require_permission(context: AuthContext, action: PermissionAction) -> None:
    if not can(context.role, action):
        raise AppError(
            code="ROLE_FORBIDDEN",
            message="当前角色无权执行该操作",
            status_code=403,
        )


def can_run_feishu_sync(role: str, workspace_config: dict[str, object]) -> bool:
    configured = workspace_config.get("feishu_manual_sync_roles", ["admin", "lead"])
    roles = configured if isinstance(configured, list) else ["admin", "lead"]
    return role in {str(item) for item in roles}


def require_feishu_sync(
    context: AuthContext, workspace_config: dict[str, object]
) -> None:
    if not can_run_feishu_sync(context.role, workspace_config):
        raise AppError(
            code="FEISHU_SYNC_FORBIDDEN",
            message="当前角色无权手动同步飞书",
            status_code=403,
        )
