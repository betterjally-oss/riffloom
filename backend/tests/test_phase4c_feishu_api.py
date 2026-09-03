from __future__ import annotations

from sqlalchemy import func, select

from app.core.auth import AuthContext
from app.models import (
    AgentTask,
    FeishuRecordLink,
    FeishuSyncBinding,
    FeishuSyncItem,
    FeishuSyncRun,
    Membership,
    PilotMemberWorkspace,
    ProviderCall,
    TaskAttempt,
    User,
    Workspace,
)
from app.services.feishu_service import create_sync_run, execute_sync_run
from conftest import wait_for_terminal

ADMIN_HEADERS = {
    "X-Riffloom-User": "user_admin",
    "X-Riffloom-Workspace": "ws_demo",
}
LEAD_HEADERS = {
    "X-Riffloom-User": "user_lead",
    "X-Riffloom-Workspace": "ws_demo",
}
OTHER_HEADERS = {
    "X-Riffloom-User": "user_other",
    "X-Riffloom-Workspace": "ws_other",
}


def _collect(
    client, kind: str, *, key: str, value: str | None = None, headers=LEAD_HEADERS
) -> dict:
    field = {
        "single": "url",
        "keyword": "keyword",
        "creator_content": "creator",
        "creator_profile": "creator",
    }[kind]
    default = {
        "single": "https://sandbox.riffloom.local/notes/note-001",
        "keyword": "AI 工作流",
        "creator_content": "creator-001",
        "creator_profile": "creator-001",
    }[kind]
    response = client.post(
        "/api/v1/collection-tasks",
        headers={**headers, "Idempotency-Key": key},
        json={
            "kind": kind,
            "platform": "riffloom-sandbox",
            "query": {field: value or default},
            "usage_confirmed": True,
            "limit": 2 if kind in {"keyword", "creator_content"} else 1,
        },
    )
    assert response.status_code == 202
    return wait_for_terminal(client, response.json()["id"], headers)


def _seed_all_scopes(client) -> None:
    single = _collect(client, "single", key="phase4c-seed-single")
    _collect(client, "keyword", key="phase4c-seed-keyword")
    _collect(client, "creator_content", key="phase4c-seed-creator-content")
    _collect(client, "creator_profile", key="phase4c-seed-creator-profile")
    source_id = next(
        ref["id"] for ref in single["result_refs"] if ref["type"] == "collection"
    )
    breakdown = client.post(
        "/api/v1/breakdown-tasks",
        headers={**LEAD_HEADERS, "Idempotency-Key": "phase4c-seed-breakdown"},
        json={"prompt": "为飞书六表同步准备一条结构化拆解", "source_ids": [source_id]},
    )
    assert breakdown.status_code == 202
    assert (
        wait_for_terminal(client, breakdown.json()["id"], LEAD_HEADERS)["status"]
        == "success"
    )
    creation = client.post(
        "/api/v1/creation-tasks",
        headers={**LEAD_HEADERS, "Idempotency-Key": "phase4c-seed-creation"},
        json={
            "creation_type": "original",
            "prompt": "写一篇用于飞书单向同步验收的小红书文案",
            "knowledge_refs": [{"library_type": "collections", "record_id": source_id}],
            "target_platform": "小红书",
        },
    )
    assert creation.status_code == 202
    assert (
        wait_for_terminal(client, creation.json()["id"], LEAD_HEADERS)["status"]
        == "success"
    )


def _connect(client, *, headers=ADMIN_HEADERS) -> dict:
    response = client.post(
        "/api/v1/integrations/feishu/connections",
        headers=headers,
        json={"tenant_name": "示例团队 sandbox", "external_copy_confirmed": True},
    )
    assert response.status_code == 201
    return response.json()


def _binding(
    client,
    connection_id: str,
    scope: str,
    *,
    table_name: str | None = None,
    headers=ADMIN_HEADERS,
) -> dict:
    table_id = f"table_{scope.replace('.', '_')}"
    names = {
        "collection.single": "单篇采集库",
        "collection.keyword": "关键词采集库",
        "collection.creator_content": "博主内容库",
        "collection.creator_profile": "博主信息库",
        "breakdown": "拆解库",
        "creation": "创作库",
    }
    response = client.post(
        "/api/v1/integrations/feishu/bindings",
        headers=headers,
        json={
            "connection_id": connection_id,
            "scope_key": scope,
            "target_base_id": "base_sandbox_riffloom",
            "target_table_id": table_id,
            "target_table_name": table_name or names[scope],
            "field_mapping": {},
            "strategy": "manual_incremental",
            "external_copy_confirmed": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _sync(
    client, binding_id: str, *, mode: str, key: str, headers=ADMIN_HEADERS
) -> dict:
    response = client.post(
        f"/api/v1/integrations/feishu/bindings/{binding_id}/syncs",
        headers={**headers, "Idempotency-Key": key},
        json={"mode": mode},
    )
    assert response.status_code == 202, response.text
    return wait_for_terminal(client, response.json()["id"], headers)


def test_provider_and_connection_are_explicit_sandbox_without_credentials(
    client, headers
):
    provider = client.get("/api/v1/integrations/feishu/provider", headers=headers)
    assert provider.status_code == 200
    assert provider.json()["provider"] == "sandbox-feishu-v1"
    assert provider.json()["external_calls"] is False
    assert len(provider.json()["scopes"]) == 6

    own_listing = client.get("/api/v1/integrations/feishu/connections", headers=headers)
    assert own_listing.status_code == 200
    assert own_listing.json()["total"] == 0
    connection = _connect(client)
    assert "credential_ref" not in connection
    listing = client.get(
        "/api/v1/integrations/feishu/connections", headers=ADMIN_HEADERS
    ).json()
    assert listing["total"] == 1
    assert "credential_ref" not in listing["items"][0]

    targets = client.get(
        f"/api/v1/integrations/feishu/targets?connection_id={connection['id']}",
        headers=ADMIN_HEADERS,
    )
    assert targets.status_code == 200
    assert targets.json()["external_calls"] is False
    assert len(targets.json()["items"][0]["tables"]) == 6


def test_isolated_pilot_member_can_configure_and_sync_own_feishu_copy(client, headers):
    isolated_headers = {
        "X-Riffloom-User": "user_isolated_feishu",
        "X-Riffloom-Workspace": "ws_isolated_feishu",
    }
    with client.app.state.database.session_factory() as session:
        session.add_all(
            [
                User(id="user_isolated_feishu", name="飞书测试成员", status="active"),
                Workspace(
                    id="ws_isolated_feishu",
                    name="飞书测试成员 · 独立空间",
                    status="active",
                    config={},
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                Membership(
                    id="mem_isolated_feishu",
                    workspace_id="ws_isolated_feishu",
                    user_id="user_isolated_feishu",
                    role="editor",
                ),
                PilotMemberWorkspace(
                    id="pmw_isolated_feishu",
                    workspace_id="ws_isolated_feishu",
                    parent_workspace_id="ws_demo",
                    member_user_id="user_isolated_feishu",
                ),
            ]
        )
        session.commit()

    assert (
        client.get("/api/v1/integrations/feishu/provider", headers=headers).json()[
            "can_configure"
        ]
        is True
    )
    provider = client.get(
        "/api/v1/integrations/feishu/provider", headers=isolated_headers
    )
    assert provider.status_code == 200
    assert provider.json()["can_configure"] is True

    _collect(
        client,
        "single",
        key="isolated-feishu-source",
        headers=isolated_headers,
    )
    connection = _connect(client, headers=isolated_headers)
    binding = _binding(
        client,
        connection["id"],
        "collection.single",
        headers=isolated_headers,
    )
    task = _sync(
        client,
        binding["id"],
        mode="full",
        key="isolated-feishu-sync",
        headers=isolated_headers,
    )
    assert task["status"] == "success", task
    assert task["result_summary"]["success"] == 1


def test_six_scopes_full_then_incremental_are_idempotent(client, headers):
    _seed_all_scopes(client)
    connection = _connect(client)
    scopes = [
        "collection.single",
        "collection.keyword",
        "collection.creator_content",
        "collection.creator_profile",
        "breakdown",
        "creation",
    ]
    total_links = 0
    for index, scope in enumerate(scopes):
        binding = _binding(client, connection["id"], scope)
        full = _sync(
            client,
            binding["id"],
            mode="full",
            key=f"phase4c-full-{index}",
        )
        assert full["status"] == "success"
        assert full["result_summary"]["external_calls"] is False
        assert full["result_summary"]["success"] >= 1
        total_links += full["result_summary"]["success"]
        run_id = next(
            ref["id"] for ref in full["result_refs"] if ref["type"] == "feishu_sync_run"
        )
        run = client.get(
            f"/api/v1/integrations/feishu/syncs/{run_id}", headers=ADMIN_HEADERS
        ).json()
        assert run["success_count"] == full["result_summary"]["success"]

        incremental = _sync(
            client,
            binding["id"],
            mode="incremental",
            key=f"phase4c-incremental-{index}",
        )
        assert incremental["status"] == "success"
        assert incremental["result_summary"]["success"] == 0
        assert incremental["result_summary"]["skipped"] >= 1

    with client.app.state.database.session_factory() as session:
        assert session.scalar(select(func.count(FeishuRecordLink.id))) == total_links
        remote_ids = session.scalars(select(FeishuRecordLink.remote_record_id)).all()
        assert len(remote_ids) == len(set(remote_ids))


def test_partial_sync_retries_only_failed_record(client):
    _collect(client, "keyword", key="phase4c-partial-source", value="部分失败验收")
    connection = _connect(client)
    binding = _binding(
        client,
        connection["id"],
        "collection.keyword",
        table_name="关键词采集库 [partial]",
    )
    task = _sync(client, binding["id"], mode="full", key="phase4c-partial-full")
    assert task["status"] == "partial_success"
    assert task["result_summary"]["success"] == 1
    assert task["result_summary"]["failed"] == 1
    run_id = task["result_refs"][0]["id"]

    retry = client.post(
        f"/api/v1/integrations/feishu/syncs/{run_id}/retries",
        headers={**ADMIN_HEADERS, "Idempotency-Key": "phase4c-partial-retry"},
    )
    assert retry.status_code == 202
    recovered = wait_for_terminal(client, retry.json()["id"], ADMIN_HEADERS)
    assert recovered["status"] == "success"
    assert recovered["result_summary"]["total"] == 1
    assert recovered["result_summary"]["success"] == 1

    with client.app.state.database.session_factory() as session:
        assert session.scalar(select(func.count(FeishuRecordLink.id))) == 2
        runs = session.scalars(
            select(FeishuSyncRun).order_by(FeishuSyncRun.created_at)
        ).all()
        assert len(runs) == 2
        assert (
            session.scalar(
                select(func.count(FeishuSyncItem.id)).where(
                    FeishuSyncItem.run_id == runs[1].id
                )
            )
            == 1
        )


def test_running_feishu_sync_can_be_cancelled(client, monkeypatch):
    _collect(client, "single", key="phase4c-cancel-source")
    connection = _connect(client)
    binding = _binding(client, connection["id"], "collection.single")
    monkeypatch.setattr(
        client.app.state.worker, "submit", lambda _task_id, **_kwargs: True
    )
    response = client.post(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}/syncs",
        headers={**ADMIN_HEADERS, "Idempotency-Key": "phase4c-cancel-sync"},
        json={"mode": "full"},
    )
    assert response.status_code == 202
    task_id = response.json()["id"]
    with client.app.state.database.session_factory() as session:
        task = session.get(AgentTask, task_id)
        run = session.scalar(
            select(FeishuSyncRun).where(FeishuSyncRun.task_id == task_id)
        )
        attempt = session.scalar(
            select(TaskAttempt).where(TaskAttempt.task_id == task_id)
        )
        assert task is not None and run is not None and attempt is not None
        task.status = run.status = attempt.status = "running"
        session.commit()

    cancelled = client.post(f"/api/v1/tasks/{task_id}/cancel", headers=ADMIN_HEADERS)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    run_id = next(ref["id"] for ref in response.json()["result_refs"])
    run = client.get(
        f"/api/v1/integrations/feishu/syncs/{run_id}", headers=ADMIN_HEADERS
    )
    assert run.json()["status"] == "cancelled"


def test_role_pause_disconnect_and_workspace_boundaries(client, headers):
    _collect(client, "single", key="phase4c-permission-source")
    connection = _connect(client)
    binding = _binding(client, connection["id"], "collection.single")

    denied = client.post(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}/syncs",
        headers={**headers, "Idempotency-Key": "phase4c-editor-denied"},
        json={"mode": "full"},
    )
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "FEISHU_BINDING_NOT_FOUND"

    paused = client.patch(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}",
        headers=ADMIN_HEADERS,
        json={"paused": True},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    blocked = client.post(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}/syncs",
        headers={**ADMIN_HEADERS, "Idempotency-Key": "phase4c-paused-denied"},
        json={"mode": "full"},
    )
    assert blocked.status_code == 409

    assert (
        client.get(
            f"/api/v1/integrations/feishu/syncs/frun_missing", headers=OTHER_HEADERS
        ).status_code
        == 404
    )
    confirmation_required = client.delete(
        f"/api/v1/integrations/feishu/connections/{connection['id']}?retain_external_copies_confirmed=false",
        headers=ADMIN_HEADERS,
    )
    assert confirmation_required.status_code == 422
    assert (
        confirmation_required.json()["error"]["code"]
        == "FEISHU_DISCONNECT_CONFIRMATION_REQUIRED"
    )
    disconnected = client.delete(
        f"/api/v1/integrations/feishu/connections/{connection['id']}?retain_external_copies_confirmed=true",
        headers=ADMIN_HEADERS,
    )
    assert disconnected.status_code == 200
    assert disconnected.json()["status"] == "disconnected"
    listed = client.get(
        "/api/v1/integrations/feishu/bindings", headers=ADMIN_HEADERS
    ).json()
    assert listed["items"][0]["status"] == "connection_invalid"
    _connect(client)
    restored = client.get(
        "/api/v1/integrations/feishu/bindings", headers=ADMIN_HEADERS
    ).json()
    assert restored["items"][0]["status"] == "paused"


def test_preflight_rate_limit_retry_and_auth_expiry_are_structured(client):
    _collect(client, "single", key="phase4c-errors-source")
    connection = _connect(client)
    bad_preflight = client.post(
        "/api/v1/integrations/feishu/bindings/preflight",
        headers=ADMIN_HEADERS,
        json={
            "connection_id": connection["id"],
            "scope_key": "collection.single",
            "target_base_id": "base_sandbox_riffloom",
            "target_table_id": "table_collection_single",
            "target_table_name": "单篇采集库 [field-error]",
            "field_mapping": {},
        },
    )
    assert bad_preflight.status_code == 200
    assert bad_preflight.json()["valid"] is False
    assert bad_preflight.json()["errors"][0]["code"] == "FEISHU_FIELD_TYPE_MISMATCH"

    binding = _binding(
        client,
        connection["id"],
        "collection.single",
        table_name="单篇采集库 [rate-limit]",
    )
    limited = _sync(client, binding["id"], mode="full", key="phase4c-rate-limited")
    assert limited["status"] == "failed"
    assert limited["error"]["code"] == "FEISHU_RATE_LIMITED"
    run_id = limited["result_refs"][0]["id"]
    retry = client.post(
        f"/api/v1/integrations/feishu/syncs/{run_id}/retries",
        headers={**ADMIN_HEADERS, "Idempotency-Key": "phase4c-rate-retry"},
    )
    assert retry.status_code == 202
    assert (
        wait_for_terminal(client, retry.json()["id"], ADMIN_HEADERS)["status"]
        == "success"
    )

    renamed = client.patch(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}",
        headers=ADMIN_HEADERS,
        json={"target_table_name": "单篇采集库 [auth-expired]"},
    )
    assert renamed.status_code == 200
    expired = _sync(client, binding["id"], mode="full", key="phase4c-auth-expired")
    assert expired["status"] == "failed"
    assert expired["error"]["code"] == "FEISHU_AUTH_EXPIRED"
    connections = client.get(
        "/api/v1/integrations/feishu/connections", headers=ADMIN_HEADERS
    ).json()
    assert connections["items"][0]["status"] == "expired"

    reconnected = _connect(client)
    assert reconnected["id"] == connection["id"]
    restored = client.get(
        "/api/v1/integrations/feishu/bindings", headers=ADMIN_HEADERS
    ).json()
    assert restored["items"][0]["status"] == "active"

    renamed = client.patch(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}",
        headers=ADMIN_HEADERS,
        json={"target_table_name": "单篇采集库 [target-deleted]"},
    )
    assert renamed.status_code == 200
    deleted = _sync(client, binding["id"], mode="full", key="phase4c-target-deleted")
    assert deleted["status"] == "failed"
    assert deleted["error"]["code"] == "FEISHU_TARGET_DELETED"
    not_retryable = client.post(
        f"/api/v1/integrations/feishu/syncs/{deleted['result_refs'][0]['id']}/retries",
        headers={**ADMIN_HEADERS, "Idempotency-Key": "phase4c-target-retry"},
    )
    assert not_retryable.status_code == 409
    assert not_retryable.json()["error"]["code"] == "FEISHU_SYNC_NOT_RETRYABLE"

    with client.app.state.database.session_factory() as session:
        failed_calls = session.scalars(
            select(ProviderCall).where(ProviderCall.task_id == deleted["id"])
        ).all()
        assert failed_calls[0].status == "failed"
        assert failed_calls[0].error_type == "FEISHU_TARGET_DELETED"


def test_sync_request_idempotency_reuses_task_and_run(client):
    _collect(client, "single", key="phase4c-idempotency-source")
    connection = _connect(client)
    binding = _binding(client, connection["id"], "collection.single")
    request_headers = {**ADMIN_HEADERS, "Idempotency-Key": "phase4c-sync-idempotent"}
    first = client.post(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}/syncs",
        headers=request_headers,
        json={"mode": "full"},
    )
    second = client.post(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}/syncs",
        headers=request_headers,
        json={"mode": "full"},
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    conflict = client.post(
        f"/api/v1/integrations/feishu/bindings/{binding['id']}/syncs",
        headers=request_headers,
        json={"mode": "incremental"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    wait_for_terminal(client, first.json()["id"], ADMIN_HEADERS)
    with client.app.state.database.session_factory() as session:
        assert session.scalar(select(func.count(FeishuSyncRun.id))) == 1
        assert (
            "source_record_id"
            not in session.get(AgentTask, first.json()["id"]).input_snapshot
        )


def test_worker_rechecks_pause_before_provider_upsert(client):
    _collect(client, "single", key="phase4c-worker-recheck-source")
    connection = _connect(client)
    binding = _binding(client, connection["id"], "collection.single")
    context = AuthContext(
        user_id="user_admin",
        workspace_id="ws_demo",
        role="admin",
        user_name="管理员",
        workspace_name="示例内容团队",
    )
    with client.app.state.database.session_factory() as session:
        task, run, created = create_sync_run(
            session,
            context,
            binding["id"],
            "full",
            "phase4c-worker-recheck",
            client.app.state.feishu_provider,
        )
        assert created is True
        task_id = task.id
        run_id = run.id
        stored_binding = session.get(FeishuSyncBinding, binding["id"])
        assert stored_binding is not None
        stored_binding.status = "paused"
        session.commit()

    with client.app.state.database.session_factory() as session:
        task = session.get(AgentTask, task_id)
        assert task is not None
        execute_sync_run(
            session,
            task=task,
            attempt_no=1,
            provider=client.app.state.feishu_provider,
        )

    with client.app.state.database.session_factory() as session:
        failed_task = session.get(AgentTask, task_id)
        failed_run = session.get(FeishuSyncRun, run_id)
        assert failed_task is not None and failed_task.status == "failed"
        assert failed_task.error["code"] == "FEISHU_BINDING_PAUSED"
        assert failed_run is not None and failed_run.status == "failed"
        assert (
            session.scalar(
                select(func.count(ProviderCall.id)).where(
                    ProviderCall.task_id == task_id
                )
            )
            == 0
        )
