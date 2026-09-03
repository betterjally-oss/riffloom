from __future__ import annotations

import re
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from .base import (
    CollectionRequest,
    ProviderBatch,
    ProviderBlogger,
    ProviderContent,
    ProviderError,
    ProviderItemError,
)


class SandboxCollectorProvider:
    provider_id = "sandbox-v1"
    is_sandbox = True
    platform = "riffloom-sandbox"
    sample_host = "sandbox.riffloom.local"

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider_id,
            "mode": "compliance_sandbox",
            "is_sandbox": True,
            "platforms": [self.platform, "xiaohongshu"],
            "kinds": ["single", "keyword", "creator_content", "creator_profile"],
            "max_items": 30,
            "sample_inputs": {
                "single": "https://sandbox.riffloom.local/notes/note-001",
                "keyword": "AI 工作流",
                "creator_content": "creator-001",
                "creator_profile": "creator-001",
            },
        }

    def collect(self, request: CollectionRequest) -> ProviderBatch:
        started = time.monotonic()
        raw_input = " ".join(str(value) for value in request.query.values())
        if "[rate-limit]" in raw_input:
            raise ProviderError(
                "PROVIDER_RATE_LIMITED",
                "合规沙箱已触发限流场景，请稍后重试",
                retryable=True,
                status_code=429,
            )
        if "[timeout]" in raw_input:
            raise ProviderError(
                "PROVIDER_TIMEOUT",
                "合规沙箱已触发超时场景",
                retryable=True,
                status_code=504,
            )

        if request.kind == "single":
            batch = self._single(request)
        elif request.kind == "keyword":
            batch = self._keyword(request)
        elif request.kind == "creator_content":
            batch = self._creator_content(request)
        else:
            batch = self._creator_profile(request)

        return ProviderBatch(
            request_id=batch.request_id,
            provider=batch.provider,
            content_items=batch.content_items,
            blogger_items=batch.blogger_items,
            item_errors=batch.item_errors,
            elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
            rate_limit={"remaining": 999, "limit": 1000},
            cost=0.0,
        )

    def search_trends(self, keyword: str, *, window_days: int = 7, limit: int = 5) -> ProviderBatch:
        count = min(30, max(15, limit * 2))
        now = datetime.now(timezone.utc)
        return self._batch(
            content=[
                replace(
                    self._content(f"trend-{index:03d}", keyword=keyword),
                    published_at=now
                    - timedelta(seconds=index * window_days * 86_400 // (count + 1)),
                    actual_upstream="sandbox/sandbox-search",
                )
                for index in range(1, count + 1)
            ]
        )

    def _single(self, request: CollectionRequest) -> ProviderBatch:
        url = str(request.query.get("url", ""))
        parts = urlsplit(url)
        if (
            parts.scheme in {"http", "https"}
            and request.platform == "xiaohongshu"
            and parts.hostname is not None
            and (parts.hostname == "xiaohongshu.com" or parts.hostname.endswith(".xiaohongshu.com"))
        ):
            external_id = parts.path.rstrip("/").split("/")[-1]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", external_id):
                raise ProviderError("SOURCE_NOT_FOUND", "小红书链接缺少内容标识", retryable=False)
            canonical_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            return self._batch(
                content=[
                    ProviderContent(
                        external_id=external_id,
                        canonical_url=canonical_url,
                        title="待上传媒体的小红书参考视频",
                        body="未自动读取平台页面；请上传已获权媒体补齐视频文案。",
                        author_name="用户提供",
                        author_external_id=None,
                        content_type="视频",
                        published_at=None,
                        topics=["用户上传", "待转写"],
                        video_transcript_status="required",
                    )
                ],
                provider="user-upload-v1",
            )
        if parts.scheme not in {"http", "https"} or parts.hostname != self.sample_host:
            raise ProviderError(
                "SOURCE_NOT_AUTHORIZED",
                "当前入口只接受沙箱示例或小红书获权媒体链接",
                retryable=False,
            )
        match = re.search(r"/(?:notes)/([a-z0-9-]+)$", parts.path.rstrip("/"))
        if not match:
            raise ProviderError("SOURCE_NOT_FOUND", "未找到该沙箱内容", retryable=False)
        external_id = match.group(1)
        item = self._content(external_id)
        return self._batch(content=[item])

    def _keyword(self, request: CollectionRequest) -> ProviderBatch:
        keyword = str(request.query.get("keyword", "")).strip()
        content_type = str(request.query.get("content_type") or "image")
        items = [
            self._content(f"note-{index:03d}", keyword=keyword, content_type=content_type)
            for index in range(1, request.limit + 1)
        ]
        errors: list[ProviderItemError] = []
        if "[partial]" in keyword and request.attempt_no == 1 and items:
            failed = items.pop()
            errors.append(
                ProviderItemError(
                    source_key=failed.external_id,
                    code="PROVIDER_ITEM_TEMPORARY",
                    message="沙箱逐项临时失败，可单独重试",
                    retryable=True,
                )
            )
        items = self._filter_retry_content(items, request)
        if request.retry_keys and not items:
            items = [
                self._content(
                    key,
                    keyword=keyword.replace("[partial]", "").strip(),
                    content_type=content_type,
                )
                for key in request.retry_keys
            ]
            errors = []
        return self._batch(content=items, errors=errors)

    def _creator_content(self, request: CollectionRequest) -> ProviderBatch:
        creator_id = self._creator_id(request)
        items = [
            self._content(f"{creator_id}-note-{index:03d}", creator_id=creator_id)
            for index in range(1, request.limit + 1)
        ]
        return self._batch(content=self._filter_retry_content(items, request))

    def _creator_profile(self, request: CollectionRequest) -> ProviderBatch:
        creator_id = self._creator_id(request)
        if request.retry_keys and creator_id not in request.retry_keys:
            return self._batch()
        index = self._numeric_suffix(creator_id)
        blogger = ProviderBlogger(
            external_id=creator_id,
            profile_url=f"https://{self.sample_host}/creators/{creator_id}",
            name=f"灵感创作者 {index:02d}",
            avatar_url=f"https://{self.sample_host}/media/{creator_id}.jpg",
            bio="获权脱敏的合规沙箱博主资料，仅用于验证字段、去重和任务恢复。",
            followers=12_000 + index * 137,
            likes_and_collects=86_000 + index * 311,
            tags=["合规沙箱", "内容方法"],
        )
        return self._batch(bloggers=[blogger])

    def _content(
        self,
        external_id: str,
        *,
        keyword: str = "AI 工作流",
        creator_id: str | None = None,
        content_type: str = "image",
    ) -> ProviderContent:
        index = self._numeric_suffix(external_id)
        owner = creator_id or f"creator-{((index - 1) % 20) + 1:03d}"
        clean_keyword = keyword.replace("[partial]", "").strip() or "AI 工作流"
        return ProviderContent(
            external_id=external_id,
            canonical_url=f"https://{self.sample_host}/notes/{external_id}",
            title=f"{clean_keyword}：可复用内容样本 {index:02d}",
            body=(
                "这是经过授权脱敏的 Riffloom 合规沙箱内容。"
                "它用于验证四类采集、字段规范化、来源追溯、去重与失败重试，"
                "不代表任何真实平台的实时内容。"
            ),
            author_name=f"灵感创作者 {self._numeric_suffix(owner):02d}",
            author_external_id=owner,
            content_type="视频" if content_type == "video" else "图文",
            published_at=datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(days=index - 1),
            cover_url=f"https://{self.sample_host}/media/{external_id}-cover.jpg",
            media_refs=[
                {
                    "type": "image",
                    "url": f"https://{self.sample_host}/media/{external_id}-01.jpg",
                }
            ],
            topics=[clean_keyword, "合规沙箱"],
            metrics={
                "likes": 1_000 + index * 17,
                "collects": 500 + index * 11,
                "comments": 60 + index,
                "shares": 20 + index,
                "views": 20_000 + index * 101,
            },
            video_transcript_status=(
                "not_collected" if content_type == "video" else "not_applicable"
            ),
        )

    @staticmethod
    def _numeric_suffix(value: str) -> int:
        match = re.search(r"(\d+)$", value)
        return int(match.group(1)) if match else 1

    def _creator_id(self, request: CollectionRequest) -> str:
        raw = str(request.query.get("creator", "")).strip()
        if raw.startswith(("http://", "https://")):
            raw = urlsplit(raw).path.rstrip("/").split("/")[-1]
        if not re.fullmatch(r"creator-\d{3}", raw):
            raise ProviderError(
                "SOURCE_NOT_FOUND",
                "沙箱博主标识格式应为 creator-001",
                retryable=False,
            )
        return raw

    @staticmethod
    def _filter_retry_content(
        items: list[ProviderContent], request: CollectionRequest
    ) -> list[ProviderContent]:
        if not request.retry_keys:
            return items
        allowed = set(request.retry_keys)
        return [item for item in items if item.external_id in allowed]

    def _batch(
        self,
        *,
        content: list[ProviderContent] | None = None,
        bloggers: list[ProviderBlogger] | None = None,
        errors: list[ProviderItemError] | None = None,
        provider: str | None = None,
    ) -> ProviderBatch:
        return ProviderBatch(
            request_id=f"sandbox_{uuid4().hex[:16]}",
            provider=provider or self.provider_id,
            content_items=content or [],
            blogger_items=bloggers or [],
            item_errors=errors or [],
        )
