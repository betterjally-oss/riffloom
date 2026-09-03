from __future__ import annotations

import base64

from sqlalchemy import func, select

from app.models import AuditLog, CoverAsset, MediaAsset, ProviderCall
from conftest import wait_for_terminal


PNG_1X1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _upload(client, headers, *, role="original", filename="source.png") -> dict:
    response = client.post(
        "/api/v1/media-assets",
        headers=headers,
        json={
            "filename": filename,
            "mime_type": "image/png",
            "role": role,
            "rights_confirmed": True,
            "data_url": f"data:image/png;base64,{PNG_1X1}",
        },
    )
    assert response.status_code == 201
    return response.json()


def _create_cover(client, headers, asset_id: str, *, prompt: str, key: str) -> dict:
    response = client.post(
        "/api/v1/cover-tasks",
        headers={**headers, "Idempotency-Key": key},
        json={"prompt": prompt, "media_asset_ids": [asset_id]},
    )
    assert response.status_code == 202
    return wait_for_terminal(client, response.json()["id"], headers)


def test_cover_provider_is_local_deterministic_mock(client, headers):
    response = client.get("/api/v1/cover-provider", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "provider": "mock-cover-v1",
        "model": "deterministic-svg-cover-v1",
        "mode": "deterministic_mock",
        "is_mock": True,
        "ratios": ["3:4"],
        "width": 1200,
        "height": 1600,
        "variants": 4,
        "max_revisions": 2,
        "external_calls": False,
        "requires_usage_confirmation": False,
        "estimated_cost_cny_per_image": 0.0,
        "estimated_cost_cny_max_request": 0.0,
    }


def test_media_upload_requires_rights_and_valid_image_content(client, headers):
    denied = client.post(
        "/api/v1/media-assets",
        headers=headers,
        json={
            "filename": "denied.png",
            "mime_type": "image/png",
            "role": "reference",
            "rights_confirmed": False,
            "data_url": f"data:image/png;base64,{PNG_1X1}",
        },
    )
    assert denied.status_code == 422

    fake = base64.b64encode(b"not an image").decode()
    invalid = client.post(
        "/api/v1/media-assets",
        headers=headers,
        json={
            "filename": "fake.png",
            "mime_type": "image/png",
            "role": "reference",
            "rights_confirmed": True,
            "data_url": f"data:image/png;base64,{fake}",
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "MEDIA_DECODE_FAILED"


def test_media_upload_is_deduplicated_and_served_with_safe_headers(client, headers):
    first = _upload(client, headers)
    second = _upload(client, headers, filename="same-content.png")
    assert second["id"] == first["id"]
    assert first["rights_status"] == "approved"
    assert first["width"] == first["height"] == 1

    listing = client.get("/api/v1/media-assets", headers=headers).json()
    assert listing["total"] == 1
    content = client.get(first["content_url"], headers=headers)
    assert content.status_code == 200
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["content-disposition"] == f'inline; filename="{first["id"]}.png"'
    assert content.content.startswith(b"\x89PNG")


def test_cover_task_generates_four_traceable_variants_and_can_save(client, headers):
    asset = _upload(client, headers)
    task = _create_cover(
        client,
        headers,
        asset["id"],
        prompt="生成一组突出 AI 工作流的小红书封面",
        key="phase4b-four-covers",
    )
    assert task["status"] == "success"
    assert task["result_summary"] == {
        "kind": "cover_generation",
        "provider": "mock-cover-v1",
        "model": "deterministic-svg-cover-v1",
        "provider_request_id": task["result_summary"]["provider_request_id"],
        "generated": 4,
        "failed": 0,
        "failed_variants": [],
        "ratio": "3:4",
        "width": 1200,
        "height": 1600,
        "revision_no": 0,
        "parent_asset_id": None,
        "external_calls": False,
        "estimated_cost_usd": 0.0,
        "estimated_cost_cny": 0.0,
    }
    assert len(task["result_refs"]) == 4

    listing = client.get("/api/v1/covers", headers=headers).json()
    assert listing["total"] == 4
    assert {item["variant_no"] for item in listing["items"]} == {1, 2, 3, 4}
    assert all(
        item["provider"] == "mock-cover-v1"
        and item["ratio"] == "3:4"
        and item["revision_no"] == 0
        for item in listing["items"]
    )
    first = listing["items"][0]
    content = client.get(first["content_url"], headers=headers)
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("image/svg+xml")
    assert "未调用外部图片服务" in content.text

    saved = client.post(f"/api/v1/covers/{first['id']}/save", headers=headers)
    assert saved.status_code == 200
    assert saved.json()["status"] == "saved"
    assert client.get(f"/api/v1/covers/{first['id']}", headers=headers).json()[
        "status"
    ] == "saved"

    with client.app.state.database.session_factory() as session:
        assert session.scalar(
            select(func.count(ProviderCall.id)).where(
                ProviderCall.operation == "cover.generate",
                ProviderCall.provider == "mock-cover-v1",
            )
        ) == 1
        assert session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "cover.saved"
            )
        ) == 1


def test_identical_mock_outputs_reuse_media_without_losing_cover_history(client, headers):
    asset = _upload(client, headers)
    first = _create_cover(
        client,
        headers,
        asset["id"],
        prompt="相同输入应复用底层确定性图片资产",
        key="phase4b-dedup-cover-one",
    )
    second = _create_cover(
        client,
        headers,
        asset["id"],
        prompt="相同输入应复用底层确定性图片资产",
        key="phase4b-dedup-cover-two",
    )
    assert first["status"] == second["status"] == "success"

    with client.app.state.database.session_factory() as session:
        assert session.scalar(
            select(func.count(MediaAsset.id)).where(MediaAsset.role == "generated")
        ) == 4
        assert session.scalar(select(func.count(CoverAsset.id))) == 8


def test_cover_task_rejects_unapproved_and_cross_workspace_assets(client, headers):
    with client.app.state.database.session_factory() as session:
        pending = MediaAsset(
            id="asset_pending",
            workspace_id="ws_demo",
            storage_key="pending/not-used.png",
            original_name="pending.png",
            mime_type="image/png",
            byte_size=10,
            width=1,
            height=1,
            role="original",
            rights_status="pending",
            content_hash="pending-hash",
            created_by="user_demo",
        )
        session.add(pending)
        session.commit()

    denied = client.post(
        "/api/v1/cover-tasks",
        headers={**headers, "Idempotency-Key": "phase4b-pending-rights"},
        json={"prompt": "不应调用 Provider 的封面", "media_asset_ids": ["asset_pending"]},
    )
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "MEDIA_RIGHTS_REQUIRED"

    other_headers = {
        "X-Riffloom-User": "user_other",
        "X-Riffloom-Workspace": "ws_other",
    }
    other_asset = _upload(client, other_headers)
    isolated = client.post(
        "/api/v1/cover-tasks",
        headers={**headers, "Idempotency-Key": "phase4b-cross-workspace"},
        json={"prompt": "不应读到另一工作区素材", "media_asset_ids": [other_asset["id"]]},
    )
    assert isolated.status_code == 404
    assert isolated.json()["error"]["code"] == "MEDIA_ASSET_NOT_FOUND"
    assert client.get("/api/v1/media-assets", headers=headers).json()["total"] == 1


def test_partial_cover_task_retries_only_missing_variant(client, headers):
    asset = _upload(client, headers)
    task = _create_cover(
        client,
        headers,
        asset["id"],
        prompt="测试封面局部失败 [cover-partial]",
        key="phase4b-partial",
    )
    assert task["status"] == "partial_success"
    assert task["result_summary"]["generated"] == 3
    assert task["result_summary"]["failed_variants"] == [4]

    retried = client.post(
        f"/api/v1/tasks/{task['id']}/retry",
        headers={**headers, "Idempotency-Key": "phase4b-partial-retry"},
    )
    assert retried.status_code == 202
    recovered = wait_for_terminal(client, task["id"], headers)
    assert recovered["status"] == "success"
    assert recovered["result_summary"]["generated"] == 4
    assert len(recovered["result_refs"]) == 4

    with client.app.state.database.session_factory() as session:
        assert session.scalar(
            select(func.count(CoverAsset.id)).where(CoverAsset.task_id == task["id"])
        ) == 4
        calls = session.scalars(
            select(ProviderCall)
            .where(ProviderCall.task_id == task["id"])
            .order_by(ProviderCall.attempt_no)
        ).all()
        assert [call.result_count for call in calls] == [3, 1]


def test_cover_revision_creates_new_assets_and_stops_after_two_rounds(client, headers):
    asset = _upload(client, headers)
    base_task = _create_cover(
        client,
        headers,
        asset["id"],
        prompt="初始封面方向需要明确主标题",
        key="phase4b-revision-base",
    )
    base_id = base_task["result_refs"][0]["id"]

    first = client.post(
        f"/api/v1/covers/{base_id}/revisions",
        headers={**headers, "Idempotency-Key": "phase4b-revision-one"},
        json={"prompt": "第一轮修改：放大主标题并减少装饰"},
    )
    first_task = wait_for_terminal(client, first.json()["id"], headers)
    assert first_task["status"] == "success"
    first_id = first_task["result_refs"][0]["id"]
    first_cover = client.get(f"/api/v1/covers/{first_id}", headers=headers).json()
    assert first_cover["revision_no"] == 1
    assert first_cover["parent_asset_id"] == base_id

    second = client.post(
        f"/api/v1/covers/{first_id}/revisions",
        headers={**headers, "Idempotency-Key": "phase4b-revision-two"},
        json={"prompt": "第二轮修改：增加底部行动口号"},
    )
    second_task = wait_for_terminal(client, second.json()["id"], headers)
    second_id = second_task["result_refs"][0]["id"]
    assert client.get(f"/api/v1/covers/{second_id}", headers=headers).json()[
        "revision_no"
    ] == 2

    blocked = client.post(
        f"/api/v1/covers/{second_id}/revisions",
        headers={**headers, "Idempotency-Key": "phase4b-revision-three"},
        json={"prompt": "第三轮修改应该被阻止"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "COVER_REVISION_LIMIT"
