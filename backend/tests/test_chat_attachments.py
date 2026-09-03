from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from tests.conftest import wait_for_terminal


def test_attachment_is_workspace_scoped_and_available_to_agent(
    client: TestClient, headers: dict[str, str]
) -> None:
    content = "标题：附件测试\n正文：请根据这份资料给出建议。".encode()
    uploaded = client.post(
        "/api/v1/attachments",
        headers=headers,
        json={
            "filename": "资料.txt",
            "mime_type": "text/plain",
            "data_url": f"data:text/plain;base64,{base64.b64encode(content).decode()}",
        },
    )
    assert uploaded.status_code == 201
    attachment_id = uploaded.json()["id"]

    created = client.post(
        "/api/v1/tasks",
        headers={**headers, "Idempotency-Key": "agent-attachment-001"},
        json={
            "mode": "agent",
            "skill_id": "riffloom_agent",
            "input": {"prompt": "总结附件"},
            "attachment_ids": [attachment_id],
        },
    )
    assert created.status_code == 202
    assert created.json()["input"]["attachments"][0]["name"] == "资料.txt"
    assert wait_for_terminal(client, created.json()["id"], headers)["status"] == "success"

    denied = client.post(
        "/api/v1/tasks",
        headers={
            "X-Riffloom-User": "user_other",
            "X-Riffloom-Workspace": "ws_other",
            "Idempotency-Key": "agent-attachment-cross-workspace",
        },
        json={
            "mode": "agent",
            "skill_id": "riffloom_agent",
            "input": {"prompt": "读取附件"},
            "attachment_ids": [attachment_id],
        },
    )
    assert denied.status_code == 404
