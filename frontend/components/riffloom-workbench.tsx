"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowUp,
  BookOpen,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Copy,
  Database,
  Download,
  Eye,
  ExternalLink,
  FileText,
  ImageIcon,
  KeyRound,
  Layers3,
  Link2,
  LogOut,
  MessageSquareText,
  MoreHorizontal,
  PenLine,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  X,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  adoptApiCreation,
  apiIntegrationEnabled,
  cancelApiTask,
  completeApiOnboarding,
  createApiAttachment,
  createApiBreakdownTask,
  createApiCollectionTask,
  createApiCollectionBatchTasks,
  createApiCollectRewriteTask,
  createApiCoverRevision,
  createApiCoverTask,
  createApiCreationTask,
  createApiCreationVersion,
  deleteApiConversation,
  deleteApiLibraryRecord,
  createApiFeishuBinding,
  createApiFeishuConnection,
  createApiFeishuSync,
  createApiMediaAsset,
  createApiTask,
  createApiTranscriptionTask,
  initApiTranscriptionUpload,
  putApiTranscriptionUpload,
  createApiTrendTask,
  getCoverProvider,
  getFeishuProvider,
  getApiCollection,
  getApiConversation,
  getApiBreakdown,
  getApiCreation,
  getApiTask,
  getCollectionProvider,
  getHealth,
  getSession,
  getTranscriptionProvider,
  feishuOAuthAuthorizeUrl,
  listApiLibrary,
  listApiCovers,
  listApiConversations,
  listApiFeishuBindings,
  listApiFeishuConnections,
  listApiFeishuTargets,
  listApiMediaAssets,
  listApiTasks,
  pageHasApiLibrary,
  recordFromApi,
  preflightApiFeishuBinding,
  retryApiFeishuSync,
  retryApiTask,
  resolveApiContentUrl,
  redeemInvitation,
  saveApiCover,
  signOut as signOutApi,
  taskFromApi,
  topicCandidatesFromTask,
  updateApiCollectionMetadata,
  updateApiConversation,
  type ApiCollectionDetail,
  type ApiConversationSummary,
  type ApiAttachment,
  type ApiCollectionProvider,
  type ApiCover,
  type ApiCoverProvider,
  type ApiFeishuBinding,
  type ApiFeishuConnection,
  type ApiFeishuProvider,
  type ApiFeishuTarget,
  type ApiBreakdownDetail,
  type ApiCreationDetail,
  type ApiMediaAsset,
  type ApiTask,
  type ApiTopicCandidate,
  type ApiTranscriptionProvider,
  type FeishuScope,
  updateApiFeishuBinding,
} from "@/lib/api";
import { CollectionTaskModal, type CollectionTaskDraft } from "@/components/collection-task-modal";
import { CollectRewriteTaskModal, type CollectRewriteDraft } from "@/components/collect-rewrite-task-modal";
import { useDialogLifecycle } from "@/components/use-dialog-lifecycle";
import {
  collectionRecords,
  creationRecords,
  breakdownRecords,
  initialTasks,
  skills,
} from "@/lib/fixtures";
import {
  canRetryTask,
  collectionMetricLabel,
  collectionProviderLabel,
  modeConfig,
  pagePath,
  readableParagraphs,
  readableTranscript,
  taskStatusMeta,
  type ChatMode,
  type CollectionKind,
  type LibraryRecord,
  type PageKey,
  type Task,
} from "@/lib/domain";

type WorkbenchProps = {
  initialPage: PageKey;
  initialRecordId?: string;
  initialTaskId?: string;
  initialMode?: string;
  initialSourceIds?: string[];
};

type ChatMessage = {
  id: string;
  role: "user" | "agent";
  text: string;
  taskId?: string;
  resultId?: string;
  resultPage?: "breakdowns" | "creations";
  topicCandidates?: ApiTopicCandidate[];
  restored?: boolean;
  chatReply?: boolean;
};

type ChatModeSession = {
  input: string;
  selectedSkills: string[];
  messages: ChatMessage[];
  agentConversationId?: string;
  selectedSourceIds: string[];
  selectedKnowledgeIds: string[];
  attachments: ApiAttachment[];
  trendSourceTaskId?: string;
};

function createEmptyChatModeSessions(): Record<ChatMode, ChatModeSession> {
  const empty = (): ChatModeSession => ({
    input: "",
    selectedSkills: [],
    messages: [],
    selectedSourceIds: [],
    selectedKnowledgeIds: [],
    attachments: [],
  });
  return {
    agent: empty(),
    capture: empty(),
    breakdown: empty(),
    creation: empty(),
    hot: empty(),
  };
}

type ApiStatus = "scenario" | "connecting" | "connected" | "disconnected" | "auth_required";

function isSessionAuthError(error: unknown) {
  return error instanceof ApiError
    && ["AUTH_REQUIRED", "AUTH_HEADER_INVALID", "AUTH_SESSION_INVALID"].includes(error.code);
}

const skillIds: Record<string, string> = {
  爆款拆解: "viral_breakdown",
  爆款选题指导: "viral_topic_coach",
  采集内容: "collect_content",
  文案仿写: "copy_rewrite",
  一键采集仿写: "collect_breakdown_rewrite",
  文案原创: "original_copy",
};

const emptySourceIds: string[] = [];

function feishuScopeForPage(
  page: "collections" | "breakdowns" | "creations",
  collectionKind: CollectionKind,
): FeishuScope {
  if (page === "breakdowns") return "breakdown";
  if (page === "creations") return "creation";
  return `collection.${collectionKind}`;
}

const navItems = [
  { key: "chat" as const, label: "对话", icon: MessageSquareText },
  { key: "collections" as const, label: "采集库", icon: Database },
  { key: "breakdowns" as const, label: "拆解库", icon: Layers3 },
  { key: "creations" as const, label: "创作库", icon: PenLine },
  { key: "covers" as const, label: "封面设计", icon: ImageIcon },
];

const onboardingSteps = [
  {
    title: "欢迎来到 Riffloom",
    description: "用一条对话完成内容采集、拆解、创作和封面设计。接下来用一分钟认识工作台。",
    icon: Sparkles,
  },
  {
    title: "从对话开始",
    description: "直接说出你想完成的内容任务，也可以在对话框粘贴小红书等内容链接，继续采集、拆解或创作。",
    target: "chat",
    icon: MessageSquareText,
  },
  {
    title: "沉淀采集内容",
    description: "单篇内容、关键词结果和博主资料都会保存在采集库，方便后续引用和同步。",
    target: "collections",
    icon: Database,
  },
  {
    title: "拆出爆款方法",
    description: "把已采集的内容拆成结构、钩子和情绪等可复用方法，统一收进拆解库。",
    target: "breakdowns",
    icon: Layers3,
  },
  {
    title: "管理创作版本",
    description: "生成、编辑和采用不同版本的文案，所有创作结果都会保存在创作库。",
    target: "creations",
    icon: PenLine,
  },
  {
    title: "生成内容封面",
    description: "基于创作内容生成封面并继续调整，让图文内容可以直接进入发布流程。",
    target: "covers",
    icon: ImageIcon,
  },
  {
    title: "随时查看任务进度",
    description: "采集、拆解和创作会在后台运行。打开任务中心即可查看进度、失败原因或重试。",
    target: "tasks",
    icon: Zap,
  },
] as const;

const toolLabels = {
  knowledge: "引用知识",
  skills: "引用技能",
};

const attachmentTypes = [
  "image/png",
  "image/jpeg",
  "text/plain",
  "text/markdown",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
] as const;

type AttachmentMime = (typeof attachmentTypes)[number];

function attachmentMime(file: File): AttachmentMime | undefined {
  if (attachmentTypes.includes(file.type as AttachmentMime)) return file.type as AttachmentMime;
  const fallback: Record<string, AttachmentMime> = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  };
  return fallback[file.name.slice(file.name.lastIndexOf(".")).toLowerCase()];
}

function nextId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function firstHttpsUrl(value: string) {
  return value.match(/https:\/\/[^\s]+/)?.[0];
}

function taskInputLabel(input: Record<string, unknown>) {
  if (typeof input.prompt === "string") return input.prompt;
  const query = input.query;
  if (query && typeof query === "object") {
    const value = Object.values(query as Record<string, unknown>).find((item) => typeof item === "string");
    if (typeof value === "string") return value;
  }
  return "恢复上次任务";
}

function chatModeFromApiMode(mode: string): ChatMode {
  if (mode === "collection") return "capture";
  if (mode === "trend") return "hot";
  if (mode === "breakdown" || mode === "creation") return mode;
  return "agent";
}

function messagesFromTasks(messages: ChatMessage[], tasks: ApiTask[]) {
  return messages.map((message) => {
    if (!message.taskId) return message;
    const task = tasks.find((item) => item.id === message.taskId);
    if (!task) return message;
    return {
      ...message,
      resultId: task.result_refs.find((ref) => ref.type === (task.result_refs.some((item) => item.type === "creation") ? "creation" : "breakdown"))?.id,
      resultPage: task.result_refs.some((ref) => ref.type === "creation") ? "creations" as const : task.result_refs.some((ref) => ref.type === "breakdown") ? "breakdowns" as const : undefined,
      topicCandidates: task.status === "success" ? topicCandidatesFromTask(task) : message.topicCandidates,
      text: task.status === "success"
        ? task.type === "riffloom_agent"
          ? typeof task.result_summary.reply === "string" ? task.result_summary.reply : "Riffloom 智能体已回复。"
          : task.type.startsWith("collection_")
          ? `采集任务已完成：${task.result_summary.total ?? 0} 项已处理，可在对应采集库查看。`
          : task.type === "viral_breakdown"
            ? "结构化拆解已完成，结果已保存到拆解库。"
            : task.type === "original_copy" || task.type === "copy_rewrite"
              ? "创作版本已生成并保存到创作库，可查看来源、知识快照和风险。"
              : task.type === "viral_topic_coach"
                ? `已根据 TikHub 近 ${task.input.window_days ?? 7} 天公开信号生成 ${topicCandidatesFromTask(task).length} 条热点。`
          : message.restored
            ? "任务已从 API 恢复，采集、拆解和创作结果均已持久化。"
            : "一键采集仿写已完成：采集、拆解和创作结果已分别入库。"
        : task.error?.message ?? `${task.stage} · ${task.progress}%`,
    };
  });
}

function AppIcon({ name }: { name: string }) {
  const icons: Record<string, typeof Sparkles> = {
    爆款拆解: Layers3,
    爆款选题指导: Zap,
    采集内容: Database,
    文案仿写: RefreshCw,
    一键采集仿写: Link2,
    文案原创: PenLine,
  };
  const Icon = icons[name] ?? Sparkles;
  return <Icon aria-hidden="true" />;
}

function StatusPill({ status }: { status: Task["status"] }) {
  const meta = taskStatusMeta[status];
  return <span className={`status-pill tone-${meta.tone}`}>{meta.label}</span>;
}

export function RiffloomWorkbench({ initialPage, initialRecordId, initialTaskId, initialMode, initialSourceIds = emptySourceIds }: WorkbenchProps) {
  const router = useRouter();
  const [collapsed, setCollapsed] = useState(false);
  const [taskCenterOpen, setTaskCenterOpen] = useState(false);
  const [focusedCollectionTaskId, setFocusedCollectionTaskId] = useState<string>();
  const [profileOpen, setProfileOpen] = useState(false);
  const [mode, setMode] = useState<ChatMode>(initialMode === "breakdown" ? "breakdown" : initialMode === "creation" ? "creation" : "agent");
  const [input, setInput] = useState("");
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [agentConversationId, setAgentConversationId] = useState<string>();
  const [agentConversations, setAgentConversations] = useState<ApiConversationSummary[]>([]);
  const [conversationMenuId, setConversationMenuId] = useState<string>();
  const [renamingConversationId, setRenamingConversationId] = useState<string>();
  const [conversationTitleDraft, setConversationTitleDraft] = useState("");
  const [conversationActionBusyId, setConversationActionBusyId] = useState<string>();
  const [tasks, setTasks] = useState<Task[]>(apiIntegrationEnabled ? [] : initialTasks);
  const [drawerRecord, setDrawerRecord] = useState<LibraryRecord | null>(null);
  const [drawerTab, setDrawerTab] = useState<"content" | "versions" | "source" | "audit">("content");
  const [adoptedVersions, setAdoptedVersions] = useState<Record<string, string>>({ "C-002": "v3" });
  const [query, setQuery] = useState("");
  const [collectionTab, setCollectionTab] = useState<CollectionKind>("single");
  const [collectionModalOpen, setCollectionModalOpen] = useState(false);
  const [collectionInitialValue, setCollectionInitialValue] = useState("");
  const [collectionProvider, setCollectionProvider] = useState<ApiCollectionProvider | null>(null);
  const [coverProvider, setCoverProvider] = useState<ApiCoverProvider | null>(null);
  const [collectionSubmitting, setCollectionSubmitting] = useState(false);
  const [collectRewriteModal, setCollectRewriteModal] = useState<{
    source: { url?: string; collectionId?: string };
    prompt: string;
  } | null>(null);
  const [feishuBindings, setFeishuBindings] = useState<Partial<Record<FeishuScope, ApiFeishuBinding>>>({});
  const [feishuProvider, setFeishuProvider] = useState<ApiFeishuProvider | null>(null);
  const [feishuModal, setFeishuModal] = useState<FeishuScope | null>(null);
  const [toast, setToast] = useState("");
  const [onboardingStep, setOnboardingStep] = useState<number | null>(null);
  const [onboardingSaving, setOnboardingSaving] = useState(false);
  const [apiStatus, setApiStatus] = useState<ApiStatus>(apiIntegrationEnabled ? "connecting" : "scenario");
  const [modelProvider, setModelProvider] = useState("mock-v1");
  const [submitting, setSubmitting] = useState(false);
  const [sessionName, setSessionName] = useState(apiIntegrationEnabled ? "正在加载工作区" : "示例内容团队");
  const [sessionUserName, setSessionUserName] = useState(apiIntegrationEnabled ? "正在加载" : "示例用户");
  const [sessionRole, setSessionRole] = useState("editor");
  const [sessionAuthMode, setSessionAuthMode] = useState<"demo_headers" | "invite_token">("demo_headers");
  const [authEpoch, setAuthEpoch] = useState(0);
  const [authSubmitting, setAuthSubmitting] = useState(false);
  const [authError, setAuthError] = useState("");
  const [apiRecords, setApiRecords] = useState<Partial<Record<"collections" | "breakdowns" | "creations", LibraryRecord[]>>>({});
  const [creationDetail, setCreationDetail] = useState<ApiCreationDetail | null>(null);
  const [breakdownDetail, setBreakdownDetail] = useState<ApiBreakdownDetail | null>(null);
  const [collectionDetail, setCollectionDetail] = useState<ApiCollectionDetail | null>(null);
  const [transcriptionProvider, setTranscriptionProvider] = useState<ApiTranscriptionProvider | null>(null);
  const [contextRecords, setContextRecords] = useState<LibraryRecord[]>(
    apiIntegrationEnabled ? [] : [
      ...collectionRecords.map((record) => ({ ...record, libraryType: "collections" as const })),
      ...breakdownRecords.map((record) => ({ ...record, libraryType: "breakdowns" as const })),
      ...creationRecords.map((record) => ({ ...record, libraryType: "creations" as const })),
    ],
  );
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [selectedKnowledgeIds, setSelectedKnowledgeIds] = useState<string[]>([]);
  const [knowledgePickerOpen, setKnowledgePickerOpen] = useState(false);
  const [skillsPickerOpen, setSkillsPickerOpen] = useState(false);
  const [attachments, setAttachments] = useState<ApiAttachment[]>([]);
  const [attachmentUploading, setAttachmentUploading] = useState(false);
  const [trendSourceTaskId, setTrendSourceTaskId] = useState<string | undefined>();
  const timers = useRef<Array<ReturnType<typeof setTimeout>>>([]);
  const handledAgentTasks = useRef(new Set<string>());
  const pendingAgentInputs = useRef<Record<string, string>>({});
  const focusedCollectionTaskIdRef = useRef<string | undefined>(undefined);
  const restoredTaskId = useRef<string | undefined>(undefined);
  const modeChangeRevision = useRef(0);
  const taskRoute = useRef({ taskId: initialTaskId, modeChangeRevision: 0 });
  const activeModeRef = useRef<ChatMode>(mode);
  const modeSessions = useRef<Record<ChatMode, ChatModeSession>>(createEmptyChatModeSessions());
  const taskStatusSnapshot = useRef(new Map(tasks.map((task) => [task.id, task.status])));

  if (taskRoute.current.taskId !== initialTaskId) {
    taskRoute.current = { taskId: initialTaskId, modeChangeRevision: modeChangeRevision.current };
  }

  useEffect(() => {
    activeModeRef.current = mode;
    modeSessions.current[mode] = {
      input,
      selectedSkills,
      messages,
      agentConversationId,
      selectedSourceIds,
      selectedKnowledgeIds,
      attachments,
      trendSourceTaskId,
    };
  }, [agentConversationId, attachments, input, messages, mode, selectedKnowledgeIds, selectedSkills, selectedSourceIds, trendSourceTaskId]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const scope = params.get("scope") as FeishuScope | null;
    if (params.get("feishu") !== "connected" || !scope) return;
    window.history.replaceState({}, "", window.location.pathname);
    const timer = window.setTimeout(() => {
      setFeishuModal(scope);
      setToast("已连接你的飞书，请选择本人有权编辑的多维表格和数据表");
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    taskStatusSnapshot.current = new Map(tasks.map((task) => [task.id, task.status]));
  }, [tasks]);

  useEffect(() => {
    if (apiIntegrationEnabled) return;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams(window.location.search);
      const taskId = params.get("task");
      if (!taskId) return;
      setMessages((current) =>
        current.some((message) => message.taskId === taskId)
          ? current
          : [
              { id: "restored-user", role: "user", text: "恢复上次的一键采集仿写任务" },
              {
                id: "restored-agent",
                role: "agent",
                taskId,
                text: "任务状态已从可分享链接恢复。采集、拆解和创作结果均已保留。",
              },
            ],
      );
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (!apiIntegrationEnabled) return;
    let cancelled = false;
    async function bootstrap() {
      setApiStatus("connecting");
      try {
        const [health, session, remoteTasks, provider, remoteTranscriptionProvider, remoteCoverProvider, remoteFeishuProvider, remoteFeishuBindings, remoteConversations] = await Promise.all([
          getHealth(),
          getSession(),
          listApiTasks(),
          getCollectionProvider(),
          getTranscriptionProvider(),
          getCoverProvider(),
          getFeishuProvider(),
          listApiFeishuBindings(),
          initialPage === "chat" ? listApiConversations() : Promise.resolve({ items: [], total: 0 }),
        ]);
        if (cancelled) return;
        setModelProvider(health.model_provider);
        setSessionName(session.workspace_name);
        setSessionUserName(session.user_name);
        setSessionRole(session.role);
        setSessionAuthMode(session.auth_mode);
        if (!session.onboarding_completed) {
          setCollapsed(false);
          setOnboardingStep(0);
        }
        setTasks(remoteTasks.items.map(taskFromApi));
        setCollectionProvider(provider);
        setTranscriptionProvider(remoteTranscriptionProvider);
        setCoverProvider(remoteCoverProvider);
        setFeishuProvider(remoteFeishuProvider);
        setFeishuBindings(Object.fromEntries(
          remoteFeishuBindings.items.map((binding) => [binding.scope_key, binding]),
        ));
        setAgentConversations(remoteConversations.items);
        setApiStatus("connected");
        if (pageHasApiLibrary(initialPage)) {
          const library = await listApiLibrary(initialPage, initialPage === "collections" ? "single" : undefined);
          if (!cancelled) {
            const records = library.items.map(recordFromApi);
            setApiRecords((current) => ({ ...current, [initialPage]: records }));
            const restoredRecord = records.find((record) => record.id === initialRecordId);
            if (restoredRecord) {
              setDrawerRecord(restoredRecord);
              setDrawerTab("content");
              if (initialPage === "creations") {
                try {
                  const detail = await getApiCreation(restoredRecord.id);
                  if (!cancelled) setCreationDetail(detail);
                } catch {
                  // The library can remain usable even when one detail record was removed.
                  if (!cancelled) setCreationDetail(null);
                }
              } else if (initialPage === "breakdowns") {
                try {
                  const detail = await getApiBreakdown(restoredRecord.id);
                  if (!cancelled) setBreakdownDetail(detail);
                } catch {
                  if (!cancelled) setBreakdownDetail(null);
                }
              } else if (initialPage === "collections") {
                try {
                  const detail = await getApiCollection(restoredRecord.id);
                  if (!cancelled) setCollectionDetail(detail);
                } catch {
                  if (!cancelled) setCollectionDetail(null);
                }
              }
            }
          }
        }
        const taskId = initialTaskId;
        if (taskId) {
          const task = await getApiTask(taskId);
          if (cancelled) return;
          if (restoredTaskId.current !== task.id) {
            restoredTaskId.current = task.id;
            const restoredMode = chatModeFromApiMode(task.mode);
            const restoredMessages: ChatMessage[] = [
              { id: `restored-user-${task.id}`, role: "user", text: taskInputLabel(task.input) },
              {
                id: `restored-agent-${task.id}`,
                role: "agent",
                taskId: task.id,
                resultId: task.result_refs.find((ref) => ref.type === (task.result_refs.some((item) => item.type === "creation") ? "creation" : "breakdown"))?.id,
                resultPage: task.result_refs.some((ref) => ref.type === "creation") ? "creations" : task.result_refs.some((ref) => ref.type === "breakdown") ? "breakdowns" : undefined,
                restored: true,
                chatReply: task.skill_id === "riffloom_agent",
                topicCandidates: topicCandidatesFromTask(task),
                text: task.status === "success"
                  ? task.type === "riffloom_agent"
                    ? typeof task.result_summary.reply === "string" ? task.result_summary.reply : "Riffloom 智能体已回复。"
                    : task.type.startsWith("collection_")
                    ? "采集任务已从 API 恢复，结果已持久化到对应采集库。"
                    : task.type === "viral_topic_coach"
                      ? `已恢复 ${topicCandidatesFromTask(task).length} 条 TikHub 近期热点。`
                      : "任务已从 API 恢复，采集、拆解和创作结果均已持久化。"
                  : task.error?.message ?? `${task.stage} · ${task.progress}%`,
              },
            ];
            const currentSession = modeSessions.current[restoredMode];
            const restoredSession = {
              ...currentSession,
              messages: currentSession.messages.some((message) => message.taskId === task.id)
                ? messagesFromTasks(currentSession.messages, [task])
                : [...currentSession.messages, ...restoredMessages],
              agentConversationId: task.conversation_id ?? undefined,
            };
            modeSessions.current[restoredMode] = restoredSession;
            if (modeChangeRevision.current === taskRoute.current.modeChangeRevision) {
              activeModeRef.current = restoredMode;
              setMode(restoredMode);
              setInput(restoredSession.input);
              setSelectedSkills(restoredSession.selectedSkills);
              setMessages(restoredSession.messages);
              setAgentConversationId(restoredSession.agentConversationId);
              setSelectedSourceIds(restoredSession.selectedSourceIds);
              setSelectedKnowledgeIds(restoredSession.selectedKnowledgeIds);
              setAttachments(restoredSession.attachments);
              setTrendSourceTaskId(restoredSession.trendSourceTaskId);
            }
          }
        }
        if (initialPage === "chat") {
          const libraries = await Promise.all([
            listApiLibrary("collections"),
            listApiLibrary("breakdowns"),
            listApiLibrary("creations"),
          ]);
          if (!cancelled) {
            const records = libraries.flatMap((library) => library.items.map(recordFromApi));
            setContextRecords(records);
            const linkedSources = initialSourceIds;
            if (linkedSources.length) {
              const restoredMode = initialMode === "breakdown" ? "breakdown" : "creation";
              const restoredSession = { ...modeSessions.current[restoredMode], selectedSourceIds: linkedSources };
              modeSessions.current[restoredMode] = restoredSession;
              activeModeRef.current = restoredMode;
              setMode(restoredMode);
              setInput(restoredSession.input);
              setSelectedSkills(restoredSession.selectedSkills);
              setMessages(restoredSession.messages);
              setAgentConversationId(restoredSession.agentConversationId);
              setSelectedSourceIds(linkedSources);
              setSelectedKnowledgeIds(restoredSession.selectedKnowledgeIds);
              setAttachments(restoredSession.attachments);
              setTrendSourceTaskId(restoredSession.trendSourceTaskId);
            }
          }
        }
      } catch (error) {
        if (!cancelled) setApiStatus(isSessionAuthError(error) ? "auth_required" : "disconnected");
      }
    }
    void bootstrap();
    return () => { cancelled = true; };
  }, [authEpoch, initialPage, initialMode, initialRecordId, initialSourceIds, initialTaskId]);

  useEffect(() => {
    const requireAuthentication = () => setApiStatus("auth_required");
    window.addEventListener("riffloom:auth-required", requireAuthentication);
    return () => window.removeEventListener("riffloom:auth-required", requireAuthentication);
  }, []);

  const hasActiveTasks = tasks.some((task) => ["queued", "running", "submitting"].includes(task.status));

  useEffect(() => {
    if (apiStatus !== "connected") return;
    let cancelled = false;
    const refresh = async (includePageData = false) => {
      try {
        const remoteTasks = await listApiTasks();
        if (cancelled) return;
        const nextTasks = remoteTasks.items.map(taskFromApi);
        const reachedTerminal = nextTasks.some((task) =>
          ["queued", "running", "submitting"].includes(taskStatusSnapshot.current.get(task.id) ?? "")
          && !["queued", "running", "submitting"].includes(task.status),
        );
        const completedFeishuSync = reachedTerminal && remoteTasks.items.some((task) =>
          task.type === "feishu_sync"
          && !["queued", "running"].includes(task.status)
          && ["queued", "running", "submitting"].includes(taskStatusSnapshot.current.get(task.id) ?? ""),
        );
        const shouldRefreshPage = includePageData
          || reachedTerminal;
        const [library, remoteFeishuBindings, remoteConversations] = await Promise.all([
          shouldRefreshPage && pageHasApiLibrary(initialPage)
            ? listApiLibrary(initialPage, initialPage === "collections" ? collectionTab : undefined)
            : null,
          shouldRefreshPage && (pageHasApiLibrary(initialPage) || completedFeishuSync)
            ? listApiFeishuBindings()
            : null,
          reachedTerminal && initialPage === "chat" ? listApiConversations() : null,
        ]);
        if (cancelled) return;
        taskStatusSnapshot.current = new Map(nextTasks.map((task) => [task.id, task.status]));
        setTasks(nextTasks);
        if (remoteTasks.items.some((task) => task.id === focusedCollectionTaskIdRef.current && task.status === "success")) {
          focusedCollectionTaskIdRef.current = undefined;
          setFocusedCollectionTaskId(undefined);
          setTaskCenterOpen(false);
        }
        for (const task of remoteTasks.items) {
          if (
            task.skill_id !== "riffloom_agent"
            || task.status !== "success"
            || handledAgentTasks.current.has(task.id)
            || pendingAgentInputs.current[task.id] === undefined
          ) continue;
          if (typeof task.result_summary.delegated_task_id === "string") {
            handledAgentTasks.current.add(task.id);
            delete pendingAgentInputs.current[task.id];
            setTaskCenterOpen(true);
            setToast(`Agent 已委派任务 ${task.result_summary.delegated_task_id}`);
          } else if (task.result_summary.agent_action === "confirmation_required") {
            const confirmation = task.result_summary.confirmation;
            if (!confirmation || typeof confirmation !== "object") continue;
            const type = String((confirmation as Record<string, unknown>).type ?? "");
            const original = pendingAgentInputs.current[task.id]
              ?? (typeof task.input.prompt === "string" ? task.input.prompt : "");
            const url = firstHttpsUrl(original) ?? "";
            if (type === "collect_rewrite") {
              setCollectRewriteModal({
                source: { url },
                prompt: String((confirmation as Record<string, unknown>).prompt ?? original),
              });
            } else {
              setCollectionInitialValue(url);
              setCollectionModalOpen(true);
            }
            handledAgentTasks.current.add(task.id);
            delete pendingAgentInputs.current[task.id];
          }
        }
        if (library && pageHasApiLibrary(initialPage)) {
          setApiRecords((current) => ({ ...current, [initialPage]: library.items.map(recordFromApi) }));
        }
        if (remoteFeishuBindings) {
          setFeishuBindings(Object.fromEntries(
            remoteFeishuBindings.items.map((binding) => [binding.scope_key, binding]),
          ));
        }
        if (remoteConversations) setAgentConversations(remoteConversations.items);
        setMessages((current) => messagesFromTasks(current, remoteTasks.items));
        for (const chatMode of Object.keys(modeConfig) as ChatMode[]) {
          if (chatMode === activeModeRef.current) continue;
          const session = modeSessions.current[chatMode];
          modeSessions.current[chatMode] = {
            ...session,
            messages: messagesFromTasks(session.messages, remoteTasks.items),
          };
        }
      } catch (error) {
        if (!cancelled) setApiStatus(isSessionAuthError(error) ? "auth_required" : "disconnected");
      }
    };
    const refreshOnFocus = () => { void refresh(true); };
    window.addEventListener("focus", refreshOnFocus);
    if (taskCenterOpen) void refresh(true);
    const interval = hasActiveTasks ? window.setInterval(() => { void refresh(); }, 2_000) : undefined;
    return () => {
      cancelled = true;
      window.removeEventListener("focus", refreshOnFocus);
      if (interval !== undefined) window.clearInterval(interval);
    };
  }, [apiStatus, collectionTab, hasActiveTasks, initialPage, taskCenterOpen]);

  useEffect(() => {
    if (!apiIntegrationEnabled || apiStatus !== "disconnected") return;
    let cancelled = false;
    const reconnect = async () => {
      try {
        const [health, session] = await Promise.all([getHealth(), getSession()]);
        if (!cancelled) {
          setModelProvider(health.model_provider);
          setSessionName(session.workspace_name);
          setSessionUserName(session.user_name);
          setSessionRole(session.role);
          setSessionAuthMode(session.auth_mode);
          if (!session.onboarding_completed) {
            setCollapsed(false);
            setOnboardingStep(0);
          }
          setApiStatus("connected");
          setToast("API 已恢复连接");
        }
      } catch (error) {
        if (!cancelled && isSessionAuthError(error)) setApiStatus("auth_required");
      }
    };
    const interval = window.setInterval(() => { void reconnect(); }, 2_000);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, [apiStatus]);

  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 2200);
    return () => clearTimeout(timer);
  }, [toast]);

  const activeMode = modeConfig[mode];
  const activeSkills = selectedSkills.length ? selectedSkills : activeMode.defaultSkills;
  const runningTaskCount = tasks.filter((task) => ["queued", "running", "submitting"].includes(task.status)).length;
  const roleLabel = sessionRole === "admin" ? "管理员" : sessionRole === "lead" ? "内容负责人" : "内容成员";
  const canConfigureFeishu = apiStatus === "scenario" || Boolean(feishuProvider?.can_configure);
  const collectionFeishuScope = feishuScopeForPage("collections", collectionTab);
  const drawerFeishuScope = drawerRecord && initialPage === "collections"
    ? feishuScopeForPage("collections", drawerRecord.collectionKind ?? collectionTab)
    : undefined;
  const drawerFeishuBinding = drawerFeishuScope ? feishuBindings[drawerFeishuScope] : undefined;

  async function loginWithInvitation(invitationCode: string) {
    setAuthSubmitting(true);
    setAuthError("");
    try {
      const result = await redeemInvitation(invitationCode);
      setSessionName(result.session.workspace_name);
      setSessionUserName(result.session.user_name);
      setSessionRole(result.session.role);
      setSessionAuthMode(result.session.auth_mode);
      if (!result.session.onboarding_completed) {
        setCollapsed(false);
        setOnboardingStep(0);
      }
      setApiStatus("connecting");
      setAuthEpoch((value) => value + 1);
    } catch (error) {
      setAuthError(error instanceof ApiError ? error.message : "邀请码登录失败，请稍后重试");
    } finally {
      setAuthSubmitting(false);
    }
  }

  async function logout() {
    setProfileOpen(false);
    try {
      await signOutApi();
    } finally {
      setApiStatus("auth_required");
      setAuthError("");
      setTasks([]);
      setApiRecords({});
      setCollectionTab("single");
      modeSessions.current = createEmptyChatModeSessions();
      activeModeRef.current = "agent";
      handledAgentTasks.current.clear();
      pendingAgentInputs.current = {};
      restoredTaskId.current = undefined;
      setMode("agent");
      setInput("");
      setSelectedSkills([]);
      setMessages([]);
      setAgentConversationId(undefined);
      setAgentConversations([]);
      setSelectedSourceIds([]);
      setSelectedKnowledgeIds([]);
      setAttachments([]);
      setTrendSourceTaskId(undefined);
      setOnboardingStep(null);
    }
  }

  async function finishOnboarding() {
    if (onboardingSaving) return;
    setOnboardingSaving(true);
    setOnboardingStep(null);
    try {
      if (apiIntegrationEnabled) await completeApiOnboarding();
    } catch {
      setToast("引导已关闭，但完成状态暂未保存");
    } finally {
      setOnboardingSaving(false);
    }
  }

  function advanceOnboarding() {
    if (onboardingStep === null) return;
    if (onboardingStep < onboardingSteps.length - 1) {
      setOnboardingStep(onboardingStep + 1);
    } else {
      void finishOnboarding();
    }
  }

  async function changeCollectionTab(nextTab: CollectionKind) {
    setCollectionTab(nextTab);
    if (apiStatus !== "connected") return;
    try {
      const library = await listApiLibrary("collections", nextTab);
      setApiRecords((current) => ({ ...current, collections: library.items.map(recordFromApi) }));
    } catch (error) {
      if (isSessionAuthError(error)) setApiStatus("auth_required");
      else setToast(error instanceof ApiError ? error.message : "采集库加载失败");
    }
  }

  function changeMode(nextMode: ChatMode) {
    if (nextMode === mode) return;
    modeChangeRevision.current += 1;
    modeSessions.current[mode] = {
      input,
      selectedSkills,
      messages,
      agentConversationId,
      selectedSourceIds,
      selectedKnowledgeIds,
      attachments,
      trendSourceTaskId,
    };
    const next = modeSessions.current[nextMode];
    activeModeRef.current = nextMode;
    setMode(nextMode);
    setSelectedSkills(next.selectedSkills);
    setMessages(next.messages);
    setAgentConversationId(next.agentConversationId);
    setInput(next.input);
    setSelectedSourceIds(next.selectedSourceIds);
    setSelectedKnowledgeIds(next.selectedKnowledgeIds);
    setAttachments(next.attachments);
    setKnowledgePickerOpen(false);
    setSkillsPickerOpen(false);
    setTrendSourceTaskId(next.trendSourceTaskId);
  }

  function activateConversation(nextMode: ChatMode, next: ChatModeSession) {
    modeSessions.current[nextMode] = next;
    activeModeRef.current = nextMode;
    setMode(nextMode);
    setInput(next.input);
    setSelectedSkills(next.selectedSkills);
    setMessages(next.messages);
    setAgentConversationId(next.agentConversationId);
    setSelectedSourceIds(next.selectedSourceIds);
    setSelectedKnowledgeIds(next.selectedKnowledgeIds);
    setAttachments(next.attachments);
    setTrendSourceTaskId(next.trendSourceTaskId);
    window.history.replaceState({}, "", "/chat");
  }

  function startNewConversation() {
    setConversationMenuId(undefined);
    setRenamingConversationId(undefined);
    activateConversation(mode, createEmptyChatModeSessions()[mode]);
  }

  async function openConversation(conversationId: string) {
    setConversationMenuId(undefined);
    try {
      const conversation = await getApiConversation(conversationId);
      const nextMode = chatModeFromApiMode(conversation.mode);
      const next: ChatModeSession = {
        ...createEmptyChatModeSessions()[nextMode],
        agentConversationId: conversation.id,
        messages: conversation.messages.map((message, index) => ({
          id: `${conversation.id}-${index}`,
          role: message.role === "assistant" ? "agent" : "user",
          text: message.content,
          chatReply: message.role === "assistant",
        })),
      };
      activateConversation(nextMode, next);
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "历史对话加载失败");
    }
  }

  function beginConversationRename(conversation: ApiConversationSummary) {
    setConversationMenuId(undefined);
    setRenamingConversationId(conversation.id);
    setConversationTitleDraft(conversation.title);
  }

  async function renameConversation(conversationId: string) {
    const title = conversationTitleDraft.trim();
    if (!title) {
      setToast("对话名称不能为空");
      return;
    }
    setConversationActionBusyId(conversationId);
    try {
      const updated = await updateApiConversation(conversationId, title);
      setAgentConversations((current) => current.map((item) => item.id === conversationId ? updated : item));
      setRenamingConversationId(undefined);
      setToast("对话已重命名");
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "对话重命名失败");
    } finally {
      setConversationActionBusyId(undefined);
    }
  }

  async function deleteConversation(conversation: ApiConversationSummary) {
    setConversationMenuId(undefined);
    if (!window.confirm(`确认删除“${conversation.title}”？删除后无法恢复。`)) return;
    setConversationActionBusyId(conversation.id);
    try {
      await deleteApiConversation(conversation.id);
      setAgentConversations((current) => current.filter((item) => item.id !== conversation.id));
      if (agentConversationId === conversation.id) startNewConversation();
      setToast("对话已删除");
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "删除对话失败");
    } finally {
      setConversationActionBusyId(undefined);
    }
  }

  function invokeSkill(skillName: string) {
    const skill = skills.find((item) => item.name === skillName);
    if (!skill) return;
    setSelectedSkills((current) => current.includes(skillName) ? current.filter((item) => item !== skillName) : [...current, skillName]);
    setSkillsPickerOpen(false);
    setToast(`已调用「${skillName}」技能，请补充素材或要求`);
  }

  function selectKnowledgeRecord(recordId: string) {
    setSelectedKnowledgeIds((current) =>
      current.includes(recordId)
        ? current.filter((id) => id !== recordId)
        : [...current, recordId],
    );
    setKnowledgePickerOpen(false);
  }

  async function uploadAttachments(files: FileList | null) {
    if (!files?.length || attachmentUploading) return;
    const available = 4 - attachments.length;
    if (available <= 0) {
      setToast("每次最多上传 4 个附件");
      return;
    }
    const chosen = Array.from(files).slice(0, available).map((file) => ({ file, mimeType: attachmentMime(file) }));
    const invalid = chosen.find(({ file, mimeType }) =>
      file.size > 5 * 1024 * 1024 || !mimeType,
    );
    if (invalid) {
      setToast("支持 5 MiB 以内的 PNG、JPEG、TXT、Markdown 或 DOCX");
      return;
    }
    setAttachmentUploading(true);
    try {
      const uploaded = await Promise.all(chosen.map(async ({ file, mimeType }) => {
        if (!apiIntegrationEnabled) {
          return {
            id: nextId("attachment"),
            original_name: file.name,
            mime_type: file.type,
            byte_size: file.size,
            content_url: "",
            created_at: new Date().toISOString(),
          } satisfies ApiAttachment;
        }
        return createApiAttachment({
          filename: file.name,
          mimeType: mimeType!,
          dataUrl: await fileToDataUrl(file),
        });
      }));
      setAttachments((current) => [
        ...current,
        ...uploaded.filter((item) => !current.some((existing) => existing.id === item.id)),
      ]);
      setToast(`已添加 ${uploaded.length} 个附件`);
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "附件上传失败");
    } finally {
      setAttachmentUploading(false);
    }
  }

  async function submitTask() {
    const value = input.trim() || (attachments.length && mode !== "capture" ? "请分析并处理附件内容" : "");
    if (!value || submitting) return;
    if (apiIntegrationEnabled && apiStatus !== "connected") {
      setToast("API 未连接，请确认本地后端可用");
      return;
    }
    const wantsCollection = mode === "capture" || selectedSkills.includes("采集内容");
    if (wantsCollection && apiStatus === "connected") {
      if (!collectionProvider) {
        setToast("采集 Provider 配置仍在加载，请稍后重试");
        return;
      }
      setCollectionInitialValue(value);
      setCollectionModalOpen(true);
      return;
    }
    const selectedName = selectedSkills[0] ?? activeSkills[0];
    if (apiStatus === "connected" && selectedName === "一键采集仿写") {
      const url = firstHttpsUrl(value);
      const collectionId = selectedSourceIds.find((id) =>
        contextRecords.some((record) => record.id === id && record.libraryType === "collections")
      );
      if (!url && !collectionId) {
        setToast("一键采集仿写需要一个获权链接或已选中的采集记录");
        return;
      }
      setCollectRewriteModal({
        source: url ? { url } : { collectionId },
        prompt: url ? value.replace(url, "").trim() || "参考来源结构重新创作" : value,
      });
      return;
    }
    if (apiStatus === "connected") {
      setSubmitting(true);
      try {
        const selectedSkillId = selectedName
          ? skillIds[selectedName] ?? "original_copy"
          : mode === "agent" ? "riffloom_agent" : "original_copy";
        const knowledgeRefs = contextRecords
          .filter((record) => selectedKnowledgeIds.includes(record.id))
          .map((record) => ({
            library_type: record.libraryType ?? "collections",
            record_id: record.id,
          }));
        const task = selectedSkillId === "viral_topic_coach"
          ? await createApiTrendTask({
              direction: value,
              conversationId: agentConversationId,
              idempotencyKey: `trend-web-${crypto.randomUUID()}`,
            })
          : selectedSkillId === "viral_breakdown"
          ? await createApiBreakdownTask({
              prompt: value,
              conversationId: agentConversationId,
              sourceIds: selectedSourceIds,
              knowledgeRefs,
              attachmentIds: attachments.map((item) => item.id),
              idempotencyKey: `breakdown-web-${crypto.randomUUID()}`,
            })
          : selectedSkillId === "original_copy" || selectedSkillId === "copy_rewrite"
            ? await createApiCreationTask({
                creationType: selectedSkillId === "copy_rewrite" ? "rewrite" : "original",
                prompt: value,
                conversationId: agentConversationId,
                sourceIds: selectedSourceIds,
                knowledgeRefs,
                attachmentIds: attachments.map((item) => item.id),
                trendTaskId: trendSourceTaskId,
                idempotencyKey: `creation-web-${crypto.randomUUID()}`,
              })
            : await createApiTask({
                mode,
                skillId: selectedSkillId,
                prompt: value,
                knowledgeRefs,
                attachmentIds: attachments.map((item) => item.id),
                sourceIds: selectedSourceIds,
                idempotencyKey: `web-${crypto.randomUUID()}`,
                conversationId: agentConversationId,
              });
        setAgentConversationId(task.conversation_id ?? undefined);
        if (selectedSkillId === "riffloom_agent") {
          pendingAgentInputs.current[task.id] = value;
        }
        void listApiConversations()
          .then((history) => setAgentConversations(history.items))
          .catch(() => undefined);
        setTasks((current) => [taskFromApi(task), ...current.filter((item) => item.id !== task.id)]);
        setMessages((current) => [
          ...current,
          { id: nextId("M-U"), role: "user", text: value },
          {
            id: nextId("M-A"),
            role: "agent",
            taskId: task.id,
            chatReply: selectedSkillId === "riffloom_agent",
            text: selectedSkillId === "riffloom_agent" ? "Riffloom 智能体正在回复…" : "任务已受理，可关闭页面后继续执行。",
          },
        ]);
        setInput("");
        setAttachments([]);
        setKnowledgePickerOpen(false);
        setSkillsPickerOpen(false);
        if (selectedSkillId === "original_copy" || selectedSkillId === "copy_rewrite") {
          setTrendSourceTaskId(undefined);
        }
        window.history.replaceState({}, "", `/chat?task=${task.id}`);
      } catch (error) {
        const message = error instanceof ApiError ? error.message : "任务提交失败";
        if (error instanceof ApiError && error.code === "API_UNREACHABLE") setApiStatus("disconnected");
        setMessages((current) => [...current, { id: nextId("M-E"), role: "agent", text: message }]);
        setToast(message);
      } finally {
        setSubmitting(false);
      }
      return;
    }
    const taskId = nextId("T");
    const task: Task = {
      id: taskId,
      kind: selectedSkills.includes("一键采集仿写") ? "创作" : mode === "capture" ? "采集" : mode === "breakdown" ? "拆解" : "创作",
      title: selectedSkills.includes("一键采集仿写") ? "一键采集仿写" : `${activeMode.label}任务`,
      status: "queued",
      stage: "任务已受理",
      progress: 8,
      detail: "已锁定来源、技能版本和知识快照",
      resultPage: "creations",
      createdAt: "刚刚",
    };
    setTasks((current) => [task, ...current]);
    setMessages((current) => [
      ...current,
      { id: nextId("M-U"), role: "user", text: value },
      {
        id: nextId("M-A"),
        role: "agent",
        taskId,
        text: "任务已受理。将依次完成采集、结构化拆解与创作版本保存；页面可以关闭。",
      },
    ]);
    setInput("");
    setAttachments([]);
    setKnowledgePickerOpen(false);
    setSkillsPickerOpen(false);
    window.history.replaceState({}, "", `/chat?task=${taskId}`);

    timers.current.push(
      setTimeout(() => {
        setTasks((current) => current.map((item) => (item.id === taskId ? { ...item, status: "running", stage: "生成创作版本", progress: 68, detail: "采集和拆解已完成，正在校验文本重合风险" } : item)));
      }, 350),
      setTimeout(() => {
        setTasks((current) => current.map((item) => (item.id === taskId ? { ...item, status: "succeeded", stage: "结果已入库", progress: 100, detail: "生成不可变版本 v1，可查看来源并确认采用" } : item)));
        setMessages((current) => current.map((message) => (message.taskId === taskId ? { ...message, text: "一键采集仿写已完成：采集记录、拆解记录与创作版本 v1 已分别入库。" } : message)));
      }, 1150),
    );
  }

  function continueTopic(candidate: ApiTopicCandidate, taskId: string) {
    changeMode("creation");
    setSelectedSkills(["文案原创"]);
    setTrendSourceTaskId(taskId);
    setInput(
      `请围绕标题「${candidate.title_suggestion}」创作小红书图文文案。\n热点：${candidate.topic}\n我的切入点：${candidate.angle}\n目标人群：${candidate.audience}\n核心价值：${candidate.core_value}`,
    );
    setToast("已带上选题任务快照，创作结果可追溯回该选题");
  }

  async function submitCollectionTask(draft: CollectionTaskDraft) {
    if (collectionSubmitting) return;
    setCollectionSubmitting(true);
    try {
      const task = await createApiCollectionTask({
        ...draft,
        attachmentIds: attachments.map((item) => item.id),
        idempotencyKey: `collection-web-${crypto.randomUUID()}`,
      });
      setTasks((current) => [taskFromApi(task), ...current.filter((item) => item.id !== task.id)]);
      setMessages((current) => [
        ...current,
        { id: nextId("M-U"), role: "user", text: draft.value },
        { id: nextId("M-A"), role: "agent", taskId: task.id, text: "采集任务已受理；可关闭页面，完成后进入对应采集库。" },
      ]);
      setCollectionModalOpen(false);
      setCollectionInitialValue("");
      setInput("");
      setAttachments([]);
      const uploadFallback = collectionProvider?.provider === "sandbox-v1" && draft.platform === "xiaohongshu";
      focusedCollectionTaskIdRef.current = uploadFallback ? undefined : task.id;
      setFocusedCollectionTaskId(focusedCollectionTaskIdRef.current);
      setTaskCenterOpen(!uploadFallback);
      setToast(uploadFallback ? "待转写记录正在创建，稍后打开并上传已获权媒体" : `采集任务 ${task.id} 已受理`);
      if (uploadFallback) router.push("/collections");
      else if (initialPage === "chat") window.history.replaceState({}, "", `/chat?task=${task.id}`);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "采集任务提交失败";
      if (error instanceof ApiError && error.code === "API_UNREACHABLE") setApiStatus("disconnected");
      setToast(message);
    } finally {
      setCollectionSubmitting(false);
    }
  }

  async function submitCollectRewriteTask(draft: CollectRewriteDraft) {
    if (collectionSubmitting) return;
    setCollectionSubmitting(true);
    try {
      const knowledgeRefs = contextRecords
        .filter((record) => selectedKnowledgeIds.includes(record.id))
        .map((record) => ({
          library_type: record.libraryType ?? "collections" as const,
          record_id: record.id,
        }));
      const task = await createApiCollectRewriteTask({
        ...draft,
        knowledgeRefs,
        attachmentIds: attachments.map((item) => item.id),
        idempotencyKey: `collect-rewrite-web-${crypto.randomUUID()}`,
      });
      setTasks((current) => [taskFromApi(task), ...current.filter((item) => item.id !== task.id)]);
      setMessages((current) => [
        ...current,
        { id: nextId("M-U"), role: "user", text: draft.prompt },
        { id: nextId("M-A"), role: "agent", taskId: task.id, text: "一键采集仿写已受理；会从已保存的检查点继续。" },
      ]);
      setCollectRewriteModal(null);
      setInput("");
      setAttachments([]);
      setTaskCenterOpen(true);
      setToast(`一键采集仿写任务 ${task.id} 已受理`);
      if (initialPage === "chat") window.history.replaceState({}, "", `/chat?task=${task.id}`);
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "一键采集仿写提交失败");
    } finally {
      setCollectionSubmitting(false);
    }
  }

  async function runCollectionBatch(action: "breakdown" | "rewrite", recordIds: string[]) {
    const prompt = action === "rewrite"
      ? window.prompt("填写一次创作要求，将为每条记录创建独立仿写任务：", "面向目标受众重新表达，保留结构方法但不复制原文")
      : "";
    if (action === "rewrite" && !prompt?.trim()) return;
    try {
      const result = await createApiCollectionBatchTasks({
        action,
        recordIds,
        prompt: prompt ?? "",
        idempotencyKey: `collection-batch-web-${crypto.randomUUID()}`,
      });
      setTasks((current) => [
        ...result.tasks.map(taskFromApi),
        ...current.filter((item) => !result.tasks.some((task) => task.id === item.id)),
      ]);
      setTaskCenterOpen(true);
      setToast(`已创建 ${result.tasks.length} 个任务${result.issues.length ? `，${result.issues.length} 条需处理` : ""}`);
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "批量任务创建失败");
    }
  }

  async function updateCollectionBatchMetadata(
    recordIds: string[],
    change: { benchmark?: boolean; categoryTags?: string[] },
  ) {
    try {
      await updateApiCollectionMetadata({ recordIds, ...change });
      const library = await listApiLibrary("collections", collectionTab);
      setApiRecords((current) => ({ ...current, collections: library.items.map(recordFromApi) }));
      setToast(`已更新 ${recordIds.length} 条采集记录`);
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "批量元数据更新失败");
    }
  }

  async function retryTask(taskId: string) {
    if (apiStatus === "connected" && taskId.startsWith("task_")) {
      try {
        const task = await retryApiTask(taskId);
        setTasks((current) => current.map((item) => item.id === taskId ? taskFromApi(task) : item));
        setToast(`第 ${task.current_attempt} 次执行已受理，只补齐失败阶段`);
      } catch (error) {
        const message = error instanceof ApiError ? error.message : "任务重试失败";
        setToast(message);
      }
      return;
    }
    setTasks((current) =>
      current.map((task) =>
        task.id === taskId
          ? { ...task, status: "running", stage: "重试失败项", progress: Math.max(task.progress, 40), detail: "已完成结果保持不变，只重试失败记录" }
          : task,
      ),
    );
    timers.current.push(
      setTimeout(() => {
        setTasks((current) => current.map((task) => (task.id === taskId ? { ...task, status: "succeeded", stage: "重试完成", progress: 100, detail: "失败项已恢复，旧结果未被覆盖" } : task)));
      }, 900),
    );
  }

  async function cancelTask(taskId: string) {
    if (apiStatus === "connected" && taskId.startsWith("task_")) {
      try {
        const task = await cancelApiTask(taskId);
        setTasks((current) => current.map((item) => item.id === taskId ? taskFromApi(task) : item));
        if (pageHasApiLibrary(initialPage)) await refreshFeishuBindings();
        setToast("任务已取消；已发出的第三方请求无法撤回");
      } catch (error) {
        setToast(error instanceof ApiError ? error.message : "任务取消失败");
      }
      return;
    }
    setTasks((current) => current.map((task) => task.id === taskId
      ? { ...task, status: "cancelled", stage: "任务已取消", detail: "未完成步骤不会继续执行" }
      : task));
  }

  function openRecord(record: LibraryRecord) {
    setDrawerRecord(record);
    setDrawerTab("content");
    setCreationDetail(null);
    setBreakdownDetail(null);
    setCollectionDetail(null);
    window.history.replaceState({}, "", `${pagePath[initialPage]}?record=${record.id}`);
    if (apiStatus === "connected" && initialPage === "creations" && record.sourceKind === "api") {
      void getApiCreation(record.id)
        .then(setCreationDetail)
        .catch((error) => setToast(error instanceof ApiError ? error.message : "创作详情加载失败"));
    } else if (apiStatus === "connected" && initialPage === "breakdowns" && record.sourceKind === "api") {
      void getApiBreakdown(record.id)
        .then(setBreakdownDetail)
        .catch((error) => setToast(error instanceof ApiError ? error.message : "拆解详情加载失败"));
    } else if (apiStatus === "connected" && initialPage === "collections" && record.sourceKind === "api") {
      void getApiCollection(record.id)
        .then(setCollectionDetail)
        .catch((error) => setToast(error instanceof ApiError ? error.message : "采集详情加载失败"));
    }
  }

  function closeDrawer() {
    setDrawerRecord(null);
    setCreationDetail(null);
    setBreakdownDetail(null);
    setCollectionDetail(null);
    window.history.replaceState({}, "", pagePath[initialPage]);
  }

  async function adoptVersion(record: LibraryRecord, versionLabel: string) {
    if (apiStatus === "connected" && record.sourceKind === "api") {
      const version = creationDetail?.versions.find((item) => `v${item.version}` === versionLabel);
      if (!version) {
        setToast("版本详情仍在加载，请稍后重试");
        return;
      }
      try {
        await adoptApiCreation(record.id, version.id);
        setAdoptedVersions((current) => ({ ...current, [record.id]: versionLabel }));
        setCreationDetail((current) => current ? { ...current, adopted_version_id: version.id, status: "adopted" } : current);
        setApiRecords((current) => ({
          ...current,
          creations: current.creations?.map((item) => item.id === record.id ? { ...item, status: "已采用", adoptedVersionId: version.id } : item),
        }));
        setToast(`${versionLabel} 已通过 API 确认采用`);
      } catch (error) {
        setToast(error instanceof ApiError ? error.message : "确认采用失败");
      }
      return;
    }
    setAdoptedVersions((current) => ({ ...current, [record.id]: versionLabel }));
    setToast(`${versionLabel} 已确认采用，并计入核心指标`);
  }

  async function saveCreationVersion(record: LibraryRecord, body: string) {
    const baseVersionId = creationDetail?.current_version_id;
    if (!baseVersionId) {
      setToast("当前版本尚未加载");
      return;
    }
    try {
      await createApiCreationVersion({
        recordId: record.id,
        baseVersionId,
        body,
        changeNote: "继续修改",
      });
      const detail = await getApiCreation(record.id);
      setCreationDetail(detail);
      setToast(`已保存为 v${detail.versions.at(-1)?.version ?? "?"}，旧版本保持不变`);
      const library = await listApiLibrary("creations");
      setApiRecords((current) => ({ ...current, creations: library.items.map(recordFromApi) }));
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "新版本保存失败");
    }
  }

  async function deleteRecord(record: LibraryRecord) {
    if (record.sourceKind !== "api" || !record.libraryType) return;
    if (!window.confirm(`确认删除“${record.title}”？删除后将不再出现在库中。`)) return;
    try {
      await deleteApiLibraryRecord(record.libraryType, record.id);
      const libraryType = record.libraryType;
      setApiRecords((current) => ({
        ...current,
        [libraryType]: current[libraryType]?.filter((item) => item.id !== record.id),
      }));
      setContextRecords((current) => current.filter((item) => item.id !== record.id));
      closeDrawer();
      setToast("记录已删除；飞书副本会在下次同步时标记为已删除");
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "删除记录失败");
    }
  }

  async function deleteCollectionRecords(recordIds: string[]) {
    const results = await Promise.allSettled(
      recordIds.map((recordId) => deleteApiLibraryRecord("collections", recordId)),
    );
    const deletedIds = recordIds.filter((_, index) => results[index].status === "fulfilled");
    if (deletedIds.length) {
      const deleted = new Set(deletedIds);
      setApiRecords((current) => ({
        ...current,
        collections: current.collections?.filter((item) => !deleted.has(item.id)),
      }));
      setContextRecords((current) => current.filter((item) => !deleted.has(item.id)));
    }
    const failed = recordIds.length - deletedIds.length;
    setToast(failed ? `已删除 ${deletedIds.length} 条，${failed} 条删除失败` : `已删除 ${deletedIds.length} 条采集记录`);
  }

  async function refreshFeishuBindings() {
    const remote = await listApiFeishuBindings();
    setFeishuBindings(Object.fromEntries(
      remote.items.map((binding) => [binding.scope_key, binding]),
    ));
  }

  async function startFeishuSync(scope: FeishuScope, configuredBinding?: ApiFeishuBinding, sourceRecordId?: string) {
    const binding = configuredBinding ?? feishuBindings[scope];
    if (!binding) {
      setToast("请先配置当前库的飞书绑定");
      return;
    }
    if (!binding.can_sync) {
      setToast("当前账号只能查看同步状态");
      return;
    }
    if (binding.status !== "active") {
      setToast(binding.status === "paused" ? "当前绑定已暂停" : "飞书连接已失效");
      return;
    }
    try {
      const failedRun = !sourceRecordId && binding.last_run && binding.last_run.failed_count > 0
        ? binding.last_run
        : null;
      const task = failedRun
        ? await retryApiFeishuSync(failedRun.id, `feishu-retry-web-${crypto.randomUUID()}`)
        : await createApiFeishuSync({
            bindingId: binding.id,
            mode: sourceRecordId || binding.link_count ? "incremental" : "full",
            idempotencyKey: `feishu-sync-web-${crypto.randomUUID()}`,
            sourceRecordId,
          });
      setTasks((current) => [taskFromApi(task), ...current.filter((item) => item.id !== task.id)]);
      setTaskCenterOpen(true);
      setToast("飞书同步已受理；Riffloom 主数据不会被改写");
      const completed = await waitForApiTask(task);
      setTasks((current) => [taskFromApi(completed), ...current.filter((item) => item.id !== completed.id)]);
      await refreshFeishuBindings();
      setToast(
        completed.status === "cancelled"
          ? "飞书同步已取消"
          : completed.status === "success"
          ? `飞书同步完成：成功 ${String(completed.result_summary.success ?? 0)}，跳过 ${String(completed.result_summary.skipped ?? 0)}`
          : `飞书同步部分失败：${completed.error?.message ?? "可只重试失败项"}`,
      );
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "飞书同步失败");
    }
  }

  async function pauseFeishuBinding(scope: FeishuScope) {
    const binding = feishuBindings[scope];
    if (!binding?.can_configure) return;
    try {
      await updateApiFeishuBinding({
        bindingId: binding.id,
        paused: binding.status !== "paused",
      });
      await refreshFeishuBindings();
      setToast(binding.status === "paused" ? "飞书绑定已恢复" : "飞书绑定已暂停；已写入副本保持不变");
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "飞书绑定状态更新失败");
    }
  }

  if (apiStatus === "auth_required") {
    return (
      <InvitationLogin
        error={authError}
        submitting={authSubmitting}
        onSubmit={loginWithInvitation}
      />
    );
  }

  const showSidebarHistory = initialPage === "chat" && !collapsed;

  return (
    <div className={`app-shell ${collapsed ? "nav-collapsed" : ""}`}>
      <aside className="sidebar" aria-label="主导航" onClick={() => setConversationMenuId(undefined)}>
        <div className="brand-row">
          <div className="brand-mark">R</div>
          {!collapsed && (
            <div className="brand-copy">
              <strong>Riffloom</strong>
              <span>内容创作工作台</span>
            </div>
          )}
          <button className="icon-button collapse-button" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? "展开导航" : "折叠导航"}>
            {collapsed ? <ChevronRight /> : <ChevronLeft />}
          </button>
        </div>

        <nav className="primary-nav">
          {navItems.map(({ key, label, icon: Icon }) => (
            <Link key={key} href={pagePath[key]} data-onboarding={key} className={`nav-item ${initialPage === key ? "active" : ""}`} aria-label={label} aria-current={initialPage === key ? "page" : undefined} title={collapsed ? label : undefined}>
              <Icon aria-hidden="true" />
              {!collapsed && <span>{label}</span>}
            </Link>
          ))}
        </nav>

        {showSidebarHistory && (
          <section className="sidebar-history" aria-label="历史对话">
            <h2>历史对话</h2>
            <button className="sidebar-conversation-new" type="button" onClick={startNewConversation}>新对话</button>
            <nav className="sidebar-conversation-list" aria-label="对话列表">
              {agentConversations.map((conversation) => (
                <div key={conversation.id} className={`sidebar-conversation-row ${agentConversationId === conversation.id ? "active" : ""}`}>
                  {renamingConversationId === conversation.id ? (
                    <form className="sidebar-conversation-rename" onSubmit={(event) => { event.preventDefault(); void renameConversation(conversation.id); }} onClick={(event) => event.stopPropagation()}>
                      <input
                        autoFocus
                        aria-label={`重命名 ${conversation.title}`}
                        maxLength={120}
                        value={conversationTitleDraft}
                        disabled={conversationActionBusyId === conversation.id}
                        onChange={(event) => setConversationTitleDraft(event.target.value)}
                        onKeyDown={(event) => { if (event.key === "Escape") setRenamingConversationId(undefined); }}
                      />
                      <button type="submit" aria-label="保存对话名称" disabled={conversationActionBusyId === conversation.id}><Check /></button>
                      <button type="button" aria-label="取消重命名" disabled={conversationActionBusyId === conversation.id} onClick={() => setRenamingConversationId(undefined)}><X /></button>
                    </form>
                  ) : (
                    <>
                      <button
                        className="sidebar-conversation-open"
                        type="button"
                        aria-current={agentConversationId === conversation.id ? "page" : undefined}
                        onClick={() => { void openConversation(conversation.id); }}
                        title={conversation.title}
                      >
                        <span>{conversation.title}</span>
                      </button>
                      <button
                        className="sidebar-conversation-more"
                        type="button"
                        aria-label={`管理对话 ${conversation.title}`}
                        aria-expanded={conversationMenuId === conversation.id}
                        onClick={(event) => { event.stopPropagation(); setConversationMenuId((current) => current === conversation.id ? undefined : conversation.id); }}
                      >
                        <MoreHorizontal />
                      </button>
                      {conversationMenuId === conversation.id && (
                        <div className="sidebar-conversation-menu" aria-label={`${conversation.title} 操作`} onClick={(event) => event.stopPropagation()}>
                          <button type="button" onClick={() => beginConversationRename(conversation)}><PenLine />重命名</button>
                          <button className="danger" type="button" disabled={conversationActionBusyId === conversation.id} onClick={() => { void deleteConversation(conversation); }}><Trash2 />删除</button>
                        </div>
                      )}
                    </>
                  )}
                </div>
              ))}
            </nav>
          </section>
        )}

        <div className="sidebar-spacer" />
        <div className="profile-wrap">
          <button className="profile-button" onClick={() => setProfileOpen((value) => !value)} aria-expanded={profileOpen}>
            <span className="avatar">{sessionUserName.slice(0, 1)}</span>
            {!collapsed && (
              <span className="profile-copy">
                <strong>{sessionUserName}</strong>
                <span>{sessionName} · {roleLabel}</span>
              </span>
            )}
            {!collapsed && <MoreHorizontal aria-hidden="true" />}
          </button>
          {profileOpen && (
            <div className="profile-menu">
              <button onClick={() => { setProfileOpen(false); router.push("/chat"); setKnowledgePickerOpen(true); }}><BookOpen />知识引用</button>
              <button onClick={() => { setProfileOpen(false); setToast("已加载 Riffloom 智能体、六项任务技能、封面资产链路和飞书 sandbox 同步"); }}><Sparkles />智能体与技能</button>
              {sessionRole === "admin" && <button onClick={() => { setProfileOpen(false); router.push("/admin"); }}><ShieldCheck />管理后台</button>}
              {canConfigureFeishu && <button onClick={() => { setProfileOpen(false); setFeishuModal(initialPage === "breakdowns" ? "breakdown" : initialPage === "creations" ? "creation" : collectionFeishuScope); }}><ExternalLink />飞书连接</button>}
              {sessionAuthMode === "invite_token" && <button onClick={() => { void logout(); }}><LogOut />退出登录</button>}
            </div>
          )}
        </div>
      </aside>

      <section className="content-shell" onClick={() => setConversationMenuId(undefined)}>
        <header className="topbar">
          <div className="workspace-title">
            <strong>{sessionName}</strong>
            <span className={`runtime-state ${apiStatus}`}>
              {apiStatus === "connected" ? `API 已连接 · ${modelProvider}` : apiStatus === "connecting" ? "正在连接 API" : apiStatus === "disconnected" ? "API 连接中断" : "本地示例场景"}
            </span>
          </div>
          <div className="top-actions">
            <button className="task-trigger" data-onboarding="tasks" onClick={() => setTaskCenterOpen((value) => !value)} aria-expanded={taskCenterOpen}>
              <span className="task-dot" />任务中心 · 运行中 <strong>{runningTaskCount}</strong>
            </button>
            <button className="icon-button" aria-label="帮助" onClick={() => { setCollapsed(false); setTaskCenterOpen(false); setProfileOpen(false); setOnboardingStep(0); }}><CircleHelp /></button>
          </div>
        </header>

        <main className={`main-stage ${initialPage === "chat" && messages.length ? "chat-active" : ""} ${initialPage === "chat" ? "history-visible" : ""}`}>
          {apiStatus === "connected" && collectionProvider && coverProvider && feishuProvider && (
            <aside className="pilot-capability-banner" aria-label="运行能力边界">
              <ShieldCheck />
              <strong>本地演示</strong>
              <span>
                采集 {collectionProvider.is_sandbox ? "SANDBOX" : "REAL"}
                {" · "}文本 {modelProvider}
                {" · "}封面 {coverProvider.is_mock ? "MOCK" : "REAL"}
                {" · "}飞书外部写入 {feishuProvider.external_calls && feishuProvider.writes_enabled ? "ON" : "OFF"}
              </span>
            </aside>
          )}
          {initialPage === "chat" && (
            <ChatPage
              mode={mode}
              input={input}
              messages={messages}
              onModeChange={changeMode}
              onInputChange={setInput}
              onInvokeSkill={invokeSkill}
              onSubmit={submitTask}
              submitting={submitting}
              onPrompt={setInput}
              contextRecords={contextRecords}
              selectedSourceIds={selectedSourceIds}
              onRemoveSource={(recordId) => setSelectedSourceIds((current) => current.filter((id) => id !== recordId))}
              selectedKnowledgeIds={selectedKnowledgeIds}
              knowledgePickerOpen={knowledgePickerOpen}
              onToggleKnowledge={() => { setKnowledgePickerOpen((value) => !value); setSkillsPickerOpen(false); }}
              selectedSkills={selectedSkills}
              onRemoveSkill={(skill) => setSelectedSkills((current) => current.filter((item) => item !== skill))}
              skillsPickerOpen={skillsPickerOpen}
              onToggleSkillsPicker={() => { setSkillsPickerOpen((value) => !value); setKnowledgePickerOpen(false); }}
              onToggleKnowledgeRecord={selectKnowledgeRecord}
              attachments={attachments}
              attachmentUploading={attachmentUploading}
              onUploadAttachments={(files) => { void uploadAttachments(files); }}
              onRemoveAttachment={(attachmentId) => setAttachments((current) => current.filter((item) => item.id !== attachmentId))}
              onViewResult={(page, recordId) => router.push(`/${page}${recordId ? `?record=${recordId}` : ""}`)}
              onContinueTopic={continueTopic}
            />
          )}
          {initialPage === "collections" && (
            <LibraryPage
              key={`collections-${collectionTab}`}
              title="采集库"
              description="集中管理已采集的内容和博主数据"
              records={apiStatus === "scenario" ? collectionRecords : (apiRecords.collections ?? [])}
              query={query}
              onQueryChange={setQuery}
              onOpenRecord={openRecord}
              feishuScope={collectionFeishuScope}
              binding={feishuBindings[collectionFeishuScope]}
              provider={feishuProvider}
              canConfigureFeishu={canConfigureFeishu}
              onConfigureFeishu={() => setFeishuModal(collectionFeishuScope)}
              onSyncFeishu={(sourceRecordId) => { void startFeishuSync(collectionFeishuScope, undefined, sourceRecordId); }}
              onPauseFeishu={() => { void pauseFeishuBinding(collectionFeishuScope); }}
              onCancelTask={(taskId) => { void cancelTask(taskId); }}
              tabs={[
                ["single", "单篇采集库"],
                ["keyword", "关键词采集库"],
                ["creator_content", "博主内容库"],
                ["creator_profile", "博主信息库"],
              ]}
              activeTab={collectionTab}
              onTabChange={(tab) => { void changeCollectionTab(tab); }}
              primaryAction="新建采集"
              onPrimaryAction={() => {
                if (!collectionProvider) setToast("采集 Provider 配置仍在加载，请稍后重试");
                else { setCollectionInitialValue(""); setCollectionModalOpen(true); }
              }}
              dataLabel={apiStatus === "connected" ? `${collectionProvider?.is_sandbox ? "合规沙箱" : "真实采集"} · ${collectionProvider?.provider ?? "Provider 加载中"}` : "脱敏示例数据"}
              emptyHint="从链接、关键词或博主主页开始，采集第一篇内容并自动结构化入库"
              onBatchAction={(action, recordIds) => { void runCollectionBatch(action, recordIds); }}
              onBatchMetadata={(recordIds, change) => { void updateCollectionBatchMetadata(recordIds, change); }}
              onBatchDelete={(recordIds) => { void deleteCollectionRecords(recordIds); }}
            />
          )}
          {initialPage === "breakdowns" && (
            <LibraryPage
              title="拆解库"
              description="保存每一次结构化爆款分析结果"
              records={apiStatus === "scenario" ? breakdownRecords : (apiRecords.breakdowns ?? [])}
              query={query}
              onQueryChange={setQuery}
              onOpenRecord={openRecord}
              feishuScope="breakdown"
              binding={feishuBindings.breakdown}
              provider={feishuProvider}
              canConfigureFeishu={canConfigureFeishu}
              onConfigureFeishu={() => setFeishuModal("breakdown")}
              onSyncFeishu={(sourceRecordId) => { void startFeishuSync("breakdown", undefined, sourceRecordId); }}
              onPauseFeishu={() => { void pauseFeishuBinding("breakdown"); }}
              onCancelTask={(taskId) => { void cancelTask(taskId); }}
              primaryAction="新建拆解"
              onPrimaryAction={() => router.push("/chat?mode=breakdown")}
              dataLabel={apiStatus === "connected" ? `结构化拆解 · ${modelProvider}` : "脱敏示例数据"}
              emptyHint="拆解一篇爆款内容，把开头、结构、情绪与可复用方法沉淀为可检索资产"
            />
          )}
          {initialPage === "creations" && (
            <LibraryPage
              title="创作库"
              description="管理原创、仿写和采集仿写产生的版本"
              records={apiStatus === "scenario" ? creationRecords : (apiRecords.creations ?? [])}
              query={query}
              onQueryChange={setQuery}
              onOpenRecord={openRecord}
              feishuScope="creation"
              binding={feishuBindings.creation}
              provider={feishuProvider}
              canConfigureFeishu={canConfigureFeishu}
              onConfigureFeishu={() => setFeishuModal("creation")}
              onSyncFeishu={(sourceRecordId) => { void startFeishuSync("creation", undefined, sourceRecordId); }}
              onPauseFeishu={() => { void pauseFeishuBinding("creation"); }}
              onCancelTask={(taskId) => { void cancelTask(taskId); }}
              primaryAction="开始创作"
              onPrimaryAction={() => router.push("/chat?mode=creation")}
              dataLabel={apiStatus === "connected" ? "不可变创作版本" : "脱敏示例数据"}
              emptyHint="从选题或拆解开始，生成第一个可编辑、可追溯的创作版本"
            />
          )}
          {initialPage === "covers" && (
            <CoverPage
              apiStatus={apiStatus}
              onTaskChange={(task) => setTasks((current) => [taskFromApi(task), ...current.filter((item) => item.id !== task.id)])}
              onToast={setToast}
            />
          )}
        </main>

        {taskCenterOpen && (
          <TaskCenter tasks={focusedCollectionTaskId ? tasks.filter((task) => task.id === focusedCollectionTaskId) : tasks} onClose={() => setTaskCenterOpen(false)} onRetry={retryTask} onCancel={cancelTask} />
        )}
      </section>

      {drawerRecord && (
        <RecordDrawer
          record={drawerRecord}
          page={initialPage}
          workspaceName={sessionName}
          tab={drawerTab}
          adoptedVersion={adoptedVersions[drawerRecord.id]}
          apiDetail={creationDetail}
          breakdownDetail={breakdownDetail}
          collectionDetail={collectionDetail}
          transcriptionProvider={transcriptionProvider}
          canDelete={apiStatus === "connected" && drawerRecord.sourceKind === "api"}
          canSyncFeishu={Boolean(feishuProvider?.writes_enabled && drawerFeishuBinding?.can_sync && drawerFeishuBinding.status === "active")}
          onTabChange={setDrawerTab}
          onClose={closeDrawer}
          onAdopt={(version) => { void adoptVersion(drawerRecord, version); }}
          onContinue={() => {
            if (initialPage === "breakdowns") {
              const params = new URLSearchParams();
              params.set("mode", "creation");
              params.append("source", drawerRecord.id);
              if (drawerRecord.sourceId && drawerRecord.sourceId !== drawerRecord.id) params.append("source", drawerRecord.sourceId);
              closeDrawer();
              router.push(`/chat?${params.toString()}`);
            } else if (initialPage === "collections") {
              closeDrawer();
              router.push(`/chat?mode=breakdown&source=${encodeURIComponent(drawerRecord.id)}`);
            }
          }}
          onBreakdownComplete={(recordId) => {
            closeDrawer();
            router.push(`/breakdowns?record=${encodeURIComponent(recordId)}`);
          }}
          onCollectionDetailChange={setCollectionDetail}
          onTaskChange={(task) => setTasks((current) => [taskFromApi(task), ...current.filter((item) => item.id !== task.id)])}
          onToast={setToast}
          onSyncFeishu={() => { if (drawerFeishuScope) void startFeishuSync(drawerFeishuScope, undefined, drawerRecord.id); }}
          onSaveVersion={(body) => { void saveCreationVersion(drawerRecord, body); }}
          onDelete={() => { void deleteRecord(drawerRecord); }}
        />
      )}

      {collectionModalOpen && collectionProvider && (
        <CollectionTaskModal
          provider={collectionProvider}
          initialValue={collectionInitialValue}
          submitting={collectionSubmitting}
          onClose={() => { if (!collectionSubmitting) setCollectionModalOpen(false); }}
          onSubmit={(draft) => { void submitCollectionTask(draft); }}
        />
      )}

      {collectRewriteModal && (
        <CollectRewriteTaskModal
          key={`${collectRewriteModal.source.url ?? collectRewriteModal.source.collectionId}-${collectRewriteModal.prompt}`}
          source={collectRewriteModal.source}
          initialPrompt={collectRewriteModal.prompt}
          submitting={collectionSubmitting}
          onClose={() => { if (!collectionSubmitting) setCollectRewriteModal(null); }}
          onSubmit={(draft) => { void submitCollectRewriteTask(draft); }}
        />
      )}

      {feishuModal && (
        <FeishuModal
          scope={feishuModal}
          binding={feishuBindings[feishuModal]}
          provider={feishuProvider}
          connected={apiStatus === "connected"}
          canConfigure={canConfigureFeishu}
          onClose={() => setFeishuModal(null)}
          onSaved={(binding, syncNow) => {
            const scope = feishuModal;
            setFeishuBindings((current) => ({ ...current, [scope]: binding }));
            setFeishuModal(null);
            setToast(`${feishuProvider?.provider ?? "飞书"} 绑定已通过服务端预检并保存`);
            if (syncNow) void startFeishuSync(scope, binding);
          }}
          onToast={setToast}
        />
      )}

      {onboardingStep !== null && (
        <OnboardingTour
          step={onboardingStep}
          saving={onboardingSaving}
          onPrevious={() => setOnboardingStep((current) => current === null ? null : Math.max(0, current - 1))}
          onNext={advanceOnboarding}
          onSkip={() => { void finishOnboarding(); }}
        />
      )}

      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}

function OnboardingTour({
  step,
  saving,
  onPrevious,
  onNext,
  onSkip,
}: {
  step: number;
  saving: boolean;
  onPrevious: () => void;
  onNext: () => void;
  onSkip: () => void;
}) {
  const current = onboardingSteps[step];
  const Icon = current.icon;
  const target = "target" in current ? current.target : undefined;
  const [targetRect, setTargetRect] = useState<{
    top: number;
    left: number;
    width: number;
    height: number;
  } | null>(null);

  useEffect(() => {
    const updateTarget = () => {
      const element = target
        ? document.querySelector<HTMLElement>(`[data-onboarding="${target}"]`)
        : null;
      const rect = element?.getBoundingClientRect();
      setTargetRect(rect && rect.width && rect.height
        ? {
            top: Math.max(6, rect.top - 6),
            left: Math.max(6, rect.left - 6),
            width: rect.width + 12,
            height: rect.height + 12,
          }
        : null);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onSkip();
    };
    updateTarget();
    window.addEventListener("resize", updateTarget);
    window.addEventListener("keydown", handleKeyDown);
    document.addEventListener("scroll", updateTarget, true);
    return () => {
      window.removeEventListener("resize", updateTarget);
      window.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("scroll", updateTarget, true);
    };
  }, [onSkip, target]);

  return (
    <div className="onboarding-layer">
      {targetRect ? (
        <div
          className="onboarding-spotlight"
          aria-hidden="true"
          style={targetRect}
        />
      ) : (
        <div className="onboarding-shade" aria-hidden="true" />
      )}
      <section
        className="onboarding-card"
        role="dialog"
        aria-modal="false"
        aria-labelledby="onboarding-title"
        aria-describedby="onboarding-description"
      >
        <header>
          <span className="onboarding-icon"><Icon aria-hidden="true" /></span>
          <span>{step + 1} / {onboardingSteps.length}</span>
        </header>
        <div
          className="onboarding-progress"
          role="progressbar"
          aria-label="新手引导进度"
          aria-valuemin={1}
          aria-valuemax={onboardingSteps.length}
          aria-valuenow={step + 1}
        >
          <span style={{ transform: `scaleX(${(step + 1) / onboardingSteps.length})` }} />
        </div>
        <h2 id="onboarding-title">{current.title}</h2>
        <p id="onboarding-description">{current.description}</p>
        <footer>
          <button type="button" className="onboarding-skip" disabled={saving} onClick={onSkip}>跳过引导</button>
          <span />
          {step > 0 && (
            <button type="button" className="onboarding-previous" disabled={saving} onClick={onPrevious}><ChevronLeft />上一步</button>
          )}
          <button autoFocus type="button" className="onboarding-next" disabled={saving} onClick={onNext}>{saving ? "正在保存" : "我知道了"}</button>
        </footer>
      </section>
    </div>
  );
}

function InvitationLogin({
  error,
  submitting,
  onSubmit,
}: {
  error: string;
  submitting: boolean;
  onSubmit: (invitationCode: string) => Promise<void>;
}) {
  const [invitationCode, setInvitationCode] = useState("");
  return (
    <main className="login-shell">
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-brand" aria-hidden="true">R</div>
        <p className="login-eyebrow">RIFFLOOM PILOT</p>
        <h1 id="login-title">使用邀请码进入</h1>
        <p className="login-intro">普通成员使用一次性邀请码；管理员也可以使用后台生成的登录密钥。</p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void onSubmit(invitationCode);
          }}
        >
          <label htmlFor="invitation-code">邀请码</label>
          <div className="login-input-wrap">
            <KeyRound aria-hidden="true" />
            <input
              id="invitation-code"
              name="invitation_code"
              type="password"
              autoComplete="one-time-code"
              minLength={12}
              maxLength={200}
              value={invitationCode}
              onChange={(event) => setInvitationCode(event.target.value)}
              placeholder="粘贴邀请码或管理员登录密钥"
              required
              autoFocus
            />
          </div>
          {error && <p className="login-error" role="alert">{error}</p>}
          <button className="button primary login-submit" type="submit" disabled={submitting || invitationCode.trim().length < 12}>
            {submitting ? "正在验证…" : "进入工作台"}
          </button>
        </form>
        <p className="login-security">令牌保存在 HttpOnly 安全 Cookie 中，页面脚本无法读取。</p>
      </section>
    </main>
  );
}

type ChatPageProps = {
  mode: ChatMode;
  input: string;
  messages: ChatMessage[];
  onModeChange: (mode: ChatMode) => void;
  onInputChange: (value: string) => void;
  onInvokeSkill: (skill: string) => void;
  onSubmit: () => void;
  submitting: boolean;
  onPrompt: (value: string) => void;
  contextRecords: LibraryRecord[];
  selectedSourceIds: string[];
  onRemoveSource: (recordId: string) => void;
  selectedKnowledgeIds: string[];
  knowledgePickerOpen: boolean;
  onToggleKnowledge: () => void;
  selectedSkills: string[];
  onRemoveSkill: (skill: string) => void;
  skillsPickerOpen: boolean;
  onToggleSkillsPicker: () => void;
  onToggleKnowledgeRecord: (recordId: string) => void;
  attachments: ApiAttachment[];
  attachmentUploading: boolean;
  onUploadAttachments: (files: FileList | null) => void;
  onRemoveAttachment: (attachmentId: string) => void;
  onViewResult: (page: "breakdowns" | "creations", recordId?: string) => void;
  onContinueTopic: (candidate: ApiTopicCandidate, taskId: string) => void;
};

function ChatPage({ mode, input, messages, onModeChange, onInputChange, onInvokeSkill, onSubmit, submitting, onPrompt, contextRecords, selectedSourceIds, onRemoveSource, selectedKnowledgeIds, knowledgePickerOpen, onToggleKnowledge, selectedSkills, onRemoveSkill, skillsPickerOpen, onToggleSkillsPicker, onToggleKnowledgeRecord, attachments, attachmentUploading, onUploadAttachments, onRemoveAttachment, onViewResult, onContinueTopic }: ChatPageProps) {
  const config = modeConfig[mode];
  const invokedSkills = selectedSkills.length ? selectedSkills : config.defaultSkills;
  const placeholder = skills.find((skill) => skill.name === selectedSkills[0])?.prompt ?? config.placeholder;
  const attachmentInput = useRef<HTMLInputElement>(null);
  const [expandedLibrary, setExpandedLibrary] = useState<LibraryRecord["libraryType"]>();
  const selectedSources = selectedSourceIds.map((id) => {
    const record = contextRecords.find((item) => item.id === id);
    const libraryType = record?.libraryType
      ?? (id.startsWith("col_") ? "collections" : id.startsWith("brk_") ? "breakdowns" : "creations");
    return { id, title: record?.title ?? id, libraryType };
  });
  const knowledgeLibraries = [
    { type: "collections" as const, label: "采集库", icon: Database },
    { type: "breakdowns" as const, label: "拆解库", icon: Layers3 },
    { type: "creations" as const, label: "创作库", icon: PenLine },
  ];
  return (
    <div className="chat-layout">
      <section className={`chat-stage ${messages.length ? "has-messages" : ""}`}>
        {!messages.length && (
          <>
            <section className="agent-intro" aria-label="Riffloom 智能体介绍">
              <strong>你好呀！我是 Riffloom 智能体 👋</strong>
            </section>

            <div className="chat-mode-tabs" role="tablist" aria-label="对话功能">
              {(Object.keys(modeConfig) as ChatMode[]).map((key) => (
                <button key={key} role="tab" aria-selected={mode === key} className={mode === key ? "active" : ""} onClick={() => onModeChange(key)}>{modeConfig[key].label}</button>
              ))}
            </div>
          </>
        )}

      {!!messages.length && (
        <div className="message-list" aria-live="polite">
          {messages.map((message) => (
            <article key={message.id} className={`message ${message.role} ${message.topicCandidates?.length ? "with-topics" : ""}`}>
              <p>{message.text}</p>
              {!!message.topicCandidates?.length && message.taskId && (
                <div className="topic-candidates" aria-label="选题建议">
                  {message.topicCandidates.map((candidate, index) => (
                    <section className="topic-card" key={`${message.taskId}-${candidate.topic}-${index}`}>
                      <header>
                        <span className="topic-rank">{index + 1}</span>
                        <h3>{candidate.topic}</h3>
                      </header>
                      <dl>
                        <div><dt>为什么火</dt><dd>{candidate.why_hot}</dd></div>
                        <div><dt>目标人群</dt><dd>{candidate.audience}</dd></div>
                        <div><dt>核心价值</dt><dd>{candidate.core_value}</dd></div>
                        <div><dt>标题建议</dt><dd>{candidate.title_suggestion}</dd></div>
                        <div><dt>我的切入点</dt><dd>{candidate.angle}</dd></div>
                      </dl>
                      {!!candidate.sources.length && (
                        <div className="topic-sources">
                          {candidate.sources.map((source) => (
                            <span key={`${source.platform}-${source.source_id}`}>
                              {source.platform === "xiaohongshu" ? "小红书" : source.platform === "douyin" ? "抖音" : source.platform === "bilibili" ? "B站" : source.platform === "weibo" ? "微博" : "沙箱"}
                            </span>
                          ))}
                        </div>
                      )}
                      <button className="button small primary" onClick={() => onContinueTopic(candidate, message.taskId!)}>
                        用这个选题继续创作 <PenLine />
                      </button>
                    </section>
                  ))}
                </div>
              )}
              {message.taskId && !message.chatReply && (message.resultId || !apiIntegrationEnabled) && (
                <div className="message-task">
                  <button onClick={() => onViewResult(message.resultPage ?? "creations", message.resultId)}>查看{message.resultPage === "breakdowns" ? "拆解" : "创作"}结果 <ArrowUp /></button>
                </div>
              )}
            </article>
          ))}
        </div>
      )}

      <div className="composer-region">
      {knowledgePickerOpen && (
        <section className="context-picker knowledge-picker" aria-label="引用知识库">
          <header>
            <div><strong>引用知识库</strong><span>选择库中的记录，选中后自动收起</span></div>
            <button className="icon-button small" onClick={onToggleKnowledge} aria-label="关闭引用知识"><X /></button>
          </header>
          <div className="knowledge-tree">
            {knowledgeLibraries.map(({ type, label, icon: Icon }) => {
              const records = contextRecords.filter((record) => record.libraryType === type);
              const expanded = expandedLibrary === type;
              return (
                <div className="knowledge-branch" key={type}>
                  <button className="knowledge-library" aria-expanded={expanded} onClick={() => setExpandedLibrary(expanded ? undefined : type)}>
                    <Icon /><span><strong>{label}</strong><small>{records.length} 条记录</small></span><ChevronRight />
                  </button>
                  {expanded && (
                    <div className="knowledge-records">
                      {records.map((record) => (
                        <button key={record.id} className={selectedKnowledgeIds.includes(record.id) ? "selected" : ""} onClick={() => onToggleKnowledgeRecord(record.id)}>
                          <FileText /><span><strong>{record.title}</strong><small>{record.version}</small></span>{selectedKnowledgeIds.includes(record.id) && <Check />}
                        </button>
                      ))}
                      {!records.length && <span className="context-empty">当前目录暂无记录</span>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {skillsPickerOpen && (
        <section className="context-picker skills-picker" aria-label="技能选择">
          <header>
            <div><strong>引用技能</strong><span>可选择多个技能；选中后显示在输入框中</span></div>
            <button className="button small" onClick={onToggleSkillsPicker}><Check />完成</button>
          </header>
          <div className="skill-picker-grid">
            {skills.map((skill) => (
              <button key={skill.name} className={`skill-picker-entry ${selectedSkills.includes(skill.name) ? "active" : ""}`} onClick={() => onInvokeSkill(skill.name)}>
                <span className="skill-icon"><AppIcon name={skill.name} /></span>
                <span><strong>{skill.name}</strong><small>{skill.description}</small></span>
              </button>
            ))}
          </div>
        </section>
      )}

      <div className="composer">
        <div className="composer-input">
          {!!selectedSources.length && (
            <div className="invoked-attachments" aria-label="当前来源">
              {selectedSources.map((record) => {
                const libraryLabel = record.libraryType === "collections" ? "采集库" : record.libraryType === "breakdowns" ? "拆解库" : "创作库";
                return <span className="invoked-attachment" key={record.id}>
                  <FileText />
                  <span>{libraryLabel} · {record.title}</span>
                  <button type="button" aria-label={`移除来源「${record.title}」`} onClick={() => onRemoveSource(record.id)}><X /></button>
                </span>;
              })}
            </div>
          )}
          {!!attachments.length && (
            <div className="invoked-attachments" aria-label="当前附件">
              {attachments.map((attachment) => (
                <span className="invoked-attachment" key={attachment.id}>
                  {attachment.mime_type.startsWith("image/") ? <ImageIcon /> : <FileText />}
                  <span>{attachment.original_name}</span>
                  <button type="button" aria-label={`移除附件「${attachment.original_name}」`} onClick={() => onRemoveAttachment(attachment.id)}><X /></button>
                </span>
              ))}
            </div>
          )}
          {!!invokedSkills.length && (
            <div className="invoked-skills" aria-label="当前调用技能">
              {invokedSkills.map((skill) => (
                <span className="invoked-skill" key={skill}>
                  <AppIcon name={skill} />{skill}
                  {selectedSkills.includes(skill) && <button type="button" aria-label={`移除技能「${skill}」`} onClick={() => onRemoveSkill(skill)}><X /></button>}
                </span>
              ))}
            </div>
          )}
          <textarea value={input} onChange={(event) => onInputChange(event.target.value)} placeholder={placeholder} aria-label="任务描述" onKeyDown={(event) => { if (event.key === "Backspace" && !input && selectedSkills.length) { event.preventDefault(); onRemoveSkill(selectedSkills.at(-1)!); } else if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); onSubmit(); } }} />
        </div>
        <div className="composer-toolbar">
          <div className="composer-tools">
            {mode !== "hot" && (
              <>
                <input ref={attachmentInput} className="attachment-input" type="file" multiple accept=".png,.jpg,.jpeg,.txt,.md,.docx,image/png,image/jpeg,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={(event) => { onUploadAttachments(event.target.files); event.currentTarget.value = ""; }} />
                <button className="tool-chip attachment-trigger" onClick={() => attachmentInput.current?.click()} disabled={attachmentUploading || attachments.length >= 4} aria-label="添加图片或文档"><Plus />{attachmentUploading ? "上传中" : "附件"}</button>
              </>
            )}
            {config.tools.map((tool) => (
              <button key={tool} className={`tool-chip ${(tool === "skills" && selectedSkills.length) || (tool === "knowledge" && selectedKnowledgeIds.length) ? "on" : ""}`} onClick={() => tool === "knowledge" ? onToggleKnowledge() : onToggleSkillsPicker()}>
                {tool === "knowledge" ? <BookOpen /> : <Sparkles />}
                {toolLabels[tool]}{tool === "knowledge" && selectedKnowledgeIds.length ? ` · ${selectedKnowledgeIds.length}` : ""}
              </button>
            ))}
          </div>
          <button className="send-button" onClick={onSubmit} disabled={(!input.trim() && (!attachments.length || mode === "capture")) || submitting} aria-label="发送">{submitting ? <RefreshCw className="spin" /> : <ArrowUp />}</button>
        </div>
      </div>
      </div>

      {!messages.length && (
        <>
          <div className="quick-prompts">
            {config.prompts.map((prompt) => <button key={prompt} onClick={() => onPrompt(prompt)}>{prompt}</button>)}
          </div>

          <section className="frequent-skills" aria-label="常用技能">
            <h2>常用技能</h2>
            <div className="skill-grid">
              {skills.map((skill) => (
                <button key={skill.name} className="skill-entry" onClick={() => onInvokeSkill(skill.name)}>
                  <span className="skill-icon"><AppIcon name={skill.name} /></span>
                  <span><strong>{skill.name}</strong><small>{skill.description}</small></span>
                </button>
              ))}
            </div>
          </section>
        </>
      )}
      </section>
    </div>
  );
}

type LibraryPageProps = {
  title: string;
  description: string;
  records: LibraryRecord[];
  query: string;
  onQueryChange: (value: string) => void;
  onOpenRecord: (record: LibraryRecord) => void;
  feishuScope: FeishuScope;
  binding?: ApiFeishuBinding;
  provider: ApiFeishuProvider | null;
  canConfigureFeishu: boolean;
  onConfigureFeishu: () => void;
  onSyncFeishu: (sourceRecordId?: string) => void;
  onPauseFeishu: () => void;
  onCancelTask: (taskId: string) => void;
  tabs?: Array<[CollectionKind, string]>;
  activeTab?: CollectionKind;
  onTabChange?: (tab: CollectionKind) => void;
  primaryAction: string;
  onPrimaryAction: () => void;
  dataLabel: string;
  emptyHint?: string;
  onBatchAction?: (action: "breakdown" | "rewrite", recordIds: string[]) => void;
  onBatchMetadata?: (
    recordIds: string[],
    change: { benchmark?: boolean; categoryTags?: string[] },
  ) => void;
  onBatchDelete?: (recordIds: string[]) => void;
};

function EmptyIllustration() {
  return (
    <svg className="empty-illustration" viewBox="0 0 240 176" fill="none" aria-hidden="true">
      <g stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round">
        <path className="empty-illustration-fill" d="M44 139c-14 3-23 10-23 17h194c0-8-11-14-27-17" />
        <circle className="empty-illustration-fill" cx="72" cy="49" r="25" />
        <path d="M49 47c2-17 11-25 24-25 15 0 23 10 25 25-8-3-13-9-17-17-7 10-17 15-32 17Z" />
        <circle cx="63" cy="52" r="2.5" fill="currentColor" stroke="none" />
        <circle cx="81" cy="52" r="2.5" fill="currentColor" stroke="none" />
        <path d="M66 63c4 3 8 3 12 0" />
        <path className="empty-illustration-fill" d="M51 83c12-9 31-9 43 0l8 55H43l8-55Z" />
        <path d="M47 91 31 119m61-28 18 20" />
        <path d="M31 119c-2 8 2 13 10 13m69-21c7 2 10-1 11-7" />
        <path d="M59 138v17m27-17v17m-35 0h16m11 0h16" />
        <path className="empty-illustration-paper" d="m116 52 39 8-8 43-39-8 8-43Z" />
        <path d="m123 65 22 5m-24 7 17 4" />
        <path d="M135 108v11" strokeDasharray="2 8" />
        <path className="empty-illustration-fill" d="m124 124 15-14h57l14 14-9 32h-69l-8-32Z" />
        <path d="m124 124 31 8h26l29-8m-55 8-16-22m42 22 15-22" />
      </g>
      <circle cx="18" cy="137" r="4" fill="currentColor" opacity=".35" />
      <circle cx="221" cy="132" r="3" fill="currentColor" opacity=".24" />
    </svg>
  );
}

export function LibraryPage({ title, description, records, query, onQueryChange, onOpenRecord, feishuScope, binding, provider, canConfigureFeishu, onConfigureFeishu, onSyncFeishu, onPauseFeishu, onCancelTask, tabs, activeTab, onTabChange, primaryAction, onPrimaryAction, dataLabel, emptyHint, onBatchAction, onBatchMetadata, onBatchDelete }: LibraryPageProps) {
  const [contentType, setContentType] = useState("all");
  const [providerFilter, setProviderFilter] = useState("all");
  const [benchmarkFilter, setBenchmarkFilter] = useState("all");
  const [tagFilter, setTagFilter] = useState("all");
  const [sortBy, setSortBy] = useState("updated");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [hiddenColumns, setHiddenColumns] = useState<string[]>([]);
  const providers = useMemo(() => [...new Set(records.map((record) => record.provider).filter(Boolean) as string[])], [records]);
  const contentTypes = useMemo(() => [...new Set(records.map((record) => record.contentType).filter(Boolean) as string[])], [records]);
  const categoryTags = useMemo(() => [...new Set(records.flatMap((record) => record.categoryTags ?? []))], [records]);
  const filtered = useMemo(() => records
    .filter((record) => Object.values(record).join(" ").toLowerCase().includes(query.toLowerCase()))
    .filter((record) => contentType === "all" || record.contentType === contentType)
    .filter((record) => providerFilter === "all" || record.provider === providerFilter)
    .filter((record) => benchmarkFilter === "all" || record.benchmark === (benchmarkFilter === "yes"))
    .filter((record) => tagFilter === "all" || record.categoryTags?.includes(tagFilter))
    .sort((left, right) => {
      const metric = sortBy === "likes" ? "likes" : sortBy === "collects" ? "collects" : sortBy === "comments" ? "comments" : null;
      if (metric) return (right.metrics?.[metric] ?? 0) - (left.metrics?.[metric] ?? 0);
      const rightDate = sortBy === "published" ? right.publishedAt : right.updatedAtIso;
      const leftDate = sortBy === "published" ? left.publishedAt : left.updatedAtIso;
      return new Date(rightDate ?? 0).getTime() - new Date(leftDate ?? 0).getTime();
    }), [records, query, contentType, providerFilter, benchmarkFilter, tagFilter, sortBy]);
  const selectable = filtered.slice(0, 20);
  const allCurrentSelected = selectable.length > 0 && selectable.every((record) => selectedIds.includes(record.id));
  const selectedContentOnly = selectedIds.every((id) =>
    records.find((record) => record.id === id)?.collectionKind !== "creator_profile"
  );

  function toggleAllCurrent() {
    const visible = new Set(selectable.map((record) => record.id));
    setSelectedIds((current) => allCurrentSelected
      ? current.filter((id) => !visible.has(id))
      : [...new Set([...current, ...visible])]);
  }

  function replaceCategoryTags() {
    const value = window.prompt("用逗号分隔分类标签；留空将清除当前分类标签：", "");
    if (value === null) return;
    onBatchMetadata?.(selectedIds, { categoryTags: value.split(/[,，]/).map((item) => item.trim()).filter(Boolean) });
    setSelectedIds([]);
  }
  return (
    <section className="page-panel">
      <header className="page-header">
        <div><h1>{title}</h1><p>{description}</p></div>
        <div className="header-actions">
          {(binding || canConfigureFeishu) && <button className="button" onClick={onConfigureFeishu}><ExternalLink />{binding ? "飞书设置" : "首次配置飞书"}</button>}
          <button className="button primary" onClick={onPrimaryAction}><Plus />{primaryAction}</button>
        </div>
      </header>

      <FeishuBanner scope={feishuScope} binding={binding} provider={provider} canConfigure={canConfigureFeishu} onConfigure={onConfigureFeishu} onSync={() => onSyncFeishu()} onPause={onPauseFeishu} onCancel={onCancelTask} />

      {!!tabs?.length && (
        <div className="library-tabs">
          {tabs.map(([key, label]) => <button key={key} className={activeTab === key ? "active" : ""} onClick={() => onTabChange?.(key)}>{label}</button>)}
        </div>
      )}

      <div className="library-card">
        <div className="library-toolbar">
          <label className="search-field"><Search /><input aria-label={`搜索${title}记录`} value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder={`搜索${title}记录`} /></label>
          {onBatchAction && <div className="library-filters" aria-label="采集库筛选与排序">
            <select aria-label="内容类型筛选" value={contentType} onChange={(event) => setContentType(event.target.value)}><option value="all">全部内容类型</option>{contentTypes.map((value) => <option key={value}>{value}</option>)}</select>
            <select aria-label="Provider 筛选" value={providerFilter} onChange={(event) => setProviderFilter(event.target.value)}><option value="all">全部 Provider</option>{providers.map((value) => <option key={value}>{value}</option>)}</select>
            <select aria-label="对标状态筛选" value={benchmarkFilter} onChange={(event) => setBenchmarkFilter(event.target.value)}><option value="all">全部对标状态</option><option value="yes">仅对标</option><option value="no">非对标</option></select>
            <select aria-label="分类标签筛选" value={tagFilter} onChange={(event) => setTagFilter(event.target.value)}><option value="all">全部分类标签</option>{categoryTags.map((value) => <option key={value}>{value}</option>)}</select>
            <select aria-label="采集库排序" value={sortBy} onChange={(event) => setSortBy(event.target.value)}><option value="updated">更新时间</option><option value="published">发布时间</option><option value="likes">点赞</option><option value="collects">收藏</option><option value="comments">评论</option></select>
          </div>}
          <details className="column-picker"><summary>显示列</summary><div>{[["type", "类型"], ["status", "状态"], ["source", "来源"], ["tags", "分类标签"], ["version", "版本"], ["updated", "更新时间"]].map(([key, label]) => <label key={key}><input type="checkbox" aria-label={`显示${label}列`} checked={!hiddenColumns.includes(key)} onChange={() => setHiddenColumns((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key])} />{label}</label>)}</div></details>
        </div>
        {!!selectedIds.length && onBatchAction && <div className="batch-toolbar" role="toolbar" aria-label="采集库批量操作"><strong>已选 {selectedIds.length} 条</strong>{provider?.external_calls && provider.writes_enabled && binding?.can_sync && binding.status === "active" && <button className="button small" disabled={selectedIds.length !== 1} title={selectedIds.length === 1 ? undefined : "真实飞书单次只允许同步 1 条"} onClick={() => onSyncFeishu(selectedIds[0])}>同步所选 1 条到飞书</button>}<button className="button small" onClick={() => { onBatchMetadata?.(selectedIds, { benchmark: true }); setSelectedIds([]); }}>设为对标</button><button className="button small" onClick={() => { onBatchMetadata?.(selectedIds, { benchmark: false }); setSelectedIds([]); }}>取消对标</button><button className="button small" onClick={replaceCategoryTags}>替换分类标签</button><button className="button small" disabled={!selectedContentOnly} title={selectedContentOnly ? undefined : "博主资料不支持拆解"} onClick={() => { onBatchAction("breakdown", selectedIds); setSelectedIds([]); }}>批量拆解</button><button className="button small primary" disabled={!selectedContentOnly} title={selectedContentOnly ? undefined : "博主资料不支持仿写"} onClick={() => { onBatchAction("rewrite", selectedIds); setSelectedIds([]); }}>批量仿写</button>{onBatchDelete && <button className="button small danger" onClick={() => { if (window.confirm(`确认删除选中的 ${selectedIds.length} 条采集记录？`)) { onBatchDelete(selectedIds); setSelectedIds([]); } }}><Trash2 />批量删除</button>}</div>}
        {records.length === 0 ? (
          <div className="library-empty">
            <EmptyIllustration />
            <strong>{title}还是空的</strong>
            <span>{emptyHint ?? `点击「${primaryAction}」开始积累第一条记录`}</span>
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead><tr>{onBatchAction && <th><input type="checkbox" aria-label="全选当前结果" checked={allCurrentSelected} onChange={toggleAllCurrent} /></th>}<th>标题</th>{!hiddenColumns.includes("type") && <th>类型</th>}{!hiddenColumns.includes("status") && <th>状态</th>}{!hiddenColumns.includes("source") && <th>来源</th>}{!hiddenColumns.includes("tags") && <th>分类标签</th>}{!hiddenColumns.includes("version") && <th>版本</th>}{!hiddenColumns.includes("updated") && <th>更新时间</th>}<th aria-label="操作" /></tr></thead>
              <tbody>
                {filtered.map((record, index) => (
                  <tr key={record.id} onClick={() => onOpenRecord(record)}>
                    {onBatchAction && <td onClick={(event) => event.stopPropagation()}><input type="checkbox" aria-label={`选择 ${record.title}`} checked={selectedIds.includes(record.id)} disabled={!selectedIds.includes(record.id) && selectedIds.length >= 20} onChange={() => setSelectedIds((current) => current.includes(record.id) ? current.filter((id) => id !== record.id) : [...current, record.id])} /></td>}
                    <td><div className="record-title"><span className={`record-thumb thumb-${index + 1}`} style={record.thumbnailUrl ? { backgroundImage: `url(${JSON.stringify(record.thumbnailUrl)})` } : undefined}>{!record.thumbnailUrl && <ImageIcon />}</span><strong>{record.title}{record.benchmark ? " · 对标" : ""}</strong></div></td>
                    {!hiddenColumns.includes("type") && <td>{record.type}</td>}
                    {!hiddenColumns.includes("status") && <td><span className={`record-status ${record.status.includes("部分") || record.status.includes("待") ? "warning" : ""}`}>{record.status}</span></td>}
                    {!hiddenColumns.includes("source") && <td>{record.source}</td>}
                    {!hiddenColumns.includes("tags") && <td><div className="tag-list">{(record.categoryTags?.length ? record.categoryTags : record.tags).map((tag, tagIndex) => <span key={`${record.id}-${tag}-${tagIndex}`}>{tag}</span>)}</div></td>}
                    {!hiddenColumns.includes("version") && <td className="mono">{record.version}</td>}
                    {!hiddenColumns.includes("updated") && <td>{record.updatedAt}</td>}
                    <td><button className="icon-button" aria-label={`查看 ${record.title} 详情`} onClick={(event) => { event.stopPropagation(); onOpenRecord(record); }}><MoreHorizontal /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!filtered.length && <div className="empty-state"><Search /><strong>没有符合条件的记录</strong><span>调整关键词后再试</span></div>}
          </div>
        )}
      </div>
      <p className="result-count">当前显示 {filtered.length} 条 · {dataLabel}</p>
    </section>
  );
}

function FeishuBanner({ scope, binding, provider, canConfigure, onConfigure, onSync, onPause, onCancel }: {
  scope: FeishuScope;
  binding?: ApiFeishuBinding;
  provider: ApiFeishuProvider | null;
  canConfigure: boolean;
  onConfigure: () => void;
  onSync: () => void;
  onPause: () => void;
  onCancel: (taskId: string) => void;
}) {
  const lastRun = binding?.last_run;
  const isRunning = lastRun?.status === "queued" || lastRun?.status === "running";
  const label = !binding
    ? "未绑定"
    : binding.status === "paused"
      ? "已暂停"
      : binding.status === "connection_invalid"
        ? "连接失效"
        : isRunning
          ? "同步中"
          : lastRun?.status === "cancelled"
            ? "已取消"
          : lastRun?.failed_count
            ? `${lastRun.failed_count} 条失败`
            : "已同步";
  const scopeLabel: Record<FeishuScope, string> = {
    "collection.single": "单篇采集库",
    "collection.keyword": "关键词采集库",
    "collection.creator_content": "博主内容库",
    "collection.creator_profile": "博主信息库",
    breakdown: "拆解库",
    creation: "创作库",
  };
  return (
    <section className="sync-banner" aria-label="飞书同步状态">
      <span className="sync-logo" aria-hidden="true" />
      <div className="sync-copy"><strong>飞书多维表格 · {binding?.target_table_name ?? scopeLabel[scope]}</strong><span>{!binding && !canConfigure ? "当前独立资料库未开通；不会写入管理员的飞书表" : `${provider?.provider ?? "sandbox-feishu-v1"} · ${provider && !provider.writes_enabled ? "只读 Gate · 写入关闭" : binding ? `${binding.link_count} 条稳定映射 · ${binding.external_calls ? "外部调用" : "0 次外部调用"}` : "可配置当前资料库的单向副本"}`}</span></div>
      <span className={`sync-state ${binding?.status ?? "unbound"} ${isRunning ? "syncing" : ""}`}>{label}</span>
      <div className="sync-actions">
        {binding?.target_openable
          ? <a className="button small" href={`/api/riffloom/integrations/feishu/bindings/${encodeURIComponent(binding.id)}/target`} target="_blank" rel="noreferrer"><ExternalLink />打开目标表</a>
          : binding && <button className="button small" disabled title="沙盒或尚未重新绑定真实目标"><ExternalLink />无外部目标</button>}
        {canConfigure && <button className="button small" onClick={onConfigure}><Settings2 />{binding ? "字段映射" : "配置绑定"}</button>}
        {binding?.can_configure && <button className="button small" onClick={onPause}>{binding.status === "paused" ? "恢复" : "暂停"}</button>}
        {isRunning && lastRun && <button className="button small" onClick={() => onCancel(lastRun.task_id)}>取消同步</button>}
        <button className="button small primary" onClick={onSync} disabled={!binding?.can_sync || binding.status !== "active" || isRunning}><RefreshCw className={isRunning ? "spin" : ""} />{!binding ? "未开通" : lastRun?.failed_count ? "重试失败" : binding.link_count ? "增量同步" : "首次全量"}</button>
      </div>
    </section>
  );
}

function TaskCenter({ tasks, onClose, onRetry, onCancel }: { tasks: Task[]; onClose: () => void; onRetry: (id: string) => void; onCancel: (id: string) => void }) {
  return (
    <aside className="task-center" aria-label="任务中心">
      <header><div><h2>任务中心</h2><p>慢任务可在后台继续执行</p></div><button className="icon-button" onClick={onClose} aria-label="关闭任务中心"><X /></button></header>
      <div className="task-list">
        {tasks.map((task) => {
          const resultType = task.resultPage === "collections" ? "collection" : task.resultPage === "breakdowns" ? "breakdown" : task.resultPage === "creations" ? "creation" : undefined;
          const resultId = task.resultRefs?.find((ref) => ref.type === resultType)?.id;
          const resultHref = task.resultPage === "chat"
            ? `/chat?task=${encodeURIComponent(task.id)}`
            : task.resultPage && resultId
              ? `${pagePath[task.resultPage]}?record=${encodeURIComponent(resultId)}`
              : task.resultPage ? pagePath[task.resultPage] : undefined;
          return <article key={task.id} className="task-card">
            <div className="task-card-head"><span className="task-kind">{task.kind}</span><StatusPill status={task.status} /></div>
            <h3>{task.title}</h3>
            <div className="task-meta"><span className="mono">{task.id}</span><span>{task.createdAt}</span></div>
            <div className="progress-track"><span style={{ width: `${task.progress}%` }} /></div>
            <strong className="task-stage">{task.stage} · {task.progress}%</strong>
            <p>{task.detail}</p>
            <div className="task-card-actions">
              {(task.status === "queued" || task.status === "running") && <button className="button small" onClick={() => onCancel(task.id)}>取消任务</button>}
              {canRetryTask(task.status, task.retryable !== false) && <button className="button small" onClick={() => onRetry(task.id)}><RefreshCw />{task.status === "partially_succeeded" ? "只重试失败项" : "重试任务"}</button>}
              {resultHref && <Link className="text-link" href={resultHref} onClick={onClose}>{task.resultPage === "chat" ? "查看回复" : "查看所在库"} <ChevronRight /></Link>}
            </div>
          </article>
        })}
      </div>
    </aside>
  );
}

type CollectionMedia = ApiCollectionDetail["media_refs"][number];

function mediaIsVideo(media: CollectionMedia) {
  return media.type.startsWith("video");
}

function formatCollectionTime(value: string | null) {
  if (!value) return "暂无";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "暂无";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function CollectionDetailContent({ detail }: { detail: ApiCollectionDetail }) {
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  const previewDialogRef = useDialogLifecycle<HTMLDivElement>(() => setPreviewIndex(null), previewIndex !== null);
  const isVideo = ["视频", "video"].includes(detail.content_type ?? "");
  const media = detail.media_refs;
  const images = media.filter((item) => !mediaIsVideo(item));
  const coverUrl = detail.cover_url ? resolveApiContentUrl(detail.cover_url) : null;
  const coverIndex = Math.max(0, media.findIndex((item) => item.type.startsWith("cover") || resolveApiContentUrl(item.url) === coverUrl));
  const bodyParagraphs = isVideo ? [] : readableParagraphs(detail.body, detail.topics);
  const transcript = readableTranscript(
    detail.video_transcript_corrected || detail.video_transcript || "",
    detail.video_transcript_segments,
  );
  const transcriptParagraphs = readableParagraphs(transcript);
  const activeMedia = previewIndex == null ? null : media[previewIndex];

  useEffect(() => {
    if (previewIndex == null) return;
    const index = previewIndex;
    function handleKeyDown(event: KeyboardEvent) {
      if (media.length > 1 && event.key === "ArrowRight") setPreviewIndex((index + 1) % media.length);
      if (media.length > 1 && event.key === "ArrowLeft") setPreviewIndex((index - 1 + media.length) % media.length);
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [media.length, previewIndex]);

  function downloadMedia(item: CollectionMedia) {
    const anchor = document.createElement("a");
    anchor.href = resolveApiContentUrl(item.url);
    anchor.download = "";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  }

  return (
    <>
      {coverUrl ? (
        <button className="detail-cover" type="button" aria-label="查看真实封面" style={{ backgroundImage: `url(${coverUrl})` }} disabled={!media.length} onClick={() => setPreviewIndex(coverIndex)} />
      ) : (
        <div className="detail-visual"><FileText /></div>
      )}
      {bodyParagraphs.length > 0 && (
        <section className="detail-section readable-section">
          <h3>正文</h3>
          <div className="readable-copy">{bodyParagraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}</div>
        </section>
      )}
      {detail.topics.length > 0 && (
        <section className="detail-section topic-section">
          <h3>话题标签</h3>
          <div className="topic-tag-list">{detail.topics.map((topic) => <span key={topic}>#{topic.replace(/^#/, "")}</span>)}</div>
        </section>
      )}
      {transcriptParagraphs.length > 0 && (
        <section className="detail-section readable-section">
          <h3>视频文案</h3>
          <div className="readable-copy">{transcriptParagraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}</div>
        </section>
      )}
      {media.length > 0 && (
        <section className="detail-section media-section">
          <header className="media-section-head">
            <div><h3>媒体附件</h3><span>{isVideo ? "封面与视频" : `共 ${images.length} 张图片`}</span></div>
            {!isVideo && images.length > 1 && <button className="button small" type="button" onClick={() => images.forEach(downloadMedia)}><Download />下载全部图片</button>}
          </header>
          <div className="media-gallery">
            {media.map((item, index) => {
              const video = mediaIsVideo(item);
              const imageNumber = images.indexOf(item) + 1;
              const label = video ? "视频" : item.type.startsWith("cover") ? "封面" : `图片 ${imageNumber}`;
              const url = resolveApiContentUrl(item.url);
              return (
                <article className="media-card" key={item.asset_id ?? `${item.type}-${index}`}>
                  <button className={`media-thumbnail ${video ? "video" : ""}`} type="button" aria-label={`查看${label}`} style={{ backgroundImage: `url(${video ? coverUrl ?? "" : url})` }} onClick={() => setPreviewIndex(index)}>
                    {video && <span><Play />播放视频</span>}
                  </button>
                  <div className="media-card-meta"><strong>{label}</strong>{item.byte_size && <span>{(Number(item.byte_size) / 1024 / 1024).toFixed(1)} MiB</span>}</div>
                  <div className="media-card-actions"><button type="button" onClick={() => setPreviewIndex(index)}><Eye />查看</button><a href={url} download><Download />下载</a></div>
                </article>
              );
            })}
          </div>
        </section>
      )}
      {activeMedia && previewIndex != null && (
        <div ref={previewDialogRef} className="media-lightbox" role="dialog" aria-modal="true" aria-label="媒体预览" onMouseDown={(event) => { if (event.target === event.currentTarget) setPreviewIndex(null); }}>
          <div className="media-lightbox-card">
            <button className="icon-button media-lightbox-close" type="button" aria-label="关闭媒体预览" onClick={() => setPreviewIndex(null)}><X /></button>
            {media.length > 1 && <button className="icon-button media-lightbox-prev" type="button" aria-label="查看上一项" onClick={() => setPreviewIndex((previewIndex - 1 + media.length) % media.length)}><ChevronLeft /></button>}
            <div className="media-lightbox-stage">
              {mediaIsVideo(activeMedia) ? <video src={resolveApiContentUrl(activeMedia.url)} controls playsInline /> : <div role="img" aria-label={`图片 ${previewIndex + 1}`} style={{ backgroundImage: `url(${resolveApiContentUrl(activeMedia.url)})` }} />}
            </div>
            {media.length > 1 && <button className="icon-button media-lightbox-next" type="button" aria-label="查看下一项" onClick={() => setPreviewIndex((previewIndex + 1) % media.length)}><ChevronRight /></button>}
            <footer><strong>{mediaIsVideo(activeMedia) ? "视频" : activeMedia.type.startsWith("cover") ? "封面" : `图片 ${images.indexOf(activeMedia) + 1}`} · {previewIndex + 1}/{media.length}</strong><a className="button small" href={resolveApiContentUrl(activeMedia.url)} download><Download />下载当前项</a></footer>
          </div>
        </div>
      )}
    </>
  );
}

function RecordDrawer({ record, page, workspaceName, tab, adoptedVersion, apiDetail, breakdownDetail, collectionDetail, transcriptionProvider, canDelete, canSyncFeishu, onTabChange, onClose, onAdopt, onContinue, onBreakdownComplete, onCollectionDetailChange, onTaskChange, onToast, onSyncFeishu, onSaveVersion, onDelete }: { record: LibraryRecord; page: PageKey; workspaceName: string; tab: "content" | "versions" | "source" | "audit"; adoptedVersion?: string; apiDetail: ApiCreationDetail | null; breakdownDetail: ApiBreakdownDetail | null; collectionDetail: ApiCollectionDetail | null; transcriptionProvider: ApiTranscriptionProvider | null; canDelete: boolean; canSyncFeishu: boolean; onTabChange: (tab: "content" | "versions" | "source" | "audit") => void; onClose: () => void; onAdopt: (version: string) => void; onContinue: () => void; onBreakdownComplete: (recordId: string) => void; onCollectionDetailChange: (detail: ApiCollectionDetail) => void; onTaskChange: (task: ApiTask) => void; onToast: (text: string) => void; onSyncFeishu: () => void; onSaveVersion: (body: string) => void; onDelete: () => void }) {
  const dialogRef = useDialogLifecycle(onClose);
  const currentCreation = apiDetail?.versions.find((version) => version.id === apiDetail.current_version_id) ?? apiDetail?.versions.at(-1);
  const currentBreakdown = breakdownDetail?.versions.find((version) => version.id === breakdownDetail.current_version_id) ?? breakdownDetail?.versions.at(-1);
  const [editing, setEditing] = useState(false);
  const [draftBody, setDraftBody] = useState("");
  const [transcriptionFile, setTranscriptionFile] = useState<File | null>(null);
  const [transcriptionDuration, setTranscriptionDuration] = useState("");
  const [transcriptionRightsConfirmed, setTranscriptionRightsConfirmed] = useState(false);
  const [transcriptionBusy, setTranscriptionBusy] = useState(false);
  const [breakdownTask, setBreakdownTask] = useState<ApiTask | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const versions = apiDetail
    ? [...apiDetail.versions].sort((a, b) => b.version - a.version).map((version) => ({ label: `v${version.version}`, note: version.change_note, createdAt: new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(version.created_at)), id: version.id }))
    : breakdownDetail
      ? [...breakdownDetail.versions].sort((a, b) => b.version - a.version).map((version) => ({ label: `v${version.version}`, note: String(version.skill_snapshot.name ?? "爆款拆解"), createdAt: new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(version.created_at)), id: version.id }))
      : (page === "creations" ? [record.version, "v1"] : [record.version]).map((label, index) => ({ label, note: index === 0 ? "AI 生成后人工修改" : "历史生成版本", createdAt: index === 0 ? record.updatedAt : "昨天 16:20", id: label }));
  const activeAdoptedVersion = apiDetail?.adopted_version_id
    ? versions.find((version) => version.id === apiDetail.adopted_version_id)?.label
    : adoptedVersion;
  const transcriptRequired = Boolean(
    page === "collections"
    && collectionDetail?.collection_kind === "single"
    && ["视频", "video"].includes(collectionDetail.content_type ?? "")
    && !["complete", "corrected"].includes(collectionDetail.video_transcript_status),
  );
  const breakdownRunning = Boolean(breakdownTask && ["queued", "running"].includes(breakdownTask.status));
  const collectionLoading = page === "collections" && record.sourceKind === "api" && !collectionDetail;
  const hideCollectionType = page === "collections"
    && (collectionDetail?.collection_kind ?? record.collectionKind) === "single";

  async function submitBreakdown() {
    if (page !== "collections" || record.sourceKind !== "api") {
      onContinue();
      return;
    }
    if (!collectionDetail || transcriptRequired || breakdownRunning) return;
    if (collectionDetail.breakdown_id && collectionDetail.breakdown_is_current && !breakdownTask) {
      onBreakdownComplete(collectionDetail.breakdown_id);
      return;
    }
    try {
      const task = breakdownTask?.status === "failed"
        ? await retryApiTask(breakdownTask.id)
        : await createApiBreakdownTask({
            prompt: "请基于已采集的视频画面、声音、文案和互动数据，生成 800～1500 字的精简爆款拆解。",
            sourceIds: [record.id],
            idempotencyKey: `drawer-breakdown-${record.id}-${record.version}`,
            preset: "drawer_compact",
          });
      if (mounted.current) setBreakdownTask(task);
      onTaskChange(task);
      onToast("拆解任务已受理，关闭抽屉后仍会继续");
      const completed = await waitForApiTask(task, (current) => {
        if (mounted.current) setBreakdownTask(current);
        onTaskChange(current);
      });
      if (completed.status === "success") {
        const result = completed.result_refs.find((ref) => ref.type === "breakdown");
        if (!result) throw new ApiError("BREAKDOWN_RESULT_MISSING", "拆解已完成，但未找到结果记录");
        if (mounted.current) onBreakdownComplete(result.id);
        else onToast("拆解已完成，可在拆解库查看");
      } else {
        onToast(completed.error?.message ?? "拆解失败，可重试");
      }
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : "拆解任务提交失败");
    }
  }

  async function submitTranscription(event: React.FormEvent) {
    event.preventDefault();
    if (!transcriptionFile || !transcriptionRightsConfirmed || transcriptionBusy) return;
    const mimeType = (["audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/mp4", "audio/x-m4a", "video/mp4"] as const)
      .find((value) => value === transcriptionFile.type);
    const durationSeconds = Number(transcriptionDuration);
    if (!mimeType) {
      onToast("只接受 MP3、WAV、M4A 或 MP4 媒体");
      return;
    }
    if (transcriptionFile.size > 15 * 1024 * 1024) {
      onToast("转写媒体不能超过 15 MiB");
      return;
    }
    if (!Number.isFinite(durationSeconds) || durationSeconds <= 0 || durationSeconds > (transcriptionProvider?.short_max_seconds || 300)) {
      onToast(`媒体时长必须在 1–${transcriptionProvider?.short_max_seconds || 300} 秒之间`);
      return;
    }
    setTranscriptionBusy(true);
    try {
      const common = {
        recordId: record.id,
        filename: transcriptionFile.name,
        mimeType,
        durationSeconds,
        rightsConfirmed: true,
        idempotencyKey: `transcription-web-${crypto.randomUUID()}`,
      };
      let task: ApiTask;
      if (transcriptionProvider?.upload_mode === "tos_presign") {
        onToast("正在将媒体直传对象存储");
        const upload = await initApiTranscriptionUpload({
          filename: transcriptionFile.name,
          mimeType,
          byteSize: transcriptionFile.size,
          durationSeconds,
          rightsConfirmed: true,
        });
        if (!upload.presigned_put_url) throw new ApiError("TOS_PRESIGN_FAILED", "服务端未返回媒体直传地址");
        await putApiTranscriptionUpload(upload.presigned_put_url, transcriptionFile, mimeType);
        task = await createApiTranscriptionTask({ ...common, assetId: upload.asset_id });
      } else {
        task = await createApiTranscriptionTask({ ...common, dataUrl: await fileToDataUrl(transcriptionFile) });
      }
      onTaskChange(task);
      onToast("视频文案转写已受理，原始媒体最长保留 24 小时");
      const completed = await waitForApiTask(task);
      onTaskChange(completed);
      onCollectionDetailChange(await getApiCollection(record.id));
      if (completed.status === "success") {
        setTranscriptionFile(null);
        setTranscriptionDuration("");
        setTranscriptionRightsConfirmed(false);
        onToast("视频文案已补齐，原始媒体已清除");
      } else {
        onToast(completed.error?.message ?? "视频文案转写失败");
      }
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : "视频文案转写失败");
    } finally {
      setTranscriptionBusy(false);
    }
  }

  async function copyCreationBody() {
    try {
      await navigator.clipboard.writeText(currentCreation?.body ?? record.summary);
      onToast("正文已复制");
    } catch {
      onToast("正文复制失败");
    }
  }

  return (
    <div className="overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside ref={dialogRef} className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <header><div><h2 id="drawer-title">{record.title}</h2><p>{record.id} · 最后更新 {record.updatedAt}</p></div><button className="icon-button" onClick={onClose} aria-label="关闭详情"><X /></button></header>
        <nav className="drawer-tabs">
          {(page === "collections" ? [['content', '内容'], ['versions', '版本'], ['audit', '任务与审计']] as const : [['content', '内容'], ['versions', '版本'], ['source', '来源'], ['audit', '任务与审计']] as const).map(([key, label]) => <button key={key} className={tab === key ? "active" : ""} onClick={() => onTabChange(key)}>{label}</button>)}
        </nav>
        <div className="drawer-body">
          {tab === "content" && (
            <>
              {collectionDetail ? <CollectionDetailContent detail={collectionDetail} /> : currentBreakdown ? (
                <>
                  <div className="detail-visual"><FileText /></div>
                <div className="breakdown-sections">
                  <section className="detail-section"><h3>核心结论 · {currentBreakdown.hook.type}</h3><p>{currentBreakdown.hook.expression}</p><p className="detail-muted">{currentBreakdown.hook.why_effective}</p></section>
                  {Object.keys(breakdownDetail?.source_metrics ?? {}).length > 0 && <section className="detail-section"><h3>关键数据</h3><ul>{Object.entries(breakdownDetail?.source_metrics ?? {}).filter(([key, value]) => key !== "views" || value > 0).slice(0, 5).map(([key, value]) => <li key={key}>{collectionMetricLabel(key)}：{value.toLocaleString("zh-CN")}</li>)}</ul></section>}
                  <section className="detail-section"><h3>内容结构</h3><ol>{currentBreakdown.structure.slice(0, 6).map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ol></section>
                  <section className="detail-section"><h3>爆款原因</h3><ul>{currentBreakdown.observed_facts.slice(0, 4).map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></section>
                  {(currentBreakdown.visual.observed ?? []).length > 0 && <section className="detail-section"><h3>画面与声音</h3><ul>{(currentBreakdown.visual.observed ?? []).slice(0, 5).map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></section>}
                  <section className="detail-section"><h3>可复用公式</h3><ul>{currentBreakdown.reusable_methods.slice(0, 4).map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></section>
                  <section className="detail-section risk-section"><h3>短板与风险</h3><ul>{currentBreakdown.risks.slice(0, 2).map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></section>
                </div>
                </>
              ) : currentCreation ? (
                <>
                  <div className="detail-visual"><FileText /></div>
                  <section className="detail-section"><h3>标题候选</h3><ul>{currentCreation.title_candidates.map((title) => <li key={title}>{title}</li>)}</ul></section>
                  <section className="detail-section creation-body"><h3>当前正文 · v{currentCreation.version}</h3><p>{currentCreation.body}</p></section>
                  <section className="detail-section risk-section"><h3>风险提示</h3><ul>{currentCreation.risk_notes.map((item) => <li key={item}>{item}</li>)}</ul>{Object.keys(currentCreation.similarity_report).length > 0 && <p className="detail-muted">文本重合风险：{String(currentCreation.similarity_report.level ?? "none")} · {String(currentCreation.similarity_report.note ?? "")}</p>}</section>
                </>
              ) : (
                <><div className="detail-visual"><FileText /></div><section className="detail-section"><h3>内容摘要</h3><p>{record.summary}</p></section></>
              )}
              {transcriptRequired && (
                <form className="detail-section transcription-form" onSubmit={submitTranscription}>
                  <h3>补齐视频文案</h3>
                  <p>{transcriptionProvider?.enabled ? `上传不超过 15 MiB / ${transcriptionProvider.short_max_seconds} 秒的获权媒体，${transcriptionProvider.upload_mode === "tos_presign" ? "文件会直传对象存储，" : ""}转写成功后立即删除原始文件。` : "本机尚未启用转写服务，请先完成服务端配置。"}</p>
                  <div className="transcription-upload-row">
                    <label className="cover-file-field"><input type="file" accept="audio/mpeg,audio/mp3,audio/wav,audio/x-wav,audio/mp4,audio/x-m4a,video/mp4" aria-label="选择转写媒体" disabled={!transcriptionProvider?.enabled || transcriptionBusy} onChange={(event) => setTranscriptionFile(event.target.files?.[0] ?? null)} /><span>{transcriptionFile?.name ?? "选择 MP3、WAV、M4A 或 MP4"}</span></label>
                    <label><span>时长（秒）</span><input type="number" min="1" max={transcriptionProvider?.short_max_seconds || 300} step="0.1" required value={transcriptionDuration} disabled={!transcriptionProvider?.enabled || transcriptionBusy} onChange={(event) => setTranscriptionDuration(event.target.value)} /></label>
                  </div>
                  <label className="check-row required-check"><input type="checkbox" checked={transcriptionRightsConfirmed} disabled={!transcriptionProvider?.enabled || transcriptionBusy} onChange={(event) => setTranscriptionRightsConfirmed(event.target.checked)} /><span><strong>我确认有权将该媒体交给转写服务</strong><small>媒体会发送到火山方舟豆包模型进行语音识别；未确认时不会上传。</small></span></label>
                  <button className="button primary" type="submit" disabled={!transcriptionProvider?.enabled || !transcriptionFile || !transcriptionDuration || !transcriptionRightsConfirmed || transcriptionBusy}>{transcriptionBusy ? <RefreshCw className="spin" /> : <Upload />}{transcriptionBusy ? "正在处理" : "上传并补齐文案"}</button>
                </form>
              )}
              <section className="detail-grid">
                <div><span>状态</span><strong>{record.status}</strong></div>
                {!hideCollectionType && <div><span>类型</span><strong>{record.type}</strong></div>}
                <div><span>{collectionDetail?.entity_type === "blogger" ? "博主" : "作者"}</span><strong>{record.author}</strong></div>
                {page !== "collections" && <div><span>数据环境</span><strong>{record.provider === "deterministic-phase1" ? "历史确定性合成" : record.isSandbox ? "合规沙箱样本" : currentCreation?.generation_config.provider ? `${String(currentCreation.generation_config.provider)} · ${String(currentCreation.generation_config.model)}` : currentBreakdown?.model_call_id ? "结构化模型结果" : record.risk ?? "未发现阻断风险"}</strong></div>}
                {collectionDetail && <><div><span>采集时间</span><strong>{formatCollectionTime(collectionDetail.collected_at)}</strong></div><div><span>发布时间</span><strong>{formatCollectionTime(collectionDetail.published_at)}</strong></div></>}
                {Object.entries(collectionDetail?.metrics ?? record.metrics ?? {}).filter(([key, value]) => key !== "views" || value > 0).map(([key, value]) => <div key={key}><span>{collectionMetricLabel(key)}</span><strong>{value.toLocaleString("zh-CN")}</strong></div>)}
              </section>
              {editing && currentCreation && <section className="detail-section version-editor"><h3>基于 v{currentCreation.version} 创建新版本</h3><textarea value={draftBody} onChange={(event) => setDraftBody(event.target.value)} aria-label="新版本正文" /><div><button className="button" onClick={() => setEditing(false)}>取消</button><button className="button primary" disabled={draftBody.trim().length < 20 || draftBody === currentCreation.body} onClick={() => { onSaveVersion(draftBody); setEditing(false); }}>保存为新版本</button></div></section>}
            </>
          )}
          {tab === "versions" && (
            <section className="detail-section"><h3>不可变版本</h3><p>每次生成或正文修改都会创建新版本；确认采用始终指向一个明确版本。</p><div className="version-list">{versions.map((version, index) => <article key={version.id}><div><strong>{version.label}{index === 0 ? " · 当前版本" : ""}</strong><span>{version.createdAt} · {version.note}</span></div>{activeAdoptedVersion === version.label ? <span className="adopted-badge"><Check />已采用</span> : page === "creations" ? <button className="button small" onClick={() => onAdopt(version.label)}>标记采用</button> : null}</article>)}</div></section>
          )}
          {tab === "source" && (
              <section className="detail-section"><h3>来源与关联</h3><div className="trace-list"><div><span>关联来源</span><strong>{record.source}</strong></div><div><span>来源编号</span><strong className="mono">{collectionDetail?.source_id ?? record.sourceId}</strong></div>{page === "collections" ? <><div><span>采集服务</span><strong>{collectionProviderLabel(collectionDetail?.provider ?? record.provider ?? "sandbox-v1")}</strong></div><div><span>外部引用</span><strong className="mono">{collectionDetail?.external_url ?? record.externalUrl ?? "—"}</strong></div><div><span>边界</span><strong>{collectionDetail?.provider === "user-upload-v1" ? "仅保留来源链接与转写结果 · 不自动读取平台页面" : collectionDetail && !collectionDetail.is_sandbox ? "读取用户确认有权使用的完整分享链接 · 不接收浏览器登录信息、密码或验证码 · 无平台写入" : "获权脱敏样本 · 不代表平台实时数据 · 无平台写入"}</strong></div></> : <><div><span>技能版本</span><strong>{currentBreakdown ? `${String(currentBreakdown.skill_snapshot.name ?? "爆款拆解")} · v${String(currentBreakdown.skill_snapshot.version ?? "1.0.0")}` : apiDetail?.skill_id ?? "内置技能"}</strong></div><div><span>知识快照</span><strong>{currentCreation ? `${Array.isArray(currentCreation.knowledge_snapshot.fragments) ? currentCreation.knowledge_snapshot.fragments.length : 0} 个带版本片段` : "拆解来源快照已锁定"}</strong></div><div><span>版本安全</span><strong>生成时已锁定，不随知识库更新漂移</strong></div></>}</div></section>
          )}
          {tab === "audit" && (
            <section className="detail-section"><h3>任务与审计记录</h3><div className="trace-list"><div><span>任务编号</span><strong className="mono">{record.taskId ?? "T-1045"}</strong></div><div><span>执行来源</span><strong>{record.sourceKind === "api" ? record.provider === "deterministic-phase1" ? "历史持久化任务执行器" : page === "collections" ? "采集服务 + 持久化任务执行器" : "持久化任务执行器" : "本地示例数据"}</strong></div><div><span>工作空间</span><strong>{workspaceName}</strong></div><div><span>结果校验</span><strong>字段结构通过 · 来源完整 · 版本匹配</strong></div><div><span>数据说明</span><strong>{record.provider === "deterministic-phase1" ? "历史确定性合成记录；来源链接是当时输入，不代表已抓取平台内容" : record.isSandbox ? "合规沙箱中的获权脱敏样本；可重启恢复、可逐项追溯" : record.sourceKind === "api" ? "确定性合成内容；数据库事实可在重启后恢复" : "脱敏示例数据，不是实际模型轨迹"}</strong></div></div></section>
          )}
        </div>
        <footer className="drawer-actions">
          <button className="button primary" disabled={transcriptRequired || breakdownRunning || collectionLoading} onClick={() => { if (page === "creations" && currentCreation) { setDraftBody(currentCreation.body); setEditing(true); } else if (page === "collections") { void submitBreakdown(); } else { onContinue(); } }}>{page === "creations" ? "继续修改" : page === "breakdowns" ? "继续创作" : collectionLoading ? "加载中" : transcriptRequired ? "先补齐视频文案" : breakdownRunning ? `${breakdownTask?.stage ?? "正在拆解"} · ${breakdownTask?.progress ?? 0}%` : breakdownTask?.status === "failed" ? "重试拆解" : collectionDetail?.breakdown_id && collectionDetail.breakdown_is_current ? "查看拆解" : collectionDetail?.breakdown_id ? "重新拆解" : "发起拆解"}</button>
          {page === "creations" && <button className="button" onClick={() => { void copyCreationBody(); }}><Copy />复制正文</button>}
          {page === "creations" && <button className="button" onClick={() => onAdopt(versions[0]?.label ?? record.version)}><Check />确认采用当前版本</button>}
          {page === "collections" && canSyncFeishu && <button className="button" onClick={onSyncFeishu}><RefreshCw />同步这一条到飞书</button>}
          {page === "collections" ? (collectionDetail?.external_url || record.externalUrl) && <a className="button" href={collectionDetail?.external_url ?? record.externalUrl} target="_blank" rel="noreferrer"><ExternalLink />原文链接</a> : <button className="button" onClick={() => onTabChange("source")}><ExternalLink />查看来源</button>}
          {canDelete && <button className="button danger" type="button" onClick={onDelete}><Trash2 />删除</button>}
        </footer>
      </aside>
    </div>
  );
}

function FeishuModal({ scope, binding, provider, connected, canConfigure, onClose, onSaved, onToast }: {
  scope: FeishuScope;
  binding?: ApiFeishuBinding;
  provider: ApiFeishuProvider | null;
  connected: boolean;
  canConfigure: boolean;
  onClose: () => void;
  onSaved: (binding: ApiFeishuBinding, syncNow: boolean) => void;
  onToast: (text: string) => void;
}) {
  const dialogRef = useDialogLifecycle(onClose);
  const names: Record<FeishuScope, string> = {
    "collection.single": "单篇采集库",
    "collection.keyword": "关键词采集库",
    "collection.creator_content": "博主内容库",
    "collection.creator_profile": "博主信息库",
    breakdown: "拆解库",
    creation: "创作库",
  };
  const [connections, setConnections] = useState<ApiFeishuConnection[]>([]);
  const [baseUrl, setBaseUrl] = useState(binding?.target_base_id ?? "");
  const [targets, setTargets] = useState<ApiFeishuTarget[]>(
    binding ? [{ base_id: binding.target_base_id, base_name: "当前多维表格", tables: [{ table_id: binding.target_table_id, table_name: binding.target_table_name, scope_key: scope, fields: [] }] }] : [],
  );
  const [selectedTableId, setSelectedTableId] = useState(binding?.target_table_id ?? "");
  const [tableName, setTableName] = useState(binding?.target_table_name ?? names[scope]);
  const [externalCopyConfirmed, setExternalCopyConfirmed] = useState(false);
  const [syncNow, setSyncNow] = useState(provider?.writes_enabled ?? false);
  const [submitting, setSubmitting] = useState(false);
  const [loadingTargets, setLoadingTargets] = useState(false);
  const [preflightMessage, setPreflightMessage] = useState("尚未执行服务端字段预检");

  useEffect(() => {
    if (!connected || !canConfigure) return;
    let cancelled = false;
    void listApiFeishuConnections()
      .then((result) => { if (!cancelled) setConnections(result.items); })
      .catch((error) => { if (!cancelled) onToast(error instanceof ApiError ? error.message : "飞书连接状态加载失败"); });
    return () => { cancelled = true; };
  }, [canConfigure, connected, onToast]);

  async function loadTargets(connection: ApiFeishuConnection) {
    if (!baseUrl.trim() || loadingTargets) return;
    setLoadingTargets(true);
    try {
      const result = await listApiFeishuTargets(connection.id, { baseUrl: baseUrl.trim(), scopeKey: scope });
      setTargets(result.items);
      const tables = result.items.flatMap((base) => base.tables);
      const selected = tables.find((table) => table.table_id === selectedTableId) ?? tables[0];
      setSelectedTableId(selected?.table_id ?? "");
      setTableName(selected?.table_name ?? names[scope]);
      setPreflightMessage(tables.length ? `已读取 ${tables.length} 张本人可访问的数据表` : "该多维表格中没有可用数据表");
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : "飞书数据表读取失败");
    } finally {
      setLoadingTargets(false);
    }
  }

  async function saveBinding() {
    if (!connected || !canConfigure || !externalCopyConfirmed || !tableName.trim() || submitting) return;
    setSubmitting(true);
    try {
      let connection = connections.find((item) => item.status === "active");
      if (!connection) {
        if (provider?.auth_mode === "user_oauth") throw new ApiError("FEISHU_OAUTH_REQUIRED", "请先连接你自己的飞书账号");
        connection = await createApiFeishuConnection({
          tenantName: provider?.is_sandbox ? "示例团队 sandbox" : "示例飞书工作区",
          externalCopyConfirmed: true,
        });
        setConnections([connection]);
      }
      const result = provider?.auth_mode === "user_oauth"
        ? { items: targets }
        : await listApiFeishuTargets(connection.id);
      const base = result.items.find((item) => item.tables.some((table) => table.table_id === selectedTableId)) ?? result.items[0];
      const table = base?.tables.find((item) => provider?.auth_mode === "user_oauth" ? item.table_id === selectedTableId : item.scope_key === scope);
      if (!base || !table) throw new ApiError("FEISHU_TARGET_NOT_FOUND", "当前 Provider 没有匹配当前库的已授权目标表");
      const draft = {
        connectionId: connection.id,
        scopeKey: scope,
        targetBaseId: base.base_id,
        targetTableId: table.table_id,
        targetTableName: provider?.is_sandbox ? tableName.trim() : table.table_name,
        fieldMapping: binding?.field_mapping ?? {},
      };
      const preflight = await preflightApiFeishuBinding(draft);
      const onlyMissingFields = preflight.errors.length > 0 && preflight.errors.every((item) => item.code === "FEISHU_FIELD_MISSING");
      if (!preflight.valid && !onlyMissingFields) {
        setPreflightMessage(`预检失败：${preflight.errors[0]?.message ?? "字段不兼容"}`);
        return;
      }
      setPreflightMessage(onlyMissingFields ? "保存时会在本人目标表中自动补齐同步字段" : `预检通过 · ${preflight.required_fields.length} 个系统字段 · ${preflight.external_calls ? "已执行只读外部调用" : "0 次外部调用"}`);
      const configured = await createApiFeishuBinding(draft);
      onSaved(configured, syncNow);
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : "飞书绑定保存失败");
    } finally {
      setSubmitting(false);
    }
  }

  const connection = connections.find((item) => item.status === "active");
  const isReal = Boolean(provider && !provider.is_sandbox);
  const isUserOAuth = provider?.auth_mode === "user_oauth";
  const isRealReadonly = Boolean(provider && !provider.is_sandbox && !provider.writes_enabled);
  const scopeSupported = provider?.scopes.includes(scope) ?? false;
  const selectableTables = targets.flatMap((base) => base.tables);
  return (
    <div className="overlay modal-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialogRef} className="modal" role="dialog" aria-modal="true" aria-labelledby="feishu-title">
        <header><div><h2 id="feishu-title">同步到飞书多维表格</h2><p>{isUserOAuth ? "只写入你本人授权并选择的飞书数据表，其他用户无法复用此连接。" : isReal ? `仅向已确认的测试表单向${provider?.writes_enabled ? "写入" : "读取"}，Riffloom 保持为主数据源。` : `${names[scope]}将单向同步到确定性 sandbox，Riffloom 保持为主数据源。`}</p></div><button className="icon-button" onClick={onClose} aria-label="关闭飞书设置"><X /></button></header>
        <div className="connection-card"><span className="sync-logo" aria-hidden="true" /><div><strong>{connection ? `${connection.tenant_name} · 本人账号已连接` : "飞书 · 尚未连接本人账号"}</strong><span>{canConfigure ? (isUserOAuth ? "每位用户独立 OAuth 授权 · 令牌加密存储 · 不共享管理员飞书" : isReal ? "当前资料库配置" : "当前资料库配置 · 不读取真实凭据") : "当前角色只可查看同步状态"}</span></div><span className="sandbox-badge">{isUserOAuth ? "USER OAUTH" : provider?.external_calls ? provider.writes_enabled ? "OPENAPI WRITE GATED" : "READONLY OPENAPI" : "0 EXTERNAL CALLS"}</span></div>
        {isUserOAuth && !connection && canConfigure && <a className="button primary" href={feishuOAuthAuthorizeUrl(scope)}><ExternalLink />连接我的飞书</a>}
        <label className="form-label">目标方式</label>
        <div className="option-grid"><button className="active" disabled><strong>{isUserOAuth ? "我的飞书目标" : isReal ? `真实飞书${isRealReadonly ? "只读" : "测试"}目标` : "绑定 sandbox 目标"}</strong><span>{isReal ? "每类资料库绑定一张本人有权编辑的数据表" : "六类库各自保持一个稳定目标表"}</span></button><button disabled><strong>记录写入 {provider?.writes_enabled ? "已开启" : "保持关闭"}</strong><span>{provider?.writes_enabled ? "单次最多同步 30 条" : "需要再次确认具体测试表"}</span></button></div>
        {isUserOAuth ? <>
          <div className="form-row"><label><span>我的多维表格链接</span><input aria-label="我的多维表格链接" value={baseUrl} disabled={!connection || !canConfigure} placeholder="https://你的团队.feishu.cn/base/..." onChange={(event) => { setBaseUrl(event.target.value); setTargets([]); setSelectedTableId(""); setPreflightMessage("链接已变更，请重新读取数据表"); }} /></label><label><span>目标数据表</span><select aria-label="目标数据表" value={selectedTableId} disabled={!connection || !selectableTables.length} onChange={(event) => { const table = selectableTables.find((item) => item.table_id === event.target.value); setSelectedTableId(event.target.value); setTableName(table?.table_name ?? ""); setPreflightMessage("目标表已变更，需要重新预检"); }}>{!selectableTables.length && <option value="">请先读取数据表</option>}{selectableTables.map((table) => <option key={table.table_id} value={table.table_id}>{table.table_name}</option>)}</select></label></div>
          <button className="button" disabled={!connection || !baseUrl.trim() || loadingTargets} onClick={() => connection && void loadTargets(connection)}>{loadingTargets ? <RefreshCw className="spin" /> : <Search />}{loadingTargets ? "正在读取" : "读取我的数据表"}</button>
        </> : <div className="form-row"><label><span>多维表格文档</span><input value={isReal ? "Riffloom 已确认测试多维表格" : "Riffloom 内容工作台（sandbox）"} disabled /></label><label><span>目标数据表</span><input aria-label="目标数据表" value={tableName} disabled={!canConfigure || isReal} onChange={(event) => { setTableName(event.target.value); setPreflightMessage("字段映射已变更，需要重新预检"); }} /></label></div>}
        <div className="mapping-preview"><strong>系统字段预检</strong><span>riffloom_record_id → 文本（只读）</span><span>riffloom_record_version → 文本（只读）</span><span>last_synced_at → 文本·ISO 时间（只读）</span><span>riffloom_status → 文本（只读）</span><em>{preflightMessage}</em></div>
        <button className={`switch-line ${syncNow ? "on" : ""}`} onClick={() => setSyncNow((value) => !value)} aria-pressed={syncNow} disabled={!canConfigure || !provider?.writes_enabled}><span><strong>保存后立即同步</strong><small>{provider?.writes_enabled ? "首次为全量；以后仅同步新记录或新版本。" : "真实写入 Gate 关闭；需再次确认后才可启用。"}</small></span><i /></button>
        <label className="check-row required-check"><input type="checkbox" checked={externalCopyConfirmed} disabled={!canConfigure} onChange={(event) => setExternalCopyConfirmed(event.target.checked)} /><span><strong>我确认这是 Riffloom → 飞书的单向外部副本</strong><small>飞书编辑不会反向覆盖 Riffloom 主数据；断开连接不会删除已写入副本。</small></span></label>
        <div className="risk-note"><ShieldCheck />{isUserOAuth ? "只使用当前登录用户的授权访问所选表；断开后立即清除该用户令牌。" : isReal ? isRealReadonly ? "会读取已确认测试表及字段；不会创建、更新或删除记录，写入开关保持关闭。" : "只会写入已确认测试表，并受单批上限和幂等校验保护。" : "当前只写确定性本地 sandbox，不会连接真实飞书。"}</div>
        {!scopeSupported && <div className="risk-note"><ShieldCheck />当前资料库尚未配置已授权的飞书目标表。</div>}
        <footer><button className="button" onClick={onClose}>取消</button><button className="button primary" disabled={!connected || !canConfigure || !scopeSupported || !externalCopyConfirmed || !tableName.trim() || submitting || (isUserOAuth && (!connection || !selectedTableId))} onClick={() => void saveBinding()}>{submitting ? <RefreshCw className="spin" /> : <Check />}{binding ? "保存映射" : "预检并保存绑定"}</button></footer>
      </section>
    </div>
  );
}

function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("素材读取失败"));
    reader.readAsDataURL(file);
  });
}

async function waitForApiTask(task: ApiTask, onChange?: (task: ApiTask) => void) {
  let current = task;
  for (let index = 0; index < 240; index += 1) {
    if (["success", "partial_success", "failed", "cancelled"].includes(current.status)) return current;
    await new Promise((resolve) => window.setTimeout(resolve, 500));
    current = await getApiTask(current.id);
    onChange?.(current);
  }
  throw new ApiError("TASK_TIMEOUT", "任务仍在后台执行，请稍后刷新", true);
}

function CoverPage({ apiStatus, onTaskChange, onToast }: {
  apiStatus: ApiStatus;
  onTaskChange: (task: ApiTask) => void;
  onToast: (text: string) => void;
}) {
  const [provider, setProvider] = useState<ApiCoverProvider | null>(null);
  const [assets, setAssets] = useState<ApiMediaAsset[]>([]);
  const [covers, setCovers] = useState<ApiCover[]>([]);
  const [creations, setCreations] = useState<LibraryRecord[]>([]);
  const [selectedAssetIds, setSelectedAssetIds] = useState<string[]>([]);
  const [creationId, setCreationId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [assetRole, setAssetRole] = useState<"original" | "reference">("original");
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [externalUseConfirmed, setExternalUseConfirmed] = useState(false);
  const [prompt, setPrompt] = useState("为小红书内容设计一张重点清晰、留白克制的 3:4 封面");
  const [revisionTarget, setRevisionTarget] = useState<ApiCover | null>(null);
  const [revisionPrompt, setRevisionPrompt] = useState("");
  const [revisionUseConfirmed, setRevisionUseConfirmed] = useState(false);
  const [activeTask, setActiveTask] = useState<ApiTask | null>(null);
  const [busy, setBusy] = useState<"loading" | "upload" | "generate" | "revision" | "save" | "retry" | null>(null);

  async function refreshCovers() {
    const result = await listApiCovers();
    setCovers(result.items);
  }

  useEffect(() => {
    if (apiStatus !== "connected") return;
    let cancelled = false;
    void Promise.all([
      getCoverProvider(),
      listApiMediaAssets(),
      listApiCovers(),
      listApiLibrary("creations"),
    ]).then(([nextProvider, nextAssets, nextCovers, nextCreations]) => {
      if (cancelled) return;
      setProvider(nextProvider);
      setAssets(nextAssets.items);
      setCovers(nextCovers.items);
      setCreations(nextCreations.items.map(recordFromApi));
    }).catch((error) => {
      if (!cancelled) onToast(error instanceof ApiError ? error.message : "封面工作区加载失败");
    }).finally(() => {
      if (!cancelled) setBusy(null);
    });
    return () => { cancelled = true; };
  }, [apiStatus, onToast]);

  async function uploadAsset(event: React.FormEvent) {
    event.preventDefault();
    if (!file || !rightsConfirmed || busy) return;
    if (file.type !== "image/png" && file.type !== "image/jpeg") {
      onToast("只接受 PNG 或 JPEG 素材");
      return;
    }
    setBusy("upload");
    try {
      const uploaded = await createApiMediaAsset({
        filename: file.name,
        mimeType: file.type,
        role: assetRole,
        rightsConfirmed,
        dataUrl: await fileToDataUrl(file),
      });
      setAssets((current) => [uploaded, ...current.filter((item) => item.id !== uploaded.id)]);
      setSelectedAssetIds((current) => current.includes(uploaded.id) ? current : [...current, uploaded.id]);
      setFile(null);
      setRightsConfirmed(false);
      onToast(`素材「${uploaded.original_name}」已校验并入库`);
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : "素材上传失败");
    } finally {
      setBusy(null);
    }
  }

  async function finishTask(task: ApiTask, action: "generate" | "revision" | "retry") {
    setActiveTask(task);
    onTaskChange(task);
    const completed = await waitForApiTask(task);
    setActiveTask(completed);
    onTaskChange(completed);
    await refreshCovers();
    if (completed.status === "success") {
      onToast(action === "revision" ? "修改已生成 4 个新方案，旧封面保持不变" : "已生成 4 个可追溯封面方案");
      if (action === "revision") {
        setRevisionTarget(null);
        setRevisionPrompt("");
      }
    } else if (completed.status === "partial_success") {
      onToast("部分方案生成失败；已完成方案保持不变，可只重试失败项");
    } else {
      onToast(completed.error?.message ?? "封面任务失败");
    }
  }

  async function generateCovers() {
    if (prompt.trim().length < 5 || selectedAssetIds.length === 0 || busy || (provider?.requires_usage_confirmation && !externalUseConfirmed)) return;
    setBusy("generate");
    try {
      const task = await createApiCoverTask({
        prompt: prompt.trim(),
        mediaAssetIds: selectedAssetIds,
        creationId: creationId || undefined,
        usageConfirmed: externalUseConfirmed,
        idempotencyKey: `cover-web-${crypto.randomUUID()}`,
      });
      await finishTask(task, "generate");
      setExternalUseConfirmed(false);
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : "封面任务提交失败");
    } finally {
      setBusy(null);
    }
  }

  async function reviseCover() {
    if (!revisionTarget || revisionPrompt.trim().length < 5 || revisionTarget.revision_no >= (provider?.max_revisions ?? 2) || busy || (provider?.requires_usage_confirmation && !revisionUseConfirmed)) return;
    setBusy("revision");
    try {
      const task = await createApiCoverRevision({
        coverId: revisionTarget.id,
        prompt: revisionPrompt.trim(),
        usageConfirmed: revisionUseConfirmed,
        idempotencyKey: `cover-revision-web-${crypto.randomUUID()}`,
      });
      await finishTask(task, "revision");
      setRevisionUseConfirmed(false);
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : "封面修改任务提交失败");
    } finally {
      setBusy(null);
    }
  }

  async function retryCoverTask() {
    if (!activeTask || busy) return;
    setBusy("retry");
    try {
      await finishTask(await retryApiTask(activeTask.id), "retry");
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : "失败方案重试失败");
    } finally {
      setBusy(null);
    }
  }

  async function saveCover(cover: ApiCover) {
    if (busy) return;
    setBusy("save");
    try {
      await saveApiCover(cover.id);
      await refreshCovers();
      onToast(`方案 ${cover.variant_no} 已保存${cover.creation_id ? "并保留创作稿版本关联" : ""}`);
    } catch (error) {
      onToast(error instanceof ApiError ? error.message : "封面保存失败");
    } finally {
      setBusy(null);
    }
  }

  const uploadAssets = assets.filter((asset) => ["original", "reference"].includes(asset.role));
  const latestTaskId = covers[0]?.task_id;
  const latestCovers = covers
    .filter((cover) => cover.task_id === latestTaskId)
    .sort((left, right) => left.variant_no - right.variant_no);

  return (
    <section className="cover-page">
      <header className="page-header">
        <div><h1>封面设计</h1><p>引用创作稿和已确认使用权的素材，生成不可变、可追溯的静态封面版本</p></div>
        <span className={`cover-provider-badge ${provider?.is_mock ? "mock" : ""}`}>
          <ShieldCheck />{provider ? `${provider.provider} · ${provider.external_calls ? "外部调用已开启" : "0 次外部图片调用"}` : "Provider 加载中"}
        </span>
      </header>

      {apiStatus !== "connected" ? (
        <div className="cover-empty"><ImageIcon /><strong>API 尚未连接</strong><span>启动本地后端后可上传授权素材并生成封面。</span></div>
      ) : (
        <div className="cover-workspace">
          <div className="cover-composer cover-composer-unified">
            <form className="cover-material-zone" onSubmit={uploadAsset}>
              <header>
                <div><strong>封面素材</strong><span>PNG / JPEG · 单张 ≤ 5 MB · 已选 {selectedAssetIds.length} / 8</span></div>
              </header>
              <div className="cover-upload-row">
                <label className="cover-file-field"><input type="file" accept="image/png,image/jpeg" aria-label="选择封面素材" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><span>{file?.name ?? "选择本地图片"}</span></label>
                <div className="cover-role-row" aria-label="素材角色">
                  <button type="button" className={assetRole === "original" ? "active" : ""} onClick={() => setAssetRole("original")}>原片</button>
                  <button type="button" className={assetRole === "reference" ? "active" : ""} onClick={() => setAssetRole("reference")}>风格参考</button>
                </div>
                <button className="button primary" type="submit" disabled={!file || !rightsConfirmed || busy === "upload"}>{busy === "upload" ? <RefreshCw className="spin" /> : <Upload />}校验并添加</button>
              </div>
              <label className="check-row required-check"><input type="checkbox" checked={rightsConfirmed} onChange={(event) => setRightsConfirmed(event.target.checked)} /><span><strong>我确认拥有必要使用权</strong><small>未经确认的素材不会入库，也不能参与生成。</small></span></label>
              <div className="cover-material-list">
                {uploadAssets.map((asset) => (
                  <label key={asset.id} className={selectedAssetIds.includes(asset.id) ? "selected" : ""}>
                    <input type="checkbox" checked={selectedAssetIds.includes(asset.id)} onChange={(event) => setSelectedAssetIds((current) => event.target.checked ? [...current, asset.id].slice(0, 8) : current.filter((id) => id !== asset.id))} />
                    <span className="cover-thumb" role="img" aria-label={`素材 ${asset.original_name}`} style={{ backgroundImage: `url(${resolveApiContentUrl(asset.content_url)})` }} />
                    <span><strong>{asset.original_name}</strong><small>{asset.role === "original" ? "原片" : "风格参考"} · {asset.width}×{asset.height}</small></span>
                  </label>
                ))}
                {!uploadAssets.length && <div className="cover-list-empty">添加一张已获权图片后即可选择</div>}
              </div>
            </form>

            <label className="cover-creation-link"><Link2 /><span>关联创作稿（可选）</span><select aria-label="关联创作稿" value={creationId} onChange={(event) => setCreationId(event.target.value)}><option value="">不关联创作稿</option>{creations.map((creation) => <option key={creation.id} value={creation.id}>{creation.title} · {creation.version}</option>)}</select></label>

            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} aria-label="封面设计要求" placeholder="描述封面用途、重点和风格，例如：标题醒目、留白克制、突出产品" />

            {provider?.requires_usage_confirmation && <label className="check-row required-check"><input type="checkbox" checked={externalUseConfirmed} onChange={(event) => setExternalUseConfirmed(event.target.checked)} /><span><strong>我确认发送素材并接受本次费用上限</strong><small>选中素材及关联创作摘要将发送给外部图片服务；最多 {provider.variants} 次调用，预计不超过 ¥{provider.estimated_cost_cny_max_request.toFixed(2)}，仅成功图片计费。</small></span></label>}

            <div className="cover-composer-footer">
              <span className="cover-spec"><ImageIcon />固定 3:4 · 1200×1600 · 每次 4 个方案</span>
              <button className="send-button" onClick={() => void generateCovers()} disabled={prompt.trim().length < 5 || selectedAssetIds.length === 0 || busy !== null || Boolean(provider?.requires_usage_confirmation && !externalUseConfirmed)} aria-label="生成封面">{busy === "generate" ? <RefreshCw className="spin" /> : <ArrowUp />}</button>
            </div>
          </div>
          <div className="quick-prompts cover-prompts">{["做一张小红书知识卡片封面", "按参考图提取配色和留白", "生成高级感人物主题海报", "做重点突出的产品种草海报"].map((item) => <button key={item} onClick={() => setPrompt(item)}>{item}</button>)}</div>

          {activeTask?.status === "partial_success" && <div className="cover-retry"><span>已完成 {String(activeTask.result_summary.generated ?? 0)} 个，失败 {String(activeTask.result_summary.failed ?? 0)} 个；已有结果不会被覆盖。</span><button className="button small" onClick={() => void retryCoverTask()} disabled={busy !== null}><RefreshCw />只重试失败方案</button></div>}

          {revisionTarget && <section className="cover-revision-panel"><div><strong>继续修改方案 {revisionTarget.variant_no}</strong><span>将创建第 {revisionTarget.revision_no + 1} 轮新资产，原方案保持不变；最多 {provider?.max_revisions ?? 2} 轮。</span></div><textarea aria-label="封面修改要求" value={revisionPrompt} onChange={(event) => setRevisionPrompt(event.target.value)} placeholder="例如：标题再醒目一些，背景减少装饰元素" />{provider?.requires_usage_confirmation && <label className="check-row required-check"><input type="checkbox" checked={revisionUseConfirmed} onChange={(event) => setRevisionUseConfirmed(event.target.checked)} /><span><strong>我确认发送封面并接受本次修改费用上限</strong><small>当前封面和原授权素材将发送给外部图片服务，预计不超过 ¥{provider.estimated_cost_cny_max_request.toFixed(2)}。</small></span></label>}<div><button className="button small" onClick={() => { setRevisionTarget(null); setRevisionUseConfirmed(false); }}>取消</button><button className="button small primary" onClick={() => void reviseCover()} disabled={revisionPrompt.trim().length < 5 || busy !== null || Boolean(provider?.requires_usage_confirmation && !revisionUseConfirmed)}>{busy === "revision" ? <RefreshCw className="spin" /> : <Sparkles />}生成修改方案</button></div></section>}

          {(latestCovers.length > 0 || busy === "generate" || busy === "revision") && <section className="cover-result-section" aria-label="封面方案"><header><div><strong>{latestCovers[0]?.revision_no ? `第 ${latestCovers[0].revision_no} 轮修改方案` : "最新生成方案"}</strong><span>{latestCovers.length} / 4 个成功 · 历史资产 {Math.max(0, covers.length - latestCovers.length)} 个</span></div>{busy && <span><RefreshCw className="spin" />任务执行中</span>}</header><div className="cover-results">{latestCovers.map((cover) => <article key={cover.id} className="cover-result"><span className="cover-result-image" role="img" aria-label={`封面方案 ${cover.variant_no}`} style={{ backgroundImage: `url(${resolveApiContentUrl(cover.content_url)})` }} /><header><strong>方案 {cover.variant_no}</strong><span>v{cover.revision_no + 1} · {cover.ratio}</span></header><small>{cover.provider} · {cover.estimated_cost_cny > 0 ? `¥${cover.estimated_cost_cny.toFixed(2)}` : `$${cover.estimated_cost_usd.toFixed(2)}`} · {cover.status === "saved" ? "已保存" : "未保存"}</small><div><button className="button small" disabled={cover.revision_no >= (provider?.max_revisions ?? 2) || busy !== null} onClick={() => { setRevisionTarget(cover); setRevisionPrompt(""); setRevisionUseConfirmed(false); }}>{cover.revision_no >= (provider?.max_revisions ?? 2) ? "已到修改上限" : "继续修改"}</button><button className="button small primary" disabled={cover.status === "saved" || busy !== null} onClick={() => void saveCover(cover)}>{cover.status === "saved" ? <Check /> : <Download />}{cover.status === "saved" ? "已保存" : "保存"}</button></div></article>)}</div></section>}

          {!latestCovers.length && busy !== "loading" && busy !== "generate" && <div className="cover-empty"><ImageIcon /><strong>还没有封面方案</strong><span>添加授权素材、填写设计要求后会生成 4 个 3:4 方案。</span></div>}
        </div>
      )}
    </section>
  );
}
