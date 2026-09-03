from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select

from app.core.config import Settings
from app.core.database import Database
from app.core.errors import AppError
from app.core.observability import bind_log_context, new_trace_id
from app.models import (
    AgentTask,
    BloggerRecord,
    BreakdownRecord,
    CollectionRecord,
    CollectionTaskItem,
    Conversation,
    CreationRecord,
    CreationVersion,
    MediaAsset,
    Membership,
    ProviderCall,
    TaskAttempt,
    User,
    Workspace,
)
from app.models.entities import new_id, utcnow
from app.providers import (
    CollectionRequest,
    CollectorProvider,
    CoverProvider,
    CoverProviderError,
    FeishuProvider,
    ProviderBatch,
    ProviderError,
    TranscriptionProvider,
    TranscriptionProviderError,
    TranscriptionRequest,
)
from app.providers.models import GenerationProvider, ModelProviderError
from app.core.auth import AuthContext
from app.schemas.api import CollectRewriteTaskInput, CreateTaskInput, TrendTaskInput
from app.schemas.generation import AgentChatOutputV2
from app.services.generation_service import (
    create_breakdown_result,
    create_creation_result,
    create_topic_result,
    selected_knowledge_context,
)
from app.services.asset_store import AssetStore
from app.services.attachment_service import load_attachment_context
from app.services.cover_service import execute_cover_task
from app.services.feishu_service import execute_sync_run
from app.services.collection_service import create_collect_rewrite_task
from app.services.task_service import add_audit, conversation_write_lock, create_task
from app.services.trend_service import create_trend_task, snapshot_from_trend_batch
from app.services.model_service import call_model
from app.services.transcription_service import execute_transcription_task
from app.services.tos_presign import PresignError, UploadPresigner

logger = logging.getLogger("riffloom.worker")

_RIFFLOOM_AGENT_CAPABILITIES = (
    "原创或仿写小红书文案，生成标题、正文和话题建议",
    "拆解内容的开头钩子、结构、情绪、互动与可复用方法",
    "结合当前工作区记录提供选题灵感与创作切角",
    "采集有权使用的小红书单篇链接并保存结构化结果",
    "转写有权使用的 5 分钟以内音视频",
    "引用采集库、拆解库和创作库中明确选中的记录",
)
_RIFFLOOM_AGENT_ACTIONS = frozenset(
    {"reply", "breakdown", "original", "rewrite", "trend", "collect", "collect_rewrite"}
)


class TaskWorker:
    def __init__(
        self,
        database: Database,
        *,
        provider: CollectorProvider,
        generation_provider: GenerationProvider,
        transcription_provider: TranscriptionProvider,
        cover_provider: CoverProvider,
        feishu_provider: FeishuProvider,
        asset_store: AssetStore,
        media_presigner: UploadPresigner,
        settings: Settings,
        step_delay: float,
        max_workers: int = 2,
    ):
        self.database = database
        self.provider = provider
        self.generation_provider = generation_provider
        self.transcription_provider = transcription_provider
        self.cover_provider = cover_provider
        self.feishu_provider = feishu_provider
        self.asset_store = asset_store
        self.media_presigner = media_presigner
        self.settings = settings
        self.step_delay = step_delay
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="riffloom"
        )
        self._inflight: set[str] = set()
        self._collection_queries: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._cover_lock = threading.Lock()
        self._feishu_lock = threading.Lock()

    def submit(
        self,
        task_id: str,
        *,
        collection_query: dict[str, Any] | None = None,
    ) -> bool:
        with self._lock:
            if task_id in self._inflight:
                return False
            self._inflight.add(task_id)
            if collection_query is not None:
                self._collection_queries[task_id] = dict(collection_query)
        future = self.executor.submit(self._run, task_id)
        future.add_done_callback(lambda completed: self._finish(task_id, completed))
        return True

    def _finish(self, task_id: str, future: Future[None]) -> None:
        with self._lock:
            self._inflight.discard(task_id)
            self._collection_queries.pop(task_id, None)
        exception = future.exception()
        if exception is not None:
            logger.exception(
                "worker task crashed", exc_info=exception, extra={"task_id": task_id}
            )

    def recover(self) -> int:
        with self.database.session_factory() as session:
            tasks = list(
                session.scalars(
                    select(AgentTask).where(AgentTask.status.in_(["queued", "running"]))
                ).all()
            )
            for task in tasks:
                if task.status == "running":
                    task.status = "queued"
                    task.stage = "服务恢复后重新排队"
                    attempt = session.scalar(
                        select(TaskAttempt).where(
                            TaskAttempt.task_id == task.id,
                            TaskAttempt.attempt_no == task.current_attempt,
                        )
                    )
                    if attempt:
                        attempt.status = "queued"
                        attempt.stage = task.stage
                session.commit()
        for task in tasks:
            self.submit(task.id)
        return len(tasks)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)

    def _run(self, task_id: str) -> None:
        started = time.perf_counter()
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None:
                trace_id = new_trace_id()
                workspace_id = None
            else:
                trace_id = task.trace_id or new_trace_id()
                workspace_id = task.workspace_id
                if task.trace_id is None:
                    task.trace_id = trace_id
                    session.commit()
        with bind_log_context(
            trace_id=trace_id,
            task_id=task_id,
            workspace_id=workspace_id,
        ):
            logger.info("task_execution_started")
            try:
                self._run_bound(task_id)
            finally:
                with self.database.session_factory() as session:
                    completed = session.get(AgentTask, task_id)
                    status = completed.status if completed else "missing"
                    attempt_no = completed.current_attempt if completed else None
                logger.info(
                    "task_execution_finished",
                    extra={
                        "status": status,
                        "attempt_no": attempt_no,
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                    },
                )

    def _run_bound(self, task_id: str) -> None:
        attempt_no = self._start(task_id)
        if attempt_no is None:
            return
        try:
            if self._is_collection_task(task_id):
                self._run_collection(task_id, attempt_no)
                return
            if self._task_type(task_id) in {"cover_generation", "cover_revision"}:
                self._run_cover_task(task_id, attempt_no)
                return
            if self._task_type(task_id) == "feishu_sync":
                self._run_feishu_task(task_id, attempt_no)
                return
            if self._task_type(task_id) == "collection_video_transcription":
                self._run_transcription_task(task_id, attempt_no)
                return
            skill_id = self._skill_id(task_id)
            if skill_id == "riffloom_agent":
                self._run_agent_chat(task_id, attempt_no)
                return
            if skill_id == "collect_breakdown_rewrite":
                self._run_collect_rewrite(task_id, attempt_no)
                return
            if skill_id == "viral_breakdown":
                self._run_breakdown_task(task_id, attempt_no)
                return
            if skill_id in {"original_copy", "copy_rewrite"}:
                self._run_creation_task(task_id, attempt_no)
                return
            if skill_id == "viral_topic_coach":
                self._run_topic_task(task_id, attempt_no)
                return
            if skill_id == "collect_content":
                self._fail(
                    task_id,
                    attempt_no,
                    status="failed",
                    stage="采集任务输入不完整",
                    code="COLLECTION_ENDPOINT_REQUIRED",
                    message="采集内容请通过四类采集表单创建任务",
                    progress=10,
                    retryable=False,
                )
                return
            self._sleep()
            collection = self._collection_stage(task_id, attempt_no)
            if collection is None:
                return
            if self._should_fail(task_id, attempt_no):
                self._fail(
                    task_id,
                    attempt_no,
                    status="partial_success",
                    stage="拆解适配器失败",
                    code="SIMULATED_PROVIDER_ERROR",
                    message="阶段 1 故障场景：拆解适配器暂时不可用",
                    progress=38,
                )
                return
            self._sleep()
            breakdown = self._breakdown_stage(task_id, attempt_no, collection.id)
            if breakdown is None:
                return
            self._sleep()
            creation = self._creation_stage(
                task_id, attempt_no, collection.id, breakdown.id
            )
            if creation is None:
                return
            self._complete(
                task_id, attempt_no, collection.id, breakdown.id, creation.id
            )
        except (
            AppError,
            ModelProviderError,
            ProviderError,
            CoverProviderError,
            TranscriptionProviderError,
        ) as exc:
            self._fail(
                task_id,
                attempt_no,
                status="failed",
                stage="生成任务失败",
                code=exc.code,
                message=exc.message,
                progress=20,
                retryable=getattr(exc, "retryable", False),
            )
        except Exception as exc:  # boundary: worker errors become persisted failures
            logger.exception(
                "workflow failed", extra={"task_id": task_id, "attempt_no": attempt_no}
            )
            self._fail(
                task_id,
                attempt_no,
                status="failed",
                stage="工作流异常",
                code="WORKFLOW_ERROR",
                message="后台任务执行失败，请重试",
                progress=0,
                details={"type": type(exc).__name__},
            )

    def _skill_id(self, task_id: str) -> str:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            return task.skill_id if task else ""

    def _task_type(self, task_id: str) -> str:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            return task.type if task else ""

    def _run_cover_task(self, task_id: str, attempt_no: int) -> None:
        self._advance(task_id, attempt_no, "再次校验素材权利与创作版本", 25)
        self._sleep()
        self._advance(task_id, attempt_no, "封面 Provider 正在生成", 55)
        # ponytail: global lock prevents duplicate asset commits; use per-workspace
        # locks only if cover generation throughput becomes a measured bottleneck.
        with self._cover_lock:
            with self.database.session_factory() as session:
                task = session.get(AgentTask, task_id)
                if task is None or not self._current(task, attempt_no):
                    return
                execute_cover_task(
                    session,
                    task=task,
                    attempt_no=attempt_no,
                    provider=self.cover_provider,
                    store=self.asset_store,
                )

    def _run_feishu_task(self, task_id: str, attempt_no: int) -> None:
        self._advance(task_id, attempt_no, "再次校验飞书连接、绑定与角色", 25)
        self._sleep()
        self._advance(task_id, attempt_no, "飞书 Provider 正在执行受限 upsert", 55)
        with self._feishu_lock:
            with self.database.session_factory() as session:
                task = session.get(AgentTask, task_id)
                if task is None or not self._current(task, attempt_no):
                    return
                execute_sync_run(
                    session,
                    task=task,
                    attempt_no=attempt_no,
                    provider=self.feishu_provider,
                )

    def _run_breakdown_task(self, task_id: str, attempt_no: int) -> None:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            compact = bool(
                task and task.input_snapshot.get("preset") == "drawer_compact"
            )
        video_analysis = self._analyze_breakdown_video(task_id, attempt_no)
        self._advance(
            task_id,
            attempt_no,
            "正在提炼精简拆解" if compact else "正在生成结构化拆解",
            70 if compact else 35,
        )
        self._sleep()
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            record = create_breakdown_result(
                session,
                task=task,
                attempt_no=attempt_no,
                provider=self.generation_provider,
                settings=self.settings,
                asset_store=self.asset_store,
                video_analysis=video_analysis,
            )
        self._complete_single(
            task_id, attempt_no, "breakdown", record.id, "拆解结果已入库"
        )

    def _analyze_breakdown_video(
        self, task_id: str, attempt_no: int
    ) -> dict[str, Any] | None:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return None
            if task.input_snapshot.get("preset") != "drawer_compact":
                return None
            source = session.scalar(
                select(CollectionRecord).where(
                    CollectionRecord.workspace_id == task.workspace_id,
                    CollectionRecord.id.in_(
                        task.input_snapshot.get("source_ids") or []
                    ),
                    CollectionRecord.deleted_at.is_(None),
                )
            )
            if source is None or (source.content_type or "").lower() not in {
                "视频",
                "video",
            }:
                return None
            transcript = (source.video_transcript_corrected or "").strip() or (
                source.video_transcript or ""
            ).strip()
            if not transcript or source.video_transcript_status not in {
                "complete",
                "corrected",
            }:
                return None
            video_ref = next(
                (
                    ref
                    for ref in source.media_refs
                    if str(ref.get("type") or "").startswith("video")
                    and ref.get("asset_id")
                ),
                None,
            )
            # Manually supplied audio can produce a transcript without a retained video.
            if video_ref is None:
                return None
            asset = session.scalar(
                select(MediaAsset).where(
                    MediaAsset.id == video_ref["asset_id"],
                    MediaAsset.workspace_id == task.workspace_id,
                    MediaAsset.role == "reference",
                    MediaAsset.rights_status == "approved",
                )
            )
            if asset is None:
                raise AppError(
                    "VIDEO_ANALYSIS_ASSET_NOT_FOUND",
                    "已采集的视频文件不可用，请重新采集后再拆解",
                    404,
                )
            if not self.media_presigner.enabled:
                raise AppError(
                    "VIDEO_ANALYSIS_STORAGE_NOT_CONFIGURED",
                    "视频画面分析需要对象存储临时读取地址",
                    503,
                )
            try:
                source_url = self.media_presigner.presigned_get(
                    self.asset_store.object_key(
                        workspace_id=task.workspace_id,
                        asset_id=asset.id,
                        suffix=".mp4",
                    ),
                    ttl_seconds=self.settings.tos_presign_url_ttl_seconds,
                )
            except PresignError as exc:
                raise AppError(
                    "VIDEO_ANALYSIS_URL_FAILED",
                    "无法生成视频临时读取地址，请重试",
                    503,
                ) from exc
            duration_seconds = float(video_ref.get("duration_seconds") or 0)

        self._advance(task_id, attempt_no, "正在读取视频画面与声音", 25)
        analyzer = getattr(self.transcription_provider, "analyze_video", None)
        if analyzer is None:
            raise TranscriptionProviderError(
                "VIDEO_ANALYSIS_NOT_CONFIGURED",
                "视频画面分析 Provider 尚未启用",
                retryable=False,
            )
        result = analyzer(
            TranscriptionRequest(
                duration_seconds=duration_seconds,
                mime_type="video/mp4",
                source_url=source_url,
            ),
            transcript=transcript,
        )
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is not None:
                session.add(
                    ProviderCall(
                        task_id=task.id,
                        workspace_id=task.workspace_id,
                        attempt_no=attempt_no,
                        provider=result.provider,
                        operation="breakdown_video_analysis",
                        provider_request_id=result.provider_request_id,
                        status="success",
                        elapsed_ms=result.elapsed_ms,
                        result_count=1,
                    )
                )
                session.commit()
        return result.analysis

    def _run_agent_chat(self, task_id: str, attempt_no: int) -> None:
        self._advance(task_id, attempt_no, "Riffloom 智能体正在回复", 45)
        self._sleep()
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            conversation = session.get(Conversation, task.conversation_id)
            if conversation is None:
                raise AppError(
                    "CONVERSATION_NOT_FOUND",
                    "Agent 对话不存在或已删除",
                    404,
                )
            attached = load_attachment_context(
                session,
                task.workspace_id,
                list(task.input_snapshot.get("attachment_ids") or []),
                self.asset_store,
            )
            fragments, _ = selected_knowledge_context(
                session,
                task=task,
                provider=self.generation_provider,
                settings=self.settings,
            )
            output, trace = call_model(
                session,
                task=task,
                attempt_no=attempt_no,
                provider=self.generation_provider,
                operation="agent_chat",
                contract_version="agent-chat.v2",
                payload={
                    "prompt": str(task.input_snapshot.get("prompt") or ""),
                    "conversation_history": conversation.messages[-20:],
                    "available_capabilities": list(_RIFFLOOM_AGENT_CAPABILITIES),
                    "knowledge_fragments": [item.model_dump() for item in fragments],
                    "attachment_text": attached["text"],
                    "media_refs": attached["media_refs"],
                    "source_ids": list(task.input_snapshot.get("source_ids") or []),
                },
                output_schema=AgentChatOutputV2,
            )
            child = None
            child_created = False
            action = output.action
            summary: dict[str, Any] = {
                "reply": output.reply,
                "provider": trace.provider,
                "model": trace.model,
                "contract_version": "agent-chat.v2",
                "agent_action": "reply",
            }
            if (
                action
                and action.name in _RIFFLOOM_AGENT_ACTIONS
                and action.name != "reply"
            ):
                source_ids = list(
                    dict.fromkeys(
                        [
                            *action.source_ids,
                            *list(task.input_snapshot.get("source_ids") or []),
                        ]
                    )
                )
                membership = session.scalar(
                    select(Membership).where(
                        Membership.workspace_id == task.workspace_id,
                        Membership.user_id == task.created_by,
                    )
                )
                user = session.get(User, task.created_by)
                workspace = session.get(Workspace, task.workspace_id)
                context = (
                    AuthContext(
                        user_id=task.created_by,
                        workspace_id=task.workspace_id,
                        role=membership.role,
                        user_name=user.name,
                        workspace_name=workspace.name,
                    )
                    if membership and user and workspace
                    else None
                )
                safe_prompt = (
                    action.prompt.strip()
                    or str(task.input_snapshot.get("prompt") or "").strip()
                )
                child_key = (
                    "agent-"
                    + hashlib.sha256(
                        f"{task.id}:{action.name}:{source_ids}:{safe_prompt}".encode(
                            "utf-8"
                        )
                    ).hexdigest()
                )
                if action.name in {"collect", "collect_rewrite"} and action.url:
                    summary.update(
                        {
                            "agent_action": "confirmation_required",
                            "confirmation": {
                                "type": action.name,
                                "collection_kind": action.collection_kind or "single",
                                "prompt": safe_prompt,
                            },
                        }
                    )
                elif context is not None:
                    try:
                        if action.name == "breakdown" and self._agent_sources_exist(
                            session,
                            task.workspace_id,
                            source_ids,
                            require_breakdown=False,
                        ):
                            child, child_created = create_task(
                                session,
                                context,
                                CreateTaskInput(
                                    mode="breakdown",
                                    skill_id="viral_breakdown",
                                    input={
                                        "prompt": safe_prompt,
                                        "knowledge_refs": list(
                                            task.input_snapshot.get("knowledge_refs")
                                            or []
                                        ),
                                    },
                                    source_ids=source_ids,
                                    attachment_ids=list(
                                        task.input_snapshot.get("attachment_ids") or []
                                    ),
                                ),
                                child_key,
                            )
                        elif action.name in {"original", "rewrite"} and (
                            action.name == "original"
                            or self._agent_sources_exist(
                                session,
                                task.workspace_id,
                                source_ids,
                                require_breakdown=True,
                            )
                        ):
                            child, child_created = create_task(
                                session,
                                context,
                                CreateTaskInput(
                                    mode="creation",
                                    skill_id=(
                                        "original_copy"
                                        if action.name == "original"
                                        else "copy_rewrite"
                                    ),
                                    input={
                                        "prompt": safe_prompt,
                                        "knowledge_refs": list(
                                            task.input_snapshot.get("knowledge_refs")
                                            or []
                                        ),
                                    },
                                    source_ids=source_ids,
                                    attachment_ids=list(
                                        task.input_snapshot.get("attachment_ids") or []
                                    ),
                                ),
                                child_key,
                            )
                        elif action.name == "trend" and safe_prompt:
                            child, child_created = create_trend_task(
                                session,
                                context,
                                TrendTaskInput(direction=safe_prompt),
                                child_key,
                            )
                        elif action.name == "collect_rewrite" and action.collection_id:
                            child, child_created, _ = create_collect_rewrite_task(
                                session,
                                context,
                                CollectRewriteTaskInput(
                                    collection_id=action.collection_id,
                                    prompt=safe_prompt,
                                    knowledge_refs=list(
                                        task.input_snapshot.get("knowledge_refs") or []
                                    ),
                                    attachment_ids=list(
                                        task.input_snapshot.get("attachment_ids") or []
                                    ),
                                ),
                                child_key,
                                self.provider,
                            )
                    except AppError:
                        child = None
            if child is not None:
                summary.update(
                    {
                        "agent_action": "delegated",
                        "delegated_task_id": child.id,
                        "delegated_skill_id": child.skill_id,
                    }
                )
                task.result_refs = [
                    *[ref for ref in task.result_refs if ref.get("type") != "task"],
                    {"type": "task", "id": child.id},
                ]
            elif action and summary["agent_action"] == "reply":
                summary["reply"] = (
                    f"{output.reply}\n\n这个动作缺少可用来源、权限或完整参数，我没有执行任何任务。"
                )
            with conversation_write_lock:
                session.refresh(conversation, attribute_names=["messages"])
                conversation.messages = [
                    *conversation.messages,
                    {"role": "assistant", "content": summary["reply"]},
                ]
                task.result_summary = summary
                session.commit()
        if child is not None and child_created:
            self.submit(child.id)
        self._complete_single(task_id, attempt_no, None, None, "Riffloom 智能体已回复")

    @staticmethod
    def _agent_sources_exist(
        session,
        workspace_id: str,
        source_ids: list[str],
        *,
        require_breakdown: bool,
    ) -> bool:
        if not source_ids:
            return False
        collection = session.scalar(
            select(CollectionRecord.id).where(
                CollectionRecord.workspace_id == workspace_id,
                CollectionRecord.id.in_(source_ids),
            )
        )
        if not collection:
            return False
        return not require_breakdown or bool(
            session.scalar(
                select(BreakdownRecord.id).where(
                    BreakdownRecord.workspace_id == workspace_id,
                    BreakdownRecord.id.in_(source_ids),
                )
            )
        )

    def _run_transcription_task(self, task_id: str, attempt_no: int) -> None:
        self._advance(task_id, attempt_no, "校验媒体权利与临时保留期", 20)
        self._sleep()
        self._advance(task_id, attempt_no, "火山豆包正在生成视频文案", 45)
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            record = execute_transcription_task(
                session,
                task=task,
                attempt_no=attempt_no,
                provider=self.transcription_provider,
                store=self.asset_store,
            )
        self._complete_single(
            task_id,
            attempt_no,
            "collection",
            record.id,
            "视频文案已生成并写入采集记录",
        )

    def _run_creation_task(self, task_id: str, attempt_no: int) -> None:
        self._advance(task_id, attempt_no, "检索已选择知识", 25)
        self._sleep()
        self._advance(task_id, attempt_no, "正在生成创作版本", 55)
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            record = create_creation_result(
                session,
                task=task,
                attempt_no=attempt_no,
                provider=self.generation_provider,
                settings=self.settings,
                asset_store=self.asset_store,
            )
        self._complete_single(
            task_id, attempt_no, "creation", record.id, "创作版本已入库"
        )

    def _run_topic_task(self, task_id: str, attempt_no: int) -> None:
        self._advance(task_id, attempt_no, "正在通过 TikHub 搜索近期热点", 25)
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            search_query = str(task.input_snapshot.get("search_query") or "").strip()
            window_days = int(task.input_snapshot.get("window_days") or 7)
            result_count = int(task.input_snapshot.get("result_count") or 5)
        search_trends = getattr(self.provider, "search_trends", None)
        if not callable(search_trends):
            raise AppError(
                "TREND_PROVIDER_NOT_CONFIGURED",
                "热点搜索需要启用 TikHub Provider",
                503,
            )
        try:
            batch = search_trends(
                search_query,
                window_days=window_days,
                limit=result_count,
            )
        except ProviderError as exc:
            self._record_provider_error(task_id, attempt_no, "trend.search", exc)
            raise
        self._record_provider_success(task_id, attempt_no, "trend.search", batch)
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            snapshot = snapshot_from_trend_batch(batch, window_days)
            task.input_snapshot = {
                **task.input_snapshot,
                "signal_snapshot": snapshot,
                "realtime_signal_verified": snapshot["freshness_verified"],
            }
            session.commit()
        self._advance(task_id, attempt_no, "正在生成热点选题分析", 60)
        self._sleep()
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            create_topic_result(
                session,
                task=task,
                attempt_no=attempt_no,
                provider=self.generation_provider,
            )
        self._complete_single(task_id, attempt_no, None, None, "热点选题分析已生成")

    def _complete_single(
        self,
        task_id: str,
        attempt_no: int,
        result_type: str | None,
        result_id: str | None,
        stage: str,
    ) -> None:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            if (
                result_type
                and result_id
                and not any(
                    ref.get("type") == result_type and ref.get("id") == result_id
                    for ref in task.result_refs
                )
            ):
                task.result_refs = [
                    *task.result_refs,
                    {"type": result_type, "id": result_id},
                ]
            now = utcnow()
            task.status = "success"
            task.stage = stage
            task.progress = 100
            task.finished_at = now
            task.error = None
            attempt = self._attempt(session, task.id, attempt_no)
            attempt.status = "success"
            attempt.stage = stage
            attempt.progress = 100
            attempt.finished_at = now
            with conversation_write_lock:
                self._append_conversation_reply(session, task, stage)
                add_audit(
                    session,
                    workspace_id=task.workspace_id,
                    user_id=task.created_by,
                    action="task.completed",
                    entity_type="AgentTask",
                    entity_id=task.id,
                    details={"attempt_no": attempt_no, "result_type": result_type},
                )
                session.commit()

    @staticmethod
    def _append_conversation_reply(session, task: AgentTask, content: str) -> None:
        if not task.conversation_id or task.skill_id == "riffloom_agent":
            return
        conversation = session.get(Conversation, task.conversation_id)
        if conversation is None:
            return
        session.refresh(conversation, attribute_names=["messages"])
        conversation.messages = [
            *conversation.messages,
            {"role": "assistant", "content": content},
        ]

    def _is_collection_task(self, task_id: str) -> bool:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            return bool(
                task
                and task.type
                in {
                    "collection_single",
                    "collection_keyword",
                    "collection_creator_content",
                    "collection_creator_profile",
                }
            )

    def _run_collection(self, task_id: str, attempt_no: int) -> None:
        self._advance(task_id, attempt_no, "校验采集输入与使用边界", 15)
        self._sleep()
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            snapshot = dict(task.input_snapshot)
            retry_keys = self._retry_keys(session, task.id, attempt_no)
        with self._lock:
            query = dict(self._collection_queries.get(task_id) or snapshot["query"])

        source_host = urlsplit(str(query.get("url", ""))).hostname
        platform = (
            "xiaohongshu"
            if snapshot["kind"] == "single"
            and source_host is not None
            and (
                source_host == "xiaohongshu.com"
                or source_host.endswith(".xiaohongshu.com")
            )
            else snapshot["platform"]
        )

        request = CollectionRequest(
            kind=snapshot["kind"],
            platform=platform,
            query=query,
            limit=int(snapshot.get("limit", 20)),
            attempt_no=attempt_no,
            retry_keys=tuple(retry_keys),
        )
        self._advance(
            task_id,
            attempt_no,
            (
                "合规沙箱正在采集"
                if self.provider.is_sandbox
                else "正在读取获权内容并下载媒体"
            ),
            30,
        )
        try:
            batch = self.provider.collect(request)
        except ProviderError as exc:
            self._record_provider_error(task_id, attempt_no, request.kind, exc)
            self._fail(
                task_id,
                attempt_no,
                status="failed",
                stage="采集数据源失败",
                code=exc.code,
                message=exc.message,
                progress=30,
                retryable=exc.retryable,
                details={"provider": self.provider.provider_id},
            )
            return

        batch = replace(
            batch,
            content_items=[
                (
                    self._transcribe_collected_video(task_id, attempt_no, item)
                    if self._should_materialize_collection(task_id, request, item)
                    else replace(item, downloaded_media=[])
                )
                for item in batch.content_items
            ],
        )
        self._record_provider_success(task_id, attempt_no, request.kind, batch)
        self._advance(task_id, attempt_no, "规范化与去重", 60)
        self._sleep()
        self._persist_collection_batch(task_id, attempt_no, request, batch)

    def _run_collect_rewrite(self, task_id: str, attempt_no: int) -> None:
        self._advance(task_id, attempt_no, "检查采集与生成检查点", 12)
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            snapshot = dict(task.input_snapshot)
            collection = next(
                (
                    session.scalar(
                        select(CollectionRecord).where(
                            CollectionRecord.id == ref.get("id"),
                            CollectionRecord.workspace_id == task.workspace_id,
                            CollectionRecord.deleted_at.is_(None),
                        )
                    )
                    for ref in task.result_refs
                    if ref.get("type") == "collection"
                ),
                None,
            )
            if collection is None and snapshot.get("collection_id"):
                collection = session.scalar(
                    select(CollectionRecord).where(
                        CollectionRecord.id == snapshot["collection_id"],
                        CollectionRecord.workspace_id == task.workspace_id,
                        CollectionRecord.deleted_at.is_(None),
                    )
                )
                if collection is None:
                    raise AppError(
                        "COLLECTION_RECORD_NOT_FOUND",
                        "采集记录不存在或不属于当前 Workspace",
                        404,
                    )
                task.result_refs = [
                    *task.result_refs,
                    {"type": "collection", "id": collection.id},
                ]
                session.commit()

        if collection is None:
            with self._lock:
                query = dict(self._collection_queries.get(task_id) or {})
            if (
                not query.get("url")
                and snapshot.get("url")
                and not snapshot.get("source_link_requires_resubmission")
            ):
                query = {"url": snapshot["url"]}
            if not query.get("url"):
                raise AppError(
                    "SOURCE_LINK_REQUIRED_AGAIN",
                    "服务恢复时采集尚未完成，请重新提交包含访问参数的完整分享链接",
                    409,
                )
            request = CollectionRequest(
                kind="single",
                platform="xiaohongshu",
                query=query,
                limit=1,
                attempt_no=attempt_no,
            )
            self._advance(task_id, attempt_no, "正在采集获权单篇内容", 25)
            try:
                batch = self.provider.collect(request)
            except ProviderError as exc:
                self._record_provider_error(task_id, attempt_no, request.kind, exc)
                raise AppError(
                    exc.code, exc.message, exc.status_code or 502, exc.retryable
                ) from exc
            batch = replace(
                batch,
                content_items=[
                    (
                        self._transcribe_collected_video(task_id, attempt_no, item)
                        if self._should_materialize_collection(task_id, request, item)
                        else replace(item, downloaded_media=[])
                    )
                    for item in batch.content_items
                ],
            )
            self._record_provider_success(task_id, attempt_no, request.kind, batch)
            refs = self._persist_collection_batch(
                task_id, attempt_no, request, batch, finalize=False
            )
            collection_id = next(
                (ref["id"] for ref in refs if ref.get("type") == "collection"), None
            )
            if not collection_id:
                raise AppError(
                    "COLLECTION_RESULT_EMPTY",
                    "单篇采集没有返回可用于拆解的内容",
                    502,
                )
        else:
            collection_id = collection.id

        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            breakdown = next(
                (
                    session.scalar(
                        select(BreakdownRecord).where(
                            BreakdownRecord.id == ref.get("id"),
                            BreakdownRecord.workspace_id == task.workspace_id,
                            BreakdownRecord.deleted_at.is_(None),
                        )
                    )
                    for ref in task.result_refs
                    if ref.get("type") == "breakdown"
                ),
                None,
            )
            source = session.get(CollectionRecord, collection_id)
            if breakdown is not None and source is not None:
                current = next(
                    (
                        version
                        for version in breakdown.versions
                        if version.id == breakdown.current_version_id
                    ),
                    None,
                )
                if current is None or not self._source_version_matches(
                    current.source_refs, "collection", collection_id, source.version
                ):
                    breakdown = None
            if breakdown is None:
                self._advance(task_id, attempt_no, "正在生成结构化拆解", 55)
                breakdown = create_breakdown_result(
                    session,
                    task=task,
                    attempt_no=attempt_no,
                    provider=self.generation_provider,
                    settings=self.settings,
                    asset_store=self.asset_store,
                    forced_collection_id=collection_id,
                )
            breakdown_id = breakdown.id
            breakdown_version = breakdown.version

        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            creation = next(
                (
                    session.scalar(
                        select(CreationRecord).where(
                            CreationRecord.id == ref.get("id"),
                            CreationRecord.workspace_id == task.workspace_id,
                            CreationRecord.deleted_at.is_(None),
                        )
                    )
                    for ref in task.result_refs
                    if ref.get("type") == "creation"
                ),
                None,
            )
            source = session.get(CollectionRecord, collection_id)
            if creation is not None and source is not None:
                current = next(
                    (
                        version
                        for version in creation.versions
                        if version.id == creation.current_version_id
                    ),
                    None,
                )
                if current is None or not (
                    self._source_version_matches(
                        current.source_refs, "collection", collection_id, source.version
                    )
                    and self._source_version_matches(
                        current.source_refs,
                        "breakdown",
                        breakdown_id,
                        breakdown_version,
                    )
                ):
                    creation = None
            if creation is None:
                self._advance(task_id, attempt_no, "正在生成仿写并检查重合风险", 82)
                creation = create_creation_result(
                    session,
                    task=task,
                    attempt_no=attempt_no,
                    provider=self.generation_provider,
                    settings=self.settings,
                    asset_store=self.asset_store,
                    forced_source_ids=[collection_id, breakdown_id],
                    forced_creation_type="rewrite",
                )
            creation_id = creation.id
        self._complete(task_id, attempt_no, collection_id, breakdown_id, creation_id)

    @staticmethod
    def _source_version_matches(
        refs: list[dict[str, Any]], kind: str, record_id: str, version: int
    ) -> bool:
        return any(
            ref.get("type") == kind
            and ref.get("id") == record_id
            and ref.get("version") == f"v{version}"
            for ref in refs
        )

    def _should_materialize_collection(
        self, task_id: str, request: CollectionRequest, item
    ) -> bool:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or bool(task.input_snapshot.get("refresh")):
                return True
            record = session.scalar(
                select(CollectionRecord).where(
                    CollectionRecord.workspace_id == task.workspace_id,
                    CollectionRecord.source_identity
                    == self._source_identity(request.platform, item.external_id),
                )
            )
            return (
                record is None
                or record.deleted_at is not None
                or record.provider == "user-upload-v1"
            )

    def _transcribe_collected_video(self, task_id: str, attempt_no: int, item):
        video = next(
            (media for media in item.downloaded_media if media.kind == "video"), None
        )
        if video is None or item.video_transcript_status != "required":
            return item
        if not self.transcription_provider.capabilities().get("enabled"):
            return item
        try:
            if self.media_presigner.enabled:
                with self.database.session_factory() as session:
                    task = session.get(AgentTask, task_id)
                    if task is None:
                        return item
                    item = self._store_collected_media(
                        session, task, item, retain_downloads=True
                    )
                    video_ref = next(
                        ref
                        for ref in item.media_refs
                        if ref["type"] == "video_attachment"
                    )
                    session.commit()
                    source_url = self.media_presigner.presigned_get(
                        self.asset_store.object_key(
                            workspace_id=task.workspace_id,
                            asset_id=video_ref["asset_id"],
                            suffix=".mp4",
                        ),
                        ttl_seconds=self.settings.tos_presign_url_ttl_seconds,
                    )
                request = TranscriptionRequest(
                    duration_seconds=video.duration_seconds,
                    mime_type=video.mime_type,
                    source_url=source_url,
                )
            else:
                request = TranscriptionRequest(
                    duration_seconds=video.duration_seconds,
                    mime_type=video.mime_type,
                    data_url=(
                        f"data:{video.mime_type};base64,"
                        f"{base64.b64encode(video.content).decode('ascii')}"
                    ),
                )
            result = self.transcription_provider.transcribe(request)
        except (PresignError, TranscriptionProviderError) as exc:
            logger.warning(
                "automatic_collection_transcription_failed",
                extra={
                    "task_id": task_id,
                    "attempt_no": attempt_no,
                    "error_type": getattr(exc, "code", "ASR_MEDIA_PREPARE_FAILED"),
                },
            )
            return replace(
                item,
                video_transcript_status="failed",
                video_transcript_source=getattr(
                    exc, "code", "ASR_MEDIA_PREPARE_FAILED"
                ),
            )
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is not None:
                session.add(
                    ProviderCall(
                        task_id=task.id,
                        workspace_id=task.workspace_id,
                        attempt_no=attempt_no,
                        provider=result.provider,
                        operation="automatic_collection_transcription",
                        provider_request_id=result.provider_request_id,
                        status="success",
                        elapsed_ms=result.elapsed_ms,
                        result_count=1,
                    )
                )
                session.commit()
        return replace(
            item,
            video_transcript=result.text,
            video_transcript_status="complete",
            video_transcript_source=result.model,
            video_transcript_confidence=result.confidence,
            video_transcript_segments=[
                {
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "text": segment.text,
                    **(
                        {"confidence": segment.confidence}
                        if segment.confidence is not None
                        else {}
                    ),
                }
                for segment in result.segments
            ],
        )

    @staticmethod
    def _retry_keys(session, task_id: str, attempt_no: int) -> list[str]:
        if attempt_no <= 1:
            return []
        return list(
            session.scalars(
                select(CollectionTaskItem.source_key).where(
                    CollectionTaskItem.task_id == task_id,
                    CollectionTaskItem.attempt_no == attempt_no - 1,
                    CollectionTaskItem.status == "failed",
                )
            ).all()
        )

    def _advance(
        self, task_id: str, attempt_no: int, stage: str, progress: int
    ) -> None:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            task.stage = stage
            task.progress = progress
            attempt = self._attempt(session, task_id, attempt_no)
            attempt.stage = stage
            attempt.progress = progress
            session.commit()

    def _record_provider_success(
        self, task_id: str, attempt_no: int, operation: str, batch: ProviderBatch
    ) -> None:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None:
                return
            if not batch.request_traces:
                session.add(
                    ProviderCall(
                        task_id=task.id,
                        workspace_id=task.workspace_id,
                        attempt_no=attempt_no,
                        provider=batch.provider,
                        operation=operation,
                        provider_request_id=batch.request_id,
                        status=(
                            "success" if not batch.item_errors else "partial_success"
                        ),
                        elapsed_ms=batch.elapsed_ms,
                        result_count=len(batch.content_items)
                        + len(batch.blogger_items),
                        rate_limit=batch.rate_limit,
                        cost=batch.cost,
                    )
                )
            for index, trace in enumerate(batch.request_traces):
                session.add(
                    ProviderCall(
                        task_id=task.id,
                        workspace_id=task.workspace_id,
                        attempt_no=attempt_no,
                        provider=batch.provider,
                        operation=trace.operation,
                        provider_request_id=trace.request_id or None,
                        status=trace.status,
                        elapsed_ms=batch.elapsed_ms if index == 0 else 0,
                        http_status=trace.http_status,
                        error_type=trace.error_type,
                        result_count=(
                            len(batch.content_items) + len(batch.blogger_items)
                            if index == 0
                            else 0
                        ),
                        rate_limit=batch.rate_limit if index == 0 else {},
                        cost=trace.cost,
                    )
                )
            session.commit()

    def _record_provider_error(
        self, task_id: str, attempt_no: int, operation: str, error: ProviderError
    ) -> None:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None:
                return
            session.add(
                ProviderCall(
                    task_id=task.id,
                    workspace_id=task.workspace_id,
                    attempt_no=attempt_no,
                    provider=self.provider.provider_id,
                    operation=operation,
                    provider_request_id=error.provider_request_id,
                    status="failed",
                    http_status=error.status_code,
                    error_type=error.code,
                )
            )
            session.commit()

    @staticmethod
    def _source_identity(platform: str, external_id: str) -> str:
        return hashlib.sha256(f"{platform}:{external_id}".encode("utf-8")).hexdigest()

    def _persist_collection_batch(
        self,
        task_id: str,
        attempt_no: int,
        request: CollectionRequest,
        batch: ProviderBatch,
        *,
        finalize: bool = True,
    ) -> list[dict[str, str]]:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return []
            refs = {(ref["type"], ref["id"]): ref for ref in (task.result_refs or [])}
            refresh = bool(task.input_snapshot.get("refresh"))
            errors_by_source = {error.source_key: error for error in batch.item_errors}

            for item in batch.content_items:
                item_error = errors_by_source.pop(item.external_id, None)
                item = self._store_collected_media(session, task, item)
                upload_fallback = batch.provider == "user-upload-v1"
                identity = self._source_identity(request.platform, item.external_id)
                record = session.scalar(
                    select(CollectionRecord).where(
                        CollectionRecord.workspace_id == task.workspace_id,
                        CollectionRecord.source_identity == identity,
                    )
                )
                is_new = record is None
                upgrade_fallback = bool(
                    record is not None
                    and record.provider == "user-upload-v1"
                    and batch.provider != "user-upload-v1"
                )
                if record is None:
                    needs_transcript = item.content_type in {
                        "视频",
                        "video",
                    } and item.video_transcript_status not in {
                        "complete",
                        "corrected",
                        "not_collected",
                    }
                    record = CollectionRecord(
                        workspace_id=task.workspace_id,
                        task_id=task.id,
                        source_identity=identity,
                        canonical_url=item.canonical_url,
                        platform=request.platform,
                        title=item.title,
                        body=item.body,
                        author=item.author_name,
                        collection_kind=request.kind,
                        external_id=item.external_id,
                        content_type=item.content_type,
                        published_at=item.published_at,
                        author_external_id=item.author_external_id,
                        cover_url=item.cover_url,
                        media_refs=item.media_refs,
                        topics=item.topics,
                        metrics=item.metrics,
                        provider=batch.provider,
                        actual_upstream=item.actual_upstream,
                        video_transcript=item.video_transcript,
                        video_transcript_corrected=item.video_transcript_corrected,
                        video_transcript_status=item.video_transcript_status,
                        video_transcript_source=item.video_transcript_source,
                        video_transcript_confidence=item.video_transcript_confidence,
                        video_transcript_segments=item.video_transcript_segments,
                        raw_schema_version="1.1-real-collection",
                        last_collected_at=utcnow(),
                        status="needs_transcript" if needs_transcript else "completed",
                        tags=list(
                            dict.fromkeys(
                                [
                                    (
                                        "用户上传"
                                        if upload_fallback
                                        else (
                                            "合规沙箱"
                                            if self.provider.is_sandbox
                                            else "真实采集"
                                        )
                                    ),
                                    *item.topics,
                                ]
                            )
                        ),
                    )
                    session.add(record)
                    session.flush()
                elif (
                    refresh
                    or upgrade_fallback
                    or item.external_id in request.retry_keys
                ):
                    record.task_id = task.id if upgrade_fallback else record.task_id
                    record.canonical_url = item.canonical_url
                    record.title = item.title
                    record.body = item.body
                    record.author = item.author_name
                    record.content_type = item.content_type
                    record.published_at = item.published_at
                    record.author_external_id = item.author_external_id
                    record.cover_url = item.cover_url
                    record.media_refs = item.media_refs
                    record.topics = item.topics
                    record.metrics = item.metrics
                    if item.video_transcript_status in {
                        "complete",
                        "corrected",
                    } or record.video_transcript_status not in {
                        "complete",
                        "corrected",
                    }:
                        record.video_transcript = item.video_transcript
                        record.video_transcript_corrected = (
                            item.video_transcript_corrected
                        )
                        record.video_transcript_status = item.video_transcript_status
                        record.video_transcript_source = item.video_transcript_source
                        record.video_transcript_confidence = (
                            item.video_transcript_confidence
                        )
                        record.video_transcript_segments = (
                            item.video_transcript_segments
                        )
                    record.actual_upstream = item.actual_upstream
                    record.provider = batch.provider
                    record.tags = list(dict.fromkeys(["真实采集", *item.topics]))
                    record.last_collected_at = utcnow()
                    record.version += 1
                record.deleted_at = None
                blocked_by_transcript = record.content_type in {
                    "视频",
                    "video",
                } and record.video_transcript_status not in {
                    "complete",
                    "corrected",
                    "not_collected",
                }
                record.status = (
                    "needs_transcript" if blocked_by_transcript else "completed"
                )
                session.add(
                    CollectionTaskItem(
                        task_id=task.id,
                        workspace_id=task.workspace_id,
                        attempt_no=attempt_no,
                        source_key=item.external_id,
                        entity_type="collection",
                        entity_id=record.id,
                        status=(
                            "failed"
                            if item_error is not None
                            or (blocked_by_transcript and not upload_fallback)
                            else "created" if is_new else "reused"
                        ),
                        is_new=is_new
                        and (upload_fallback or not blocked_by_transcript),
                        duplicate_of=None if is_new else record.id,
                        error=(
                            {
                                "code": item_error.code,
                                "message": item_error.message,
                                "retryable": item_error.retryable,
                            }
                            if item_error is not None
                            else (
                                {
                                    "code": item.video_transcript_source
                                    or "VIDEO_TRANSCRIPT_REQUIRED",
                                    "message": "视频与元数据已保存，但自动转写未完成；可重试任务或在详情页手动补齐",
                                    "retryable": False,
                                }
                                if blocked_by_transcript and not upload_fallback
                                else None
                            )
                        ),
                    )
                )
                refs[("collection", record.id)] = {
                    "type": "collection",
                    "id": record.id,
                }

            for item in batch.blogger_items:
                item_error = errors_by_source.pop(item.external_id, None)
                record = session.scalar(
                    select(BloggerRecord).where(
                        BloggerRecord.workspace_id == task.workspace_id,
                        BloggerRecord.platform == request.platform,
                        BloggerRecord.external_id == item.external_id,
                    )
                )
                is_new = record is None
                if record is None:
                    record = BloggerRecord(
                        workspace_id=task.workspace_id,
                        task_id=task.id,
                        platform=request.platform,
                        external_id=item.external_id,
                        profile_url=item.profile_url,
                        name=item.name,
                        avatar_url=item.avatar_url,
                        bio=item.bio,
                        followers=item.followers,
                        likes_and_collects=item.likes_and_collects,
                        tags=list(
                            dict.fromkeys(
                                [
                                    (
                                        "合规沙箱"
                                        if self.provider.is_sandbox
                                        else "真实采集"
                                    ),
                                    *item.tags,
                                ]
                            )
                        ),
                        provider=batch.provider,
                        last_collected_at=utcnow(),
                    )
                    session.add(record)
                    session.flush()
                elif refresh or item.external_id in request.retry_keys:
                    record.profile_url = item.profile_url
                    record.name = item.name
                    record.avatar_url = item.avatar_url
                    record.bio = item.bio
                    record.followers = item.followers
                    record.likes_and_collects = item.likes_and_collects
                    record.tags = list(
                        dict.fromkeys(
                            [
                                "合规沙箱" if self.provider.is_sandbox else "真实采集",
                                *item.tags,
                            ]
                        )
                    )
                    record.provider = batch.provider
                    record.last_collected_at = utcnow()
                record.deleted_at = None
                session.add(
                    CollectionTaskItem(
                        task_id=task.id,
                        workspace_id=task.workspace_id,
                        attempt_no=attempt_no,
                        source_key=item.external_id,
                        entity_type="blogger",
                        entity_id=record.id,
                        status=(
                            "failed"
                            if item_error is not None
                            else "created" if is_new else "reused"
                        ),
                        is_new=is_new,
                        duplicate_of=None if is_new else record.id,
                        error=(
                            {
                                "code": item_error.code,
                                "message": item_error.message,
                                "retryable": item_error.retryable,
                            }
                            if item_error is not None
                            else None
                        ),
                    )
                )
                refs[("blogger", record.id)] = {"type": "blogger", "id": record.id}

            for error in errors_by_source.values():
                session.add(
                    CollectionTaskItem(
                        task_id=task.id,
                        workspace_id=task.workspace_id,
                        attempt_no=attempt_no,
                        source_key=error.source_key,
                        status="failed",
                        is_new=False,
                        error={
                            "code": error.code,
                            "message": error.message,
                            "retryable": error.retryable,
                        },
                    )
                )
            session.flush()

            all_items = session.scalars(
                select(CollectionTaskItem)
                .where(CollectionTaskItem.task_id == task.id)
                .order_by(CollectionTaskItem.attempt_no, CollectionTaskItem.created_at)
            ).all()
            latest: dict[str, CollectionTaskItem] = {}
            for item in all_items:
                latest[item.source_key] = item
            summary: dict[str, Any] = {
                "new": sum(1 for item in latest.values() if item.status == "created"),
                "reused": sum(1 for item in latest.values() if item.status == "reused"),
                "failed": sum(1 for item in latest.values() if item.status == "failed"),
                "total": len(latest),
                "provider": batch.provider,
                "is_sandbox": batch.provider.startswith("sandbox"),
                "kind": request.kind,
            }
            successful = summary["new"] + summary["reused"]
            failed = summary["failed"]
            failed_items = [item for item in latest.values() if item.status == "failed"]
            failure_codes = {
                str((item.error or {}).get("code") or "PROVIDER_PARTIAL_FAILURE")
                for item in failed_items
            }
            retryable_failure = any(
                bool((item.error or {}).get("retryable")) for item in failed_items
            )
            if not finalize:
                task.result_refs = list(refs.values())
                task.result_summary = summary
                task.stage = "采集检查点已入库"
                task.progress = max(task.progress, 42)
                attempt = self._attempt(session, task.id, attempt_no)
                attempt.stage = task.stage
                attempt.progress = task.progress
                session.commit()
                return list(refs.values())
            now = utcnow()
            status = (
                "partial_success"
                if successful and failed
                else "failed" if failed else "success"
            )
            task.status = status
            task.stage = (
                "采集部分完成"
                if status == "partial_success"
                else "采集失败" if status == "failed" else "采集结果已入库"
            )
            task.progress = 100
            task.result_refs = list(refs.values())
            task.result_summary = summary
            task.finished_at = now
            task.error = (
                {
                    "code": (
                        next(iter(failure_codes))
                        if len(failure_codes) == 1
                        else "PROVIDER_PARTIAL_FAILURE"
                    ),
                    "message": (
                        str((failed_items[0].error or {}).get("message"))
                        if len(failed_items) == 1
                        else f"{failed} 个结果失败"
                    ),
                    "retryable": retryable_failure,
                    "details": {"failed": failed},
                    "trace_id": task.trace_id,
                }
                if failed
                else None
            )
            attempt = self._attempt(session, task.id, attempt_no)
            attempt.status = status
            attempt.stage = task.stage
            attempt.progress = 100
            attempt.finished_at = now
            attempt.error = task.error
            with conversation_write_lock:
                self._append_conversation_reply(
                    session,
                    task,
                    str((task.error or {}).get("message") or task.stage),
                )
                add_audit(
                    session,
                    workspace_id=task.workspace_id,
                    user_id=task.created_by,
                    action=(
                        "collection_task.completed"
                        if status == "success"
                        else "collection_task.partial"
                    ),
                    entity_type="AgentTask",
                    entity_id=task.id,
                    details={**summary, "attempt_no": attempt_no},
                )
                session.commit()
            return list(refs.values())

    def _store_collected_media(
        self, session, task: AgentTask, item, *, retain_downloads: bool = False
    ):
        if not item.downloaded_media:
            return item
        refs: list[dict[str, str]] = []
        cover_url = item.cover_url
        for index, media in enumerate(item.downloaded_media, 1):
            content_hash = hashlib.sha256(media.content).hexdigest()
            asset = session.scalar(
                select(MediaAsset).where(
                    MediaAsset.workspace_id == task.workspace_id,
                    MediaAsset.content_hash == content_hash,
                    MediaAsset.role == "reference",
                )
            )
            if asset is None:
                asset_id = new_id("asset")
                suffix = {
                    "image/png": ".png",
                    "image/webp": ".webp",
                    "video/mp4": ".mp4",
                }.get(media.mime_type, ".jpg")
                storage_key = self.asset_store.put(
                    workspace_id=task.workspace_id,
                    asset_id=asset_id,
                    suffix=suffix,
                    content=media.content,
                )
                asset = MediaAsset(
                    id=asset_id,
                    workspace_id=task.workspace_id,
                    storage_key=storage_key,
                    original_name=f"xiaohongshu-{item.external_id}-{index}{suffix}",
                    mime_type=media.mime_type,
                    byte_size=len(media.content),
                    width=media.width,
                    height=media.height,
                    role="reference",
                    rights_status="approved",
                    content_hash=content_hash,
                    created_by=task.created_by,
                )
                session.add(asset)
                session.flush()
            content_url = f"/api/v1/media-assets/{asset.id}/content"
            if cover_url is None and media.kind in {"cover", "image"}:
                cover_url = content_url
            refs.append(
                {
                    "type": f"{media.kind}_attachment",
                    "url": content_url,
                    "asset_id": asset.id,
                    "mime_type": asset.mime_type,
                    "byte_size": str(asset.byte_size),
                    "width": str(asset.width),
                    "height": str(asset.height),
                    **(
                        {"duration_seconds": f"{media.duration_seconds:.3f}"}
                        if media.duration_seconds
                        else {}
                    ),
                }
            )
        return replace(
            item,
            cover_url=cover_url,
            media_refs=refs,
            downloaded_media=item.downloaded_media if retain_downloads else [],
        )

    def _sleep(self) -> None:
        if self.step_delay > 0:
            time.sleep(self.step_delay)

    def _start(self, task_id: str) -> int | None:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or task.status != "queued":
                return None
            attempt_no = task.current_attempt
            attempt = session.scalar(
                select(TaskAttempt).where(
                    TaskAttempt.task_id == task.id,
                    TaskAttempt.attempt_no == attempt_no,
                )
            )
            if attempt is None:
                return None
            now = utcnow()
            task.status = "running"
            task.stage = "校验任务输入"
            task.progress = max(task.progress, 10)
            task.started_at = now
            attempt.status = "running"
            attempt.stage = task.stage
            attempt.progress = task.progress
            attempt.started_at = now
            add_audit(
                session,
                workspace_id=task.workspace_id,
                user_id=task.created_by,
                action="task.started",
                entity_type="AgentTask",
                entity_id=task.id,
                details={"attempt_no": attempt_no},
            )
            session.commit()
            return attempt_no

    def _current(self, task: AgentTask, attempt_no: int) -> bool:
        return task.current_attempt == attempt_no and task.status == "running"

    def _collection_stage(
        self, task_id: str, attempt_no: int
    ) -> CollectionRecord | None:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return None
            prompt = str(task.input_snapshot.get("prompt", "")).strip()
            url = next(
                (
                    token
                    for token in prompt.split()
                    if token.startswith(("http://", "https://"))
                ),
                "",
            )
            if url:
                parts = urlsplit(url)
                canonical_url = urlunsplit(
                    (
                        parts.scheme.lower(),
                        parts.netloc.lower(),
                        parts.path.rstrip("/"),
                        parts.query,
                        "",
                    )
                )
            else:
                canonical_url = f"https://example.invalid/riffloom/{task.id}"
            source_identity = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
            record = session.scalar(
                select(CollectionRecord).where(
                    CollectionRecord.workspace_id == task.workspace_id,
                    CollectionRecord.source_identity == source_identity,
                )
            )
            if record is None:
                record = CollectionRecord(
                    workspace_id=task.workspace_id,
                    task_id=task.id,
                    source_identity=source_identity,
                    canonical_url=canonical_url,
                    title="阶段 1 合成采集：从素材到可恢复工作流",
                    body="这是用于验证任务持久化、来源追溯与版本关系的合成内容，不代表真实平台采集结果。",
                    tags=["阶段 1", "合成数据"],
                )
                session.add(record)
                session.flush()
            task.stage = "采集记录已入库"
            task.progress = 35
            attempt = self._attempt(session, task.id, attempt_no)
            attempt.stage = task.stage
            attempt.progress = task.progress
            session.commit()
            session.refresh(record)
            return record

    def _should_fail(self, task_id: str, attempt_no: int) -> bool:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None:
                return False
            prompt = str(task.input_snapshot.get("prompt", ""))
            return attempt_no == 1 and (
                bool(task.input_snapshot.get("simulate_failure")) or "[fail]" in prompt
            )

    def _breakdown_stage(
        self, task_id: str, attempt_no: int, collection_id: str
    ) -> BreakdownRecord | None:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return None
            record = create_breakdown_result(
                session,
                task=task,
                attempt_no=attempt_no,
                provider=self.generation_provider,
                settings=self.settings,
                asset_store=self.asset_store,
                forced_collection_id=collection_id,
            )
            task.stage = "结构化拆解已入库"
            task.progress = 68
            attempt = self._attempt(session, task.id, attempt_no)
            attempt.stage = task.stage
            attempt.progress = task.progress
            session.commit()
            session.refresh(record)
            return record

    def _creation_stage(
        self, task_id: str, attempt_no: int, collection_id: str, breakdown_id: str
    ) -> CreationRecord | None:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return None
            record = create_creation_result(
                session,
                task=task,
                attempt_no=attempt_no,
                provider=self.generation_provider,
                settings=self.settings,
                asset_store=self.asset_store,
                forced_source_ids=[collection_id, breakdown_id],
                forced_creation_type="rewrite",
            )
            task.stage = "创作版本已入库"
            task.progress = 92
            attempt = self._attempt(session, task.id, attempt_no)
            attempt.stage = task.stage
            attempt.progress = task.progress
            session.commit()
            session.refresh(record)
            return record

    def _complete(
        self,
        task_id: str,
        attempt_no: int,
        collection_id: str,
        breakdown_id: str,
        creation_id: str,
    ) -> None:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            now = utcnow()
            task.status = "success"
            task.stage = "结果已入库"
            task.progress = 100
            task.finished_at = now
            task.error = None
            task.result_refs = [
                {"type": "collection", "id": collection_id},
                {"type": "breakdown", "id": breakdown_id},
                {"type": "creation", "id": creation_id},
            ]
            attempt = self._attempt(session, task.id, attempt_no)
            attempt.status = "success"
            attempt.stage = task.stage
            attempt.progress = 100
            attempt.finished_at = now
            with conversation_write_lock:
                self._append_conversation_reply(session, task, task.stage)
                add_audit(
                    session,
                    workspace_id=task.workspace_id,
                    user_id=task.created_by,
                    action="task.completed",
                    entity_type="AgentTask",
                    entity_id=task.id,
                    details={"attempt_no": attempt_no, "result_count": 3},
                )
                session.commit()

    def _fail(
        self,
        task_id: str,
        attempt_no: int,
        *,
        status: str,
        stage: str,
        code: str,
        message: str,
        progress: int,
        retryable: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.database.session_factory() as session:
            task = session.get(AgentTask, task_id)
            if task is None or not self._current(task, attempt_no):
                return
            now = utcnow()
            error = {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details or {},
                "trace_id": task.trace_id,
            }
            task.status = status
            task.stage = stage
            task.progress = max(progress, task.progress)
            task.error = error
            task.finished_at = now
            attempt = self._attempt(session, task.id, attempt_no)
            attempt.status = status
            attempt.stage = stage
            attempt.progress = task.progress
            attempt.error = error
            attempt.finished_at = now
            with conversation_write_lock:
                self._append_conversation_reply(session, task, message)
                add_audit(
                    session,
                    workspace_id=task.workspace_id,
                    user_id=task.created_by,
                    action="task.failed",
                    entity_type="AgentTask",
                    entity_id=task.id,
                    details={"attempt_no": attempt_no, "error_code": code},
                )
                session.commit()

    @staticmethod
    def _attempt(session, task_id: str, attempt_no: int) -> TaskAttempt:
        attempt = session.scalar(
            select(TaskAttempt).where(
                TaskAttempt.task_id == task_id,
                TaskAttempt.attempt_no == attempt_no,
            )
        )
        if attempt is None:
            raise RuntimeError("task attempt missing")
        return attempt
