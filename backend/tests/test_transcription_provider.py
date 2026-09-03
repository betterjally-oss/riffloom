from __future__ import annotations

import json

import pytest

from app.providers import (
    TranscriptionProviderError,
    TranscriptionRequest,
    VolcengineDoubaoASRProvider,
)


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, *, posts: list[dict] | None = None):
        self.posts = list(posts or [])
        self.requests: list[tuple[str, dict]] = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return FakeResponse(self.posts.pop(0))


def test_doubao_asr_calls_ark_chat_completions_and_parses_json():
    transcript = {
        "text": "先给结论，再展示过程，最后邀请观众互动。",
        "segments": [
            {"start": 0.0, "end": 2.5, "text": "先给结论，再展示过程，最后邀请观众互动。"}
        ],
    }
    client = FakeClient(
        posts=[
            {
                "id": "req_doubao_1",
                "model": "doubao-seed-2-0-lite-260428",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(transcript, ensure_ascii=False),
                        }
                    }
                ],
            }
        ]
    )
    provider = VolcengineDoubaoASRProvider(api_key="test-key", client=client)

    result = provider.transcribe(
        TranscriptionRequest(
            duration_seconds=300,
            mime_type="audio/mpeg",
            data_url="data:audio/mpeg;base64,dGVzdA==",
        )
    )

    assert result.model == "doubao-seed-2-0-lite-260428"
    assert result.text == "先给结论，再展示过程，最后邀请观众互动。"
    assert result.segments[0].start_ms == 0
    assert result.segments[0].end_ms == 2500
    url, kwargs = client.requests[0]
    assert url.endswith("/chat/completions")
    assert kwargs["json"]["model"] == "doubao-seed-2-0-lite-260428"
    content = kwargs["json"]["messages"][0]["content"]
    assert content[0] == {
        "type": "input_audio",
        "input_audio": {"data": "dGVzdA==", "format": "mp3"},
    }
    assert content[1]["type"] == "text"


def test_doubao_asr_falls_back_to_plain_text_when_model_ignores_json():
    client = FakeClient(
        posts=[
            {
                "id": "req_doubao_2",
                "choices": [{"message": {"content": "这是没有 JSON 的纯转写文本。"}}],
            }
        ]
    )
    provider = VolcengineDoubaoASRProvider(api_key="test-key", client=client)
    result = provider.transcribe(
        TranscriptionRequest(
            duration_seconds=10,
            mime_type="audio/wav",
            data_url="data:audio/wav;base64,dGVzdA==",
        )
    )
    assert result.text == "这是没有 JSON 的纯转写文本。"
    assert result.segments == []


def test_doubao_asr_restores_sentence_punctuation_from_segments():
    transcript = {
        "text": "对他唯一遗憾是分手那天我奔腾的眼泪都停不下来若那一刻重来我不哭让他知道我可以很好",
        "segments": [
            {"start": 0, "end": 2, "text": "对他唯一遗憾是分手那天"},
            {"start": 2, "end": 4, "text": "我奔腾的眼泪都停不下来"},
            {"start": 4, "end": 6, "text": "若那一刻重来我不哭"},
            {"start": 6, "end": 8, "text": "让他知道我可以很好"},
        ],
    }
    client = FakeClient(
        posts=[
            {
                "id": "req-no-punctuation",
                "choices": [
                    {"message": {"content": json.dumps(transcript, ensure_ascii=False)}}
                ],
            }
        ]
    )
    provider = VolcengineDoubaoASRProvider(api_key="test-key", client=client)

    result = provider.transcribe(
        TranscriptionRequest(
            duration_seconds=8,
            mime_type="audio/mpeg",
            data_url="data:audio/mpeg;base64,dGVzdA==",
        )
    )

    assert result.text == (
        "对他唯一遗憾是分手那天。"
        "我奔腾的眼泪都停不下来。"
        "若那一刻重来我不哭。"
        "让他知道我可以很好。"
    )
    assert all(segment.text.endswith("。") for segment in result.segments)


def test_doubao_asr_can_send_an_https_video_url():
    client = FakeClient(
        posts=[{"id": "req-video", "choices": [{"message": {"content": '{"text":"视频文案","segments":[]}'}}]}]
    )
    provider = VolcengineDoubaoASRProvider(api_key="test-key", client=client)
    provider.transcribe(
        TranscriptionRequest(
            duration_seconds=10,
            mime_type="video/mp4",
            source_url="https://media.example.com/video.mp4?signature=test",
        )
    )

    content = client.requests[0][1]["json"]["messages"][0]["content"]
    assert content[0] == {
        "type": "video_url",
        "video_url": {"url": "https://media.example.com/video.mp4?signature=test"},
    }


def test_doubao_analyzes_video_as_structured_visual_evidence():
    analysis = {
        "summary": "用现场演唱和字幕情绪反差抓住注意力",
        "timeline": [
            {
                "start": 0,
                "end": 3,
                "role": "开头钩子",
                "visual": "人物近景",
                "audio": "演唱",
                "text": "字幕同步出现",
            }
        ],
        "visual_hooks": ["人物近景"],
        "editing": ["长镜头"],
        "audio": ["现场演唱"],
        "onscreen_text": ["逐句字幕"],
    }
    client = FakeClient(
        posts=[
            {
                "id": "req-video-analysis",
                "choices": [
                    {"message": {"content": json.dumps(analysis, ensure_ascii=False)}}
                ],
            }
        ]
    )
    provider = VolcengineDoubaoASRProvider(api_key="test-key", client=client)

    result = provider.analyze_video(
        TranscriptionRequest(
            duration_seconds=20,
            mime_type="video/mp4",
            source_url="https://media.example.com/video.mp4?signature=test",
        ),
        transcript="对他唯一的遗憾。",
    )

    assert result.analysis == analysis
    content = client.requests[0][1]["json"]["messages"][0]["content"]
    assert content[0]["type"] == "video_url"
    assert "已有转写：对他唯一的遗憾。" in content[1]["text"]


def test_doubao_asr_rejects_duration_over_max():
    provider = VolcengineDoubaoASRProvider(api_key="test-key", client=FakeClient())
    with pytest.raises(TranscriptionProviderError) as captured:
        provider.transcribe(
            TranscriptionRequest(
                duration_seconds=301,
                mime_type="video/mp4",
                data_url="data:video/mp4;base64,dGVzdA==",
            )
        )
    assert captured.value.code == "ASR_DURATION_TOO_LONG"


def test_doubao_asr_rejects_unsupported_media_type_before_calling_provider():
    client = FakeClient()
    provider = VolcengineDoubaoASRProvider(api_key="test-key", client=client)
    with pytest.raises(TranscriptionProviderError) as captured:
        provider.transcribe(
            TranscriptionRequest(
                duration_seconds=10,
                mime_type="application/octet-stream",
                data_url="data:application/octet-stream;base64,dGVzdA==",
            )
        )
    assert captured.value.code == "ASR_MEDIA_TYPE_UNSUPPORTED"
    assert client.requests == []


def test_doubao_asr_requires_api_key():
    with pytest.raises(TranscriptionProviderError) as captured:
        VolcengineDoubaoASRProvider(api_key=None)
    assert captured.value.code == "ASR_NOT_CONFIGURED"
