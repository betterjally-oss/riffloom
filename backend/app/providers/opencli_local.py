from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit, urlunsplit
from uuid import uuid4

from .base import (
    CollectionRequest,
    ProviderBatch,
    ProviderContent,
    ProviderError,
    ProviderItemError,
)


_XHS_HOSTS = {"www.xiaohongshu.com", "xiaohongshu.com"}
_NOTE_PATH = re.compile(r"/(?:explore|search_result|note)/([0-9a-f]{24})(?:/|$)", re.I)
_COUNT_SUFFIXES = {"k": 1_000, "w": 10_000, "万": 10_000, "m": 1_000_000}


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(self, args: list[str], timeout_seconds: float) -> CommandResult: ...


class SubprocessCommandRunner:
    """Run OpenCLI locally without a shell or credential copying."""

    def run(self, args: list[str], timeout_seconds: float) -> CommandResult:
        allowed_environment = {
            "HOME",
            "LANG",
            "LC_ALL",
            "OPENCLI_BROWSER_COMMAND_TIMEOUT",
            "OPENCLI_BROWSER_CONNECT_TIMEOUT",
            "OPENCLI_CACHE_DIR",
            "OPENCLI_CDP_ENDPOINT",
            "OPENCLI_CDP_TARGET",
            "OPENCLI_PROFILE",
            "OPENCLI_WINDOW",
            "PATH",
            "TMPDIR",
            "USER",
        }
        environment = {
            key: value for key, value in os.environ.items() if key in allowed_environment
        }
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                check=False,
                env=environment,
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise ProviderError(
                "OPENCLI_NOT_INSTALLED",
                "本机尚未安装 OpenCLI，无法读取浏览器中的小红书页面",
                retryable=False,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(
                "OPENCLI_TIMEOUT",
                "OpenCLI 读取页面超时，请确认 Chrome 与扩展仍保持连接",
                retryable=True,
                status_code=504,
            ) from exc
        return CommandResult(
            args=tuple(args),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    raw = str(value or "").strip().lower().replace(",", "")
    if not raw:
        return 0
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([kw万m]?)", raw)
    if not match:
        return 0
    return max(0, int(float(match.group(1)) * _COUNT_SUFFIXES.get(match.group(2), 1)))


def _published_at(value: Any, canonical_url: str) -> datetime:
    raw = str(value or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    match = _NOTE_PATH.search(urlsplit(canonical_url).path)
    if match:
        try:
            return datetime.fromtimestamp(int(match.group(1)[:8], 16), tz=timezone.utc)
        except (OverflowError, ValueError):
            pass
    return datetime.now(timezone.utc)


def _unsigned_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _json_output(raw: str) -> Any:
    text = raw.strip()
    if not text:
        raise ProviderError(
            "OPENCLI_EMPTY_RESULT",
            "OpenCLI 没有返回可用数据",
            retryable=True,
        )
    try:
        value: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            "OPENCLI_OUTPUT_INVALID",
            "OpenCLI 返回的不是有效 JSON，请升级 OpenCLI 后重试",
            retryable=False,
        ) from exc
    # Browser eval commonly returns a JSON string nested inside a result/value.
    for _ in range(4):
        if isinstance(value, str):
            try:
                value = json.loads(value)
                continue
            except json.JSONDecodeError:
                break
        if isinstance(value, dict):
            nested = next(
                (
                    value[key]
                    for key in ("result", "value", "data")
                    if key in value and len(value) <= 4
                ),
                None,
            )
            if nested is not None:
                value = nested
                continue
        break
    return value


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("rows", "items", "notes", "list"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
    return []


def _field_rows(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for row in _rows(value):
        field = str(row.get("field") or "").strip().lower()
        if field:
            result[field] = row.get("value")
    return result


def _pseudonymous_author_id(author: str) -> str:
    return "xhs-author-" + hashlib.sha256(author.encode("utf-8")).hexdigest()[:20]


_NOTE_INSPECTION_SCRIPT = r"""
JSON.stringify((() => {
  const video = document.querySelector('video');
  if (!video) return {content_type: 'image', transcript: '', segments: []};
  const segments = [];
  for (const element of video.querySelectorAll('track[kind="captions"], track[kind="subtitles"]')) {
    const cues = element.track && element.track.cues ? Array.from(element.track.cues) : [];
    for (const cue of cues) {
      const text = String(cue.text || '').trim();
      if (text) segments.push({start_ms: Math.round(cue.startTime * 1000), end_ms: Math.round(cue.endTime * 1000), text});
    }
  }
  return {content_type: 'video', transcript: segments.map(item => item.text).join('\n'), segments};
})())
""".strip()


class OpenCLILocalCollectorProvider:
    """Read Xiaohongshu through the user's local, logged-in Chrome session.

    This provider never runs ``download``, ``doctor``, login, publish, like, or
    comment commands. It accepts only read-only OpenCLI adapters and browser DOM
    inspection. Passwords and cookies are never part of the provider contract.
    """

    provider_id = "opencli-local-v1"
    is_sandbox = False
    platform = "xiaohongshu"

    def __init__(
        self,
        *,
        command: str = "opencli",
        timeout_seconds: float = 60.0,
        runner: CommandRunner | None = None,
        inspect_single_pages: bool = True,
    ):
        self.command = command
        self.timeout_seconds = max(5.0, timeout_seconds)
        self.runner = runner or SubprocessCommandRunner()
        self.inspect_single_pages = inspect_single_pages

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider_id,
            "mode": "experimental_local_helper",
            "is_sandbox": False,
            "platforms": [self.platform],
            "kinds": ["single", "keyword", "creator_content"],
            "max_items": 30,
            "sample_inputs": {
                "single": "https://www.xiaohongshu.com/explore/笔记ID?xsec_token=签名",
                "keyword": "AI 工作流",
                "creator_content": "博主主页 URL 或用户 ID",
                "creator_profile": "当前本地 Provider 暂不采集博主隐私资料",
            },
            "actual_upstream": "opencli/xiaohongshu",
            "requires_local_browser": True,
            "external_calls": True,
            "video_transcript_required": True,
        }

    def collect(self, request: CollectionRequest) -> ProviderBatch:
        if request.platform != self.platform:
            raise ProviderError(
                "PLATFORM_NOT_ALLOWED",
                "OpenCLI 本地 Provider 当前只支持小红书",
                retryable=False,
            )
        started = time.monotonic()
        if request.kind == "single":
            content, errors = self._single(request)
        elif request.kind == "keyword":
            content, errors = self._keyword(request)
        elif request.kind == "creator_content":
            content, errors = self._creator_content(request)
        else:
            raise ProviderError(
                "COLLECTION_KIND_NOT_SUPPORTED",
                "OpenCLI 本地 Provider 暂不采集博主资料",
                retryable=False,
            )
        return ProviderBatch(
            request_id=f"opencli_{uuid4().hex[:20]}",
            provider=self.provider_id,
            content_items=content,
            item_errors=errors,
            elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
            rate_limit={"limit": 20, "remaining": max(0, 20 - len(content))},
            cost=0.0,
        )

    def _execute(
        self,
        arguments: list[str],
        *,
        format_json: bool = True,
        expect_json: bool = True,
    ) -> Any:
        command = [self.command, *arguments]
        if format_json:
            command.extend(["-f", "json"])
        result = self.runner.run(command, self.timeout_seconds)
        if result.returncode == 0:
            return _json_output(result.stdout) if expect_json else result.stdout
        code, message, retryable = {
            66: ("OPENCLI_EMPTY_RESULT", "页面没有返回可采集内容", False),
            69: ("OPENCLI_BRIDGE_UNAVAILABLE", "OpenCLI 浏览器桥未连接", True),
            75: ("OPENCLI_TIMEOUT", "OpenCLI 读取页面超时", True),
            77: ("OPENCLI_AUTH_REQUIRED", "请先在 Chrome 中登录小红书", False),
            78: ("OPENCLI_CONFIGURATION_INVALID", "OpenCLI 配置无效", False),
        }.get(
            result.returncode,
            ("OPENCLI_COMMAND_FAILED", "OpenCLI 读取小红书失败", True),
        )
        raise ProviderError(code, message, retryable=retryable)

    @staticmethod
    def _validated_note_url(value: Any) -> tuple[str, str]:
        url = str(value or "").strip()
        parsed = urlsplit(url)
        match = _NOTE_PATH.search(parsed.path)
        query = parse_qs(parsed.query)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in _XHS_HOSTS
            or match is None
            or not str(query.get("xsec_token", [""])[0]).strip()
        ):
            raise ProviderError(
                "XHS_SIGNED_URL_REQUIRED",
                "请使用搜索结果中的完整小红书链接（必须包含 xsec_token），不能使用裸笔记 ID",
                retryable=False,
            )
        return url, match.group(1).lower()

    def _single(
        self, request: CollectionRequest
    ) -> tuple[list[ProviderContent], list[ProviderItemError]]:
        url, note_id = self._validated_note_url(request.query.get("url"))
        fields = _field_rows(self._execute(["xiaohongshu", "note", url]))
        if not fields:
            raise ProviderError(
                "OPENCLI_OUTPUT_INVALID",
                "OpenCLI 笔记详情缺少 field/value 结构",
                retryable=False,
            )
        inspection: dict[str, Any] = {"content_type": "unknown", "transcript": "", "segments": []}
        if self.inspect_single_pages:
            inspection = self._inspect_page(url)
        content_type = str(inspection.get("content_type") or "unknown").lower()
        transcript = str(inspection.get("transcript") or "").strip()
        segments = inspection.get("segments") if isinstance(inspection.get("segments"), list) else []
        author = str(fields.get("author") or "小红书创作者").strip()
        tags = [item.strip() for item in str(fields.get("tags") or "").split(",") if item.strip()]
        item = ProviderContent(
            external_id=note_id,
            canonical_url=_unsigned_url(url),
            title=str(fields.get("title") or "无标题笔记").strip(),
            body=str(fields.get("content") or "").strip(),
            author_name=author,
            author_external_id=_pseudonymous_author_id(author),
            content_type="视频" if content_type == "video" else "图文" if content_type == "image" else "未知",
            published_at=_published_at(None, url),
            topics=tags,
            metrics={
                "likes": _count(fields.get("likes")),
                "collects": _count(fields.get("collects")),
                "comments": _count(fields.get("comments")),
            },
            actual_upstream="opencli/xiaohongshu",
            video_transcript=transcript or None,
            video_transcript_status=(
                "complete"
                if transcript
                else "required"
                if content_type == "video"
                else "not_applicable"
            ),
            video_transcript_source="page_text_track" if transcript else None,
            video_transcript_segments=[
                {
                    "start_ms": int(segment.get("start_ms") or 0),
                    "end_ms": int(segment.get("end_ms") or 0),
                    "text": str(segment.get("text") or "").strip(),
                }
                for segment in segments
                if isinstance(segment, dict) and str(segment.get("text") or "").strip()
            ],
        )
        return [item], []

    def _inspect_page(self, url: str) -> dict[str, Any]:
        session = f"riffloom-xhs-{uuid4().hex[:12]}"
        try:
            self._execute(
                ["browser", session, "open", url, "--window", "background"],
                format_json=False,
            )
            value = self._execute(
                ["browser", session, "eval", _NOTE_INSPECTION_SCRIPT],
                format_json=False,
            )
            if not isinstance(value, dict) or value.get("content_type") not in {"image", "video"}:
                raise ProviderError(
                    "OPENCLI_PAGE_INSPECTION_FAILED",
                    "无法可靠判断该笔记是图文还是视频，已拒绝以不完整字段入库",
                    retryable=True,
                )
            return value
        finally:
            try:
                self._execute(
                    ["browser", session, "close"],
                    format_json=False,
                    expect_json=False,
                )
            except ProviderError:
                pass

    def _keyword(
        self, request: CollectionRequest
    ) -> tuple[list[ProviderContent], list[ProviderItemError]]:
        keyword = str(request.query.get("keyword") or "").strip()
        arguments = ["xiaohongshu", "search", keyword, "--limit", str(request.limit)]
        sort = str(request.query.get("sort") or "").strip()
        publish_time = str(request.query.get("publish_time") or "").strip()
        if sort in {"comprehensive", "latest", "most-liked", "most-commented", "most-collected"}:
            arguments.extend(["--sort", sort])
        if publish_time in {"anytime", "day", "week", "half-year"}:
            arguments.extend(["--publish-time", publish_time])
        return self._light_items(_rows(self._execute(arguments)), request, keyword=keyword), []

    def _creator_content(
        self, request: CollectionRequest
    ) -> tuple[list[ProviderContent], list[ProviderItemError]]:
        creator = str(request.query.get("creator") or "").strip()
        if not creator:
            raise ProviderError("CREATOR_REQUIRED", "博主主页或用户 ID 不能为空", retryable=False)
        rows = _rows(
            self._execute(
                ["xiaohongshu", "user", creator, "--limit", str(request.limit)]
            )
        )
        return self._light_items(rows, request, creator=creator), []

    def _light_items(
        self,
        rows: list[dict[str, Any]],
        request: CollectionRequest,
        *,
        keyword: str = "",
        creator: str = "",
    ) -> list[ProviderContent]:
        allowed = set(request.retry_keys)
        items: list[ProviderContent] = []
        for row in rows[: request.limit]:
            url = str(row.get("url") or "").strip()
            match = _NOTE_PATH.search(urlsplit(url).path)
            note_id = str(row.get("id") or (match.group(1) if match else "")).strip()
            if not note_id or (allowed and note_id not in allowed):
                continue
            author = str(row.get("author") or creator or "小红书创作者").strip()
            content_type = str(row.get("type") or "未知").strip()
            topics = [keyword] if keyword else []
            items.append(
                ProviderContent(
                    external_id=note_id,
                    canonical_url=url,
                    title=str(row.get("title") or "无标题笔记").strip(),
                    # Search/user results are intentionally lightweight. Full
                    # text and video transcript require a separate single fetch.
                    body="",
                    author_name=author,
                    author_external_id=_pseudonymous_author_id(author),
                    content_type=content_type,
                    published_at=_published_at(row.get("published_at"), url),
                    topics=topics,
                    metrics={"likes": _count(row.get("likes"))},
                    actual_upstream="opencli/xiaohongshu",
                    video_transcript_status="not_collected",
                )
            )
        return items
