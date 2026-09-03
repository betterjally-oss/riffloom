import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { CollectionTaskModal } from "@/components/collection-task-modal";

it("小红书链接创建待上传转写记录", () => {
  const onSubmit = vi.fn();
  render(
    <CollectionTaskModal
      provider={{
        provider: "sandbox-v1",
        mode: "compliance_sandbox",
        is_sandbox: true,
        platforms: ["riffloom-sandbox", "xiaohongshu"],
        kinds: ["single"],
        max_items: 20,
        sample_inputs: {
          single: "https://sandbox.riffloom.local/notes/note-001",
          keyword: "AI 工作流",
          creator_content: "creator-001",
          creator_profile: "creator-001",
        },
      }}
      submitting={false}
      onClose={() => undefined}
      onSubmit={onSubmit}
    />,
  );

  fireEvent.change(screen.getByRole("textbox", { name: "获权内容链接" }), {
    target: { value: "https://www.xiaohongshu.com/explore/demo-id?xsec_token=secret" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: /我确认有权/ }));
  fireEvent.click(screen.getByRole("button", { name: /创建待转写记录/ }));

  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    platform: "xiaohongshu",
  }));
});

it("生产 Provider 直接创建自动采集任务", () => {
  const onSubmit = vi.fn();
  render(
    <CollectionTaskModal
      provider={{
        provider: "xiaohongshu-web-v1",
        mode: "production",
        is_sandbox: false,
        platforms: ["xiaohongshu"],
        kinds: ["single"],
        max_items: 1,
        sample_inputs: {
          single: "粘贴小红书分享链接（需包含 xsec_token）",
          keyword: "暂未开放",
          creator_content: "暂未开放",
          creator_profile: "暂未开放",
        },
      }}
      submitting={false}
      onClose={() => undefined}
      onSubmit={onSubmit}
    />,
  );

  fireEvent.change(screen.getByRole("textbox", { name: "获权内容链接" }), {
    target: { value: "https://www.xiaohongshu.com/explore/demo-id?xsec_token=secret" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: /我确认有权/ }));
  fireEvent.click(screen.getByRole("button", { name: /创建采集任务/ }));

  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ platform: "xiaohongshu" }));
});

it("生产 Provider 没有真实样本时留空输入并隐藏推荐样本", () => {
  render(
    <CollectionTaskModal
      provider={{
        provider: "xiaohongshu-web-v1",
        mode: "production",
        is_sandbox: false,
        platforms: ["xiaohongshu"],
        kinds: ["single"],
        max_items: 1,
        sample_inputs: { single: "", keyword: "", creator_content: "", creator_profile: "" },
      }}
      submitting={false}
      onClose={() => undefined}
      onSubmit={() => undefined}
    />,
  );

  expect(screen.getByRole("textbox", { name: "获权内容链接" })).toHaveValue("");
  expect(screen.getByRole("textbox", { name: "获权内容链接" })).toHaveAttribute("placeholder", expect.stringContaining("xsec_token"));
  expect(screen.queryByRole("button", { name: "填入推荐样本" })).not.toBeInTheDocument();
});

it("TikHub Provider 显示线上第三方采集说明", () => {
  render(
    <CollectionTaskModal
      provider={{
        provider: "tikhub-v1",
        mode: "production_third_party",
        is_sandbox: false,
        platforms: ["xiaohongshu"],
        kinds: ["keyword", "creator_content"],
        max_items: 30,
        sample_inputs: {
          single: "暂未开放",
          keyword: "AI 工具",
          creator_content: "博主主页 URL 或 24 位用户 ID",
          creator_profile: "暂未开放",
        },
      }}
      submitting={false}
      onClose={() => undefined}
      onSubmit={() => undefined}
    />,
  );

  expect(screen.getByText("线上真实采集 · tikhub-v1")).toBeInTheDocument();
  expect(screen.getByText(/关键词、博主近期内容和博主资料走 TikHub/)).toBeInTheDocument();
  expect(screen.getByText("返回最多 30 条轻量结果")).toBeInTheDocument();
  expect(screen.getByRole("spinbutton", { name: "采集条数" })).toHaveAttribute("max", "30");
  expect(screen.getByText("最多返回 30 条")).toBeInTheDocument();
  expect(screen.getByText("LIVE")).toBeInTheDocument();
});

it("关键词默认图文，并从输入识别视频和选择最热范围", () => {
  const onSubmit = vi.fn();
  render(
    <CollectionTaskModal
      provider={{
        provider: "tikhub-v1",
        mode: "production_third_party",
        is_sandbox: false,
        platforms: ["xiaohongshu"],
        kinds: ["keyword"],
        max_items: 20,
        sample_inputs: {
          single: "暂未开放",
          keyword: "AI 工具",
          creator_content: "暂未开放",
          creator_profile: "暂未开放",
        },
      }}
      submitting={false}
      onClose={() => undefined}
      onSubmit={onSubmit}
    />,
  );

  expect(screen.getByText(/自动识别为图文笔记/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "搜索关键词" }), {
    target: { value: "采集 AI 口播视频" },
  });
  expect(screen.getByText(/自动识别为视频笔记/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "最热时间范围" }), {
    target: { value: "month" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: /我确认有权/ }));
  fireEvent.click(screen.getByRole("button", { name: /创建采集任务/ }));

  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    contentType: "video",
    publishTime: "month",
  }));
});

it.each([
  ["关键词搜索", "keyword", "采集条数", 7],
  ["博主内容", "creator_content", "近期采集条数", 6],
] as const)("%s可选择采集条数", (tabName, kind, limitLabel, limit) => {
  const onSubmit = vi.fn();
  render(
    <CollectionTaskModal
      provider={{
        provider: "opencli-local-v1",
        mode: "experimental_local_helper",
        is_sandbox: false,
        platforms: ["xiaohongshu"],
        kinds: ["single", "keyword", "creator_content"],
        max_items: 20,
        sample_inputs: {
          single: "https://www.xiaohongshu.com/explore/demo?xsec_token=demo",
          keyword: "AI 工作流",
          creator_content: "https://www.xiaohongshu.com/user/profile/demo",
          creator_profile: "暂未开放",
        },
      }}
      submitting={false}
      onClose={() => undefined}
      onSubmit={onSubmit}
    />,
  );

  fireEvent.click(screen.getByRole("tab", { name: new RegExp(tabName) }));
  fireEvent.change(screen.getByRole("spinbutton", { name: limitLabel }), {
    target: { value: String(limit) },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: /我确认有权/ }));
  fireEvent.click(screen.getByRole("button", { name: /创建采集任务/ }));

  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ kind, limit }));
});
