from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.providers import (
    CollectionRequest,
    TranscriptSegment,
    TranscriptionResult,
    XiaohongshuWebCollectorProvider,
)
from conftest import wait_for_terminal


NOTE_ID = "6a646e770000000010028db8"
SIGNED_URL = f"https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token=test-token&xsec_source=pc_user"


def _state() -> dict:
    return {
        "note": {
            "noteDetailMap": {
                NOTE_ID: {
                    "note": {
                        "noteId": NOTE_ID,
                        "title": "旅行转场教程",
                        "desc": "先固定机位，再完成动作衔接。",
                        "type": "video",
                        "time": 1_777_593_600_000,
                        "user": {"userId": "creator-88", "nickname": "旅行创作者"},
                        "tagList": [{"name": "转场"}, {"name": "旅行"}],
                        "interactInfo": {
                            "likedCount": "1.2万",
                            "collectedCount": "2300",
                            "commentCount": "88",
                            "shareCount": "66",
                        },
                        "imageList": [
                            {
                                "urlDefault": "http://sns-img-qc.xhscdn.com/cover.jpg?sign=private",
                                "width": 1080,
                                "height": 1440,
                            }
                        ],
                        "video": {
                            "media": {
                                "stream": {
                                    "EF4": [
                                        {
                                            "masterUrl": "http://sns-video-v6.xhscdn.com/video.mp4?sign=private",
                                            "videoDuration": 12_345,
                                            "width": 1280,
                                            "height": 720,
                                        }
                                    ]
                                }
                            }
                        },
                    }
                }
            }
        }
    }


def _client() -> httpx.Client:
    html = f'<script>window.__INITIAL_STATE__={json.dumps(_state(), ensure_ascii=False)}</script>'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.xiaohongshu.com":
            return httpx.Response(200, text=html, request=request)
        if request.url.path.endswith("cover.jpg"):
            return httpx.Response(200, content=b"jpeg-content", headers={"content-type": "image/jpeg"}, request=request)
        if request.url.path.endswith("video.mp4"):
            return httpx.Response(200, content=b"video-content", headers={"content-type": "video/mp4"}, request=request)
        return httpx.Response(404, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_signed_note_collects_complete_fields_and_downloads_media():
    provider = XiaohongshuWebCollectorProvider(client=_client())
    batch = provider.collect(
        CollectionRequest(
            kind="single",
            platform="xiaohongshu",
            query={"url": SIGNED_URL},
            limit=1,
            attempt_no=1,
        )
    )

    item = batch.content_items[0]
    assert item.canonical_url == f"https://www.xiaohongshu.com/explore/{NOTE_ID}"
    assert item.title == "旅行转场教程"
    assert item.body == "先固定机位，再完成动作衔接。"
    assert item.author_name == "旅行创作者"
    assert item.topics == ["转场", "旅行"]
    assert item.metrics == {"likes": 12_000, "collects": 2300, "comments": 88, "shares": 66, "views": 0}
    assert [(media.kind, media.content) for media in item.downloaded_media] == [
        ("cover", b"jpeg-content"),
        ("video", b"video-content"),
    ]
    assert item.downloaded_media[1].duration_seconds == 12.345
    assert "test-token" not in repr(item)


def test_capabilities_do_not_expose_input_guidance_as_a_sample():
    provider = XiaohongshuWebCollectorProvider(client=_client())

    assert provider.capabilities()["sample_inputs"]["single"] == ""


class FakeTranscriber:
    provider_id = "fake-asr"

    def __init__(self):
        self.requests = []

    def capabilities(self):
        return {"enabled": True}

    def transcribe(self, request):
        self.requests.append(request)
        return TranscriptionResult(
            text="固定机位，然后用动作衔接完成转场。",
            segments=[TranscriptSegment(start_ms=0, end_ms=3000, text="固定机位，然后用动作衔接完成转场。")],
            provider=self.provider_id,
            model="fake-video-model",
            provider_request_id="req-auto-1",
            elapsed_ms=3,
        )


class FakeMediaPresigner:
    enabled = True

    def __init__(self):
        self.calls = []

    def presigned_get(self, object_key, *, ttl_seconds):
        self.calls.append((object_key, ttl_seconds))
        return f"https://tos.example.test/{object_key}?signature=test"


def test_collection_task_persists_media_and_automatic_transcript(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'xhs.db'}",
        asset_storage_dir=str(tmp_path / "uploads"),
        collection_provider="xiaohongshu-web-v1",
        worker_step_delay=0,
        worker_threads=1,
        log_level="WARNING",
    )
    with TestClient(create_app(settings)) as client:
        client.app.state.collection_provider.client = _client()
        transcriber = FakeTranscriber()
        presigner = FakeMediaPresigner()
        client.app.state.worker.transcription_provider = transcriber
        client.app.state.worker.media_presigner = presigner
        headers = {"X-Riffloom-User": "user_demo", "X-Riffloom-Workspace": "ws_demo"}
        response = client.post(
            "/api/v1/collection-tasks",
            headers={**headers, "Idempotency-Key": "xhs-auto-download-1"},
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
        detail = client.get(
            f"/api/v1/collections/{task['result_refs'][0]['id']}", headers=headers
        ).json()

        assert task["status"] == "success"
        assert task["input"]["query"]["url"] == f"https://www.xiaohongshu.com/explore/{NOTE_ID}"
        assert detail["provider"] == "xiaohongshu-web-v1"
        assert detail["video_transcript_status"] == "complete"
        assert detail["video_transcript"] == "固定机位，然后用动作衔接完成转场。"
        assert detail["cover_url"].startswith("/api/v1/media-assets/")
        assert len(detail["media_refs"]) == 2
        assert all("private" not in json.dumps(ref) for ref in detail["media_refs"])
        assert transcriber.requests[0].data_url is None
        assert transcriber.requests[0].source_url.startswith("https://tos.example.test/")
        assert len(presigner.calls) == 1
        assert presigner.calls[0][0].endswith(".mp4")
        video_ref = next(ref for ref in detail["media_refs"] if ref["type"] == "video_attachment")
        media = client.get(video_ref["url"], headers=headers)
        assert media.status_code == 200
        assert media.headers["content-disposition"].endswith('.mp4"')
        assert media.content == b"video-content"
