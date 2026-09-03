from __future__ import annotations

import base64
import binascii
import hashlib
import json
import struct
import time
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import AuthContext
from app.core.errors import AppError
from app.core.observability import current_trace_id, new_trace_id
from app.core.permissions import PermissionAction, require_permission
from app.models import (
    AgentTask,
    CoverAsset,
    CreationRecord,
    CreationVersion,
    MediaAsset,
    ProviderCall,
    TaskAttempt,
)
from app.models.entities import new_id, utcnow
from app.providers import (
    CoverGenerationBatch,
    CoverGenerationRequest,
    CoverProvider,
    CoverProviderError,
)
from app.schemas.api import (
    CoverRead,
    CoverSaveRead,
    CoverTaskInput,
    MediaAssetCreateInput,
    MediaAssetRead,
)
from app.services.asset_store import AssetStore
from app.services.task_service import add_audit, get_task


COVER_WIDTH = 1200
COVER_HEIGHT = 1600
COVER_VARIANTS = 4
MAX_COVER_REVISIONS = 2


def _png_dimensions(content: bytes) -> tuple[int, int] | None:
    if len(content) < 24 or content[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", content[16:24])
    return (width, height) if width and height else None


def _jpeg_dimensions(content: bytes) -> tuple[int, int] | None:
    if len(content) < 4 or content[:2] != b"\xff\xd8":
        return None
    offset = 2
    while offset + 9 < len(content):
        if content[offset] != 0xFF:
            offset += 1
            continue
        marker = content[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(content):
            return None
        length = int.from_bytes(content[offset : offset + 2], "big")
        if length < 2 or offset + length > len(content):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(content[offset + 3 : offset + 5], "big")
            width = int.from_bytes(content[offset + 5 : offset + 7], "big")
            return (width, height) if width and height else None
        offset += length
    return None


def _decode_upload(
    payload: MediaAssetCreateInput, max_bytes: int
) -> tuple[bytes, int, int, str]:
    prefix = f"data:{payload.mime_type};base64,"
    if not payload.data_url.startswith(prefix):
        raise AppError(
            "MEDIA_DATA_URL_INVALID", "素材内容与声明的 MIME 类型不一致", 422
        )
    try:
        content = base64.b64decode(payload.data_url[len(prefix) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError("MEDIA_DATA_INVALID", "素材 Base64 内容无效", 422) from exc
    if not content or len(content) > max_bytes:
        raise AppError(
            "MEDIA_SIZE_INVALID",
            f"素材大小必须在 1 字节到 {max_bytes} 字节之间",
            422,
        )
    dimensions = (
        _png_dimensions(content)
        if payload.mime_type == "image/png"
        else _jpeg_dimensions(content)
    )
    if dimensions is None:
        raise AppError(
            "MEDIA_DECODE_FAILED", "图片头或尺寸无法解码，已拒绝登记", 422
        )
    width, height = dimensions
    if width > 12000 or height > 12000:
        raise AppError("MEDIA_DIMENSIONS_INVALID", "图片尺寸超过 12000px 上限", 422)
    suffix = ".png" if payload.mime_type == "image/png" else ".jpg"
    return content, width, height, suffix


def media_asset_to_read(asset: MediaAsset) -> MediaAssetRead:
    return MediaAssetRead(
        id=asset.id,
        original_name=asset.original_name,
        mime_type=asset.mime_type,
        byte_size=asset.byte_size,
        width=asset.width,
        height=asset.height,
        role=asset.role,
        rights_status=asset.rights_status,
        content_hash=asset.content_hash,
        content_url=f"/api/v1/media-assets/{asset.id}/content",
        created_by=asset.created_by,
        created_at=asset.created_at,
    )


def create_media_asset(
    session: Session,
    context: AuthContext,
    payload: MediaAssetCreateInput,
    store: AssetStore,
    max_bytes: int,
) -> MediaAssetRead:
    require_permission(context, PermissionAction.COVER_CREATE)
    content, width, height, suffix = _decode_upload(payload, max_bytes)
    content_hash = hashlib.sha256(content).hexdigest()
    existing = session.scalar(
        select(MediaAsset).where(
            MediaAsset.workspace_id == context.workspace_id,
            MediaAsset.content_hash == content_hash,
            MediaAsset.role == payload.role,
        )
    )
    if existing is not None:
        if existing.rights_status != "approved":
            raise AppError("MEDIA_RIGHTS_REQUIRED", "该素材的使用权状态尚未批准", 422)
        return media_asset_to_read(existing)

    asset_id = new_id("asset")
    storage_key = store.put(
        workspace_id=context.workspace_id,
        asset_id=asset_id,
        suffix=suffix,
        content=content,
    )
    asset = MediaAsset(
        id=asset_id,
        workspace_id=context.workspace_id,
        storage_key=storage_key,
        original_name=Path(payload.filename).name[:240],
        mime_type=payload.mime_type,
        byte_size=len(content),
        width=width,
        height=height,
        role=payload.role,
        rights_status="approved",
        content_hash=content_hash,
        created_by=context.user_id,
    )
    session.add(asset)
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="media_asset.created",
        entity_type="MediaAsset",
        entity_id=asset.id,
        details={
            "role": asset.role,
            "rights_status": asset.rights_status,
            "mime_type": asset.mime_type,
            "byte_size": asset.byte_size,
        },
    )
    session.commit()
    session.refresh(asset)
    return media_asset_to_read(asset)


def list_media_assets(
    session: Session, context: AuthContext
) -> list[MediaAssetRead]:
    require_permission(context, PermissionAction.COVER_READ)
    assets = session.scalars(
        select(MediaAsset)
        .where(MediaAsset.workspace_id == context.workspace_id)
        .order_by(desc(MediaAsset.created_at))
    ).all()
    return [media_asset_to_read(asset) for asset in assets]


def get_media_asset(
    session: Session, context: AuthContext, asset_id: str
) -> MediaAsset:
    require_permission(context, PermissionAction.COVER_READ)
    asset = session.scalar(
        select(MediaAsset).where(
            MediaAsset.id == asset_id,
            MediaAsset.workspace_id == context.workspace_id,
        )
    )
    if asset is None:
        raise AppError("MEDIA_ASSET_NOT_FOUND", "当前 Workspace 中不存在该素材", 404)
    return asset


def _authorized_assets(
    session: Session, workspace_id: str, asset_ids: list[str]
) -> list[MediaAsset]:
    unique_ids = list(dict.fromkeys(asset_ids))
    assets = session.scalars(
        select(MediaAsset).where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.id.in_(unique_ids),
        )
    ).all()
    by_id = {asset.id: asset for asset in assets}
    if any(asset_id not in by_id for asset_id in unique_ids):
        raise AppError("MEDIA_ASSET_NOT_FOUND", "封面素材不存在或不属于当前 Workspace", 404)
    ordered = [by_id[asset_id] for asset_id in unique_ids]
    if any(asset.rights_status != "approved" for asset in ordered):
        raise AppError("MEDIA_RIGHTS_REQUIRED", "所有封面素材都必须具备 approved 使用权", 422)
    return ordered


def _creation_snapshot(
    session: Session,
    workspace_id: str,
    creation_id: str | None,
    creation_version_id: str | None,
) -> tuple[str | None, str | None, dict[str, Any]]:
    if not creation_id and not creation_version_id:
        return None, None, {}
    if creation_version_id:
        row = session.execute(
            select(CreationVersion, CreationRecord)
            .join(CreationRecord, CreationRecord.id == CreationVersion.record_id)
            .where(
                CreationVersion.id == creation_version_id,
                CreationVersion.workspace_id == workspace_id,
                CreationRecord.workspace_id == workspace_id,
                CreationRecord.deleted_at.is_(None),
            )
        ).one_or_none()
        if row is None:
            raise AppError("CREATION_VERSION_NOT_FOUND", "创作版本不存在或不属于当前 Workspace", 404)
        version, record = row
        if creation_id and record.id != creation_id:
            raise AppError("CREATION_VERSION_MISMATCH", "创作记录与版本不匹配", 409)
    else:
        record = session.scalar(
            select(CreationRecord).where(
                CreationRecord.id == creation_id,
                CreationRecord.workspace_id == workspace_id,
                CreationRecord.deleted_at.is_(None),
            )
        )
        if record is None or not record.current_version_id:
            raise AppError("CREATION_NOT_FOUND", "创作记录不存在或尚无可用版本", 404)
        version = session.get(CreationVersion, record.current_version_id)
        if version is None or version.workspace_id != workspace_id:
            raise AppError("CREATION_VERSION_NOT_FOUND", "创作当前版本不可用", 404)
    return record.id, version.id, {
        "title": record.title,
        "version": version.version,
        "summary": version.summary[:400],
        "body_excerpt": version.body[:600],
    }


def _cover_request_hash(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _enqueue_cover_task(
    session: Session,
    context: AuthContext,
    *,
    prompt: str,
    assets: list[MediaAsset],
    creation_id: str | None,
    creation_version_id: str | None,
    creation_snapshot: dict[str, Any],
    parent: CoverAsset | None,
    revision_no: int,
    idempotency_key: str,
    provider: CoverProvider,
    usage_confirmed: bool,
) -> tuple[AgentTask, bool]:
    snapshot = {
        "prompt": prompt,
        "ratio": "3:4",
        "width": COVER_WIDTH,
        "height": COVER_HEIGHT,
        "variant_count": COVER_VARIANTS,
        "revision_no": revision_no,
        "parent_asset_id": parent.id if parent else None,
        "creation_id": creation_id,
        "creation_version_id": creation_version_id,
        "creation_snapshot": creation_snapshot,
        "input_asset_ids": [asset.id for asset in assets],
        "input_assets": [
            {
                "id": asset.id,
                "role": asset.role,
                "rights_status": asset.rights_status,
                "content_hash": asset.content_hash,
            }
            for asset in assets
        ],
        "provider": provider.provider_id,
        "external_calls": provider.external_calls,
        "usage_confirmed": usage_confirmed,
    }
    digest = _cover_request_hash(snapshot)
    existing = session.scalar(
        select(AgentTask).where(
            AgentTask.workspace_id == context.workspace_id,
            AgentTask.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_hash != digest:
            raise AppError("IDEMPOTENCY_CONFLICT", "该幂等键已用于不同封面输入", 409)
        return get_task(session, context.workspace_id, existing.id), False

    task = AgentTask(
        workspace_id=context.workspace_id,
        conversation_id=None,
        created_by=context.user_id,
        type="cover_generation" if parent is None else "cover_revision",
        mode="cover",
        skill_id="cover_generation",
        status="queued",
        stage="封面任务已受理",
        progress=5,
        input_snapshot=snapshot,
        result_refs=[],
        result_summary={
            "provider": provider.provider_id,
            "generated": 0,
            "failed": 0,
            "external_calls": provider.external_calls,
        },
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
            stage=task.stage,
            progress=5,
        )
    )
    add_audit(
        session,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        action="cover_task.created" if parent is None else "cover_revision.created",
        entity_type="AgentTask",
        entity_id=task.id,
        details={
            "asset_count": len(assets),
            "creation_version_id": creation_version_id,
            "parent_asset_id": parent.id if parent else None,
            "revision_no": revision_no,
            "provider": provider.provider_id,
        },
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(AgentTask).where(
                AgentTask.workspace_id == context.workspace_id,
                AgentTask.idempotency_key == idempotency_key,
            )
        )
        if existing is None or existing.request_hash != digest:
            raise AppError("IDEMPOTENCY_CONFLICT", "封面任务创建发生幂等冲突", 409)
        return get_task(session, context.workspace_id, existing.id), False
    return get_task(session, context.workspace_id, task.id), True


def create_cover_task(
    session: Session,
    context: AuthContext,
    payload: CoverTaskInput,
    idempotency_key: str,
    provider: CoverProvider,
) -> tuple[AgentTask, bool]:
    require_permission(context, PermissionAction.COVER_CREATE)
    if provider.external_calls and not payload.usage_confirmed:
        raise AppError(
            "EXTERNAL_USE_CONFIRMATION_REQUIRED",
            "请先确认将授权素材发送给外部图片服务并接受费用上限",
            422,
        )
    assets = _authorized_assets(session, context.workspace_id, payload.media_asset_ids)
    if any(asset.role == "generated" for asset in assets):
        raise AppError("MEDIA_ROLE_INVALID", "初始封面任务只接受原片或风格参考素材", 422)
    creation_id, version_id, creation_snapshot = _creation_snapshot(
        session,
        context.workspace_id,
        payload.creation_id,
        payload.creation_version_id,
    )
    return _enqueue_cover_task(
        session,
        context,
        prompt=payload.prompt,
        assets=assets,
        creation_id=creation_id,
        creation_version_id=version_id,
        creation_snapshot=creation_snapshot,
        parent=None,
        revision_no=0,
        idempotency_key=idempotency_key,
        provider=provider,
        usage_confirmed=payload.usage_confirmed,
    )


def create_cover_revision_task(
    session: Session,
    context: AuthContext,
    cover_id: str,
    prompt: str,
    idempotency_key: str,
    provider: CoverProvider,
    usage_confirmed: bool = False,
) -> tuple[AgentTask, bool]:
    require_permission(context, PermissionAction.COVER_CREATE)
    if provider.external_calls and not usage_confirmed:
        raise AppError(
            "EXTERNAL_USE_CONFIRMATION_REQUIRED",
            "请先确认将封面及授权素材发送给外部图片服务并接受费用上限",
            422,
        )
    parent = session.scalar(
        select(CoverAsset).where(
            CoverAsset.id == cover_id,
            CoverAsset.workspace_id == context.workspace_id,
        )
    )
    if parent is None:
        raise AppError("COVER_NOT_FOUND", "当前 Workspace 中不存在该封面方案", 404)
    if parent.revision_no >= MAX_COVER_REVISIONS:
        raise AppError("COVER_REVISION_LIMIT", "封面最多允许两轮继续修改", 409)
    source_ids = list(parent.prompt_snapshot.get("input_asset_ids") or [])
    assets = _authorized_assets(session, context.workspace_id, source_ids)
    creation_id, version_id, creation_snapshot = _creation_snapshot(
        session,
        context.workspace_id,
        parent.creation_id,
        parent.creation_version_id,
    )
    return _enqueue_cover_task(
        session,
        context,
        prompt=prompt,
        assets=assets,
        creation_id=creation_id,
        creation_version_id=version_id,
        creation_snapshot=creation_snapshot,
        parent=parent,
        revision_no=parent.revision_no + 1,
        idempotency_key=idempotency_key,
        provider=provider,
        usage_confirmed=usage_confirmed,
    )


def cover_to_read(cover: CoverAsset) -> CoverRead:
    return CoverRead(
        id=cover.id,
        task_id=cover.task_id,
        media_asset_id=cover.media_asset_id,
        creation_id=cover.creation_id,
        creation_version_id=cover.creation_version_id,
        parent_asset_id=cover.parent_asset_id,
        revision_no=cover.revision_no,
        variant_no=cover.variant_no,
        prompt=str(cover.prompt_snapshot.get("prompt") or ""),
        provider=cover.provider,
        model=cover.model,
        provider_request_id=cover.provider_request_id,
        status=cover.status,
        content_url=f"/api/v1/covers/{cover.id}/content",
        mime_type=cover.mime_type,
        width=cover.width,
        height=cover.height,
        ratio=cover.ratio,
        estimated_cost_usd=cover.estimated_cost_usd,
        estimated_cost_cny=cover.estimated_cost_cny,
        created_by=cover.created_by,
        saved_at=cover.saved_at,
        created_at=cover.created_at,
    )


def list_covers(session: Session, context: AuthContext) -> list[CoverRead]:
    require_permission(context, PermissionAction.COVER_READ)
    covers = session.scalars(
        select(CoverAsset)
        .where(CoverAsset.workspace_id == context.workspace_id)
        .order_by(desc(CoverAsset.created_at), CoverAsset.variant_no)
    ).all()
    return [cover_to_read(cover) for cover in covers]


def get_cover(session: Session, context: AuthContext, cover_id: str) -> CoverAsset:
    require_permission(context, PermissionAction.COVER_READ)
    cover = session.scalar(
        select(CoverAsset).where(
            CoverAsset.id == cover_id,
            CoverAsset.workspace_id == context.workspace_id,
        )
    )
    if cover is None:
        raise AppError("COVER_NOT_FOUND", "当前 Workspace 中不存在该封面方案", 404)
    return cover


def save_cover(
    session: Session, context: AuthContext, cover_id: str
) -> CoverSaveRead:
    require_permission(context, PermissionAction.COVER_SAVE)
    cover = get_cover(session, context, cover_id)
    if context.role == "editor" and cover.created_by != context.user_id:
        raise AppError("COVER_SAVE_FORBIDDEN", "内容成员只能保存自己创建的封面方案", 403)
    if cover.saved_at is None:
        cover.saved_at = utcnow()
        cover.status = "saved"
        add_audit(
            session,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            action="cover.saved",
            entity_type="CoverAsset",
            entity_id=cover.id,
            details={
                "creation_id": cover.creation_id,
                "creation_version_id": cover.creation_version_id,
                "revision_no": cover.revision_no,
            },
        )
        session.commit()
        session.refresh(cover)
    return CoverSaveRead(id=cover.id, status="saved", saved_at=cover.saved_at)


def execute_cover_task(
    session: Session,
    *,
    task: AgentTask,
    attempt_no: int,
    provider: CoverProvider,
    store: AssetStore,
) -> None:
    snapshot = dict(task.input_snapshot)
    assets = _authorized_assets(
        session, task.workspace_id, list(snapshot.get("input_asset_ids") or [])
    )
    _creation_snapshot(
        session,
        task.workspace_id,
        snapshot.get("creation_id"),
        snapshot.get("creation_version_id"),
    )
    existing = session.scalars(
        select(CoverAsset).where(CoverAsset.task_id == task.id)
    ).all()
    existing_numbers = {cover.variant_no for cover in existing}
    missing_numbers = tuple(
        number for number in range(1, COVER_VARIANTS + 1) if number not in existing_numbers
    )
    runtime_assets = [
        {
            "id": asset.id,
            "role": asset.role,
            "rights_status": asset.rights_status,
            "mime_type": asset.mime_type,
            "content": store.read(asset.storage_key),
        }
        for asset in assets
    ]
    if snapshot.get("parent_asset_id"):
        parent = session.scalar(
            select(CoverAsset).where(
                CoverAsset.id == snapshot["parent_asset_id"],
                CoverAsset.workspace_id == task.workspace_id,
            )
        )
        if parent is None:
            raise AppError("COVER_NOT_FOUND", "待修改封面不存在", 404)
        parent_media = session.scalar(
            select(MediaAsset).where(
                MediaAsset.id == parent.media_asset_id,
                MediaAsset.workspace_id == task.workspace_id,
            )
        )
        if parent_media is None:
            raise AppError("COVER_CONTENT_NOT_FOUND", "待修改封面文件不存在", 404)
        runtime_assets.insert(
            0,
            {
                "id": parent_media.id,
                "role": "parent",
                "rights_status": "approved",
                "mime_type": parent_media.mime_type,
                "content": store.read(parent_media.storage_key),
            },
        )
    provider_prompt = str(snapshot.get("prompt") or "")
    creation = snapshot.get("creation_snapshot")
    if isinstance(creation, dict) and creation:
        provider_prompt += (
            f"\n关联创作稿标题：{creation.get('title') or ''}"
            f"\n创作摘要：{creation.get('summary') or ''}"
            f"\n正文摘录：{creation.get('body_excerpt') or ''}"
        )
    started = time.monotonic()
    try:
        batch = (
            provider.generate(
                CoverGenerationRequest(
                    task_id=task.id,
                    attempt_no=attempt_no,
                    prompt=provider_prompt,
                    width=COVER_WIDTH,
                    height=COVER_HEIGHT,
                    variant_numbers=missing_numbers,
                    input_assets=tuple(runtime_assets),
                    revision_no=int(snapshot.get("revision_no") or 0),
                )
            )
            if missing_numbers
            else CoverGenerationBatch(
                request_id="reused-existing-covers",
                provider=provider.provider_id,
                model=provider.model_id,
                variants=(),
                failed_variants=(),
            )
        )
    except CoverProviderError as exc:
        session.add(
            ProviderCall(
                task_id=task.id,
                workspace_id=task.workspace_id,
                attempt_no=attempt_no,
                provider=provider.provider_id,
                operation="cover.generate",
                status="failed",
                elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
                error_type=exc.code,
            )
        )
        session.commit()
        raise

    if missing_numbers:
        session.add(
            ProviderCall(
                task_id=task.id,
                workspace_id=task.workspace_id,
                attempt_no=attempt_no,
                provider=batch.provider,
                operation="cover.generate",
                provider_request_id=batch.request_id,
                status="partial_success" if batch.failed_variants else "success",
                elapsed_ms=max(1, int((time.monotonic() - started) * 1000)),
                result_count=len(batch.variants),
                cost=batch.estimated_cost_usd,
            )
        )
    session.refresh(task)
    if task.status == "cancelled":
        session.commit()
        return
    created: list[CoverAsset] = []
    for variant in batch.variants:
        content_hash = hashlib.sha256(variant.content).hexdigest()
        media = session.scalar(
            select(MediaAsset).where(
                MediaAsset.workspace_id == task.workspace_id,
                MediaAsset.content_hash == content_hash,
                MediaAsset.role == "generated",
            )
        )
        if media is None:
            media_id = new_id("asset")
            suffix = {
                "image/svg+xml": ".svg",
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/webp": ".webp",
            }.get(variant.mime_type)
            if suffix is None:
                raise AppError("COVER_FORMAT_UNSUPPORTED", "封面图片格式不受支持", 500)
            storage_key = store.put(
                workspace_id=task.workspace_id,
                asset_id=media_id,
                suffix=suffix,
                content=variant.content,
            )
            media = MediaAsset(
                id=media_id,
                workspace_id=task.workspace_id,
                storage_key=storage_key,
                original_name=f"{task.id}-variant-{variant.variant_no}{suffix}",
                mime_type=variant.mime_type,
                byte_size=len(variant.content),
                width=variant.width,
                height=variant.height,
                role="generated",
                rights_status="approved",
                content_hash=content_hash,
                created_by=task.created_by,
            )
            session.add(media)
            session.flush()
        cover_id = new_id("cover")
        cover = CoverAsset(
            id=cover_id,
            workspace_id=task.workspace_id,
            task_id=task.id,
            media_asset_id=media.id,
            creation_id=snapshot.get("creation_id"),
            creation_version_id=snapshot.get("creation_version_id"),
            parent_asset_id=snapshot.get("parent_asset_id"),
            revision_no=int(snapshot.get("revision_no") or 0),
            variant_no=variant.variant_no,
            prompt_snapshot=snapshot,
            provider=batch.provider,
            model=batch.model,
            provider_request_id=batch.request_id,
            status="generated",
            result_ref=f"/api/v1/covers/{cover_id}/content",
            mime_type=variant.mime_type,
            width=variant.width,
            height=variant.height,
            ratio="3:4",
            estimated_cost_usd=(
                batch.estimated_cost_usd / max(1, len(batch.variants))
            ),
            estimated_cost_cny=(
                batch.estimated_cost_cny / max(1, len(batch.variants))
            ),
            created_by=task.created_by,
        )
        session.add(cover)
        created.append(cover)
    session.flush()
    all_covers = [*existing, *created]
    all_covers.sort(key=lambda cover: cover.variant_no)
    success_count = len(all_covers)
    failed_count = len(batch.failed_variants)
    total_cost_usd = round(sum(cover.estimated_cost_usd for cover in all_covers), 6)
    total_cost_cny = round(sum(cover.estimated_cost_cny for cover in all_covers), 4)
    final_status = (
        "success"
        if success_count == COVER_VARIANTS and not failed_count
        else "partial_success"
        if success_count
        else "failed"
    )
    now = utcnow()
    task.status = final_status
    task.stage = (
        "封面四方案已生成"
        if final_status == "success"
        else "封面方案部分完成"
        if final_status == "partial_success"
        else "封面生成失败"
    )
    task.progress = 100
    task.finished_at = now
    task.result_refs = [{"type": "cover", "id": cover.id} for cover in all_covers]
    task.result_summary = {
        "kind": "cover_generation",
        "provider": batch.provider,
        "model": batch.model,
        "provider_request_id": batch.request_id,
        "generated": success_count,
        "failed": failed_count,
        "failed_variants": list(batch.failed_variants),
        "ratio": "3:4",
        "width": COVER_WIDTH,
        "height": COVER_HEIGHT,
        "revision_no": int(snapshot.get("revision_no") or 0),
        "parent_asset_id": snapshot.get("parent_asset_id"),
        "external_calls": provider.external_calls,
        "estimated_cost_usd": total_cost_usd,
        "estimated_cost_cny": total_cost_cny,
    }
    task.error = (
        {
            "code": "COVER_PARTIAL_FAILURE" if success_count else "COVER_PROVIDER_FAILED",
            "message": f"{failed_count} 个方案生成失败，可只重试失败项",
            "retryable": True,
            "details": {"failed_variants": list(batch.failed_variants)},
        }
        if failed_count
        else None
    )
    attempt = session.scalar(
        select(TaskAttempt).where(
            TaskAttempt.task_id == task.id,
            TaskAttempt.attempt_no == attempt_no,
        )
    )
    if attempt is None:
        raise AppError("TASK_ATTEMPT_NOT_FOUND", "封面任务 attempt 不存在", 500)
    attempt.status = final_status
    attempt.stage = task.stage
    attempt.progress = 100
    attempt.finished_at = now
    attempt.error = task.error
    add_audit(
        session,
        workspace_id=task.workspace_id,
        user_id=task.created_by,
        action="cover_task.completed" if final_status == "success" else "cover_task.partial",
        entity_type="AgentTask",
        entity_id=task.id,
        details={**task.result_summary, "attempt_no": attempt_no},
    )
    session.commit()
