from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urlparse

import httpx

from app.services.credential_store import (
    FEISHU_SELF_BUILT_CREDENTIAL_REF,
    CredentialStoreError,
    EnvCredentialStore,
    FeishuTokenCipher,
)

FEISHU_SCOPES = (
    "collection.single",
    "collection.keyword",
    "collection.creator_content",
    "collection.creator_profile",
    "breakdown",
    "creation",
)
FEISHU_OAUTH_SCOPES = "offline_access bitable:app"


@dataclass(frozen=True)
class FeishuSyncRecord:
    source_record_id: str
    source_version: str
    fields: dict[str, Any]
    existing_remote_record_id: str | None = None


@dataclass(frozen=True)
class FeishuSyncOutcome:
    source_record_id: str
    source_version: str
    action: str
    status: str
    remote_record_id: str | None
    fields: dict[str, Any]
    error: dict[str, Any] | None = None


@dataclass(frozen=True)
class FeishuSyncBatch:
    request_id: str
    provider: str
    outcomes: tuple[FeishuSyncOutcome, ...]
    external_calls: bool = False


@dataclass(frozen=True)
class FeishuOAuthGrant:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    scopes: tuple[str, ...]


class FeishuProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 502,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable

    def as_item_error(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


class FeishuProvider(Protocol):
    provider_id: str
    is_sandbox: bool
    external_calls: bool
    writes_enabled: bool
    max_records_per_run: int

    def capabilities(self) -> dict[str, object]: ...

    def connection_metadata(
        self, *, workspace_id: str, tenant_name: str
    ) -> dict[str, object]: ...

    def targets(self) -> list[dict[str, object]]: ...

    def accepts_target(self, target_base_id: str, target_table_id: str) -> bool: ...

    def target_web_url(
        self, target_base_id: str, target_table_id: str
    ) -> str | None: ...

    def upsert(
        self,
        *,
        binding_id: str,
        target_base_id: str,
        target_table_id: str,
        target_table_name: str,
        attempt_no: int,
        records: list[FeishuSyncRecord],
    ) -> FeishuSyncBatch: ...

    def close(self) -> None: ...


class SandboxFeishuProvider:
    provider_id = "sandbox-feishu-v1"
    is_sandbox = True
    external_calls = False
    writes_enabled = True
    max_records_per_run = 1_000_000

    _table_names = {
        "collection.single": "单篇采集库",
        "collection.keyword": "关键词采集库",
        "collection.creator_content": "博主内容库",
        "collection.creator_profile": "博主信息库",
        "breakdown": "拆解库",
        "creation": "创作库",
    }

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider_id,
            "mode": "deterministic_sandbox",
            "is_sandbox": True,
            "external_calls": False,
            "writes_enabled": True,
            "credential_storage": "sandbox_reference",
            "auth_mode": "sandbox",
            "scopes": list(FEISHU_SCOPES),
            "batch_size": 100,
            "supports_full": True,
            "supports_incremental": True,
            "supports_retry_failed": True,
        }

    def connection_metadata(
        self, *, workspace_id: str, tenant_name: str
    ) -> dict[str, object]:
        tenant_hash = hashlib.sha256(workspace_id.encode()).hexdigest()[:16]
        return {
            "tenant_key": f"tenant_sbx_{tenant_hash}",
            "tenant_name": tenant_name,
            "auth_type": "tenant_self_built_sandbox",
            "scopes": ["bitable:app", "bitable:table", "bitable:record"],
            "credential_ref": f"sandbox-credential://{tenant_hash}",
        }

    def targets(self) -> list[dict[str, object]]:
        fields = [
            {"name": "riffloom_record_id", "type": "text", "readonly": True},
            {"name": "riffloom_record_version", "type": "text", "readonly": True},
            {"name": "last_synced_at", "type": "datetime", "readonly": True},
            {"name": "riffloom_status", "type": "text", "readonly": True},
            {"name": "标题", "type": "text", "readonly": False},
            {"name": "摘要", "type": "text", "readonly": False},
            {"name": "标签", "type": "text", "readonly": False},
        ]
        return [
            {
                "base_id": "base_sandbox_riffloom",
                "base_name": "Riffloom 内容工作台（sandbox）",
                "tables": [
                    {
                        "table_id": f"table_{scope.replace('.', '_')}",
                        "table_name": name,
                        "scope_key": scope,
                        "fields": fields,
                    }
                    for scope, name in self._table_names.items()
                ],
            }
        ]

    def accepts_target(self, target_base_id: str, target_table_id: str) -> bool:
        return target_base_id == "base_sandbox_riffloom" and target_table_id in {
            f"table_{scope.replace('.', '_')}" for scope in FEISHU_SCOPES
        }

    def target_web_url(self, target_base_id: str, target_table_id: str) -> None:
        del target_base_id, target_table_id
        return None

    def upsert(
        self,
        *,
        binding_id: str,
        target_base_id: str,
        target_table_id: str,
        target_table_name: str,
        attempt_no: int,
        records: list[FeishuSyncRecord],
    ) -> FeishuSyncBatch:
        del target_base_id, target_table_id
        outcomes: list[FeishuSyncOutcome] = []
        global_error: tuple[str, str, bool] | None = None
        if "[auth-expired]" in target_table_name:
            global_error = ("FEISHU_AUTH_EXPIRED", "sandbox 模拟授权失效", False)
        elif "[target-deleted]" in target_table_name:
            global_error = (
                "FEISHU_TARGET_DELETED",
                "sandbox 模拟目标数据表已删除",
                False,
            )
        elif "[rate-limit]" in target_table_name and attempt_no == 1:
            global_error = ("FEISHU_RATE_LIMITED", "sandbox 模拟限流，请重试", True)

        partial_id = (
            sorted(record.source_record_id for record in records)[-1]
            if records and "[partial]" in target_table_name and attempt_no == 1
            else None
        )
        for record in records:
            error = global_error
            if record.source_record_id == partial_id:
                error = ("FEISHU_RECORD_REJECTED", "sandbox 模拟单条字段拒绝", True)
            if error:
                code, message, retryable = error
                outcomes.append(
                    FeishuSyncOutcome(
                        source_record_id=record.source_record_id,
                        source_version=record.source_version,
                        action=(
                            "update" if record.existing_remote_record_id else "create"
                        ),
                        status="failed",
                        remote_record_id=record.existing_remote_record_id,
                        fields=record.fields,
                        error={
                            "code": code,
                            "message": message,
                            "retryable": retryable,
                        },
                    )
                )
                continue
            remote_id = record.existing_remote_record_id or self._remote_id(
                binding_id, record.source_record_id
            )
            outcomes.append(
                FeishuSyncOutcome(
                    source_record_id=record.source_record_id,
                    source_version=record.source_version,
                    action="update" if record.existing_remote_record_id else "create",
                    status="success",
                    remote_record_id=remote_id,
                    fields=record.fields,
                )
            )
        digest = hashlib.sha256(
            f"{binding_id}:{attempt_no}:{','.join(record.source_record_id for record in records)}".encode()
        ).hexdigest()[:16]
        return FeishuSyncBatch(
            request_id=f"feishu_sandbox_{digest}",
            provider=self.provider_id,
            outcomes=tuple(outcomes),
        )

    @staticmethod
    def _remote_id(binding_id: str, source_record_id: str) -> str:
        digest = hashlib.sha256(
            f"{binding_id}:{source_record_id}".encode()
        ).hexdigest()[:20]
        return f"rec_sbx_{digest}"

    def close(self) -> None:
        return None


class OpenApiFeishuProvider:
    provider_id = "openapi-v1"
    is_sandbox = False
    external_calls = True

    def __init__(
        self,
        *,
        credential_store: EnvCredentialStore,
        app_token: str | None,
        test_table_id: str | None,
        smoke_scope: str,
        writes_enabled: bool,
        max_records_per_run: int,
        base_url: str,
        timeout_seconds: float,
        target_tables: dict[str, str] | None = None,
        token_cipher: FeishuTokenCipher | None = None,
        oauth_redirect_uri: str | None = None,
        oauth_state_secret: str | None = None,
        client: httpx.Client | None = None,
    ):
        if smoke_scope not in FEISHU_SCOPES:
            raise RuntimeError("RIFFLOOM_FEISHU_SMOKE_SCOPE 不受支持")
        oauth_enabled = bool(oauth_redirect_uri)
        configured_targets = {} if oauth_enabled else dict(target_tables or {})
        if test_table_id and not oauth_enabled:
            configured_targets.setdefault(smoke_scope, test_table_id)
        if any(scope not in FEISHU_SCOPES for scope in configured_targets):
            raise RuntimeError("RIFFLOOM_FEISHU_TARGET_TABLES 包含不受支持的 scope")
        if len(set(configured_targets.values())) != len(configured_targets):
            raise RuntimeError("真实飞书每个同步 scope 必须绑定不同的数据表")
        if not 1 <= max_records_per_run <= 30:
            raise RuntimeError("真实飞书单次记录上限必须在 1 到 30 之间")
        self.credential_store = credential_store
        self.token_cipher = token_cipher
        self.oauth_redirect_uri = oauth_redirect_uri
        self.oauth_enabled = oauth_enabled
        self._oauth_state_key = hashlib.sha256(
            f"riffloom:feishu-oauth-state:{oauth_state_secret or ''}".encode()
        ).digest()
        if self.oauth_enabled and (token_cipher is None or not oauth_state_secret):
            raise RuntimeError("飞书用户 OAuth 需要令牌加密器和 RIFFLOOM_AUTH_SECRET")
        self.app_token = None if oauth_enabled else app_token
        self.test_table_id = None if oauth_enabled else test_table_id
        self.target_base_ref = self._target_ref(
            "base", self.app_token or "unconfigured"
        )
        self.target_tables = configured_targets
        self.target_table_refs = {
            scope: self._target_ref(
                "table", f"{self.app_token or 'unconfigured'}:{table_id}"
            )
            for scope, table_id in configured_targets.items()
        }
        self.target_table_ref = self.target_table_refs.get(
            smoke_scope,
            self._target_ref(
                "table", f"{self.app_token or 'unconfigured'}:unconfigured"
            ),
        )
        self.smoke_scope = smoke_scope
        self.writes_enabled = writes_enabled
        self.max_records_per_run = max_records_per_run
        self._client = client or httpx.Client(
            base_url=f"{base_url.rstrip('/')}/",
            timeout=timeout_seconds,
            follow_redirects=False,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        self._owns_client = client is None
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider_id,
            "mode": "production",
            "is_sandbox": False,
            "external_calls": True,
            "writes_enabled": self.writes_enabled,
            "credential_storage": (
                "encrypted_per_user"
                if self.oauth_enabled
                else "env_reference_and_memory_token"
            ),
            "auth_mode": "user_oauth" if self.oauth_enabled else "shared_application",
            "scopes": list(FEISHU_SCOPES if self.oauth_enabled else self.target_tables),
            "batch_size": 1,
            "supports_full": self.writes_enabled,
            "supports_incremental": self.writes_enabled,
            "supports_retry_failed": self.writes_enabled,
        }

    def oauth_authorization_url(
        self, *, workspace_id: str, user_id: str, scope_key: str, return_to: str
    ) -> str:
        if not self.oauth_enabled or not self.oauth_redirect_uri:
            raise FeishuProviderError(
                "FEISHU_OAUTH_DISABLED", "飞书用户授权尚未配置", status_code=409
            )
        credentials = self._credentials()
        state = self._sign_state(
            {
                "workspace_id": workspace_id,
                "user_id": user_id,
                "scope_key": scope_key,
                "return_to": return_to,
                "expires_at": int(time.time()) + 600,
                "nonce": secrets.token_urlsafe(12),
            }
        )
        return "https://accounts.feishu.cn/open-apis/authen/v1/authorize?" + urlencode(
            {
                "client_id": credentials.app_id,
                "response_type": "code",
                "redirect_uri": self.oauth_redirect_uri,
                "scope": FEISHU_OAUTH_SCOPES,
                "state": state,
            }
        )

    def verify_oauth_state(self, state: str) -> dict[str, object]:
        try:
            encoded, signature = state.split(".", 1)
            expected = hmac.new(
                self._oauth_state_key, encoded.encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = json.loads(urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if not isinstance(payload, dict) or int(payload.get("expires_at", 0)) < int(
                time.time()
            ):
                raise ValueError
            return payload
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise FeishuProviderError(
                "FEISHU_OAUTH_STATE_INVALID",
                "飞书授权状态无效或已过期，请重新绑定",
                status_code=422,
            ) from error

    def exchange_oauth_code(self, code: str) -> FeishuOAuthGrant:
        credentials = self._credentials()
        payload, _ = self._request(
            "POST",
            "authen/v2/oauth/token",
            json_body={
                "grant_type": "authorization_code",
                "client_id": credentials.app_id,
                "client_secret": credentials.app_secret,
                "code": code,
                "redirect_uri": self.oauth_redirect_uri,
            },
            authenticated=False,
        )
        return self._oauth_grant(payload)

    def refresh_oauth_grant(self, refresh_token: str) -> FeishuOAuthGrant:
        credentials = self._credentials()
        payload, _ = self._request(
            "POST",
            "authen/v2/oauth/token",
            json_body={
                "grant_type": "refresh_token",
                "client_id": credentials.app_id,
                "client_secret": credentials.app_secret,
                "refresh_token": refresh_token,
            },
            authenticated=False,
        )
        return self._oauth_grant(payload)

    def oauth_user_info(self, access_token: str) -> dict[str, str]:
        payload, _ = self._authorized_request(
            "GET", "authen/v1/user_info", access_token=access_token
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        open_id = str(data.get("open_id") or "")
        if not open_id:
            raise FeishuProviderError(
                "FEISHU_AUTH_RESPONSE_INVALID",
                "飞书用户信息响应缺少 open_id",
                status_code=502,
            )
        return {
            "open_id": open_id,
            "tenant_key": str(data.get("tenant_key") or open_id),
            "name": str(data.get("name") or "我的飞书"),
        }

    def ensure_target_fields(
        self,
        *,
        access_token: str,
        app_token: str,
        table_id: str,
        field_names: set[str],
    ) -> None:
        if not self.oauth_enabled or not self.accepts_target(app_token, table_id):
            raise FeishuProviderError(
                "FEISHU_TARGET_INVALID", "飞书目标数据表无效", status_code=422
            )
        fields_path = (
            f"bitable/v1/apps/{quote(app_token, safe='')}/tables/"
            f"{quote(table_id, safe='')}/fields"
        )
        existing = {
            str(item.get("field_name") or item.get("name") or "")
            for item in self._list_items(fields_path, access_token=access_token)
        }
        for name in sorted(field_names - existing):
            self._authorized_request(
                "POST",
                fields_path,
                json_body={"field_name": name, "type": 1},
                access_token=access_token,
            )

    def encrypt_token(self, token: str) -> str:
        if self.token_cipher is None:
            raise FeishuProviderError(
                "FEISHU_OAUTH_DISABLED", "飞书用户授权尚未配置", status_code=409
            )
        return self.token_cipher.encrypt(token)

    def decrypt_token(self, encrypted: str) -> str:
        if self.token_cipher is None:
            raise FeishuProviderError(
                "FEISHU_OAUTH_DISABLED", "飞书用户授权尚未配置", status_code=409
            )
        try:
            return self.token_cipher.decrypt(encrypted)
        except CredentialStoreError as error:
            raise FeishuProviderError(
                "FEISHU_AUTH_EXPIRED", str(error), status_code=401
            ) from error

    @staticmethod
    def parse_app_token(value: str) -> str:
        candidate = value.strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{8,160}", candidate):
            return candidate
        parsed = urlparse(candidate)
        if parsed.scheme != "https" or not (
            parsed.hostname == "feishu.cn"
            or (parsed.hostname or "").endswith(".feishu.cn")
        ):
            raise FeishuProviderError(
                "FEISHU_BASE_URL_INVALID",
                "请粘贴 https://*.feishu.cn/base/... 多维表格链接",
                status_code=422,
            )
        parts = [part for part in parsed.path.split("/") if part]
        if (
            len(parts) < 2
            or parts[0] != "base"
            or not re.fullmatch(r"[A-Za-z0-9_-]{8,160}", parts[1])
        ):
            raise FeishuProviderError(
                "FEISHU_BASE_URL_INVALID",
                "当前仅支持飞书 /base/ 多维表格链接",
                status_code=422,
            )
        return parts[1]

    def connection_metadata(
        self, *, workspace_id: str, tenant_name: str
    ) -> dict[str, object]:
        del workspace_id
        credentials = self._credentials()
        self._access_token()
        app_hash = hashlib.sha256(credentials.app_id.encode()).hexdigest()[:16]
        return {
            "tenant_key": f"tenant_openapi_{app_hash}",
            "tenant_name": tenant_name,
            "auth_type": "tenant_self_built_openapi",
            "scopes": ["bitable:app"],
            "credential_ref": FEISHU_SELF_BUILT_CREDENTIAL_REF,
        }

    def targets(
        self,
        *,
        access_token: str | None = None,
        app_token: str | None = None,
        scope_key: str | None = None,
    ) -> list[dict[str, object]]:
        if self.oauth_enabled:
            if not access_token or not app_token or scope_key not in FEISHU_SCOPES:
                raise FeishuProviderError(
                    "FEISHU_TARGET_NOT_CONFIGURED",
                    "请先授权飞书并选择自己的多维表格",
                    status_code=409,
                )
            raw_app_token = self.parse_app_token(app_token)
            escaped_app_token = quote(raw_app_token, safe="")
            metadata, _ = self._authorized_request(
                "GET", f"bitable/v1/apps/{escaped_app_token}", access_token=access_token
            )
            tables = self._list_items(
                f"bitable/v1/apps/{escaped_app_token}/tables", access_token=access_token
            )
            normalized_tables = []
            for table in tables:
                raw_table_id = str(table.get("table_id") or "")
                if not raw_table_id:
                    continue
                fields = self._list_items(
                    f"bitable/v1/apps/{escaped_app_token}/tables/{quote(raw_table_id, safe='')}/fields",
                    access_token=access_token,
                )
                normalized_tables.append(
                    {
                        "table_id": raw_table_id,
                        "table_name": str(table.get("name") or scope_key),
                        "scope_key": scope_key,
                        "fields": self._normalize_fields(fields),
                    }
                )
            data = (
                metadata.get("data") if isinstance(metadata.get("data"), dict) else {}
            )
            app = data.get("app") if isinstance(data.get("app"), dict) else {}
            return [
                {
                    "base_id": raw_app_token,
                    "base_name": str(app.get("name") or "我的飞书多维表格"),
                    "tables": normalized_tables,
                }
            ]
        if not self.app_token or not self.target_tables:
            raise FeishuProviderError(
                "FEISHU_TARGET_NOT_CONFIGURED",
                "RIFFLOOM_FEISHU_APP_TOKEN 或目标数据表尚未配置",
                status_code=409,
            )
        app_token = quote(self.app_token, safe="")
        tables = self._list_items(f"bitable/v1/apps/{app_token}/tables")
        tables_by_id = {str(item.get("table_id")): item for item in tables}
        normalized_tables = []
        for scope, raw_table_id in self.target_tables.items():
            table = tables_by_id.get(raw_table_id)
            if table is None:
                raise FeishuProviderError(
                    "FEISHU_TARGET_DELETED",
                    f"{scope} 对应的飞书数据表不存在或应用无权访问",
                    status_code=404,
                )
            table_id = quote(raw_table_id, safe="")
            fields = self._list_items(
                f"bitable/v1/apps/{app_token}/tables/{table_id}/fields"
            )
            normalized_tables.append(
                {
                    "table_id": self.target_table_refs[scope],
                    "table_name": str(table.get("name") or scope),
                    "scope_key": scope,
                    "fields": self._normalize_fields(fields),
                }
            )
        return [
            {
                "base_id": self.target_base_ref,
                "base_name": "Riffloom 已确认测试多维表格",
                "tables": normalized_tables,
            }
        ]

    def accepts_target(self, target_base_id: str, target_table_id: str) -> bool:
        if self.oauth_enabled:
            return bool(
                re.fullmatch(r"[A-Za-z0-9_-]{8,160}", target_base_id)
                and re.fullmatch(r"tbl[A-Za-z0-9_-]{5,157}", target_table_id)
            )
        return target_base_id == self.target_base_ref and target_table_id in set(
            self.target_table_refs.values()
        )

    def target_web_url(self, target_base_id: str, target_table_id: str) -> str | None:
        if self.oauth_enabled:
            if not self.accepts_target(target_base_id, target_table_id):
                return None
            return (
                f"https://feishu.cn/base/{quote(target_base_id, safe='')}"
                f"?table={quote(target_table_id, safe='')}"
            )
        if not self.app_token or not self.accepts_target(
            target_base_id, target_table_id
        ):
            return None
        raw_table_id = next(
            (
                self.target_tables[scope]
                for scope, table_ref in self.target_table_refs.items()
                if table_ref == target_table_id
            ),
            None,
        )
        if raw_table_id is None:
            return None
        return (
            f"https://feishu.cn/base/{quote(self.app_token, safe='')}"
            f"?table={quote(raw_table_id, safe='')}"
        )

    def upsert(
        self,
        *,
        binding_id: str,
        target_base_id: str,
        target_table_id: str,
        target_table_name: str,
        attempt_no: int,
        records: list[FeishuSyncRecord],
        access_token: str | None = None,
    ) -> FeishuSyncBatch:
        del binding_id, target_table_name, attempt_no
        if not self.writes_enabled:
            return self._failed_batch(
                records, "FEISHU_REAL_WRITE_DISABLED", "真实飞书写入开关仍关闭"
            )
        if self.oauth_enabled:
            if not access_token or not self.accepts_target(
                target_base_id, target_table_id
            ):
                return self._failed_batch(
                    records, "FEISHU_CONNECTION_INVALID", "当前用户飞书授权或目标表无效"
                )
            raw_app_token = target_base_id
            raw_table_id = target_table_id
        elif not self.app_token or not self.target_tables:
            return self._failed_batch(
                records,
                "FEISHU_TARGET_NOT_CONFIGURED",
                "真实飞书目标尚未配置",
            )
        else:
            raw_app_token = self.app_token or ""
            raw_table_id = next(
                (
                    self.target_tables[scope]
                    for scope, table_ref in self.target_table_refs.items()
                    if table_ref == target_table_id
                ),
                None,
            )
        if (
            (not self.oauth_enabled and target_base_id != self.target_base_ref)
            or raw_table_id is None
            or len(records) > self.max_records_per_run
        ):
            return self._failed_batch(
                records,
                "FEISHU_SMOKE_SCOPE_EXCEEDED",
                f"真实飞书只能写入已配置目标表且单次最多 {self.max_records_per_run} 条",
            )
        app_token = quote(raw_app_token, safe="")
        table_id = quote(raw_table_id, safe="")
        outcomes: list[FeishuSyncOutcome] = []
        request_ids: list[str] = []
        for record in records:
            action = "update" if record.existing_remote_record_id else "create"
            path = f"bitable/v1/apps/{app_token}/tables/{table_id}/records"
            method = "POST"
            if record.existing_remote_record_id:
                remote_id = quote(record.existing_remote_record_id, safe="")
                path = f"{path}/{remote_id}"
                method = "PUT"
            try:
                payload, request_id = self._authorized_request(
                    method,
                    path,
                    json_body={"fields": record.fields},
                    access_token=access_token,
                )
                request_ids.append(request_id)
                data = (
                    payload.get("data") if isinstance(payload.get("data"), dict) else {}
                )
                remote_record = (
                    data.get("record") if isinstance(data.get("record"), dict) else data
                )
                remote_record_id = str(
                    remote_record.get("record_id")
                    or remote_record.get("id")
                    or record.existing_remote_record_id
                    or ""
                )
                if not remote_record_id:
                    raise FeishuProviderError(
                        "FEISHU_RESPONSE_INVALID",
                        "飞书写入成功响应缺少 record_id",
                        retryable=False,
                    )
                outcomes.append(
                    FeishuSyncOutcome(
                        source_record_id=record.source_record_id,
                        source_version=record.source_version,
                        action=action,
                        status="success",
                        remote_record_id=remote_record_id,
                        fields=record.fields,
                    )
                )
            except FeishuProviderError as error:
                outcomes.append(
                    FeishuSyncOutcome(
                        source_record_id=record.source_record_id,
                        source_version=record.source_version,
                        action=action,
                        status="failed",
                        remote_record_id=record.existing_remote_record_id,
                        fields=record.fields,
                        error=error.as_item_error(),
                    )
                )
        digest = hashlib.sha256(
            ":".join(
                request_ids or [record.source_record_id for record in records]
            ).encode()
        ).hexdigest()[:16]
        return FeishuSyncBatch(
            request_id=f"feishu_openapi_{digest}",
            provider=self.provider_id,
            outcomes=tuple(outcomes),
            external_calls=True,
        )

    def _credentials(self):
        try:
            return self.credential_store.get_feishu_app_credentials(
                FEISHU_SELF_BUILT_CREDENTIAL_REF
            )
        except CredentialStoreError as error:
            raise FeishuProviderError(
                "FEISHU_CREDENTIALS_MISSING",
                str(error),
                status_code=409,
            ) from error

    def _sign_state(self, payload: dict[str, object]) -> str:
        encoded = (
            urlsafe_b64encode(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            )
            .decode()
            .rstrip("=")
        )
        signature = hmac.new(
            self._oauth_state_key, encoded.encode(), hashlib.sha256
        ).hexdigest()
        return f"{encoded}.{signature}"

    @staticmethod
    def _oauth_grant(payload: dict[str, Any]) -> FeishuOAuthGrant:
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            raise FeishuProviderError(
                "FEISHU_AUTH_RESPONSE_INVALID",
                "飞书授权响应缺少 access_token 或 refresh_token；请确认已开通 offline_access",
                status_code=502,
            )
        now = datetime.now(timezone.utc)
        return FeishuOAuthGrant(
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=now
            + timedelta(seconds=max(60, int(payload.get("expires_in") or 7200))),
            refresh_expires_at=now
            + timedelta(
                seconds=max(60, int(payload.get("refresh_token_expires_in") or 604800))
            ),
            scopes=tuple(str(payload.get("scope") or "").split()),
        )

    @staticmethod
    def _normalize_fields(fields: list[dict[str, Any]]) -> list[dict[str, object]]:
        return [
            {
                "name": str(item.get("field_name") or item.get("name") or ""),
                "type": str(item.get("type") or "unknown"),
                "readonly": str(item.get("field_name") or item.get("name") or "")
                in {
                    "riffloom_record_id",
                    "riffloom_record_version",
                    "last_synced_at",
                    "riffloom_status",
                },
            }
            for item in fields
            if item.get("field_name") or item.get("name")
        ]

    def _access_token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token
        with self._token_lock:
            now = time.monotonic()
            if self._token and now < self._token_expires_at:
                return self._token
            credentials = self._credentials()
            payload, _ = self._request(
                "POST",
                "auth/v3/tenant_access_token/internal",
                json_body={
                    "app_id": credentials.app_id,
                    "app_secret": credentials.app_secret,
                },
                authenticated=False,
            )
            token = payload.get("tenant_access_token")
            if not isinstance(token, str) or not token:
                raise FeishuProviderError(
                    "FEISHU_AUTH_RESPONSE_INVALID",
                    "飞书凭证响应缺少 tenant_access_token",
                    status_code=502,
                )
            expires_in = int(payload.get("expire") or 7200)
            self._token = token
            self._token_expires_at = time.monotonic() + max(60, expires_in - 120)
            return token

    def _authorized_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        access_token: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        try:
            return self._request(
                method,
                path,
                json_body=json_body,
                params=params,
                authenticated=True,
                access_token=access_token,
            )
        except FeishuProviderError as error:
            if error.code == "FEISHU_AUTH_EXPIRED" and access_token is None:
                self._token = None
                self._token_expires_at = 0.0
            raise

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        authenticated: bool,
        access_token: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        headers = (
            {"Authorization": f"Bearer {access_token or self._access_token()}"}
            if authenticated
            else {}
        )
        try:
            response = self._client.request(
                method,
                path,
                json=json_body,
                params=params,
                headers=headers,
            )
        except httpx.TimeoutException as error:
            raise FeishuProviderError(
                "FEISHU_TIMEOUT",
                "飞书 OpenAPI 请求超时",
                status_code=504,
                retryable=True,
            ) from error
        except httpx.HTTPError as error:
            raise FeishuProviderError(
                "FEISHU_NETWORK_ERROR",
                "无法连接飞书 OpenAPI",
                status_code=502,
                retryable=True,
            ) from error
        try:
            payload = response.json()
        except ValueError as error:
            raise FeishuProviderError(
                "FEISHU_RESPONSE_INVALID",
                "飞书 OpenAPI 返回了非 JSON 响应",
                status_code=502,
            ) from error
        if not isinstance(payload, dict):
            raise FeishuProviderError(
                "FEISHU_RESPONSE_INVALID",
                "飞书 OpenAPI 响应结构无效",
                status_code=502,
            )
        code = int(payload.get("code") or 0)
        if not response.is_success or code != 0:
            raise self._map_error(
                http_status=response.status_code,
                provider_code=code,
                provider_message=str(
                    payload.get("msg")
                    or payload.get("error_description")
                    or "OpenAPI 请求失败"
                )[:200],
            )
        request_id = (
            response.headers.get("X-Tt-Logid")
            or response.headers.get("X-Request-Id")
            or hashlib.sha256(
                f"{method}:{path}:{time.monotonic_ns()}".encode()
            ).hexdigest()[:20]
        )
        return payload, request_id

    def _list_items(
        self, path: str, *, access_token: str | None = None
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            payload, _ = self._authorized_request(
                "GET", path, params=params, access_token=access_token
            )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            page_items = (
                data.get("items") if isinstance(data.get("items"), list) else []
            )
            items.extend(item for item in page_items if isinstance(item, dict))
            if not data.get("has_more"):
                return items
            page_token = str(data.get("page_token") or "")
            if not page_token:
                return items

    @staticmethod
    def _target_ref(kind: str, value: str) -> str:
        digest = hashlib.sha256(value.encode()).hexdigest()[:20]
        return f"{kind}_feishu_{digest}"

    @staticmethod
    def _map_error(
        *, http_status: int, provider_code: int, provider_message: str
    ) -> FeishuProviderError:
        if http_status == 429 or provider_code in {99991400, 99991401}:
            return FeishuProviderError(
                "FEISHU_RATE_LIMITED",
                "飞书 OpenAPI 触发限流",
                status_code=429,
                retryable=True,
            )
        if http_status == 401 or provider_code in {99991661, 99991663, 99991668}:
            return FeishuProviderError(
                "FEISHU_AUTH_EXPIRED",
                "飞书应用凭证无效或已过期",
                status_code=401,
            )
        if http_status == 403:
            return FeishuProviderError(
                "FEISHU_PERMISSION_DENIED",
                "飞书应用无权访问已确认测试表",
                status_code=403,
            )
        if http_status == 404 or provider_code in {1254043, 1254044}:
            return FeishuProviderError(
                "FEISHU_TARGET_DELETED",
                "已确认的飞书测试目标不存在",
                status_code=404,
            )
        return FeishuProviderError(
            "FEISHU_OPENAPI_ERROR",
            f"飞书 OpenAPI 返回错误（code={provider_code}）：{provider_message}",
            status_code=502,
            retryable=http_status >= 500,
        )

    def _failed_batch(
        self,
        records: list[FeishuSyncRecord],
        code: str,
        message: str,
    ) -> FeishuSyncBatch:
        return FeishuSyncBatch(
            request_id=f"feishu_blocked_{hashlib.sha256(code.encode()).hexdigest()[:16]}",
            provider=self.provider_id,
            outcomes=tuple(
                FeishuSyncOutcome(
                    source_record_id=record.source_record_id,
                    source_version=record.source_version,
                    action="update" if record.existing_remote_record_id else "create",
                    status="failed",
                    remote_record_id=record.existing_remote_record_id,
                    fields=record.fields,
                    error={"code": code, "message": message, "retryable": False},
                )
                for record in records
            ),
            external_calls=False,
        )

    def close(self) -> None:
        self._token = None
        self._token_expires_at = 0.0
        if self._owns_client:
            self._client.close()
