"use client";

import { Check, Link2, X } from "lucide-react";
import { useState } from "react";
import { useDialogLifecycle } from "@/components/use-dialog-lifecycle";

export type CollectRewriteDraft = {
  url?: string;
  collectionId?: string;
  prompt: string;
  usageConfirmed: boolean;
  targetPlatform: string;
  audience: string;
};

export function CollectRewriteTaskModal({
  source,
  initialPrompt,
  submitting,
  onClose,
  onSubmit,
}: {
  source: { url?: string; collectionId?: string };
  initialPrompt: string;
  submitting: boolean;
  onClose: () => void;
  onSubmit: (draft: CollectRewriteDraft) => void;
}) {
  const [url, setUrl] = useState(source.url ?? "");
  const [prompt, setPrompt] = useState(initialPrompt);
  const [audience, setAudience] = useState("");
  const [usageConfirmed, setUsageConfirmed] = useState(false);
  const needsRights = !source.collectionId;
  const dialogRef = useDialogLifecycle(onClose);

  return (
    <div className="overlay modal-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialogRef} className="modal collection-modal" role="dialog" aria-modal="true" aria-labelledby="collect-rewrite-modal-title">
        <header>
          <div><h2 id="collect-rewrite-modal-title">一键采集仿写</h2><p>真实采集、结构化拆解和仿写按检查点依次执行，重试不会重复采集。</p></div>
          <button className="icon-button" onClick={onClose} aria-label="关闭一键采集仿写"><X /></button>
        </header>
        <div className="provider-notice"><span className="provider-icon"><Link2 /></span><div><strong>{source.collectionId ? "复用已有采集记录" : "采集新的获权链接"}</strong><span>{source.collectionId ?? "签名参数只用于当前采集，不写入任务快照。"}</span></div></div>
        {needsRights && <label className="collection-input-label"><span>获权内容链接</span><input aria-label="一键采集仿写链接" value={url} onChange={(event) => setUrl(event.target.value)} /></label>}
        <label className="collection-input-label"><span>创作要求</span><textarea aria-label="一键仿写创作要求" rows={4} value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
        <label className="collection-input-label"><span>目标受众（可选）</span><input aria-label="目标受众" value={audience} onChange={(event) => setAudience(event.target.value)} /></label>
        {needsRights && <label className="check-row required-check"><input type="checkbox" checked={usageConfirmed} onChange={(event) => setUsageConfirmed(event.target.checked)} /><span><strong>我确认有权使用该链接进行采集和创作</strong><small>不提交 Cookie、密码、验证码或未获权个人信息。</small></span></label>}
        <footer>
          <button className="button" onClick={onClose}>取消</button>
          <button className="button primary" disabled={submitting || !prompt.trim() || (needsRights && (!url.trim() || !usageConfirmed))} onClick={() => onSubmit({ url: needsRights ? url.trim() : undefined, collectionId: source.collectionId, prompt: prompt.trim(), usageConfirmed, targetPlatform: "小红书", audience: audience.trim() })}>{submitting ? "正在受理…" : <><Check />确认并创建任务</>}</button>
        </footer>
      </section>
    </div>
  );
}
