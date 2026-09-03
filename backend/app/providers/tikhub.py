from __future__ import annotations

from html import unescape
import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .base import (
    CollectionRequest,
    ProviderBatch,
    ProviderBlogger,
    ProviderContent,
    ProviderError,
    ProviderItemError,
    ProviderRequestTrace,
)

_BASE_URL = "https://api.tikhub.io"
_PROFILE_ID = re.compile(r"/user/profile/([0-9a-f]{24})(?:/|$)", re.I)
_USER_ID = re.compile(r"[0-9a-f]{24}", re.I)
_SORT_TYPES = {
    "comprehensive": "general",
    "latest": "time_descending",
    "most-liked": "popularity_descending",
    "most-commented": "comment_descending",
    "most-collected": "collect_descending",
}
_TIME_FILTERS = {
    "anytime": "不限",
    "day": "一天内",
    "week": "一周内",
    "half-year": "半年内",
    "month": "半年内",
}
_NOTE_TYPES = {"all": "不限", "image": "普通笔记", "video": "视频笔记"}


class TikHubCollectorProvider:
    """Collect public Xiaohongshu search and creator-list metadata via TikHub."""

    provider_id = "tikhub-v1"
    is_sandbox = False

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 60,
        client: httpx.Client | None = None,
    ):
        if not api_key:
            raise RuntimeError("tikhub-v1 必须配置 TIKHUB_API_KEY")
        self.api_key = api_key
        self.client = client or httpx.Client(
            base_url=_BASE_URL,
            timeout=timeout_seconds,
            follow_redirects=False,
        )

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider_id,
            "mode": "production_third_party",
            "is_sandbox": False,
            "platforms": ["xiaohongshu"],
            "kinds": ["keyword", "creator_content", "creator_profile"],
            "max_items": 30,
            "sample_inputs": {
                "single": "",
                "keyword": "AI 工具",
                "creator_content": "",
                "creator_profile": "",
            },
            "actual_upstream": "tikhub/xiaohongshu-app-v2",
            "requires_local_browser": False,
            "external_calls": True,
            "video_transcript_required": False,
        }

    def collect(self, request: CollectionRequest) -> ProviderBatch:
        if request.platform != "xiaohongshu":
            raise ProviderError(
                "PLATFORM_NOT_ALLOWED",
                "TikHub Provider 当前只支持小红书",
                retryable=False,
            )
        started = time.monotonic()
        detail_traces: list[ProviderRequestTrace] = []
        detail_errors: list[ProviderItemError] = []
        if request.kind == "keyword":
            notes, request_ids = self._keyword(request)
            list_operation = "keyword.list"
        elif request.kind == "creator_content":
            notes, request_ids = self._creator_content(request)
            list_operation = "creator_content.list"
            notes, detail_errors, detail_traces = self._hydrate_creator_notes(
                notes, request
            )
        elif request.kind == "creator_profile":
            blogger, request_id = self._creator_profile(request)
            return ProviderBatch(
                request_id=request_id,
                provider=self.provider_id,
                blogger_items=[blogger],
                elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
                rate_limit={"limit": 10},
                cost=0.01,
            )
        else:
            raise ProviderError(
                "COLLECTION_KIND_NOT_SUPPORTED",
                "TikHub Provider 当前只支持关键词、博主近期内容和博主信息采集",
                retryable=False,
            )

        request_id = request_ids[0]
        list_traces = tuple(
            ProviderRequestTrace(
                operation=list_operation,
                request_id=current_request_id,
                status="success",
                cost=0.01,
            )
            for current_request_id in request_ids
        )
        items, errors = self._normalize(notes, request)
        return ProviderBatch(
            request_id=request_id,
            provider=self.provider_id,
            request_traces=(*list_traces, *detail_traces),
            content_items=items,
            item_errors=[*detail_errors, *errors],
            elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
            rate_limit={"limit": 10},
            cost=round(sum(trace.cost for trace in (*list_traces, *detail_traces)), 2),
        )

    def search_trends(
        self, keyword: str, *, window_days: int = 7, limit: int = 5
    ) -> ProviderBatch:
        keyword = keyword.strip()
        if not keyword:
            raise ProviderError(
                "KEYWORD_REQUIRED", "热点搜索关键词不能为空", retryable=False
            )
        started = time.monotonic()
        now = datetime.now(timezone.utc)
        specs = [
            (
                "trend.xiaohongshu",
                "xiaohongshu",
                "get",
                "/api/v1/xiaohongshu/app_v2/search_notes",
                {
                    "keyword": keyword,
                    "page": 1,
                    "sort_type": "popularity_descending",
                    "note_type": "不限",
                    "time_filter": (
                        "一天内"
                        if window_days <= 1
                        else "一周内" if window_days <= 7 else "半年内"
                    ),
                },
            ),
        ]
        items: list[ProviderContent] = []
        errors: list[ProviderItemError] = []
        traces: list[ProviderRequestTrace] = []
        first_error: ProviderError | None = None
        for operation, platform, method, path, payload in specs:
            try:
                data, request_id = (
                    self._post(path, payload)
                    if method == "post"
                    else self._get(path, payload)
                )
            except ProviderError as exc:
                first_error = first_error or exc
                traces.append(
                    ProviderRequestTrace(
                        operation=operation,
                        request_id=exc.provider_request_id or "",
                        status="failed",
                        cost=0.01,
                        http_status=exc.status_code,
                        error_type=exc.code,
                    )
                )
                errors.append(
                    ProviderItemError(
                        source_key=platform,
                        code=exc.code,
                        message=f"{platform} 热点搜索失败",
                        retryable=exc.retryable,
                    )
                )
                continue
            traces.append(
                ProviderRequestTrace(
                    operation=operation,
                    request_id=request_id,
                    status="success",
                    cost=0.01,
                )
            )
            items.extend(self._trend_contents(platform, data, keyword))

        cutoff = now - timedelta(days=window_days)
        unique: dict[tuple[str, str], ProviderContent] = {}
        for item in items:
            if item.published_at is not None and item.published_at < cutoff:
                continue
            platform = (item.actual_upstream or "unknown").split("/")[-1]
            unique[(platform, item.external_id)] = item
        ranked = sorted(unique.values(), key=self._trend_score, reverse=True)
        if not ranked:
            raise first_error or ProviderError(
                "PROVIDER_UPSTREAM_FAILED",
                "TikHub 未返回可用的近期开源热点",
                retryable=True,
            )
        selected = ranked[: min(30, max(15, limit * 2))]
        return ProviderBatch(
            request_id=next(
                (trace.request_id for trace in traces if trace.request_id), ""
            ),
            provider=self.provider_id,
            request_traces=tuple(traces),
            content_items=selected,
            item_errors=errors,
            elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
            rate_limit={"limit": 10},
            cost=round(sum(trace.cost for trace in traces), 2),
        )

    @classmethod
    def _trend_contents(
        cls, platform: str, data: dict[str, Any], keyword: str
    ) -> list[ProviderContent]:
        id_fields = {
            "xiaohongshu": ("id", "note_id"),
            "douyin": ("aweme_id", "id"),
            "bilibili": ("bvid", "aid", "id"),
            "weibo": ("mid", "mblogid", "id"),
        }[platform]
        title_fields = ("title", "display_title", "desc", "text", "raw_text", "name")
        rows: list[dict[str, Any]] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                if any(value.get(field) for field in id_fields) and any(
                    value.get(field) for field in title_fields
                ):
                    rows.append(value)
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(data)
        results: list[ProviderContent] = []
        seen: set[str] = set()
        for row in rows:
            source_id = next(
                (
                    str(row.get(field) or "").strip()
                    for field in id_fields
                    if row.get(field)
                ),
                "",
            )
            if not source_id or source_id in seen:
                continue
            seen.add(source_id)
            title = cls._clean_text(
                next((row.get(field) for field in title_fields if row.get(field)), "")
            )
            if not title or not any(character.isalnum() for character in title):
                continue
            body = cls._clean_text(
                row.get("desc") or row.get("text") or row.get("raw_text") or title
            )
            author_value = row.get("author") or row.get("user") or row.get("owner")
            author = author_value if isinstance(author_value, dict) else {}
            stats_value = (
                row.get("statistics") or row.get("stat") or row.get("interact_info")
            )
            stats = stats_value if isinstance(stats_value, dict) else {}
            metrics = {
                "views": cls._metric(
                    row, stats, "play_count", "view_count", "play", "views"
                ),
                "likes": cls._metric(
                    row, stats, "digg_count", "liked_count", "like", "attitudes_count"
                ),
                "collects": cls._metric(
                    row, stats, "collect_count", "collected_count", "favorite"
                ),
                "comments": cls._metric(
                    row, stats, "comment_count", "comments_count", "review"
                ),
                "shares": cls._metric(
                    row, stats, "share_count", "shared_count", "reposts_count"
                ),
            }
            results.append(
                ProviderContent(
                    external_id=source_id,
                    canonical_url=cls._trend_url(platform, row, source_id),
                    title=title[:300],
                    body=body[:1200],
                    author_name=cls._clean_text(
                        author.get("nickname")
                        or author.get("name")
                        or row.get("author_name")
                        or platform
                    ),
                    author_external_id=str(
                        author.get("uid")
                        or author.get("user_id")
                        or author.get("mid")
                        or ""
                    ).strip()
                    or None,
                    content_type="热点信号",
                    published_at=cls._timestamp(
                        row.get("timestamp")
                        or row.get("create_time")
                        or row.get("pubdate")
                        or row.get("publish_time")
                    ),
                    topics=[keyword, platform],
                    metrics=metrics,
                    actual_upstream=f"tikhub/{platform}-search",
                )
            )
        return results

    @staticmethod
    def _clean_text(value: Any) -> str:
        return re.sub(
            r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(str(value or "")))
        ).strip()

    @classmethod
    def _metric(cls, row: dict[str, Any], stats: dict[str, Any], *keys: str) -> int:
        value = next(
            (
                source.get(key)
                for source in (stats, row)
                for key in keys
                if source.get(key) is not None
            ),
            0,
        )
        text = str(value).replace(",", "").strip()
        multiplier = (
            100_000_000 if text.endswith("亿") else 10_000 if text.endswith("万") else 1
        )
        try:
            return max(0, int(float(text.rstrip("万亿")) * multiplier))
        except ValueError:
            return 0

    @staticmethod
    def _trend_url(platform: str, row: dict[str, Any], source_id: str) -> str:
        for key in ("share_url", "arcurl", "url"):
            value = str(row.get(key) or "").strip()
            parts = urlsplit(value)
            if parts.scheme in {"http", "https"} and parts.hostname:
                return urlunsplit(("https", parts.netloc, parts.path, "", ""))
        return {
            "xiaohongshu": f"https://www.xiaohongshu.com/explore/{source_id}",
            "douyin": f"https://www.douyin.com/video/{source_id}",
            "bilibili": f"https://www.bilibili.com/video/{source_id}",
            "weibo": f"https://m.weibo.cn/detail/{source_id}",
        }[platform]

    @staticmethod
    def _trend_score(item: ProviderContent) -> float:
        # ponytail: cross-platform logarithmic heuristic; replace only after real ranking data exists.
        weights = {"views": 1, "likes": 2, "collects": 2.5, "comments": 3, "shares": 4}
        return sum(
            math.log1p(item.metrics.get(key, 0)) * weight
            for key, weight in weights.items()
        )

    def _keyword(
        self, request: CollectionRequest
    ) -> tuple[list[dict[str, Any]], list[str]]:
        keyword = str(request.query.get("keyword") or "").strip()
        if not keyword:
            raise ProviderError("KEYWORD_REQUIRED", "关键词不能为空", retryable=False)
        params = {
            "keyword": keyword,
            "page": 1,
            "sort_type": _SORT_TYPES.get(
                str(request.query.get("sort") or ""), "general"
            ),
            "note_type": _NOTE_TYPES.get(
                str(request.query.get("content_type") or ""), "不限"
            ),
            "time_filter": _TIME_FILTERS.get(
                str(request.query.get("publish_time") or ""), "不限"
            ),
        }
        data, request_id = self._get("/api/v1/xiaohongshu/app_v2/search_notes", params)
        rows = data.get("items") if isinstance(data, dict) else None
        notes = [row.get("note") for row in rows or [] if isinstance(row, dict)]
        result = [note for note in notes if isinstance(note, dict)]
        request_ids = [request_id]
        search_id = str(data.get("search_id") or "").strip()
        search_session_id = str(data.get("search_session_id") or "").strip()
        if (
            request.limit > len(result)
            and data.get("has_more") is not False
            and search_id
            and search_session_id
        ):
            next_data, next_request_id = self._get(
                "/api/v1/xiaohongshu/app_v2/search_notes",
                {
                    **params,
                    "page": 2,
                    "search_id": search_id,
                    "search_session_id": search_session_id,
                },
            )
            next_rows = next_data.get("items") if isinstance(next_data, dict) else None
            result.extend(
                row["note"]
                for row in next_rows or []
                if isinstance(row, dict) and isinstance(row.get("note"), dict)
            )
            request_ids.append(next_request_id)
        return result, request_ids

    def _creator_content(
        self, request: CollectionRequest
    ) -> tuple[list[dict[str, Any]], list[str]]:
        params = self._creator_params(request.query.get("creator"))
        params["cursor"] = ""
        data, request_id = self._get(
            "/api/v1/xiaohongshu/app_v2/get_user_posted_notes", params
        )
        notes = data.get("notes") if isinstance(data, dict) else None
        result = [note for note in notes or [] if isinstance(note, dict)]
        request_ids = [request_id]
        cursor = str(
            (result[-1].get("cursor") or result[-1].get("id")) if result else ""
        ).strip()
        if request.limit > len(result) and data.get("has_more") is not False and cursor:
            next_data, next_request_id = self._get(
                "/api/v1/xiaohongshu/app_v2/get_user_posted_notes",
                {**params, "cursor": cursor},
            )
            next_notes = next_data.get("notes") if isinstance(next_data, dict) else None
            result.extend(note for note in next_notes or [] if isinstance(note, dict))
            request_ids.append(next_request_id)
        return result, request_ids

    def _creator_profile(
        self, request: CollectionRequest
    ) -> tuple[ProviderBlogger, str]:
        data, request_id = self._get(
            "/api/v1/xiaohongshu/app_v2/get_user_info",
            self._creator_params(request.query.get("creator")),
        )
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        user_id = str(data.get("userid") or "").strip()
        if (
            data.get("success") is not True
            or result.get("success") is not True
            or not user_id
        ):
            raise ProviderError(
                "SOURCE_NOT_FOUND",
                "TikHub 未返回可用的博主资料，请检查主页链接或用户 ID",
                retryable=False,
            )
        tags = [
            value
            for value in (
                str(data.get("red_official_verify_content") or "").strip(),
                str(data.get("location") or "").strip(),
            )
            if value
        ]
        return (
            ProviderBlogger(
                external_id=user_id,
                profile_url=f"https://www.xiaohongshu.com/user/profile/{user_id}",
                name=str(data.get("nickname") or "小红书博主").strip(),
                avatar_url=self._media_url(data.get("imageb") or data.get("images")),
                bio=str(data.get("desc") or "").strip(),
                followers=self._integer(data.get("fans")),
                likes_and_collects=self._integer(data.get("liked"))
                + self._integer(data.get("collected")),
                tags=tags,
            ),
            request_id,
        )

    def _hydrate_creator_notes(
        self, notes: list[dict[str, Any]], request: CollectionRequest
    ) -> tuple[
        list[dict[str, Any]], list[ProviderItemError], list[ProviderRequestTrace]
    ]:
        allowed = set(request.retry_keys)
        hydrated: list[dict[str, Any]] = []
        errors: list[ProviderItemError] = []
        traces: list[ProviderRequestTrace] = []
        selected = 0
        for note in notes:
            note_id = str(note.get("id") or "").strip()
            if (
                not note_id
                or (allowed and note_id not in allowed)
                or selected >= request.limit
            ):
                hydrated.append(note)
                continue
            selected += 1
            try:
                detail, detail_request_id = self._get(
                    "/api/v1/xiaohongshu/app_v2/get_image_note_detail",
                    {"note_id": note_id},
                )
            except ProviderError as exc:
                traces.append(
                    ProviderRequestTrace(
                        operation="creator_content.image_detail",
                        request_id=exc.provider_request_id or "",
                        status="failed",
                        cost=0.01,
                        http_status=exc.status_code,
                        error_type=exc.code,
                    )
                )
                errors.append(
                    ProviderItemError(
                        source_key=note_id,
                        code=exc.code,
                        message="TikHub 笔记详情补齐失败",
                        retryable=exc.retryable,
                    )
                )
                hydrated.append(note)
                continue
            merged = {**note, **self._detail_note(detail)}
            merged_content = self._content(merged, note_id, request)
            expected_media = (
                "video" if merged_content.content_type == "视频" else "image"
            )
            operation = "creator_content.image_detail"
            if expected_media == "video":
                traces.append(
                    ProviderRequestTrace(
                        operation=operation,
                        request_id=detail_request_id,
                        status="success",
                        cost=0.01,
                    )
                )
                operation = "creator_content.video_detail"
                try:
                    video_detail, detail_request_id = self._get(
                        "/api/v1/xiaohongshu/app_v2/get_video_note_detail",
                        {"note_id": note_id},
                    )
                except ProviderError as exc:
                    traces.append(
                        ProviderRequestTrace(
                            operation=operation,
                            request_id=exc.provider_request_id or "",
                            status="failed",
                            cost=0.01,
                            http_status=exc.status_code,
                            error_type=exc.code,
                        )
                    )
                    errors.append(
                        ProviderItemError(
                            source_key=note_id,
                            code=exc.code,
                            message="TikHub 视频详情补齐失败",
                            retryable=exc.retryable,
                        )
                    )
                    hydrated.append(merged)
                    continue
                merged = {**merged, **self._detail_note(video_detail)}
                merged_content = self._content(merged, note_id, request)
            detail_valid = self._has_expected_media(merged_content, expected_media)
            traces.append(
                ProviderRequestTrace(
                    operation=operation,
                    request_id=detail_request_id,
                    status="success" if detail_valid else "failed",
                    cost=0.01,
                    http_status=None if detail_valid else 200,
                    error_type=None if detail_valid else "PROVIDER_RESPONSE_INVALID",
                )
            )
            if not detail_valid:
                errors.append(
                    ProviderItemError(
                        source_key=note_id,
                        code="PROVIDER_RESPONSE_INVALID",
                        message="TikHub 笔记详情未返回可用媒体",
                        retryable=True,
                    )
                )
            hydrated.append(merged)
        return hydrated, errors, traces

    @staticmethod
    def _has_expected_media(content: ProviderContent, expected: str) -> bool:
        return any(media.get("type") == expected for media in content.media_refs)

    @staticmethod
    def _detail_note(data: dict[str, Any]) -> dict[str, Any]:
        current = data
        for _ in range(4):
            nested = next(
                (
                    current.get(key)
                    for key in (
                        "note",
                        "note_card",
                        "note_info",
                        "item",
                        "result",
                        "data",
                    )
                    if isinstance(current.get(key), dict)
                ),
                None,
            )
            if nested is None:
                break
            current = nested
        return current

    @staticmethod
    def _creator_params(value: Any) -> dict[str, str]:
        creator = str(value or "").strip()
        if _USER_ID.fullmatch(creator):
            return {"user_id": creator.lower()}
        parts = urlsplit(creator)
        host = (parts.hostname or "").lower()
        allowed = host in {
            "xiaohongshu.com",
            "www.xiaohongshu.com",
            "xhslink.com",
            "www.xhslink.com",
            "xhslink.cn",
            "www.xhslink.cn",
        }
        if parts.scheme != "https" or not allowed or parts.username or parts.password:
            raise ProviderError(
                "CREATOR_REQUIRED",
                "请填写小红书博主主页 HTTPS 链接或 24 位用户 ID",
                retryable=False,
            )
        match = _PROFILE_ID.search(parts.path)
        return {"user_id": match.group(1).lower()} if match else {"share_text": creator}

    def _get(self, path: str, params: dict[str, Any]) -> tuple[dict[str, Any], str]:
        return self._request("GET", path, params=params)

    def _post(self, path: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        return self._request("POST", path, json=payload)

    def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> tuple[dict[str, Any], str]:
        try:
            response = self.client.request(
                method,
                path,
                headers={"Authorization": f"Bearer {self.api_key}"},
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "PROVIDER_TIMEOUT", "TikHub 请求超时", retryable=True, status_code=504
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "PROVIDER_REQUEST_FAILED", "TikHub 请求失败", retryable=True
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = None
        provider_request_id = str(
            response.headers.get("x-request-id")
            or (payload.get("request_id") if isinstance(payload, dict) else "")
            or ""
        ).strip()[:160]
        if response.status_code == 402:
            raise ProviderError(
                "TIKHUB_BALANCE_REQUIRED",
                "TikHub 余额不足",
                retryable=False,
                status_code=402,
                provider_request_id=provider_request_id or None,
            )
        if response.status_code in {401, 403}:
            raise ProviderError(
                "TIKHUB_AUTH_FAILED",
                "TikHub API 密钥无效或权限不足",
                retryable=False,
                status_code=response.status_code,
                provider_request_id=provider_request_id or None,
            )
        if response.status_code == 429:
            raise ProviderError(
                "PROVIDER_RATE_LIMITED",
                "TikHub 请求过于频繁，请稍后重试",
                retryable=True,
                status_code=429,
                provider_request_id=provider_request_id or None,
            )
        if response.status_code >= 400:
            raise ProviderError(
                "PROVIDER_REQUEST_FAILED",
                "TikHub 请求失败",
                retryable=response.status_code >= 500,
                status_code=response.status_code,
                provider_request_id=provider_request_id or None,
            )
        if payload is None:
            raise ProviderError(
                "PROVIDER_RESPONSE_INVALID",
                "TikHub 返回了无效 JSON",
                retryable=True,
                provider_request_id=provider_request_id or None,
            )
        envelope = payload.get("data") if isinstance(payload, dict) else None
        data = envelope.get("data") if isinstance(envelope, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("code") != 200
            or not isinstance(envelope, dict)
            or envelope.get("code") != 0
            or envelope.get("success") is not True
            or not isinstance(data, dict)
        ):
            raise ProviderError(
                "PROVIDER_UPSTREAM_FAILED",
                "TikHub 上游未返回可用数据",
                retryable=True,
                provider_request_id=provider_request_id or None,
            )
        return data, provider_request_id

    def _normalize(
        self, notes: list[dict[str, Any]], request: CollectionRequest
    ) -> tuple[list[ProviderContent], list[ProviderItemError]]:
        allowed = set(request.retry_keys)
        month_cutoff = (
            datetime.now(timezone.utc) - timedelta(days=30)
            if request.kind == "keyword"
            and request.query.get("publish_time") == "month"
            else None
        )
        items: list[ProviderContent] = []
        errors: list[ProviderItemError] = []
        for index, note in enumerate(notes):
            note_id = str(note.get("id") or "").strip()
            if allowed and note_id not in allowed:
                continue
            if not note_id:
                errors.append(
                    ProviderItemError(
                        source_key=f"item-{index + 1}",
                        code="SOURCE_PARSE_FAILED",
                        message="TikHub 结果缺少笔记 ID",
                        retryable=False,
                    )
                )
                continue
            content = self._content(note, note_id, request)
            if month_cutoff and (
                content.published_at is None or content.published_at < month_cutoff
            ):
                continue
            items.append(content)
            if len(items) >= request.limit:
                break
        return items, errors

    @classmethod
    def _content(
        cls, note: dict[str, Any], note_id: str, request: CollectionRequest
    ) -> ProviderContent:
        user_value = note.get("user") or note.get("user_info")
        user = user_value if isinstance(user_value, dict) else {}
        body = str(note.get("desc") or "").strip()
        raw_type = str(note.get("type") or note.get("note_type") or "").lower()
        is_video = raw_type == "video"
        images_value = (
            note.get("images_list") or note.get("image_list") or note.get("images")
        )
        images = images_value if isinstance(images_value, list) else []
        image_urls = [
            url for image in images for url in [cls._media_candidate(image)] if url
        ]
        cover_url = cls._media_candidate(note.get("cover") or note.get("cover_url"))
        video_url = cls._media_candidate(note.get("video_info") or note.get("video"))
        media_refs = [
            *([{"type": "cover", "url": cover_url}] if cover_url else []),
            *[{"type": "image", "url": url} for url in image_urls if url != cover_url],
            *([{"type": "video", "url": video_url}] if video_url else []),
        ]
        topics = list(dict.fromkeys(re.findall(r"#([^#\s]+)", body)))
        topic_values = note.get("tag_list") or note.get("topics") or []
        if isinstance(topic_values, list):
            topics.extend(
                str(
                    topic.get("name")
                    or topic.get("title")
                    or topic.get("tag_name")
                    or ""
                    if isinstance(topic, dict)
                    else topic
                ).strip()
                for topic in topic_values
            )
        if request.kind == "keyword":
            keyword = str(request.query.get("keyword") or "").strip()
            if keyword:
                topics.insert(0, keyword)
        interaction = (
            note.get("interact_info")
            if isinstance(note.get("interact_info"), dict)
            else {}
        )
        return ProviderContent(
            external_id=note_id,
            canonical_url=f"https://www.xiaohongshu.com/explore/{note_id}",
            title=str(
                note.get("title")
                or note.get("display_title")
                or body[:60]
                or "无标题笔记"
            ).strip(),
            body=body,
            author_name=str(user.get("nickname") or "小红书创作者").strip(),
            author_external_id=str(
                user.get("userid") or user.get("user_id") or ""
            ).strip()
            or None,
            content_type="视频" if is_video else "图文",
            published_at=cls._timestamp(
                note.get("timestamp")
                or note.get("create_time")
                or note.get("last_update_time")
            ),
            cover_url=cover_url or (image_urls[0] if image_urls else None),
            media_refs=media_refs,
            topics=list(dict.fromkeys(topic for topic in topics if topic)),
            metrics={
                "likes": cls._integer(
                    note.get("liked_count")
                    or note.get("nice_count")
                    or note.get("likes")
                    or interaction.get("liked_count")
                ),
                "collects": cls._integer(
                    note.get("collected_count") or interaction.get("collected_count")
                ),
                "comments": cls._integer(
                    note.get("comments_count")
                    or interaction.get("comment_count")
                    or interaction.get("comments_count")
                ),
                "shares": cls._integer(
                    note.get("shared_count")
                    or note.get("share_count")
                    or interaction.get("share_count")
                ),
                "views": cls._integer(
                    note.get("view_count") or interaction.get("view_count")
                ),
            },
            actual_upstream="tikhub/xiaohongshu-app-v2",
            video_transcript_status="not_collected" if is_video else "not_applicable",
        )

    @staticmethod
    def _media_url(value: Any) -> str | None:
        parts = urlsplit(str(value or "").strip())
        host = (parts.hostname or "").lower()
        if parts.scheme not in {"http", "https"} or not (
            host == "xhscdn.com" or host.endswith(".xhscdn.com")
        ):
            return None
        return urlunsplit(("https", parts.netloc, parts.path, "", ""))

    @classmethod
    def _media_candidate(cls, value: Any) -> str | None:
        direct = cls._media_url(value)
        if direct:
            return direct
        if isinstance(value, dict):
            candidate = next(
                (
                    value.get(key)
                    for key in (
                        "url_size_large",
                        "url_default",
                        "master_url",
                        "url",
                        "original",
                    )
                    if value.get(key)
                ),
                None,
            )
            direct = cls._media_url(candidate)
            if direct:
                return direct
            return next(
                (
                    url
                    for key in (
                        "media",
                        "stream",
                        "h264",
                        "h265",
                        "video",
                        "video_info",
                        "url_list",
                        "backup_urls",
                        "info_list",
                        "image_info",
                    )
                    if key in value
                    for nested in [value[key]]
                    for url in [cls._media_candidate(nested)]
                    if url
                ),
                None,
            )
        if isinstance(value, list):
            return next(
                (
                    url
                    for nested in value
                    for url in [cls._media_candidate(nested)]
                    if url
                ),
                None,
            )
        return None

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        try:
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            return (
                datetime.fromtimestamp(timestamp, tz=timezone.utc)
                if timestamp > 0
                else None
            )
        except (OSError, OverflowError, TypeError, ValueError):
            return None

    @staticmethod
    def _integer(value: Any) -> int:
        try:
            return max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            return 0

    def close(self) -> None:
        self.client.close()
