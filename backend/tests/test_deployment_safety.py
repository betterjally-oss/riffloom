from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.deployment_safety import DeploymentCheck, validate_deployment_settings
from app.core.http_safety import RequestBodyLimitMiddleware
from app.main import create_app


def _pilot_settings(**overrides) -> Settings:
    values = {
        "deployment_profile": "pilot",
        "database_url": "sqlite:////tmp/riffloom-pilot-preflight.db",
        "database_schema_mode": "migrations_only",
        "database_migrate_on_startup": True,
        "database_backup_enabled": True,
        "database_restore_policy": "required",
        "sqlite_single_instance_acknowledged": True,
        "object_storage_provider": "tos_mount",
        "object_storage_mount_dir": "/mnt/riffloom-tos",
        "auth_mode": "invite_token",
        "auth_secret": "test-only-pilot-secret-at-least-32-bytes",
        "api_docs_enabled": False,
        "log_format": "json",
    }
    values.update(overrides)
    return Settings(**values)


def test_pilot_preflight_accepts_only_fail_closed_configuration():
    checks = validate_deployment_settings(_pilot_settings())

    assert {item.name: item.value for item in checks} == {
        "deployment_profile": "pilot",
        "auth": "invite_token",
        "database": "sqlite_tmp_migrations_only",
        "migration_order": "restore_then_upgrade",
        "durability": "tos_mount_verified_backup",
        "instance_model": "single_instance_acknowledged",
        "restore_policy": "required",
        "api_docs": "disabled",
        "log_format": "json",
        "external_writes": "disabled",
        "request_body_limit": "bounded",
    }


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"auth_mode": "demo_headers"}, "invite_token"),
        ({"auth_secret": "short"}, "至少 32 字节"),
        ({"database_url": "sqlite:////var/lib/riffloom.db"}, "必须位于 /tmp"),
        ({"database_schema_mode": "create_all"}, "migrations_only"),
        ({"database_migrate_on_startup": False}, "恢复后自动迁移"),
        ({"database_backup_enabled": False}, "一致性备份"),
        ({"database_restore_policy": "if_available"}, "required"),
        ({"object_storage_provider": "local"}, "tos_mount"),
        ({"sqlite_single_instance_acknowledged": False}, "恰好一个实例"),
        ({"api_docs_enabled": True}, "关闭公开"),
        ({"feishu_real_writes_enabled": True}, "openapi-v1"),
        ({"collection_provider": "opencli-local-v1"}, "OpenCLI"),
        ({"request_max_body_bytes": 40 * 1024 * 1024}, "不得超过 32 MiB"),
    ],
)
def test_pilot_preflight_rejects_unsafe_configuration(override, message):
    with pytest.raises(RuntimeError, match=message):
        validate_deployment_settings(_pilot_settings(**override))


def test_bootstrap_profile_allows_only_transitional_restore_policy():
    checks = validate_deployment_settings(
        _pilot_settings(
            deployment_profile="pilot_bootstrap",
            database_restore_policy="if_available",
        )
    )
    assert checks[0].value == "pilot_bootstrap"


def test_pilot_allows_per_user_oauth_feishu_writes():
    safe = {
        "feishu_provider": "openapi-v1",
        "feishu_app_id": "cli_test_id",
        "feishu_app_secret": "test-secret",
        "feishu_oauth_redirect_uri": "https://riffloom.example/api/riffloom/integrations/feishu/oauth/callback",
        "feishu_max_records_per_run": 30,
        "feishu_real_writes_enabled": True,
    }
    checks = validate_deployment_settings(_pilot_settings(**safe))
    assert DeploymentCheck("external_writes", "feishu_user_oauth") in checks

    for unsafe in (
        {"feishu_max_records_per_run": 31},
        {"feishu_oauth_redirect_uri": None},
        {"feishu_oauth_redirect_uri": "http://riffloom.example/callback"},
    ):
        with pytest.raises(RuntimeError):
            validate_deployment_settings(_pilot_settings(**(safe | unsafe)))


def test_settings_parse_feishu_target_table_mapping(monkeypatch):
    monkeypatch.setenv(
        "RIFFLOOM_FEISHU_TARGET_TABLES",
        '{"collection.keyword":"tbl_keyword","creation":"tbl_creation"}',
    )

    assert Settings.from_env().feishu_target_tables == (
        ("collection.keyword", "tbl_keyword"),
        ("creation", "tbl_creation"),
    )


def test_pilot_real_asr_requires_ark_and_tos_presign():
    with pytest.raises(RuntimeError, match="ARK_API_KEY"):
        validate_deployment_settings(
            _pilot_settings(asr_provider="volcengine-doubao-asr-v1")
        )

    checks = validate_deployment_settings(
        _pilot_settings(
            asr_provider="volcengine-doubao-asr-v1",
            volcengine_ark_api_key="test-ark-key",
            tos_endpoint="https://tos-cn-beijing.volces.com",
            tos_bucket="riffloom-pilot-test",
            tos_access_key_id="test-ak",
            tos_secret_access_key="test-sk",
            request_max_body_bytes=8 * 1024 * 1024,
        )
    )
    assert checks[-1] == DeploymentCheck("media_transcription", "tos_presign")


def test_request_body_limit_returns_correlated_413(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'body-limit.db'}",
            asset_storage_dir=str(tmp_path / "uploads"),
            request_max_body_bytes=1024,
            worker_step_delay=0.01,
            worker_threads=1,
            log_level="WARNING",
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/invitations/redeem",
            content=b"x" * 1025,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
    assert response.headers["X-Trace-ID"] == response.json()["error"]["trace_id"]
    assert response.json()["error"]["details"] == {"max_body_bytes": 1024}


def test_request_body_limit_counts_stream_without_content_length():
    sent = []
    messages = iter(
        [
            {"type": "http.request", "body": b"a" * 600, "more_body": True},
            {"type": "http.request", "body": b"b" * 600, "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    async def consuming_app(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(consuming_app, max_body_bytes=1024)
    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/upload",
                "headers": [],
                "state": {"request_id": "req_test", "trace_id": "trc_test"},
            },
            receive,
            send,
        )
    )

    assert sent[0]["status"] == 413


def test_api_docs_can_be_disabled_without_affecting_health(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'docs-disabled.db'}",
            asset_storage_dir=str(tmp_path / "uploads"),
            api_docs_enabled=False,
            worker_step_delay=0.01,
            worker_threads=1,
            log_level="WARNING",
        )
    )
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/api/v1/health").status_code == 200


def test_migrations_only_can_upgrade_after_restore_step(tmp_path):
    database_path = tmp_path / "migrated-on-startup.db"
    app = create_app(
        Settings(
            database_url=f"sqlite:///{database_path}",
            database_schema_mode="migrations_only",
            database_migrate_on_startup=True,
            asset_storage_dir=str(tmp_path / "uploads"),
            worker_step_delay=0.01,
            worker_threads=1,
            log_level="WARNING",
        )
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200

    assert database_path.is_file()
    with app.state.database.engine.connect() as connection:
        collection_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(collection_records)"
            )
        }
        cover_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(cover_assets)")
        }
        blogger_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(blogger_records)")
        }
        breakdown_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(breakdown_records)"
            )
        }
        creation_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(creation_records)")
        }
    assert {"benchmark", "category_tags"} <= collection_columns
    assert "estimated_cost_cny" in cover_columns
    assert "deleted_at" in collection_columns
    assert "deleted_at" in blogger_columns
    assert "deleted_at" in breakdown_columns
    assert "deleted_at" in creation_columns


def test_pilot_real_cover_requires_ark_key():
    with pytest.raises(RuntimeError, match="ARK_API_KEY"):
        validate_deployment_settings(
            _pilot_settings(cover_provider="volcengine-seedream-v1")
        )

    checks = validate_deployment_settings(
        _pilot_settings(
            cover_provider="volcengine-seedream-v1",
            volcengine_ark_api_key="test-ark-key",
        )
    )
    assert checks[-1] == DeploymentCheck("cover_generation", "seedream_watermarked")
