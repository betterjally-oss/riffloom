from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError
from app.models import (
    BreakdownRecord,
    CollectionRecord,
    CreationRecord,
    KnowledgeChunk,
)
from app.providers.models import GenerationProvider
from app.schemas.generation import KnowledgeFragment, KnowledgeSnapshotV1


ALLOWED_LIBRARIES = {"collections", "breakdowns", "creations"}


def _bigrams(text: str) -> set[str]:
    normalized = "".join(character.lower() for character in text if not character.isspace())
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def _keyword_score(query: str, text: str) -> float:
    left, right = _bigrams(query), _bigrams(text)
    return len(left & right) / max(1, len(left))


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if not denominator:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / denominator


def _record_candidates(
    session: Session,
    workspace_id: str,
    selected_libraries: list[str],
    selected_refs: list[dict[str, str]],
) -> list[dict[str, Any]]:
    refs_by_type: dict[str, set[str]] = {}
    for ref in selected_refs:
        refs_by_type.setdefault(ref.get("type", ""), set()).add(ref.get("id", ""))
    candidates: list[dict[str, Any]] = []
    if "collections" in selected_libraries:
        query = select(CollectionRecord).where(
            CollectionRecord.workspace_id == workspace_id,
            CollectionRecord.deleted_at.is_(None),
        )
        if refs_by_type.get("collection"):
            query = query.where(CollectionRecord.id.in_(refs_by_type["collection"]))
        for record in session.scalars(query).all():
            transcript = (
                (record.video_transcript_corrected or "").strip()
                or (record.video_transcript or "").strip()
            )
            candidates.append(
                {
                    "library_type": "collections",
                    "record_id": record.id,
                    "field_name": "body",
                    "record_version": f"v{record.version}",
                    "text": "\n".join(
                        part
                        for part in (
                            record.title,
                            record.body,
                            f"视频文案：{transcript}" if transcript else "",
                        )
                        if part
                    ),
                    "updated_at": record.updated_at,
                }
            )
    if "breakdowns" in selected_libraries:
        query = (
            select(BreakdownRecord)
            .options(selectinload(BreakdownRecord.versions))
            .where(
                BreakdownRecord.workspace_id == workspace_id,
                BreakdownRecord.deleted_at.is_(None),
            )
        )
        if refs_by_type.get("breakdown"):
            query = query.where(BreakdownRecord.id.in_(refs_by_type["breakdown"]))
        for record in session.scalars(query).all():
            version = next(
                (item for item in record.versions if item.id == record.current_version_id),
                record.versions[-1] if record.versions else None,
            )
            text = (
                "\n".join(
                    [
                        record.title,
                        *version.structure,
                        *version.reusable_methods,
                        *version.risks,
                    ]
                )
                if version
                else "\n".join([record.title, *record.structure, *record.reusable_methods])
            )
            candidates.append(
                {
                    "library_type": "breakdowns",
                    "record_id": record.id,
                    "field_name": "analysis",
                    "record_version": f"v{version.version if version else record.version}",
                    "text": text,
                    "updated_at": record.updated_at,
                }
            )
    if "creations" in selected_libraries:
        query = (
            select(CreationRecord)
            .options(selectinload(CreationRecord.versions))
            .where(
                CreationRecord.workspace_id == workspace_id,
                CreationRecord.deleted_at.is_(None),
            )
        )
        if refs_by_type.get("creation"):
            query = query.where(CreationRecord.id.in_(refs_by_type["creation"]))
        for record in session.scalars(query).all():
            version = next(
                (item for item in record.versions if item.id == record.current_version_id),
                record.versions[-1] if record.versions else None,
            )
            if version:
                candidates.append(
                    {
                        "library_type": "creations",
                        "record_id": record.id,
                        "field_name": "body",
                        "record_version": f"v{version.version}",
                        "text": f"{record.title}\n{version.body}",
                        "updated_at": version.updated_at,
                    }
                )
    return candidates


def search_knowledge(
    session: Session,
    *,
    workspace_id: str,
    provider: GenerationProvider,
    query: str,
    selected_libraries: list[str],
    selected_refs: list[dict[str, str]] | None = None,
    top_k: int = 8,
    max_chars: int = 12000,
) -> tuple[list[KnowledgeFragment], KnowledgeSnapshotV1]:
    invalid = set(selected_libraries) - ALLOWED_LIBRARIES
    if invalid:
        raise AppError("KNOWLEDGE_LIBRARY_INVALID", "知识库类型不受支持", 422)
    if not selected_libraries:
        snapshot = KnowledgeSnapshotV1(
            query=query,
            selected_libraries=[],
            fragments=[],
            empty=True,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return [], snapshot

    candidates = _record_candidates(
        session, workspace_id, selected_libraries, selected_refs or []
    )
    if not candidates:
        snapshot = KnowledgeSnapshotV1(
            query=query,
            selected_libraries=selected_libraries,
            fragments=[],
            empty=True,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return [], snapshot

    missing: list[dict[str, Any]] = []
    indexed: list[KnowledgeChunk] = []
    for candidate in candidates:
        text_hash = hashlib.sha256(candidate["text"].encode("utf-8")).hexdigest()
        chunk = session.scalar(
            select(KnowledgeChunk).where(
                KnowledgeChunk.workspace_id == workspace_id,
                KnowledgeChunk.library_type == candidate["library_type"],
                KnowledgeChunk.record_id == candidate["record_id"],
                KnowledgeChunk.field_name == candidate["field_name"],
                KnowledgeChunk.record_version == candidate["record_version"],
                KnowledgeChunk.text_hash == text_hash,
                KnowledgeChunk.deleted_at.is_(None),
            )
        )
        if chunk is None:
            candidate["text_hash"] = text_hash
            missing.append(candidate)
        else:
            indexed.append(chunk)
    if missing:
        batch = provider.embed([candidate["text"] for candidate in missing])
        for candidate, vector in zip(missing, batch.vectors):
            chunk = KnowledgeChunk(
                workspace_id=workspace_id,
                library_type=candidate["library_type"],
                record_id=candidate["record_id"],
                field_name=candidate["field_name"],
                record_version=candidate["record_version"],
                text=candidate["text"],
                text_hash=candidate["text_hash"],
                embedding=vector,
                embedding_model=batch.model,
                embedding_dimensions=len(vector),
            )
            session.add(chunk)
            indexed.append(chunk)
        session.flush()

    query_vector = provider.embed([query]).vectors[0]
    ranked: list[tuple[float, KnowledgeChunk]] = []
    for chunk in indexed:
        keyword = _keyword_score(query, chunk.text)
        vector = max(0.0, _cosine(query_vector, chunk.embedding))
        explicit = 1.0 if selected_refs and any(
            ref.get("id") == chunk.record_id for ref in selected_refs
        ) else 0.0
        score = 0.45 * keyword + 0.45 * vector + 0.10 * (explicit or 0.5)
        ranked.append((score, chunk))
    ranked.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)

    fragments: list[KnowledgeFragment] = []
    used_chars = 0
    for score, chunk in ranked:
        if len(fragments) >= top_k or used_chars >= max_chars:
            break
        text = chunk.text[: max(0, max_chars - used_chars)]
        if not text:
            break
        fragments.append(
            KnowledgeFragment(
                chunk_id=chunk.id,
                library_type=chunk.library_type,
                record_id=chunk.record_id,
                field_name=chunk.field_name,
                record_version=chunk.record_version,
                updated_at=chunk.updated_at.isoformat(),
                text=text,
                score=round(score, 6),
            )
        )
        used_chars += len(text)
    snapshot_fragments = [
        {
            "chunk_id": item.chunk_id,
            "library_type": item.library_type,
            "record_id": item.record_id,
            "field_name": item.field_name,
            "record_version": item.record_version,
            "updated_at": item.updated_at,
            "text_hash": hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
            "excerpt": item.text[:240],
            "score": item.score,
        }
        for item in fragments
    ]
    snapshot = KnowledgeSnapshotV1(
        query=query,
        selected_libraries=selected_libraries,
        fragments=snapshot_fragments,
        empty=not fragments,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return fragments, snapshot
