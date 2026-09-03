from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import uuid4

import httpx

from .base import CollectionRequest, ProviderBatch, ProviderContent, ProviderError, ProviderMedia


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class XiaohongshuWebCollectorProvider:
    """Read a user-authorized, signed Xiaohongshu note and retain its media."""

    provider_id = "xiaohongshu-web-v1"
    is_sandbox = False

    def __init__(
        self,
        *,
        timeout_seconds: float = 60,
        media_max_bytes: int = 50 * 1024 * 1024,
        client: httpx.Client | None = None,
    ):
        self.client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=False)
        self.media_max_bytes = media_max_bytes

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider_id,
            "mode": "production",
            "is_sandbox": False,
            "platforms": ["xiaohongshu"],
            "kinds": ["single"],
            "max_items": 1,
            "sample_inputs": {
                "single": "",
                "keyword": "",
                "creator_content": "",
                "creator_profile": "",
            },
            "actual_upstream": "xiaohongshu-web",
            "requires_local_browser": False,
            "external_calls": True,
            "video_transcript_required": True,
        }

    def collect(self, request: CollectionRequest) -> ProviderBatch:
        if request.kind != "single" or request.platform != "xiaohongshu":
            raise ProviderError(
                "COLLECTION_KIND_NOT_SUPPORTED",
                "当前线上真实采集仅支持小红书单篇内容链接",
                retryable=False,
            )
        started = time.monotonic()
        url = str(request.query.get("url") or "").strip()
        response, resolved_url = self._get(url, page=True, max_bytes=2 * 1024 * 1024)
        parts = urlsplit(resolved_url)
        if not re.search(r"(?:^|&)xsec_token=[^&]+", parts.query):
            raise ProviderError(
                "SOURCE_ACCESS_REQUIRED",
                "请粘贴小红书完整分享链接（需要包含 xsec_token）",
                retryable=False,
            )
        note_id = self._note_id(parts.path)
        state = self._initial_state(response.text)
        note = self._note(state, note_id)
        media = self._download_media(note)
        content_type = "视频" if any(item.kind == "video" for item in media) else "图文"
        item = ProviderContent(
            external_id=str(note.get("noteId") or note_id),
            canonical_url=f"https://www.xiaohongshu.com/explore/{note_id}",
            title=str(note.get("title") or "").strip() or str(note.get("desc") or "")[:60],
            body=str(note.get("desc") or "").strip(),
            author_name=str((note.get("user") or {}).get("nickname") or "未知博主"),
            author_external_id=str((note.get("user") or {}).get("userId") or "") or None,
            content_type=content_type,
            published_at=self._timestamp(note.get("time")),
            topics=[
                str(tag.get("name") or "").strip()
                for tag in (note.get("tagList") or [])
                if isinstance(tag, dict) and str(tag.get("name") or "").strip()
            ],
            metrics=self._metrics(note.get("interactInfo") or {}),
            actual_upstream="xiaohongshu-web",
            video_transcript_status="required" if content_type == "视频" else "not_applicable",
            downloaded_media=media,
        )
        return ProviderBatch(
            request_id=f"xhs_{uuid4().hex[:16]}",
            provider=self.provider_id,
            content_items=[item],
            elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
            rate_limit={},
        )

    def _get(self, url: str, *, page: bool, max_bytes: int) -> tuple[httpx.Response, str]:
        parts = urlsplit(url)
        current = (
            urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))
            if not page and parts.scheme == "http"
            else url
        )
        for _ in range(4):
            self._validate_url(current, page=page)
            try:
                response = self.client.get(
                    current,
                    headers={"User-Agent": _USER_AGENT, "Referer": "https://www.xiaohongshu.com/"},
                )
            except httpx.HTTPError as exc:
                raise ProviderError(
                    "PROVIDER_REQUEST_FAILED", "小红书页面或媒体请求失败", retryable=True
                ) from exc
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    break
                current = urljoin(current, location)
                continue
            if response.status_code == 404:
                raise ProviderError("SOURCE_NOT_FOUND", "小红书内容不存在或暂不可见", retryable=False)
            if response.status_code == 429:
                raise ProviderError(
                    "PROVIDER_RATE_LIMITED", "小红书请求过于频繁，请稍后重试", retryable=True, status_code=429
                )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ProviderError(
                    "PROVIDER_REQUEST_FAILED", "小红书页面或媒体请求失败", retryable=response.status_code >= 500,
                    status_code=response.status_code,
                ) from exc
            if len(response.content) > max_bytes:
                raise ProviderError("MEDIA_TOO_LARGE", "小红书媒体超过 50 MiB 下载上限", retryable=False)
            return response, current
        raise ProviderError("SOURCE_REDIRECT_INVALID", "小红书链接重定向次数过多", retryable=False)

    @staticmethod
    def _validate_url(url: str, *, page: bool) -> None:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        allowed = (
            host in {"xiaohongshu.com", "xhslink.com"}
            or host.endswith((".xiaohongshu.com", ".xhslink.com"))
            if page
            else host == "xhscdn.com" or host.endswith(".xhscdn.com")
        )
        if parts.scheme != "https" or not allowed or parts.username or parts.password or parts.port:
            raise ProviderError(
                "SOURCE_NOT_AUTHORIZED" if page else "MEDIA_SOURCE_NOT_AUTHORIZED",
                "只允许读取 HTTPS 小红书页面及其官方媒体域名",
                retryable=False,
            )

    @staticmethod
    def _note_id(path: str) -> str:
        match = re.search(r"/(?:explore|discovery/item)/([0-9a-f]{24})(?:/|$)", path, re.I)
        if not match:
            raise ProviderError("SOURCE_NOT_FOUND", "小红书链接缺少有效笔记 ID", retryable=False)
        return match.group(1).lower()

    @staticmethod
    def _initial_state(html: str) -> dict[str, Any]:
        marker = "window.__INITIAL_STATE__="
        start = html.find(marker)
        if start < 0:
            raise ProviderError("SOURCE_PARSE_FAILED", "小红书页面缺少可读取内容", retryable=False)
        raw = html[start + len(marker) :].split("</script>", 1)[0].strip().rstrip(";")
        raw = re.sub(r"(?<![\w\"'])undefined(?![\w\"'])", "null", raw)
        try:
            state = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError("SOURCE_PARSE_FAILED", "小红书页面内容解析失败", retryable=False) from exc
        if not isinstance(state, dict):
            raise ProviderError("SOURCE_PARSE_FAILED", "小红书页面内容格式无效", retryable=False)
        return state

    @staticmethod
    def _note(state: dict[str, Any], note_id: str) -> dict[str, Any]:
        detail_map = ((state.get("note") or {}).get("noteDetailMap") or {})
        wrapper = detail_map.get(note_id) if isinstance(detail_map, dict) else None
        if not isinstance(wrapper, dict):
            wrapper = next((value for value in detail_map.values() if isinstance(value, dict)), None)
        note = wrapper.get("note") if isinstance(wrapper, dict) else None
        if not isinstance(note, dict) or not note.get("noteId"):
            raise ProviderError("SOURCE_NOT_FOUND", "小红书内容不存在或暂不可见", retryable=False)
        return note

    def _download_media(self, note: dict[str, Any]) -> list[ProviderMedia]:
        media: list[ProviderMedia] = []
        images = [item for item in (note.get("imageList") or []) if isinstance(item, dict)]
        has_video = isinstance(note.get("video"), dict)
        for index, image in enumerate(images):
            url = next(
                (str(image.get(key)) for key in ("urlDefault", "url", "urlPre") if image.get(key)),
                "",
            )
            if not url:
                continue
            response, resolved = self._get(url, page=False, max_bytes=self.media_max_bytes)
            mime = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
            media.append(
                ProviderMedia(
                    kind="cover" if has_video and index == 0 else "image",
                    source_url=resolved,
                    content=response.content,
                    mime_type=mime if mime in {"image/jpeg", "image/png", "image/webp"} else "image/jpeg",
                    width=self._integer(image.get("width")),
                    height=self._integer(image.get("height")),
                )
            )
        video = self._video_stream(note.get("video"))
        if video:
            url = str(video.get("masterUrl") or (video.get("backupUrls") or [""])[0])
            response, resolved = self._get(url, page=False, max_bytes=self.media_max_bytes)
            duration_ms = video.get("videoDuration") or video.get("duration") or 0
            media.append(
                ProviderMedia(
                    kind="video",
                    source_url=resolved,
                    content=response.content,
                    mime_type="video/mp4",
                    width=self._integer(video.get("width")),
                    height=self._integer(video.get("height")),
                    duration_seconds=max(0, self._number(duration_ms) / 1000),
                )
            )
        if not media:
            raise ProviderError("MEDIA_NOT_FOUND", "小红书内容没有可下载的图片或视频", retryable=False)
        return media

    @staticmethod
    def _video_stream(video: Any) -> dict[str, Any] | None:
        streams = (((video or {}).get("media") or {}).get("stream") or {})
        if not isinstance(streams, dict):
            return None
        for key in ("h264", "H264", "avc", "AVC", "EF4", *streams.keys()):
            options = streams.get(key)
            if isinstance(options, list):
                candidate = next(
                    (item for item in options if isinstance(item, dict) and (item.get("masterUrl") or item.get("backupUrls"))),
                    None,
                )
                if candidate:
                    return candidate
        return None

    @classmethod
    def _metrics(cls, value: Any) -> dict[str, int]:
        info = value if isinstance(value, dict) else {}
        return {
            "likes": cls._integer(info.get("likedCount") or info.get("niceCount")),
            "collects": cls._integer(info.get("collectedCount")),
            "comments": cls._integer(info.get("commentCount")),
            "shares": cls._integer(info.get("shareCount")),
            "views": cls._integer(info.get("viewCount") or info.get("playCount")),
        }

    @staticmethod
    def _number(value: Any) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        text = str(value or "").strip().replace(",", "")
        multiplier = 10_000 if text.endswith("万") else 1
        try:
            return float(text.rstrip("万")) * multiplier
        except ValueError:
            return 0

    @classmethod
    def _integer(cls, value: Any) -> int:
        return int(cls._number(value))

    @classmethod
    def _timestamp(cls, value: Any) -> datetime | None:
        timestamp = cls._number(value)
        if timestamp <= 0:
            return None
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
