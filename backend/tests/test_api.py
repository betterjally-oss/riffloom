from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AgentTask, AuditLog
from app.providers.models import MockGenerationProvider, ModelProviderError
from conftest import wait_for_terminal


def task_payload(**task_input):
    return {
        "url": "https://sandbox.riffloom.local/notes/note-001?access=transient",
        "prompt": "请生成职场新人版本",
        "usage_confirmed": True,
        **task_input,
    }


def create_task(
    client: TestClient,
    headers: dict[str, str],
    *,
    key: str = "phase1-flow-001",
    payload: dict | None = None,
):
    body = payload or task_payload()
    return client.post(
        "/api/v1/tasks" if "skill_id" in body else "/api/v1/collect-rewrite-tasks",
        headers={**headers, "Idempotency-Key": key},
        json=body,
    )


def test_health_and_demo_session(client: TestClient, headers: dict[str, str]):
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["database"] == "ok"
    assert health.json()["model_provider"] == "mock-v1"
    assert health.json()["model_routing"]["text"]["model"] == "mock-riffloom-v1"
    assert health.headers["X-Request-ID"].startswith("req_")
    assert health.headers["X-Trace-ID"].startswith("trc_")

    session = client.get("/api/v1/session", headers=headers)
    assert session.status_code == 200
    assert session.json() == {
        "user_id": "user_demo",
        "user_name": "示例用户",
        "workspace_id": "ws_demo",
        "workspace_name": "示例内容团队",
        "role": "editor",
        "auth_mode": "demo_headers",
        "onboarding_completed": False,
    }


def test_user_can_complete_onboarding_once(client: TestClient, headers: dict[str, str]):
    completed = client.post("/api/v1/session/onboarding/complete", headers=headers)
    assert completed.status_code == 200
    assert completed.json()["onboarding_completed"] is True

    repeated = client.post("/api/v1/session/onboarding/complete", headers=headers)
    assert repeated.status_code == 200
    assert client.get("/api/v1/session", headers=headers).json()[
        "onboarding_completed"
    ] is True


def test_task_runs_and_persists_three_library_results(
    client: TestClient, headers: dict[str, str]
):
    response = create_task(client, headers)
    assert response.status_code == 202
    task_id = response.json()["id"]

    task = wait_for_terminal(client, task_id, headers)
    assert task["status"] == "success"
    assert task["progress"] == 100
    assert {ref["type"] for ref in task["result_refs"]} == {
        "collection",
        "breakdown",
        "creation",
    }

    for library_type in ("collections", "breakdowns", "creations"):
        library = client.get(f"/api/v1/libraries/{library_type}", headers=headers)
        assert library.status_code == 200
        assert library.json()["total"] == 1
        assert library.json()["items"][0]["task_id"] == task_id


def test_riffloom_agent_chats_and_reuses_the_conversation(
    client: TestClient, headers: dict[str, str]
):
    first_response = create_task(
        client,
        headers,
        key="agent-chat-001",
        payload={
            "mode": "agent",
            "skill_id": "riffloom_agent",
            "input": {"prompt": "你好，你能做什么？"},
        },
    )
    assert first_response.status_code == 202
    second_response = create_task(
        client,
        headers,
        key="agent-chat-002",
        payload={
            "mode": "agent",
            "skill_id": "riffloom_agent",
            "input": {"prompt": "我想拆解一篇小红书内容"},
            "conversation_id": first_response.json()["conversation_id"],
        },
    )
    assert second_response.status_code == 202
    first = wait_for_terminal(client, first_response.json()["id"], headers)
    second = wait_for_terminal(client, second_response.json()["id"], headers)
    assert first["status"] == "success"
    assert "Riffloom 智能体" in first["result_summary"]["reply"]
    assert first["result_summary"]["model"] == "mock-riffloom-v1"
    assert second["status"] == "success"
    assert second["conversation_id"] == first["conversation_id"]
    assert "Riffloom" in second["result_summary"]["reply"]

    history = client.get("/api/v1/conversations", headers=headers)
    assert history.status_code == 200
    assert history.json()["items"] == [
        {
            "id": first["conversation_id"],
            "mode": "agent",
            "title": "你好，你能做什么？",
            "message_count": 4,
            "updated_at": history.json()["items"][0]["updated_at"],
        }
    ]

    detail = client.get(
        f"/api/v1/conversations/{first['conversation_id']}", headers=headers
    )
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert [item["content"] for item in messages if item["role"] == "user"] == [
        "你好，你能做什么？",
        "我想拆解一篇小红书内容",
    ]
    assert {item["content"] for item in messages if item["role"] == "assistant"} == {
        first["result_summary"]["reply"],
        second["result_summary"]["reply"],
    }

    other_headers = {
        "X-Riffloom-User": "user_other",
        "X-Riffloom-Workspace": "ws_other",
    }
    renamed = client.patch(
        f"/api/v1/conversations/{first['conversation_id']}",
        headers=headers,
        json={"title": "我的 AI 内容助手"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "我的 AI 内容助手"
    assert client.get("/api/v1/conversations", headers=headers).json()["items"][0][
        "title"
    ] == "我的 AI 内容助手"
    assert (
        client.patch(
            f"/api/v1/conversations/{first['conversation_id']}",
            headers=other_headers,
            json={"title": "越权修改"},
        ).status_code
        == 404
    )
    assert (
        client.get("/api/v1/conversations", headers=other_headers).json()["items"] == []
    )
    assert (
        client.get(
            f"/api/v1/conversations/{first['conversation_id']}", headers=other_headers
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/conversations/{first['conversation_id']}", headers=headers
        ).status_code
        == 204
    )
    assert client.get("/api/v1/conversations", headers=headers).json()["items"] == []
    assert (
        client.get(
            f"/api/v1/conversations/{first['conversation_id']}", headers=headers
        ).status_code
        == 404
    )


def test_non_agent_chat_is_real_conversation_history(
    client: TestClient, headers: dict[str, str]
):
    response = create_task(
        client,
        headers,
        key="creation-chat-history-001",
        payload={
            "mode": "creation",
            "skill_id": "original_copy",
            "input": {"prompt": "验证创作对话会进入真实历史"},
        },
    )
    assert response.status_code == 202
    background = create_task(
        client,
        headers,
        key=f"agent-{'a' * 64}",
        payload={
            "mode": "creation",
            "skill_id": "original_copy",
            "input": {"prompt": "后台委派任务不应伪装成一次用户对话"},
        },
    )
    assert background.status_code == 202
    assert wait_for_terminal(client, response.json()["id"], headers)["status"] == "success"

    history = client.get("/api/v1/conversations", headers=headers)
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["mode"] == "creation"
    assert history.json()["items"][0]["title"] == "验证创作对话会进入真实历史"

    detail = client.get(
        f"/api/v1/conversations/{response.json()['conversation_id']}", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["mode"] == "creation"
    assert detail.json()["messages"] == [
        {"role": "user", "content": "验证创作对话会进入真实历史"},
        {"role": "assistant", "content": "创作版本已入库"},
    ]


def test_idempotency_reuses_task_and_rejects_changed_payload(
    client: TestClient, headers: dict[str, str]
):
    first = create_task(client, headers, key="same-request-001")
    second = create_task(client, headers, key="same-request-001")
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]

    changed = create_task(
        client,
        headers,
        key="same-request-001",
        payload=task_payload(prompt="https://example.com/changed 不同输入"),
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_partial_failure_keeps_result_and_retry_creates_new_attempt(
    client: TestClient, headers: dict[str, str]
):
    class FailOnce(MockGenerationProvider):
        failed = False

        def generate(self, operation, payload, output_schema):
            if operation == "breakdown" and not self.failed:
                self.failed = True
                raise ModelProviderError("MODEL_TIMEOUT", "首次拆解失败")
            return super().generate(operation, payload, output_schema)

    client.app.state.worker.generation_provider = FailOnce()
    response = create_task(
        client,
        headers,
        key="failure-flow-001",
        payload=task_payload(),
    )
    task_id = response.json()["id"]
    failed = wait_for_terminal(client, task_id, headers)
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "MODEL_TIMEOUT"
    assert failed["error"]["retryable"] is True
    assert (
        client.get("/api/v1/libraries/collections", headers=headers).json()["total"]
        == 1
    )

    retry = client.post(
        f"/api/v1/tasks/{task_id}/retry",
        headers={**headers, "Idempotency-Key": "failure-retry-001"},
    )
    assert retry.status_code == 202
    completed = wait_for_terminal(client, task_id, headers)
    assert completed["status"] == "success"
    assert completed["current_attempt"] == 2
    assert completed["retry_count"] == 1
    assert [attempt["attempt_no"] for attempt in completed["attempts"]] == [1, 2]


def test_workspace_isolation_returns_not_found(
    client: TestClient, headers: dict[str, str]
):
    response = create_task(client, headers, key="isolation-flow-001")
    task_id = response.json()["id"]
    other_headers = {
        "X-Riffloom-User": "user_other",
        "X-Riffloom-Workspace": "ws_other",
    }
    hidden = client.get(f"/api/v1/tasks/{task_id}", headers=other_headers)
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "TASK_NOT_FOUND"

    forged = client.get(
        "/api/v1/session",
        headers={
            "X-Riffloom-User": "user_demo",
            "X-Riffloom-Workspace": "ws_other",
        },
    )
    assert forged.status_code == 403


def test_task_cancellation_is_idempotent_and_permission_scoped(
    client, headers, monkeypatch
):
    monkeypatch.setattr(
        client.app.state.worker, "submit", lambda _task_id, **_kwargs: True
    )
    queued = create_task(client, headers, key="cancel-own-task-001")
    task_id = queued.json()["id"]

    cancelled = client.post(f"/api/v1/tasks/{task_id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["attempts"][0]["status"] == "cancelled"
    assert (
        client.post(f"/api/v1/tasks/{task_id}/cancel", headers=headers).json()["status"]
        == "cancelled"
    )

    lead_task = create_task(
        client,
        {"X-Riffloom-User": "user_lead", "X-Riffloom-Workspace": "ws_demo"},
        key="cancel-lead-task-001",
    ).json()
    denied = client.post(f"/api/v1/tasks/{lead_task['id']}/cancel", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "TASK_CANCEL_FORBIDDEN"
    admin = {"X-Riffloom-User": "user_admin", "X-Riffloom-Workspace": "ws_demo"}
    assert (
        client.post(
            f"/api/v1/tasks/{lead_task['id']}/cancel", headers=admin
        ).status_code
        == 200
    )

    with client.app.state.database.session_factory() as session:
        task = session.get(AgentTask, task_id)
        assert task is not None and task.status == "cancelled"
        assert (
            session.scalar(
                select(AuditLog).where(
                    AuditLog.entity_id == task_id,
                    AuditLog.action == "task.cancelled",
                )
            )
            is not None
        )


def test_creation_adoption_points_to_an_explicit_version(
    client: TestClient, headers: dict[str, str]
):
    response = create_task(client, headers, key="adoption-flow-001")
    task = wait_for_terminal(client, response.json()["id"], headers)
    creation_id = next(
        ref["id"] for ref in task["result_refs"] if ref["type"] == "creation"
    )
    detail = client.get(f"/api/v1/creations/{creation_id}", headers=headers)
    assert detail.status_code == 200
    version_id = detail.json()["current_version_id"]

    adoption_headers = {**headers, "Idempotency-Key": "adopt-version-001"}
    adopted = client.post(
        f"/api/v1/creations/{creation_id}/adoptions",
        headers=adoption_headers,
        json={"version_id": version_id},
    )
    assert adopted.status_code == 200
    assert adopted.json()["version_id"] == version_id
    assert adopted.json()["status"] == "adopted"

    repeated = client.post(
        f"/api/v1/creations/{creation_id}/adoptions",
        headers=adoption_headers,
        json={"version_id": version_id},
    )
    assert repeated.status_code == 200
    assert repeated.json() == adopted.json()


def test_admin_sees_persisted_pilot_metrics(
    client: TestClient, headers: dict[str, str]
):
    completed = create_task(client, headers, key="pilot-metrics-success")
    completed_task = wait_for_terminal(client, completed.json()["id"], headers)
    creation_id = next(
        ref["id"] for ref in completed_task["result_refs"] if ref["type"] == "creation"
    )
    detail = client.get(f"/api/v1/creations/{creation_id}", headers=headers).json()
    client.post(
        f"/api/v1/creations/{creation_id}/adoptions",
        headers={**headers, "Idempotency-Key": "pilot-metrics-adopt"},
        json={"version_id": detail["current_version_id"]},
    )

    class FailBreakdown(MockGenerationProvider):
        def generate(self, operation, payload, output_schema):
            if operation == "breakdown":
                raise ModelProviderError("MODEL_TIMEOUT", "拆解失败")
            return super().generate(operation, payload, output_schema)

    client.app.state.worker.generation_provider = FailBreakdown()
    partial = create_task(
        client,
        headers,
        key="pilot-metrics-partial",
        payload=task_payload(url="https://sandbox.riffloom.local/notes/note-002"),
    )
    assert (
        wait_for_terminal(client, partial.json()["id"], headers)["status"] == "failed"
    )

    forbidden = client.get("/api/v1/workspace/pilot-metrics", headers=headers)
    assert forbidden.status_code == 403

    response = client.get(
        "/api/v1/workspace/pilot-metrics",
        headers={"X-Riffloom-User": "user_admin", "X-Riffloom-Workspace": "ws_demo"},
    )
    assert response.status_code == 200
    metrics = response.json()
    assert metrics["accepted_tasks"] == 2
    assert metrics["completed_tasks"] == 1
    assert metrics["failed_tasks"] == 1
    assert metrics["completion_rate"] == 0.5
    assert metrics["adopted_creations"] == 1
    assert metrics["first_version_adoptions"] == 1
    assert metrics["first_version_adoption_rate"] == 1.0
    assert metrics["median_delivery_minutes"] is not None
    assert metrics["median_delivery_minutes"] >= 0
    assert metrics["estimated_cost_usd"] == 0
    assert metrics["cost_per_completed_task_usd"] == 0


def test_validation_and_skill_allowlist_have_structured_errors(
    client: TestClient, headers: dict[str, str]
):
    missing_key = client.post("/api/v1/tasks", headers=headers, json=task_payload())
    assert missing_key.status_code == 422
    assert missing_key.json()["error"]["code"] == "VALIDATION_ERROR"

    forbidden = client.post(
        "/api/v1/tasks",
        headers={**headers, "Idempotency-Key": "unknown-skill-001"},
        json={"mode": "agent", "skill_id": "unbounded_tool", "input": {"prompt": "x"}},
    )
    assert forbidden.status_code == 422
    assert forbidden.json()["error"]["code"] == "SKILL_NOT_ALLOWED"
