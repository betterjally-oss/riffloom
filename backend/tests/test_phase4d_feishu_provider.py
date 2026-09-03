from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.models import FeishuRecordLink
from app.providers import FeishuProviderError, FeishuSyncRecord, OpenApiFeishuProvider
from app.services.credential_store import EnvCredentialStore
from conftest import wait_for_terminal

APP_TOKEN = "app_test_token"
TABLE_ID = "tbl_test_only"
MULTI_TABLES = {
    "collection.single": TABLE_ID,
    "collection.keyword": "tbl_keyword",
    "collection.creator_content": "tbl_creator",
    "creation": "tbl_creation",
}
FIELDS = (
    "riffloom_record_id",
    "riffloom_record_version",
    "last_synced_at",
    "riffloom_status",
    "标题",
    "摘要",
    "标签",
)


def _provider(
    handler,
    *,
    writes_enabled: bool = False,
    app_id: str | None = "cli_test",
    app_secret: str | None = "secret-never-returned",
    target_tables: dict[str, str] | None = None,
    max_records_per_run: int = 1,
) -> OpenApiFeishuProvider:
    client = httpx.Client(
        base_url="https://open.feishu.cn/open-apis/",
        transport=httpx.MockTransport(handler),
    )
    return OpenApiFeishuProvider(
        credential_store=EnvCredentialStore(
            feishu_app_id=app_id,
            feishu_app_secret=app_secret,
        ),
        app_token=APP_TOKEN,
        test_table_id=TABLE_ID,
        smoke_scope="collection.single",
        writes_enabled=writes_enabled,
        max_records_per_run=max_records_per_run,
        base_url="https://open.feishu.cn/open-apis",
        timeout_seconds=2,
        target_tables=target_tables,
        client=client,
    )


def _record(*, remote_id: str | None = None) -> FeishuSyncRecord:
    return FeishuSyncRecord(
        source_record_id="col_test",
        source_version="v1",
        fields={"riffloom_record_id": "col_test", "标题": "只读 Gate 测试"},
        existing_remote_record_id=remote_id,
    )


def test_readonly_preflight_uses_cached_token_and_makes_zero_record_writes():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "tenant_access_token": "tenant-token-never-returned",
                    "expire": 7200,
                },
            )
        if request.url.path.endswith(f"/apps/{APP_TOKEN}/tables"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"table_id": TABLE_ID, "name": "Riffloom 4D-1 测试"}]
                    },
                },
            )
        if request.url.path.endswith(f"/tables/{TABLE_ID}/fields"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"field_name": name, "type": 1} for name in FIELDS]
                    },
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    provider = _provider(handler)
    metadata = provider.connection_metadata(
        workspace_id="ws_demo", tenant_name="测试租户"
    )
    targets = provider.targets()

    assert metadata["credential_ref"] == "env://feishu-self-built"
    assert "secret" not in repr(metadata).lower()
    assert "tenant-token-never-returned" not in repr((metadata, targets))
    assert targets[0]["base_id"].startswith("base_feishu_")
    assert targets[0]["tables"][0]["table_id"].startswith("table_feishu_")
    assert APP_TOKEN not in repr(targets)
    assert TABLE_ID not in repr(targets)
    assert {field["name"] for field in targets[0]["tables"][0]["fields"]} == set(FIELDS)
    assert (
        sum(path.endswith("/auth/v3/tenant_access_token/internal") for _, path in calls)
        == 1
    )
    assert not any("/records" in path for _, path in calls)


def test_real_provider_lists_and_writes_each_configured_target():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "token", "expire": 7200},
            )
        if request.url.path.endswith(f"/apps/{APP_TOKEN}/tables"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"table_id": table_id, "name": scope}
                            for scope, table_id in MULTI_TABLES.items()
                        ]
                    },
                },
            )
        if "/fields" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"field_name": name, "type": 1} for name in FIELDS]
                    },
                },
            )
        if request.method == "POST" and request.url.path.endswith(
            f"/tables/{MULTI_TABLES['collection.keyword']}/records"
        ):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"record": {"record_id": "rec_keyword"}}},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    provider = _provider(
        handler,
        writes_enabled=True,
        target_tables=MULTI_TABLES,
        max_records_per_run=30,
    )
    targets = provider.targets()[0]["tables"]
    keyword_target = next(
        item for item in targets if item["scope_key"] == "collection.keyword"
    )
    result = provider.upsert(
        binding_id="binding",
        target_base_id=provider.target_base_ref,
        target_table_id=keyword_target["table_id"],
        target_table_name=keyword_target["table_name"],
        attempt_no=1,
        records=[_record()],
    )

    assert provider.capabilities()["scopes"] == list(MULTI_TABLES)
    assert len(targets) == 4
    assert result.outcomes[0].remote_record_id == "rec_keyword"
    assert all(table_id not in repr(targets) for table_id in MULTI_TABLES.values())
    assert any(
        f"/tables/{MULTI_TABLES['collection.keyword']}/records" in path
        for _, path in calls
    )


def test_real_target_replaces_only_sandbox_links_and_opens_controlled_redirect(client):
    admin_headers = {
        "X-Riffloom-User": "user_admin",
        "X-Riffloom-Workspace": "ws_demo",
    }
    connection = client.post(
        "/api/v1/integrations/feishu/connections",
        headers=admin_headers,
        json={"tenant_name": "sandbox setup", "external_copy_confirmed": True},
    ).json()
    binding = client.post(
        "/api/v1/integrations/feishu/bindings",
        headers=admin_headers,
        json={
            "connection_id": connection["id"],
            "scope_key": "collection.single",
            "target_base_id": "base_sandbox_riffloom",
            "target_table_id": "table_collection_single",
            "target_table_name": "单篇采集库",
            "field_mapping": {},
            "external_copy_confirmed": True,
        },
    ).json()
    with client.app.state.database.session_factory() as session:
        session.add(
            FeishuRecordLink(
                workspace_id="ws_demo",
                binding_id=binding["id"],
                source_record_id="col_sandbox_only",
                remote_record_id="rec_sbx_test",
                source_version="v1",
            )
        )
        session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "token", "expire": 7200},
            )
        if request.url.path.endswith(f"/apps/{APP_TOKEN}/tables"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"items": [{"table_id": TABLE_ID, "name": "单篇采集测试"}]},
                },
            )
        if request.url.path.endswith(f"/tables/{TABLE_ID}/fields"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"field_name": name, "type": 1} for name in FIELDS]
                    },
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    provider = _provider(handler, writes_enabled=True)
    client.app.state.feishu_provider = provider
    client.app.state.worker.feishu_provider = provider
    real_connection = client.post(
        "/api/v1/integrations/feishu/connections",
        headers=admin_headers,
        json={"tenant_name": "real test", "external_copy_confirmed": True},
    ).json()
    target = client.get(
        "/api/v1/integrations/feishu/targets",
        headers=admin_headers,
        params={"connection_id": real_connection["id"]},
    ).json()["items"][0]
    table = target["tables"][0]
    rebound = client.post(
        "/api/v1/integrations/feishu/bindings",
        headers=admin_headers,
        json={
            "connection_id": real_connection["id"],
            "scope_key": "collection.single",
            "target_base_id": target["base_id"],
            "target_table_id": table["table_id"],
            "target_table_name": table["table_name"],
            "field_mapping": {},
            "external_copy_confirmed": True,
        },
    ).json()

    assert rebound["link_count"] == 0
    assert rebound["target_openable"] is True
    redirect = client.get(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}/target",
        headers=admin_headers,
        follow_redirects=False,
    )
    assert redirect.status_code == 307
    assert redirect.headers["location"].startswith("https://feishu.cn/base/")


def test_readonly_provider_rejects_upsert_without_any_http_call():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("read-disabled upsert must not call Feishu")

    provider = _provider(handler)
    batch = provider.upsert(
        binding_id="binding",
        target_base_id=provider.target_base_ref,
        target_table_id=provider.target_table_ref,
        target_table_name="test",
        attempt_no=1,
        records=[_record()],
    )

    assert calls == []
    assert batch.external_calls is False
    assert batch.outcomes[0].error["code"] == "FEISHU_REAL_WRITE_DISABLED"


def test_enabled_provider_creates_then_updates_only_confirmed_record():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "cached-token", "expire": 7200},
            )
        if request.method == "POST" and request.url.path.endswith(
            f"/tables/{TABLE_ID}/records"
        ):
            return httpx.Response(
                200, json={"code": 0, "data": {"record": {"record_id": "rec_1"}}}
            )
        if request.method == "PUT" and request.url.path.endswith(
            f"/tables/{TABLE_ID}/records/rec_1"
        ):
            return httpx.Response(
                200, json={"code": 0, "data": {"record": {"record_id": "rec_1"}}}
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    provider = _provider(handler, writes_enabled=True)
    created = provider.upsert(
        binding_id="binding",
        target_base_id=provider.target_base_ref,
        target_table_id=provider.target_table_ref,
        target_table_name="test",
        attempt_no=1,
        records=[_record()],
    )
    updated = provider.upsert(
        binding_id="binding",
        target_base_id=provider.target_base_ref,
        target_table_id=provider.target_table_ref,
        target_table_name="test",
        attempt_no=2,
        records=[_record(remote_id="rec_1")],
    )

    assert created.outcomes[0].action == "create"
    assert created.outcomes[0].remote_record_id == "rec_1"
    assert updated.outcomes[0].action == "update"
    assert (
        sum(path.endswith("/auth/v3/tenant_access_token/internal") for _, path in calls)
        == 1
    )
    assert [method for method, path in calls if "/records" in path] == ["POST", "PUT"]


def test_missing_credentials_and_scope_limits_fail_closed():
    provider = _provider(lambda _: pytest.fail("no HTTP call expected"), app_id=None)
    with pytest.raises(FeishuProviderError) as captured:
        provider.connection_metadata(workspace_id="ws", tenant_name="test")
    assert captured.value.code == "FEISHU_CREDENTIALS_MISSING"

    with pytest.raises(RuntimeError, match="单次记录上限"):
        OpenApiFeishuProvider(
            credential_store=EnvCredentialStore(
                feishu_app_id="id", feishu_app_secret="secret"
            ),
            app_token=APP_TOKEN,
            test_table_id=TABLE_ID,
            smoke_scope="collection.single",
            writes_enabled=False,
            max_records_per_run=31,
            base_url="https://open.feishu.cn/open-apis",
            timeout_seconds=2,
        )


def test_api_sync_gate_rejects_before_worker_or_external_call(client):
    admin_headers = {
        "X-Riffloom-User": "user_admin",
        "X-Riffloom-Workspace": "ws_demo",
    }
    lead_headers = {
        "X-Riffloom-User": "user_lead",
        "X-Riffloom-Workspace": "ws_demo",
    }
    connection = client.post(
        "/api/v1/integrations/feishu/connections",
        headers=admin_headers,
        json={"tenant_name": "sandbox setup", "external_copy_confirmed": True},
    ).json()
    targets = client.get(
        "/api/v1/integrations/feishu/targets",
        headers=admin_headers,
        params={"connection_id": connection["id"]},
    ).json()
    base = targets["items"][0]
    table = next(
        item for item in base["tables"] if item["scope_key"] == "collection.single"
    )
    binding = client.post(
        "/api/v1/integrations/feishu/bindings",
        headers=admin_headers,
        json={
            "connection_id": connection["id"],
            "scope_key": "collection.single",
            "target_base_id": base["base_id"],
            "target_table_id": table["table_id"],
            "target_table_name": table["table_name"],
            "field_mapping": {},
            "strategy": "manual_incremental",
            "external_copy_confirmed": True,
        },
    ).json()
    client.app.state.feishu_provider = _provider(
        lambda _: pytest.fail("write-disabled API gate must make no external calls")
    )

    response = client.post(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}/syncs",
        headers={**admin_headers, "Idempotency-Key": "phase4d-readonly-gate"},
        json={"mode": "full"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FEISHU_REAL_WRITE_DISABLED"


def test_worker_blocks_more_than_one_real_record_before_provider_call(client):
    admin_headers = {
        "X-Riffloom-User": "user_admin",
        "X-Riffloom-Workspace": "ws_demo",
    }
    lead_headers = {
        "X-Riffloom-User": "user_lead",
        "X-Riffloom-Workspace": "ws_demo",
    }
    source_ids = []
    for index in (1, 2):
        response = client.post(
            "/api/v1/collection-tasks",
            headers={**lead_headers, "Idempotency-Key": f"phase4d-cap-source-{index}"},
            json={
                "kind": "single",
                "platform": "riffloom-sandbox",
                "query": {"url": f"https://sandbox.riffloom.local/notes/cap-{index}"},
                "usage_confirmed": True,
                "limit": 1,
            },
        )
        assert response.status_code == 202
        completed = wait_for_terminal(client, response.json()["id"], lead_headers)
        assert completed["status"] == "success"
        source_ids.append(completed["result_refs"][0]["id"])

    record_writes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "token", "expire": 7200},
            )
        if request.url.path.endswith(f"/apps/{APP_TOKEN}/tables"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"items": [{"table_id": TABLE_ID, "name": "单篇采集测试"}]},
                },
            )
        if request.url.path.endswith(f"/tables/{TABLE_ID}/fields"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"field_name": name, "type": 1} for name in FIELDS]
                    },
                },
            )
        if request.method == "POST" and request.url.path.endswith(
            f"/tables/{TABLE_ID}/records"
        ):
            record_writes.append(
                json.loads(request.content)["fields"]["riffloom_record_id"]
            )
            return httpx.Response(
                200,
                json={"code": 0, "data": {"record": {"record_id": "rec_targeted"}}},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    provider = _provider(handler, writes_enabled=True)
    client.app.state.feishu_provider = provider
    client.app.state.worker.feishu_provider = provider

    connection = client.post(
        "/api/v1/integrations/feishu/connections",
        headers=admin_headers,
        json={"tenant_name": "real setup", "external_copy_confirmed": True},
    ).json()
    targets = client.get(
        "/api/v1/integrations/feishu/targets",
        headers=admin_headers,
        params={"connection_id": connection["id"]},
    ).json()
    base = targets["items"][0]
    table = next(
        item for item in base["tables"] if item["scope_key"] == "collection.single"
    )
    binding = client.post(
        "/api/v1/integrations/feishu/bindings",
        headers=admin_headers,
        json={
            "connection_id": connection["id"],
            "scope_key": "collection.single",
            "target_base_id": base["base_id"],
            "target_table_id": table["table_id"],
            "target_table_name": table["table_name"],
            "field_mapping": {},
            "strategy": "manual_incremental",
            "external_copy_confirmed": True,
        },
    ).json()
    response = client.post(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}/syncs",
        headers={**admin_headers, "Idempotency-Key": "phase4d-cap-sync"},
        json={"mode": "full"},
    )
    assert response.status_code == 202
    task = wait_for_terminal(client, response.json()["id"], admin_headers)
    assert task["status"] == "failed"
    assert task["error"]["code"] == "FEISHU_SMOKE_SCOPE_EXCEEDED"
    assert record_writes == []

    missing = client.post(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}/syncs",
        headers={**admin_headers, "Idempotency-Key": "phase4d-missing-source"},
        json={"mode": "full", "source_record_id": "col_missing"},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "FEISHU_SYNC_SOURCE_NOT_FOUND"

    targeted = client.post(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}/syncs",
        headers={**admin_headers, "Idempotency-Key": "phase4d-targeted-sync"},
        json={"mode": "full", "source_record_id": source_ids[0]},
    )
    assert targeted.status_code == 202
    completed = wait_for_terminal(client, targeted.json()["id"], admin_headers)
    assert completed["status"] == "success"
    assert completed["result_summary"]["total"] == 1
    assert completed["result_summary"]["external_calls"] is True
    assert record_writes == [source_ids[0]]

    with client.app.state.database.session_factory() as session:
        assert session.scalars(select(FeishuRecordLink.source_record_id)).all() == [
            source_ids[0]
        ]
