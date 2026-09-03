from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class TranscriptionRequest:
    duration_seconds: float
    mime_type: str
    data_url: str | None = None
    source_url: str | None = None
    language: str = "zh"


@dataclass(frozen=True)
class TranscriptSegment:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    segments: list[TranscriptSegment]
    provider: str
    model: str
    provider_request_id: str
    elapsed_ms: int
    confidence: float | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VideoAnalysisResult:
    analysis: dict[str, Any]
    provider: str
    model: str
    provider_request_id: str
    elapsed_ms: int


class TranscriptionProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class TranscriptionProvider(Protocol):
    provider_id: str

    def capabilities(self) -> dict[str, Any]: ...

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult: ...

    def analyze_video(
        self, request: TranscriptionRequest, *, transcript: str
    ) -> VideoAnalysisResult: ...


class DisabledTranscriptionProvider:
    provider_id = "disabled"

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.provider_id,
            "enabled": False,
            "external_calls": False,
            "short_model": None,
            "long_model": None,
            "short_max_seconds": 0,
        }

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        raise TranscriptionProviderError(
            "ASR_NOT_CONFIGURED",
            "视频转写 Provider 尚未启用",
            retryable=False,
        )

    def analyze_video(
        self, request: TranscriptionRequest, *, transcript: str
    ) -> VideoAnalysisResult:
        raise TranscriptionProviderError(
            "VIDEO_ANALYSIS_NOT_CONFIGURED",
            "视频画面分析 Provider 尚未启用",
            retryable=False,
        )


_ASR_JSON_PROMPT = (
    "请把音频中的全部语音逐字转写为文字，只输出一个 JSON 对象，不要输出任何解释或 markdown 代码块。"
    "请根据语义补充逗号、句号、问号和感叹号，但不得改写、增删口语内容。"
    "JSON 格式为："
    '{"text": "完整转写文本", "segments": [{"start": 秒, "end": 秒, "text": "该句文本"}]}'
    "其中 start 与 end 是秒（数字，保留两位小数），segments 按语义句切分，"
    "所有 segments 的 text 按顺序拼接后必须与 text 完全一致。"
)

_VIDEO_ANALYSIS_PROMPT = (
    "请完整观看视频，结合画面、声音、字幕和已有转写，提取用于内容拆解的客观信息。"
    "只输出一个 JSON 对象，不要输出解释或 markdown。JSON 格式为："
    '{"summary":"一句话概括","timeline":[{"start":0,"end":3,"role":"开头钩子",'
    '"visual":"画面","audio":"声音","text":"字幕或口播"}],'
    '"visual_hooks":["可观察到的视觉钩子"],"editing":["剪辑节奏与转场"],'
    '"audio":["人声、音乐或音效"],"onscreen_text":["字幕或屏幕文字"]}'
    "timeline 按时间排序，最多 10 段；其他数组各最多 5 条。"
    "只写视频中确实能观察到的内容，不得推测。"
)

_PUNCTUATION = frozenset("，。！？；：,.!?;:")
_SENTENCE_ENDINGS = frozenset("。！？；!?;")


def _format_for_mime(mime_type: str) -> str:
    formats = {
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/mp4": "m4a",
        "audio/x-m4a": "m4a",
        "video/mp4": "mp4",
    }
    try:
        return formats[mime_type]
    except KeyError as exc:
        raise TranscriptionProviderError(
            "ASR_MEDIA_TYPE_UNSUPPORTED",
            "视频转写当前只支持 MP3、WAV、M4A 或 MP4",
            retryable=False,
        ) from exc


def _data_url_b64(data_url: str) -> str:
    if "," in data_url:
        return data_url.split(",", 1)[1]
    return data_url


def _response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
    output = payload.get("output")
    if isinstance(output, dict) and isinstance(output.get("text"), str):
        return output["text"]
    return ""


def _parse_transcript(content: str) -> tuple[str, list[TranscriptSegment]]:
    text = (content or "").strip()
    if not text:
        return "", []
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
            transcript = str(payload.get("text") or "").strip()
            if transcript:
                segments: list[TranscriptSegment] = []
                raw_segments = payload.get("segments") or []
                if isinstance(raw_segments, list):
                    for item in raw_segments:
                        if not isinstance(item, dict):
                            continue
                        seg_text = str(item.get("text") or "").strip()
                        if not seg_text:
                            continue
                        try:
                            seg_start = max(0.0, float(item.get("start") or 0))
                            seg_end = max(0.0, float(item.get("end") or 0))
                        except (TypeError, ValueError):
                            seg_start = seg_end = 0.0
                        segments.append(
                            TranscriptSegment(
                                start_ms=int(round(seg_start * 1000)),
                                end_ms=int(round(seg_end * 1000)),
                                text=seg_text,
                            )
                        )
                if len(segments) > 1 and not any(
                    character in _PUNCTUATION for character in transcript
                ):
                    segments = [
                        replace(
                            segment,
                            text=(
                                segment.text
                                if segment.text[-1] in _SENTENCE_ENDINGS
                                else f"{segment.text}。"
                            ),
                        )
                        for segment in segments
                    ]
                    transcript = "".join(segment.text for segment in segments)
                return transcript, segments
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return text.strip("`").strip(), []


def _parse_video_analysis(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise TranscriptionProviderError(
            "VIDEO_ANALYSIS_OUTPUT_INVALID",
            "视频画面分析未返回可用的结构化结果",
            retryable=False,
        )
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise TranscriptionProviderError(
            "VIDEO_ANALYSIS_OUTPUT_INVALID",
            "视频画面分析结果无法解析",
            retryable=False,
        ) from exc
    if not isinstance(payload, dict) or not str(payload.get("summary") or "").strip():
        raise TranscriptionProviderError(
            "VIDEO_ANALYSIS_OUTPUT_INVALID",
            "视频画面分析缺少内容概括",
            retryable=False,
        )
    return payload


class VolcengineDoubaoASRProvider:
    """Transcribe media through Volcengine Ark's Doubao audio understanding.

    Uses the same Ark endpoint and ``ARK_API_KEY`` as Riffloom's vision and
    embedding routes. A single synchronous Chat Completions call handles the
    whole product upload boundary (300 seconds), so there is no separate long
    media path. The prompt requests a JSON object with a full transcript plus
    sentence-level start/end timestamps; when the model ignores the JSON shape
    the raw text is still kept as the transcript without segments.
    """

    provider_id = "volcengine-doubao-asr-v1"

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        model: str = "doubao-seed-2-0-lite-260428",
        max_seconds: int = 300,
        timeout_seconds: float = 300.0,
        client: Any | None = None,
    ):
        if not api_key:
            raise TranscriptionProviderError(
                "ASR_NOT_CONFIGURED",
                "ARK_API_KEY 尚未在服务端配置",
                retryable=False,
            )
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("ASR base_url must be HTTPS")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_seconds = max(1, max_seconds)
        self.client = client or httpx.Client(
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.provider_id,
            "enabled": True,
            "external_calls": True,
            "short_model": self.model,
            "long_model": None,
            "short_max_seconds": self.max_seconds,
            "raw_retention_hours": 24,
        }

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        if request.duration_seconds <= 0:
            raise TranscriptionProviderError(
                "ASR_DURATION_INVALID", "媒体时长无效", retryable=False
            )
        if request.duration_seconds > self.max_seconds:
            raise TranscriptionProviderError(
                "ASR_DURATION_TOO_LONG",
                f"当前上传转写只支持 {self.max_seconds} 秒以内媒体",
                retryable=False,
            )
        source = request.data_url
        source_url = request.source_url
        if not source and not source_url:
            raise TranscriptionProviderError(
                "ASR_MEDIA_REQUIRED", "转写缺少媒体内容", retryable=False
            )
        if source_url:
            parsed_source = urlsplit(source_url)
            if parsed_source.scheme != "https" or not parsed_source.hostname:
                raise TranscriptionProviderError(
                    "ASR_MEDIA_URL_INVALID", "转写媒体 URL 必须为 HTTPS", retryable=False
                )
        prompt = _ASR_JSON_PROMPT
        if request.language and request.language != "zh":
            prompt = f"{prompt}音频语言：{request.language}。"
        started = time.monotonic()
        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                (
                                    {"type": "video_url", "video_url": {"url": source_url}}
                                    if source_url
                                    else {
                                        "type": "input_audio",
                                        "input_audio": {
                                            "data": _data_url_b64(source or ""),
                                            "format": _format_for_mime(request.mime_type),
                                        },
                                    }
                                ),
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
        except TranscriptionProviderError:
            raise
        except Exception as exc:
            raise TranscriptionProviderError(
                "ASR_CALL_FAILED", "火山豆包语音识别调用失败"
            ) from exc
        text, segments = _parse_transcript(_response_text(payload))
        if not text:
            raise TranscriptionProviderError(
                "ASR_OUTPUT_INVALID",
                "豆包转写成功响应中没有可用视频文案",
                retryable=False,
            )
        request_id = str(payload.get("id") or "") if isinstance(payload, dict) else ""
        return TranscriptionResult(
            text=text,
            segments=segments,
            provider=self.provider_id,
            model=self.model,
            provider_request_id=request_id,
            elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
            raw_metadata={
                "mode": "synchronous",
                "duration_seconds": request.duration_seconds,
            },
        )

    def analyze_video(
        self, request: TranscriptionRequest, *, transcript: str
    ) -> VideoAnalysisResult:
        if request.mime_type != "video/mp4":
            raise TranscriptionProviderError(
                "VIDEO_ANALYSIS_MEDIA_TYPE_UNSUPPORTED",
                "视频画面分析当前只支持 MP4",
                retryable=False,
            )
        if request.duration_seconds <= 0 or request.duration_seconds > self.max_seconds:
            raise TranscriptionProviderError(
                "VIDEO_ANALYSIS_DURATION_INVALID",
                f"视频画面分析只支持 1～{self.max_seconds} 秒视频",
                retryable=False,
            )
        source_url = request.source_url
        parsed_source = urlsplit(source_url or "")
        if not source_url or parsed_source.scheme != "https" or not parsed_source.hostname:
            raise TranscriptionProviderError(
                "VIDEO_ANALYSIS_MEDIA_URL_INVALID",
                "视频画面分析需要可读取的 HTTPS 视频地址",
                retryable=False,
            )
        prompt = f"{_VIDEO_ANALYSIS_PROMPT}\n已有转写：{transcript[:12000]}"
        started = time.monotonic()
        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "video_url", "video_url": {"url": source_url}},
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise TranscriptionProviderError(
                "VIDEO_ANALYSIS_CALL_FAILED",
                "视频画面与声音分析失败，请重试",
            ) from exc
        return VideoAnalysisResult(
            analysis=_parse_video_analysis(_response_text(payload)),
            provider=self.provider_id,
            model=self.model,
            provider_request_id=str(payload.get("id") or "") if isinstance(payload, dict) else "",
            elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
        )
