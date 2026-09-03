from __future__ import annotations

import hashlib
import json
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.core.errors import AppError
from app.models import (
    AgentTask,
    BreakdownRecord,
    BreakdownVersion,
    CollectionRecord,
    CreationRecord,
    CreationVersion,
)
from app.providers.models import GenerationProvider
from app.schemas.generation import (
    BreakdownOutputV1,
    CreationOutputV1,
    KnowledgeFragment,
    KnowledgeSnapshotV1,
    TopicGuidanceOutputV3,
)
from app.services.knowledge_service import search_knowledge
from app.services.model_service import call_model
from app.services.skill_service import get_skill, skill_snapshot
from app.services.asset_store import AssetStore
from app.services.attachment_service import load_attachment_context


def _snapshot_hash(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _collection_source(
    session: Session, workspace_id: str, source_ids: list[str]
) -> CollectionRecord | None:
    if not source_ids:
        return None
    return session.scalar(
        select(CollectionRecord).where(
            CollectionRecord.workspace_id == workspace_id,
            CollectionRecord.id.in_(source_ids),
            CollectionRecord.deleted_at.is_(None),
        )
    )


def _breakdown_source(
    session: Session, workspace_id: str, source_ids: list[str]
) -> BreakdownRecord | None:
    if not source_ids:
        return None
    return session.scalar(
        select(BreakdownRecord)
        .options(selectinload(BreakdownRecord.versions))
        .where(
            BreakdownRecord.workspace_id == workspace_id,
            BreakdownRecord.id.in_(source_ids),
            BreakdownRecord.deleted_at.is_(None),
        )
    )


def _collection_source_text(source: CollectionRecord) -> str:
    content_type = (source.content_type or "").strip().lower()
    is_video = content_type in {"video", "视频"}
    transcript = (source.video_transcript_corrected or "").strip() or (
        source.video_transcript or ""
    ).strip()
    if is_video and (
        not transcript or source.video_transcript_status not in {"complete", "corrected"}
    ):
        raise AppError(
            "VIDEO_TRANSCRIPT_REQUIRED",
            "视频采集记录缺少完整视频文案，请先上传已确认有权分析的媒体并完成转写",
            422,
        )
    parts = [source.body.strip()]
    if transcript:
        parts.append(f"视频文案：\n{transcript}")
    return "\n\n".join(part for part in parts if part)


def selected_knowledge_context(
    session: Session,
    *,
    task: AgentTask,
    provider: GenerationProvider,
    settings: Settings,
) -> tuple[list[KnowledgeFragment], KnowledgeSnapshotV1]:
    knowledge_refs = list(task.input_snapshot.get("knowledge_refs") or [])
    aliases = {
        "collection": "collections",
        "collections": "collections",
        "breakdown": "breakdowns",
        "breakdowns": "breakdowns",
        "creation": "creations",
        "creations": "creations",
    }
    libraries = list(
        dict.fromkeys(
            aliases[str(ref.get("library_type") or ref.get("type"))]
            for ref in knowledge_refs
            if str(ref.get("library_type") or ref.get("type")) in aliases
        )
    )
    refs = [
        {
            "type": str(ref.get("type") or str(ref.get("library_type", "")).rstrip("s")),
            "id": str(ref.get("id") or ref.get("record_id") or ""),
        }
        for ref in knowledge_refs
    ]
    return search_knowledge(
        session,
        workspace_id=task.workspace_id,
        provider=provider,
        query=str(task.input_snapshot.get("prompt") or "").strip(),
        selected_libraries=libraries,
        selected_refs=refs,
        top_k=settings.rag_top_k_final,
        max_chars=settings.rag_max_context_chars,
    )


def create_breakdown_result(
    session: Session,
    *,
    task: AgentTask,
    attempt_no: int,
    provider: GenerationProvider,
    settings: Settings,
    asset_store: AssetStore,
    forced_collection_id: str | None = None,
    video_analysis: dict[str, Any] | None = None,
) -> BreakdownRecord:
    skill = get_skill("viral_breakdown")
    source_ids = list(task.input_snapshot.get("source_ids") or [])
    if forced_collection_id and forced_collection_id not in source_ids:
        source_ids.insert(0, forced_collection_id)
    source = _collection_source(session, task.workspace_id, source_ids)
    prompt = str(task.input_snapshot.get("prompt") or "").strip()
    attached = load_attachment_context(
        session,
        task.workspace_id,
        list(task.input_snapshot.get("attachment_ids") or []),
        asset_store,
    )
    source_text = "\n\n".join(
        part
        for part in (
            (_collection_source_text(source) if source else prompt),
            str(attached["text"]),
        )
        if part
    )
    if not source_text:
        raise AppError("SOURCE_REQUIRED", "拆解需要采集记录或临时正文", 422)
    source_refs = [
        *(
            [{"type": "collection", "id": source.id, "version": f"v{source.version}"}]
            if source
            else [
                {
                    "type": "temporary_text",
                    "hash": hashlib.sha256(source_text.encode()).hexdigest(),
                }
            ]
        ),
        *attached["refs"],
    ]
    knowledge_fragments, _ = selected_knowledge_context(
        session, task=task, provider=provider, settings=settings
    )
    compact = task.input_snapshot.get("preset") == "drawer_compact"
    payload = {
        "prompt": prompt,
        "source_title": source.title if source else "临时输入",
        "source_text": source_text,
        "source_refs": source_refs,
        "media_refs": [*(source.media_refs if source else []), *attached["media_refs"]],
        "knowledge_fragments": [item.model_dump() for item in knowledge_fragments],
        "metrics": source.metrics if source else {},
        "video_analysis": video_analysis or {},
        "output_preferences": (
            {
                "language": "zh-CN",
                "style": "concise",
                "target_length": "800-1500字",
                "observed_facts_max": 4,
                "structure_max": 6,
                "interaction_max": 2,
                "reusable_methods_max": 4,
                "risks_max": 2,
            }
            if compact
            else {}
        ),
        "constraints": {
            "separate_observation_and_judgment": True,
            "do_not_infer_unseen_visuals": True,
            "do_not_copy_original_expression": True,
        },
    }
    output, trace = call_model(
        session,
        task=task,
        attempt_no=attempt_no,
        provider=provider,
        operation="breakdown",
        contract_version="breakdown.v1",
        payload=payload,
        output_schema=BreakdownOutputV1,
    )
    record = session.scalar(select(BreakdownRecord).where(BreakdownRecord.task_id == task.id))
    if record is None:
        record = BreakdownRecord(
            workspace_id=task.workspace_id,
            task_id=task.id,
            source_record_id=source.id if source else None,
            title=output.title,
            status="completed",
            hook=output.hook.expression,
            structure=output.structure,
            emotion=output.emotion.target,
            visual="；".join([*output.visual.observed, *output.visual.limitations]),
            reusable_methods=output.reusable_methods,
            skill_version=skill.version,
            version=0,
        )
        session.add(record)
        session.flush()
    next_version = (
        int(
            session.scalar(
                select(func.max(BreakdownVersion.version)).where(
                    BreakdownVersion.record_id == record.id
                )
            )
            or 0
        )
        + 1
    )
    version = BreakdownVersion(
        workspace_id=task.workspace_id,
        record_id=record.id,
        version=next_version,
        source_refs=source_refs,
        source_snapshot={
            "title": source.title if source else "临时输入",
            "body_hash": hashlib.sha256(source_text.encode()).hexdigest(),
            "excerpt": source_text[:240],
            "video_analysis": video_analysis or {},
        },
        observed_facts=output.observed_facts,
        hook=output.hook.model_dump(),
        structure=output.structure,
        emotion=output.emotion.model_dump(),
        visual=output.visual.model_dump(),
        interaction=output.interaction,
        reusable_methods=output.reusable_methods,
        risks=output.risks,
        skill_snapshot=skill_snapshot(skill),
        model_call_id=trace.id,
        created_by=task.created_by,
    )
    session.add(version)
    session.flush()
    record.current_version_id = version.id
    record.version = version.version
    record.title = output.title
    record.hook = output.hook.expression
    record.structure = output.structure
    record.emotion = output.emotion.target
    record.visual = "；".join([*output.visual.observed, *output.visual.limitations])
    record.reusable_methods = output.reusable_methods
    task.result_refs = [
        *[ref for ref in task.result_refs if ref.get("type") != "breakdown"],
        {"type": "breakdown", "id": record.id},
    ]
    task.result_summary = {
        "kind": "breakdown",
        "version": version.version,
        "model": trace.model,
        "provider": trace.provider,
        "source_count": len(source_refs),
    }
    session.commit()
    session.refresh(record)
    return record


def _source_context(
    session: Session, workspace_id: str, source_ids: list[str]
) -> tuple[list[dict[str, str]], str, BreakdownRecord | None]:
    collection = _collection_source(session, workspace_id, source_ids)
    breakdown = _breakdown_source(session, workspace_id, source_ids)
    refs: list[dict[str, str]] = []
    texts: list[str] = []
    if collection:
        refs.append(
            {
                "type": "collection",
                "id": collection.id,
                "version": f"v{collection.version}",
            }
        )
        texts.append(_collection_source_text(collection))
    if breakdown:
        version = next(
            (item for item in breakdown.versions if item.id == breakdown.current_version_id),
            breakdown.versions[-1] if breakdown.versions else None,
        )
        refs.append(
            {
                "type": "breakdown",
                "id": breakdown.id,
                "version": f"v{version.version if version else breakdown.version}",
            }
        )
        if version:
            texts.append("\n".join([*version.structure, *version.reusable_methods]))
    return refs, "\n\n".join(texts), breakdown


def similarity_report(body: str, source_text: str) -> dict[str, Any]:
    normalized_body = "".join(body.lower().split())
    normalized_source = "".join(source_text.lower().split())
    if not normalized_source:
        return {
            "algorithm": "sequence-ratio.v1",
            "score": 0.0,
            "level": "none",
            "note": "无可比较来源",
        }
    score = SequenceMatcher(None, normalized_body, normalized_source, autojunk=False).ratio()
    level = "high" if score >= 0.55 else "medium" if score >= 0.32 else "low"
    return {
        "algorithm": "sequence-ratio.v1",
        "score": round(score, 4),
        "level": level,
        "note": "仅作为文本重合风险信号，不构成版权或侵权判断",
    }


def create_creation_result(
    session: Session,
    *,
    task: AgentTask,
    attempt_no: int,
    provider: GenerationProvider,
    settings: Settings,
    asset_store: AssetStore,
    forced_source_ids: list[str] | None = None,
    forced_creation_type: str | None = None,
) -> CreationRecord:
    skill = get_skill(task.skill_id)
    source_ids = list(task.input_snapshot.get("source_ids") or [])
    for source_id in forced_source_ids or []:
        if source_id not in source_ids:
            source_ids.append(source_id)
    creation_type = forced_creation_type or (
        "rewrite" if task.skill_id in {"copy_rewrite", "collect_breakdown_rewrite"} else "original"
    )
    source_refs, source_text, breakdown = _source_context(session, task.workspace_id, source_ids)
    attached = load_attachment_context(
        session,
        task.workspace_id,
        list(task.input_snapshot.get("attachment_ids") or []),
        asset_store,
    )
    source_refs.extend(attached["refs"])
    source_text = "\n\n".join(part for part in (source_text, str(attached["text"])) if part)
    trend_task_id = str(task.input_snapshot.get("trend_task_id") or "").strip()
    trend_context: dict[str, Any] = {}
    if trend_task_id:
        trend_task = session.scalar(
            select(AgentTask).where(
                AgentTask.id == trend_task_id,
                AgentTask.workspace_id == task.workspace_id,
                AgentTask.skill_id == "viral_topic_coach",
                AgentTask.status == "success",
            )
        )
        if trend_task is None:
            raise AppError(
                "TREND_TASK_NOT_FOUND",
                "选题任务不存在、未完成或不属于当前 Workspace",
                404,
            )
        source_refs.append(
            {
                "type": "trend_task",
                "id": trend_task.id,
                "version": str(
                    trend_task.result_summary.get("contract_version", "topic-guidance.v3")
                ),
            }
        )
        trend_context = {
            "task_id": trend_task.id,
            "candidates": trend_task.result_summary.get("candidates", []),
            "signal_snapshot": trend_task.result_summary.get("signal_snapshot", {}),
        }
    if creation_type == "rewrite" and (not source_refs or breakdown is None):
        raise AppError(
            "SOURCE_REQUIRED",
            "文案仿写必须同时选择来源内容与拆解依据",
            422,
        )
    prompt = str(task.input_snapshot.get("prompt") or "").strip()
    if not prompt:
        raise AppError("CREATION_REQUEST_REQUIRED", "创作要求不能为空", 422)
    fragments, snapshot = selected_knowledge_context(
        session, task=task, provider=provider, settings=settings
    )
    payload = {
        "prompt": prompt,
        "creation_type": creation_type,
        "target_platform": task.input_snapshot.get("target_platform", "小红书"),
        "audience": task.input_snapshot.get("audience", "由用户要求推断，需人工审核"),
        "source_refs": source_refs,
        "source_strategy": source_text,
        "trend_context": trend_context,
        "knowledge_fragments": [item.model_dump() for item in fragments],
        "media_refs": attached["media_refs"],
        "constraints": {
            "reuse_structure_not_protected_expression": creation_type == "rewrite",
            "external_facts_require_sources": True,
            "return_risks": True,
        },
    }
    output, trace = call_model(
        session,
        task=task,
        attempt_no=attempt_no,
        provider=provider,
        operation="creation",
        contract_version="creation.v1",
        payload=payload,
        output_schema=CreationOutputV1,
    )
    record = session.scalar(
        select(CreationRecord)
        .options(selectinload(CreationRecord.versions))
        .where(CreationRecord.task_id == task.id)
    )
    if record is None:
        record = CreationRecord(
            workspace_id=task.workspace_id,
            task_id=task.id,
            title=output.title_candidates[0],
            creation_type=creation_type,
            status="pending_review",
            source_refs=source_refs,
            skill_id=task.skill_id,
            created_by=task.created_by,
        )
        session.add(record)
        session.flush()
        current_versions: list[CreationVersion] = []
    else:
        current_versions = record.versions
    next_version = max((item.version for item in current_versions), default=0) + 1
    overlap = (
        similarity_report(output.body, source_text)
        if creation_type == "rewrite"
        else similarity_report(output.body, "")
    )
    version = CreationVersion(
        workspace_id=task.workspace_id,
        record_id=record.id,
        version=next_version,
        body=output.body,
        topics=output.topics,
        change_note="AI 生成" if next_version == 1 else "AI 重试生成新版本",
        knowledge_snapshot=snapshot.model_dump(),
        generation_config={
            "provider": trace.provider,
            "model": trace.model,
            "contract": "creation.v1",
            "skill": skill_snapshot(skill),
        },
        title_candidates=output.title_candidates,
        summary=output.summary,
        risk_notes=output.risk_notes,
        source_refs=source_refs,
        model_call_id=trace.id,
        similarity_report=overlap,
        input_snapshot_hash=_snapshot_hash(task.input_snapshot),
        created_by=task.created_by,
    )
    session.add(version)
    session.flush()
    record.current_version_id = version.id
    record.title = output.title_candidates[0]
    record.status = "pending_review"
    task.result_refs = [
        *[ref for ref in task.result_refs if ref.get("type") != "creation"],
        {"type": "creation", "id": record.id},
    ]
    task.result_summary = {
        "kind": "creation",
        "creation_type": creation_type,
        "version": version.version,
        "model": trace.model,
        "provider": trace.provider,
        "knowledge_count": len(fragments),
        "similarity_level": overlap["level"],
    }
    session.commit()
    session.refresh(record)
    return record


def create_topic_result(
    session: Session,
    *,
    task: AgentTask,
    attempt_no: int,
    provider: GenerationProvider,
) -> TopicGuidanceOutputV3:
    prompt = str(task.input_snapshot.get("prompt") or "").strip()
    if not prompt:
        raise AppError("TOPIC_INPUT_REQUIRED", "选题指导需要行业、账号方向或目标人群", 422)
    signal_snapshot = task.input_snapshot.get("signal_snapshot") or {}
    snapshot_items = [item for item in signal_snapshot.get("items", []) if isinstance(item, dict)]
    if not snapshot_items:
        raise AppError(
            "TREND_SIGNAL_REQUIRED",
            "TikHub 未返回可核验的近一周热点，请稍后重试",
            503,
        )
    result_count = min(15, max(1, int(task.input_snapshot.get("result_count") or 5)))
    output, trace = call_model(
        session,
        task=task,
        attempt_no=attempt_no,
        provider=provider,
        operation="topic_guidance",
        contract_version="topic-guidance.v3",
        payload={
            "prompt": prompt,
            "direction": task.input_snapshot.get("direction", prompt),
            "audience": task.input_snapshot.get("audience", ""),
            "keywords": task.input_snapshot.get("keywords", []),
            "window_days": task.input_snapshot.get("window_days", 7),
            "result_count": result_count,
            "signal_snapshot": signal_snapshot,
            "realtime_signal_verified": True,
        },
        output_schema=TopicGuidanceOutputV3,
    )
    allowed_signals = {
        (
            str(item.get("platform")),
            str(item.get("source_id")),
            str(item.get("observed_at")),
        )
        for item in snapshot_items
    }
    cited_count = 0
    output.candidates = output.candidates[:result_count]
    for candidate in output.candidates:
        candidate.sources = [
            citation
            for citation in candidate.sources
            if (
                citation.platform,
                citation.source_id,
                citation.observed_at,
            )
            in allowed_signals
        ]
        cited_count += len(candidate.sources)
    if len(output.candidates) != result_count or any(
        not candidate.sources for candidate in output.candidates
    ):
        raise AppError(
            "TOPIC_OUTPUT_INVALID",
            "热点分析结果数量或来源校验失败，请重试",
            502,
        )
    output.caveat = (
        "当前为合规沙箱热点，仅用于验证流程。"
        if signal_snapshot.get("provider") == "sandbox-v1"
        else f"已基于 TikHub 聚合的近 {signal_snapshot.get('window_days', 7)} 天公开内容信号生成。"
    )
    task.result_summary = {
        "kind": "topic_guidance",
        "contract_version": "topic-guidance.v3",
        "model": trace.model,
        "provider": trace.provider,
        "candidates": [item.model_dump() for item in output.candidates],
        "caveat": output.caveat,
        "signal_snapshot": signal_snapshot,
        "signal_count": len(snapshot_items),
        "citation_count": cited_count,
        "freshness": "verified_external_signals",
    }
    session.commit()
    return output


def add_manual_creation_version(
    session: Session,
    *,
    workspace_id: str,
    user_id: str,
    record_id: str,
    base_version_id: str,
    body: str,
    change_note: str,
) -> CreationVersion:
    record = session.scalar(
        select(CreationRecord)
        .options(selectinload(CreationRecord.versions))
        .where(CreationRecord.id == record_id, CreationRecord.workspace_id == workspace_id)
        .where(CreationRecord.deleted_at.is_(None))
    )
    if record is None:
        raise AppError("CREATION_NOT_FOUND", "当前 Workspace 中不存在该创作记录", 404)
    base = next((item for item in record.versions if item.id == base_version_id), None)
    if base is None:
        raise AppError("VERSION_NOT_FOUND", "基础版本不存在或不属于该记录", 404)
    version = CreationVersion(
        workspace_id=workspace_id,
        record_id=record.id,
        version=max(item.version for item in record.versions) + 1,
        body=body,
        topics=base.topics,
        change_note=change_note,
        knowledge_snapshot=base.knowledge_snapshot,
        generation_config={**base.generation_config, "editor": "manual"},
        title_candidates=base.title_candidates,
        summary=body[:240],
        risk_notes=base.risk_notes,
        source_refs=base.source_refs,
        similarity_report=base.similarity_report,
        input_snapshot_hash=hashlib.sha256(body.encode()).hexdigest(),
        created_by=user_id,
    )
    session.add(version)
    session.flush()
    record.current_version_id = version.id
    record.status = "pending_review"
    session.commit()
    session.refresh(version)
    return version
