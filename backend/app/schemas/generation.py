from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentChatOutputV1(StrictModel):
    reply: str = Field(min_length=1, max_length=12000)


class AgentToolIntentV2(StrictModel):
    name: str = Field(min_length=1, max_length=40)
    prompt: str = Field(default="", max_length=20000)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    url: str | None = Field(default=None, max_length=4000)
    collection_id: str | None = Field(default=None, max_length=64)
    collection_kind: Literal["single", "keyword", "creator_content", "creator_profile"] | None = (
        None
    )


class AgentChatOutputV2(StrictModel):
    reply: str = Field(min_length=1, max_length=12000)
    action: AgentToolIntentV2 | None = None


class HookAnalysis(StrictModel):
    type: str = Field(min_length=1, max_length=80)
    expression: str = Field(min_length=1, max_length=500)
    why_effective: str = Field(min_length=1, max_length=1000)


class EmotionAnalysis(StrictModel):
    target: str = Field(min_length=1, max_length=200)
    turn: str = Field(min_length=1, max_length=500)
    action_driver: str = Field(min_length=1, max_length=500)


class VisualAnalysis(StrictModel):
    observed: list[str] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=10)


class BreakdownOutputV1(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    observed_facts: list[str] = Field(min_length=1, max_length=30)
    hook: HookAnalysis
    structure: list[str] = Field(min_length=1, max_length=20)
    emotion: EmotionAnalysis
    visual: VisualAnalysis
    interaction: list[str] = Field(min_length=1, max_length=20)
    reusable_methods: list[str] = Field(min_length=1, max_length=20)
    risks: list[str] = Field(min_length=1, max_length=20)


class SourceCitation(StrictModel):
    library_type: Literal["collections", "breakdowns", "creations"]
    record_id: str
    record_version: str
    field_name: str


class CreationOutputV1(StrictModel):
    title_candidates: list[str] = Field(min_length=2, max_length=8)
    summary: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=20, max_length=20000)
    topics: list[str] = Field(default_factory=list, max_length=20)
    used_sources: list[SourceCitation] = Field(default_factory=list, max_length=20)
    risk_notes: list[str] = Field(min_length=1, max_length=20)


class TrendSourceCitation(StrictModel):
    platform: str = Field(min_length=1, max_length=40)
    source_id: str = Field(min_length=1, max_length=160)
    observed_at: str


class TopicCandidate(StrictModel):
    topic: str
    why_hot: str
    audience: str
    core_value: str
    title_suggestion: str
    angle: str
    sources: list[TrendSourceCitation] = Field(min_length=1, max_length=5)


class TopicGuidanceOutputV3(StrictModel):
    candidates: list[TopicCandidate] = Field(min_length=1, max_length=15)
    caveat: str


class KnowledgeFragment(StrictModel):
    chunk_id: str
    library_type: Literal["collections", "breakdowns", "creations"]
    record_id: str
    field_name: str
    record_version: str
    updated_at: str
    text: str
    score: float


class KnowledgeSnapshotV1(StrictModel):
    schema_version: Literal["knowledge-snapshot.v1"] = "knowledge-snapshot.v1"
    query: str
    selected_libraries: list[str]
    fragments: list[dict[str, Any]]
    empty: bool
    created_at: str
