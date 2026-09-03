import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminPage from "@/app/admin/page";
import {
  createWorkspaceMember,
  getPilotMetrics,
  getSession,
  issueWorkspaceMemberInvitation,
  listWorkspaceMembers,
  updateWorkspaceMember,
} from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  createWorkspaceMember: vi.fn(),
  getPilotMetrics: vi.fn(),
  getSession: vi.fn(),
  issueWorkspaceMemberInvitation: vi.fn(),
  listWorkspaceMembers: vi.fn(),
  updateWorkspaceMember: vi.fn(),
}));

const member = {
  membership_id: "mem_editor",
  user_id: "user_editor",
  user_name: "测试内容成员",
  role: "editor" as const,
  status: "active" as const,
  is_isolated: true,
  created_at: "2026-08-30T08:00:00Z",
  updated_at: "2026-08-30T08:00:00Z",
};

const admin = {
  ...member,
  membership_id: "mem_admin",
  user_id: "user_admin",
  user_name: "管理员",
  role: "admin" as const,
  is_isolated: false,
};

describe("管理后台", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getSession).mockResolvedValue({
      user_id: "user_admin",
      user_name: "管理员",
      workspace_id: "ws_demo",
      workspace_name: "测试工作区",
      role: "admin",
      auth_mode: "invite_token",
      onboarding_completed: true,
    });
    vi.mocked(listWorkspaceMembers).mockResolvedValue({ items: [member], total: 1 });
    vi.mocked(getPilotMetrics).mockResolvedValue({
      accepted_tasks: 10,
      completed_tasks: 8,
      failed_tasks: 2,
      completion_rate: 0.8,
      adopted_creations: 5,
      first_version_adoptions: 3,
      first_version_adoption_rate: 0.6,
      median_delivery_minutes: 42.5,
      estimated_cost_usd: 0.18,
      cost_per_completed_task_usd: 0.0225,
    });
    vi.mocked(updateWorkspaceMember).mockResolvedValue({ ...member, role: "lead" });
    vi.mocked(createWorkspaceMember).mockResolvedValue({
      member: { ...member, membership_id: "mem_new", user_id: "user_new", user_name: "第五位成员" },
      invitation_id: "invite_new",
      invitation_code: "rfi_one_time_code_for_member_05",
      expires_at: "2026-09-06T08:00:00Z",
    });
  });

  it("允许管理员调整角色并添加带一次性邀请码的成员", async () => {
    render(<AdminPage />);

    expect(await screen.findByRole("heading", { name: "管理后台" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("调整 测试内容成员 的角色"), { target: { value: "lead" } });
    await waitFor(() => expect(updateWorkspaceMember).toHaveBeenCalledWith("mem_editor", { role: "lead" }));

    fireEvent.change(screen.getByPlaceholderText("例如：测试成员 05"), { target: { value: "第五位成员" } });
    fireEvent.click(screen.getByRole("button", { name: "添加并生成邀请码" }));

    expect(await screen.findByText("rfi_one_time_code_for_member_05")).toBeInTheDocument();
    expect(createWorkspaceMember).toHaveBeenCalledWith({
      userName: "第五位成员",
      role: "editor",
      expiresInHours: 168,
    });
  });

  it("展示所有测试空间汇总的 Pilot 指标", async () => {
    render(<AdminPage />);

    expect(await screen.findByText("Pilot 指标")).toBeInTheDocument();
    expect(screen.getByText("80.0%")).toBeInTheDocument();
    expect(screen.getByText("42.5 分钟")).toBeInTheDocument();
    expect(screen.getByText("5 篇")).toBeInTheDocument();
    expect(screen.getByText("60.0%")).toBeInTheDocument();
    expect(screen.getByText("$0.1800")).toBeInTheDocument();
  });

  it("允许管理员为自己生成可重复使用的登录密钥", async () => {
    vi.mocked(listWorkspaceMembers).mockResolvedValue({ items: [admin, member], total: 2 });
    vi.mocked(issueWorkspaceMemberInvitation).mockResolvedValue({
      member: admin,
      invitation_id: "invite_admin_access",
      invitation_code: "rfi_reusable_admin_access_key_0001",
      expires_at: "2026-10-02T08:00:00Z",
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminPage />);
    fireEvent.click(await screen.findByRole("button", { name: "生成登录密钥" }));

    expect(issueWorkspaceMemberInvitation).toHaveBeenCalledWith("mem_admin", 720);
    expect(await screen.findByText("管理员登录密钥")).toBeInTheDocument();
    expect(screen.getByText(/30 天内可重复使用/)).toBeInTheDocument();
  });

  it("迁移共享成员后显示新邀请码和独立资料库", async () => {
    const legacyMember = { ...member, is_isolated: false };
    vi.mocked(listWorkspaceMembers).mockResolvedValue({ items: [legacyMember], total: 1 });
    vi.mocked(issueWorkspaceMemberInvitation).mockResolvedValue({
      member,
      invitation_id: "invite_migrated",
      invitation_code: "rfi_migrated_member_code_0001",
      expires_at: "2026-09-06T08:00:00Z",
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminPage />);
    fireEvent.click(await screen.findByRole("button", { name: "迁移并生成邀请码" }));

    expect(issueWorkspaceMemberInvitation).toHaveBeenCalledWith("mem_editor");
    expect(await screen.findByText("rfi_migrated_member_code_0001")).toBeInTheDocument();
    expect(screen.getByText("独立资料库")).toBeInTheDocument();
  });
});
