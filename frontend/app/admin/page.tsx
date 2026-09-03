"use client";

import Link from "next/link";
import { ArrowLeft, Check, Copy, Plus, RefreshCw, ShieldCheck, UserRoundPlus } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import {
  ApiError,
  createWorkspaceMember,
  getPilotMetrics,
  getSession,
  issueWorkspaceMemberInvitation,
  listWorkspaceMembers,
  updateWorkspaceMember,
  type ApiSession,
  type ApiPilotMetrics,
  type ApiWorkspaceMember,
  type ApiWorkspaceMemberInvitation,
} from "@/lib/api";

const roleLabels: Record<ApiWorkspaceMember["role"], string> = {
  editor: "内容成员",
  lead: "内容负责人",
  admin: "管理员",
};
type TestMemberRole = Exclude<ApiWorkspaceMember["role"], "admin">;

export default function AdminPage() {
  const [session, setSession] = useState<ApiSession | null>(null);
  const [members, setMembers] = useState<ApiWorkspaceMember[]>([]);
  const [metrics, setMetrics] = useState<ApiPilotMetrics | null>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState<TestMemberRole>("editor");
  const [expiresInHours, setExpiresInHours] = useState(168);
  const [issued, setIssued] = useState<ApiWorkspaceMemberInvitation | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getSession(), listWorkspaceMembers(), getPilotMetrics()])
      .then(([currentSession, result, pilotMetrics]) => {
        if (cancelled) return;
        setSession(currentSession);
        setMembers(result.items);
        setMetrics(pilotMetrics);
      })
      .catch((cause) => {
        if (!cancelled) setError(apiMessage(cause));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  function replaceMember(member: ApiWorkspaceMember) {
    setMembers((current) => current.map((item) =>
      item.membership_id === member.membership_id ? member : item,
    ));
  }

  async function addMember(event: FormEvent) {
    event.preventDefault();
    setBusy("create");
    setError("");
    try {
      const result = await createWorkspaceMember({ userName: name, role, expiresInHours });
      setMembers((current) => [...current, result.member]);
      setIssued(result);
      setCopied(false);
      setName("");
      setRole("editor");
    } catch (cause) {
      setError(apiMessage(cause));
    } finally {
      setBusy("");
    }
  }

  async function changeMember(
    member: ApiWorkspaceMember,
    change: Partial<Pick<ApiWorkspaceMember, "role" | "status">>,
  ) {
    setBusy(member.membership_id);
    setError("");
    try {
      replaceMember(await updateWorkspaceMember(member.membership_id, change));
    } catch (cause) {
      setError(apiMessage(cause));
    } finally {
      setBusy("");
    }
  }

  async function reissue(member: ApiWorkspaceMember) {
    const isOwnAdmin = member.user_id === session?.user_id && member.role === "admin";
    const warning = isOwnAdmin
      ? "新登录密钥会立即撤销旧密钥；当前登录不受影响。新密钥在 30 天内可重复使用，是否继续？"
      : member.is_isolated
      ? "新邀请码会立即撤销该成员之前未使用的邀请码，是否继续？"
      : "该成员会移入独立的空资料库，旧登录会立即失效；原有数据不会删除。是否继续？";
    if (!window.confirm(warning)) return;
    setBusy(member.membership_id);
    setError("");
    try {
      const result = isOwnAdmin
        ? await issueWorkspaceMemberInvitation(member.membership_id, 720)
        : await issueWorkspaceMemberInvitation(member.membership_id);
      replaceMember(result.member);
      setIssued(result);
      setCopied(false);
    } catch (cause) {
      setError(apiMessage(cause));
    } finally {
      setBusy("");
    }
  }

  async function copyCode() {
    if (!issued) return;
    await navigator.clipboard.writeText(issued.invitation_code);
    setCopied(true);
  }

  if (loading) return <AdminState icon={<RefreshCw className="spin" />} title="正在加载管理后台" />;
  if (!session) {
    return <AdminState icon={<ShieldCheck />} title="管理后台暂时无法加载" detail={error || "请返回工作台重新登录。"} />;
  }
  if (session.role !== "admin") {
    return <AdminState icon={<ShieldCheck />} title="仅管理员可以访问" detail="请使用管理员账号登录后再试。" />;
  }

  const activeCount = members.filter((member) => member.status === "active").length;
  const issuedIsAdminKey = issued?.member.user_id === session.user_id && issued.member.role === "admin";
  return (
    <main className="admin-shell">
      <header className="admin-topbar">
        <Link href="/chat" className="button"><ArrowLeft />返回工作台</Link>
        <span>{session.workspace_name} · {session.user_name}</span>
      </header>

      <section className="admin-page">
        <header className="admin-heading">
          <div><span>ADMIN</span><h1>管理后台</h1><p>每位测试成员使用独立资料库，成员之间互不可见</p></div>
          <div className="admin-stats"><strong>{activeCount}</strong><span>启用成员 / 共 {members.length} 人</span></div>
        </header>

        {error && <div className="admin-alert" role="alert">{error}</div>}
        {issued && (
          <section className="invitation-result" aria-label={issuedIsAdminKey ? "管理员登录密钥" : "新邀请码"}>
            <div><Check /><span><strong>{issuedIsAdminKey ? "管理员登录密钥" : `${issued.member.user_name} 的邀请码`}</strong><small>{issuedIsAdminKey ? "请保存到密码管理器，30 天内可重复使用" : "明文只显示这一次，请通过私密渠道发送"}；有效期至 {formatDate(issued.expires_at)}</small></span></div>
            <code>{issued.invitation_code}</code>
            <div><button className="button primary" onClick={() => { void copyCode(); }}><Copy />{copied ? "已复制" : issuedIsAdminKey ? "复制登录密钥" : "复制邀请码"}</button><button className="button" onClick={() => setIssued(null)}>我已保存</button></div>
          </section>
        )}

        {metrics && (
          <section className="admin-card pilot-metrics-card" aria-label="Pilot 指标">
            <header><div><span><strong>Pilot 指标</strong><small>主空间与所有独立测试空间的累计数据</small></span></div></header>
            <div className="pilot-metrics-grid">
              <article><span>核心任务完成率</span><strong>{(metrics.completion_rate * 100).toFixed(1)}%</strong><small>{metrics.completed_tasks} / {metrics.accepted_tasks} 完成 · {metrics.failed_tasks} 失败</small></article>
              <article><span>中位交付时长</span><strong>{metrics.median_delivery_minutes == null ? "—" : `${metrics.median_delivery_minutes} 分钟`}</strong><small>创建任务到首次确认采用</small></article>
              <article><span>确认采用稿件</span><strong>{metrics.adopted_creations} 篇</strong><small>同一创作记录只计一次</small></article>
              <article><span>AI 首稿直接采纳率</span><strong>{(metrics.first_version_adoption_rate * 100).toFixed(1)}%</strong><small>{metrics.first_version_adoptions} / {metrics.adopted_creations} 为首版</small></article>
              <article><span>估算调用成本</span><strong>${metrics.estimated_cost_usd.toFixed(4)}</strong><small>${metrics.cost_per_completed_task_usd.toFixed(4)} / 完成任务</small></article>
            </div>
          </section>
        )}

        <section className="admin-card add-member-card">
          <header><div><UserRoundPlus /><span><strong>添加测试成员</strong><small>创建独立空资料库，并生成一次性登录邀请码</small></span></div></header>
          <form onSubmit={addMember}>
            <label><span>成员名称</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：测试成员 05" maxLength={120} required /></label>
            <label><span>角色</span><select value={role} onChange={(event) => setRole(event.target.value as TestMemberRole)}><option value="editor">内容成员</option><option value="lead">内容负责人</option></select></label>
            <label><span>邀请码有效期</span><select value={expiresInHours} onChange={(event) => setExpiresInHours(Number(event.target.value))}><option value={24}>1 天</option><option value={168}>7 天</option><option value={720}>30 天</option></select></label>
            <button className="button primary" type="submit" disabled={busy === "create" || !name.trim()}>{busy === "create" ? <RefreshCw className="spin" /> : <Plus />}{busy === "create" ? "正在创建" : "添加并生成邀请码"}</button>
          </form>
        </section>

        <section className="admin-card member-card">
          <header><div><ShieldCheck /><span><strong>成员与权限</strong><small>停用账号会立即撤销其登录会话和未使用邀请码</small></span></div></header>
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead><tr><th>成员</th><th>状态</th><th>角色</th><th>加入时间</th><th>操作</th></tr></thead>
              <tbody>{members.map((member) => {
                const isSelf = member.user_id === session.user_id;
                const memberBusy = busy === member.membership_id;
                return <tr key={member.membership_id}>
                  <td><strong>{member.user_name}</strong><small>{isSelf ? "当前账号" : member.is_isolated ? "独立资料库" : "共享资料库（待迁移）"}</small></td>
                  <td><span className={`member-status ${member.status}`}>{member.status === "active" ? "已启用" : "已停用"}</span></td>
                  <td><select aria-label={`调整 ${member.user_name} 的角色`} value={member.role} disabled={memberBusy || isSelf || member.status === "disabled"} onChange={(event) => { void changeMember(member, { role: event.target.value as ApiWorkspaceMember["role"] }); }}>{Object.entries(roleLabels).filter(([value]) => !member.is_isolated || value !== "admin").map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></td>
                  <td>{formatDate(member.created_at)}</td>
                  <td><div className="member-actions">{member.status === "active" && <button className="button small" disabled={memberBusy} onClick={() => { void reissue(member); }}>{isSelf ? "生成登录密钥" : member.is_isolated ? "生成新邀请码" : "迁移并生成邀请码"}</button>}<button className="button small" disabled={memberBusy || isSelf} onClick={() => { void changeMember(member, { status: member.status === "active" ? "disabled" : "active" }); }}>{memberBusy ? <RefreshCw className="spin" /> : null}{member.status === "active" ? "停用" : "启用"}</button></div></td>
                </tr>;
              })}</tbody>
            </table>
          </div>
        </section>
      </section>
    </main>
  );
}

function AdminState({ icon, title, detail }: { icon: ReactNode; title: string; detail?: string }) {
  return <main className="admin-state">{icon}<h1>{title}</h1>{detail && <p>{detail}</p>}<Link href="/chat" className="button primary">返回工作台</Link></main>;
}

function apiMessage(cause: unknown) {
  return cause instanceof ApiError ? cause.message : "管理操作失败，请稍后重试";
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
