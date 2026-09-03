import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { CollectRewriteTaskModal } from "@/components/collect-rewrite-task-modal";

it("新链接一键仿写不会替用户勾选使用权确认", () => {
  const onSubmit = vi.fn();
  render(
    <CollectRewriteTaskModal
      source={{ url: "https://www.xiaohongshu.com/explore/demo?xsec_token=token" }}
      initialPrompt="面向新人重新表达"
      submitting={false}
      onClose={() => undefined}
      onSubmit={onSubmit}
    />,
  );
  const confirmation = screen.getByRole("checkbox", { name: /我确认有权/ });
  const submit = screen.getByRole("button", { name: "确认并创建任务" });
  expect(confirmation).not.toBeChecked();
  expect(submit).toBeDisabled();
  fireEvent.click(confirmation);
  fireEvent.click(submit);
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    usageConfirmed: true,
    prompt: "面向新人重新表达",
  }));
});

it("已有采集记录可直接进入检查点工作流", () => {
  const onSubmit = vi.fn();
  render(
    <CollectRewriteTaskModal
      source={{ collectionId: "col_demo" }}
      initialPrompt="换一个受众"
      submitting={false}
      onClose={() => undefined}
      onSubmit={onSubmit}
    />,
  );
  expect(screen.queryByRole("checkbox", { name: /我确认有权/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "确认并创建任务" }));
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ collectionId: "col_demo" }));
});
