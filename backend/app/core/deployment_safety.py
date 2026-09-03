from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import Settings


@dataclass(frozen=True)
class DeploymentCheck:
    name: str
    value: str


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"Pilot 生产预检失败：{message}")


def _sqlite_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database:
        return None
    if url.database == ":memory:":
        return None
    return Path(url.database).expanduser().resolve()


def validate_deployment_settings(settings: Settings) -> list[DeploymentCheck]:
    """Fail closed when an explicitly selected Pilot profile is unsafe.

    The returned summary intentionally contains no URL, path, credential, or
    secret value so it can be used by a local preflight command and logs.
    """

    profile = settings.deployment_profile
    _require(
        profile in {"local", "pilot_bootstrap", "pilot"},
        "RIFFLOOM_DEPLOYMENT_PROFILE 仅支持 local、pilot_bootstrap 或 pilot",
    )
    checks = [DeploymentCheck("deployment_profile", profile)]
    if profile == "local":
        return checks

    database_path = _sqlite_path(settings.database_url)
    _require(database_path is not None, "Pilot 当前只支持文件型 SQLite")
    temporary_root = Path("/tmp").resolve()
    _require(
        database_path == temporary_root or temporary_root in database_path.parents,
        "SQLite 运行副本必须位于 /tmp",
    )
    _require(
        settings.database_schema_mode == "migrations_only",
        "必须使用 RIFFLOOM_DATABASE_SCHEMA_MODE=migrations_only",
    )
    _require(
        settings.database_migrate_on_startup,
        "必须开启恢复后自动迁移 RIFFLOOM_DATABASE_MIGRATE_ON_STARTUP=true",
    )
    _require(
        settings.auth_mode == "invite_token",
        "必须使用 RIFFLOOM_AUTH_MODE=invite_token",
    )
    _require(
        bool(settings.auth_secret) and len(settings.auth_secret.encode("utf-8")) >= 32,
        "RIFFLOOM_AUTH_SECRET 必须至少 32 字节",
    )
    _require(
        settings.object_storage_provider == "tos_mount",
        "必须使用 RIFFLOOM_OBJECT_STORAGE_PROVIDER=tos_mount",
    )
    mount_dir = (
        Path(settings.object_storage_mount_dir).expanduser()
        if settings.object_storage_mount_dir
        else None
    )
    _require(
        mount_dir is not None and mount_dir.is_absolute(), "TOS 挂载目录必须是绝对路径"
    )
    resolved_mount_dir = mount_dir.resolve()
    _require(
        resolved_mount_dir != temporary_root
        and temporary_root not in resolved_mount_dir.parents,
        "TOS 挂载目录不能位于临时 /tmp",
    )
    _require(settings.database_backup_enabled, "必须开启数据库一致性备份")
    _require(
        settings.sqlite_single_instance_acknowledged,
        "必须先把后端限制为恰好一个实例并显式确认",
    )
    allowed_restore_policies = (
        {"if_available", "required"} if profile == "pilot_bootstrap" else {"required"}
    )
    _require(
        settings.database_restore_policy in allowed_restore_policies,
        "稳定 Pilot 必须使用 required 恢复策略；首次引导期可用 if_available",
    )
    _require(settings.log_format == "json", "Pilot 日志必须使用 json 格式")
    _require(not settings.api_docs_enabled, "Pilot 必须关闭公开 OpenAPI/Swagger 文档")
    _require("*" not in settings.cors_origins, "CORS 不允许通配来源")
    if settings.feishu_real_writes_enabled:
        _require(
            settings.feishu_provider == "openapi-v1", "飞书真实写入必须使用 openapi-v1"
        )
        _require(
            all(
                (
                    settings.feishu_app_id,
                    settings.feishu_app_secret,
                    settings.feishu_oauth_redirect_uri,
                    settings.auth_secret,
                )
            ),
            "飞书真实写入必须配置应用凭据、用户 OAuth 回调和令牌加密密钥",
        )
        _require(
            str(settings.feishu_oauth_redirect_uri).startswith("https://"),
            "飞书用户 OAuth 回调必须使用 HTTPS",
        )
        _require(
            1 <= settings.feishu_max_records_per_run <= 30,
            "飞书真实写入单次记录上限必须在 1 到 30 之间",
        )
    _require(
        settings.collection_provider
        in {
            "sandbox-v1",
            "xiaohongshu-web-v1",
            "tikhub-v1",
            "xiaohongshu-hybrid-v1",
        },
        "Pilot 云端禁止启用依赖用户本机浏览器的 OpenCLI 采集 Provider",
    )
    if settings.collection_provider in {"tikhub-v1", "xiaohongshu-hybrid-v1"}:
        _require(bool(settings.tikhub_api_key), "TikHub 采集必须配置 TIKHUB_API_KEY")
    _require(
        settings.asr_provider in {"disabled", "volcengine-doubao-asr-v1"},
        "Pilot 云端仅支持 disabled 或 volcengine-doubao-asr-v1 转写 Provider",
    )
    if settings.asr_provider == "volcengine-doubao-asr-v1":
        _require(bool(settings.volcengine_ark_api_key), "真实转写必须配置 ARK_API_KEY")
        _require(
            all(
                (
                    settings.tos_endpoint,
                    settings.tos_bucket,
                    settings.tos_access_key_id,
                    settings.tos_secret_access_key,
                )
            ),
            "Pilot 真实转写必须使用完整的 TOS 预签名直传配置",
        )
    _require(
        settings.cover_provider in {"mock-cover-v1", "volcengine-seedream-v1"},
        "封面 Provider 仅支持 mock-cover-v1 或 volcengine-seedream-v1",
    )
    if settings.cover_provider == "volcengine-seedream-v1":
        _require(
            bool(settings.volcengine_ark_api_key), "真实封面生成必须配置 ARK_API_KEY"
        )
    minimum_body_limit = (settings.asset_max_bytes * 4 + 2) // 3 + 512 * 1024
    _require(
        settings.request_max_body_bytes >= minimum_body_limit,
        "请求体上限不足以容纳已允许的 Base64 素材",
    )
    _require(
        settings.request_max_body_bytes <= 32 * 1024 * 1024,
        "请求体上限不得超过 32 MiB",
    )

    checks.extend(
        [
            DeploymentCheck("auth", "invite_token"),
            DeploymentCheck("database", "sqlite_tmp_migrations_only"),
            DeploymentCheck("migration_order", "restore_then_upgrade"),
            DeploymentCheck("durability", "tos_mount_verified_backup"),
            DeploymentCheck("instance_model", "single_instance_acknowledged"),
            DeploymentCheck("restore_policy", settings.database_restore_policy),
            DeploymentCheck("api_docs", "disabled"),
            DeploymentCheck("log_format", "json"),
            DeploymentCheck(
                "external_writes",
                (
                    "feishu_user_oauth"
                    if settings.feishu_real_writes_enabled
                    else "disabled"
                ),
            ),
            DeploymentCheck("request_body_limit", "bounded"),
        ]
    )
    if settings.asr_provider == "volcengine-doubao-asr-v1":
        checks.append(DeploymentCheck("media_transcription", "tos_presign"))
    if settings.cover_provider == "volcengine-seedream-v1":
        checks.append(DeploymentCheck("cover_generation", "seedream_watermarked"))
    return checks


def main() -> int:
    checks = validate_deployment_settings(Settings.from_env())
    print(
        json.dumps(
            {"ok": True, "checks": [asdict(check) for check in checks]},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
