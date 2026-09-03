from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.auth import AuthContext
from app.core.permissions import PermissionAction, require_permission
from app.models.entities import utcnow
from app.providers import ProviderBatch
from app.schemas.api import CreateTaskInput, TrendTaskInput
from app.services.task_service import create_task


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _search_query(direction: str) -> str:
    query = re.sub(
        r"(?:给我|输出|生成|推荐|找出|提供)?\s*\d{1,3}\s*[个条]\s*(?:热点|选题)",
        "",
        direction,
    ).strip(" ，,。；;")
    return query or direction.strip()


def empty_trend_snapshot(window_days: int) -> dict[str, Any]:
    return {
        "schema_version": "tikhub-trend-snapshot.v1",
        "provider": "pending",
        "window_days": window_days,
        "created_at": _iso(utcnow()),
        "items": [],
        "platforms": [],
        "empty": True,
        "freshness_verified": False,
        "scope": "tikhub_multi_platform_recent_public_content",
    }


def snapshot_from_trend_batch(batch: ProviderBatch, window_days: int) -> dict[str, Any]:
    items = [
        {
            "platform": (item.actual_upstream or "unknown").split("/")[-1].split("-")[0],
            "source_id": item.external_id,
            "title": item.title,
            "observed_at": _iso(item.published_at or utcnow()),
            "excerpt": item.body[:500],
            "source_url": item.canonical_url,
            "author": item.author_name,
            "metrics": item.metrics,
        }
        for item in batch.content_items
    ]
    platforms = list(dict.fromkeys(str(item["platform"]) for item in items))
    return {
        "schema_version": "tikhub-trend-snapshot.v1",
        "provider": batch.provider,
        "window_days": window_days,
        "created_at": _iso(utcnow()),
        "items": items,
        "platforms": platforms,
        "empty": not items,
        "freshness_verified": bool(items),
        "scope": "tikhub_multi_platform_recent_public_content",
    }


def create_trend_task(
    session: Session,
    context: AuthContext,
    payload: TrendTaskInput,
    idempotency_key: str,
):
    require_permission(context, PermissionAction.TREND_CREATE)
    prompt = payload.direction
    if payload.audience:
        prompt += f"；目标受众：{payload.audience}"
    if payload.keywords:
        prompt += f"；关键词：{'、'.join(payload.keywords)}"
    generic = CreateTaskInput(
        mode="trend",
        skill_id="viral_topic_coach",
        input={
            "prompt": prompt,
            "direction": payload.direction,
            "search_query": _search_query(payload.direction),
            "audience": payload.audience,
            "keywords": payload.keywords,
            "window_days": payload.window_days,
            "result_count": payload.result_count or 5,
            "signal_snapshot": empty_trend_snapshot(payload.window_days),
            "realtime_signal_verified": False,
        },
        conversation_id=payload.conversation_id,
    )
    return create_task(session, context, generic, idempotency_key)
