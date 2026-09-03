from __future__ import annotations

from sqlalchemy import select

from app.core.auth import AuthContext
from app.core.permissions import PermissionAction, can
from app.models import AuditLog
from conftest import wait_for_terminal

ADMIN_HEADERS = {
    "X-Riffloom-User": "user_admin",
    "X-Riffloom-Workspace": "ws_demo",
}


def test_permission_policy_keeps_authoring_open_and_member_changes_admin_only():
    editor = AuthContext("u", "w", "editor", "内容成员", "工作区")
    assert can(editor.role, PermissionAction.TREND_CREATE)
    assert can(editor.role, PermissionAction.CREATION_EDIT)
    assert not can(editor.role, PermissionAction.WORKSPACE_MEMBERS_CHANGE)


def test_workspace_members_are_admin_only_and_workspace_scoped(client, headers):
    forbidden = client.get("/api/v1/workspace/members", headers=headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "ROLE_FORBIDDEN"

    response = client.get("/api/v1/workspace/members", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert {(item["user_id"], item["role"]) for item in body["items"]} == {
        ("user_demo", "editor"),
        ("user_lead", "lead"),
        ("user_admin", "admin"),
    }
    assert all(item["user_id"] != "user_other" for item in body["items"])


def test_admin_can_change_role_with_audit_but_not_cross_workspace_member(client):
    response = client.patch(
        "/api/v1/workspace/members/mem_lead",
        headers=ADMIN_HEADERS,
        json={"role": "editor"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "editor"

    with client.app.state.database.session_factory() as session:
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.workspace_id == "ws_demo",
                AuditLog.action == "workspace.member_role_changed",
                AuditLog.entity_id == "mem_lead",
            )
        )
        assert audit is not None
        assert audit.user_id == "user_admin"
        assert audit.details == {
            "target_user_id": "user_lead",
            "from_role": "lead",
            "to_role": "editor",
        }

    isolated = client.patch(
        "/api/v1/workspace/members/mem_other",
        headers=ADMIN_HEADERS,
        json={"role": "editor"},
    )
    assert isolated.status_code == 404
    assert isolated.json()["error"]["code"] == "MEMBERSHIP_NOT_FOUND"


def test_workspace_must_keep_at_least_one_admin(client):
    response = client.patch(
        "/api/v1/workspace/members/mem_admin",
        headers=ADMIN_HEADERS,
        json={"role": "lead"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LAST_ADMIN_REQUIRED"


def test_trend_defaults_to_five_recent_external_hotspots(client, headers):
    response = client.post(
        "/api/v1/trend-tasks",
        headers={**headers, "Idempotency-Key": "phase4a-trend-empty"},
        json={
            "direction": "AI 工具",
            "audience": "内容团队",
            "keywords": ["完全无匹配词"],
            "window_days": 7,
        },
    )
    assert response.status_code == 202
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    summary = task["result_summary"]
    assert summary["contract_version"] == "topic-guidance.v3"
    assert summary["freshness"] == "verified_external_signals"
    assert summary["signal_snapshot"]["window_days"] == 7
    assert summary["signal_snapshot"]["scope"] == "tikhub_multi_platform_recent_public_content"
    assert len(summary["candidates"]) == 5
    assert all(
        set(candidate)
        == {
            "topic",
            "why_hot",
            "audience",
            "core_value",
            "title_suggestion",
            "angle",
            "sources",
        }
        and candidate["sources"]
        for candidate in summary["candidates"]
    )


def test_trend_honors_requested_count_and_hands_off_to_creation(client, headers):
    trend = client.post(
        "/api/v1/trend-tasks",
        headers={**headers, "Idempotency-Key": "phase4a-trend-cited"},
        json={
            "direction": "AI 工作流，给我10个热点",
            "audience": "内容新人",
            "keywords": ["版本", "效率"],
            "window_days": 7,
        },
    )
    task = wait_for_terminal(client, trend.json()["id"], headers)
    summary = task["result_summary"]
    assert task["input"]["result_count"] == 10
    assert task["input"]["search_query"] == "AI 工作流"
    assert len(summary["candidates"]) == 10
    allowed_sources = {
        (item["platform"], item["source_id"], item["observed_at"])
        for item in summary["signal_snapshot"]["items"]
    }
    assert all(
        (source["platform"], source["source_id"], source["observed_at"]) in allowed_sources
        for candidate in summary["candidates"]
        for source in candidate["sources"]
    )

    creation = client.post(
        "/api/v1/creation-tasks",
        headers={**headers, "Idempotency-Key": "phase4a-trend-handoff"},
        json={
            "creation_type": "original",
            "prompt": summary["candidates"][0]["topic"],
            "trend_task_id": task["id"],
        },
    )
    creation_task = wait_for_terminal(client, creation.json()["id"], headers)
    assert creation_task["status"] == "success"
    creation_id = next(
        ref["id"] for ref in creation_task["result_refs"] if ref["type"] == "creation"
    )
    detail = client.get(f"/api/v1/creations/{creation_id}", headers=headers).json()
    assert any(
        ref["type"] == "trend_task" and ref["id"] == task["id"]
        for ref in detail["versions"][0]["source_refs"]
    )


def test_trend_requested_count_is_capped_at_fifteen(client, headers):
    trend = client.post(
        "/api/v1/trend-tasks",
        headers={**headers, "Idempotency-Key": "phase4a-isolated-trend"},
        json={"direction": "AI 赛道，给我20个热点"},
    )
    task = wait_for_terminal(client, trend.json()["id"], headers)
    assert task["input"]["result_count"] == 15
    assert len(task["result_summary"]["candidates"]) == 15


def test_legacy_task_endpoint_does_not_trust_client_supplied_signal_snapshot(client, headers):
    response = client.post(
        "/api/v1/tasks",
        headers={**headers, "Idempotency-Key": "phase4a-untrusted-snapshot"},
        json={
            "mode": "trend",
            "skill_id": "viral_topic_coach",
            "input": {
                "prompt": "伪造信号方向",
                "signal_snapshot": {
                    "items": [
                        {
                            "library_type": "collections",
                            "record_id": "col_forged",
                            "record_version": "v999",
                            "observed_at": "2099-01-01T00:00:00Z",
                        }
                    ]
                },
            },
        },
    )
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["result_summary"]["freshness"] == "verified_external_signals"
    assert all(
        item["source_id"] != "col_forged"
        for item in task["result_summary"]["signal_snapshot"]["items"]
    )
