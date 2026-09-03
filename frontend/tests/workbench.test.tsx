import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LibraryPage, RiffloomWorkbench } from "@/components/riffloom-workbench";
import { collectionRecords } from "@/lib/fixtures";
import type { ApiFeishuBinding, ApiFeishuProvider } from "@/lib/api";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

describe("Riffloom 工作台", () => {
  beforeEach(() => {
    push.mockReset();
    window.history.replaceState({}, "", "/chat");
  });

  it("导航只响应用户手动折叠和展开", async () => {
    const { container } = render(<RiffloomWorkbench initialPage="chat" />);
    const shell = container.querySelector(".app-shell");
    expect(shell).not.toHaveClass("nav-collapsed");
    expect(screen.getByLabelText("历史对话")).toBeVisible();

    fireEvent.keyDown(screen.getByRole("textbox", { name: "任务描述" }), { key: "A" });
    expect(shell).not.toHaveClass("nav-collapsed");

    fireEvent.click(screen.getByRole("tab", { name: "采集" }));
    expect(shell).not.toHaveClass("nav-collapsed");
    fireEvent.click(screen.getByRole("button", { name: "折叠导航" }));
    expect(shell).toHaveClass("nav-collapsed");
    fireEvent.click(screen.getByRole("button", { name: "展开导航" }));
    expect(shell).not.toHaveClass("nav-collapsed");

    fireEvent.keyDown(screen.getByRole("textbox", { name: "任务描述" }), { key: "A" });
    expect(shell).not.toHaveClass("nav-collapsed");
    fireEvent.click(screen.getByRole("tab", { name: "Agent" }));
    expect(screen.getByLabelText("历史对话")).toBeVisible();
  });

  it("只让历史对话列表滚动，保留新对话和底部账号区", () => {
    const { container } = render(<RiffloomWorkbench initialPage="chat" />);
    const history = screen.getByLabelText("历史对话");
    const conversationList = screen.getByRole("navigation", { name: "对话列表" });
    const newConversation = screen.getByRole("button", { name: "新对话" });
    const profile = container.querySelector(".profile-wrap");

    expect(conversationList).toHaveClass("sidebar-conversation-list");
    expect(conversationList).not.toContainElement(newConversation);
    expect(history).toContainElement(newConversation);
    expect(profile).not.toBeNull();
    expect(history.compareDocumentPosition(profile!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("帮助入口可逐步查看并完成七步新手引导", () => {
    render(<RiffloomWorkbench initialPage="chat" />);
    fireEvent.click(screen.getByRole("button", { name: "帮助" }));

    const titles = [
      "欢迎来到 Riffloom",
      "从对话开始",
      "沉淀采集内容",
      "拆出爆款方法",
      "管理创作版本",
      "生成内容封面",
      "随时查看任务进度",
    ];
    for (const [index, title] of titles.entries()) {
      const dialog = screen.getByRole("dialog", { name: title });
      expect(within(dialog).getByText(`${index + 1} / 7`)).toBeInTheDocument();
      if (index === 1) expect(dialog).toHaveTextContent("在对话框粘贴小红书等内容链接");
      fireEvent.click(within(dialog).getByRole("button", { name: "我知道了" }));
    }
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("空白对话保留欢迎语和 Tab，发送后隐藏并释放消息空间", () => {
    render(<RiffloomWorkbench initialPage="chat" />);
    const intro = screen.getByLabelText("Riffloom 智能体介绍");
    const tabs = screen.getByRole("tablist", { name: "对话功能" });
    expect(intro.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "采集" }));
    expect(screen.getByLabelText("Riffloom 智能体介绍")).toHaveTextContent("你好呀！我是 Riffloom 智能体");
    fireEvent.click(screen.getByRole("tab", { name: "Agent" }));
    fireEvent.change(screen.getByRole("textbox", { name: "任务描述" }), { target: { value: "测试对话空间" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(screen.queryByLabelText("Riffloom 智能体介绍")).not.toBeInTheDocument();
    expect(screen.queryByRole("tablist", { name: "对话功能" })).not.toBeInTheDocument();
    expect(screen.getByText("测试对话空间")).toBeInTheDocument();
  });

  it("选择技能后收起选择框，并把技能提示作为占位文字", () => {
    render(<RiffloomWorkbench initialPage="chat" />);
    fireEvent.click(screen.getByRole("tab", { name: "创作" }));
    expect(screen.getByPlaceholderText("告诉我选题、目标受众、发布平台和创作要求")).toBeInTheDocument();
    expect(screen.getByLabelText("当前调用技能")).toHaveTextContent("文案原创");
    expect(screen.queryByRole("button", { name: "移除技能「文案原创」" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^引用技能/ }));
    const picker = screen.getByLabelText("技能选择");
    expect(picker).toBeInTheDocument();
    fireEvent.click(within(picker).getByRole("button", { name: /文案原创/ }));
    expect(screen.queryByLabelText("技能选择")).not.toBeInTheDocument();
    expect(screen.getByLabelText("当前调用技能")).toHaveTextContent("文案原创");
    expect(screen.getByRole("textbox", { name: "任务描述" })).toHaveValue("");
    expect(screen.getByRole("textbox", { name: "任务描述" })).toHaveAttribute(
      "placeholder",
      "请使用「文案原创」技能，围绕以下选题创作：",
    );
    expect(within(screen.getByLabelText("常用技能")).getByRole("button", { name: /^文案原创/ })).not.toHaveClass("active");
    fireEvent.click(screen.getByRole("button", { name: "移除技能「文案原创」" }));
    expect(screen.getByLabelText("当前调用技能")).toHaveTextContent("文案原创");
    expect(screen.queryByRole("button", { name: "移除技能「文案原创」" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^引用技能/ }));
    fireEvent.click(within(screen.getByLabelText("技能选择")).getByRole("button", { name: /文案原创/ }));
    fireEvent.keyDown(screen.getByRole("textbox", { name: "任务描述" }), { key: "Backspace" });
    expect(screen.getByLabelText("当前调用技能")).toHaveTextContent("文案原创");
    expect(screen.queryByRole("button", { name: "移除技能「文案原创」" })).not.toBeInTheDocument();
  });

  it("常用技能支持多选并在输入框保留多个标签", () => {
    render(<RiffloomWorkbench initialPage="chat" />);
    const frequentSkills = screen.getByLabelText("常用技能");
    fireEvent.click(within(frequentSkills).getByRole("button", { name: /^爆款拆解/ }));
    fireEvent.click(within(frequentSkills).getByRole("button", { name: /^文案仿写/ }));

    const selectedSkills = screen.getByLabelText("当前调用技能");
    expect(within(selectedSkills).getByText("爆款拆解")).toBeInTheDocument();
    expect(within(selectedSkills).getByText("文案仿写")).toBeInTheDocument();
  });

  it("四个任务 Tab 显示默认技能，技能选择器提供六项可用技能", () => {
    render(<RiffloomWorkbench initialPage="chat" />);
    for (const [tab, skill] of [["采集", "采集内容"], ["拆解", "爆款拆解"], ["创作", "文案原创"], ["热点", "爆款选题指导"]]) {
      fireEvent.click(screen.getByRole("tab", { name: tab }));
      expect(screen.getByLabelText("当前调用技能")).toHaveTextContent(skill);
      expect(screen.getByRole("button", { name: "引用技能" })).toBeInTheDocument();
      expect(screen.queryByText(`默认 ${skill}`)).not.toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("button", { name: /^引用技能/ }));
    const picker = screen.getByLabelText("技能选择");
    for (const skill of ["爆款拆解", "爆款选题指导", "采集内容", "文案仿写", "一键采集仿写", "文案原创"]) {
      expect(within(picker).getByRole("button", { name: new RegExp(`^${skill}`) })).toBeInTheDocument();
    }
  });

  it("调用一键采集仿写后写入可编辑提示并受理任务", () => {
    render(<RiffloomWorkbench initialPage="chat" />);
    fireEvent.click(screen.getByRole("button", { name: /一键采集仿写/ }));
    const input = screen.getByRole("textbox", { name: "任务描述" });
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("placeholder", "请使用「一键采集仿写」技能，处理以下内容：");
    fireEvent.change(input, { target: { value: "https://example.com/demo 请生成职场新人版本" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(screen.getByText(/任务已受理。将依次完成采集/)).toBeInTheDocument();
    expect(window.location.search).toContain("task=T-");
  });

  it("从知识库目录选中记录后收起，并高亮引用状态", () => {
    render(<RiffloomWorkbench initialPage="chat" />);
    expect(screen.queryByRole("button", { name: "提示词" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "快速" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加图片或文档" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^引用知识/ }));
    const picker = screen.getByLabelText("引用知识库");
    fireEvent.click(within(picker).getByRole("button", { name: /采集库/ }));
    const record = within(picker).getAllByRole("button").find((button) =>
      button.textContent?.includes("从小白到 AI 万粉博主"),
    );
    expect(record).toBeDefined();
    fireEvent.click(record!);

    expect(screen.queryByLabelText("引用知识库")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /引用知识 · 1/ })).toHaveClass("on");
  });

  it("展示飞书单向同步边界和系统字段预检", () => {
    render(<RiffloomWorkbench initialPage="breakdowns" />);
    expect(document.querySelectorAll(".sync-logo")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /配置绑定|字段映射/ }));
    expect(screen.getByRole("dialog", { name: "同步到飞书多维表格" })).toBeInTheDocument();
    expect(document.querySelectorAll(".sync-logo")).toHaveLength(2);
    expect(screen.getByText(/飞书编辑不会反向覆盖 Riffloom/)).toBeInTheDocument();
    expect(screen.getByText(/riffloom_record_id/)).toBeInTheDocument();
  });

  it("创作库可查看记录并复制当前正文", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<RiffloomWorkbench initialPage="creations" />);

    expect(screen.getByRole("heading", { name: "创作库" })).toBeInTheDocument();
    fireEvent.click(screen.getAllByText("别再收藏 AI 工具了，先搭好你的创作工作流")[0]);
    fireEvent.click(screen.getByRole("button", { name: "复制正文" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining("流程没有连起来")));
    expect(screen.getByRole("status")).toHaveTextContent("正文已复制");
  });

  it("封面页恢复真实工作区入口", () => {
    render(<RiffloomWorkbench initialPage="covers" />);
    expect(screen.getByRole("heading", { name: "封面设计" })).toBeInTheDocument();
    expect(screen.queryByText("开发中，敬请期待...")).not.toBeInTheDocument();
  });

  it("采集库支持筛选、全选当前结果和批量工具栏", () => {
    render(<RiffloomWorkbench initialPage="collections" />);
    expect(screen.getByRole("button", { name: "关键词采集库" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "内容类型筛选" }), {
      target: { value: "图文" },
    });
    expect(screen.getByText(/从小白到 AI 万粉博主，敢想比敢做更重要/)).toBeInTheDocument();
    expect(screen.queryByText("用 AI 发掘财富机会，思路值得借鉴")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "全选当前结果" }));
    expect(screen.getByRole("toolbar", { name: "采集库批量操作" })).toHaveTextContent("已选 2 条");
    expect(screen.getByRole("button", { name: "批量拆解" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批量仿写" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批量删除" })).toBeInTheDocument();
  });

  it("真实飞书写入只提交用户勾选的一条记录", () => {
    const onSyncFeishu = vi.fn();
    const provider: ApiFeishuProvider = {
      provider: "openapi-v1",
      mode: "production",
      is_sandbox: false,
      external_calls: true,
      writes_enabled: true,
      credential_storage: "env_reference_and_memory_token",
      auth_mode: "shared_application",
      scopes: ["collection.single"],
      batch_size: 1,
      supports_full: true,
      supports_incremental: true,
      supports_retry_failed: true,
      can_configure: true,
    };
    const binding: ApiFeishuBinding = {
      id: "fbind_demo",
      connection_id: "fconn_demo",
      scope_key: "collection.single",
      target_base_id: "base_demo",
      target_table_id: "table_demo",
      target_table_name: "单篇采集测试",
      field_mapping: {},
      strategy: "manual_incremental",
      cursor: {},
      status: "active",
      last_synced_at: null,
      link_count: 0,
      last_run: null,
      can_configure: true,
      can_sync: true,
      provider: "openapi-v1",
      is_sandbox: false,
      external_calls: true,
      target_openable: true,
    };

    render(<LibraryPage
      title="采集库"
      description="测试"
      records={collectionRecords.slice(0, 2)}
      query=""
      onQueryChange={vi.fn()}
      onOpenRecord={vi.fn()}
      feishuScope="collection.single"
      binding={binding}
      provider={provider}
      canConfigureFeishu
      onConfigureFeishu={vi.fn()}
      onSyncFeishu={onSyncFeishu}
      onPauseFeishu={vi.fn()}
      onCancelTask={vi.fn()}
      primaryAction="新建采集"
      onPrimaryAction={vi.fn()}
      dataLabel="真实采集"
      onBatchAction={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("checkbox", { name: `选择 ${collectionRecords[0].title}` }));
    fireEvent.click(screen.getByRole("button", { name: "同步所选 1 条到飞书" }));
    expect(onSyncFeishu).toHaveBeenCalledWith(collectionRecords[0].id);
  });

  it("未开通飞书的独立资料库不展示无效配置入口", () => {
    render(<LibraryPage
      title="采集库"
      description="测试"
      records={collectionRecords.slice(0, 1)}
      query=""
      onQueryChange={vi.fn()}
      onOpenRecord={vi.fn()}
      feishuScope="collection.single"
      provider={null}
      canConfigureFeishu={false}
      onConfigureFeishu={vi.fn()}
      onSyncFeishu={vi.fn()}
      onPauseFeishu={vi.fn()}
      onCancelTask={vi.fn()}
      primaryAction="新建采集"
      onPrimaryAction={vi.fn()}
      dataLabel="真实采集"
    />);

    expect(screen.queryByRole("button", { name: "首次配置飞书" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "配置绑定" })).not.toBeInTheDocument();
    expect(screen.getByText(/当前独立资料库未开通/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "未开通" })).toBeDisabled();
  });

  it("资料库可隐藏非关键列", () => {
    render(<RiffloomWorkbench initialPage="collections" />);
    fireEvent.click(screen.getByText("显示列"));
    fireEvent.click(screen.getByRole("checkbox", { name: "显示类型列" }));
    expect(screen.queryByRole("columnheader", { name: "类型" })).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "标题" })).toBeInTheDocument();
  });

  it("任务中心可以取消执行中的任务", () => {
    render(<RiffloomWorkbench initialPage="chat" />);
    fireEvent.click(screen.getByRole("button", { name: /任务中心/ }));
    const cancel = screen.getAllByRole("button", { name: "取消任务" })[0];
    fireEvent.click(cancel);
    expect(screen.getByText("已取消")).toBeInTheDocument();
  });
});
