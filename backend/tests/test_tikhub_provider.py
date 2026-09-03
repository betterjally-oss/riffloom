from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.core.deployment_safety import validate_deployment_settings
from app.main import _build_collection_provider, create_app
from app.models import CollectionRecord, CollectionTaskItem, ProviderCall
from app.providers import (
    CollectionRequest,
    ProviderError,
    TikHubCollectorProvider,
    XiaohongshuHybridCollectorProvider,
)
from conftest import wait_for_terminal


def _notes(count: int = 22) -> list[dict]:
    return [
        {
            "id": f"66c01234567890123456{index:04d}",
            "title": f"真实结构样本 {index}",
            "desc": "正文 #AI工具",
            "type": "video" if index == 2 else "normal",
            "timestamp": 1_777_593_600_000 + index,
            "user": {"userid": "61b46d790000000010008153", "nickname": "测试作者"},
            "images_list": [
                {
                    "url_size_large": (
                        f"http://sns-img-qc.xhscdn.com/{index}.jpg?sign=private"
                    )
                }
            ],
            "liked_count": 100 + index,
            "collected_count": 50 + index,
            "comments_count": 10 + index,
            "shared_count": index,
        }
        for index in range(1, count + 1)
    ]


def _provider(handler) -> TikHubCollectorProvider:
    return TikHubCollectorProvider(
        api_key="test-secret",
        client=httpx.Client(
            base_url="https://api.tikhub.io",
            transport=httpx.MockTransport(handler),
        ),
    )


def _request(kind: str, query: dict, *, limit: int = 20) -> CollectionRequest:
    return CollectionRequest(
        kind=kind,  # type: ignore[arg-type]
        platform="xiaohongshu",
        query=query,
        limit=limit,
        attempt_no=1,
    )


def test_keyword_and_creator_content_map_real_tikhub_shapes_and_limit_to_twenty():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        notes = _notes()
        data = (
            {"items": [{"note": note} for note in notes]}
            if request.url.path.endswith("/search_notes")
            else {"notes": notes, "has_more": True}
        )
        return httpx.Response(
            200,
            json={
                "code": 200,
                "request_id": f"req-{len(calls)}",
                "data": {"code": 0, "success": True, "data": data},
            },
            request=request,
        )

    provider = _provider(handler)
    keyword = provider.collect(
        _request(
            "keyword",
            {"keyword": "AI工具", "sort": "most-liked", "publish_time": "week"},
        )
    )
    creator = provider.collect(
        _request(
            "creator_content",
            {
                "creator": (
                    "https://www.xiaohongshu.com/user/profile/"
                    "61b46d790000000010008153?xsec_token=private"
                )
            },
        )
    )

    assert len(keyword.content_items) == len(creator.content_items) == 20
    assert keyword.content_items[0].topics == ["AI工具"]
    assert keyword.content_items[0].cover_url == "https://sns-img-qc.xhscdn.com/1.jpg"
    assert keyword.content_items[1].content_type == "视频"
    assert keyword.content_items[1].video_transcript_status == "not_collected"
    assert "private" not in repr(keyword)
    assert calls[0].url.params["sort_type"] == "popularity_descending"
    assert calls[0].url.params["time_filter"] == "一周内"
    assert calls[1].url.params["user_id"] == "61b46d790000000010008153"
    assert calls[1].url.params["cursor"] == ""
    assert all(
        request.headers["Authorization"] == "Bearer test-secret" for request in calls
    )


def test_keyword_and_creator_content_fetch_second_page_up_to_thirty():
    notes = _notes(35)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/search_notes"):
            page = int(request.url.params["page"])
            rows = notes[:20] if page == 1 else notes[20:]
            data = {
                "items": [{"note": note} for note in rows],
                "search_id": "search-30",
                "search_session_id": "session-30",
            }
        else:
            cursor = request.url.params["cursor"]
            rows = notes[:20] if not cursor else notes[20:]
            rows[-1]["cursor"] = "cursor-next"
            data = {"notes": rows, "has_more": not cursor}
        return httpx.Response(
            200,
            json={
                "code": 200,
                "request_id": f"req-{len(calls)}",
                "data": {"code": 0, "success": True, "data": data},
            },
            request=request,
        )

    provider = _provider(handler)
    keyword = provider.collect(_request("keyword", {"keyword": "AI工具"}, limit=30))
    provider._hydrate_creator_notes = lambda rows, request: (rows, [], [])  # type: ignore[method-assign]
    creator = provider.collect(
        _request(
            "creator_content",
            {"creator": "61b46d790000000010008153"},
            limit=30,
        )
    )

    assert len(keyword.content_items) == len(creator.content_items) == 30
    list_calls = [
        request
        for request in calls
        if request.url.path.endswith(("/search_notes", "/get_user_posted_notes"))
    ]
    assert [request.url.params.get("page") for request in list_calls[:2]] == ["1", "2"]
    assert list_calls[1].url.params["search_id"] == "search-30"
    assert list_calls[1].url.params["search_session_id"] == "session-30"
    assert list_calls[3].url.params["cursor"] == "cursor-next"
    assert keyword.cost == creator.cost == 0.02


def test_trend_search_uses_xiaohongshu_for_the_last_week():
    calls: list[httpx.Request] = []
    timestamp = int(datetime.now(timezone.utc).timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        note = _notes(1)[0]
        data = {
            "items": [
                {"note": {**note, "timestamp": timestamp * 1000}},
                {
                    "note": {
                        **note,
                        "id": "emoji-only",
                        "title": "😂😂😂",
                        "desc": "😂😂😂",
                    }
                },
            ]
        }
        return httpx.Response(
            200,
            json={
                "code": 200,
                "request_id": f"trend-{len(calls)}",
                "data": {"code": 0, "success": True, "data": data},
            },
            request=request,
        )

    batch = _provider(handler).search_trends("AI 工具", window_days=7, limit=5)

    assert len(calls) == 1
    assert calls[0].url.params["time_filter"] == "一周内"
    assert [item.actual_upstream for item in batch.content_items] == [
        "tikhub/xiaohongshu-search"
    ]
    assert batch.cost == 0.01


def test_tikhub_trend_metric_keeps_decimal_wan_values():
    assert (
        TikHubCollectorProvider._metric({"digg_count": "1.2万"}, {}, "digg_count")
        == 12_000
    )


def test_keyword_maps_content_type_and_filters_month_without_an_extra_request():
    calls: list[httpx.Request] = []
    now = datetime.now(timezone.utc)
    notes = _notes(2)
    notes[0]["timestamp"] = int((now - timedelta(days=10)).timestamp() * 1000)
    notes[1]["timestamp"] = int((now - timedelta(days=40)).timestamp() * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "code": 200,
                "request_id": "req-month",
                "data": {
                    "code": 0,
                    "success": True,
                    "data": {"items": [{"note": note} for note in notes]},
                },
            },
            request=request,
        )

    batch = _provider(handler).collect(
        _request(
            "keyword",
            {
                "keyword": "视频口播",
                "content_type": "video",
                "sort": "most-liked",
                "publish_time": "month",
            },
        )
    )

    assert len(calls) == 1
    assert calls[0].url.params["note_type"] == "视频笔记"
    assert calls[0].url.params["time_filter"] == "半年内"
    assert [item.external_id for item in batch.content_items] == [notes[0]["id"]]


def test_creator_content_maps_list_cover_media_topics_and_nested_metrics():
    note = {
        "id": "66c012345678901234560001",
        "display_title": "博主视频样本",
        "desc": "正文 #正文话题",
        "type": "video",
        "timestamp": 1_777_593_600_000,
        "user": {"userid": "61b46d790000000010008153", "nickname": "测试作者"},
        "cover": {"url_default": "http://sns-img-qc.xhscdn.com/cover.jpg?sign=private"},
        "image_list": [{"url": "http://sns-img-qc.xhscdn.com/image.jpg?sign=private"}],
        "video_info": {
            "master_url": "http://sns-video-qc.xhscdn.com/video.mp4?sign=private"
        },
        "tag_list": [{"name": "结构化标签"}],
        "interact_info": {
            "liked_count": "120",
            "collected_count": "60",
            "comment_count": "12",
            "share_count": "3",
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 200,
                "request_id": "req-creator-fields",
                "data": {
                    "code": 0,
                    "success": True,
                    "data": {"notes": [note], "has_more": False},
                },
            },
            request=request,
        )

    item = (
        _provider(handler)
        .collect(_request("creator_content", {"creator": "61b46d790000000010008153"}))
        .content_items[0]
    )

    assert item.cover_url == "https://sns-img-qc.xhscdn.com/cover.jpg"
    assert item.media_refs == [
        {"type": "cover", "url": "https://sns-img-qc.xhscdn.com/cover.jpg"},
        {"type": "image", "url": "https://sns-img-qc.xhscdn.com/image.jpg"},
        {"type": "video", "url": "https://sns-video-qc.xhscdn.com/video.mp4"},
    ]
    assert item.topics == ["正文话题", "结构化标签"]
    assert item.metrics == {
        "likes": 120,
        "collects": 60,
        "comments": 12,
        "shares": 3,
        "views": 0,
    }


def test_creator_content_hydrates_missing_image_and_video_media_from_details():
    notes = [
        {
            "id": "66c012345678901234560001",
            "title": "图文样本",
            "desc": "图文正文",
            "type": "normal",
            "user": {"userid": "61b46d790000000010008153", "nickname": "测试作者"},
        },
        {
            "id": "66c012345678901234560002",
            "title": "视频样本",
            "desc": "视频正文",
            "type": "video",
            "user": {"userid": "61b46d790000000010008153", "nickname": "测试作者"},
        },
    ]
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/get_user_posted_notes"):
            data = {"notes": notes, "has_more": False}
        elif request.url.path.endswith("/get_image_note_detail"):
            data = {
                "note_card": {
                    "image_list": [
                        {"url": "http://sns-img-qc.xhscdn.com/detail.jpg?sign=private"}
                    ],
                    "tag_list": ["图文标签"],
                }
            }
        else:
            data = {
                "data": {
                    "note": {
                        "video_info": {
                            "media": {
                                "master_url": "http://sns-video-qc.xhscdn.com/detail.mp4?sign=private"
                            }
                        },
                        "tag_list": [{"name": "视频标签"}],
                    }
                }
            }
        return httpx.Response(
            200,
            json={
                "code": 200,
                "request_id": f"req-{len(paths)}",
                "data": {"code": 0, "success": True, "data": data},
            },
            request=request,
        )

    batch = _provider(handler).collect(
        _request("creator_content", {"creator": "61b46d790000000010008153"}, limit=2)
    )

    assert paths == [
        "/api/v1/xiaohongshu/app_v2/get_user_posted_notes",
        "/api/v1/xiaohongshu/app_v2/get_image_note_detail",
        "/api/v1/xiaohongshu/app_v2/get_image_note_detail",
        "/api/v1/xiaohongshu/app_v2/get_video_note_detail",
    ]
    assert batch.cost == 0.04
    assert [trace.request_id for trace in batch.request_traces] == [
        "req-1",
        "req-2",
        "req-3",
        "req-4",
    ]
    assert batch.item_errors == []
    assert batch.content_items[0].media_refs == [
        {"type": "image", "url": "https://sns-img-qc.xhscdn.com/detail.jpg"}
    ]
    assert batch.content_items[0].topics == ["图文标签"]
    assert batch.content_items[1].media_refs == [
        {"type": "image", "url": "https://sns-img-qc.xhscdn.com/detail.jpg"},
        {"type": "video", "url": "https://sns-video-qc.xhscdn.com/detail.mp4"},
    ]
    assert batch.content_items[1].topics == ["视频标签"]


def test_creator_video_prefetches_general_detail_before_video_detail():
    note = {
        "id": "66c012345678901234560002",
        "title": "列表视频标题",
        "desc": "列表摘要",
        "type": "video",
        "user": {"userid": "61b46d790000000010008153", "nickname": "测试作者"},
    }
    paths: list[str] = []
    general_detail_loaded = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal general_detail_loaded
        paths.append(request.url.path)
        if request.url.path.endswith("/get_user_posted_notes"):
            data = {"notes": [note], "has_more": False}
        elif request.url.path.endswith("/get_image_note_detail"):
            general_detail_loaded = True
            data = {
                "note_card": {
                    "type": "video",
                    "desc": "完整视频正文",
                    "cover": {
                        "url_default": "http://sns-img-qc.xhscdn.com/video-cover.jpg?sign=private"
                    },
                }
            }
        elif general_detail_loaded:
            data = {
                "note": {
                    "video_info": {
                        "master_url": "http://sns-video-qc.xhscdn.com/video.mp4?sign=private"
                    }
                }
            }
        else:
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "request_id": "req-video-before-general-detail",
                    "data": {"code": -1, "success": False, "data": {}},
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "code": 200,
                "request_id": f"req-{len(paths)}",
                "data": {"code": 0, "success": True, "data": data},
            },
            request=request,
        )

    batch = _provider(handler).collect(
        _request("creator_content", {"creator": "61b46d790000000010008153"}, limit=1)
    )

    assert paths == [
        "/api/v1/xiaohongshu/app_v2/get_user_posted_notes",
        "/api/v1/xiaohongshu/app_v2/get_image_note_detail",
        "/api/v1/xiaohongshu/app_v2/get_video_note_detail",
    ]
    assert batch.item_errors == []
    assert batch.content_items[0].body == "完整视频正文"
    assert batch.content_items[0].media_refs == [
        {"type": "cover", "url": "https://sns-img-qc.xhscdn.com/video-cover.jpg"},
        {"type": "video", "url": "https://sns-video-qc.xhscdn.com/video.mp4"},
    ]


def test_creator_detail_failure_preserves_list_result_and_request_id():
    note = {
        "id": "66c012345678901234560001",
        "title": "仍可使用的列表结果",
        "desc": "列表正文",
        "type": "normal",
        "user": {"userid": "61b46d790000000010008153", "nickname": "测试作者"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/get_user_posted_notes"):
            status, request_id, data = 200, "req-list", {"notes": [note]}
        else:
            status, request_id, data = 429, "req-detail-failed", {}
        return httpx.Response(
            status,
            json={
                "code": 200,
                "request_id": request_id,
                "data": {"code": 0, "success": True, "data": data},
            },
            request=request,
        )

    batch = _provider(handler).collect(
        _request("creator_content", {"creator": "61b46d790000000010008153"}, limit=1)
    )

    assert [
        (trace.request_id, trace.status, trace.http_status, trace.error_type)
        for trace in batch.request_traces
    ] == [
        ("req-list", "success", None, None),
        ("req-detail-failed", "failed", 429, "PROVIDER_RATE_LIMITED"),
    ]
    assert batch.cost == 0.02
    assert [item.title for item in batch.content_items] == ["仍可使用的列表结果"]
    assert [error.code for error in batch.item_errors] == ["PROVIDER_RATE_LIMITED"]


def test_tikhub_error_never_copies_upstream_header_echo_into_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={
                "detail": {
                    "message_zh": "余额不足",
                    "headers": {"Authorization": "Bearer test-secret"},
                }
            },
            request=request,
        )

    with pytest.raises(ProviderError) as captured:
        _provider(handler).collect(_request("keyword", {"keyword": "AI工具"}))

    assert captured.value.code == "TIKHUB_BALANCE_REQUIRED"
    assert "test-secret" not in captured.value.message


def test_creator_profile_maps_real_shape_and_rejects_upstream_service_failure():
    success = True

    def handler(request: httpx.Request) -> httpx.Response:
        data = {
            "success": success,
            "result": {"success": success, "message": "sensitive upstream text"},
            "userid": "61b46d790000000010008153" if success else "",
            "nickname": "测试博主",
            "imageb": "http://sns-avatar-qc.xhscdn.com/avatar.jpg?sign=private",
            "desc": "内容简介",
            "fans": 1200,
            "liked": 3000,
            "collected": 400,
            "location": "上海",
        }
        return httpx.Response(
            200,
            json={
                "code": 200,
                "request_id": "req-profile",
                "data": {"code": 0, "success": True, "data": data},
            },
            request=request,
        )

    provider = _provider(handler)
    batch = provider.collect(
        _request("creator_profile", {"creator": "61b46d790000000010008153"}, limit=1)
    )
    blogger = batch.blogger_items[0]
    assert blogger.name == "测试博主"
    assert blogger.followers == 1200
    assert blogger.likes_and_collects == 3400
    assert blogger.avatar_url == "https://sns-avatar-qc.xhscdn.com/avatar.jpg"
    assert "private" not in repr(batch)

    success = False
    with pytest.raises(ProviderError) as captured:
        provider.collect(
            _request(
                "creator_profile", {"creator": "61b46d790000000010008153"}, limit=1
            )
        )
    assert captured.value.code == "SOURCE_NOT_FOUND"
    assert "sensitive" not in captured.value.message


def test_hybrid_routes_single_to_web_and_metadata_to_tikhub():
    calls: list[str] = []

    class Stub:
        def __init__(self, name: str):
            self.name = name

        def capabilities(self):
            return {
                "sample_inputs": {
                    kind: kind
                    for kind in (
                        "single",
                        "keyword",
                        "creator_content",
                        "creator_profile",
                    )
                }
            }

        def collect(self, request):
            calls.append(self.name)
            return type("Batch", (), {"provider": self.name})()

    provider = XiaohongshuHybridCollectorProvider(Stub("web"), Stub("tikhub"))  # type: ignore[arg-type]
    assert provider.capabilities()["kinds"] == [
        "single",
        "keyword",
        "creator_content",
        "creator_profile",
    ]
    assert (
        provider.collect(_request("single", {"url": "https://example.com"})).provider
        == "web"
    )
    assert (
        provider.collect(
            _request("creator_profile", {"creator": "61b46d790000000010008153"})
        ).provider
        == "tikhub"
    )
    assert calls == ["web", "tikhub"]


def test_tikhub_keyword_task_persists_twenty_lightweight_results(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 200,
                "request_id": "req-task-20",
                "data": {
                    "code": 0,
                    "success": True,
                    "data": {"items": [{"note": note} for note in _notes(20)]},
                },
            },
            request=request,
        )

    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'tikhub.db'}",
            asset_storage_dir=str(tmp_path / "uploads"),
            collection_provider="tikhub-v1",
            tikhub_api_key="test-secret",
            worker_step_delay=0,
            worker_threads=1,
            log_level="WARNING",
        )
    )
    app.state.collection_provider.client = httpx.Client(
        base_url="https://api.tikhub.io",
        transport=httpx.MockTransport(handler),
    )
    headers = {"X-Riffloom-User": "user_demo", "X-Riffloom-Workspace": "ws_demo"}
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/collection-tasks",
            headers={**headers, "Idempotency-Key": "tikhub-keyword-20"},
            json={
                "kind": "keyword",
                "platform": "xiaohongshu",
                "query": {"keyword": "AI工具"},
                "usage_confirmed": True,
                "limit": 20,
            },
        )
        task = wait_for_terminal(client, response.json()["id"], headers)

    assert task["status"] == "success"
    assert task["result_summary"] == {
        "new": 20,
        "reused": 0,
        "failed": 0,
        "total": 20,
        "provider": "tikhub-v1",
        "is_sandbox": False,
        "kind": "keyword",
    }
    assert len(task["result_refs"]) == 20


def test_creator_task_audits_list_and_detail_request_ids(tmp_path):
    note = {
        "id": "66c012345678901234560001",
        "title": "博主图文",
        "desc": "正文",
        "type": "normal",
        "user": {"userid": "61b46d790000000010008153", "nickname": "测试作者"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        is_list = request.url.path.endswith("/get_user_posted_notes")
        data = (
            {"notes": [note]}
            if is_list
            else {
                "image_list": [
                    {"url": "http://sns-img-qc.xhscdn.com/audited.jpg?sign=private"}
                ]
            }
        )
        return httpx.Response(
            200,
            json={
                "code": 200,
                "request_id": "req-list-audit" if is_list else "req-detail-audit",
                "data": {"code": 0, "success": True, "data": data},
            },
            request=request,
        )

    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'tikhub-audit.db'}",
            asset_storage_dir=str(tmp_path / "uploads"),
            collection_provider="tikhub-v1",
            tikhub_api_key="test-secret",
            worker_step_delay=0,
            worker_threads=1,
            log_level="WARNING",
        )
    )
    app.state.collection_provider.client = httpx.Client(
        base_url="https://api.tikhub.io",
        transport=httpx.MockTransport(handler),
    )
    headers = {"X-Riffloom-User": "user_demo", "X-Riffloom-Workspace": "ws_demo"}
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/collection-tasks",
            headers={**headers, "Idempotency-Key": "tikhub-creator-audit"},
            json={
                "kind": "creator_content",
                "platform": "xiaohongshu",
                "query": {"creator": "61b46d790000000010008153"},
                "usage_confirmed": True,
                "limit": 1,
            },
        )
        task = wait_for_terminal(client, response.json()["id"], headers)
        with client.app.state.database.session_factory() as session:
            calls = session.scalars(
                select(ProviderCall)
                .where(ProviderCall.task_id == task["id"])
                .order_by(ProviderCall.operation)
            ).all()

    assert task["status"] == "success"
    assert [
        (call.operation, call.provider_request_id, call.cost) for call in calls
    ] == [
        ("creator_content.image_detail", "req-detail-audit", 0.01),
        ("creator_content.list", "req-list-audit", 0.01),
    ]


def test_creator_detail_failure_persists_one_failed_item_and_request_statuses(tmp_path):
    note = {
        "id": "66c012345678901234560001",
        "title": "列表结果仍需保留",
        "desc": "列表正文",
        "type": "normal",
        "user": {"userid": "61b46d790000000010008153", "nickname": "测试作者"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        is_list = request.url.path.endswith("/get_user_posted_notes")
        return httpx.Response(
            200 if is_list else 429,
            json={
                "code": 200,
                "request_id": (
                    "req-list-failed-task" if is_list else "req-detail-failed-task"
                ),
                "data": {
                    "code": 0,
                    "success": True,
                    "data": {"notes": [note]} if is_list else {},
                },
            },
            request=request,
        )

    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'tikhub-partial.db'}",
            asset_storage_dir=str(tmp_path / "uploads"),
            collection_provider="tikhub-v1",
            tikhub_api_key="test-secret",
            worker_step_delay=0,
            worker_threads=1,
            log_level="WARNING",
        )
    )
    app.state.collection_provider.client = httpx.Client(
        base_url="https://api.tikhub.io",
        transport=httpx.MockTransport(handler),
    )
    headers = {"X-Riffloom-User": "user_demo", "X-Riffloom-Workspace": "ws_demo"}
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/collection-tasks",
            headers={**headers, "Idempotency-Key": "tikhub-detail-failed-task"},
            json={
                "kind": "creator_content",
                "platform": "xiaohongshu",
                "query": {"creator": "61b46d790000000010008153"},
                "usage_confirmed": True,
                "limit": 1,
            },
        )
        task = wait_for_terminal(client, response.json()["id"], headers)
        with client.app.state.database.session_factory() as session:
            items = session.scalars(
                select(CollectionTaskItem).where(
                    CollectionTaskItem.task_id == task["id"]
                )
            ).all()
            records = session.scalars(
                select(CollectionRecord).where(CollectionRecord.task_id == task["id"])
            ).all()
            calls = session.scalars(
                select(ProviderCall)
                .where(ProviderCall.task_id == task["id"])
                .order_by(ProviderCall.operation)
            ).all()

    assert task["status"] == "failed"
    assert task["result_summary"]["failed"] == task["result_summary"]["total"] == 1
    assert len(task["result_refs"]) == len(items) == len(records) == 1
    assert items[0].entity_id == records[0].id
    assert items[0].status == "failed"
    assert items[0].error["code"] == "PROVIDER_RATE_LIMITED"
    assert [
        (call.operation, call.status, call.http_status, call.error_type)
        for call in calls
    ] == [
        ("creator_content.image_detail", "failed", 429, "PROVIDER_RATE_LIMITED"),
        ("creator_content.list", "success", None, None),
    ]


def test_tikhub_provider_requires_key_and_is_allowed_by_pilot_preflight():
    with pytest.raises(RuntimeError, match="TIKHUB_API_KEY"):
        _build_collection_provider(Settings(collection_provider="tikhub-v1"))

    provider = _build_collection_provider(
        Settings(collection_provider="tikhub-v1", tikhub_api_key="test-secret")
    )
    assert provider.capabilities()["kinds"] == [
        "keyword",
        "creator_content",
        "creator_profile",
    ]
    assert provider.capabilities()["sample_inputs"] == {
        "single": "",
        "keyword": "AI 工具",
        "creator_content": "",
        "creator_profile": "",
    }

    with pytest.raises(RuntimeError, match="TIKHUB_API_KEY"):
        validate_deployment_settings(
            Settings(
                deployment_profile="pilot",
                database_url="sqlite:////tmp/riffloom-pilot-preflight.db",
                database_schema_mode="migrations_only",
                database_migrate_on_startup=True,
                database_backup_enabled=True,
                database_restore_policy="required",
                sqlite_single_instance_acknowledged=True,
                object_storage_provider="tos_mount",
                object_storage_mount_dir="/mnt/riffloom-tos",
                auth_mode="invite_token",
                auth_secret="test-only-pilot-secret-at-least-32-bytes",
                api_docs_enabled=False,
                log_format="json",
                collection_provider="tikhub-v1",
            )
        )


def test_hybrid_provider_exposes_four_kinds_through_status_endpoint(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'hybrid.db'}",
            asset_storage_dir=str(tmp_path / "uploads"),
            collection_provider="xiaohongshu-hybrid-v1",
            tikhub_api_key="test-secret",
            worker_step_delay=0,
            worker_threads=1,
            log_level="WARNING",
        )
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/collection-provider",
            headers={
                "X-Riffloom-User": "user_demo",
                "X-Riffloom-Workspace": "ws_demo",
            },
        )
    assert response.status_code == 200
    assert response.json()["mode"] == "production_third_party"
    assert response.json()["kinds"] == [
        "single",
        "keyword",
        "creator_content",
        "creator_profile",
    ]
