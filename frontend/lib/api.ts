import { collectionRecordStatusLabel, type ChatMode, type CollectionKind, type LibraryRecord, type PageKey, type Task, type UnifiedTaskStatus } from "./domain";

const API_BASE_URL = "/api/riffloom";

export type ApiSession = {
  user_id: string;
  user_name: string;
  workspace_id: string;
  workspace_name: string;
  role: string;
  auth_mode: "demo_headers" | "invite_token";
  onboarding_completed: boolean;
};

export type ApiWorkspaceMember = {
  membership_id: string;
  user_id: string;
  user_name: string;
  role: "editor" | "lead" | "admin";
  status: "active" | "disabled";
  is_isolated: boolean;
  created_at: string;
  updated_at: string;
};

export type ApiWorkspaceMemberInvitation = {
  member: ApiWorkspaceMember;
  invitation_id: string;
  invitation_code: string;
  expires_at: string;
};

export type ApiPilotMetrics = {
  accepted_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  completion_rate: number;
  adopted_creations: number;
  first_version_adoptions: number;
  first_version_adoption_rate: number;
  median_delivery_minutes: number | null;
  estimated_cost_usd: number;
  cost_per_completed_task_usd: number;
};

export const apiIntegrationEnabled =
  process.env.NEXT_PUBLIC_API_MODE !== "scenario" && process.env.NODE_ENV !== "test";

export type ApiTask = {
  id: string;
  workspace_id: string;
  conversation_id: string | null;
  type: string;
  mode: string;
  skill_id: string;
  status: "queued" | "running" | "success" | "partial_success" | "failed" | "cancelled";
  stage: string;
  progress: number;
  input: Record<string, unknown>;
  result_refs: Array<{ type: string; id: string }>;
  result_summary: Record<string, unknown>;
  error: { code: string; message: string; retryable: boolean } | null;
  trace_id: string | null;
  current_attempt: number;
  retry_count: number;
  attempts: Array<{ attempt_no: number; status: string; stage: string; progress: number }>;
  created_at: string;
  updated_at: string;
};

export type ApiConversationSummary = {
  id: string;
  mode: "agent" | "collection" | "breakdown" | "creation" | "trend";
  title: string;
  message_count: number;
  updated_at: string;
};

export type ApiConversation = ApiConversationSummary & {
  messages: Array<{ role: "user" | "assistant"; content: string }>;
};

export type ApiTopicSource = {
  platform: string;
  source_id: string;
  observed_at: string;
};

export type ApiTopicCandidate = {
  topic: string;
  why_hot: string;
  audience: string;
  core_value: string;
  title_suggestion: string;
  angle: string;
  sources: ApiTopicSource[];
};

export type ApiLibraryRecord = {
  id: string;
  library_type: "collections" | "breakdowns" | "creations";
  title: string;
  status: string;
  type: string;
  source: string;
  author: string;
  updated_at: string;
  published_at?: string | null;
  summary: string;
  tags: string[];
  version: string;
  source_id: string;
  task_id: string;
  adopted_version_id: string | null;
  collection_kind: CollectionKind | null;
  provider: string | null;
  external_url: string | null;
  thumbnail_url: string | null;
  metrics: Record<string, number>;
  content_type?: string | null;
  benchmark?: boolean;
  category_tags?: string[];
  is_sandbox: boolean;
};

export type ApiCollectionProvider = {
  provider: string;
  mode: "compliance_sandbox" | "experimental_local_helper" | "production" | "production_third_party";
  is_sandbox: boolean;
  platforms: string[];
  kinds: CollectionKind[];
  max_items: number;
  sample_inputs: Record<CollectionKind, string>;
  actual_upstream?: string | null;
  requires_local_browser?: boolean;
  external_calls?: boolean;
  video_transcript_required?: boolean;
};

export type ApiTranscriptionProvider = {
  provider: string;
  enabled: boolean;
  external_calls: boolean;
  short_model: string | null;
  long_model: string | null;
  short_max_seconds: number;
  raw_retention_hours: number | null;
  upload_mode: "base64" | "tos_presign";
};

export type ApiTranscriptionUpload = {
  asset_id: string;
  object_key: string;
  presigned_put_url: string | null;
  expires_in_seconds: number;
  upload_mode: "base64" | "tos_presign";
};

export type ApiCoverProvider = {
  provider: string;
  model: string;
  mode: "deterministic_mock" | "production";
  is_mock: boolean;
  ratios: ["3:4"];
  width: number;
  height: number;
  variants: number;
  max_revisions: number;
  external_calls: boolean;
  requires_usage_confirmation: boolean;
  estimated_cost_cny_per_image: number;
  estimated_cost_cny_max_request: number;
};

export type ApiMediaAsset = {
  id: string;
  original_name: string;
  mime_type: string;
  byte_size: number;
  width: number;
  height: number;
  role: "original" | "reference" | "generated" | "attachment" | "transcription_source";
  rights_status: "approved" | "pending" | "rejected";
  content_hash: string;
  content_url: string;
  created_by: string;
  created_at: string;
};

export type ApiAttachment = {
  id: string;
  original_name: string;
  mime_type: string;
  byte_size: number;
  content_url: string;
  created_at: string;
};

export type ApiCover = {
  id: string;
  task_id: string;
  media_asset_id: string;
  creation_id: string | null;
  creation_version_id: string | null;
  parent_asset_id: string | null;
  revision_no: number;
  variant_no: number;
  prompt: string;
  provider: string;
  model: string;
  provider_request_id: string;
  status: "generated" | "saved";
  content_url: string;
  mime_type: string;
  width: number;
  height: number;
  ratio: "3:4";
  estimated_cost_usd: number;
  estimated_cost_cny: number;
  created_by: string;
  saved_at: string | null;
  created_at: string;
};

export type FeishuScope =
  | "collection.single"
  | "collection.keyword"
  | "collection.creator_content"
  | "collection.creator_profile"
  | "breakdown"
  | "creation";

export type ApiFeishuProvider = {
  provider: string;
  mode: "deterministic_sandbox" | "production";
  is_sandbox: boolean;
  external_calls: boolean;
  writes_enabled: boolean;
  credential_storage: string;
  auth_mode: "sandbox" | "shared_application" | "user_oauth";
  scopes: FeishuScope[];
  batch_size: number;
  supports_full: boolean;
  supports_incremental: boolean;
  supports_retry_failed: boolean;
  can_configure: boolean;
};

export type ApiFeishuConnection = {
  id: string;
  provider: string;
  tenant_key: string;
  tenant_name: string;
  auth_type: string;
  scopes: string[];
  status: "active" | "expired" | "disconnected";
  expires_at: string | null;
  created_by: string;
  created_at: string;
};

export type ApiFeishuTarget = {
  base_id: string;
  base_name: string;
  tables: Array<{
    table_id: string;
    table_name: string;
    scope_key: FeishuScope;
    fields: Array<{ name: string; type: string; readonly: boolean }>;
  }>;
};

export type ApiFeishuSyncItem = {
  id: string;
  source_record_id: string;
  source_version: string;
  action: "create" | "update" | "skip";
  status: "success" | "failed" | "skipped";
  remote_record_id: string | null;
  error: { code: string; message: string; retryable?: boolean } | null;
  retry_count: number;
};

export type ApiFeishuSyncRun = {
  id: string;
  binding_id: string;
  task_id: string;
  parent_run_id: string | null;
  mode: "full" | "incremental" | "retry_failed";
  status: "queued" | "running" | "success" | "partial_success" | "failed" | "cancelled";
  total_count: number;
  success_count: number;
  failed_count: number;
  skipped_count: number;
  cursor_before: Record<string, unknown>;
  cursor_after: Record<string, unknown>;
  error: { code: string; message: string; retryable?: boolean } | null;
  items: ApiFeishuSyncItem[];
  created_by: string;
  created_at: string;
  finished_at: string | null;
};

export type ApiFeishuBinding = {
  id: string;
  connection_id: string;
  scope_key: FeishuScope;
  target_base_id: string;
  target_table_id: string;
  target_table_name: string;
  field_mapping: Record<string, string>;
  strategy: string;
  cursor: Record<string, unknown>;
  status: "active" | "paused" | "connection_invalid";
  last_synced_at: string | null;
  link_count: number;
  last_run: ApiFeishuSyncRun | null;
  can_configure: boolean;
  can_sync: boolean;
  provider: string;
  is_sandbox: boolean;
  external_calls: boolean;
  target_openable: boolean;
};

export type ApiHealth = {
  status: "ok";
  database: "ok";
  worker: "ok";
  version: string;
  model_provider: string;
  model_routing: Record<string, unknown>;
  transcription_provider: Record<string, unknown>;
};

export type ApiCollectionDetail = {
  id: string;
  entity_type: "content" | "blogger";
  collection_kind: CollectionKind;
  title: string;
  body: string;
  content_type: string | null;
  platform: string;
  provider: string;
  actual_upstream: string | null;
  is_sandbox: boolean;
  source_id: string;
  external_url: string;
  author: string;
  author_external_id: string | null;
  cover_url: string | null;
  published_at: string | null;
  topics: string[];
  tags: string[];
  metrics: Record<string, number>;
  derived_metrics: Record<string, number | null>;
  system_fields: Record<string, unknown>;
  media_refs: Array<{ type: string; url: string; asset_id?: string; byte_size?: string }>;
  video_transcript: string | null;
  video_transcript_corrected: string | null;
  video_transcript_status: string;
  video_transcript_source: string | null;
  video_transcript_confidence: number | null;
  video_transcript_segments: Array<{
    start_ms: number;
    end_ms: number;
    text: string;
    confidence?: number;
  }>;
  breakdown_id: string | null;
  breakdown_is_current: boolean;
  task_id: string;
  collected_at: string;
  updated_at: string;
};

export type ApiCreationDetail = {
  id: string;
  title: string;
  status: string;
  source_refs: Array<{ type: string; id: string }>;
  skill_id: string;
  current_version_id: string | null;
  adopted_version_id: string | null;
  versions: Array<{
    id: string;
    version: number;
    body: string;
    topics: string[];
    change_note: string;
    knowledge_snapshot: Record<string, unknown>;
    generation_config: Record<string, unknown>;
    title_candidates: string[];
    summary: string;
    risk_notes: string[];
    source_refs: Array<Record<string, unknown>>;
    similarity_report: Record<string, unknown>;
    model_call_id: string | null;
    created_at: string;
  }>;
  task_id: string;
};

export type ApiBreakdownDetail = {
  id: string;
  title: string;
  status: string;
  source_record_id: string | null;
  source_metrics: Record<string, number>;
  current_version_id: string | null;
  versions: Array<{
    id: string;
    version: number;
    source_refs: Array<Record<string, unknown>>;
    observed_facts: string[];
    hook: { type?: string; expression?: string; why_effective?: string };
    structure: string[];
    emotion: { target?: string; turn?: string; action_driver?: string };
    visual: { observed?: string[]; limitations?: string[] };
    interaction: string[];
    reusable_methods: string[];
    risks: string[];
    skill_snapshot: Record<string, unknown>;
    model_call_id: string | null;
    created_at: string;
  }>;
  task_id: string;
  created_at: string;
  updated_at: string;
};

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly retryable = false,
    public readonly status = 0,
    public readonly requestId: string | null = null,
    public readonly traceId: string | null = null,
  ) {
    super(message);
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 5_000);
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const error = body?.error;
      if (
        response.status === 401
        && ["AUTH_REQUIRED", "AUTH_HEADER_INVALID", "AUTH_SESSION_INVALID"].includes(error?.code)
      ) {
        window.dispatchEvent(new Event("riffloom:auth-required"));
      }
      throw new ApiError(
        error?.code ?? "API_ERROR",
        error?.message ?? `API 请求失败（${response.status}）`,
        error?.retryable ?? false,
        response.status,
        error?.request_id ?? null,
        error?.trace_id ?? null,
      );
    }
    return body as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      "API_UNREACHABLE",
      error instanceof DOMException && error.name === "AbortError"
        ? "API 响应超时"
        : "无法连接 API",
      true,
    );
  } finally {
    window.clearTimeout(timeout);
  }
}

export function getSession() {
  return request<ApiSession>("/session");
}

export function completeApiOnboarding() {
  return request<ApiSession>("/session/onboarding/complete", { method: "POST" });
}

export function listWorkspaceMembers() {
  return request<{ items: ApiWorkspaceMember[]; total: number }>("/workspace/members");
}

export function getPilotMetrics() {
  return request<ApiPilotMetrics>("/workspace/pilot-metrics");
}

export function createWorkspaceMember(args: {
  userName: string;
  role: Exclude<ApiWorkspaceMember["role"], "admin">;
  expiresInHours: number;
}) {
  return request<ApiWorkspaceMemberInvitation>("/workspace/members", {
    method: "POST",
    body: JSON.stringify({
      user_name: args.userName,
      role: args.role,
      expires_in_hours: args.expiresInHours,
    }),
  });
}

export function updateWorkspaceMember(
  membershipId: string,
  change: Partial<Pick<ApiWorkspaceMember, "role" | "status">>,
) {
  return request<ApiWorkspaceMember>(
    `/workspace/members/${encodeURIComponent(membershipId)}`,
    { method: "PATCH", body: JSON.stringify(change) },
  );
}

export function issueWorkspaceMemberInvitation(
  membershipId: string,
  expiresInHours = 168,
) {
  return request<ApiWorkspaceMemberInvitation>(
    `/workspace/members/${encodeURIComponent(membershipId)}/invitation`,
    { method: "POST", body: JSON.stringify({ expires_in_hours: expiresInHours }) },
  );
}

export function redeemInvitation(invitationCode: string) {
  return request<{ session: ApiSession; expires_at: string }>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ invitation_code: invitationCode }),
  });
}

export function signOut() {
  return request<{ status: "signed_out" }>("/auth/logout", { method: "POST" });
}

export function getHealth() {
  return request<ApiHealth>("/health");
}

export function listApiTasks() {
  return request<{ items: ApiTask[]; total: number }>("/tasks");
}

export function getApiTask(taskId: string) {
  return request<ApiTask>(`/tasks/${encodeURIComponent(taskId)}`);
}

export function listApiConversations() {
  return request<{ items: ApiConversationSummary[]; total: number }>("/conversations");
}

export function getApiConversation(conversationId: string) {
  return request<ApiConversation>(`/conversations/${encodeURIComponent(conversationId)}`);
}

export function updateApiConversation(conversationId: string, title: string) {
  return request<ApiConversationSummary>(`/conversations/${encodeURIComponent(conversationId)}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export function deleteApiConversation(conversationId: string) {
  return request<void>(`/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" });
}

export function createApiTask(args: {
  mode: ChatMode;
  skillId: string;
  prompt: string;
  idempotencyKey: string;
  conversationId?: string;
  knowledgeRefs?: Array<{ library_type: "collections" | "breakdowns" | "creations"; record_id: string }>;
  attachmentIds?: string[];
  sourceIds?: string[];
}) {
  const modeMap: Record<ChatMode, string> = {
    agent: "agent",
    capture: "collection",
    breakdown: "breakdown",
    creation: "creation",
    hot: "trend",
  };
  return request<ApiTask>("/tasks", {
    method: "POST",
    headers: { "Idempotency-Key": args.idempotencyKey },
    body: JSON.stringify({
      mode: modeMap[args.mode],
      skill_id: args.skillId,
      input: { prompt: args.prompt, knowledge_refs: args.knowledgeRefs ?? [] },
      source_ids: args.sourceIds ?? [],
      attachment_ids: args.attachmentIds ?? [],
      conversation_id: args.conversationId,
    }),
  });
}

export function createApiTrendTask(args: {
  direction: string;
  conversationId?: string;
  audience?: string;
  keywords?: string[];
  windowDays?: number;
  idempotencyKey: string;
}) {
  return request<ApiTask>("/trend-tasks", {
    method: "POST",
    headers: { "Idempotency-Key": args.idempotencyKey },
    body: JSON.stringify({
      direction: args.direction,
      audience: args.audience ?? "",
      keywords: args.keywords ?? [],
      window_days: args.windowDays ?? 7,
      conversation_id: args.conversationId,
    }),
  });
}

export function createApiBreakdownTask(args: {
  prompt: string;
  conversationId?: string;
  sourceIds: string[];
  idempotencyKey: string;
  preset?: "default" | "drawer_compact";
  knowledgeRefs?: Array<{ library_type: "collections" | "breakdowns" | "creations"; record_id: string }>;
  attachmentIds?: string[];
}) {
  return request<ApiTask>("/breakdown-tasks", {
    method: "POST",
    headers: { "Idempotency-Key": args.idempotencyKey },
    body: JSON.stringify({
      prompt: args.prompt,
      source_ids: args.sourceIds,
      knowledge_refs: args.knowledgeRefs ?? [],
      attachment_ids: args.attachmentIds ?? [],
      preset: args.preset ?? "default",
      conversation_id: args.conversationId,
    }),
  });
}

export function createApiCreationTask(args: {
  creationType: "original" | "rewrite";
  prompt: string;
  conversationId?: string;
  sourceIds: string[];
  knowledgeRefs: Array<{ library_type: "collections" | "breakdowns" | "creations"; record_id: string }>;
  trendTaskId?: string;
  attachmentIds?: string[];
  idempotencyKey: string;
}) {
  return request<ApiTask>("/creation-tasks", {
    method: "POST",
    headers: { "Idempotency-Key": args.idempotencyKey },
    body: JSON.stringify({
      creation_type: args.creationType,
      prompt: args.prompt,
      source_ids: args.sourceIds,
      knowledge_refs: args.knowledgeRefs,
      attachment_ids: args.attachmentIds ?? [],
      target_platform: "小红书",
      trend_task_id: args.trendTaskId,
      conversation_id: args.conversationId,
    }),
  });
}

export function getCollectionProvider() {
  return request<ApiCollectionProvider>("/collection-provider");
}

export function getTranscriptionProvider() {
  return request<ApiTranscriptionProvider>("/transcription-provider");
}

export function getCoverProvider() {
  return request<ApiCoverProvider>("/cover-provider");
}

export function listApiMediaAssets() {
  return request<{ items: ApiMediaAsset[]; total: number }>("/media-assets");
}

export function createApiMediaAsset(args: {
  filename: string;
  mimeType: "image/png" | "image/jpeg";
  role: "original" | "reference";
  rightsConfirmed: boolean;
  dataUrl: string;
}) {
  return request<ApiMediaAsset>("/media-assets", {
    method: "POST",
    body: JSON.stringify({
      filename: args.filename,
      mime_type: args.mimeType,
      role: args.role,
      rights_confirmed: args.rightsConfirmed,
      data_url: args.dataUrl,
    }),
  });
}

export function createApiAttachment(args: {
  filename: string;
  mimeType: "image/png" | "image/jpeg" | "text/plain" | "text/markdown" | "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  dataUrl: string;
}) {
  return request<ApiAttachment>("/attachments", {
    method: "POST",
    body: JSON.stringify({
      filename: args.filename,
      mime_type: args.mimeType,
      data_url: args.dataUrl,
    }),
  });
}

export function listApiCovers() {
  return request<{ items: ApiCover[]; total: number }>("/covers");
}

export function createApiCoverTask(args: {
  prompt: string;
  mediaAssetIds: string[];
  creationId?: string;
  usageConfirmed?: boolean;
  idempotencyKey: string;
}) {
  return request<ApiTask>("/cover-tasks", {
    method: "POST",
    headers: { "Idempotency-Key": args.idempotencyKey },
    body: JSON.stringify({
      prompt: args.prompt,
      media_asset_ids: args.mediaAssetIds,
      creation_id: args.creationId || undefined,
      usage_confirmed: args.usageConfirmed ?? false,
    }),
  });
}

export function createApiCoverRevision(args: {
  coverId: string;
  prompt: string;
  usageConfirmed?: boolean;
  idempotencyKey: string;
}) {
  return request<ApiTask>(`/covers/${encodeURIComponent(args.coverId)}/revisions`, {
    method: "POST",
    headers: { "Idempotency-Key": args.idempotencyKey },
    body: JSON.stringify({
      prompt: args.prompt,
      usage_confirmed: args.usageConfirmed ?? false,
    }),
  });
}

export function saveApiCover(coverId: string) {
  return request<{ id: string; status: "saved"; saved_at: string }>(
    `/covers/${encodeURIComponent(coverId)}/save`,
    { method: "POST" },
  );
}

export function getFeishuProvider() {
  return request<ApiFeishuProvider>("/integrations/feishu/provider");
}

export function listApiFeishuConnections() {
  return request<{ items: ApiFeishuConnection[]; total: number }>(
    "/integrations/feishu/connections",
  );
}

export function createApiFeishuConnection(args: {
  tenantName: string;
  externalCopyConfirmed: boolean;
}) {
  return request<ApiFeishuConnection>("/integrations/feishu/connections", {
    method: "POST",
    body: JSON.stringify({
      tenant_name: args.tenantName,
      external_copy_confirmed: args.externalCopyConfirmed,
    }),
  });
}

export function listApiFeishuTargets(
  connectionId: string,
  args?: { baseUrl?: string; scopeKey?: FeishuScope },
) {
  const query = new URLSearchParams({ connection_id: connectionId });
  if (args?.baseUrl) query.set("base_url", args.baseUrl);
  if (args?.scopeKey) query.set("scope_key", args.scopeKey);
  return request<{
    items: ApiFeishuTarget[];
    total: number;
    provider: string;
    external_calls: boolean;
  }>(`/integrations/feishu/targets?${query.toString()}`);
}

export function feishuOAuthAuthorizeUrl(scopeKey: FeishuScope) {
  return `/api/riffloom/integrations/feishu/oauth/authorize?scope_key=${encodeURIComponent(scopeKey)}`;
}

export function listApiFeishuBindings() {
  return request<{ items: ApiFeishuBinding[]; total: number }>(
    "/integrations/feishu/bindings",
  );
}

type FeishuBindingDraft = {
  connectionId: string;
  scopeKey: FeishuScope;
  targetBaseId: string;
  targetTableId: string;
  targetTableName: string;
  fieldMapping?: Record<string, string>;
};

function feishuBindingBody(args: FeishuBindingDraft) {
  return {
    connection_id: args.connectionId,
    scope_key: args.scopeKey,
    target_base_id: args.targetBaseId,
    target_table_id: args.targetTableId,
    target_table_name: args.targetTableName,
    field_mapping: args.fieldMapping ?? {},
  };
}

export function preflightApiFeishuBinding(args: FeishuBindingDraft) {
  return request<{
    valid: boolean;
    scope_key: FeishuScope;
    field_mapping: Record<string, string>;
    required_fields: string[];
    sample: Record<string, unknown>;
    errors: Array<{ code: string; message: string }>;
    provider: string;
    external_calls: boolean;
  }>("/integrations/feishu/bindings/preflight", {
    method: "POST",
    body: JSON.stringify(feishuBindingBody(args)),
  });
}

export function createApiFeishuBinding(args: FeishuBindingDraft) {
  return request<ApiFeishuBinding>("/integrations/feishu/bindings", {
    method: "POST",
    body: JSON.stringify({
      ...feishuBindingBody(args),
      strategy: "manual_incremental",
      external_copy_confirmed: true,
    }),
  });
}

export function updateApiFeishuBinding(args: {
  bindingId: string;
  paused?: boolean;
  targetTableName?: string;
  fieldMapping?: Record<string, string>;
}) {
  return request<ApiFeishuBinding>(
    `/integrations/feishu/bindings/${encodeURIComponent(args.bindingId)}`,
    {
      method: "PATCH",
      body: JSON.stringify({
        paused: args.paused,
        target_table_name: args.targetTableName,
        field_mapping: args.fieldMapping,
      }),
    },
  );
}

export function createApiFeishuSync(args: {
  bindingId: string;
  mode: "full" | "incremental";
  idempotencyKey: string;
  sourceRecordId?: string;
}) {
  return request<ApiTask>(
    `/integrations/feishu/bindings/${encodeURIComponent(args.bindingId)}/syncs`,
    {
      method: "POST",
      headers: { "Idempotency-Key": args.idempotencyKey },
      body: JSON.stringify({ mode: args.mode, source_record_id: args.sourceRecordId }),
    },
  );
}

export function retryApiFeishuSync(runId: string, idempotencyKey: string) {
  return request<ApiTask>(
    `/integrations/feishu/syncs/${encodeURIComponent(runId)}/retries`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    },
  );
}

export function resolveApiContentUrl(path: string) {
  if (/^https?:\/\//.test(path)) return path;
  const backendPath = path.replace(/^\/api\/v1/, "");
  return new URL(
    `${API_BASE_URL}${backendPath.startsWith("/") ? backendPath : `/${backendPath}`}`,
    window.location.origin,
  ).toString();
}

export function createApiCollectionTask(args: {
  kind: CollectionKind;
  platform: string;
  value: string;
  usageConfirmed: boolean;
  refresh?: boolean;
  limit?: number;
  contentType?: "image" | "video";
  publishTime?: "day" | "week" | "month" | "half-year" | "anytime";
  idempotencyKey: string;
  attachmentIds?: string[];
}) {
  const queryField: Record<CollectionKind, string> = {
    single: "url",
    keyword: "keyword",
    creator_content: "creator",
    creator_profile: "creator",
  };
  return request<ApiTask>("/collection-tasks", {
    method: "POST",
    headers: { "Idempotency-Key": args.idempotencyKey },
    body: JSON.stringify({
      kind: args.kind,
      platform: args.platform,
      query: {
        [queryField[args.kind]]: args.value,
        ...(args.kind === "keyword"
          ? {
              sort: "most-liked",
              publish_time: args.publishTime ?? "week",
              content_type: args.contentType ?? "image",
            }
          : {}),
      },
      usage_confirmed: args.usageConfirmed,
      refresh: args.refresh ?? false,
      limit: args.kind === "single" || args.kind === "creator_profile" ? 1 : (args.limit ?? 20),
      attachment_ids: args.attachmentIds ?? [],
    }),
  });
}

export function createApiCollectRewriteTask(args: {
  url?: string;
  collectionId?: string;
  prompt: string;
  usageConfirmed?: boolean;
  targetPlatform?: string;
  audience?: string;
  knowledgeRefs?: Array<{ library_type: "collections" | "breakdowns" | "creations"; record_id: string }>;
  attachmentIds?: string[];
  idempotencyKey: string;
}) {
  return request<ApiTask>("/collect-rewrite-tasks", {
    method: "POST",
    headers: { "Idempotency-Key": args.idempotencyKey },
    body: JSON.stringify({
      url: args.url,
      collection_id: args.collectionId,
      prompt: args.prompt,
      usage_confirmed: args.usageConfirmed ?? false,
      target_platform: args.targetPlatform ?? "小红书",
      audience: args.audience ?? "",
      knowledge_refs: args.knowledgeRefs ?? [],
      attachment_ids: args.attachmentIds ?? [],
    }),
  });
}

export function updateApiCollectionMetadata(args: {
  recordIds: string[];
  benchmark?: boolean;
  categoryTags?: string[];
}) {
  return request<{ updated_ids: string[] }>("/collections/batch-metadata", {
    method: "PATCH",
    body: JSON.stringify({
      record_ids: args.recordIds,
      benchmark: args.benchmark,
      category_tags: args.categoryTags,
    }),
  });
}

export function createApiCollectionBatchTasks(args: {
  action: "breakdown" | "rewrite";
  recordIds: string[];
  prompt?: string;
  idempotencyKey: string;
}) {
  return request<{
    tasks: ApiTask[];
    issues: Array<{ record_id: string; code: string; message: string; retryable: boolean }>;
  }>("/collection-batch-tasks", {
    method: "POST",
    headers: { "Idempotency-Key": args.idempotencyKey },
    body: JSON.stringify({
      action: args.action,
      record_ids: args.recordIds,
      prompt: args.prompt ?? "",
    }),
  });
}

export function retryApiTask(taskId: string) {
  return request<ApiTask>(`/tasks/${encodeURIComponent(taskId)}/retry`, {
    method: "POST",
    headers: { "Idempotency-Key": `retry-${taskId}-${crypto.randomUUID()}` },
  });
}

export function cancelApiTask(taskId: string) {
  return request<ApiTask>(`/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
  });
}

export function listApiLibrary(page: "collections" | "breakdowns" | "creations", kind?: CollectionKind) {
  const query = page === "collections" && kind ? `?kind=${encodeURIComponent(kind)}` : "";
  return request<{ items: ApiLibraryRecord[]; total: number }>(`/libraries/${page}${query}`);
}

export function deleteApiLibraryRecord(
  libraryType: "collections" | "breakdowns" | "creations",
  recordId: string,
) {
  return request<{ id: string; library_type: typeof libraryType; status: "deleted"; deleted_at: string }>(
    `/libraries/${libraryType}/${encodeURIComponent(recordId)}`,
    { method: "DELETE" },
  );
}

export function getApiCollection(recordId: string) {
  return request<ApiCollectionDetail>(`/collections/${encodeURIComponent(recordId)}`);
}

type TranscriptionTaskArgs = {
  recordId: string;
  filename: string;
  mimeType: "audio/mpeg" | "audio/mp3" | "audio/wav" | "audio/x-wav" | "audio/mp4" | "audio/x-m4a" | "video/mp4";
  durationSeconds: number;
  rightsConfirmed: boolean;
  idempotencyKey: string;
} & ({ dataUrl: string; assetId?: never } | { assetId: string; dataUrl?: never });

export function initApiTranscriptionUpload(args: {
  filename: string;
  mimeType: TranscriptionTaskArgs["mimeType"];
  byteSize: number;
  durationSeconds: number;
  rightsConfirmed: boolean;
}) {
  return request<ApiTranscriptionUpload>("/transcription-uploads", {
    method: "POST",
    body: JSON.stringify({
      filename: args.filename,
      mime_type: args.mimeType,
      byte_size: args.byteSize,
      duration_seconds: args.durationSeconds,
      language: "zh",
      rights_confirmed: args.rightsConfirmed,
    }),
  });
}

export async function putApiTranscriptionUpload(
  presignedPutUrl: string,
  file: File,
  mimeType: TranscriptionTaskArgs["mimeType"],
) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 120_000);
  try {
    const response = await fetch(presignedPutUrl, {
      method: "PUT",
      headers: { "Content-Type": mimeType },
      body: file,
      credentials: "omit",
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new ApiError("TOS_UPLOAD_FAILED", "媒体直传对象存储失败，请重新选择文件后重试", true, response.status);
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      "TOS_UPLOAD_FAILED",
      error instanceof DOMException && error.name === "AbortError"
        ? "媒体直传超时，请检查网络后重试"
        : "媒体直传对象存储失败，请检查网络和存储跨域配置",
      true,
    );
  } finally {
    window.clearTimeout(timeout);
  }
}

export function createApiTranscriptionTask(args: TranscriptionTaskArgs) {
  return request<ApiTask>(
    `/collections/${encodeURIComponent(args.recordId)}/transcription-tasks`,
    {
      method: "POST",
      headers: { "Idempotency-Key": args.idempotencyKey },
      body: JSON.stringify({
        filename: args.filename,
        mime_type: args.mimeType,
        duration_seconds: args.durationSeconds,
        language: "zh",
        rights_confirmed: args.rightsConfirmed,
        ...(args.assetId ? { asset_id: args.assetId } : { data_url: args.dataUrl }),
      }),
    },
  );
}

export function getApiCreation(recordId: string) {
  return request<ApiCreationDetail>(`/creations/${encodeURIComponent(recordId)}`);
}

export function getApiBreakdown(recordId: string) {
  return request<ApiBreakdownDetail>(`/breakdowns/${encodeURIComponent(recordId)}`);
}

export function createApiCreationVersion(args: {
  recordId: string;
  baseVersionId: string;
  body: string;
  changeNote: string;
}) {
  return request<ApiCreationDetail["versions"][number]>(
    `/creations/${encodeURIComponent(args.recordId)}/versions`,
    {
      method: "POST",
      headers: { "Idempotency-Key": `version-${args.recordId}-${crypto.randomUUID()}` },
      body: JSON.stringify({
        base_version_id: args.baseVersionId,
        body: args.body,
        change_note: args.changeNote,
      }),
    },
  );
}

export function adoptApiCreation(recordId: string, versionId: string) {
  return request<{ record_id: string; version_id: string; status: "adopted" }>(
    `/creations/${encodeURIComponent(recordId)}/adoptions`,
    {
      method: "POST",
      headers: { "Idempotency-Key": `adopt-${recordId}-${versionId}` },
      body: JSON.stringify({ version_id: versionId }),
    },
  );
}

const statusMap: Record<ApiTask["status"], UnifiedTaskStatus> = {
  queued: "queued",
  running: "running",
  success: "succeeded",
  partial_success: "partially_succeeded",
  failed: "failed",
  cancelled: "cancelled",
};

export function taskFromApi(task: ApiTask): Task {
  const kindByType: Record<string, Task["kind"]> = {
    collect_content: "采集",
    viral_breakdown: "拆解",
    cover_generation: "封面",
    cover_revision: "封面",
    feishu_sync: "飞书",
    collect_breakdown_rewrite: "创作",
    copy_rewrite: "创作",
    original_copy: "创作",
    viral_topic_coach: "创作",
  };
  const isCollection = task.type.startsWith("collection_");
  const kind = isCollection ? "采集" : (kindByType[task.type] ?? "创作");
  const collectionLabels: Record<string, string> = {
    collection_single: "单篇内容采集",
    collection_keyword: "关键词搜索",
    collection_creator_content: "博主内容采集",
    collection_creator_profile: "博主信息采集",
    collection_video_transcription: "补齐视频文案",
  };
  const taskLabels: Record<string, string> = {
    riffloom_agent: "Riffloom 智能体",
    viral_breakdown: "爆款拆解",
    original_copy: "文案原创",
    copy_rewrite: "文案仿写",
    viral_topic_coach: "爆款选题指导",
    cover_generation: "生成 4 个封面方案",
    cover_revision: "封面修改方案",
    feishu_sync: "飞书单向同步",
  };
  const summary = task.result_summary;
  const collectionDetail = isCollection && typeof summary.total === "number"
    ? `共 ${summary.total} 项 · 新增 ${summary.new ?? 0} · 复用 ${summary.reused ?? 0} · 失败 ${summary.failed ?? 0}${task.error?.message ? ` · ${task.error.message}` : ""}`
    : null;
  const coverDetail = task.type.startsWith("cover_") && typeof summary.generated === "number"
    ? `共 ${summary.generated}/4 个方案 · 失败 ${summary.failed ?? 0} · 3:4${task.error?.message ? ` · ${task.error.message}` : ""}`
    : null;
  const feishuDetail = task.type === "feishu_sync" && typeof summary.total === "number"
    ? `共 ${summary.total} 条 · 成功 ${summary.success ?? 0} · 跳过 ${summary.skipped ?? 0} · 失败 ${summary.failed ?? 0}`
    : null;
  return {
    id: task.id,
    kind,
    title: task.type === "collect_breakdown_rewrite" ? "一键采集仿写" : (collectionLabels[task.type] ?? taskLabels[task.type] ?? task.type),
    status: statusMap[task.status] ?? "stale",
    stage: task.stage,
    progress: task.progress,
    detail: collectionDetail ?? coverDetail ?? feishuDetail ?? task.error?.message ?? (task.status === "success" ? "采集、拆解与创作结果已持久化" : `第 ${task.current_attempt} 次执行`),
    resultPage: task.type === "riffloom_agent" || task.type === "viral_topic_coach"
      ? "chat"
      : isCollection
      ? "collections"
      : task.result_refs.some((ref) => ref.type === "creation")
        ? "creations"
        : task.result_refs.some((ref) => ref.type === "breakdown")
          ? "breakdowns"
          : task.result_refs.some((ref) => ref.type === "collection")
            ? "collections"
            : undefined,
    retryable: task.error?.retryable,
    attempt: task.current_attempt,
    resultRefs: task.result_refs,
    errorCode: task.error?.code,
    source: "api",
    createdAt: new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(task.created_at)),
  };
}

export function recordFromApi(record: ApiLibraryRecord): LibraryRecord {
  return {
    id: record.id,
    title: record.title,
    status: collectionRecordStatusLabel(record.status),
    type: record.type,
    source: record.source,
    author: record.author,
    updatedAt: new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(record.updated_at)),
    updatedAtIso: record.updated_at,
    publishedAt: record.published_at ?? undefined,
    summary: record.summary,
    tags: record.tags,
    version: record.version,
    sourceId: record.source_id,
    taskId: record.task_id,
    adoptedVersionId: record.adopted_version_id ?? undefined,
    sourceKind: "api",
    libraryType: record.library_type,
    collectionKind: record.collection_kind ?? undefined,
    provider: record.provider ?? undefined,
    externalUrl: record.external_url ?? undefined,
    thumbnailUrl: record.thumbnail_url ? resolveApiContentUrl(record.thumbnail_url) : undefined,
    metrics: record.metrics,
    contentType: record.content_type ?? undefined,
    benchmark: record.benchmark ?? false,
    categoryTags: record.category_tags ?? [],
    isSandbox: record.is_sandbox,
  };
}

function isTopicSource(value: unknown): value is ApiTopicSource {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return typeof item.platform === "string"
    && typeof item.source_id === "string"
    && typeof item.observed_at === "string";
}

export function topicCandidatesFromTask(task: ApiTask): ApiTopicCandidate[] {
  if (task.skill_id !== "viral_topic_coach") return [];
  const candidates = task.result_summary.candidates;
  if (!Array.isArray(candidates)) return [];
  return candidates.flatMap((value) => {
    if (!value || typeof value !== "object") return [];
    const item = value as Record<string, unknown>;
    if (
      typeof item.topic !== "string"
      || typeof item.angle !== "string"
      || typeof item.audience !== "string"
      || typeof item.why_hot !== "string"
      || typeof item.core_value !== "string"
      || typeof item.title_suggestion !== "string"
    ) return [];
    const sources = Array.isArray(item.sources) ? item.sources.filter(isTopicSource) : [];
    return [{
      topic: item.topic,
      why_hot: item.why_hot,
      audience: item.audience,
      core_value: item.core_value,
      title_suggestion: item.title_suggestion,
      angle: item.angle,
      sources,
    }];
  });
}

export function pageHasApiLibrary(page: PageKey): page is "collections" | "breakdowns" | "creations" {
  return page === "collections" || page === "breakdowns" || page === "creations";
}
