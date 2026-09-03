from __future__ import annotations

from sqlalchemy import select

from app.models import AuditLog, BloggerRecord, BreakdownRecord, CollectionRecord, CreationRecord
from app.services.feishu_service import _source_records
from conftest import wait_for_terminal


def _post_task(client, headers, path: str, key: str, payload: dict) -> dict:
    response = client.post(
        path,
        headers={**headers, "Idempotency-Key": key},
        json=payload,
    )
    assert response.status_code == 202, response.text
    task = wait_for_terminal(client, response.json()["id"], headers)
    assert task["status"] == "success", task
    return task


def test_each_library_record_can_be_soft_deleted_and_is_workspace_isolated(client, headers):
    collection_task = _post_task(
        client,
        headers,
        "/api/v1/collection-tasks",
        "delete-collection",
        {
            "kind": "single",
            "platform": "riffloom-sandbox",
            "query": {"url": "https://sandbox.riffloom.local/notes/note-001"},
            "usage_confirmed": True,
            "limit": 1,
        },
    )
    collection_id = next(
        ref["id"] for ref in collection_task["result_refs"] if ref["type"] == "collection"
    )
    blogger_task = _post_task(
        client,
        headers,
        "/api/v1/collection-tasks",
        "delete-blogger",
        {
            "kind": "creator_profile",
            "platform": "riffloom-sandbox",
            "query": {"creator": "creator-001"},
            "usage_confirmed": True,
            "limit": 1,
        },
    )
    blogger_id = next(
        ref["id"] for ref in blogger_task["result_refs"] if ref["type"] == "blogger"
    )
    breakdown_task = _post_task(
        client,
        headers,
        "/api/v1/breakdown-tasks",
        "delete-breakdown",
        {"prompt": "拆解这条内容的结构与可复用方法", "source_ids": [collection_id]},
    )
    breakdown_id = next(
        ref["id"] for ref in breakdown_task["result_refs"] if ref["type"] == "breakdown"
    )
    creation_task = _post_task(
        client,
        headers,
        "/api/v1/creation-tasks",
        "delete-creation",
        {
            "creation_type": "original",
            "prompt": "写一篇用于验收资料库删除的小红书文案",
            "knowledge_refs": [
                {"library_type": "collections", "record_id": collection_id}
            ],
            "target_platform": "小红书",
        },
    )
    creation_id = next(
        ref["id"] for ref in creation_task["result_refs"] if ref["type"] == "creation"
    )

    other_headers = {
        "X-Riffloom-User": "user_other",
        "X-Riffloom-Workspace": "ws_other",
    }
    hidden = client.delete(
        f"/api/v1/libraries/collections/{collection_id}", headers=other_headers
    )
    assert hidden.status_code == 404

    records = [
        ("collections", collection_id, "/api/v1/collections"),
        ("collections", blogger_id, "/api/v1/collections"),
        ("breakdowns", breakdown_id, "/api/v1/breakdowns"),
        ("creations", creation_id, "/api/v1/creations"),
    ]
    for library_type, record_id, detail_path in records:
        deleted = client.delete(
            f"/api/v1/libraries/{library_type}/{record_id}", headers=headers
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["status"] == "deleted"
        assert client.get(f"{detail_path}/{record_id}", headers=headers).status_code == 404
        repeated = client.delete(
            f"/api/v1/libraries/{library_type}/{record_id}", headers=headers
        )
        assert repeated.status_code == 200

    assert client.get(
        "/api/v1/libraries/collections?kind=single", headers=headers
    ).json()["total"] == 0
    assert client.get(
        "/api/v1/libraries/collections?kind=creator_profile", headers=headers
    ).json()["total"] == 0
    assert client.get("/api/v1/libraries/breakdowns", headers=headers).json()["total"] == 0
    assert client.get("/api/v1/libraries/creations", headers=headers).json()["total"] == 0

    search = client.post(
        "/api/v1/knowledge/search",
        headers=headers,
        json={
            "query": "验收",
            "selected_libraries": ["collections", "breakdowns", "creations"],
            "selected_refs": [],
        },
    )
    assert search.status_code == 200
    assert search.json()["fragments"] == []

    with client.app.state.database.session_factory() as session:
        assert session.get(CollectionRecord, collection_id).deleted_at is not None
        assert session.get(BloggerRecord, blogger_id).deleted_at is not None
        assert session.get(BreakdownRecord, breakdown_id).deleted_at is not None
        assert session.get(CreationRecord, creation_id).deleted_at is not None
        audits = session.scalars(
            select(AuditLog).where(AuditLog.action == "library_record.deleted")
        ).all()
        assert len(audits) == 4
        statuses = {
            record.id: record.fields["riffloom_status"]
            for scope in (
                "collection.single",
                "collection.creator_profile",
                "breakdown",
                "creation",
            )
            for record in _source_records(session, "ws_demo", scope)
        }
        assert all(statuses[record_id] == "deleted" for _, record_id, _ in records)


def test_delete_rejects_unknown_library_type(client, headers):
    response = client.delete("/api/v1/libraries/covers/missing", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "LIBRARY_TYPE_INVALID"
