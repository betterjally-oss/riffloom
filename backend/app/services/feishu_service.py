from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import AuthContext
from app.core.errors import AppError
from app.core.observability import current_trace_id, new_trace_id
from app.core.permissions import (
    PermissionAction,
    can_run_feishu_sync,
    require_feishu_sync,
    require_permission,
)
from app.models import (
    AgentTask,
    BloggerRecord,
    BreakdownRecord,
    CollectionRecord,
    CreationRecord,
    CreationVersion,
    FeishuConnection,
    FeishuOAuthCredential,
    FeishuRecordLink,
    FeishuSyncBinding,
    FeishuSyncItem,
    FeishuSyncRun,
    Membership,
    PilotMemberWorkspace,
    ProviderCall,
    TaskAttempt,
    Workspace,
)
from app.models.entities import new_id, utcnow
from app.providers import (
    FEISHU_SCOPES,
    FeishuProvider,
    FeishuProviderError,
    FeishuSyncRecord,
)
from app.schemas.api import (
    FeishuBindingCreateInput,
    FeishuBindingPreflightInput,
    FeishuBindingPreflightRead,
    FeishuBindingRead,
    FeishuBindingUpdateInput,
    FeishuConnectionCreateInput,
    FeishuConnectionRead,
    FeishuSyncItemRead,
    FeishuSyncRunRead,
)
from app.services.task_service import add_audit, get_task

# ponytail: one process-wide refresh lock is enough for the single-instance Pilot;
# use per-connection distributed locks only when the deployment model changes.
_oauth_refresh_lock = threading.Lock()


REQUIRED_SYSTEM_FIELDS = (
    "riffloom_record_id",
    "riffloom_record_version",
    "last_synced_at",
    "riffloom_status",
)
DEFAULT_FIELD_MAPPING = {
    "riffloom_record_id": "riffloom_record_id",
    "riffloom_record_version": "riffloom_record_version",
    "last_synced_at": "last_synced_at",
    "riffloom_status": "riffloom_status",
    "title": "标题",
    "summary": "摘要",
    "tags": "标签",
}
_SCOPE_RETURN_TO = {
    "breakdown": "/breakdowns",
    "creation": "/creations",
}


def _digest(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _provider_app_error(error: FeishuProviderError) -> AppError:
    return AppError(error.code, error.message, error.status_code, error.retryable)


def _is_isolated_workspace_owner(
    session: Session, workspace_id: str, user_id: str
) -> bool:
    return (
        session.scalar(
            select(PilotMemberWorkspace.id).where(
                PilotMemberWorkspace.workspace_id == workspace_id,
                PilotMemberWorkspace.member_user_id == user_id,
            )
        )
        is not None
    )


def can_manage_feishu(session: Session, context: AuthContext) -> bool:
    del session
    return context.role in {"editor", "lead", "admin"}


def _require_feishu_management(
    session: Session, context: AuthContext, action: PermissionAction
) -> None:
    if not can_manage_feishu(session, context):
        require_permission(context, action)


def connection_to_read(connection: FeishuConnection) -> FeishuConnectionRead:
    return FeishuConnectionRead(
        id=connection.id,
        provider=connection.provider,
        tenant_key=connection.tenant_key,
        tenant_name=connection.tenant_name,
        auth_type=connection.auth_type,
        scopes=list(connection.scopes or []),
        status=connection.status,
        expires_at=connection.expires_at,
        created_by=connection.created_by,
        created_at=connection.created_at,
    )


def oauth_authorization_url(
    session: Session,
    context: AuthContext,
    provider: FeishuProvider,
    scope_key: str,
) -> str:
    _require_feishu_management(
        session, context, PermissionAction.FEISHU_CONNECTION_MANAGE
    )
    if scope_key not in FEISHU_SCOPES or not getattr(provider, "oauth_enabled", False):
        raise AppError("FEISHU_OAUTH_DISABLED", "当前飞书用户授权不可用", 409)
    try:
        return provider.oauth_authorization_url(
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            scope_key=scope_key,
            return_to=_SCOPE_RETURN_TO.get(scope_key, "/collections"),
        )
    except FeishuProviderError as error:
        raise _provider_app_error(error) from error


def complete_oauth_connection(
    session: Session,
    context: AuthContext,
    provider: FeishuProvider,
    *,
    code: str,
    state: str,
) -> str:
    _require_feishu_management(
        session, context, PermissionAction.FEISHU_CONNECTION_MANAGE
    )
    if not getattr(provider, "oauth_enabled", False):
        raise AppError("FEISHU_OAUTH_DISABLED", "当前飞书用户授权不可用", 409)
    try:
        state_payload = provider.verify_oauth_state(state)
        if (
            state_payload.get("workspace_id") != context.workspace_id
            or state_payload.get("user_id") != context.user_id
        ):
            raise FeishuProviderError(
                "FEISHU_OAUTH_STATE_INVALID",
                "飞书授权与当前登录用户不匹配",
                status_code=403,
            )
        grant = provider.exchange_oauth_code(code)
        user_info = provider.oauth_user_info(grant.access_token)
    except FeishuProviderError as error:
        raise _provider_app_error(error) from error

    connection = session.scalar(
        select(FeishuConnection).where(
            FeishuConnection.workspace_id == context.workspace_id,
            FeishuConnection.created_by == context.user_id,
        )
    )
    identity_changed = (
        connection is not None and connection.tenant_key != user_info["open_id"]
    )
    if connection is None:
        connection = FeishuConnection(
            id=new_id("fconn"),
            workspace_id=context.workspace_id,
            provider=provider.provider_id,
            tenant_key=user_info["open_id"],
            tenant_name=user_info["name"],
            auth_type="user_oauth",
            scopes=list(grant.scopes),
            status="active",
            credential_ref="pending://oauth",
            expires_at=grant.access_expires_at,
            created_by=context.user_id,
        )
        session.add(connection)
        session.flush()
    else:
        connection.provider = provider.provider_id
        connection.tenant_key = user_info["open_id"]
        connection.tenant_name = user_info["name"]
        connection.auth_type = "user_oauth"
        connection.scopes = list(grant.scopes)
        connection.status = "active"
        connection.expires_at = grant.access_expires_at
    connection.credential_ref = f"db://feishu-oauth/{connection.id}"
    credential = session.get(FeishuOAuthCredential, connection.id)
    encrypted = {
        "access_token_encrypted": provider.encrypt_token(grant.access_token),
        "refresh_token_encrypted": provider.encrypt_token(grant.refresh_token),
        "access_expires_at": grant.access_expires_at,
        "refresh_expires_at": grant.refresh_expires_at,
    }
    if credential is None:
        credential = FeishuOAuthCredential(connection_id=connection.id, **encrypted)
        session.add(credential)
    else:
        for key, value in encrypted.items():
            setattr(credential, key, value)
    bindings = session.scalars(
        select(FeishuSyncBinding).where(
            FeishuSyncBinding.workspace_id == context.workspace_id,
            FeishuSyncBinding.created_by == context.user_id,
            FeishuSyncBinding.connection_id == connection.id,
        )
    ).all()
    for binding in bindings:
        if identity_changed or not provider.accepts_target(
            binding.target_base_id, binding.target_table_id
        ):
            binding.status = "connection_invalid"
        elif binding.status == "connection_invalid":
            binding.status = "active"
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="feishu.connection.oauth_connected",
        entity_type="FeishuConnection",
        entity_id=connection.id,
        details={"provider": provider.provider_id, "auth_type": "user_oauth"},
    )
    session.commit()
    return f"{state_payload.get('return_to') or '/collections'}?" + urlencode(
        {"feishu": "connected", "scope": str(state_payload.get("scope_key") or "")}
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _user_access_token(
    session: Session, connection: FeishuConnection, provider: FeishuProvider
) -> str:
    credential = session.get(FeishuOAuthCredential, connection.id)
    if credential is None:
        connection.status = "expired"
        session.commit()
        raise AppError(
            "FEISHU_CONNECTION_INVALID", "当前用户尚未授权飞书，请重新绑定", 409
        )
    now = datetime.now(timezone.utc)
    if _aware(credential.access_expires_at) > now + timedelta(minutes=2):
        try:
            return provider.decrypt_token(credential.access_token_encrypted)
        except FeishuProviderError as error:
            connection.status = "expired"
            session.commit()
            raise _provider_app_error(error) from error
    with _oauth_refresh_lock:
        session.refresh(credential)
        if _aware(credential.access_expires_at) > datetime.now(
            timezone.utc
        ) + timedelta(minutes=2):
            try:
                return provider.decrypt_token(credential.access_token_encrypted)
            except FeishuProviderError as error:
                connection.status = "expired"
                session.commit()
                raise _provider_app_error(error) from error
        if _aware(credential.refresh_expires_at) <= datetime.now(timezone.utc):
            connection.status = "expired"
            session.commit()
            raise AppError("FEISHU_AUTH_EXPIRED", "飞书授权已过期，请重新绑定", 401)
        try:
            grant = provider.refresh_oauth_grant(
                provider.decrypt_token(credential.refresh_token_encrypted)
            )
        except FeishuProviderError as error:
            if error.code == "FEISHU_AUTH_EXPIRED":
                connection.status = "expired"
                session.commit()
            raise _provider_app_error(error) from error
        credential.access_token_encrypted = provider.encrypt_token(grant.access_token)
        credential.refresh_token_encrypted = provider.encrypt_token(grant.refresh_token)
        credential.access_expires_at = grant.access_expires_at
        credential.refresh_expires_at = grant.refresh_expires_at
        connection.expires_at = grant.access_expires_at
        connection.scopes = list(grant.scopes)
        session.commit()
        return grant.access_token


def list_connections(
    session: Session, context: AuthContext
) -> list[FeishuConnectionRead]:
    _require_feishu_management(
        session, context, PermissionAction.FEISHU_CONNECTION_MANAGE
    )
    rows = session.scalars(
        select(FeishuConnection).where(
            FeishuConnection.workspace_id == context.workspace_id,
            FeishuConnection.created_by == context.user_id,
        )
    ).all()
    return [connection_to_read(row) for row in rows]


def create_connection(
    session: Session,
    context: AuthContext,
    payload: FeishuConnectionCreateInput,
    provider: FeishuProvider,
) -> FeishuConnectionRead:
    _require_feishu_management(
        session, context, PermissionAction.FEISHU_CONNECTION_MANAGE
    )
    if getattr(provider, "oauth_enabled", False):
        raise AppError("FEISHU_OAUTH_REQUIRED", "请点击“连接我的飞书”完成本人授权", 409)
    try:
        metadata = provider.connection_metadata(
            workspace_id=context.workspace_id,
            tenant_name=payload.tenant_name,
        )
    except FeishuProviderError as error:
        raise _provider_app_error(error) from error
    connection = session.scalar(
        select(FeishuConnection).where(
            FeishuConnection.workspace_id == context.workspace_id,
            FeishuConnection.created_by == context.user_id,
        )
    )
    if connection is None:
        connection = FeishuConnection(
            id=new_id("fconn"),
            workspace_id=context.workspace_id,
            provider=provider.provider_id,
            tenant_key=str(metadata["tenant_key"]),
            tenant_name=str(metadata["tenant_name"]),
            auth_type=str(metadata["auth_type"]),
            scopes=list(metadata["scopes"]),
            status="active",
            credential_ref=str(metadata["credential_ref"]),
            created_by=context.user_id,
        )
        session.add(connection)
    else:
        connection.tenant_key = str(metadata["tenant_key"])
        connection.tenant_name = str(metadata["tenant_name"])
        connection.auth_type = str(metadata["auth_type"])
        connection.provider = provider.provider_id
        connection.status = "active"
        connection.scopes = list(metadata["scopes"])
        connection.credential_ref = str(metadata["credential_ref"])
        connection.expires_at = None
        invalid_bindings = session.scalars(
            select(FeishuSyncBinding).where(
                FeishuSyncBinding.workspace_id == context.workspace_id,
                FeishuSyncBinding.connection_id == connection.id,
                FeishuSyncBinding.status == "connection_invalid",
            )
        ).all()
        for binding in invalid_bindings:
            binding.status = "active"
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="feishu.connection.connected",
        entity_type="FeishuConnection",
        entity_id=connection.id,
        details={
            "provider": provider.provider_id,
            "external_calls": provider.external_calls,
        },
    )
    session.commit()
    session.refresh(connection)
    return connection_to_read(connection)


def disconnect_connection(
    session: Session,
    context: AuthContext,
    connection_id: str,
    retain_external_copies_confirmed: bool,
) -> FeishuConnectionRead:
    _require_feishu_management(
        session, context, PermissionAction.FEISHU_CONNECTION_MANAGE
    )
    if not retain_external_copies_confirmed:
        raise AppError(
            "FEISHU_DISCONNECT_CONFIRMATION_REQUIRED",
            "必须确认断开连接不会删除已写入的外部副本",
            422,
        )
    connection = _get_connection(
        session, context.workspace_id, context.user_id, connection_id
    )
    connection.status = "disconnected"
    connection.credential_ref = "disconnected://cleared"
    credential = session.get(FeishuOAuthCredential, connection.id)
    if credential is not None:
        session.delete(credential)
    bindings = session.scalars(
        select(FeishuSyncBinding).where(
            FeishuSyncBinding.workspace_id == context.workspace_id,
            FeishuSyncBinding.connection_id == connection.id,
        )
    ).all()
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="feishu.connection.disconnected",
        entity_type="FeishuConnection",
        entity_id=connection.id,
        details={"remote_copies_deleted": False, "binding_count": len(bindings)},
    )
    session.commit()
    session.refresh(connection)
    return connection_to_read(connection)


def _get_connection(
    session: Session, workspace_id: str, user_id: str, connection_id: str
) -> FeishuConnection:
    connection = session.scalar(
        select(FeishuConnection).where(
            FeishuConnection.id == connection_id,
            FeishuConnection.workspace_id == workspace_id,
            FeishuConnection.created_by == user_id,
        )
    )
    if connection is None:
        raise AppError(
            "FEISHU_CONNECTION_NOT_FOUND", "当前 Workspace 中不存在该飞书连接", 404
        )
    return connection


def require_active_connection(
    session: Session, workspace_id: str, user_id: str, connection_id: str
) -> FeishuConnection:
    connection = _get_connection(session, workspace_id, user_id, connection_id)
    if connection.status != "active":
        raise AppError("FEISHU_CONNECTION_INVALID", "飞书连接已失效或断开", 409)
    return connection


def get_targets(
    session: Session,
    context: AuthContext,
    connection_id: str,
    provider: FeishuProvider,
    *,
    base_url: str | None = None,
    scope_key: str | None = None,
) -> list[dict[str, object]]:
    _require_feishu_management(
        session, context, PermissionAction.FEISHU_CONNECTION_MANAGE
    )
    connection = require_active_connection(
        session, context.workspace_id, context.user_id, connection_id
    )
    try:
        if getattr(provider, "oauth_enabled", False):
            return provider.targets(
                access_token=_user_access_token(session, connection, provider),
                app_token=base_url,
                scope_key=scope_key,
            )
        return provider.targets()
    except FeishuProviderError as error:
        raise _provider_app_error(error) from error


def preflight_binding(
    session: Session,
    context: AuthContext,
    payload: FeishuBindingPreflightInput,
    provider: FeishuProvider,
) -> FeishuBindingPreflightRead:
    _require_feishu_management(session, context, PermissionAction.FEISHU_BINDING_MANAGE)
    connection = require_active_connection(
        session, context.workspace_id, context.user_id, payload.connection_id
    )
    mapping = {**DEFAULT_FIELD_MAPPING, **payload.field_mapping}
    errors: list[dict[str, str]] = []
    if payload.scope_key not in FEISHU_SCOPES:
        errors.append(
            {"code": "FEISHU_SCOPE_INVALID", "message": "同步 scope 不受支持"}
        )
    try:
        if getattr(provider, "oauth_enabled", False):
            targets = provider.targets(
                access_token=_user_access_token(session, connection, provider),
                app_token=payload.target_base_id,
                scope_key=payload.scope_key,
            )
        else:
            targets = provider.targets()
    except FeishuProviderError as error:
        raise _provider_app_error(error) from error
    target = next(
        (
            table
            for base in targets
            if base["base_id"] == payload.target_base_id
            for table in base["tables"]
            if table["table_id"] == payload.target_table_id
        ),
        None,
    )
    if target is None or target.get("scope_key") != payload.scope_key:
        errors.append(
            {
                "code": "FEISHU_TARGET_INVALID",
                "message": "目标数据表与同步 scope 不匹配",
            }
        )
    elif isinstance(target.get("fields"), list):
        remote_fields = {
            str(field.get("name"))
            for field in target["fields"]
            if isinstance(field, dict) and field.get("name")
        }
        for remote_name in mapping.values():
            if remote_name and remote_name not in remote_fields:
                errors.append(
                    {
                        "code": "FEISHU_FIELD_MISSING",
                        "message": f"目标表缺少字段：{remote_name}",
                    }
                )
    for field in REQUIRED_SYSTEM_FIELDS:
        if not mapping.get(field, "").strip():
            errors.append(
                {
                    "code": "FEISHU_FIELD_REQUIRED",
                    "message": f"缺少系统字段映射：{field}",
                }
            )
    if "[field-error]" in payload.target_table_name:
        errors.append(
            {
                "code": "FEISHU_FIELD_TYPE_MISMATCH",
                "message": "sandbox 模拟字段类型不兼容",
            }
        )
    sample = _source_records(session, context.workspace_id, payload.scope_key)[:1]
    return FeishuBindingPreflightRead(
        valid=not errors,
        scope_key=payload.scope_key,
        field_mapping=mapping,
        required_fields=list(REQUIRED_SYSTEM_FIELDS),
        sample=_map_fields(sample[0].fields, mapping) if sample else {},
        errors=errors,
        provider=provider.provider_id,
        external_calls=provider.external_calls,
    )


def create_binding(
    session: Session,
    context: AuthContext,
    payload: FeishuBindingCreateInput,
    provider: FeishuProvider,
) -> FeishuSyncBinding:
    _require_feishu_management(session, context, PermissionAction.FEISHU_BINDING_MANAGE)
    if getattr(provider, "oauth_enabled", False):
        connection = require_active_connection(
            session, context.workspace_id, context.user_id, payload.connection_id
        )
        try:
            provider.ensure_target_fields(
                access_token=_user_access_token(session, connection, provider),
                app_token=payload.target_base_id,
                table_id=payload.target_table_id,
                field_names=set(DEFAULT_FIELD_MAPPING.values())
                | {value for value in payload.field_mapping.values() if value},
            )
        except FeishuProviderError as error:
            raise _provider_app_error(error) from error
    preflight = preflight_binding(session, context, payload, provider)
    if not preflight.valid:
        raise AppError(
            "FEISHU_PREFLIGHT_FAILED",
            f"飞书字段预检未通过：{preflight.errors[0]['message']}",
            422,
        )
    duplicate_target = session.scalar(
        select(FeishuSyncBinding).where(
            FeishuSyncBinding.workspace_id == context.workspace_id,
            FeishuSyncBinding.created_by == context.user_id,
            FeishuSyncBinding.scope_key != payload.scope_key,
            FeishuSyncBinding.target_base_id == payload.target_base_id,
            FeishuSyncBinding.target_table_id == payload.target_table_id,
        )
    )
    if duplicate_target is not None:
        raise AppError(
            "FEISHU_TARGET_ALREADY_BOUND", "每类资料库必须绑定不同的飞书数据表", 409
        )
    existing = session.scalar(
        select(FeishuSyncBinding).where(
            FeishuSyncBinding.workspace_id == context.workspace_id,
            FeishuSyncBinding.created_by == context.user_id,
            FeishuSyncBinding.scope_key == payload.scope_key,
        )
    )
    if existing is not None:
        target_changed = (
            existing.target_base_id != payload.target_base_id
            or existing.target_table_id != payload.target_table_id
        )
        links = session.scalars(
            select(FeishuRecordLink).where(FeishuRecordLink.binding_id == existing.id)
        ).all()
        if (
            target_changed
            and links
            and not (
                existing.target_base_id == "base_sandbox_riffloom"
                and all(link.remote_record_id.startswith("rec_sbx_") for link in links)
            )
        ):
            raise AppError(
                "FEISHU_TARGET_CHANGE_BLOCKED",
                "已有真实同步记录时不能直接切换目标表",
                409,
            )
        if target_changed and links:
            for link in links:
                session.delete(link)
            existing.cursor = {}
            existing.last_synced_at = None
        binding = existing
    else:
        binding = FeishuSyncBinding(
            id=new_id("fbind"),
            workspace_id=context.workspace_id,
            connection_id=payload.connection_id,
            scope_key=payload.scope_key,
            target_base_id=payload.target_base_id,
            target_table_id=payload.target_table_id,
            target_table_name=payload.target_table_name,
            field_mapping=preflight.field_mapping,
            strategy=payload.strategy,
            cursor={},
            status="active",
            created_by=context.user_id,
        )
        session.add(binding)
    binding.connection_id = payload.connection_id
    binding.target_base_id = payload.target_base_id
    binding.target_table_id = payload.target_table_id
    binding.target_table_name = payload.target_table_name
    binding.field_mapping = preflight.field_mapping
    binding.strategy = payload.strategy
    binding.status = "active"
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="feishu.binding.configured",
        entity_type="FeishuSyncBinding",
        entity_id=binding.id,
        details={
            "scope_key": binding.scope_key,
            "target_base_id": binding.target_base_id,
            "target_table_id": binding.target_table_id,
            "provider": provider.provider_id,
            "external_calls": provider.external_calls,
        },
    )
    session.commit()
    session.refresh(binding)
    return binding


def _get_binding(
    session: Session, workspace_id: str, user_id: str, binding_id: str
) -> FeishuSyncBinding:
    binding = session.scalar(
        select(FeishuSyncBinding).where(
            FeishuSyncBinding.id == binding_id,
            FeishuSyncBinding.workspace_id == workspace_id,
            FeishuSyncBinding.created_by == user_id,
        )
    )
    if binding is None:
        raise AppError(
            "FEISHU_BINDING_NOT_FOUND", "当前 Workspace 中不存在该飞书绑定", 404
        )
    return binding


def update_binding(
    session: Session,
    context: AuthContext,
    binding_id: str,
    payload: FeishuBindingUpdateInput,
    provider: FeishuProvider,
) -> FeishuSyncBinding:
    _require_feishu_management(session, context, PermissionAction.FEISHU_BINDING_MANAGE)
    binding = _get_binding(session, context.workspace_id, context.user_id, binding_id)
    require_active_connection(
        session, context.workspace_id, context.user_id, binding.connection_id
    )
    if payload.paused is not None:
        binding.status = "paused" if payload.paused else "active"
    if payload.target_table_name is not None or payload.field_mapping is not None:
        preflight_payload = FeishuBindingPreflightInput(
            connection_id=binding.connection_id,
            scope_key=binding.scope_key,
            target_base_id=binding.target_base_id,
            target_table_id=binding.target_table_id,
            target_table_name=payload.target_table_name or binding.target_table_name,
            field_mapping=payload.field_mapping or binding.field_mapping,
        )
        preflight = preflight_binding(session, context, preflight_payload, provider)
        if not preflight.valid:
            raise AppError(
                "FEISHU_PREFLIGHT_FAILED",
                f"飞书字段预检未通过：{preflight.errors[0]['message']}",
                422,
            )
        binding.target_table_name = preflight_payload.target_table_name
        binding.field_mapping = preflight.field_mapping
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="feishu.binding.updated",
        entity_type="FeishuSyncBinding",
        entity_id=binding.id,
        details={"status": binding.status, "scope_key": binding.scope_key},
    )
    session.commit()
    session.refresh(binding)
    return binding


def sync_item_to_read(item: FeishuSyncItem) -> FeishuSyncItemRead:
    return FeishuSyncItemRead(
        id=item.id,
        source_record_id=item.source_record_id,
        source_version=item.source_version,
        action=item.action,
        status=item.status,
        remote_record_id=item.remote_record_id,
        error=item.error,
        retry_count=item.retry_count,
    )


def sync_run_to_read(
    session: Session, run: FeishuSyncRun, *, include_items: bool = True
) -> FeishuSyncRunRead:
    items = (
        session.scalars(
            select(FeishuSyncItem)
            .where(FeishuSyncItem.run_id == run.id)
            .order_by(FeishuSyncItem.created_at, FeishuSyncItem.id)
        ).all()
        if include_items
        else []
    )
    return FeishuSyncRunRead(
        id=run.id,
        binding_id=run.binding_id,
        task_id=run.task_id,
        parent_run_id=run.parent_run_id,
        mode=run.mode,
        status=run.status,
        total_count=run.total_count,
        success_count=run.success_count,
        failed_count=run.failed_count,
        skipped_count=run.skipped_count,
        cursor_before=dict(run.cursor_before or {}),
        cursor_after=dict(run.cursor_after or {}),
        error=run.error,
        items=[sync_item_to_read(item) for item in items],
        created_by=run.created_by,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def binding_to_read(
    session: Session,
    context: AuthContext,
    binding: FeishuSyncBinding,
    provider: FeishuProvider,
) -> FeishuBindingRead:
    connection = _get_connection(
        session, context.workspace_id, context.user_id, binding.connection_id
    )
    last_run = session.scalar(
        select(FeishuSyncRun)
        .where(FeishuSyncRun.binding_id == binding.id)
        .order_by(FeishuSyncRun.created_at.desc())
    )
    link_count = (
        session.scalar(
            select(func.count(FeishuRecordLink.id)).where(
                FeishuRecordLink.binding_id == binding.id
            )
        )
        or 0
    )
    workspace = session.get(Workspace, context.workspace_id)
    config = dict(workspace.config or {}) if workspace else {}
    effective_status = (
        "connection_invalid" if connection.status != "active" else binding.status
    )
    target_accepted = provider.accepts_target(
        binding.target_base_id, binding.target_table_id
    )
    has_user_grant = (
        not getattr(provider, "oauth_enabled", False)
        or session.get(FeishuOAuthCredential, connection.id) is not None
    )
    can_manage = can_manage_feishu(session, context)
    return FeishuBindingRead(
        id=binding.id,
        connection_id=binding.connection_id,
        scope_key=binding.scope_key,
        target_base_id=binding.target_base_id,
        target_table_id=binding.target_table_id,
        target_table_name=binding.target_table_name,
        field_mapping=dict(binding.field_mapping or {}),
        strategy=binding.strategy,
        cursor=dict(binding.cursor or {}),
        status=effective_status,
        last_synced_at=binding.last_synced_at,
        link_count=link_count,
        last_run=(
            sync_run_to_read(session, last_run, include_items=False)
            if last_run
            else None
        ),
        can_configure=can_manage,
        can_sync=provider.writes_enabled
        and target_accepted
        and has_user_grant
        and (can_manage or can_run_feishu_sync(context.role, config)),
        provider=provider.provider_id,
        is_sandbox=provider.is_sandbox,
        external_calls=provider.external_calls,
        target_openable=provider.target_web_url(
            binding.target_base_id, binding.target_table_id
        )
        is not None,
    )


def get_binding_target_url(
    session: Session,
    context: AuthContext,
    binding_id: str,
    provider: FeishuProvider,
) -> str:
    require_permission(context, PermissionAction.FEISHU_SYNC_VIEW)
    binding = _get_binding(session, context.workspace_id, context.user_id, binding_id)
    target_url = provider.target_web_url(
        binding.target_base_id, binding.target_table_id
    )
    if target_url is None:
        raise AppError(
            "FEISHU_TARGET_NOT_OPENABLE", "当前绑定没有可打开的真实飞书目标", 409
        )
    return target_url


def list_bindings(
    session: Session, context: AuthContext, provider: FeishuProvider
) -> list[FeishuBindingRead]:
    require_permission(context, PermissionAction.FEISHU_SYNC_VIEW)
    rows = session.scalars(
        select(FeishuSyncBinding)
        .where(
            FeishuSyncBinding.workspace_id == context.workspace_id,
            FeishuSyncBinding.created_by == context.user_id,
        )
        .order_by(FeishuSyncBinding.scope_key)
    ).all()
    if getattr(provider, "oauth_enabled", False):
        rows = [
            row for row in rows if not row.target_base_id.startswith("base_feishu_")
        ]
    return [binding_to_read(session, context, row, provider) for row in rows]


def get_sync_run(
    session: Session, context: AuthContext, run_id: str
) -> FeishuSyncRunRead:
    require_permission(context, PermissionAction.FEISHU_SYNC_VIEW)
    run = session.scalar(
        select(FeishuSyncRun).where(
            FeishuSyncRun.id == run_id,
            FeishuSyncRun.workspace_id == context.workspace_id,
            FeishuSyncRun.created_by == context.user_id,
        )
    )
    if run is None:
        raise AppError(
            "FEISHU_SYNC_NOT_FOUND", "当前 Workspace 中不存在该同步运行", 404
        )
    return sync_run_to_read(session, run)


def _require_sync_role(session: Session, context: AuthContext) -> None:
    if can_manage_feishu(session, context):
        return
    workspace = session.get(Workspace, context.workspace_id)
    config = dict(workspace.config or {}) if workspace else {}
    require_feishu_sync(context, config)


def create_sync_run(
    session: Session,
    context: AuthContext,
    binding_id: str,
    mode: str,
    idempotency_key: str,
    provider: FeishuProvider,
    *,
    source_record_id: str | None = None,
    parent_run: FeishuSyncRun | None = None,
) -> tuple[AgentTask, FeishuSyncRun, bool]:
    _require_sync_role(session, context)
    if not provider.writes_enabled:
        raise AppError(
            "FEISHU_REAL_WRITE_DISABLED",
            "真实飞书写入开关仍关闭；完成只读预检并确认测试表后才能开启",
            409,
        )
    binding = _get_binding(session, context.workspace_id, context.user_id, binding_id)
    if binding.status != "active":
        raise AppError("FEISHU_BINDING_PAUSED", "飞书绑定已暂停或不可用", 409)
    require_active_connection(
        session, context.workspace_id, context.user_id, binding.connection_id
    )
    if source_record_id is not None and not any(
        source.id == source_record_id
        for source in _source_records(session, context.workspace_id, binding.scope_key)
    ):
        raise AppError(
            "FEISHU_SYNC_SOURCE_NOT_FOUND", "当前同步范围中不存在该记录", 404
        )
    request = {
        "binding_id": binding.id,
        "scope_key": binding.scope_key,
        "mode": mode,
        **(
            {"source_record_id": source_record_id}
            if source_record_id is not None
            else {}
        ),
        "parent_run_id": parent_run.id if parent_run else None,
    }
    request_hash = _digest(request)
    existing = session.scalar(
        select(FeishuSyncRun).where(
            FeishuSyncRun.workspace_id == context.workspace_id,
            FeishuSyncRun.created_by == context.user_id,
            FeishuSyncRun.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise AppError(
                "IDEMPOTENCY_CONFLICT", "该幂等键已用于不同飞书同步输入", 409
            )
        return (
            get_task(session, context.workspace_id, existing.task_id),
            existing,
            False,
        )

    run_id = new_id("frun")
    task = AgentTask(
        workspace_id=context.workspace_id,
        conversation_id=None,
        created_by=context.user_id,
        type="feishu_sync",
        mode="integration",
        skill_id="feishu_sync",
        status="queued",
        stage="飞书同步已受理",
        progress=5,
        input_snapshot={
            **request,
            "provider": provider.provider_id,
            "external_calls": provider.external_calls,
        },
        result_refs=[{"type": "feishu_sync_run", "id": run_id}],
        result_summary={"total": 0, "success": 0, "failed": 0, "skipped": 0},
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        trace_id=current_trace_id() or new_trace_id(),
        current_attempt=1,
    )
    session.add(task)
    session.flush()
    session.add(
        TaskAttempt(
            task_id=task.id,
            attempt_no=1,
            status="queued",
            stage=task.stage,
            progress=5,
        )
    )
    run = FeishuSyncRun(
        id=run_id,
        workspace_id=context.workspace_id,
        binding_id=binding.id,
        task_id=task.id,
        parent_run_id=parent_run.id if parent_run else None,
        mode=mode,
        status="queued",
        cursor_before=dict(binding.cursor or {}),
        cursor_after=dict(binding.cursor or {}),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        created_by=context.user_id,
    )
    session.add(run)
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="feishu.sync.created",
        entity_type="FeishuSyncRun",
        entity_id=run.id,
        details={
            **request,
            "provider": provider.provider_id,
            "external_calls": provider.external_calls,
        },
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(FeishuSyncRun).where(
                FeishuSyncRun.workspace_id == context.workspace_id,
                FeishuSyncRun.created_by == context.user_id,
                FeishuSyncRun.idempotency_key == idempotency_key,
            )
        )
        if existing is None or existing.request_hash != request_hash:
            raise AppError("IDEMPOTENCY_CONFLICT", "飞书同步创建发生幂等冲突", 409)
        return (
            get_task(session, context.workspace_id, existing.task_id),
            existing,
            False,
        )
    return get_task(session, context.workspace_id, task.id), run, True


def retry_sync_run(
    session: Session,
    context: AuthContext,
    run_id: str,
    idempotency_key: str,
    provider: FeishuProvider,
) -> tuple[AgentTask, FeishuSyncRun, bool]:
    parent = session.scalar(
        select(FeishuSyncRun).where(
            FeishuSyncRun.id == run_id,
            FeishuSyncRun.workspace_id == context.workspace_id,
            FeishuSyncRun.created_by == context.user_id,
        )
    )
    if parent is None:
        raise AppError(
            "FEISHU_SYNC_NOT_FOUND", "当前 Workspace 中不存在该同步运行", 404
        )
    failed_items = session.scalars(
        select(FeishuSyncItem).where(
            FeishuSyncItem.run_id == parent.id,
            FeishuSyncItem.status == "failed",
        )
    ).all()
    if not failed_items:
        raise AppError("FEISHU_SYNC_NOT_RETRYABLE", "该同步运行没有失败项", 409)
    if not any(bool((item.error or {}).get("retryable")) for item in failed_items):
        raise AppError(
            "FEISHU_SYNC_NOT_RETRYABLE",
            "失败项不可重试，请修复连接或目标后重新同步",
            409,
        )
    return create_sync_run(
        session,
        context,
        parent.binding_id,
        "retry_failed",
        idempotency_key,
        provider,
        parent_run=parent,
    )


@dataclass(frozen=True)
class _SourceRecord:
    id: str
    version: str
    updated_at: datetime
    fields: dict[str, Any]


def _source_records(
    session: Session, workspace_id: str, scope_key: str
) -> list[_SourceRecord]:
    records: list[_SourceRecord] = []
    if (
        scope_key.startswith("collection.")
        and scope_key != "collection.creator_profile"
    ):
        kind = scope_key.split(".", 1)[1]
        rows = session.scalars(
            select(CollectionRecord).where(
                CollectionRecord.workspace_id == workspace_id,
                CollectionRecord.collection_kind == kind,
            )
        ).all()
        records = [
            _SourceRecord(
                id=row.id,
                version=f"v{row.version}",
                updated_at=row.updated_at,
                fields={
                    "riffloom_record_id": row.id,
                    "riffloom_record_version": f"v{row.version}",
                    "last_synced_at": utcnow().isoformat(),
                    "riffloom_status": "deleted" if row.deleted_at else row.status,
                    "title": row.title,
                    "summary": row.body[:800],
                    "tags": ", ".join(row.tags or []),
                },
            )
            for row in rows
        ]
    elif scope_key == "collection.creator_profile":
        rows = session.scalars(
            select(BloggerRecord).where(BloggerRecord.workspace_id == workspace_id)
        ).all()
        records = [
            _SourceRecord(
                id=row.id,
                version=f"updated:{row.updated_at.isoformat()}",
                updated_at=row.updated_at,
                fields={
                    "riffloom_record_id": row.id,
                    "riffloom_record_version": f"updated:{row.updated_at.isoformat()}",
                    "last_synced_at": utcnow().isoformat(),
                    "riffloom_status": "deleted" if row.deleted_at else "completed",
                    "title": row.name,
                    "summary": row.bio[:800],
                    "tags": ", ".join(row.tags or []),
                },
            )
            for row in rows
        ]
    elif scope_key == "breakdown":
        rows = session.scalars(
            select(BreakdownRecord).where(BreakdownRecord.workspace_id == workspace_id)
        ).all()
        records = [
            _SourceRecord(
                id=row.id,
                version=f"v{row.version}",
                updated_at=row.updated_at,
                fields={
                    "riffloom_record_id": row.id,
                    "riffloom_record_version": f"v{row.version}",
                    "last_synced_at": utcnow().isoformat(),
                    "riffloom_status": "deleted" if row.deleted_at else row.status,
                    "title": row.title,
                    "summary": row.hook[:800],
                    "tags": ", ".join(row.reusable_methods or []),
                },
            )
            for row in rows
        ]
    elif scope_key == "creation":
        rows = session.scalars(
            select(CreationRecord).where(CreationRecord.workspace_id == workspace_id)
        ).all()
        for row in rows:
            version = (
                session.get(CreationVersion, row.current_version_id)
                if row.current_version_id
                else None
            )
            if version is None or version.workspace_id != workspace_id:
                continue
            records.append(
                _SourceRecord(
                    id=row.id,
                    version=f"v{version.version}",
                    updated_at=version.created_at,
                    fields={
                        "riffloom_record_id": row.id,
                        "riffloom_record_version": f"v{version.version}",
                        "last_synced_at": utcnow().isoformat(),
                        "riffloom_status": "deleted" if row.deleted_at else row.status,
                        "title": row.title,
                        "summary": version.summary[:800] or version.body[:800],
                        "tags": ", ".join(version.topics or []),
                    },
                )
            )
    records.sort(key=lambda record: (record.updated_at, record.id))
    return records


def _map_fields(fields: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    return {
        remote_name: fields[local_name]
        for local_name, remote_name in mapping.items()
        if local_name in fields and remote_name
    }


def _finish_precondition_failure(
    session: Session,
    *,
    task: AgentTask,
    run: FeishuSyncRun,
    attempt_no: int,
    code: str,
    message: str,
    retryable: bool = False,
) -> None:
    now = utcnow()
    error = {"code": code, "message": message, "retryable": retryable}
    run.status = "failed"
    run.error = error
    run.finished_at = now
    task.status = "failed"
    task.stage = "飞书同步前置校验失败"
    task.progress = 100
    task.error = error
    task.finished_at = now
    attempt = session.scalar(
        select(TaskAttempt).where(
            TaskAttempt.task_id == task.id,
            TaskAttempt.attempt_no == attempt_no,
        )
    )
    if attempt is None:
        raise AppError("TASK_ATTEMPT_NOT_FOUND", "飞书同步 attempt 不存在", 500)
    attempt.status = "failed"
    attempt.stage = task.stage
    attempt.progress = 100
    attempt.error = error
    attempt.finished_at = now
    add_audit(
        session,
        workspace_id=task.workspace_id,
        user_id=task.created_by,
        action="feishu.sync.blocked",
        entity_type="FeishuSyncRun",
        entity_id=run.id,
        details={"code": code, "binding_id": run.binding_id, "external_calls": False},
    )
    session.commit()


def execute_sync_run(
    session: Session,
    *,
    task: AgentTask,
    attempt_no: int,
    provider: FeishuProvider,
) -> None:
    run = session.scalar(select(FeishuSyncRun).where(FeishuSyncRun.task_id == task.id))
    if run is None:
        raise AppError("FEISHU_SYNC_NOT_FOUND", "同步任务缺少运行记录", 500)
    binding = session.get(FeishuSyncBinding, run.binding_id)
    if (
        binding is None
        or binding.workspace_id != task.workspace_id
        or binding.created_by != task.created_by
    ):
        raise AppError("FEISHU_BINDING_NOT_FOUND", "同步任务缺少绑定", 404)
    connection = session.get(FeishuConnection, binding.connection_id)
    if (
        connection is None
        or connection.workspace_id != task.workspace_id
        or connection.created_by != task.created_by
    ):
        raise AppError("FEISHU_CONNECTION_NOT_FOUND", "同步任务缺少连接", 404)
    workspace = session.get(Workspace, task.workspace_id)
    membership = session.scalar(
        select(Membership).where(
            Membership.workspace_id == task.workspace_id,
            Membership.user_id == task.created_by,
        )
    )
    config = dict(workspace.config or {}) if workspace else {}
    if membership is None or not (
        membership.role in {"editor", "lead", "admin"}
        and binding.created_by == task.created_by
        or can_run_feishu_sync(membership.role, config)
        or _is_isolated_workspace_owner(session, task.workspace_id, task.created_by)
    ):
        _finish_precondition_failure(
            session,
            task=task,
            run=run,
            attempt_no=attempt_no,
            code="FEISHU_SYNC_FORBIDDEN",
            message="任务执行前角色权限已失效",
        )
        return
    if binding.status != "active":
        _finish_precondition_failure(
            session,
            task=task,
            run=run,
            attempt_no=attempt_no,
            code="FEISHU_BINDING_PAUSED",
            message="任务执行前飞书绑定已暂停或不可用",
        )
        return
    if connection.status != "active":
        _finish_precondition_failure(
            session,
            task=task,
            run=run,
            attempt_no=attempt_no,
            code="FEISHU_CONNECTION_INVALID",
            message="任务执行前飞书连接已失效或断开",
        )
        return
    access_token: str | None = None
    if getattr(provider, "oauth_enabled", False):
        try:
            access_token = _user_access_token(session, connection, provider)
        except AppError as error:
            _finish_precondition_failure(
                session,
                task=task,
                run=run,
                attempt_no=attempt_no,
                code=error.code,
                message=error.message,
                retryable=error.retryable,
            )
            return
    run.status = "running"
    session.commit()
    sources = _source_records(session, task.workspace_id, binding.scope_key)
    source_record_id = task.input_snapshot.get("source_record_id")
    if isinstance(source_record_id, str):
        sources = [source for source in sources if source.id == source_record_id]
        if not sources:
            _finish_precondition_failure(
                session,
                task=task,
                run=run,
                attempt_no=attempt_no,
                code="FEISHU_SYNC_SOURCE_NOT_FOUND",
                message="任务执行前目标记录已不在当前同步范围",
            )
            return
    if run.mode == "retry_failed" and run.parent_run_id:
        failed_ids = set(
            session.scalars(
                select(FeishuSyncItem.source_record_id).where(
                    FeishuSyncItem.run_id == run.parent_run_id,
                    FeishuSyncItem.status == "failed",
                )
            ).all()
        )
        sources = [source for source in sources if source.id in failed_ids]
    links = {
        link.source_record_id: link
        for link in session.scalars(
            select(FeishuRecordLink).where(FeishuRecordLink.binding_id == binding.id)
        ).all()
    }
    pending: list[_SourceRecord] = []
    if run.mode == "incremental":
        for source in sources:
            link = links.get(source.id)
            if link is not None and link.source_version == source.version:
                session.add(
                    FeishuSyncItem(
                        workspace_id=task.workspace_id,
                        run_id=run.id,
                        source_record_id=source.id,
                        source_version=source.version,
                        action="skip",
                        status="skipped",
                        remote_record_id=link.remote_record_id,
                    )
                )
            else:
                pending.append(source)
    else:
        pending = sources

    if len(pending) > provider.max_records_per_run:
        _finish_precondition_failure(
            session,
            task=task,
            run=run,
            attempt_no=attempt_no,
            code="FEISHU_SMOKE_SCOPE_EXCEEDED",
            message=f"当前 Provider 单次运行最多处理 {provider.max_records_per_run} 条记录",
        )
        return

    provider_attempt = attempt_no + (1 if run.mode == "retry_failed" else 0)
    batch_size = int(provider.capabilities()["batch_size"])
    external_calls_made = False
    for offset in range(0, len(pending), batch_size):
        session.refresh(task)
        if task.status == "cancelled":
            return
        chunk = pending[offset : offset + batch_size]
        oauth_args = {"access_token": access_token} if access_token else {}
        batch = provider.upsert(
            binding_id=binding.id,
            target_base_id=binding.target_base_id,
            target_table_id=binding.target_table_id,
            target_table_name=binding.target_table_name,
            attempt_no=provider_attempt,
            records=[
                FeishuSyncRecord(
                    source_record_id=source.id,
                    source_version=source.version,
                    fields=_map_fields(
                        source.fields, dict(binding.field_mapping or {})
                    ),
                    existing_remote_record_id=(
                        links.get(source.id).remote_record_id
                        if links.get(source.id)
                        else None
                    ),
                )
                for source in chunk
            ],
            **oauth_args,
        )
        external_calls_made = external_calls_made or batch.external_calls
        success_count = sum(outcome.status == "success" for outcome in batch.outcomes)
        failed_count = sum(outcome.status == "failed" for outcome in batch.outcomes)
        first_batch_error = next(
            (outcome.error for outcome in batch.outcomes if outcome.error),
            None,
        )
        session.add(
            ProviderCall(
                task_id=task.id,
                workspace_id=task.workspace_id,
                attempt_no=attempt_no,
                provider=batch.provider,
                operation="feishu.records.batch_upsert",
                provider_request_id=batch.request_id,
                status=(
                    "failed"
                    if failed_count and not success_count
                    else "partial_success" if failed_count else "success"
                ),
                error_type=(first_batch_error or {}).get("code"),
                result_count=success_count,
            )
        )
        source_by_id = {source.id: source for source in chunk}
        for outcome in batch.outcomes:
            session.add(
                FeishuSyncItem(
                    workspace_id=task.workspace_id,
                    run_id=run.id,
                    source_record_id=outcome.source_record_id,
                    source_version=outcome.source_version,
                    action=outcome.action,
                    status=outcome.status,
                    remote_record_id=outcome.remote_record_id,
                    error=outcome.error,
                    retry_count=1 if run.mode == "retry_failed" else 0,
                )
            )
            if outcome.status != "success" or outcome.remote_record_id is None:
                if outcome.error and outcome.error.get("code") == "FEISHU_AUTH_EXPIRED":
                    connection.status = "expired"
                continue
            link = links.get(outcome.source_record_id)
            if link is None:
                link = FeishuRecordLink(
                    workspace_id=task.workspace_id,
                    binding_id=binding.id,
                    source_record_id=outcome.source_record_id,
                    remote_record_id=outcome.remote_record_id,
                    source_version=outcome.source_version,
                    remote_fields=outcome.fields,
                    status="synced",
                    last_synced_at=utcnow(),
                )
                session.add(link)
                links[outcome.source_record_id] = link
            else:
                link.source_version = outcome.source_version
                link.remote_fields = outcome.fields
                link.status = "synced"
                link.last_synced_at = utcnow()
            source = source_by_id[outcome.source_record_id]
            run.cursor_after = {
                "updated_at": source.updated_at.isoformat(),
                "record_id": source.id,
            }
        session.commit()

    session.refresh(task)
    if task.status == "cancelled":
        return
    session.flush()
    items = session.scalars(
        select(FeishuSyncItem).where(FeishuSyncItem.run_id == run.id)
    ).all()
    run.total_count = len(items)
    run.success_count = sum(item.status == "success" for item in items)
    run.failed_count = sum(item.status == "failed" for item in items)
    run.skipped_count = sum(item.status == "skipped" for item in items)
    run.status = (
        "success"
        if run.failed_count == 0
        else "partial_success" if run.success_count or run.skipped_count else "failed"
    )
    run.finished_at = utcnow()
    first_error = next((item.error for item in items if item.error), None)
    run.error = first_error
    if run.success_count or run.status == "success":
        binding.cursor = dict(run.cursor_after or {})
        binding.last_synced_at = run.finished_at
    task.status = run.status
    task.stage = (
        "飞书同步完成"
        if run.status == "success"
        else "飞书同步部分完成" if run.status == "partial_success" else "飞书同步失败"
    )
    task.progress = 100
    task.finished_at = run.finished_at
    task.result_summary = {
        "provider": provider.provider_id,
        "scope_key": binding.scope_key,
        "mode": run.mode,
        "total": run.total_count,
        "success": run.success_count,
        "failed": run.failed_count,
        "skipped": run.skipped_count,
        "external_calls": external_calls_made,
    }
    task.error = (
        {
            **(
                first_error or {"code": "FEISHU_SYNC_FAILED", "message": "飞书同步失败"}
            ),
            "retryable": any(
                bool((item.error or {}).get("retryable")) for item in items
            ),
        }
        if run.failed_count
        else None
    )
    attempt = session.scalar(
        select(TaskAttempt).where(
            TaskAttempt.task_id == task.id,
            TaskAttempt.attempt_no == attempt_no,
        )
    )
    if attempt is None:
        raise AppError("TASK_ATTEMPT_NOT_FOUND", "飞书同步 attempt 不存在", 500)
    attempt.status = run.status
    attempt.stage = task.stage
    attempt.progress = 100
    attempt.error = task.error
    attempt.finished_at = run.finished_at
    add_audit(
        session,
        workspace_id=task.workspace_id,
        user_id=task.created_by,
        action=(
            "feishu.sync.completed"
            if run.status == "success"
            else (
                "feishu.sync.partial"
                if run.status == "partial_success"
                else "feishu.sync.failed"
            )
        ),
        entity_type="FeishuSyncRun",
        entity_id=run.id,
        details={**task.result_summary, "binding_id": binding.id},
    )
    session.commit()
