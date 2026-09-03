import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createApiCoverTask,
  createApiCollectionTask,
  cancelApiTask,
  deleteApiConversation,
  deleteApiLibraryRecord,
  createApiFeishuSync,
  createApiMediaAsset,
  createApiTranscriptionTask,
  createApiTrendTask,
  initApiTranscriptionUpload,
  preflightApiFeishuBinding,
  putApiTranscriptionUpload,
  recordFromApi,
  taskFromApi,
  topicCandidatesFromTask,
  updateApiConversation,
  type ApiLibraryRecord,
  type ApiTask,
} from "@/lib/api";
import { collectionMetricLabel, collectionProviderLabel } from "@/lib/domain";

const task: ApiTask = {
  id: "task_demo",
  workspace_id: "ws_demo",
  conversation_id: "conv_demo",
  type: "collect_breakdown_rewrite",
  mode: "agent",
  skill_id: "collect_breakdown_rewrite",
  status: "partial_success",
  stage: "拆解适配器失败",
  progress: 38,
  input: { prompt: "demo" },
  result_refs: [{ type: "collection", id: "col_demo" }],
  result_summary: {},
  error: { code: "SIMULATED_PROVIDER_ERROR", message: "暂时不可用", retryable: true },
  trace_id: "trc_test_task_001",
  current_attempt: 1,
  retry_count: 0,
  attempts: [{ attempt_no: 1, status: "partial_success", stage: "拆解适配器失败", progress: 38 }],
  created_at: "2026-08-27T07:00:00+08:00",
  updated_at: "2026-08-27T07:00:01+08:00",
};

describe("阶段 1 API 适配器", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("保留后端任务状态、attempt、错误和结果引用", () => {
    expect(taskFromApi(task)).toMatchObject({
      id: "task_demo",
      status: "partially_succeeded",
      attempt: 1,
      errorCode: "SIMULATED_PROVIDER_ERROR",
      retryable: true,
      source: "api",
      resultRefs: [{ type: "collection", id: "col_demo" }],
    });
  });

  it("将三库 API 记录映射为统一详情模型", () => {
    const record: ApiLibraryRecord = {
      id: "crt_demo",
      library_type: "creations",
      title: "持久化创作稿",
      status: "待审核",
      type: "采集仿写",
      source: "col_demo + brk_demo",
      author: "user_demo",
      updated_at: "2026-08-27T07:00:01+08:00",
      summary: "合成正文",
      tags: ["阶段 1"],
      version: "v1",
      source_id: "crt_demo",
      task_id: "task_demo",
      adopted_version_id: null,
      collection_kind: null,
      provider: null,
      external_url: null,
      thumbnail_url: null,
      metrics: {},
      is_sandbox: false,
    };
    expect(recordFromApi(record)).toMatchObject({
      id: "crt_demo",
      taskId: "task_demo",
      sourceKind: "api",
      version: "v1",
    });
  });

  it("将采集详情中的英文状态、来源和指标字段显示为中文", () => {
    const record: ApiLibraryRecord = {
      id: "col_demo",
      library_type: "collections",
      title: "视频采集",
      status: "needs_transcript",
      type: "单篇采集",
      source: "xiaohongshu",
      author: "内容成员",
      updated_at: "2026-08-29T19:00:00+08:00",
      summary: "采集正文",
      tags: [],
      version: "v1",
      source_id: "note_demo",
      task_id: "task_demo",
      adopted_version_id: null,
      collection_kind: "single",
      provider: "xiaohongshu-web-v1",
      external_url: "https://www.xiaohongshu.com/explore/note_demo",
      thumbnail_url: "/api/v1/media-assets/asset_cover/content",
      metrics: { likes: 660 },
      is_sandbox: false,
    };

    expect(recordFromApi(record)).toMatchObject({
      status: "待生成视频文案",
      thumbnailUrl: "http://localhost:3000/api/riffloom/media-assets/asset_cover/content",
    });
    expect(collectionProviderLabel("xiaohongshu-web")).toBe("小红书网页采集");
    expect(collectionMetricLabel("likes")).toBe("点赞数");
  });

  it("通过热点端点创建默认近一周的 TikHub 选题任务", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => task,
    });
    vi.stubGlobal("fetch", fetchMock);

    await createApiTrendTask({
      direction: "AI 工作流",
      keywords: ["版本"],
      windowDays: 7,
      idempotencyKey: "trend-test-key",
    });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/trend-tasks$/);
    expect(JSON.parse(String(options.body))).toMatchObject({
      direction: "AI 工作流",
      keywords: ["版本"],
      window_days: 7,
    });
  });

  it("关键词采集提交识别的内容类型和最热范围", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => task });
    vi.stubGlobal("fetch", fetchMock);

    await createApiCollectionTask({
      kind: "keyword",
      platform: "xiaohongshu",
      value: "AI 口播视频",
      usageConfirmed: true,
      contentType: "video",
      publishTime: "month",
      idempotencyKey: "collection-test-key",
    });

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.query).toEqual({
      keyword: "AI 口播视频",
      sort: "most-liked",
      publish_time: "month",
      content_type: "video",
    });
  });

  it("通过统一端点删除单条资料库记录", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        id: "brk_demo",
        library_type: "breakdowns",
        status: "deleted",
        deleted_at: "2026-08-31T05:00:00Z",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await deleteApiLibraryRecord("breakdowns", "brk_demo");

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/libraries\/breakdowns\/brk_demo$/);
    expect(options.method).toBe("DELETE");
  });

  it("支持重命名和删除历史对话", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "conv_demo", title: "新名称", message_count: 2, updated_at: "2026-09-02T00:00:00Z" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => null });
    vi.stubGlobal("fetch", fetchMock);

    await updateApiConversation("conv_demo", "新名称");
    await deleteApiConversation("conv_demo");

    expect(fetchMock.mock.calls[0][0]).toMatch(/\/conversations\/conv_demo$/);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "PATCH" });
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ title: "新名称" });
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "DELETE" });
  });

  it("通过统一任务端点取消任务", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...task, status: "cancelled" }) });
    vi.stubGlobal("fetch", fetchMock);

    await cancelApiTask("task_demo");

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/tasks\/task_demo\/cancel$/);
    expect(options.method).toBe("POST");
  });

  it("通过阶段 4B 端点提交获权素材和固定四方案任务", async () => {
    const asset = {
      id: "media_demo",
      original_name: "cover.png",
      mime_type: "image/png",
      byte_size: 68,
      width: 1,
      height: 1,
      role: "original",
      rights_status: "approved",
      content_hash: "hash",
      content_url: "/api/v1/media-assets/media_demo/content",
      created_by: "user_demo",
      created_at: "2026-08-27T07:00:00+08:00",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => asset })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ...task, type: "cover_generation", mode: "cover" }) });
    vi.stubGlobal("fetch", fetchMock);

    await createApiMediaAsset({
      filename: "cover.png",
      mimeType: "image/png",
      role: "original",
      rightsConfirmed: true,
      dataUrl: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB",
    });
    await createApiCoverTask({
      prompt: "生成知识卡片封面",
      mediaAssetIds: ["media_demo"],
      idempotencyKey: "cover-test-key",
    });

    expect(fetchMock.mock.calls[0][0]).toMatch(/\/media-assets$/);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
      role: "original",
      rights_confirmed: true,
    });
    expect(fetchMock.mock.calls[1][0]).toMatch(/\/cover-tasks$/);
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      prompt: "生成知识卡片封面",
      media_asset_ids: ["media_demo"],
      usage_confirmed: false,
    });
  });

  it("只在权利确认后提交短媒体转写", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => task });
    vi.stubGlobal("fetch", fetchMock);

    await createApiTranscriptionTask({
      recordId: "col_demo",
      filename: "authorized.mp3",
      mimeType: "audio/mpeg",
      durationSeconds: 12.5,
      rightsConfirmed: true,
      dataUrl: "data:audio/mpeg;base64,SUQz",
      idempotencyKey: "transcription-test-key",
    });

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/collections\/col_demo\/transcription-tasks$/);
    expect(options.headers).toMatchObject({ "Idempotency-Key": "transcription-test-key" });
    expect(JSON.parse(String(options.body))).toEqual({
      filename: "authorized.mp3",
      mime_type: "audio/mpeg",
      duration_seconds: 12.5,
      language: "zh",
      rights_confirmed: true,
      data_url: "data:audio/mpeg;base64,SUQz",
    });
  });

  it("通过预签名 URL 直传媒体后使用 asset_id 创建转写任务", async () => {
    const upload = {
      asset_id: "asset_transcription_demo",
      object_key: "ws_demo/asset_transcription_demo.wav",
      presigned_put_url: "https://tos.example/upload?X-Amz-Signature=fake",
      expires_in_seconds: 900,
      upload_mode: "tos_presign" as const,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => upload })
      .mockResolvedValueOnce({ ok: true, status: 200 })
      .mockResolvedValueOnce({ ok: true, json: async () => task });
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["RIFF....WAVE"], "authorized.wav", { type: "audio/wav" });

    const initialized = await initApiTranscriptionUpload({
      filename: file.name,
      mimeType: "audio/wav",
      byteSize: file.size,
      durationSeconds: 7.3,
      rightsConfirmed: true,
    });
    await putApiTranscriptionUpload(initialized.presigned_put_url!, file, "audio/wav");
    await createApiTranscriptionTask({
      recordId: "col_demo",
      filename: file.name,
      mimeType: "audio/wav",
      durationSeconds: 7.3,
      rightsConfirmed: true,
      assetId: initialized.asset_id,
      idempotencyKey: "transcription-presign-test-key",
    });

    expect(fetchMock.mock.calls[0][0]).toMatch(/\/transcription-uploads$/);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
      filename: "authorized.wav",
      mime_type: "audio/wav",
      byte_size: file.size,
      rights_confirmed: true,
    });
    expect(fetchMock.mock.calls[1]).toEqual([
      upload.presigned_put_url,
      expect.objectContaining({
        method: "PUT",
        body: file,
        credentials: "omit",
        headers: { "Content-Type": "audio/wav" },
      }),
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body))).toMatchObject({
      asset_id: "asset_transcription_demo",
      rights_confirmed: true,
    });
  });

  it("通过阶段 4C 端点预检飞书绑定并携带幂等键启动同步", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          valid: true,
          scope_key: "collection.single",
          field_mapping: { riffloom_record_id: "riffloom_record_id" },
          required_fields: ["riffloom_record_id"],
          sample: {},
          errors: [],
          provider: "sandbox-feishu-v1",
          external_calls: false,
        }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ...task, type: "feishu_sync", mode: "integration" }) });
    vi.stubGlobal("fetch", fetchMock);

    await preflightApiFeishuBinding({
      connectionId: "fconn_demo",
      scopeKey: "collection.single",
      targetBaseId: "sandbox_base",
      targetTableId: "sandbox_single",
      targetTableName: "单篇采集库",
    });
    await createApiFeishuSync({
      bindingId: "fbind_demo",
      mode: "incremental",
      idempotencyKey: "feishu-sync-test-key",
      sourceRecordId: "col_demo",
    });

    expect(fetchMock.mock.calls[0][0]).toMatch(/\/integrations\/feishu\/bindings\/preflight$/);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
      connection_id: "fconn_demo",
      scope_key: "collection.single",
      target_table_id: "sandbox_single",
    });
    expect(fetchMock.mock.calls[1][0]).toMatch(/\/integrations\/feishu\/bindings\/fbind_demo\/syncs$/);
    expect(fetchMock.mock.calls[1][1]?.headers).toMatchObject({ "Idempotency-Key": "feishu-sync-test-key" });
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      mode: "incremental",
      source_record_id: "col_demo",
    });
  });

  it("只把 topic-guidance.v3 中的五维热点与合法来源传给界面", () => {
    const topicTask: ApiTask = {
      ...task,
      skill_id: "viral_topic_coach",
      type: "viral_topic_coach",
      result_summary: {
        candidates: [
          {
            topic: "AI 工作流如何少返工",
            angle: "从版本丢失切入",
            audience: "内容团队",
            why_hot: "近一周讨论快速增长",
            core_value: "帮助团队减少返工",
            title_suggestion: "《AI 工作流为什么总在返工》",
            sources: [
              {
                platform: "xiaohongshu",
                source_id: "note_demo",
                observed_at: "2026-08-27T08:00:00Z",
              },
              { platform: "unknown", source_id: 7 },
            ],
          },
        ],
      },
    };

    expect(topicCandidatesFromTask(topicTask)).toMatchObject([
      {
        topic: "AI 工作流如何少返工",
        why_hot: "近一周讨论快速增长",
        title_suggestion: "《AI 工作流为什么总在返工》",
        sources: [{ platform: "xiaohongshu", source_id: "note_demo" }],
      },
    ]);
  });
});
