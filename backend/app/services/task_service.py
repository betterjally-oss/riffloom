from __future__ import annotations

import hashlib
import json
import re
import threading
from statistics import median
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.auth import AuthContext
from app.core.errors import AppError
from app.core.observability import current_trace_id, new_trace_id
from app.core.permissions import PermissionAction, require_permission
from app.models import (
    AgentTask,
    AuditLog,
    BloggerRecord,
    BreakdownRecord,
    BreakdownVersion,
    CollectionRecord,
    Conversation,
    CreationRecord,
    CreationVersion,
    FeishuSyncRun,
    KnowledgeChunk,
    Membership,
    ModelCall,
    PilotMemberWorkspace,
    ProviderCall,
    TaskAttempt,
    User,
    Workspace,
)
from app.models.entities import utcnow
from app.schemas.api import (
    AdoptionRead,
    AttemptRead,
    CreationDetailRead,
    CreationVersionRead,
    BreakdownDetailRead,
    BreakdownVersionRead,
    ConversationList,
    ConversationMessageRead,
    ConversationRead,
    ConversationSummaryRead,
    ConversationUpdateInput,
    CreateTaskInput,
    LibraryRecordRead,
    LibraryDeleteRead,
    PilotMetricsRead,
    TaskRead,
)
from app.services.skill_service import get_skill, skill_snapshot
from app.services.attachment_service import validate_attachment_ids

# ponytail: single-instance lock; use database row locking before horizontal scaling.
conversation_write_lock = threading.RLock()
_background_task_key_patterns = (f"agent-{'_' * 64}", f"batch-{'_' * 64}")


def seed_demo_data(session: Session) -> None:
    demo_workspace = session.get(Workspace, "ws_demo")
    if demo_workspace is None:
        demo_workspace = Workspace(
            id="ws_demo",
            name="示例内容团队",
            config={
                "manual_sync": True,
                "feishu_manual_sync_roles": ["admin", "lead"],
            },
        )
        session.add(demo_workspace)
    else:
        demo_config = dict(demo_workspace.config or {})
        demo_config.setdefault("manual_sync", True)
        demo_config.setdefault("feishu_manual_sync_roles", ["admin", "lead"])
        demo_workspace.config = demo_config

    if session.get(Workspace, "ws_other") is None:
        session.add(Workspace(id="ws_other", name="隔离测试工作区", config={}))

    users = {
        "user_demo": "示例用户",
        "user_lead": "内容负责人",
        "user_admin": "工作区管理员",
        "user_other": "隔离用户",
    }
    for user_id, name in users.items():
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, name=name))
    session.flush()

    memberships = (
        ("mem_demo", "ws_demo", "user_demo", "editor"),
        ("mem_lead", "ws_demo", "user_lead", "lead"),
        ("mem_admin", "ws_demo", "user_admin", "admin"),
        ("mem_other", "ws_other", "user_other", "admin"),
    )
    for membership_id, workspace_id, user_id, role in memberships:
        existing_membership = session.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user_id,
            )
        )
        if existing_membership is None:
            session.add(
                Membership(
                    id=membership_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    role=role,
                )
            )
    session.commit()


def _conversation_message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("prompt", "url", "keyword", "creator_url", "creator_id"):
            if isinstance(content.get(key), str) and content[key].strip():
                return content[key]
    return json.dumps(content, ensure_ascii=False)


def _conversation_summary(conversation: Conversation) -> ConversationSummaryRead:
    first_user_message = next(
        (
            _conversation_message_text(message).strip()
            for message in conversation.messages
            if message.get("role") == "user"
        ),
        "新对话",
    )
    return ConversationSummaryRead(
        id=conversation.id,
        mode=conversation.mode,
        title=(conversation.custom_title or "").strip() or first_user_message[:40] or "新对话",
        message_count=len(conversation.messages),
        updated_at=conversation.updated_at,
    )


def list_agent_conversations(
    session: Session, context: AuthContext, limit: int
) -> ConversationList:
    conversations = session.scalars(
        select(Conversation)
        .where(
            Conversation.workspace_id == context.workspace_id,
            Conversation.created_by == context.user_id,
            Conversation.mode.in_(
                ("agent", "collection", "breakdown", "creation", "trend")
            ),
            Conversation.deleted_at.is_(None),
            Conversation.id.in_(
                select(AgentTask.conversation_id).where(
                    AgentTask.conversation_id.is_not(None),
                    ~or_(
                        *(
                            AgentTask.idempotency_key.like(pattern)
                            for pattern in _background_task_key_patterns
                        )
                    ),
                )
            ),
        )
        .order_by(desc(Conversation.updated_at))
        .limit(limit)
    ).all()
    items = [_conversation_summary(conversation) for conversation in conversations]
    return ConversationList(items=items, total=len(items))


def _get_agent_conversation_model(
    session: Session, context: AuthContext, conversation_id: str
) -> Conversation:
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == context.workspace_id,
            Conversation.created_by == context.user_id,
            Conversation.mode.in_(
                ("agent", "collection", "breakdown", "creation", "trend")
            ),
            Conversation.deleted_at.is_(None),
        )
    )
    if conversation is None:
        raise AppError("CONVERSATION_NOT_FOUND", "对话不存在或无权查看", 404)
    return conversation


def get_agent_conversation(
    session: Session, context: AuthContext, conversation_id: str
) -> ConversationRead:
    conversation = _get_agent_conversation_model(session, context, conversation_id)
    summary = _conversation_summary(conversation)
    return ConversationRead(
        **summary.model_dump(),
        messages=[
            ConversationMessageRead(
                role=message["role"],
                content=_conversation_message_text(message),
            )
            for message in conversation.messages
        ],
    )


def update_agent_conversation(
    session: Session,
    context: AuthContext,
    conversation_id: str,
    payload: ConversationUpdateInput,
) -> ConversationSummaryRead:
    conversation = _get_agent_conversation_model(session, context, conversation_id)
    title = payload.title.strip()
    if not title:
        raise AppError("CONVERSATION_TITLE_INVALID", "对话名称不能为空", 422)
    conversation.custom_title = title
    session.commit()
    return _conversation_summary(conversation)


def delete_agent_conversation(
    session: Session, context: AuthContext, conversation_id: str
) -> None:
    conversation = _get_agent_conversation_model(session, context, conversation_id)
    conversation.deleted_at = utcnow()
    session.commit()


def request_hash(payload: CreateTaskInput) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _strip_url_queries(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        parts = urlsplit(match.group(0))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    return re.sub(r"https://[^\s]+", replace, value)


def add_audit(
    session: Session,
    *,
    workspace_id: str,
    user_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )


def create_task(
    session: Session,
    context: AuthContext,
    payload: CreateTaskInput,
    idempotency_key: str,
) -> tuple[AgentTask, bool]:
    with conversation_write_lock:
        return _create_task(session, context, payload, idempotency_key)


def _create_task(
    session: Session,
    context: AuthContext,
    payload: CreateTaskInput,
    idempotency_key: str,
) -> tuple[AgentTask, bool]:
    skill = get_skill(payload.skill_id, role=context.role)
    if payload.mode not in skill.modes:
        raise AppError(
            code="SKILL_MODE_INVALID",
            message="当前技能不能在该模式下运行",
            status_code=422,
        )
    agent_prompt = str(payload.input.get("prompt") or "").strip()
    if payload.skill_id == "riffloom_agent" and not 1 <= len(agent_prompt) <= 8000:
        raise AppError(
            code="AGENT_PROMPT_INVALID",
            message="Agent 对话内容需为 1～8000 个字符",
            status_code=422,
        )
    if payload.skill_id == "riffloom_agent":
        agent_prompt = _strip_url_queries(agent_prompt)
    digest = request_hash(payload)
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
                message="该幂等键已用于不同的任务输入",
                status_code=409,
            )
        return existing, False

    attachments = validate_attachment_ids(
        session, context.workspace_id, payload.attachment_ids
    )
    attachment_snapshot = [
        {
            "id": asset.id,
            "name": asset.original_name,
            "mime_type": asset.mime_type,
            "byte_size": asset.byte_size,
        }
        for asset in attachments
    ]

    conversation_id = payload.conversation_id
    message_content = agent_prompt or str(payload.input.get("prompt") or "").strip()
    if not message_content:
        message_content = json.dumps(payload.input, ensure_ascii=False)
    if conversation_id is None:
        conversation = Conversation(
            workspace_id=context.workspace_id,
            created_by=context.user_id,
            mode=payload.mode,
            messages=[{"role": "user", "content": message_content}],
            skill_refs=[payload.skill_id],
            knowledge_refs=[],
        )
        session.add(conversation)
        session.flush()
        conversation_id = conversation.id
    else:
        conversation = session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.workspace_id == context.workspace_id,
                Conversation.created_by == context.user_id,
                Conversation.deleted_at.is_(None),
            )
        )
        if conversation is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="当前用户不能继续该对话",
                status_code=404,
            )
        if conversation.mode != payload.mode:
            raise AppError(
                code="CONVERSATION_MODE_INVALID",
                message="不能在不同模式之间复用同一对话",
                status_code=422,
            )
        conversation.messages = [
            *conversation.messages,
            {"role": "user", "content": message_content},
        ]
        conversation.skill_refs = list(
            dict.fromkeys([*conversation.skill_refs, payload.skill_id])
        )

    task = AgentTask(
        workspace_id=context.workspace_id,
        conversation_id=conversation_id,
        created_by=context.user_id,
        type=payload.skill_id,
        mode=payload.mode,
        skill_id=payload.skill_id,
        status="queued",
        stage="任务已受理",
        progress=5,
        input_snapshot={
            **payload.input,
            **(
                {"prompt": agent_prompt} if payload.skill_id == "riffloom_agent" else {}
            ),
            "source_ids": payload.source_ids,
            "attachment_ids": [asset.id for asset in attachments],
            "attachments": attachment_snapshot,
            "skill_snapshot": skill_snapshot(skill),
        },
        result_refs=[],
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
            stage="任务已受理",
            progress=5,
        )
    )
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="task.created",
        entity_type="AgentTask",
        entity_id=task.id,
        details={"skill_id": payload.skill_id, "mode": payload.mode},
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
                message="任务创建发生幂等冲突",
                status_code=409,
            )
        return existing, False
    session.refresh(task)
    return task, True


def get_task(session: Session, workspace_id: str, task_id: str) -> AgentTask:
    task = session.scalar(
        select(AgentTask)
        .options(selectinload(AgentTask.attempts))
        .where(AgentTask.id == task_id, AgentTask.workspace_id == workspace_id)
    )
    if task is None:
        raise AppError(
            code="TASK_NOT_FOUND",
            message="当前 Workspace 中不存在该任务",
            status_code=404,
        )
    return task


def list_tasks(session: Session, workspace_id: str, limit: int = 50) -> list[AgentTask]:
    return list(
        session.scalars(
            select(AgentTask)
            .options(selectinload(AgentTask.attempts))
            .where(AgentTask.workspace_id == workspace_id)
            .order_by(desc(AgentTask.created_at))
            .limit(limit)
        ).all()
    )


def retry_task(session: Session, context: AuthContext, task_id: str) -> AgentTask:
    task = get_task(session, context.workspace_id, task_id)
    if task.type == "feishu_sync":
        raise AppError(
            code="FEISHU_RETRY_ENDPOINT_REQUIRED",
            message="飞书同步请通过运行记录的失败项重试接口创建新运行",
            status_code=409,
        )
    if task.status not in {"failed", "partial_success"}:
        raise AppError(
            code="TASK_NOT_RETRYABLE",
            message="只有失败或部分完成的任务可以重试",
            status_code=409,
        )
    task.current_attempt += 1
    task.retry_count += 1
    task.status = "queued"
    task.stage = "重试已受理"
    task.progress = max(5, min(task.progress, 70))
    task.error = None
    task.queued_at = utcnow()
    task.started_at = None
    task.finished_at = None
    session.add(
        TaskAttempt(
            task_id=task.id,
            attempt_no=task.current_attempt,
            status="queued",
            stage="重试已受理",
            progress=task.progress,
        )
    )
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="task.retried",
        entity_type="AgentTask",
        entity_id=task.id,
        details={"attempt_no": task.current_attempt},
    )
    session.commit()
    return get_task(session, context.workspace_id, task_id)


def cancel_task(session: Session, context: AuthContext, task_id: str) -> AgentTask:
    require_permission(context, PermissionAction.TASK_CREATE)
    task = get_task(session, context.workspace_id, task_id)
    if task.status == "cancelled":
        return task
    if task.status not in {"queued", "running"}:
        raise AppError("TASK_NOT_CANCELLABLE", "只有排队中或执行中的任务可以取消", 409)
    if context.role == "editor" and task.created_by != context.user_id:
        raise AppError("TASK_CANCEL_FORBIDDEN", "编辑只能取消自己创建的任务", 403)

    previous_status = task.status
    now = utcnow()
    task.status = "cancelled"
    task.stage = "任务已取消"
    task.error = None
    task.finished_at = now
    attempt = session.scalar(
        select(TaskAttempt).where(
            TaskAttempt.task_id == task.id,
            TaskAttempt.attempt_no == task.current_attempt,
        )
    )
    if attempt is not None:
        attempt.status = "cancelled"
        attempt.stage = task.stage
        attempt.error = None
        attempt.finished_at = now
    run = session.scalar(select(FeishuSyncRun).where(FeishuSyncRun.task_id == task.id))
    if run is not None:
        run.status = "cancelled"
        run.error = None
        run.finished_at = now
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="task.cancelled",
        entity_type="AgentTask",
        entity_id=task.id,
        details={"previous_status": previous_status},
    )
    session.commit()
    return get_task(session, context.workspace_id, task_id)


def task_to_read(task: AgentTask) -> TaskRead:
    return TaskRead(
        id=task.id,
        workspace_id=task.workspace_id,
        conversation_id=task.conversation_id,
        type=task.type,
        mode=task.mode,
        skill_id=task.skill_id,
        status=task.status,  # type: ignore[arg-type]
        stage=task.stage,
        progress=task.progress,
        input=task.input_snapshot,
        result_refs=task.result_refs or [],
        result_summary=task.result_summary or {},
        error=task.error,
        trace_id=task.trace_id,
        current_attempt=task.current_attempt,
        retry_count=task.retry_count,
        attempts=[
            AttemptRead(
                attempt_no=attempt.attempt_no,
                status=attempt.status,  # type: ignore[arg-type]
                stage=attempt.stage,
                progress=attempt.progress,
                error=attempt.error,
                started_at=attempt.started_at,
                finished_at=attempt.finished_at,
            )
            for attempt in task.attempts
        ],
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def list_library_records(
    session: Session, workspace_id: str, library_type: str
) -> list[LibraryRecordRead]:
    if library_type == "collections":
        rows = session.scalars(
            select(CollectionRecord)
            .where(
                CollectionRecord.workspace_id == workspace_id,
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
                type="单篇采集",
                source=row.platform,
                author=row.author,
                updated_at=row.updated_at,
                published_at=row.published_at,
                summary=row.body,
                tags=row.tags,
                version=f"v{row.version}",
                source_id=row.source_identity,
                task_id=row.task_id,
                thumbnail_url=row.cover_url,
                content_type=row.content_type,
                benchmark=row.benchmark,
                category_tags=row.category_tags,
            )
            for row in rows
        ]
    if library_type == "breakdowns":
        rows = session.scalars(
            select(BreakdownRecord)
            .where(
                BreakdownRecord.workspace_id == workspace_id,
                BreakdownRecord.deleted_at.is_(None),
            )
            .order_by(desc(BreakdownRecord.updated_at))
        ).all()
        return [
            LibraryRecordRead(
                id=row.id,
                library_type="breakdowns",
                title=row.title,
                status="已完成" if row.status == "completed" else row.status,
                type="爆款拆解",
                source=row.source_record_id or "临时输入",
                author="Riffloom Agent",
                updated_at=row.updated_at,
                summary=" → ".join(row.structure),
                tags=[
                    "结构化拆解",
                    "阶段 3" if row.skill_version == "1.0.0" else "历史结果",
                ],
                version=f"v{row.version}",
                source_id=row.source_record_id or row.id,
                task_id=row.task_id,
            )
            for row in rows
        ]
    if library_type == "creations":
        rows = session.scalars(
            select(CreationRecord)
            .options(selectinload(CreationRecord.versions))
            .where(
                CreationRecord.workspace_id == workspace_id,
                CreationRecord.deleted_at.is_(None),
            )
            .order_by(desc(CreationRecord.updated_at))
        ).all()
        items: list[LibraryRecordRead] = []
        for row in rows:
            current = next(
                (
                    version
                    for version in row.versions
                    if version.id == row.current_version_id
                ),
                row.versions[-1] if row.versions else None,
            )
            items.append(
                LibraryRecordRead(
                    id=row.id,
                    library_type="creations",
                    title=row.title,
                    status="已采用" if row.adopted_version_id else "待审核",
                    type={
                        "original": "原创",
                        "rewrite": "仿写",
                        "collect_breakdown_rewrite": "采集仿写",
                    }.get(row.creation_type, "创作"),
                    source=" + ".join(ref["id"] for ref in row.source_refs),
                    author=row.created_by,
                    updated_at=row.updated_at,
                    summary=current.body if current else "",
                    tags=current.topics if current else [],
                    version=f"v{current.version if current else 0}",
                    source_id=row.id,
                    task_id=row.task_id,
                    adopted_version_id=row.adopted_version_id,
                )
            )
        return items
    raise AppError(
        code="LIBRARY_TYPE_INVALID",
        message="仅支持 collections、breakdowns 或 creations",
        status_code=422,
    )


def delete_library_record(
    session: Session,
    context: AuthContext,
    library_type: str,
    record_id: str,
) -> LibraryDeleteRead:
    require_permission(context, PermissionAction.LIBRARY_DELETE)
    record: CollectionRecord | BloggerRecord | BreakdownRecord | CreationRecord | None
    if library_type == "collections":
        record = session.scalar(
            select(CollectionRecord).where(
                CollectionRecord.id == record_id,
                CollectionRecord.workspace_id == context.workspace_id,
            )
        ) or session.scalar(
            select(BloggerRecord).where(
                BloggerRecord.id == record_id,
                BloggerRecord.workspace_id == context.workspace_id,
            )
        )
    elif library_type == "breakdowns":
        record = session.scalar(
            select(BreakdownRecord).where(
                BreakdownRecord.id == record_id,
                BreakdownRecord.workspace_id == context.workspace_id,
            )
        )
    elif library_type == "creations":
        record = session.scalar(
            select(CreationRecord).where(
                CreationRecord.id == record_id,
                CreationRecord.workspace_id == context.workspace_id,
            )
        )
    else:
        raise AppError(
            code="LIBRARY_TYPE_INVALID",
            message="仅支持 collections、breakdowns 或 creations",
            status_code=422,
        )
    if record is None:
        raise AppError("LIBRARY_RECORD_NOT_FOUND", "当前 Workspace 中不存在该记录", 404)
    if record.deleted_at is None:
        record.deleted_at = utcnow()
        if hasattr(record, "status"):
            record.status = "deleted"
        for chunk in session.scalars(
            select(KnowledgeChunk).where(
                KnowledgeChunk.workspace_id == context.workspace_id,
                KnowledgeChunk.library_type == library_type,
                KnowledgeChunk.record_id == record.id,
                KnowledgeChunk.deleted_at.is_(None),
            )
        ).all():
            chunk.deleted_at = record.deleted_at
        add_audit(
            session,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            action="library_record.deleted",
            entity_type=type(record).__name__,
            entity_id=record.id,
            details={
                "library_type": library_type,
                "soft_delete": True,
                "external_copies_deleted": False,
            },
        )
        session.commit()
    return LibraryDeleteRead(
        id=record.id,
        library_type=library_type,
        status="deleted",
        deleted_at=record.deleted_at,
    )


def adopt_creation(
    session: Session,
    context: AuthContext,
    record_id: str,
    version_id: str,
) -> AdoptionRead:
    record = session.scalar(
        select(CreationRecord).where(
            CreationRecord.id == record_id,
            CreationRecord.workspace_id == context.workspace_id,
            CreationRecord.deleted_at.is_(None),
        )
    )
    if record is None:
        raise AppError("CREATION_NOT_FOUND", "当前 Workspace 中不存在该创作记录", 404)
    version = session.scalar(
        select(CreationVersion).where(
            CreationVersion.id == version_id,
            CreationVersion.record_id == record.id,
            CreationVersion.workspace_id == context.workspace_id,
        )
    )
    if version is None:
        raise AppError("VERSION_NOT_FOUND", "指定版本不存在或不属于该记录", 404)
    record.adopted_version_id = version.id
    record.status = "adopted"
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="creation.adopted",
        entity_type="CreationRecord",
        entity_id=record.id,
        details={"version_id": version.id},
    )
    session.commit()
    return AdoptionRead(record_id=record.id, version_id=version.id, status="adopted")


def get_pilot_metrics(session: Session, context: AuthContext) -> PilotMetricsRead:
    # ponytail: cumulative Pilot totals; add date filters when the baseline window is fixed.
    workspace_ids = select(Workspace.id).where(
        or_(
            Workspace.id == context.workspace_id,
            Workspace.id.in_(
                select(PilotMemberWorkspace.workspace_id).where(
                    PilotMemberWorkspace.parent_workspace_id == context.workspace_id
                )
            ),
        )
    )
    core_types = {
        "collection_single",
        "collection_keyword",
        "collection_creator_content",
        "collection_creator_profile",
        "viral_breakdown",
        "original_copy",
        "copy_rewrite",
        "collect_breakdown_rewrite",
        "cover_generation",
        "cover_revision",
    }
    tasks = session.execute(
        select(AgentTask.id, AgentTask.status).where(
            AgentTask.workspace_id.in_(workspace_ids),
            AgentTask.type.in_(core_types),
        )
    ).all()
    accepted = [task for task in tasks if task.status != "cancelled"]
    completed = sum(task.status == "success" for task in accepted)
    failed = sum(task.status in {"partial_success", "failed"} for task in accepted)

    adopted = int(
        session.scalar(
            select(func.count(CreationRecord.id)).where(
                CreationRecord.workspace_id.in_(workspace_ids),
                CreationRecord.adopted_version_id.is_not(None),
                CreationRecord.deleted_at.is_(None),
            )
        )
        or 0
    )
    first_version_adoptions = int(
        session.scalar(
            select(func.count(CreationRecord.id))
            .join(
                CreationVersion,
                CreationRecord.adopted_version_id == CreationVersion.id,
            )
            .where(
                CreationRecord.workspace_id.in_(workspace_ids),
                CreationVersion.version == 1,
                CreationRecord.deleted_at.is_(None),
            )
        )
        or 0
    )

    task_started = dict(
        session.execute(
            select(CreationRecord.id, AgentTask.created_at)
            .join(AgentTask, CreationRecord.task_id == AgentTask.id)
            .where(CreationRecord.workspace_id.in_(workspace_ids))
            .where(CreationRecord.deleted_at.is_(None))
        ).all()
    )
    first_adoptions = session.execute(
        select(AuditLog.entity_id, func.min(AuditLog.created_at))
        .where(
            AuditLog.workspace_id.in_(workspace_ids),
            AuditLog.action == "creation.adopted",
        )
        .group_by(AuditLog.entity_id)
    ).all()
    delivery_minutes = [
        max(0.0, (adopted_at - task_started[record_id]).total_seconds() / 60)
        for record_id, adopted_at in first_adoptions
        if adopted_at is not None and record_id in task_started
    ]
    task_ids = [task.id for task in tasks]
    cost = float(
        (
            session.scalar(
                select(func.sum(ModelCall.estimated_cost_usd)).where(
                    ModelCall.task_id.in_(task_ids)
                )
            )
            or 0
        )
        + (
            session.scalar(
                select(func.sum(ProviderCall.cost)).where(
                    ProviderCall.task_id.in_(task_ids)
                )
            )
            or 0
        )
    )

    return PilotMetricsRead(
        accepted_tasks=len(accepted),
        completed_tasks=completed,
        failed_tasks=failed,
        completion_rate=round(completed / len(accepted), 4) if accepted else 0,
        adopted_creations=adopted,
        first_version_adoptions=first_version_adoptions,
        first_version_adoption_rate=(
            round(first_version_adoptions / adopted, 4) if adopted else 0
        ),
        median_delivery_minutes=(
            round(median(delivery_minutes), 2) if delivery_minutes else None
        ),
        estimated_cost_usd=round(cost, 6),
        cost_per_completed_task_usd=(round(cost / completed, 6) if completed else 0),
    )


def get_creation_detail(
    session: Session, workspace_id: str, record_id: str
) -> CreationDetailRead:
    record = session.scalar(
        select(CreationRecord)
        .options(selectinload(CreationRecord.versions))
        .where(
            CreationRecord.id == record_id,
            CreationRecord.workspace_id == workspace_id,
            CreationRecord.deleted_at.is_(None),
        )
    )
    if record is None:
        raise AppError("CREATION_NOT_FOUND", "当前 Workspace 中不存在该创作记录", 404)
    return CreationDetailRead(
        id=record.id,
        title=record.title,
        status=record.status,
        source_refs=record.source_refs,
        skill_id=record.skill_id,
        current_version_id=record.current_version_id,
        adopted_version_id=record.adopted_version_id,
        versions=[
            CreationVersionRead(
                id=version.id,
                version=version.version,
                body=version.body,
                topics=version.topics,
                change_note=version.change_note,
                knowledge_snapshot=version.knowledge_snapshot,
                generation_config=version.generation_config,
                title_candidates=version.title_candidates,
                summary=version.summary,
                risk_notes=version.risk_notes,
                source_refs=version.source_refs,
                similarity_report=version.similarity_report,
                model_call_id=version.model_call_id,
                created_at=version.created_at,
            )
            for version in record.versions
        ],
        task_id=record.task_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def get_breakdown_detail(
    session: Session, workspace_id: str, record_id: str
) -> BreakdownDetailRead:
    record = session.scalar(
        select(BreakdownRecord)
        .options(selectinload(BreakdownRecord.versions))
        .where(
            BreakdownRecord.id == record_id,
            BreakdownRecord.workspace_id == workspace_id,
            BreakdownRecord.deleted_at.is_(None),
        )
    )
    if record is None:
        raise AppError("BREAKDOWN_NOT_FOUND", "当前 Workspace 中不存在该拆解记录", 404)
    source = (
        session.get(CollectionRecord, record.source_record_id)
        if record.source_record_id
        else None
    )
    return BreakdownDetailRead(
        id=record.id,
        title=record.title,
        status=record.status,
        source_record_id=record.source_record_id,
        source_metrics=(
            source.metrics
            if source
            and source.workspace_id == workspace_id
            and source.deleted_at is None
            else {}
        ),
        current_version_id=record.current_version_id,
        versions=[
            BreakdownVersionRead(
                id=version.id,
                version=version.version,
                source_refs=version.source_refs,
                observed_facts=version.observed_facts,
                hook=version.hook,
                structure=version.structure,
                emotion=version.emotion,
                visual=version.visual,
                interaction=version.interaction,
                reusable_methods=version.reusable_methods,
                risks=version.risks,
                skill_snapshot=version.skill_snapshot,
                model_call_id=version.model_call_id,
                created_at=version.created_at,
            )
            for version in record.versions
        ],
        task_id=record.task_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
