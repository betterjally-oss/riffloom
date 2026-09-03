from __future__ import annotations

import base64
import binascii
import hashlib
import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import AuthContext
from app.core.errors import AppError
from app.core.permissions import PermissionAction, require_permission
from app.models import MediaAsset
from app.models.entities import new_id
from app.schemas.api import AttachmentCreateInput, AttachmentRead
from app.services.asset_store import AssetStore


_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def _decode(payload: AttachmentCreateInput, max_bytes: int) -> bytes:
    prefix = f"data:{payload.mime_type};base64,"
    if not payload.data_url.startswith(prefix):
        raise AppError("ATTACHMENT_DATA_URL_INVALID", "附件内容与声明格式不一致", 422)
    try:
        content = base64.b64decode(payload.data_url[len(prefix) :], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError("ATTACHMENT_DATA_INVALID", "附件 Base64 内容无效", 422) from exc
    if not content or len(content) > max_bytes:
        raise AppError(
            "ATTACHMENT_SIZE_INVALID",
            f"附件大小必须在 1 字节到 {max_bytes} 字节之间",
            422,
        )
    valid = {
        "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": content.startswith(b"\xff\xd8") and content.endswith(b"\xff\xd9"),
        "text/plain": _valid_text(content),
        "text/markdown": _valid_text(content),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _valid_docx(content),
    }[payload.mime_type]
    if not valid:
        raise AppError("ATTACHMENT_FORMAT_INVALID", "附件实际内容与文件格式不一致", 422)
    return content


def _valid_text(content: bytes) -> bool:
    try:
        return "\x00" not in content.decode("utf-8")
    except UnicodeDecodeError:
        return False


def _valid_docx(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if "[Content_Types].xml" not in archive.namelist():
                return False
            if archive.getinfo("word/document.xml").file_size > 2_000_000:
                return False
            document = archive.read("word/document.xml")
            return b"<!DOCTYPE" not in document.upper()
    except (KeyError, zipfile.BadZipFile):
        return False


def _read(asset: MediaAsset) -> AttachmentRead:
    return AttachmentRead(
        id=asset.id,
        original_name=asset.original_name,
        mime_type=asset.mime_type,
        byte_size=asset.byte_size,
        content_url=f"/api/v1/media-assets/{asset.id}/content",
        created_at=asset.created_at,
    )


def create_attachment(
    session: Session,
    context: AuthContext,
    payload: AttachmentCreateInput,
    store: AssetStore,
    max_bytes: int,
) -> AttachmentRead:
    require_permission(context, PermissionAction.TASK_CREATE)
    content = _decode(payload, max_bytes)
    content_hash = hashlib.sha256(content).hexdigest()
    existing = session.scalar(
        select(MediaAsset).where(
            MediaAsset.workspace_id == context.workspace_id,
            MediaAsset.content_hash == content_hash,
            MediaAsset.role == "attachment",
            MediaAsset.purged_at.is_(None),
        )
    )
    if existing is not None:
        return _read(existing)
    asset_id = new_id("asset")
    asset = MediaAsset(
        id=asset_id,
        workspace_id=context.workspace_id,
        storage_key=store.put(
            workspace_id=context.workspace_id,
            asset_id=asset_id,
            suffix=_SUFFIXES[payload.mime_type],
            content=content,
        ),
        original_name=Path(payload.filename.replace("\\", "/")).name[:240],
        mime_type=payload.mime_type,
        byte_size=len(content),
        width=0,
        height=0,
        role="attachment",
        rights_status="approved",
        content_hash=content_hash,
        created_by=context.user_id,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return _read(asset)


def validate_attachment_ids(
    session: Session, workspace_id: str, attachment_ids: list[str]
) -> list[MediaAsset]:
    unique_ids = list(dict.fromkeys(attachment_ids))
    assets = session.scalars(
        select(MediaAsset).where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.id.in_(unique_ids),
            MediaAsset.role == "attachment",
            MediaAsset.rights_status == "approved",
            MediaAsset.purged_at.is_(None),
        )
    ).all()
    by_id = {asset.id: asset for asset in assets}
    if len(by_id) != len(unique_ids):
        raise AppError("ATTACHMENT_NOT_FOUND", "部分附件不存在或不属于当前 Workspace", 404)
    return [by_id[asset_id] for asset_id in unique_ids]


def attachment_context(assets: list[MediaAsset], store: AssetStore) -> dict[str, object]:
    texts: list[str] = []
    media_refs: list[dict[str, object]] = []
    refs: list[dict[str, object]] = []
    for asset in assets:
        try:
            content = store.read(asset.storage_key)
        except FileNotFoundError as exc:
            raise AppError("ATTACHMENT_CONTENT_NOT_FOUND", "附件文件不存在", 404) from exc
        refs.append(
            {
                "type": "attachment",
                "id": asset.id,
                "name": asset.original_name,
                "mime_type": asset.mime_type,
            }
        )
        if asset.mime_type.startswith("image/"):
            media_refs.append(
                {
                    "type": "image",
                    "url": f"data:{asset.mime_type};base64,{base64.b64encode(content).decode('ascii')}",
                    "authorized": True,
                }
            )
        elif asset.mime_type in {"text/plain", "text/markdown"}:
            texts.append(f"附件 {asset.original_name}：\n{content.decode('utf-8')[:40000]}")
        else:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            text = "\n".join(
                value.strip() for value in root.itertext() if value.strip()
            )
            texts.append(f"附件 {asset.original_name}：\n{text[:40000]}")
    return {"refs": refs, "text": "\n\n".join(texts), "media_refs": media_refs}


def load_attachment_context(
    session: Session,
    workspace_id: str,
    attachment_ids: list[str],
    store: AssetStore,
) -> dict[str, object]:
    return attachment_context(
        validate_attachment_ids(session, workspace_id, attachment_ids), store
    )
