from __future__ import annotations

from dataclasses import replace

from app.providers.models import MockGenerationProvider, ModelProviderError
from app.schemas.generation import AgentChatOutputV2, AgentToolIntentV2
from conftest import wait_for_terminal


def _collect(client, headers, *, kind="single", value=None, limit=1, key="p0-source"):
    field = "url" if kind == "single" else "keyword"
    response = client.post(
        "/api/v1/collection-tasks",
        headers={**headers, "Idempotency-Key": key},
        json={
            "kind": kind,
            "platform": "riffloom-sandbox",
            "query": {
                field: value
                or (
                    "https://sandbox.riffloom.local/notes/note-001"
                    if kind == "single"
                    else "批量测试"
                )
            },
            "usage_confirmed": True,
            "limit": limit,
        },
    )
    assert response.status_code == 202, response.text
    return wait_for_terminal(client, response.json()["id"], headers)


def test_collect_rewrite_uses_real_collection_checkpoint_and_retry_skips_paid_step(
    client, headers
):
    original_collect = client.app.state.collection_provider.collect
    collection_calls = 0

    def counted_collect(request):
        nonlocal collection_calls
        collection_calls += 1
        return original_collect(request)

    class FailCreationOnce(MockGenerationProvider):
        def __init__(self):
            self.failed = False

        def generate(self, operation, payload, output_schema):
            if operation == "creation" and not self.failed:
                self.failed = True
                raise ModelProviderError("MODEL_TIMEOUT", "首次创作失败")
            return super().generate(operation, payload, output_schema)

    client.app.state.collection_provider.collect = counted_collect
    failing_provider = FailCreationOnce()
    client.app.state.worker.generation_provider = failing_provider
    response = client.post(
        "/api/v1/collect-rewrite-tasks",
        headers={**headers, "Idempotency-Key": "collect-rewrite-checkpoint-001"},
        json={
            "url": "https://sandbox.riffloom.local/notes/note-002?xsec_token=private",
            "prompt": "保留结构方法，面向新手重新表达",
            "usage_confirmed": True,
            "target_platform": "小红书",
            "audience": "内容新人",
        },
    )
    assert response.status_code == 202, response.text
    assert "private" not in str(response.json()["input"])
    failed = wait_for_terminal(client, response.json()["id"], headers)
    assert failed["status"] == "failed"
    assert {ref["type"] for ref in failed["result_refs"]} == {
        "collection",
        "breakdown",
    }
    assert collection_calls == 1

    client.app.state.worker.generation_provider = MockGenerationProvider()
    retried = client.post(
        f"/api/v1/tasks/{failed['id']}/retry",
        headers={**headers, "Idempotency-Key": "retry-collect-rewrite-001"},
    )
    assert retried.status_code == 202
    completed = wait_for_terminal(client, failed["id"], headers)
    assert completed["status"] == "success"
    assert [ref["type"] for ref in completed["result_refs"]] == [
        "collection",
        "breakdown",
        "creation",
    ]
    assert collection_calls == 1


def test_collect_rewrite_reuses_safe_persisted_url(client, headers):
    response = client.post(
        "/api/v1/collect-rewrite-tasks",
        headers={**headers, "Idempotency-Key": "collect-rewrite-safe-url-001"},
        json={
            "url": "https://sandbox.riffloom.local/notes/safe-url-001",
            "prompt": "面向职场新人重新表达",
            "usage_confirmed": True,
        },
    )
    completed = wait_for_terminal(client, response.json()["id"], headers)
    assert completed["status"] == "success"
    assert [ref["type"] for ref in completed["result_refs"]] == [
        "collection",
        "breakdown",
        "creation",
    ]


def test_existing_collection_rewrite_and_video_transcript_gate(client, headers):
    source = _collect(client, headers, key="p0-existing-source")
    collection_id = source["result_refs"][0]["id"]
    response = client.post(
        "/api/v1/collect-rewrite-tasks",
        headers={**headers, "Idempotency-Key": "existing-rewrite-001"},
        json={
            "collection_id": collection_id,
            "prompt": "换一个受众进行仿写",
            "usage_confirmed": False,
        },
    )
    completed = wait_for_terminal(client, response.json()["id"], headers)
    assert completed["status"] == "success"
    assert {ref["type"] for ref in completed["result_refs"]} == {
        "collection",
        "breakdown",
        "creation",
    }

    with client.app.state.database.session_factory() as session:
        from app.models import CollectionRecord

        record = session.get(CollectionRecord, collection_id)
        record.content_type = "视频"
        record.video_transcript = None
        record.video_transcript_status = "not_collected"
        session.commit()
    blocked = client.post(
        "/api/v1/collect-rewrite-tasks",
        headers={**headers, "Idempotency-Key": "existing-video-blocked-001"},
        json={"collection_id": collection_id, "prompt": "仿写视频"},
    )
    failed = wait_for_terminal(client, blocked.json()["id"], headers)
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "VIDEO_TRANSCRIPT_REQUIRED"
    assert failed["result_refs"] == [{"type": "collection", "id": collection_id}]


def test_batch_metadata_workspace_isolation_and_partial_batch_tasks(client, headers):
    collected = _collect(
        client, headers, kind="keyword", value="批量验收", limit=3, key="p0-batch-source"
    )
    record_ids = [ref["id"] for ref in collected["result_refs"]]
    updated = client.patch(
        "/api/v1/collections/batch-metadata",
        headers=headers,
        json={
            "record_ids": record_ids,
            "benchmark": True,
            "category_tags": ["美妆", "测评", "美妆"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["updated_ids"] == record_ids
    library = client.get("/api/v1/libraries/collections?kind=keyword", headers=headers).json()
    assert all(item["benchmark"] is True for item in library["items"])
    assert all(item["category_tags"] == ["美妆", "测评"] for item in library["items"])
    assert all(item["published_at"] for item in library["items"])

    denied = client.patch(
        "/api/v1/collections/batch-metadata",
        headers=headers,
        json={"record_ids": [record_ids[0], "col_other_workspace"], "benchmark": False},
    )
    assert denied.status_code == 404
    library = client.get("/api/v1/libraries/collections?kind=keyword", headers=headers).json()
    assert next(item for item in library["items"] if item["id"] == record_ids[0])["benchmark"] is True

    body = {
        "action": "breakdown",
        "record_ids": [*record_ids[:2], "col_other_workspace"],
        "prompt": "",
    }
    first = client.post(
        "/api/v1/collection-batch-tasks",
        headers={**headers, "Idempotency-Key": "p0-batch-breakdown-001"},
        json=body,
    )
    assert first.status_code == 202, first.text
    data = first.json()
    assert len(data["tasks"]) == 2
    assert data["issues"] == [
        {
            "record_id": "col_other_workspace",
            "code": "COLLECTION_RECORD_NOT_FOUND",
            "message": "记录不存在、不属于当前 Workspace 或不是内容记录",
            "retryable": False,
        }
    ]
    for task in data["tasks"]:
        assert wait_for_terminal(client, task["id"], headers)["status"] == "success"

    repeated = client.post(
        "/api/v1/collection-batch-tasks",
        headers={**headers, "Idempotency-Key": "p0-batch-breakdown-001"},
        json=body,
    )
    assert [task["id"] for task in repeated.json()["tasks"]] == [
        task["id"] for task in data["tasks"]
    ]

    rewrite = client.post(
        "/api/v1/collection-batch-tasks",
        headers={**headers, "Idempotency-Key": "p0-batch-rewrite-001"},
        json={
            "action": "rewrite",
            "record_ids": record_ids[:2],
            "prompt": "面向新手重新表达",
        },
    )
    assert rewrite.status_code == 202
    for task in rewrite.json()["tasks"]:
        completed = wait_for_terminal(client, task["id"], headers)
        assert completed["status"] == "success"
        assert {ref["type"] for ref in completed["result_refs"]} == {
            "collection",
            "breakdown",
            "creation",
        }


def test_twenty_collection_records_can_start_independent_breakdowns(client, headers):
    collected = _collect(
        client,
        headers,
        kind="keyword",
        value="二十条批量拆解",
        limit=20,
        key="p0-batch-twenty-source",
    )
    record_ids = [ref["id"] for ref in collected["result_refs"]]
    response = client.post(
        "/api/v1/collection-batch-tasks",
        headers={**headers, "Idempotency-Key": "p0-batch-twenty-breakdown"},
        json={"action": "breakdown", "record_ids": record_ids, "prompt": ""},
    )
    assert response.status_code == 202
    assert len(response.json()["tasks"]) == 20
    assert response.json()["issues"] == []
    assert all(
        wait_for_terminal(client, task["id"], headers)["status"] == "success"
        for task in response.json()["tasks"]
    )


def test_agent_v2_delegates_safe_action_and_requires_confirmation_for_new_url(
    client, headers
):
    source = _collect(client, headers, key="p0-agent-source")
    collection_id = source["result_refs"][0]["id"]
    delegated = client.post(
        "/api/v1/tasks",
        headers={**headers, "Idempotency-Key": "p0-agent-delegate-001"},
        json={
            "mode": "agent",
            "skill_id": "riffloom_agent",
            "input": {"prompt": "请拆解选中的内容"},
            "source_ids": [collection_id],
        },
    )
    parent = wait_for_terminal(client, delegated.json()["id"], headers)
    assert parent["status"] == "success"
    assert parent["result_summary"]["contract_version"] == "agent-chat.v2"
    assert parent["result_summary"]["agent_action"] == "delegated"
    child_id = parent["result_summary"]["delegated_task_id"]
    assert parent["result_refs"] == [{"type": "task", "id": child_id}]
    assert wait_for_terminal(client, child_id, headers)["status"] == "success"

    before = client.get("/api/v1/tasks", headers=headers).json()["total"]
    confirmation = client.post(
        "/api/v1/tasks",
        headers={**headers, "Idempotency-Key": "p0-agent-confirm-001"},
        json={
            "mode": "agent",
            "skill_id": "riffloom_agent",
            "input": {
                "prompt": (
                    "请采集仿写 https://www.xiaohongshu.com/explore/note-safe"
                    "?xsec_token=private-signature"
                )
            },
        },
    )
    assert "private-signature" not in str(confirmation.json()["input"])
    confirmed_parent = wait_for_terminal(client, confirmation.json()["id"], headers)
    assert confirmed_parent["result_summary"]["agent_action"] == "confirmation_required"
    assert confirmed_parent["result_summary"]["confirmation"]["type"] == "collect_rewrite"
    assert confirmed_parent["result_refs"] == []
    assert "private-signature" not in str(confirmed_parent)
    assert client.get("/api/v1/tasks", headers=headers).json()["total"] == before + 1


def test_agent_v2_unknown_or_incomplete_action_has_zero_side_effects(client, headers):
    class UnknownActionProvider(MockGenerationProvider):
        def generate(self, operation, payload, output_schema):
            result = super().generate(operation, payload, output_schema)
            if operation != "agent_chat":
                return result
            return replace(
                result,
                output=AgentChatOutputV2(
                    reply="我来处理",
                    action=AgentToolIntentV2(name="delete_workspace", prompt="删除"),
                ),
            )

    client.app.state.worker.generation_provider = UnknownActionProvider()
    before = client.get("/api/v1/tasks", headers=headers).json()["total"]
    response = client.post(
        "/api/v1/tasks",
        headers={**headers, "Idempotency-Key": "p0-agent-unknown-001"},
        json={
            "mode": "agent",
            "skill_id": "riffloom_agent",
            "input": {"prompt": "执行未知动作"},
        },
    )
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    assert task["result_summary"]["agent_action"] == "reply"
    assert "没有执行任何任务" in task["result_summary"]["reply"]
    assert task["result_refs"] == []
    assert client.get("/api/v1/tasks", headers=headers).json()["total"] == before + 1
