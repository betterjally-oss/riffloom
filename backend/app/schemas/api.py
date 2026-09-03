from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TaskStatus = Literal[
    "queued", "running", "success", "partial_success", "failed", "cancelled"
]
TaskMode = Literal[
    "agent", "collection", "breakdown", "creation", "trend", "cover", "integration"
]
CollectionKind = Literal["single", "keyword", "creator_content", "creator_profile"]
FeishuScope = Literal[
    "collection.single",
    "collection.keyword",
    "collection.creator_content",
    "collection.creator_profile",
    "breakdown",
    "creation",
]


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False
    request_id: str | None = None
    trace_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class CreateTaskInput(BaseModel):
    mode: TaskMode
    skill_id: str = Field(min_length=2, max_length=80)
    input: dict[str, Any]
    source_ids: list[str] = Field(default_factory=list, max_length=50)
    attachment_ids: list[str] = Field(default_factory=list, max_length=4)
    conversation_id: str | None = None

    @field_validator("input")
    @classmethod
    def require_non_empty_input(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("任务输入不能为空")
        return value


class CreateCollectionTaskInput(BaseModel):
    kind: CollectionKind
    platform: str = Field(min_length=2, max_length=64)
    query: dict[str, Any]
    usage_confirmed: bool
    refresh: bool = False
    limit: int = Field(default=20, ge=1, le=30)
    attachment_ids: list[str] = Field(default_factory=list, max_length=4)
    conversation_id: str | None = None

    @model_validator(mode="after")
    def validate_collection_input(self) -> "CreateCollectionTaskInput":
        if not self.usage_confirmed:
            raise ValueError("必须确认有权将该输入用于采集")
        required = {
            "single": "url",
            "keyword": "keyword",
            "creator_content": "creator",
            "creator_profile": "creator",
        }[self.kind]
        value = self.query.get(required)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{self.kind} 任务缺少 {required}")
        allowed_fields = {
            "single": {"url"},
            "keyword": {"keyword", "sort", "publish_time", "content_type"},
            "creator_content": {"creator"},
            "creator_profile": {"creator"},
        }[self.kind]
        if unexpected := set(self.query) - allowed_fields:
            raise ValueError(
                f"{self.kind} 任务不支持字段: {', '.join(sorted(unexpected))}"
            )
        keyword_options = {
            "sort": {
                "comprehensive",
                "latest",
                "most-liked",
                "most-commented",
                "most-collected",
            },
            "publish_time": {"anytime", "day", "week", "month", "half-year"},
            "content_type": {"image", "video"},
        }
        for field, choices in keyword_options.items():
            if field in self.query and self.query[field] not in choices:
                raise ValueError(f"keyword 任务的 {field} 无效")
        return self


class CollectRewriteTaskInput(BaseModel):
    url: str | None = Field(default=None, max_length=4000)
    collection_id: str | None = Field(default=None, max_length=64)
    prompt: str = Field(min_length=1, max_length=20000)
    usage_confirmed: bool = False
    knowledge_refs: list["KnowledgeRefInput"] = Field(
        default_factory=list, max_length=50
    )
    attachment_ids: list[str] = Field(default_factory=list, max_length=4)
    target_platform: str = Field(default="小红书", min_length=1, max_length=80)
    audience: str = Field(default="", max_length=300)
    conversation_id: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "CollectRewriteTaskInput":
        if bool(self.url and self.url.strip()) == bool(self.collection_id):
            raise ValueError("必须且只能提供 url 或 collection_id 之一")
        if self.url and not self.usage_confirmed:
            raise ValueError("新链接采集前必须确认使用权")
        return self


class BatchCollectionMetadataInput(BaseModel):
    record_ids: list[str] = Field(min_length=1, max_length=20)
    benchmark: bool | None = None
    category_tags: list[str] | None = Field(default=None, max_length=20)

    @field_validator("record_ids")
    @classmethod
    def normalize_record_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @field_validator("category_tags")
    @classmethod
    def normalize_category_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        tags = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(len(item) > 40 for item in tags):
            raise ValueError("单个分类标签不能超过 40 个字符")
        return tags

    @model_validator(mode="after")
    def require_change(self) -> "BatchCollectionMetadataInput":
        if self.benchmark is None and self.category_tags is None:
            raise ValueError("至少需要设置对标状态或分类标签")
        return self


class BatchCollectionMetadataRead(BaseModel):
    updated_ids: list[str]


class CollectionBatchTaskInput(BaseModel):
    action: Literal["breakdown", "rewrite"]
    record_ids: list[str] = Field(min_length=1, max_length=20)
    prompt: str = Field(default="", max_length=20000)
    knowledge_refs: list["KnowledgeRefInput"] = Field(
        default_factory=list, max_length=50
    )

    @field_validator("record_ids")
    @classmethod
    def normalize_batch_record_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def require_rewrite_prompt(self) -> "CollectionBatchTaskInput":
        if self.action == "rewrite" and not self.prompt.strip():
            raise ValueError("批量仿写需要填写创作要求")
        return self


class CollectionBatchTaskIssue(BaseModel):
    record_id: str
    code: str
    message: str
    retryable: bool = False


class CollectionBatchTaskRead(BaseModel):
    tasks: list["TaskRead"]
    issues: list[CollectionBatchTaskIssue]


class TranscriptionUploadInitInput(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    mime_type: Literal[
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "audio/x-wav",
        "audio/mp4",
        "audio/x-m4a",
        "video/mp4",
    ]
    byte_size: int = Field(gt=0)
    duration_seconds: float = Field(gt=0, le=300)
    language: str = Field(default="zh", min_length=2, max_length=16)
    rights_confirmed: bool

    @model_validator(mode="after")
    def validate_transcription_upload_init(self) -> "TranscriptionUploadInitInput":
        if not self.rights_confirmed:
            raise ValueError("必须确认有权使用该媒体进行视频文案转写")
        return self


class TranscriptionUploadInitRead(BaseModel):
    asset_id: str
    object_key: str
    presigned_put_url: str | None = None
    expires_in_seconds: int
    upload_mode: Literal["base64", "tos_presign"]


class TranscriptionTaskInput(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    mime_type: Literal[
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "audio/x-wav",
        "audio/mp4",
        "audio/x-m4a",
        "video/mp4",
    ]
    duration_seconds: float = Field(gt=0, le=300)
    language: str = Field(default="zh", min_length=2, max_length=16)
    rights_confirmed: bool
    data_url: str | None = Field(default=None, min_length=32, max_length=21_000_000)
    asset_id: str | None = Field(default=None, min_length=6, max_length=64)

    @model_validator(mode="after")
    def validate_transcription_upload(self) -> "TranscriptionTaskInput":
        if not self.rights_confirmed:
            raise ValueError("必须确认有权使用该媒体进行视频文案转写")
        if (self.data_url is None) == (self.asset_id is None):
            raise ValueError(
                "必须且只能提供 data_url（本机 base64）或 asset_id（TOS 直传）之一"
            )
        if self.data_url is not None:
            prefix = f"data:{self.mime_type};base64,"
            if not self.data_url.startswith(prefix):
                raise ValueError("媒体内容与声明的 MIME 类型不一致")
        return self


class TrendTaskInput(BaseModel):
    direction: str = Field(min_length=2, max_length=300)
    audience: str = Field(default="", max_length=300)
    keywords: list[str] = Field(default_factory=list, max_length=10)
    window_days: int = Field(default=7, ge=1, le=30)
    result_count: int | None = Field(default=None, ge=1)
    source_types: list[Literal["collections", "breakdowns", "creations"]] = Field(
        default_factory=lambda: ["collections", "breakdowns", "creations"],
        min_length=1,
        max_length=3,
    )
    conversation_id: str | None = None

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if any(len(item) > 80 for item in normalized):
            raise ValueError("单个关键词不能超过 80 个字符")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def normalize_result_count(self) -> "TrendTaskInput":
        requested = self.result_count
        if requested is None:
            match = re.search(r"(\d{1,3})\s*[个条]\s*(?:热点|选题)", self.direction)
            requested = int(match.group(1)) if match else 5
        self.result_count = min(requested, 15)
        return self


class AttemptRead(BaseModel):
    attempt_no: int
    status: TaskStatus
    stage: str
    progress: int
    error: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None


class TaskRead(BaseModel):
    id: str
    workspace_id: str
    conversation_id: str | None
    type: str
    mode: str
    skill_id: str
    status: TaskStatus
    stage: str
    progress: int
    input: dict[str, Any]
    result_refs: list[dict[str, str]]
    result_summary: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None
    trace_id: str | None
    current_attempt: int
    retry_count: int
    attempts: list[AttemptRead]
    created_at: datetime
    updated_at: datetime


class TaskList(BaseModel):
    items: list[TaskRead]
    total: int


class ConversationSummaryRead(BaseModel):
    id: str
    mode: Literal["agent", "collection", "breakdown", "creation", "trend"]
    title: str
    message_count: int
    updated_at: datetime


class ConversationList(BaseModel):
    items: list[ConversationSummaryRead]
    total: int


class ConversationUpdateInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ConversationMessageRead(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ConversationRead(ConversationSummaryRead):
    messages: list[ConversationMessageRead]


class SessionRead(BaseModel):
    user_id: str
    user_name: str
    workspace_id: str
    workspace_name: str
    role: str
    auth_mode: Literal["demo_headers", "invite_token"] = "demo_headers"
    onboarding_completed: bool


class InvitationRedeemInput(BaseModel):
    invitation_code: str = Field(min_length=12, max_length=200)


class AuthTokenRead(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    session: SessionRead


class SignOutRead(BaseModel):
    status: Literal["signed_out"] = "signed_out"


class WorkspaceMemberRead(BaseModel):
    membership_id: str
    user_id: str
    user_name: str
    role: Literal["editor", "lead", "admin"]
    status: Literal["active", "disabled"]
    is_isolated: bool = False
    created_at: datetime
    updated_at: datetime


class WorkspaceMemberList(BaseModel):
    items: list[WorkspaceMemberRead]
    total: int


class WorkspaceMemberCreate(BaseModel):
    user_name: str = Field(min_length=1, max_length=120)
    role: Literal["editor", "lead"] = "editor"
    expires_in_hours: int = Field(default=168, ge=1, le=720)

    @field_validator("user_name")
    @classmethod
    def normalize_user_name(cls, value: str) -> str:
        if not (name := value.strip()):
            raise ValueError("成员名称不能为空")
        return name


class WorkspaceMemberUpdate(BaseModel):
    role: Literal["editor", "lead", "admin"] | None = None
    status: Literal["active", "disabled"] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "WorkspaceMemberUpdate":
        if self.role is None and self.status is None:
            raise ValueError("至少需要修改角色或状态")
        return self


class WorkspaceMemberInvitationInput(BaseModel):
    expires_in_hours: int = Field(default=168, ge=1, le=720)


class WorkspaceMemberInvitationRead(BaseModel):
    member: WorkspaceMemberRead
    invitation_id: str
    invitation_code: str
    expires_at: datetime


class PilotMetricsRead(BaseModel):
    accepted_tasks: int
    completed_tasks: int
    failed_tasks: int
    completion_rate: float = Field(ge=0, le=1)
    adopted_creations: int
    first_version_adoptions: int
    first_version_adoption_rate: float = Field(ge=0, le=1)
    median_delivery_minutes: float | None
    estimated_cost_usd: float
    cost_per_completed_task_usd: float


class HealthRead(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]
    worker: Literal["ok"]
    version: str
    model_provider: str
    model_routing: dict[str, Any] = Field(default_factory=dict)
    transcription_provider: dict[str, Any] = Field(default_factory=dict)


class LibraryRecordRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    library_type: Literal["collections", "breakdowns", "creations"]
    title: str
    status: str
    type: str
    source: str
    author: str
    updated_at: datetime
    published_at: datetime | None = None
    summary: str
    tags: list[str]
    version: str
    source_id: str
    task_id: str
    adopted_version_id: str | None = None
    collection_kind: str | None = None
    provider: str | None = None
    external_url: str | None = None
    thumbnail_url: str | None = None
    metrics: dict[str, int] = Field(default_factory=dict)
    content_type: str | None = None
    benchmark: bool = False
    category_tags: list[str] = Field(default_factory=list)
    is_sandbox: bool = False


class LibraryList(BaseModel):
    items: list[LibraryRecordRead]
    total: int


class LibraryDeleteRead(BaseModel):
    id: str
    library_type: Literal["collections", "breakdowns", "creations"]
    status: Literal["deleted"]
    deleted_at: datetime


class ProviderStatusRead(BaseModel):
    provider: str
    mode: Literal[
        "compliance_sandbox",
        "experimental_local_helper",
        "production",
        "production_third_party",
    ]
    is_sandbox: bool
    platforms: list[str]
    kinds: list[CollectionKind]
    max_items: int
    sample_inputs: dict[str, str]
    actual_upstream: str | None = None
    requires_local_browser: bool = False
    external_calls: bool = False
    video_transcript_required: bool = False


class TranscriptionProviderStatusRead(BaseModel):
    provider: str
    enabled: bool
    external_calls: bool
    short_model: str | None
    long_model: str | None
    short_max_seconds: int
    raw_retention_hours: int | None = None
    upload_mode: Literal["base64", "tos_presign"] = "base64"


class CoverProviderStatusRead(BaseModel):
    provider: str
    model: str
    mode: Literal["deterministic_mock", "production"]
    is_mock: bool
    ratios: list[Literal["3:4"]]
    width: int
    height: int
    variants: int
    max_revisions: int
    external_calls: bool
    requires_usage_confirmation: bool = False
    estimated_cost_cny_per_image: float = 0.0
    estimated_cost_cny_max_request: float = 0.0


class MediaAssetCreateInput(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    mime_type: Literal["image/png", "image/jpeg"]
    role: Literal["original", "reference"]
    rights_confirmed: bool
    data_url: str = Field(min_length=32, max_length=7_500_000)

    @model_validator(mode="after")
    def require_rights(self) -> "MediaAssetCreateInput":
        if not self.rights_confirmed:
            raise ValueError("必须确认对该素材具有必要使用权")
        return self


class MediaAssetRead(BaseModel):
    id: str
    original_name: str
    mime_type: str
    byte_size: int
    width: int
    height: int
    role: Literal[
        "original", "reference", "generated", "attachment", "transcription_source"
    ]
    rights_status: Literal["approved", "pending", "rejected"]
    content_hash: str
    content_url: str
    created_by: str
    created_at: datetime


class MediaAssetList(BaseModel):
    items: list[MediaAssetRead]
    total: int


class AttachmentCreateInput(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    mime_type: Literal[
        "image/png",
        "image/jpeg",
        "text/plain",
        "text/markdown",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    data_url: str = Field(min_length=32, max_length=7_500_000)


class AttachmentRead(BaseModel):
    id: str
    original_name: str
    mime_type: str
    byte_size: int
    content_url: str
    created_at: datetime


class CoverTaskInput(BaseModel):
    prompt: str = Field(min_length=5, max_length=2000)
    media_asset_ids: list[str] = Field(min_length=1, max_length=8)
    creation_id: str | None = Field(default=None, min_length=3, max_length=64)
    creation_version_id: str | None = Field(default=None, min_length=3, max_length=64)
    usage_confirmed: bool = False


class CoverRevisionInput(BaseModel):
    prompt: str = Field(min_length=5, max_length=2000)
    usage_confirmed: bool = False


class CoverRead(BaseModel):
    id: str
    task_id: str
    media_asset_id: str
    creation_id: str | None
    creation_version_id: str | None
    parent_asset_id: str | None
    revision_no: int
    variant_no: int
    prompt: str
    provider: str
    model: str
    provider_request_id: str
    status: Literal["generated", "saved"]
    content_url: str
    mime_type: str
    width: int
    height: int
    ratio: Literal["3:4"]
    estimated_cost_usd: float
    estimated_cost_cny: float = 0.0
    created_by: str
    saved_at: datetime | None
    created_at: datetime


class CoverList(BaseModel):
    items: list[CoverRead]
    total: int


class CoverSaveRead(BaseModel):
    id: str
    status: Literal["saved"]
    saved_at: datetime


class FeishuProviderStatusRead(BaseModel):
    provider: str
    mode: Literal["deterministic_sandbox", "production"]
    is_sandbox: bool
    external_calls: bool
    writes_enabled: bool
    credential_storage: str
    auth_mode: Literal["sandbox", "shared_application", "user_oauth"]
    scopes: list[FeishuScope]
    batch_size: int
    supports_full: bool
    supports_incremental: bool
    supports_retry_failed: bool
    can_configure: bool


class FeishuConnectionCreateInput(BaseModel):
    tenant_name: str = Field(default="示例内容团队", min_length=2, max_length=200)
    external_copy_confirmed: bool

    @model_validator(mode="after")
    def require_external_copy_confirmation(self) -> "FeishuConnectionCreateInput":
        if not self.external_copy_confirmed:
            raise ValueError("必须确认飞书是单向外部副本")
        return self


class FeishuConnectionRead(BaseModel):
    id: str
    provider: str
    tenant_key: str
    tenant_name: str
    auth_type: str
    scopes: list[str]
    status: Literal["active", "expired", "disconnected"]
    expires_at: datetime | None
    created_by: str
    created_at: datetime


class FeishuConnectionList(BaseModel):
    items: list[FeishuConnectionRead]
    total: int


class FeishuTargetList(BaseModel):
    items: list[dict[str, Any]]
    total: int
    provider: str
    external_calls: bool


class FeishuBindingPreflightInput(BaseModel):
    connection_id: str = Field(min_length=3, max_length=64)
    scope_key: FeishuScope
    target_base_id: str = Field(min_length=3, max_length=160)
    target_table_id: str = Field(min_length=3, max_length=160)
    target_table_name: str = Field(min_length=1, max_length=200)
    field_mapping: dict[str, str] = Field(default_factory=dict)


class FeishuBindingPreflightRead(BaseModel):
    valid: bool
    scope_key: FeishuScope
    field_mapping: dict[str, str]
    required_fields: list[str]
    sample: dict[str, Any]
    errors: list[dict[str, str]]
    provider: str
    external_calls: bool


class FeishuBindingCreateInput(FeishuBindingPreflightInput):
    strategy: Literal["manual_incremental", "manual_full"] = "manual_incremental"
    external_copy_confirmed: bool

    @model_validator(mode="after")
    def require_binding_confirmation(self) -> "FeishuBindingCreateInput":
        if not self.external_copy_confirmed:
            raise ValueError("必须确认同步是 Riffloom 到飞书的单向外部副本")
        return self


class FeishuBindingUpdateInput(BaseModel):
    paused: bool | None = None
    target_table_name: str | None = Field(default=None, min_length=1, max_length=200)
    field_mapping: dict[str, str] | None = None


class FeishuSyncItemRead(BaseModel):
    id: str
    source_record_id: str
    source_version: str
    action: Literal["create", "update", "skip"]
    status: Literal["success", "failed", "skipped"]
    remote_record_id: str | None
    error: dict[str, Any] | None
    retry_count: int


class FeishuSyncRunRead(BaseModel):
    id: str
    binding_id: str
    task_id: str
    parent_run_id: str | None
    mode: Literal["full", "incremental", "retry_failed"]
    status: Literal[
        "queued", "running", "success", "partial_success", "failed", "cancelled"
    ]
    total_count: int
    success_count: int
    failed_count: int
    skipped_count: int
    cursor_before: dict[str, Any]
    cursor_after: dict[str, Any]
    error: dict[str, Any] | None
    items: list[FeishuSyncItemRead] = Field(default_factory=list)
    created_by: str
    created_at: datetime
    finished_at: datetime | None


class FeishuBindingRead(BaseModel):
    id: str
    connection_id: str
    scope_key: FeishuScope
    target_base_id: str
    target_table_id: str
    target_table_name: str
    field_mapping: dict[str, str]
    strategy: str
    cursor: dict[str, Any]
    status: Literal["active", "paused", "connection_invalid"]
    last_synced_at: datetime | None
    link_count: int
    last_run: FeishuSyncRunRead | None
    can_configure: bool
    can_sync: bool
    provider: str
    is_sandbox: bool
    external_calls: bool
    target_openable: bool


class FeishuBindingList(BaseModel):
    items: list[FeishuBindingRead]
    total: int


class FeishuSyncCreateInput(BaseModel):
    mode: Literal["full", "incremental"] = "incremental"
    source_record_id: str | None = Field(default=None, min_length=2, max_length=64)


class CollectionDetailRead(BaseModel):
    id: str
    entity_type: Literal["content", "blogger"]
    collection_kind: CollectionKind
    title: str
    body: str
    content_type: str | None = None
    platform: str
    provider: str
    actual_upstream: str | None = None
    is_sandbox: bool
    source_id: str
    external_url: str
    author: str
    author_external_id: str | None = None
    cover_url: str | None = None
    published_at: datetime | None = None
    topics: list[str] = Field(default_factory=list)
    tags: list[str]
    metrics: dict[str, int]
    derived_metrics: dict[str, float | None] = Field(default_factory=dict)
    system_fields: dict[str, Any] = Field(default_factory=dict)
    media_refs: list[dict[str, str]]
    video_transcript: str | None = None
    video_transcript_corrected: str | None = None
    video_transcript_status: str = "not_applicable"
    video_transcript_source: str | None = None
    video_transcript_confidence: float | None = None
    video_transcript_segments: list[dict[str, Any]] = Field(default_factory=list)
    breakdown_id: str | None = None
    breakdown_is_current: bool = False
    task_id: str
    collected_at: datetime
    updated_at: datetime


class AdoptionInput(BaseModel):
    version_id: str = Field(min_length=3, max_length=64)


class AdoptionRead(BaseModel):
    record_id: str
    version_id: str
    status: Literal["adopted"]


class CreationVersionRead(BaseModel):
    id: str
    version: int
    body: str
    topics: list[str]
    change_note: str
    knowledge_snapshot: dict[str, Any]
    generation_config: dict[str, Any]
    title_candidates: list[str] = Field(default_factory=list)
    summary: str = ""
    risk_notes: list[str] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    similarity_report: dict[str, Any] = Field(default_factory=dict)
    model_call_id: str | None = None
    created_at: datetime


class CreationDetailRead(BaseModel):
    id: str
    title: str
    status: str
    source_refs: list[dict[str, str]]
    skill_id: str
    current_version_id: str | None
    adopted_version_id: str | None
    versions: list[CreationVersionRead]
    task_id: str
    created_at: datetime
    updated_at: datetime


class BreakdownVersionRead(BaseModel):
    id: str
    version: int
    source_refs: list[dict[str, Any]]
    observed_facts: list[str]
    hook: dict[str, Any]
    structure: list[str]
    emotion: dict[str, Any]
    visual: dict[str, Any]
    interaction: list[str]
    reusable_methods: list[str]
    risks: list[str]
    skill_snapshot: dict[str, Any]
    model_call_id: str | None
    created_at: datetime


class BreakdownDetailRead(BaseModel):
    id: str
    title: str
    status: str
    source_record_id: str | None
    source_metrics: dict[str, int] = Field(default_factory=dict)
    current_version_id: str | None
    versions: list[BreakdownVersionRead]
    task_id: str
    created_at: datetime
    updated_at: datetime


class KnowledgeRefInput(BaseModel):
    library_type: Literal["collections", "breakdowns", "creations"]
    record_id: str = Field(min_length=3, max_length=64)


class BreakdownTaskInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    source_ids: list[str] = Field(default_factory=list, max_length=10)
    knowledge_refs: list[KnowledgeRefInput] = Field(default_factory=list, max_length=50)
    attachment_ids: list[str] = Field(default_factory=list, max_length=4)
    conversation_id: str | None = None
    preset: Literal["default", "drawer_compact"] = "default"


class CreationTaskInput(BaseModel):
    creation_type: Literal["original", "rewrite"] = "original"
    prompt: str = Field(min_length=1, max_length=20000)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    knowledge_refs: list[KnowledgeRefInput] = Field(default_factory=list, max_length=50)
    attachment_ids: list[str] = Field(default_factory=list, max_length=4)
    target_platform: str = Field(default="小红书", min_length=1, max_length=80)
    audience: str = Field(default="", max_length=300)
    trend_task_id: str | None = Field(default=None, min_length=3, max_length=64)
    conversation_id: str | None = None


class KnowledgeSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    selected_libraries: list[Literal["collections", "breakdowns", "creations"]] = Field(
        min_length=1, max_length=3
    )
    selected_refs: list[KnowledgeRefInput] = Field(default_factory=list, max_length=50)
    top_k: int = Field(default=8, ge=1, le=20)


class KnowledgeSearchRead(BaseModel):
    fragments: list[dict[str, Any]]
    snapshot: dict[str, Any]


class ManualCreationVersionInput(BaseModel):
    base_version_id: str = Field(min_length=3, max_length=64)
    body: str = Field(min_length=20, max_length=20000)
    change_note: str = Field(default="人工修改", min_length=1, max_length=300)


class SkillRead(BaseModel):
    id: str
    name: str
    version: str
    modes: list[str]
    default_mode: str
    output_contract: str
    tools: list[str]
    max_steps: int
    fallback: str


class SkillList(BaseModel):
    items: list[SkillRead]
    total: int
