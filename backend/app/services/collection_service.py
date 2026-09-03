from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.auth import AuthContext
from app.core.errors import AppError
from app.core.observability import current_trace_id, new_trace_id
from app.models import (
    AgentTask,
    BloggerRecord,
    BreakdownRecord,
    BreakdownVersion,
    CollectionRecord,
    Conversation,
    TaskAttempt,
)
from app.providers import CollectorProvider
from app.schemas.api import (
    BatchCollectionMetadataInput,
    CollectRewriteTaskInput,
    CollectionBatchTaskInput,
    CollectionDetailRead,
    CreateCollectionTaskInput,
    CreateTaskInput,
    LibraryRecordRead,
)
from app.services.task_service import add_audit, create_task
from app.services.attachment_service import validate_attachment_ids


KIND_LABELS = {
    "single": "单篇采集",
    "keyword": "关键词结果",
    "creator_content": "博主内容",
    "creator_profile": "博主信息",
}


def _request_hash(payload: CreateCollectionTaskInput) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _split_collection_query(
    payload: CreateCollectionTaskInput,
    provider: CollectorProvider,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    query = dict(payload.query)
    persisted = dict(query)
    for key, value in query.items():
        if not isinstance(value, str):
            continue
        parts = urlsplit(value)
        if parts.scheme in {"http", "https"} and parts.netloc and (parts.query or parts.fragment):
            persisted[key] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    transient = query if persisted != query else None
    return persisted, transient


def create_collection_task(
    session: Session,
    context: AuthContext,
    payload: CreateCollectionTaskInput,
    idempotency_key: str,
    provider: CollectorProvider,
) -> tuple[AgentTask, bool, dict[str, Any] | None]:
    capabilities = provider.capabilities()
    if payload.platform not in capabilities["platforms"]:
        raise AppError(
            code="PLATFORM_NOT_ALLOWED",
            message="当前数据源未授权该采集平台",
            status_code=422,
        )
    if payload.kind not in capabilities["kinds"]:
        raise AppError(
            code="COLLECTION_KIND_NOT_SUPPORTED",
            message="当前数据源不支持该采集类型",
            status_code=422,
        )

    digest = _request_hash(payload)
    existing = session.scalar(
        select(AgentTask)
        .options(selectinload(AgentTask.attempts))
        .where(
            AgentTask.workspace_id == context.workspace_id,
            AgentTask.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_hash != digest:
            raise AppError(
                code="IDEMPOTENCY_CONFLICT",
                message="该幂等键已用于不同的采集输入",
                status_code=409,
            )
        return existing, False, None

    persisted_query, transient_query = _split_collection_query(payload, provider)
    attachments = validate_attachment_ids(
        session, context.workspace_id, payload.attachment_ids
    )

    conversation_id = payload.conversation_id
    if conversation_id is None:
        conversation = Conversation(
            workspace_id=context.workspace_id,
            created_by=context.user_id,
            mode="collection",
            messages=[{"role": "user", "content": persisted_query}],
            skill_refs=["collect_content"],
            knowledge_refs=[],
        )
        session.add(conversation)
        session.flush()
        conversation_id = conversation.id

    snapshot: dict[str, Any] = {
        "kind": payload.kind,
        "platform": payload.platform,
        "query": persisted_query,
        "usage_confirmed": payload.usage_confirmed,
        "refresh": payload.refresh,
        "limit": payload.limit,
        "provider": provider.provider_id,
        "is_sandbox": provider.is_sandbox,
        "attachment_ids": [asset.id for asset in attachments],
        "attachments": [
            {
                "id": asset.id,
                "name": asset.original_name,
                "mime_type": asset.mime_type,
                "byte_size": asset.byte_size,
            }
            for asset in attachments
        ],
    }
    task = AgentTask(
        workspace_id=context.workspace_id,
        conversation_id=conversation_id,
        created_by=context.user_id,
        type=f"collection_{payload.kind}",
        mode="collection",
        skill_id="collect_content",
        status="queued",
        stage="采集任务已受理",
        progress=5,
        input_snapshot=snapshot,
        result_refs=[],
        result_summary={"new": 0, "reused": 0, "failed": 0, "total": 0},
        idempotency_key=idempotency_key,
        request_hash=digest,
        trace_id=current_trace_id() or new_trace_id(),
        current_attempt=1,
    )
    session.add(task)
    session.flush()
    session.add(
        TaskAttempt(
            task_id=task.id,
            attempt_no=1,
            status="queued",
            stage="采集任务已受理",
            progress=5,
        )
    )
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="collection_task.created",
        entity_type="AgentTask",
        entity_id=task.id,
        details={
            "kind": payload.kind,
            "platform": payload.platform,
            "provider": provider.provider_id,
            "is_sandbox": provider.is_sandbox,
        },
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(AgentTask)
            .options(selectinload(AgentTask.attempts))
            .where(
                AgentTask.workspace_id == context.workspace_id,
                AgentTask.idempotency_key == idempotency_key,
            )
        )
        if existing is None or existing.request_hash != digest:
            raise AppError(
                code="IDEMPOTENCY_CONFLICT",
                message="采集任务创建发生幂等冲突",
                status_code=409,
            )
        return existing, False
    session.refresh(task)
    return task, True, transient_query


def create_collect_rewrite_task(
    session: Session,
    context: AuthContext,
    payload: CollectRewriteTaskInput,
    idempotency_key: str,
    provider: CollectorProvider,
) -> tuple[AgentTask, bool, dict[str, Any] | None]:
    transient_query = None
    url = None
    if payload.url:
        collection_payload = CreateCollectionTaskInput(
            kind="single",
            platform="xiaohongshu",
            query={"url": payload.url.strip()},
            usage_confirmed=payload.usage_confirmed,
            limit=1,
        )
        if "single" not in provider.capabilities()["kinds"]:
            raise AppError(
                "COLLECTION_KIND_NOT_SUPPORTED",
                "当前数据源不支持单篇链接采集",
                422,
            )
        persisted, transient_query = _split_collection_query(collection_payload, provider)
        url = persisted["url"]
    else:
        record = session.scalar(
            select(CollectionRecord).where(
                CollectionRecord.id == payload.collection_id,
                CollectionRecord.workspace_id == context.workspace_id,
                CollectionRecord.deleted_at.is_(None),
            )
        )
        if record is None:
            raise AppError(
                "COLLECTION_RECORD_NOT_FOUND",
                "采集记录不存在或不属于当前 Workspace",
                404,
            )

    task, created = create_task(
        session,
        context,
        CreateTaskInput(
            mode="agent",
            skill_id="collect_breakdown_rewrite",
            input={
                "prompt": payload.prompt.strip(),
                "url": url,
                "source_link_requires_resubmission": transient_query is not None,
                "collection_id": payload.collection_id,
                "usage_confirmed": payload.usage_confirmed,
                "knowledge_refs": [item.model_dump() for item in payload.knowledge_refs],
                "target_platform": payload.target_platform,
                "audience": payload.audience,
            },
            attachment_ids=payload.attachment_ids,
            conversation_id=payload.conversation_id,
        ),
        idempotency_key,
    )
    return task, created, transient_query


def list_collection_library(
    session: Session, workspace_id: str, kind: str
) -> list[LibraryRecordRead]:
    if kind == "creator_profile":
        rows = session.scalars(
            select(BloggerRecord)
            .where(
                BloggerRecord.workspace_id == workspace_id,
                BloggerRecord.deleted_at.is_(None),
            )
            .order_by(desc(BloggerRecord.updated_at))
        ).all()
        return [
            LibraryRecordRead(
                id=row.id,
                library_type="collections",
                title=row.name,
                status="已完成",
                type=KIND_LABELS[kind],
                source=row.profile_url,
                author=row.name,
                updated_at=row.updated_at,
                published_at=None,
                summary=row.bio,
                tags=row.tags,
                version="v1",
                source_id=row.external_id,
                task_id=row.task_id,
                collection_kind=kind,
                provider=row.provider,
                external_url=row.profile_url,
                thumbnail_url=row.avatar_url,
                metrics={"followers": row.followers, "likes_and_collects": row.likes_and_collects},
                benchmark=row.benchmark,
                category_tags=row.tags,
                is_sandbox=row.provider.startswith("sandbox"),
            )
            for row in rows
        ]

    rows = session.scalars(
        select(CollectionRecord)
        .where(
            CollectionRecord.workspace_id == workspace_id,
            CollectionRecord.collection_kind == kind,
            CollectionRecord.deleted_at.is_(None),
        )
        .order_by(desc(CollectionRecord.updated_at))
    ).all()
    return [
        LibraryRecordRead(
            id=row.id,
            library_type="collections",
            title=row.title,
            status="已完成" if row.status == "completed" else row.status,
            type=KIND_LABELS.get(kind, kind),
            source=row.canonical_url,
            author=row.author,
            updated_at=row.updated_at,
            published_at=row.published_at,
            summary=row.body,
            tags=row.tags or row.topics,
            version=f"v{row.version}",
            source_id=row.external_id or row.source_identity,
            task_id=row.task_id,
            collection_kind=kind,
            provider=row.provider,
            external_url=row.canonical_url,
            thumbnail_url=row.cover_url,
            metrics=row.metrics,
            content_type=row.content_type,
            benchmark=row.benchmark,
            category_tags=row.category_tags,
            is_sandbox=row.provider.startswith("sandbox") or row.provider.startswith("deterministic"),
        )
        for row in rows
    ]


def update_collection_metadata(
    session: Session,
    context: AuthContext,
    payload: BatchCollectionMetadataInput,
) -> list[str]:
    content = list(
        session.scalars(
            select(CollectionRecord).where(
                CollectionRecord.workspace_id == context.workspace_id,
                CollectionRecord.id.in_(payload.record_ids),
                CollectionRecord.deleted_at.is_(None),
            )
        ).all()
    )
    bloggers = list(
        session.scalars(
            select(BloggerRecord).where(
                BloggerRecord.workspace_id == context.workspace_id,
                BloggerRecord.id.in_(payload.record_ids),
                BloggerRecord.deleted_at.is_(None),
            )
        ).all()
    )
    records = [*content, *bloggers]
    if {record.id for record in records} != set(payload.record_ids):
        raise AppError(
            "COLLECTION_RECORD_NOT_FOUND",
            "部分采集记录不存在或不属于当前 Workspace",
            404,
        )
    for record in records:
        if payload.benchmark is not None:
            record.benchmark = payload.benchmark
        if payload.category_tags is not None:
            if isinstance(record, CollectionRecord):
                record.category_tags = payload.category_tags
            else:
                record.tags = payload.category_tags
        add_audit(
            session,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            action="collection_metadata.updated",
            entity_type=type(record).__name__,
            entity_id=record.id,
            details={
                "benchmark": payload.benchmark,
                "category_tags": payload.category_tags,
            },
        )
    session.commit()
    return payload.record_ids


_BATCH_BREAKDOWN_PROMPT = (
    "仅依据该采集记录的可观察内容，拆解开头钩子、结构、情绪、互动与可复用方法；"
    "区分观察和判断，不推断未提供的画面，不复制受保护表达。"
)


def create_collection_batch_tasks(
    session: Session,
    context: AuthContext,
    payload: CollectionBatchTaskInput,
    idempotency_key: str,
    provider: CollectorProvider,
) -> tuple[list[tuple[AgentTask, bool]], list[dict[str, Any]]]:
    results: list[tuple[AgentTask, bool]] = []
    issues: list[dict[str, Any]] = []
    for record_id in payload.record_ids:
        record = session.scalar(
            select(CollectionRecord).where(
                CollectionRecord.id == record_id,
                CollectionRecord.workspace_id == context.workspace_id,
                CollectionRecord.deleted_at.is_(None),
            )
        )
        if record is None:
            issues.append(
                {
                    "record_id": record_id,
                    "code": "COLLECTION_RECORD_NOT_FOUND",
                    "message": "记录不存在、不属于当前 Workspace 或不是内容记录",
                    "retryable": False,
                }
            )
            continue
        child_key = "batch-" + hashlib.sha256(
            f"{idempotency_key}:{payload.action}:{record_id}".encode("utf-8")
        ).hexdigest()
        try:
            if payload.action == "breakdown":
                result = create_task(
                    session,
                    context,
                    CreateTaskInput(
                        mode="breakdown",
                        skill_id="viral_breakdown",
                        input={
                            "prompt": payload.prompt.strip() or _BATCH_BREAKDOWN_PROMPT,
                            "knowledge_refs": [
                                item.model_dump() for item in payload.knowledge_refs
                            ],
                        },
                        source_ids=[record_id],
                    ),
                    child_key,
                )
            else:
                task, created, _ = create_collect_rewrite_task(
                    session,
                    context,
                    CollectRewriteTaskInput(
                        collection_id=record_id,
                        prompt=payload.prompt,
                        knowledge_refs=payload.knowledge_refs,
                    ),
                    child_key,
                    provider,
                )
                result = task, created
            results.append(result)
        except AppError as exc:
            issues.append(
                {
                    "record_id": record_id,
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                }
            )
    return results, issues


def get_collection_detail(
    session: Session, workspace_id: str, record_id: str
) -> CollectionDetailRead:
    record = session.scalar(
        select(CollectionRecord).where(
            CollectionRecord.id == record_id,
            CollectionRecord.workspace_id == workspace_id,
            CollectionRecord.deleted_at.is_(None),
        )
    )
    if record is not None:
        source_parts = urlsplit(record.canonical_url)
        likes = record.metrics.get("likes", 0)
        collects = record.metrics.get("collects", 0)
        comments = record.metrics.get("comments", 0)
        breakdown = session.execute(
            select(BreakdownRecord.id, BreakdownVersion.source_refs)
            .join(BreakdownVersion, BreakdownVersion.id == BreakdownRecord.current_version_id)
            .where(
                BreakdownRecord.workspace_id == workspace_id,
                BreakdownRecord.source_record_id == record.id,
                BreakdownRecord.deleted_at.is_(None),
            )
            .order_by(desc(BreakdownRecord.updated_at))
            .limit(1)
        ).first()
        breakdown_is_current = bool(
            breakdown
            and any(
                ref.get("type") == "collection"
                and ref.get("id") == record.id
                and ref.get("version") == f"v{record.version}"
                for ref in breakdown.source_refs
            )
        )
        return CollectionDetailRead(
            id=record.id,
            entity_type="content",
            collection_kind=record.collection_kind,  # type: ignore[arg-type]
            title=record.title,
            body=record.body,
            content_type=record.content_type,
            platform=record.platform,
            provider=record.provider,
            actual_upstream=record.actual_upstream,
            is_sandbox=record.provider.startswith("sandbox") or record.provider.startswith("deterministic"),
            source_id=record.external_id or record.source_identity,
            external_url=record.canonical_url,
            author=record.author,
            author_external_id=record.author_external_id,
            cover_url=record.cover_url,
            published_at=record.published_at,
            topics=record.topics,
            tags=record.tags or record.topics,
            metrics=record.metrics,
            derived_metrics={
                "like_collect_ratio": likes / collects if collects else None,
                "like_comment_ratio": likes / comments if comments else None,
            },
            system_fields={
                "normalized_url": record.canonical_url,
                "domain": source_parts.hostname or "",
                "collection_year": str(record.last_collected_at.year),
                "collection_month": f"{record.last_collected_at.month:02d}",
                "collection_month_day": record.last_collected_at.strftime("%m-%d"),
                "unique_id": record.external_id or record.source_identity,
                "cover_link": record.cover_url or "",
                "image_links": [
                    ref.get("url", "")
                    for ref in record.media_refs
                    if ref.get("type", "").startswith(("image", "cover"))
                ],
                "last_updated_at": record.updated_at.isoformat(),
            },
            media_refs=record.media_refs,
            video_transcript=record.video_transcript,
            video_transcript_corrected=record.video_transcript_corrected,
            video_transcript_status=record.video_transcript_status,
            video_transcript_source=record.video_transcript_source,
            video_transcript_confidence=record.video_transcript_confidence,
            video_transcript_segments=record.video_transcript_segments,
            breakdown_id=breakdown.id if breakdown else None,
            breakdown_is_current=breakdown_is_current,
            task_id=record.task_id,
            collected_at=record.last_collected_at,
            updated_at=record.updated_at,
        )

    blogger = session.scalar(
        select(BloggerRecord).where(
            BloggerRecord.id == record_id,
            BloggerRecord.workspace_id == workspace_id,
            BloggerRecord.deleted_at.is_(None),
        )
    )
    if blogger is None:
        raise AppError(
            code="COLLECTION_RECORD_NOT_FOUND",
            message="当前 Workspace 中不存在该采集记录",
            status_code=404,
        )
    return CollectionDetailRead(
        id=blogger.id,
        entity_type="blogger",
        collection_kind="creator_profile",
        title=blogger.name,
        body=blogger.bio,
        content_type=None,
        platform=blogger.platform,
        provider=blogger.provider,
        actual_upstream=None,
        is_sandbox=blogger.provider.startswith("sandbox"),
        source_id=blogger.external_id,
        external_url=blogger.profile_url,
        author=blogger.name,
        tags=blogger.tags,
        metrics={"followers": blogger.followers, "likes_and_collects": blogger.likes_and_collects},
        system_fields={
            "normalized_url": blogger.profile_url,
            "domain": urlsplit(blogger.profile_url).hostname or "",
            "collection_year": str(blogger.last_collected_at.year),
            "collection_month": f"{blogger.last_collected_at.month:02d}",
            "collection_month_day": blogger.last_collected_at.strftime("%m-%d"),
            "unique_id": blogger.external_id,
            "last_updated_at": blogger.updated_at.isoformat(),
        },
        media_refs=[{"type": "avatar", "url": blogger.avatar_url}] if blogger.avatar_url else [],
        video_transcript_status="not_applicable",
        task_id=blogger.task_id,
        collected_at=blogger.last_collected_at,
        updated_at=blogger.updated_at,
    )
