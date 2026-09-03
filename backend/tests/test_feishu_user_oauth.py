from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.main import create_app
from app.models import FeishuConnection, FeishuOAuthCredential, FeishuSyncBinding
from app.models.entities import utcnow

ADMIN = {"X-Riffloom-User": "user_admin", "X-Riffloom-Workspace": "ws_demo"}
LEAD = {"X-Riffloom-User": "user_lead", "X-Riffloom-Workspace": "ws_demo"}
FIELDS = (
    "riffloom_record_id",
    "riffloom_record_version",
    "last_synced_at",
    "riffloom_status",
    "标题",
    "摘要",
    "标签",
)


def test_real_feishu_oauth_is_encrypted_and_isolated_per_user(tmp_path):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/authen/v2/oauth/token"):
            body = request.read().decode()
            if '"grant_type":"refresh_token"' in body:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "access_token": "access-admin-refreshed",
                        "refresh_token": "refresh-admin-rotated",
                        "expires_in": 7200,
                        "refresh_token_expires_in": 604800,
                        "scope": "offline_access bitable:app",
                    },
                )
            suffix = "lead" if "authorization-code-lead" in body else "admin"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "access_token": f"access-{suffix}",
                    "refresh_token": f"refresh-{suffix}",
                    "expires_in": 7200,
                    "refresh_token_expires_in": 604800,
                    "scope": "offline_access bitable:app",
                },
            )
        if request.url.path.endswith("/authen/v1/user_info"):
            suffix = (
                "lead"
                if request.headers["Authorization"] == "Bearer access-lead"
                else "admin"
            )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"open_id": f"ou-{suffix}", "name": f"飞书{suffix}"},
                },
            )
        if request.url.path.endswith("/bitable/v1/apps/appPersonal1"):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"app": {"name": "我的 Riffloom"}}},
            )
        if request.url.path.endswith("/bitable/v1/apps/appPersonal1/tables"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"table_id": "tblKeyword1", "name": "关键词搜索库"}]
                    },
                },
            )
        if request.url.path.endswith("/tables/tblKeyword1/fields"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"field_name": name, "type": 1} for name in FIELDS]
                    },
                },
            )
        raise AssertionError(
            f"unexpected Feishu request: {request.method} {request.url}"
        )

    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'oauth.db'}",
            asset_storage_dir=str(tmp_path / "uploads"),
            auth_secret="test-feishu-oauth-secret-at-least-32-bytes",
            feishu_provider="openapi-v1",
            feishu_app_id="cli_test",
            feishu_app_secret="app-secret-never-stored",
            feishu_oauth_redirect_uri="https://riffloom.example/api/riffloom/integrations/feishu/oauth/callback",
            feishu_real_writes_enabled=True,
            feishu_max_records_per_run=30,
            worker_step_delay=0.01,
            log_level="WARNING",
        )
    )
    provider = app.state.feishu_provider
    provider._client.close()
    provider._client = httpx.Client(
        base_url="https://open.feishu.cn/open-apis/",
        transport=httpx.MockTransport(handler),
    )

    with TestClient(app) as client:
        for headers, code in (
            (ADMIN, "authorization-code-admin"),
            (LEAD, "authorization-code-lead"),
        ):
            start = client.get(
                "/api/v1/integrations/feishu/oauth/authorize",
                params={"scope_key": "collection.keyword"},
                headers=headers,
                follow_redirects=False,
            )
            assert start.status_code == 307
            query = parse_qs(urlparse(start.headers["location"]).query)
            assert query["scope"] == ["offline_access bitable:app"]
            callback = client.get(
                "/api/v1/integrations/feishu/oauth/callback",
                params={"code": code, "state": query["state"][0]},
                headers=headers,
                follow_redirects=False,
            )
            assert callback.status_code == 303
            assert callback.headers["location"].startswith(
                "/collections?feishu=connected"
            )

        admin_connections = client.get(
            "/api/v1/integrations/feishu/connections", headers=ADMIN
        ).json()["items"]
        lead_connections = client.get(
            "/api/v1/integrations/feishu/connections", headers=LEAD
        ).json()["items"]
        assert [item["tenant_key"] for item in admin_connections] == ["ou-admin"]
        assert [item["tenant_key"] for item in lead_connections] == ["ou-lead"]

        targets = client.get(
            "/api/v1/integrations/feishu/targets",
            params={
                "connection_id": admin_connections[0]["id"],
                "base_url": "https://team.feishu.cn/base/appPersonal1",
                "scope_key": "collection.keyword",
            },
            headers=ADMIN,
        )
        assert targets.status_code == 200, targets.text
        target = targets.json()["items"][0]
        table = target["tables"][0]
        binding = client.post(
            "/api/v1/integrations/feishu/bindings",
            headers=ADMIN,
            json={
                "connection_id": admin_connections[0]["id"],
                "scope_key": "collection.keyword",
                "target_base_id": target["base_id"],
                "target_table_id": table["table_id"],
                "target_table_name": table["table_name"],
                "field_mapping": {},
                "external_copy_confirmed": True,
            },
        )
        assert binding.status_code == 201, binding.text
        assert (
            client.get("/api/v1/integrations/feishu/bindings", headers=LEAD).json()[
                "total"
            ]
            == 0
        )
        denied = client.post(
            f"/api/v1/integrations/feishu/bindings/{binding.json()['id']}/syncs",
            headers={**LEAD, "Idempotency-Key": "other-user-cannot-sync"},
            json={"mode": "full"},
        )
        assert denied.status_code == 404

        with app.state.database.session_factory() as session:
            connection = session.scalar(
                select(FeishuConnection).where(
                    FeishuConnection.created_by == "user_admin"
                )
            )
            credential = session.get(FeishuOAuthCredential, connection.id)
            assert credential is not None
            assert "access-admin" not in credential.access_token_encrypted
            assert "refresh-admin" not in credential.refresh_token_encrypted
            credential.access_expires_at = utcnow() - timedelta(seconds=1)
            session.commit()

        refreshed = client.get(
            "/api/v1/integrations/feishu/targets",
            params={
                "connection_id": admin_connections[0]["id"],
                "base_url": "appPersonal1",
                "scope_key": "collection.keyword",
            },
            headers=ADMIN,
        )
        assert refreshed.status_code == 200
        assert any(
            request.headers.get("Authorization") == "Bearer access-admin-refreshed"
            and request.url.path.endswith("/bitable/v1/apps/appPersonal1")
            for request in calls
        )

        disconnected = client.delete(
            f"/api/v1/integrations/feishu/connections/{admin_connections[0]['id']}",
            params={"retain_external_copies_confirmed": "true"},
            headers=ADMIN,
        )
        assert disconnected.status_code == 200
        with app.state.database.session_factory() as session:
            assert (
                session.get(FeishuOAuthCredential, admin_connections[0]["id"]) is None
            )
            legacy = session.get(FeishuSyncBinding, binding.json()["id"])
            legacy.target_base_id = "base_feishu_legacy_shared_target"
            legacy.status = "connection_invalid"
            session.commit()
        assert client.get(
            "/api/v1/integrations/feishu/bindings", headers=ADMIN
        ).json() == {"items": [], "total": 0}
