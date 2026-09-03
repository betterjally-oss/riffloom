"use client";

import { Check, Database, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import type { ApiCollectionProvider } from "@/lib/api";
import type { CollectionKind } from "@/lib/domain";
import { useDialogLifecycle } from "@/components/use-dialog-lifecycle";


const kindOptions: Array<{ key: CollectionKind; label: string; description: string }> = [
  { key: "single", label: "单篇内容", description: "采集一条完整内容记录" },
  { key: "keyword", label: "关键词搜索", description: "返回最多 30 条轻量结果" },
  { key: "creator_content", label: "博主内容", description: "采集指定博主的内容列表" },
  { key: "creator_profile", label: "博主信息", description: "采集一条博主资料" },
];

const fieldMeta: Record<CollectionKind, { label: string; placeholder: string }> = {
  single: { label: "获权内容链接", placeholder: "粘贴小红书完整分享链接（需包含 xsec_token）" },
  keyword: { label: "搜索关键词", placeholder: "例如：AI 工作流" },
  creator_content: { label: "博主主页或 ID", placeholder: "博主主页 URL 或 24 位用户 ID" },
  creator_profile: { label: "博主主页或 ID", placeholder: "博主主页 URL 或 24 位用户 ID" },
};

function isXiaohongshuLink(value: string) {
  try {
    const hostname = new URL(value).hostname;
    return hostname === "xiaohongshu.com" || hostname.endsWith(".xiaohongshu.com");
  } catch {
    return false;
  }
}

export type CollectionTaskDraft = {
  kind: CollectionKind;
  platform: string;
  value: string;
  usageConfirmed: boolean;
  refresh: boolean;
  limit: number;
  contentType: "image" | "video";
  publishTime: "day" | "week" | "month" | "half-year" | "anytime";
};

export function inferKeywordContentType(value: string): "image" | "video" {
  return /视频|短视频|口播|vlog/i.test(value) ? "video" : "image";
}

export function CollectionTaskModal({
  provider,
  initialValue,
  submitting,
  onClose,
  onSubmit,
}: {
  provider: ApiCollectionProvider;
  initialValue?: string;
  submitting: boolean;
  onClose: () => void;
  onSubmit: (draft: CollectionTaskDraft) => void;
}) {
  const initialKind = provider.kinds.includes("single") ? "single" : provider.kinds[0];
  const [kind, setKind] = useState<CollectionKind>(initialKind);
  const [value, setValue] = useState(initialValue ?? provider.sample_inputs[initialKind]);
  const [usageConfirmed, setUsageConfirmed] = useState(false);
  const [refresh, setRefresh] = useState(false);
  const [publishTime, setPublishTime] = useState<CollectionTaskDraft["publishTime"]>("week");
  const maxSelectable = Math.max(1, provider.max_items);
  const [limit, setLimit] = useState(Math.min(20, maxSelectable));
  const availableKinds = kindOptions.filter((option) => provider.kinds.includes(option.key));
  const keywordContentType = inferKeywordContentType(value);
  const uploadFallback = provider.provider === "sandbox-v1" && kind === "single" && isXiaohongshuLink(value.trim());
  const providerLabel = provider.is_sandbox ? "合规沙箱" : provider.mode === "experimental_local_helper" ? "本机真实采集" : "线上真实采集";
  const providerDescription = provider.is_sandbox
    ? "示例链接走沙箱；小红书链接只建立待转写记录，不读取平台页面。"
    : provider.mode === "production_third_party"
      ? "单篇链接走获权网页采集；关键词、博主近期内容和博主资料走 TikHub，不接收 Cookie、密码或验证码。"
      : provider.mode === "production"
        ? "自动读取获权分享链接、下载媒体并生成视频文案；不接收 Cookie、密码或验证码。"
        : "读取本机已登录浏览器的可见页面，只读、不上传 Cookie；视频必须有文案才能进入拆解和仿写。";
  const dialogRef = useDialogLifecycle(onClose);

  function chooseKind(next: CollectionKind) {
    setKind(next);
    setValue(initialValue || provider.sample_inputs[next]);
  }

  return (
    <div className="overlay modal-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialogRef} className="modal collection-modal" role="dialog" aria-modal="true" aria-labelledby="collection-modal-title">
        <header>
          <div>
            <h2 id="collection-modal-title">新建采集</h2>
            <p>当前 Provider 支持 {provider.kinds.length} 类采集，共用持久化状态、逐项去重和失败重试。</p>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="关闭新建采集"><X /></button>
        </header>

        <div className="provider-notice" aria-label="当前采集数据源">
          <span className="provider-icon"><ShieldCheck /></span>
          <div>
            <strong>{providerLabel} · {provider.provider}</strong>
            <span>{providerDescription}</span>
          </div>
          <span className="sandbox-badge">{provider.is_sandbox ? "SANDBOX" : provider.mode === "experimental_local_helper" ? "LOCAL" : "LIVE"}</span>
        </div>

        <div className="collection-kind-grid" role="tablist" aria-label="采集类型">
          {availableKinds.map((option) => (
            <button key={option.key} role="tab" aria-selected={kind === option.key} className={kind === option.key ? "active" : ""} onClick={() => chooseKind(option.key)}>
              <Database /><span><strong>{option.label}</strong><small>{option.description}</small></span>
            </button>
          ))}
        </div>

        <label className="collection-input-label">
          <span>{fieldMeta[kind].label}</span>
          <input value={value} onChange={(event) => setValue(event.target.value)} placeholder={provider.sample_inputs[kind] || fieldMeta[kind].placeholder} aria-label={fieldMeta[kind].label} />
        </label>

        {(kind === "keyword" || kind === "creator_content") && (
          <label className="collection-input-label">
            <span>{kind === "creator_content" ? "近期采集条数" : "采集条数"}</span>
            <input type="number" min={1} max={maxSelectable} value={limit} onChange={(event) => setLimit(Math.min(maxSelectable, Math.max(1, Number(event.target.value) || 1)))} aria-label={kind === "creator_content" ? "近期采集条数" : "采集条数"} />
          </label>
        )}

        {provider.sample_inputs[kind] && <div className="sandbox-sample-row">
          <span>可验收样本</span>
          <button type="button" className="text-link" onClick={() => setValue(provider.sample_inputs[kind])}>填入推荐样本</button>
          {(kind === "keyword" || kind === "creator_content") && <span>最多返回 {maxSelectable} 条</span>}
        </div>}

        {uploadFallback && <p className="detail-muted">将去掉链接查询参数；创建后请在采集库打开记录，上传已获权媒体完成转写。</p>}

        {kind === "keyword" && (
          <>
            <label className="collection-input-label">
              <span>最热时间范围</span>
              <select aria-label="最热时间范围" value={publishTime} onChange={(event) => setPublishTime(event.target.value as CollectionTaskDraft["publishTime"])}>
                <option value="day">当天最热</option>
                <option value="week">一周最热</option>
                <option value="month">一个月最热（近 30 天二次筛选）</option>
                <option value="half-year">半年最热</option>
                <option value="anytime">全部时间最热</option>
              </select>
            </label>
            <p className="detail-muted" role="status">内容类型：已根据关键词自动识别为{keywordContentType === "video" ? "视频笔记" : "图文笔记"}</p>
          </>
        )}

        <label className="check-row">
          <input type="checkbox" checked={refresh} onChange={(event) => setRefresh(event.target.checked)} />
          <span><strong>刷新可更新字段</strong><small>重复记录默认复用；勾选后只更新互动数据、媒体引用和采集时间。</small></span>
        </label>
        <label className="check-row required-check">
          <input type="checkbox" checked={usageConfirmed} onChange={(event) => setUsageConfirmed(event.target.checked)} />
          <span><strong>我确认有权使用该输入进行采集</strong><small>不得提交账号密码、Cookie、验证码或未获权个人信息。</small></span>
        </label>

        <footer>
          <button className="button" onClick={onClose}>取消</button>
          <button
            className="button primary"
            disabled={!value.trim() || !usageConfirmed || submitting}
            onClick={() => onSubmit({
              kind,
              platform: uploadFallback ? "xiaohongshu" : provider.platforms[0] ?? "riffloom-sandbox",
              value: value.trim(),
              usageConfirmed,
              refresh,
              limit: kind === "single" || kind === "creator_profile" ? 1 : limit,
              contentType: keywordContentType,
              publishTime,
            })}
          >
            {submitting ? "正在受理…" : <><Check />{uploadFallback ? "创建待转写记录" : "创建采集任务"}</>}
          </button>
        </footer>
      </section>
    </div>
  );
}
