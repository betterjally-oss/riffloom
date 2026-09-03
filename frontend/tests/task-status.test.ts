import { describe, expect, it } from "vitest";
import { canRetryTask, readableParagraphs, readableTranscript } from "@/lib/domain";

describe("任务状态适配", () => {
  it("只允许失败、部分失败、断线和过期状态重试", () => {
    expect(canRetryTask("failed")).toBe(true);
    expect(canRetryTask("failed", false)).toBe(false);
    expect(canRetryTask("partially_succeeded")).toBe(true);
    expect(canRetryTask("succeeded")).toBe(false);
    expect(canRetryTask("running")).toBe(false);
  });

  it("正文和视频文案分段时移除已单列的话题标签", () => {
    const paragraphs = readableParagraphs(
      `${"第一段内容需要清楚说明。".repeat(9)}${"第二段继续补充细节。".repeat(9)} #ai[话题]# #产品设计[话题]#`,
      ["ai", "产品设计"],
    );

    expect(paragraphs.length).toBeGreaterThan(1);
    expect(paragraphs.join("")).not.toContain("[话题]");
    expect(paragraphs.join("")).not.toContain("#ai");
  });

  it("历史视频文案无标点时按已有语义分段补句号", () => {
    expect(readableTranscript("第一句第二句", [{ text: "第一句" }, { text: "第二句" }]))
      .toBe("第一句。第二句。");
    expect(readableTranscript("第一句，第二句。", [{ text: "第一句" }, { text: "第二句" }]))
      .toBe("第一句，第二句。");
  });
});
