from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import AppError


MANIFEST_DIR = Path(__file__).resolve().parents[1] / "skills" / "manifests"


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str
    modes: list[str]
    default_mode: str
    input_schema: dict[str, Any]
    output_contract: str
    tools: list[str]
    max_steps: int = Field(ge=1, le=4)
    roles: list[str]
    fallback: str
    checksum: str = ""


@lru_cache(maxsize=1)
def load_skill_registry() -> dict[str, SkillManifest]:
    registry: dict[str, SkillManifest] = {}
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        raw = path.read_bytes()
        payload = json.loads(raw)
        payload["checksum"] = hashlib.sha256(raw).hexdigest()
        manifest = SkillManifest.model_validate(payload)
        if manifest.id in registry:
            raise RuntimeError(f"duplicate skill manifest: {manifest.id}")
        registry[manifest.id] = manifest
    expected = {
        "riffloom_agent",
        "viral_breakdown",
        "viral_topic_coach",
        "collect_content",
        "copy_rewrite",
        "collect_breakdown_rewrite",
        "original_copy",
    }
    if set(registry) != expected:
        raise RuntimeError(f"skill registry mismatch: {sorted(registry)}")
    return registry


def get_skill(skill_id: str, *, role: str | None = None) -> SkillManifest:
    skill = load_skill_registry().get(skill_id)
    if skill is None:
        raise AppError("SKILL_NOT_ALLOWED", "当前技能未进入已确认的工具白名单", 422)
    if role is not None and role not in skill.roles:
        raise AppError("ROLE_FORBIDDEN", "当前角色不能调用该技能", 403)
    return skill


def skill_snapshot(skill: SkillManifest) -> dict[str, Any]:
    return {
        "id": skill.id,
        "name": skill.name,
        "version": skill.version,
        "checksum": skill.checksum,
        "max_steps": skill.max_steps,
        "tools": skill.tools,
    }
