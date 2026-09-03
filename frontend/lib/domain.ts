export type PageKey =
  | "chat"
  | "collections"
  | "breakdowns"
  | "creations"
  | "covers";

export type ChatMode = "agent" | "capture" | "breakdown" | "creation" | "hot";

export type UnifiedTaskStatus =
  | "idle"
  | "submitting"
  | "queued"
  | "running"
  | "waiting_user"
  | "succeeded"
  | "partially_succeeded"
  | "failed"
  | "cancelled"
  | "disconnected"
  | "stale";

export type TaskKind = "采集" | "拆解" | "创作" | "封面" | "飞书";

export type Task = {
  id: string;
  kind: TaskKind;
  title: string;
  status: UnifiedTaskStatus;
  stage: string;
  progress: number;
  detail: string;
  resultPage?: PageKey;
  retryable?: boolean;
  attempt?: number;
  resultRefs?: Array<{ type: string; id: string }>;
  errorCode?: string;
  source?: "api" | "fixture";
  createdAt: string;
};

export type LibraryRecord = {
  id: string;
  title: string;
  status: string;
  type: string;
  source: string;
  author: string;
  updatedAt: string;
  updatedAtIso?: string;
  publishedAt?: string;
  summary: string;
  tags: string[];
  version: string;
  sourceId: string;
  risk?: string;
  taskId?: string;
  currentVersionId?: string;
  adoptedVersionId?: string;
  sourceKind?: "api" | "fixture";
  libraryType?: "collections" | "breakdowns" | "creations";
  collectionKind?: "single" | "keyword" | "creator_content" | "creator_profile";
  provider?: string;
  externalUrl?: string;
  thumbnailUrl?: string;
  metrics?: Record<string, number>;
  contentType?: string;
  benchmark?: boolean;
  categoryTags?: string[];
  isSandbox?: boolean;
};

export type CollectionKind = NonNullable<LibraryRecord["collectionKind"]>;

export type ModeConfig = {
  label: string;
  placeholder: string;
  tools: Array<"knowledge" | "skills">;
  defaultSkills: string[];
  prompts: string[];
};

export const pagePath: Record<PageKey, string> = {
  chat: "/chat",
  collections: "/collections",
  breakdowns: "/breakdowns",
  creations: "/creations",
  covers: "/covers",
};

export const modeConfig: Record<ChatMode, ModeConfig> = {
  agent: {
    label: "Agent",
    placeholder: "和 Riffloom 智能体聊聊你想完成的内容任务",
    tools: ["knowledge", "skills"],
    defaultSkills: [],
    prompts: ["采集并拆解一篇内容", "写一版小红书文案", "分析爆款逻辑", "转写一段音视频"],
  },
  capture: {
    label: "采集",
    placeholder: "粘贴内容链接、关键词或博主主页，创建采集任务",
    tools: ["skills"],
    defaultSkills: ["采集内容"],
    prompts: ["采集一篇小红书内容", "搜索 AI 工作流", "采集指定博主内容", "更新博主信息"],
  },
  breakdown: {
    label: "拆解",
    placeholder: "选择采集记录，或粘贴链接与正文进行结构化拆解",
    tools: ["knowledge", "skills"],
    defaultSkills: ["爆款拆解"],
    prompts: ["拆解这篇内容的开头钩子", "分析内容结构", "提炼可复用方法", "检查内容风险"],
  },
  creation: {
    label: "创作",
    placeholder: "告诉我选题、目标受众、发布平台和创作要求",
    tools: ["knowledge", "skills"],
    defaultSkills: ["文案原创"],
    prompts: ["写一版小红书图文文案", "参考拆解库仿写", "生成知识口播稿", "优化标题和开头"],
  },
  hot: {
    label: "热点",
    placeholder: "输入行业、账号方向或目标人群，寻找近期内容机会",
    tools: ["skills"],
    defaultSkills: ["爆款选题指导"],
    prompts: ["AI 工具近期选题", "职场新人内容机会", "小红书近期趋势", "把热点转成创作切角"],
  },
};

export const taskStatusMeta: Record<
  UnifiedTaskStatus,
  { label: string; tone: "neutral" | "info" | "warning" | "success" | "danger" }
> = {
  idle: { label: "尚未开始", tone: "neutral" },
  submitting: { label: "正在提交", tone: "info" },
  queued: { label: "已受理", tone: "info" },
  running: { label: "执行中", tone: "info" },
  waiting_user: { label: "等待确认", tone: "warning" },
  succeeded: { label: "已完成", tone: "success" },
  partially_succeeded: { label: "部分完成", tone: "warning" },
  failed: { label: "失败", tone: "danger" },
  cancelled: { label: "已取消", tone: "neutral" },
  disconnected: { label: "连接中断", tone: "warning" },
  stale: { label: "状态待刷新", tone: "warning" },
};

const collectionRecordStatusLabels: Record<string, string> = {
  needs_transcript: "待生成视频文案",
  transcription_queued: "视频文案生成中",
  completed: "已完成",
  pending_review: "待审核",
  adopted: "已采用",
};

const collectionMetricLabels: Record<string, string> = {
  likes: "点赞数",
  collects: "收藏数",
  comments: "评论数",
  shares: "转发数",
  views: "播放量",
  followers: "粉丝数",
  likes_and_collects: "获赞与收藏",
  like_collect_ratio: "赞藏比",
  like_comment_ratio: "赞评比",
};

const collectionProviderLabels: Record<string, string> = {
  "xiaohongshu-web": "小红书网页采集",
  "xiaohongshu-web-v1": "小红书网页采集",
  "opencli/xiaohongshu": "小红书本机采集",
  "opencli-local-v1": "小红书本机采集",
  "user-upload-v1": "用户上传",
  "sandbox-v1": "合规沙箱",
  "deterministic-phase1": "历史确定性数据",
};

export function collectionRecordStatusLabel(value: string) {
  return collectionRecordStatusLabels[value]
    ?? (/^[a-z][a-z0-9_-]*$/i.test(value) ? "状态待确认" : value);
}

export function collectionMetricLabel(value: string) {
  return collectionMetricLabels[value] ?? "其他指标";
}

export function collectionProviderLabel(value: string) {
  return collectionProviderLabels[value] ?? "其他采集服务";
}

export function readableParagraphs(value: string, topics: string[] = []) {
  let text = value;
  for (const topic of [...topics].sort((a, b) => b.length - a.length)) {
    for (const token of [`#${topic}[话题]#`, `#${topic}[话题]`, `#${topic}#`, `#${topic}`]) {
      text = text.replaceAll(token, " ");
    }
  }
  const paragraphs: string[] = [];
  let paragraph = "";
  const sentences = text.replaceAll("\r", "").split(/\n+/).flatMap(
    (block) => block.match(/[^。！？!?]+[。！？!?]?/g) ?? [],
  );
  for (const sentence of sentences) {
    for (const part of sentence.replace(/\s+/g, " ").trim().match(/.{1,140}/g) ?? []) {
      if (paragraph && paragraph.length + part.length > 140) {
        paragraphs.push(paragraph);
        paragraph = "";
      }
      paragraph += part;
    }
  }
  if (paragraph) paragraphs.push(paragraph);
  return paragraphs;
}

export function readableTranscript(
  value: string,
  segments: Array<{ text: string }>,
) {
  if (/[，。！？；：,.!?;:]/.test(value) || segments.length < 2) return value;
  return segments
    .map(({ text }) => /[。！？；!?;]$/.test(text) ? text : `${text}。`)
    .join("");
}

export function canRetryTask(status: UnifiedTaskStatus, retryable = true) {
  return retryable && ["partially_succeeded", "failed", "disconnected", "stale"].includes(status);
}
