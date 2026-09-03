from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import _build_collection_provider, create_app
from app.models import AgentTask, CollectionRecord, Conversation
from app.providers import CollectionRequest, OpenCLILocalCollectorProvider, ProviderError
from app.providers.opencli_local import CommandResult, SubprocessCommandRunner
from conftest import wait_for_terminal


SIGNED_URL = (
    "https://www.xiaohongshu.com/explore/66c012345678901234567890"
    "?xsec_token=token-for-test"
)


class FakeRunner:
    def __init__(self, outputs: list[object]):
        self.outputs = list(outputs)
        self.calls: list[list[str]] = []

    def run(self, args: list[str], timeout_seconds: float) -> CommandResult:
        self.calls.append(args)
        output = self.outputs.pop(0)
        if isinstance(output, CommandResult):
            return output
        return CommandResult(tuple(args), 0, json.dumps(output, ensure_ascii=False), "")


def request(kind: str, query: dict, *, limit: int = 20) -> CollectionRequest:
    return CollectionRequest(
        kind=kind,  # type: ignore[arg-type]
        platform="xiaohongshu",
        query=query,
        limit=limit,
        attempt_no=1,
    )


def test_local_provider_is_explicitly_local_and_never_cloud_enabled():
    provider = _build_collection_provider(
        Settings(collection_provider="opencli-local-v1", deployment_profile="local")
    )
    capabilities = provider.capabilities()
    assert capabilities["mode"] == "experimental_local_helper"
    assert capabilities["requires_local_browser"] is True
    assert capabilities["video_transcript_required"] is True
    assert capabilities["kinds"] == ["single", "keyword", "creator_content"]

    with pytest.raises(RuntimeError, match="只能在用户本机运行"):
        _build_collection_provider(
            Settings(collection_provider="opencli-local-v1", deployment_profile="pilot")
        )


def test_opencli_child_process_does_not_receive_backend_secrets(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "must-not-leak")
    monkeypatch.setenv("FEISHU_APP_SECRET", "must-not-leak")
    result = SubprocessCommandRunner().run(
        [
            sys.executable,
            "-c",
            "import json, os; print(json.dumps({'home': bool(os.getenv('HOME')), 'ark': os.getenv('ARK_API_KEY'), 'feishu': os.getenv('FEISHU_APP_SECRET')}))",
        ],
        5,
    )

    assert json.loads(result.stdout) == {
        "home": True,
        "ark": None,
        "feishu": None,
    }


def test_single_image_note_uses_read_only_commands_and_normalizes_detail():
    runner = FakeRunner(
        [
            [
                {"field": "title", "value": "真实笔记标题"},
                {"field": "author", "value": "测试作者"},
                {"field": "content", "value": "真实页面正文"},
                {"field": "likes", "value": "1.2万"},
                {"field": "collects", "value": "345"},
                {"field": "comments", "value": "67"},
                {"field": "tags", "value": "AI, 工作流"},
            ],
            {"opened": True},
            {"content_type": "image", "transcript": "", "segments": []},
            {"closed": True},
        ]
    )
    provider = OpenCLILocalCollectorProvider(runner=runner)

    batch = provider.collect(request("single", {"url": SIGNED_URL}, limit=1))

    assert not batch.item_errors
    assert len(batch.content_items) == 1
    item = batch.content_items[0]
    assert item.title == "真实笔记标题"
    assert item.body == "真实页面正文"
    assert item.metrics == {"likes": 12_000, "collects": 345, "comments": 67}
    assert item.content_type == "图文"
    assert item.actual_upstream == "opencli/xiaohongshu"
    assert item.canonical_url == "https://www.xiaohongshu.com/explore/66c012345678901234567890"
    flattened = " ".join(" ".join(call) for call in runner.calls)
    assert "xsec_token=token-for-test" in flattened
    assert "xiaohongshu note" in flattened
    assert "browser" in flattened
    assert runner.calls[0][-2:] == ["-f", "json"]
    assert all("-f" not in call for call in runner.calls[1:])
    assert not any(
        forbidden in flattened
        for forbidden in (" download ", " doctor ", " publish ", " login ", " Cookie")
    )


def test_signed_url_is_transient_during_async_collection(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'opencli-task.db'}",
        asset_storage_dir=str(tmp_path / "uploads"),
        collection_provider="opencli-local-v1",
        worker_step_delay=0,
        log_level="WARNING",
    )
    app = create_app(settings)
    runner = FakeRunner(
        [
            [
                {"field": "title", "value": "真实笔记标题"},
                {"field": "author", "value": "测试作者"},
                {"field": "content", "value": "真实页面正文"},
            ],
            {"opened": True},
            {"content_type": "image", "transcript": "", "segments": []},
            {"closed": True},
        ]
    )
    app.state.collection_provider.runner = runner
    headers = {
        "X-Riffloom-User": "user_demo",
        "X-Riffloom-Workspace": "ws_demo",
        "Idempotency-Key": "opencli-transient-url-001",
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/collection-tasks",
            headers=headers,
            json={
                "kind": "single",
                "platform": "xiaohongshu",
                "query": {"url": SIGNED_URL},
                "usage_confirmed": True,
                "refresh": False,
                "limit": 1,
            },
        )
        task = wait_for_terminal(client, response.json()["id"], headers)
        assert task["status"] == "success"
        with app.state.database.session_factory() as session:
            stored_task = session.get(AgentTask, task["id"])
            conversation = session.get(Conversation, stored_task.conversation_id)
            record = session.get(CollectionRecord, task["result_refs"][0]["id"])
            persisted = json.dumps(
                [stored_task.input_snapshot, conversation.messages, record.canonical_url]
            )

        assert "xsec_token" not in persisted
        assert "token-for-test" not in persisted
        assert "token-for-test" in " ".join(" ".join(call) for call in runner.calls)
        assert not app.state.worker._collection_queries


def test_video_note_without_verified_transcript_is_marked_required():
    runner = FakeRunner(
        [
            [
                {"field": "title", "value": "视频笔记"},
                {"field": "author", "value": "测试作者"},
                {"field": "content", "value": "页面简介不是口播文案"},
            ],
            {"opened": True},
            {"content_type": "video", "transcript": "", "segments": []},
            {"closed": True},
        ]
    )
    batch = OpenCLILocalCollectorProvider(runner=runner).collect(
        request("single", {"url": SIGNED_URL}, limit=1)
    )

    assert batch.item_errors == []
    assert len(batch.content_items) == 1
    assert batch.content_items[0].content_type == "视频"
    assert batch.content_items[0].video_transcript is None
    assert batch.content_items[0].video_transcript_status == "required"


def test_video_note_accepts_only_real_text_track_as_transcript():
    runner = FakeRunner(
        [
            [
                {"field": "title", "value": "视频笔记"},
                {"field": "author", "value": "测试作者"},
                {"field": "content", "value": "页面简介"},
            ],
            {"opened": True},
            {
                "content_type": "video",
                "transcript": "第一句。\n第二句。",
                "segments": [
                    {"start_ms": 0, "end_ms": 1200, "text": "第一句。"},
                    {"start_ms": 1200, "end_ms": 2500, "text": "第二句。"},
                ],
            },
            {"closed": True},
        ]
    )
    item = OpenCLILocalCollectorProvider(runner=runner).collect(
        request("single", {"url": SIGNED_URL}, limit=1)
    ).content_items[0]

    assert item.content_type == "视频"
    assert item.video_transcript == "第一句。\n第二句。"
    assert item.video_transcript_status == "complete"
    assert item.video_transcript_source == "page_text_track"
    assert item.video_transcript_segments[1]["end_ms"] == 2500


def test_keyword_search_can_request_recent_most_liked_signals():
    runner = FakeRunner(
        [
            [
                {
                    "rank": 1,
                    "title": "近期高互动内容",
                    "author": "作者",
                    "likes": "2.5万",
                    "published_at": "2026-08-28",
                    "url": SIGNED_URL,
                }
            ]
        ]
    )
    batch = OpenCLILocalCollectorProvider(
        runner=runner, inspect_single_pages=False
    ).collect(
        request(
            "keyword",
            {"keyword": "AI 工具", "sort": "most-liked", "publish_time": "week"},
            limit=10,
        )
    )

    assert batch.content_items[0].metrics["likes"] == 25_000
    assert batch.content_items[0].body == ""
    assert batch.content_items[0].video_transcript_status == "not_collected"
    assert runner.calls[0] == [
        "opencli",
        "xiaohongshu",
        "search",
        "AI 工具",
        "--limit",
        "10",
        "--sort",
        "most-liked",
        "--publish-time",
        "week",
        "-f",
        "json",
    ]


def test_unsigned_or_bare_note_url_is_rejected_before_browser_call():
    runner = FakeRunner([])
    provider = OpenCLILocalCollectorProvider(runner=runner)
    with pytest.raises(ProviderError) as captured:
        provider.collect(
            request(
                "single",
                {"url": "https://www.xiaohongshu.com/explore/66c012345678901234567890"},
                limit=1,
            )
        )
    assert captured.value.code == "XHS_SIGNED_URL_REQUIRED"
    assert runner.calls == []
