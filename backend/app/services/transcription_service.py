from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import AuthContext
from app.core.errors import AppError
from app.core.observability import current_trace_id, new_trace_id
from app.models import (
    AgentTask,
    CollectionRecord,
    MediaAsset,
    ProviderCall,
    TaskAttempt,
)
from app.models.entities import new_id, utcnow
from app.providers import (
    TranscriptionProvider,
    TranscriptionProviderError,
    TranscriptionRequest,
)
from app.schemas.api import (
    TranscriptionTaskInput,
    TranscriptionUploadInitInput,
    TranscriptionUploadInitRead,
)
from app.services.asset_store import AssetStore
from app.services.task_service import add_audit
from app.services.tos_presign import UploadPresigner


RAW_RETENTION_HOURS = 24


def _as_utc(value: datetime) -> datetime:
    # SQLite drops timezone metadata even for DateTime(timezone=True).
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _decode_media(payload: TranscriptionTaskInput, max_bytes: int) -> bytes:
    prefix = f"data:{payload.mime_type};base64,"
    try:
        content = base64.b64decode(payload.data_url[len(prefix) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError("TRANSCRIPTION_MEDIA_INVALID", "媒体 Base64 内容无效", 422) from exc
    if not content or len(content) > max_bytes:
        raise AppError(
            "TRANSCRIPTION_MEDIA_SIZE_INVALID",
            f"转写媒体大小必须在 1 字节到 {max_bytes} 字节之间",
            422,
        )
    signatures = {
        "audio/mpeg": lambda data: data.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")),
        "audio/mp3": lambda data: data.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")),
        "audio/wav": lambda data: data.startswith(b"RIFF") and data[8:12] == b"WAVE",
        "audio/x-wav": lambda data: data.startswith(b"RIFF") and data[8:12] == b"WAVE",
        "audio/mp4": lambda data: len(data) >= 12 and data[4:8] == b"ftyp",
        "audio/x-m4a": lambda data: len(data) >= 12 and data[4:8] == b"ftyp",
        "video/mp4": lambda data: len(data) >= 12 and data[4:8] == b"ftyp",
    }
    if not signatures[payload.mime_type](content):
        raise AppError(
            "TRANSCRIPTION_MEDIA_DECODE_FAILED",
            "媒体文件头与声明类型不一致，已拒绝上传",
            422,
        )
    return content


def _suffix(mime_type: str) -> str:
    return {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "video/mp4": ".mp4",
    }[mime_type]


def create_transcription_upload(
    session: Session,
    context: AuthContext,
    *,
    payload: TranscriptionUploadInitInput,
    store: AssetStore,
    presigner: UploadPresigner,
    max_bytes: int,
    ttl_seconds: int,
) -> TranscriptionUploadInitRead:
    if not payload.rights_confirmed:
        raise AppError("TRANSCRIPTION_RIGHTS_REQUIRED", "必须确认媒体使用权利", 422)
    if not presigner.enabled:
        raise AppError(
            "TOS_PRESIGN_DISABLED",
            "TOS 预签名直传未启用，请使用本机 base64 上传",
            409,
        )
    if payload.byte_size > max_bytes:
        raise AppError(
            "TRANSCRIPTION_MEDIA_SIZE_INVALID",
            f"转写媒体大小不能超过 {max_bytes} 字节",
            422,
        )
    now = utcnow()
    asset_id = new_id("asset")
    suffix = _suffix(payload.mime_type)
    object_key = store.object_key(
        workspace_id=context.workspace_id, asset_id=asset_id, suffix=suffix
    )
    storage_key = store.storage_key(
        workspace_id=context.workspace_id, asset_id=asset_id, suffix=suffix
    )
    presigned_url: str | None = None
    if presigner.enabled:
        presigned_url = presigner.presigned_put(
            object_key, content_type=payload.mime_type, ttl_seconds=ttl_seconds
        )
    if presigned_url is None:
        raise AppError("TOS_PRESIGN_FAILED", "TOS 预签名 URL 生成失败", 503)
    asset = MediaAsset(
        id=asset_id,
        workspace_id=context.workspace_id,
        storage_key=storage_key,
        original_name=Path(payload.filename).name[:240],
        mime_type=payload.mime_type,
        byte_size=payload.byte_size,
        width=0,
        height=0,
        role="transcription_source",
        rights_status="approved",
        content_hash="",
        created_by=context.user_id,
        expires_at=now + timedelta(hours=RAW_RETENTION_HOURS),
    )
    session.add(asset)
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="transcription_upload.initialized",
        entity_type="MediaAsset",
        entity_id=asset_id,
        details={
            "object_key": object_key,
            "mime_type": payload.mime_type,
            "byte_size": payload.byte_size,
            "upload_mode": "tos_presign" if presigned_url else "base64",
        },
    )
    session.commit()
    return TranscriptionUploadInitRead(
        asset_id=asset_id,
        object_key=object_key,
        presigned_put_url=presigned_url,
        expires_in_seconds=max(60, ttl_seconds),
        upload_mode="tos_presign" if presigned_url else "base64",
    )


def create_transcription_task(
    session: Session,
    context: AuthContext,
    *,
    collection_id: str,
    payload: TranscriptionTaskInput,
    idempotency_key: str,
    provider: TranscriptionProvider,
    store: AssetStore,
    max_bytes: int,
) -> tuple[AgentTask, bool]:
    capabilities = provider.capabilities()
    if not capabilities.get("enabled"):
        raise AppError("ASR_NOT_CONFIGURED", "视频转写 Provider 尚未启用", 503)
    record = session.scalar(
        select(CollectionRecord).where(
            CollectionRecord.id == collection_id,
            CollectionRecord.workspace_id == context.workspace_id,
            CollectionRecord.deleted_at.is_(None),
        )
    )
    if record is None:
        raise AppError("COLLECTION_RECORD_NOT_FOUND", "当前 Workspace 中不存在该采集记录", 404)
    if record.collection_kind != "single":
        raise AppError(
            "FULL_COLLECTION_REQUIRED",
            "视频文案只能写入单篇采集记录；请先对搜索结果执行单篇采集",
            422,
        )

    now = utcnow()
    if payload.asset_id is not None:
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.id == payload.asset_id,
                MediaAsset.workspace_id == context.workspace_id,
            )
        )
        if asset is None or asset.role != "transcription_source":
            raise AppError(
                "TRANSCRIPTION_ASSET_NOT_FOUND",
                "转写媒体资产不存在或不可用",
                404,
            )
        if asset.purged_at is not None:
            raise AppError(
                "TRANSCRIPTION_MEDIA_EXPIRED", "转写原媒体已清除，请重新上传", 410
            )
        if asset.expires_at and _as_utc(asset.expires_at) <= now:
            store.delete(asset.storage_key)
            asset.purged_at = now
            session.commit()
            raise AppError(
                "TRANSCRIPTION_MEDIA_EXPIRED", "转写原媒体已超过 24 小时并清除", 410
            )
        if asset.mime_type != payload.mime_type:
            raise AppError(
                "TRANSCRIPTION_MEDIA_MISMATCH",
                "媒体类型与上传时声明不一致",
                422,
            )
        if asset.byte_size > max_bytes:
            raise AppError(
                "TRANSCRIPTION_MEDIA_SIZE_INVALID",
                f"转写媒体大小不能超过 {max_bytes} 字节",
                422,
            )
        content_hash = asset.content_hash or f"presign:{asset.id}"
    else:
        content = _decode_media(payload, max_bytes)
        content_hash = hashlib.sha256(content).hexdigest()

    request_value = {
        "collection_id": record.id,
        "mime_type": payload.mime_type,
        "duration_seconds": payload.duration_seconds,
        "language": payload.language,
        "content_hash": content_hash,
        "provider": provider.provider_id,
    }
    request_hash = hashlib.sha256(
        json.dumps(request_value, sort_keys=True).encode("utf-8")
    ).hexdigest()
    existing_task = session.scalar(
        select(AgentTask).where(
            AgentTask.workspace_id == context.workspace_id,
            AgentTask.idempotency_key == idempotency_key,
        )
    )
    if existing_task is not None:
        if existing_task.request_hash != request_hash:
            raise AppError(
                "IDEMPOTENCY_CONFLICT",
                "该幂等键已用于不同的视频转写输入",
                409,
            )
        return existing_task, False

    if payload.asset_id is None:
        asset_id = new_id("asset")
        storage_key = store.put(
            workspace_id=context.workspace_id,
            asset_id=asset_id,
            suffix=_suffix(payload.mime_type),
            content=content,
        )
        asset = MediaAsset(
            id=asset_id,
            workspace_id=context.workspace_id,
            storage_key=storage_key,
            original_name=Path(payload.filename).name[:240],
            mime_type=payload.mime_type,
            byte_size=len(content),
            width=0,
            height=0,
            role="transcription_source",
            rights_status="approved",
            content_hash=content_hash,
            created_by=context.user_id,
            expires_at=now + timedelta(hours=RAW_RETENTION_HOURS),
        )
        session.add(asset)
        session.flush()

    task = AgentTask(
        workspace_id=context.workspace_id,
        conversation_id=None,
        created_by=context.user_id,
        type="collection_video_transcription",
        mode="collection",
        skill_id="transcribe_user_media",
        status="queued",
        stage="视频转写任务已受理",
        progress=5,
        input_snapshot={
            "collection_id": record.id,
            "asset_id": asset.id,
            "mime_type": payload.mime_type,
            "duration_seconds": payload.duration_seconds,
            "language": payload.language,
            "provider": provider.provider_id,
            "rights_confirmed": True,
            "raw_expires_at": asset.expires_at.isoformat() if asset.expires_at else None,
        },
        result_refs=[],
        result_summary={},
        idempotency_key=idempotency_key,
        request_hash=request_hash,
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
            stage=task.stage,
            progress=task.progress,
        )
    )
    record.content_type = "视频"
    record.video_transcript_status = "queued"
    record.status = "transcription_queued"
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="transcription_task.created",
        entity_type="AgentTask",
        entity_id=task.id,
        details={
            "collection_id": record.id,
            "asset_id": asset.id,
            "provider": provider.provider_id,
            "duration_seconds": payload.duration_seconds,
            "raw_retention_hours": RAW_RETENTION_HOURS,
        },
    )
    session.commit()
    session.refresh(task)
    return task, True


def execute_transcription_task(
    session: Session,
    *,
    task: AgentTask,
    attempt_no: int,
    provider: TranscriptionProvider,
    store: AssetStore,
) -> CollectionRecord:
    snapshot = task.input_snapshot
    record = session.scalar(
        select(CollectionRecord).where(
            CollectionRecord.id == snapshot.get("collection_id"),
            CollectionRecord.workspace_id == task.workspace_id,
            CollectionRecord.deleted_at.is_(None),
        )
    )
    asset = session.scalar(
        select(MediaAsset).where(
            MediaAsset.id == snapshot.get("asset_id"),
            MediaAsset.workspace_id == task.workspace_id,
        )
    )
    if record is None:
        raise AppError("COLLECTION_RECORD_NOT_FOUND", "视频转写关联采集记录不存在", 404)
    if asset is None or asset.purged_at is not None:
        raise AppError("TRANSCRIPTION_MEDIA_EXPIRED", "转写原媒体已清除，请重新上传", 410)
    if asset.expires_at and _as_utc(asset.expires_at) <= utcnow():
        store.delete(asset.storage_key)
        asset.purged_at = utcnow()
        session.commit()
        raise AppError("TRANSCRIPTION_MEDIA_EXPIRED", "转写原媒体已超过 24 小时并清除", 410)

    try:
        content = store.read(asset.storage_key)
    except FileNotFoundError as exc:
        raise AppError("TRANSCRIPTION_MEDIA_NOT_FOUND", "转写原媒体文件不存在", 404) from exc
    data_url = f"data:{asset.mime_type};base64,{base64.b64encode(content).decode('ascii')}"
    try:
        result = provider.transcribe(
            TranscriptionRequest(
                duration_seconds=float(snapshot.get("duration_seconds") or 0),
                mime_type=asset.mime_type,
                data_url=data_url,
                language=str(snapshot.get("language") or "zh"),
            )
        )
    except TranscriptionProviderError as exc:
        record.video_transcript_status = "failed"
        record.status = "needs_transcript"
        session.add(
            ProviderCall(
                task_id=task.id,
                workspace_id=task.workspace_id,
                attempt_no=attempt_no,
                provider=provider.provider_id,
                operation="transcription",
                status="failed",
                error_type=exc.code,
            )
        )
        session.commit()
        raise

    record.content_type = "视频"
    record.video_transcript = result.text
    record.video_transcript_status = "complete"
    record.video_transcript_source = result.model
    record.video_transcript_confidence = result.confidence
    record.video_transcript_segments = [
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
    ]
    record.status = "completed"
    record.version += 1
    store.delete(asset.storage_key)
    asset.purged_at = utcnow()
    session.add(
        ProviderCall(
            task_id=task.id,
            workspace_id=task.workspace_id,
            attempt_no=attempt_no,
            provider=result.provider,
            operation="transcription",
            provider_request_id=result.provider_request_id,
            status="success",
            elapsed_ms=result.elapsed_ms,
            result_count=1,
            rate_limit={},
            cost=0.0,
        )
    )
    task.result_summary = {
        "kind": "video_transcription",
        "collection_id": record.id,
        "provider": result.provider,
        "model": result.model,
        "characters": len(result.text),
        "segments": len(result.segments),
        "raw_media_purged": True,
    }
    add_audit(
        session,
        workspace_id=task.workspace_id,
        user_id=task.created_by,
        action="transcription_task.completed",
        entity_type="CollectionRecord",
        entity_id=record.id,
        details={
            "task_id": task.id,
            "provider": result.provider,
            "model": result.model,
            "raw_media_purged": True,
        },
    )
    session.commit()
    session.refresh(record)
    return record


def purge_expired_transcription_assets(session: Session, store: AssetStore) -> int:
    now = utcnow()
    assets = session.scalars(
        select(MediaAsset).where(
            MediaAsset.role == "transcription_source",
            MediaAsset.purged_at.is_(None),
            MediaAsset.expires_at.is_not(None),
            MediaAsset.expires_at <= now,
        )
    ).all()
    for asset in assets:
        store.delete(asset.storage_key)
        asset.purged_at = now
    if assets:
        session.commit()
    return len(assets)
