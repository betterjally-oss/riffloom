from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:20]}"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="active")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="active")
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_membership"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("mem")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="editor")


class PilotMemberWorkspace(Base, TimestampMixin):
    __tablename__ = "pilot_member_workspaces"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_pilot_member_workspace_workspace"),
        UniqueConstraint(
            "parent_workspace_id",
            "member_user_id",
            name="uq_pilot_member_workspace_parent_user",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("pmw")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    parent_workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id"), index=True
    )
    member_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)


class InvitationCode(Base, TimestampMixin):
    __tablename__ = "invitation_codes"
    __table_args__ = (
        UniqueConstraint("code_hash", name="uq_invitation_code_hash"),
        Index("ix_invitation_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("invite")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)


class AuthSession(Base, TimestampMixin):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_auth_session_token_hash"),
        Index("ix_auth_session_workspace_user", "workspace_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("session")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    invitation_id: Mapped[str] = mapped_column(
        ForeignKey("invitation_codes.id"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("conv")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    mode: Mapped[str] = mapped_column(String(32))
    custom_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    skill_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    knowledge_refs: Mapped[list[str]] = mapped_column(JSON, default=list)


class AgentTask(Base, TimestampMixin):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_task_idempotency"),
        Index("ix_task_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("task")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True, index=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32))
    skill_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(120), default="任务已受理")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    result_refs: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    current_attempt: Mapped[int] = mapped_column(Integer, default=1)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    attempts: Mapped[list["TaskAttempt"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskAttempt.attempt_no",
    )


class TaskAttempt(Base, TimestampMixin):
    __tablename__ = "task_attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt_no", name="uq_task_attempt"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("attempt")
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    stage: Mapped[str] = mapped_column(String(120), default="任务已受理")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    task: Mapped[AgentTask] = relationship(back_populates="attempts")


class CollectionRecord(Base, TimestampMixin):
    __tablename__ = "collection_records"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "source_identity", name="uq_collection_source"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("col")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    source_identity: Mapped[str] = mapped_column(String(320))
    canonical_url: Mapped[str] = mapped_column(String(500))
    platform: Mapped[str] = mapped_column(String(64), default="authorized-web")
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(160), default="合成来源")
    collection_kind: Mapped[str] = mapped_column(
        String(32), default="single", index=True
    )
    external_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    content_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    author_external_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    cover_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_refs: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    metrics: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(
        String(80), default="deterministic-phase1", index=True
    )
    actual_upstream: Mapped[str | None] = mapped_column(String(120), nullable=True)
    video_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_transcript_corrected: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_transcript_status: Mapped[str] = mapped_column(
        String(40), default="not_applicable", index=True
    )
    video_transcript_source: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    video_transcript_confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    video_transcript_segments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list
    )
    raw_schema_version: Mapped[str] = mapped_column(String(40), default="1.1-phase1")
    last_collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    status: Mapped[str] = mapped_column(String(32), default="completed")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    benchmark: Mapped[bool] = mapped_column(Boolean, default=False)
    category_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BloggerRecord(Base, TimestampMixin):
    __tablename__ = "blogger_records"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "platform",
            "external_id",
            name="uq_blogger_platform_external",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("blog")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    platform: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(160), index=True)
    profile_url: Mapped[str] = mapped_column(String(500))
    name: Mapped[str] = mapped_column(String(160))
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bio: Mapped[str] = mapped_column(Text, default="")
    followers: Mapped[int] = mapped_column(Integer, default=0)
    likes_and_collects: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    benchmark: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(80), index=True)
    last_collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CollectionTaskItem(Base, TimestampMixin):
    __tablename__ = "collection_task_items"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "attempt_no", "source_key", name="uq_collection_task_item_source"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("item")
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    source_key: Mapped[str] = mapped_column(String(320))
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    is_new: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_of: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class ProviderCall(Base, TimestampMixin):
    __tablename__ = "provider_calls"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("pcall")
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(80), index=True)
    operation: Mapped[str] = mapped_column(String(80))
    provider_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    rate_limit: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cost: Mapped[float] = mapped_column(Float, default=0.0)


class BreakdownRecord(Base, TimestampMixin):
    __tablename__ = "breakdown_records"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("brk")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("agent_tasks.id"), index=True, unique=True
    )
    source_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("collection_records.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(32), default="completed")
    hook: Mapped[str] = mapped_column(Text)
    structure: Mapped[list[str]] = mapped_column(JSON)
    emotion: Mapped[str] = mapped_column(Text)
    visual: Mapped[str] = mapped_column(Text)
    reusable_methods: Mapped[list[str]] = mapped_column(JSON)
    skill_version: Mapped[str] = mapped_column(String(32), default="1.1-phase1")
    version: Mapped[int] = mapped_column(Integer, default=1)
    current_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    versions: Mapped[list["BreakdownVersion"]] = relationship(
        back_populates="record",
        cascade="all, delete-orphan",
        order_by="BreakdownVersion.version",
    )


class BreakdownVersion(Base, TimestampMixin):
    __tablename__ = "breakdown_versions"
    __table_args__ = (
        UniqueConstraint("record_id", "version", name="uq_breakdown_version"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("bver")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    record_id: Mapped[str] = mapped_column(
        ForeignKey("breakdown_records.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    observed_facts: Mapped[list[str]] = mapped_column(JSON, default=list)
    hook: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    structure: Mapped[list[str]] = mapped_column(JSON, default=list)
    emotion: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    visual: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    interaction: Mapped[list[str]] = mapped_column(JSON, default=list)
    reusable_methods: Mapped[list[str]] = mapped_column(JSON, default=list)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    skill_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model_call_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))

    record: Mapped[BreakdownRecord] = relationship(back_populates="versions")


class CreationRecord(Base, TimestampMixin):
    __tablename__ = "creation_records"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("crt")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("agent_tasks.id"), index=True, unique=True
    )
    title: Mapped[str] = mapped_column(String(300))
    creation_type: Mapped[str] = mapped_column(
        String(64), default="collect_breakdown_rewrite"
    )
    status: Mapped[str] = mapped_column(String(32), default="pending_review")
    source_refs: Mapped[list[dict[str, str]]] = mapped_column(JSON)
    skill_id: Mapped[str] = mapped_column(String(80))
    current_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adopted_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    versions: Mapped[list["CreationVersion"]] = relationship(
        back_populates="record",
        cascade="all, delete-orphan",
        order_by="CreationVersion.version",
    )


class CreationVersion(Base, TimestampMixin):
    __tablename__ = "creation_versions"
    __table_args__ = (
        UniqueConstraint("record_id", "version", name="uq_creation_version"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("ver")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    record_id: Mapped[str] = mapped_column(
        ForeignKey("creation_records.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    change_note: Mapped[str] = mapped_column(String(300))
    knowledge_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    generation_config: Mapped[dict[str, Any]] = mapped_column(JSON)
    title_candidates: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    risk_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    model_call_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    similarity_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))

    record: Mapped[CreationRecord] = relationship(back_populates="versions")


class MediaAsset(Base, TimestampMixin):
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "content_hash", "role", name="uq_media_asset_content_role"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("asset")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    original_name: Mapped[str] = mapped_column(String(240))
    mime_type: Mapped[str] = mapped_column(String(80))
    byte_size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(32), index=True)
    rights_status: Mapped[str] = mapped_column(String(32), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    purged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CoverAsset(Base, TimestampMixin):
    __tablename__ = "cover_assets"
    __table_args__ = (
        UniqueConstraint("task_id", "variant_no", name="uq_cover_task_variant"),
        Index("ix_cover_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("cover")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    media_asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_assets.id"), index=True
    )
    creation_id: Mapped[str | None] = mapped_column(
        ForeignKey("creation_records.id"), nullable=True, index=True
    )
    creation_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("creation_versions.id"), nullable=True, index=True
    )
    parent_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("cover_assets.id"), nullable=True, index=True
    )
    revision_no: Mapped[int] = mapped_column(Integer, default=0)
    variant_no: Mapped[int] = mapped_column(Integer)
    prompt_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(String(80), index=True)
    model: Mapped[str] = mapped_column(String(120))
    provider_request_id: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="generated", index=True)
    result_ref: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(80))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    ratio: Mapped[str] = mapped_column(String(20), default="3:4")
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    saved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FeishuConnection(Base, TimestampMixin):
    __tablename__ = "feishu_connections"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "created_by", name="uq_feishu_connection_workspace_user"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("fconn")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    provider: Mapped[str] = mapped_column(String(80), default="sandbox-feishu-v1")
    tenant_key: Mapped[str] = mapped_column(String(160))
    tenant_name: Mapped[str] = mapped_column(String(200))
    auth_type: Mapped[str] = mapped_column(String(40), default="tenant_self_built")
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    credential_ref: Mapped[str] = mapped_column(String(300))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)


class FeishuOAuthCredential(Base, TimestampMixin):
    __tablename__ = "feishu_oauth_credentials"

    connection_id: Mapped[str] = mapped_column(
        ForeignKey("feishu_connections.id"), primary_key=True
    )
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FeishuSyncBinding(Base, TimestampMixin):
    __tablename__ = "feishu_sync_bindings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "created_by",
            "scope_key",
            name="uq_feishu_binding_user_scope",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("fbind")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("feishu_connections.id"), index=True
    )
    scope_key: Mapped[str] = mapped_column(String(64), index=True)
    target_base_id: Mapped[str] = mapped_column(String(160))
    target_table_id: Mapped[str] = mapped_column(String(160))
    target_table_name: Mapped[str] = mapped_column(String(200))
    field_mapping: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    strategy: Mapped[str] = mapped_column(String(32), default="manual_incremental")
    cursor: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)


class FeishuRecordLink(Base, TimestampMixin):
    __tablename__ = "feishu_record_links"
    __table_args__ = (
        UniqueConstraint(
            "binding_id", "source_record_id", name="uq_feishu_link_source"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("flink")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    binding_id: Mapped[str] = mapped_column(
        ForeignKey("feishu_sync_bindings.id"), index=True
    )
    source_record_id: Mapped[str] = mapped_column(String(64), index=True)
    remote_record_id: Mapped[str] = mapped_column(String(160))
    source_version: Mapped[str] = mapped_column(String(80))
    remote_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="synced", index=True)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class FeishuSyncRun(Base, TimestampMixin):
    __tablename__ = "feishu_sync_runs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "created_by",
            "idempotency_key",
            name="uq_feishu_run_user_idempotency",
        ),
        Index("ix_feishu_run_binding_created", "binding_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("frun")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    binding_id: Mapped[str] = mapped_column(
        ForeignKey("feishu_sync_bindings.id"), index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("agent_tasks.id"), unique=True, index=True
    )
    parent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("feishu_sync_runs.id"), nullable=True, index=True
    )
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    cursor_before: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cursor_after: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FeishuSyncItem(Base, TimestampMixin):
    __tablename__ = "feishu_sync_items"
    __table_args__ = (
        UniqueConstraint("run_id", "source_record_id", name="uq_feishu_item_source"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("fitem")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("feishu_sync_runs.id"), index=True)
    source_record_id: Mapped[str] = mapped_column(String(64), index=True)
    source_version: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    remote_record_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "library_type",
            "record_id",
            "field_name",
            "record_version",
            "text_hash",
            name="uq_knowledge_chunk_source",
        ),
        Index("ix_knowledge_chunk_lookup", "workspace_id", "library_type", "record_id"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("chunk")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    library_type: Mapped[str] = mapped_column(String(32), index=True)
    record_id: Mapped[str] = mapped_column(String(64), index=True)
    field_name: Mapped[str] = mapped_column(String(64))
    record_version: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[list[float]] = mapped_column(JSON, default=list)
    embedding_model: Mapped[str] = mapped_column(
        String(100), default="mock-embedding-v1"
    )
    embedding_dimensions: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ModelCall(Base, TimestampMixin):
    __tablename__ = "model_calls"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("mcall")
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(80), index=True)
    model: Mapped[str] = mapped_column(String(120), index=True)
    operation: Mapped[str] = mapped_column(String(80), index=True)
    contract_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(32), index=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    repair_count: Mapped[int] = mapped_column(Integer, default=0)
    provider_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("audit")
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
