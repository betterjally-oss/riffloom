from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import CollectionTaskItem, ProviderCall
from conftest import wait_for_terminal


def payload(kind: str, *, value: str | None = None, limit: int = 20) -> dict:
    defaults = {
        "single": ("url", "https://sandbox.riffloom.local/notes/note-001"),
        "keyword": ("keyword", "AI 工作流"),
        "creator_content": ("creator", "creator-001"),
        "creator_profile": ("creator", "creator-001"),
    }
    field, default = defaults[kind]
    return {
        "kind": kind,
        "platform": "riffloom-sandbox",
        "query": {field: value or default},
        "usage_confirmed": True,
        "refresh": False,
        "limit": limit,
    }


def create_collection(
    client: TestClient,
    headers: dict[str, str],
    body: dict,
    *,
    key: str,
):
    return client.post(
        "/api/v1/collection-tasks",
        headers={**headers, "Idempotency-Key": key},
        json=body,
    )


def test_provider_status_is_explicitly_sandbox(client: TestClient, headers: dict[str, str]):
    response = client.get("/api/v1/collection-provider", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "sandbox-v1"
    assert data["mode"] == "compliance_sandbox"
    assert data["is_sandbox"] is True
    assert set(data["kinds"]) == {"single", "keyword", "creator_content", "creator_profile"}


def test_keyword_limit_accepts_thirty_and_rejects_thirty_one(
    client: TestClient, headers: dict[str, str]
):
    accepted = create_collection(
        client,
        headers,
        payload("keyword", limit=30),
        key="keyword-limit-30",
    )
    assert accepted.status_code == 202
    assert wait_for_terminal(client, accepted.json()["id"], headers)["result_summary"]["total"] == 30

    rejected = create_collection(
        client,
        headers,
        payload("keyword", limit=31),
        key="keyword-limit-31",
    )
    assert rejected.status_code == 422


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("single", 1), ("keyword", 20), ("creator_content", 20), ("creator_profile", 1)],
)
def test_four_collection_kinds_persist_to_separate_views(
    client: TestClient,
    headers: dict[str, str],
    kind: str,
    expected: int,
):
    response = create_collection(client, headers, payload(kind), key=f"kind-{kind}-001")
    assert response.status_code == 202
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    assert task["result_summary"] == {
        "new": expected,
        "reused": 0,
        "failed": 0,
        "total": expected,
        "provider": "sandbox-v1",
        "is_sandbox": True,
        "kind": kind,
    }

    library = client.get(f"/api/v1/libraries/collections?kind={kind}", headers=headers)
    assert library.status_code == 200
    assert library.json()["total"] == expected
    assert all(item["collection_kind"] == kind for item in library.json()["items"])
    assert all(item["is_sandbox"] is True for item in library.json()["items"])
    assert all(len(item["tags"]) == len(set(item["tags"])) for item in library.json()["items"])

    detail = client.get(
        f"/api/v1/collections/{library.json()['items'][0]['id']}", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["provider"] == "sandbox-v1"
    assert detail.json()["is_sandbox"] is True
    if kind == "creator_profile":
        assert library.json()["items"][0]["thumbnail_url"]
    else:
        assert library.json()["items"][0]["thumbnail_url"] == detail.json()["cover_url"]


def test_keyword_content_type_is_persisted_as_video(
    client: TestClient, headers: dict[str, str]
):
    body = payload("keyword", value="AI 口播视频", limit=1)
    body["query"].update({"content_type": "video", "publish_time": "week"})
    response = create_collection(client, headers, body, key="keyword-video-001")

    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    library = client.get(
        "/api/v1/libraries/collections?kind=keyword", headers=headers
    ).json()
    assert library["items"][0]["content_type"] == "视频"


@pytest.mark.parametrize(
    "query_change",
    [
        {"content_type": "audio"},
        {"publish_time": "quarter"},
        {"sort": "random"},
        {"unexpected": "value"},
    ],
)
def test_keyword_query_rejects_unknown_fields_and_enum_values(
    client: TestClient, headers: dict[str, str], query_change: dict[str, str]
):
    body = payload("keyword", limit=1)
    body["query"].update(query_change)

    response = create_collection(client, headers, body, key=f"invalid-query-{next(iter(query_change))}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_twenty_authorized_blogger_samples_are_complete(
    client: TestClient, headers: dict[str, str]
):
    for index in range(1, 21):
        creator = f"creator-{index:03d}"
        response = create_collection(
            client,
            headers,
            payload("creator_profile", value=creator),
            key=f"profile-sample-{index:03d}",
        )
        task = wait_for_terminal(client, response.json()["id"], headers)
        assert task["status"] == "success"
    library = client.get(
        "/api/v1/libraries/collections?kind=creator_profile", headers=headers
    ).json()
    assert library["total"] == 20
    assert all(item["source_id"].startswith("creator-") for item in library["items"])
    assert all(item["metrics"]["followers"] > 0 for item in library["items"])


def test_deduplication_reuses_records_without_creating_duplicates(
    client: TestClient, headers: dict[str, str]
):
    body = payload("keyword")
    first = create_collection(client, headers, body, key="dedupe-first-001")
    first_task = wait_for_terminal(client, first.json()["id"], headers)
    assert first_task["result_summary"]["new"] == 20

    second = create_collection(client, headers, body, key="dedupe-second-001")
    second_task = wait_for_terminal(client, second.json()["id"], headers)
    assert second_task["result_summary"]["new"] == 0
    assert second_task["result_summary"]["reused"] == 20
    assert client.get(
        "/api/v1/libraries/collections?kind=keyword", headers=headers
    ).json()["total"] == 20


def test_partial_batch_retries_only_failed_source_key(
    client: TestClient, headers: dict[str, str]
):
    response = create_collection(
        client,
        headers,
        payload("keyword", value="AI 工作流 [partial]"),
        key="partial-batch-001",
    )
    task_id = response.json()["id"]
    partial = wait_for_terminal(client, task_id, headers)
    assert partial["status"] == "partial_success"
    assert partial["result_summary"]["new"] == 19
    assert partial["result_summary"]["failed"] == 1

    retry = client.post(
        f"/api/v1/tasks/{task_id}/retry",
        headers={**headers, "Idempotency-Key": "partial-retry-001"},
    )
    assert retry.status_code == 202
    completed = wait_for_terminal(client, task_id, headers)
    assert completed["status"] == "success"
    assert completed["current_attempt"] == 2
    assert completed["result_summary"]["new"] == 20
    assert completed["result_summary"]["failed"] == 0

    database = client.app.state.database
    with database.session_factory() as session:
        second_attempt_items = session.scalar(
            select(func.count(CollectionTaskItem.id)).where(
                CollectionTaskItem.task_id == task_id,
                CollectionTaskItem.attempt_no == 2,
            )
        )
        calls = session.scalar(
            select(func.count(ProviderCall.id)).where(ProviderCall.task_id == task_id)
        )
    assert second_attempt_items == 1
    assert calls == 2


def test_provider_errors_and_usage_boundaries_are_structured(
    client: TestClient, headers: dict[str, str]
):
    limited = create_collection(
        client,
        headers,
        payload("keyword", value="[rate-limit]"),
        key="rate-limit-001",
    )
    failed = wait_for_terminal(client, limited.json()["id"], headers)
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "PROVIDER_RATE_LIMITED"
    assert failed["error"]["retryable"] is True

    unconfirmed = payload("single")
    unconfirmed["usage_confirmed"] = False
    response = create_collection(client, headers, unconfirmed, key="unconfirmed-001")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    unauthorized = create_collection(
        client,
        headers,
        payload("single", value="https://example.com/not-authorized"),
        key="unauthorized-source-001",
    )
    task = wait_for_terminal(client, unauthorized.json()["id"], headers)
    assert task["error"]["code"] == "SOURCE_NOT_AUTHORIZED"
    assert task["error"]["retryable"] is False


def test_xiaohongshu_link_creates_upload_fallback_without_persisting_query(
    client: TestClient, headers: dict[str, str]
):
    body = payload(
        "single",
        value=(
            "https://www.xiaohongshu.com/explore/6a646e770000000010028db8"
            "?xsec_token=test-signed-value&xsec_source=pc_user"
        ),
    )
    response = create_collection(client, headers, body, key="upload-fallback-001")

    assert response.status_code == 202
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success"
    assert task["input"]["query"]["url"] == (
        "https://www.xiaohongshu.com/explore/6a646e770000000010028db8"
    )
    assert task["result_summary"] == {
        "new": 1,
        "reused": 0,
        "failed": 0,
        "total": 1,
        "provider": "user-upload-v1",
        "is_sandbox": False,
        "kind": "single",
    }

    record_id = task["result_refs"][0]["id"]
    detail = client.get(f"/api/v1/collections/{record_id}", headers=headers).json()
    assert detail["provider"] == "user-upload-v1"
    assert detail["is_sandbox"] is False
    assert detail["external_url"] == (
        "https://www.xiaohongshu.com/explore/6a646e770000000010028db8"
    )
    assert detail["content_type"] == "视频"
    assert detail["video_transcript_status"] == "required"


def test_collection_records_are_workspace_isolated(client: TestClient, headers: dict[str, str]):
    response = create_collection(client, headers, payload("single"), key="collection-isolation-001")
    task = wait_for_terminal(client, response.json()["id"], headers)
    record_id = task["result_refs"][0]["id"]
    other_headers = {
        "X-Riffloom-User": "user_other",
        "X-Riffloom-Workspace": "ws_other",
    }
    hidden = client.get(f"/api/v1/collections/{record_id}", headers=other_headers)
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "COLLECTION_RECORD_NOT_FOUND"
