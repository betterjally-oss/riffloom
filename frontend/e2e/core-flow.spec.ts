import { expect, test } from "@playwright/test";

test.beforeEach(async () => {
  const apiPort = process.env.RIFFLOOM_E2E_API_PORT ?? "8110";
  const response = await fetch(`http://127.0.0.1:${apiPort}/api/v1/session/onboarding/complete`, {
    method: "POST",
    headers: {
      "x-riffloom-user": "user_admin",
      "x-riffloom-workspace": "ws_demo",
    },
  });
  expect(response.ok).toBe(true);
});

test("历史对话独立滚动，不挤压标题、新对话和底部账号", async ({ page }) => {
  test.skip(page.viewportSize()!.width <= 1100, "窄屏使用折叠导航");
  const conversations = Array.from({ length: 50 }, (_, index) => ({
    id: `conv-overflow-${index + 1}`,
    mode: "agent",
    title: `历史对话 ${index + 1}`,
    message_count: 2,
    updated_at: new Date(Date.UTC(2026, 8, 3, 0, index)).toISOString(),
  }));
  await page.route("**/api/riffloom/conversations", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({ json: { items: conversations, total: conversations.length } });
  });

  await page.goto("/chat");
  const history = page.getByLabel("历史对话");
  const title = history.getByRole("heading", { name: "历史对话" });
  const newConversation = history.getByRole("button", { name: "新对话" });
  const list = history.getByRole("navigation", { name: "对话列表" });
  const profile = page.locator(".profile-wrap");
  await expect(history).toBeVisible();
  await expect(list.getByRole("button", { name: "历史对话 1", exact: true })).toBeVisible();

  const [titleBefore, newBefore, listBefore, profileBefore] = await Promise.all([
    title.boundingBox(),
    newConversation.boundingBox(),
    list.boundingBox(),
    profile.boundingBox(),
  ]);
  expect(titleBefore).not.toBeNull();
  expect(newBefore).not.toBeNull();
  expect(listBefore).not.toBeNull();
  expect(profileBefore).not.toBeNull();
  expect(listBefore!.y).toBeGreaterThanOrEqual(newBefore!.y + newBefore!.height);
  expect(listBefore!.y + listBefore!.height).toBeLessThanOrEqual(profileBefore!.y);
  expect(await list.evaluate((node) => node.scrollHeight > node.clientHeight)).toBe(true);

  await list.evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await expect(list.getByRole("button", { name: "历史对话 50", exact: true })).toBeVisible();
  const [titleAfter, newAfter, profileAfter] = await Promise.all([
    title.boundingBox(),
    newConversation.boundingBox(),
    profile.boundingBox(),
  ]);
  expect(titleAfter?.y).toBe(titleBefore?.y);
  expect(newAfter?.y).toBe(newBefore?.y);
  expect(profileAfter?.y).toBe(profileBefore?.y);
});

test("一键采集仿写可受理、完成并进入创作库", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText("API 已连接 · mock-v1");
  await expect(page.getByLabel("运行能力边界")).toContainText(
    "采集 SANDBOX · 文本 mock-v1 · 封面 MOCK · 飞书外部写入 OFF",
  );
  await page.getByRole("button", { name: /一键采集仿写/ }).click();
  await page.getByRole("textbox", { name: "任务描述" }).fill("https://sandbox.riffloom.local/notes/note-001?access=e2e 请改写成职场新人版本");
  await page.getByRole("button", { name: "发送" }).click();
  const confirmation = page.getByRole("dialog", { name: "一键采集仿写" });
  await expect(confirmation.getByLabel(/我确认有权/)).not.toBeChecked();
  await confirmation.getByLabel(/我确认有权/).check();
  await confirmation.getByRole("button", { name: "确认并创建任务" }).click();
  await expect(page).toHaveURL(/task=task_/);
  await expect(page.getByText(/一键采集仿写已完成/)).toBeVisible({ timeout: 5_000 });
  const creationTask = page.getByLabel("任务中心", { exact: true }).getByRole("article").filter({ hasText: "一键采集仿写" }).first();
  await expect(creationTask.getByRole("link", { name: "查看所在库" })).toHaveAttribute("href", /\/creations\?record=crt_/);
  await expect(page.getByText("来源、技能、知识快照与版本已锁定")).toHaveCount(0);
  await expect(page.locator(".message-task .mono")).toHaveCount(0);
  await page.getByRole("button", { name: /查看创作结果/ }).click();
  await expect(page).toHaveURL(/\/creations/);
  await expect(page.getByRole("heading", { name: "创作库" })).toBeVisible();
  await expect(page.getByRole("dialog", { name: /别再堆.*工具/ })).toBeVisible();
  await page.getByRole("button", { name: "版本", exact: true }).click();
  await page.getByRole("button", { name: /标记采用|确认采用当前版本/ }).first().click();
  await expect(page.getByText(/已通过 API 确认采用/)).toBeVisible();
});

test("对话后输入框保留底部间距，且技能支持多选", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);

  await page.getByRole("button", { name: /^引用技能/ }).click();
  const picker = page.getByLabel("技能选择");
  await picker.getByRole("button", { name: /^爆款拆解/ }).click();
  await expect(picker).toBeHidden();
  const input = page.getByRole("textbox", { name: "任务描述" });
  await expect(input).toHaveValue("");
  await expect(input).toHaveAttribute("placeholder", "请使用「爆款拆解」技能，处理以下内容：");

  await page.getByRole("button", { name: /^引用技能/ }).click();
  await page.getByLabel("技能选择").getByRole("button", { name: /^爆款选题指导/ }).click();
  const skillChips = page.getByLabel("当前调用技能").locator("span");
  await expect(skillChips).toHaveCount(2);
  await expect(skillChips.nth(0)).toContainText("爆款拆解");
  await expect(skillChips.nth(1)).toContainText("爆款选题指导");
  const skillChip = await skillChips.nth(1).boundingBox();
  expect(skillChip).not.toBeNull();

  await input.fill("请拆解这段内容");
  const inputBox = await input.boundingBox();
  expect(inputBox).not.toBeNull();
  expect(inputBox!.x).toBeGreaterThan(skillChip!.x + skillChip!.width);
  await skillChips.nth(1).hover();
  const removeSkill = page.getByRole("button", { name: "移除技能「爆款选题指导」" });
  await expect(removeSkill).toBeVisible();
  await removeSkill.click();
  await expect(skillChips).toHaveCount(1);
  await skillChips.first().hover();
  await page.getByRole("button", { name: "移除技能「爆款拆解」" }).click();
  await expect(page.getByLabel("当前调用技能")).toHaveCount(0);
  await input.fill("");

  await page.getByRole("button", { name: /^引用技能/ }).click();
  await page.getByLabel("技能选择").getByRole("button", { name: /^爆款拆解/ }).click();
  await input.fill("请拆解这段内容");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.locator(".message.user")).toContainText("请拆解这段内容");
  await expect(page.getByLabel("Riffloom 智能体介绍")).toHaveCount(0);
  await expect(page.getByRole("tablist", { name: "对话功能" })).toHaveCount(0);
  const composer = await page.locator(".composer").boundingBox();
  const userMessage = await page.locator(".message.user").last().boundingBox();
  const chatStage = await page.locator(".chat-stage").boundingBox();
  const viewport = page.viewportSize();
  expect(composer).not.toBeNull();
  expect(userMessage).not.toBeNull();
  expect(chatStage).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(userMessage!.height).toBeLessThan(80);
  expect(userMessage!.y - chatStage!.y).toBeLessThanOrEqual(64);
  const bottomGap = viewport!.height - (composer!.y + composer!.height);
  expect(bottomGap).toBeGreaterThanOrEqual(29);
  expect(bottomGap).toBeLessThanOrEqual(31);
});

test("任务对话会写入真实历史并在刷新后恢复", async ({ page }) => {
  test.skip(page.viewportSize()!.width <= 1100, "窄屏使用折叠导航");
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  await page.getByRole("tab", { name: "创作" }).click();

  const message = "验证创作对话会进入真实历史";
  await page.getByRole("textbox", { name: "任务描述" }).fill(message);
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.locator(".message.user")).toContainText(message);

  const history = page.getByLabel("历史对话");
  await expect(history).toBeVisible();
  await expect(history.getByRole("button", { name: message, exact: true })).toBeVisible();

  await page.reload();
  await expect(history.getByRole("button", { name: message, exact: true })).toBeVisible();
  await history.getByRole("button", { name: message, exact: true }).click();
  await expect(page.locator(".message.user")).toContainText(message);
});

test("Agent 支持 Enter 连续对话、无框回答与分 Tab 保留", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  await expect(page.getByRole("tab", { name: "Agent" })).toHaveAttribute("aria-selected", "true");

  const input = page.getByRole("textbox", { name: "任务描述" });
  await input.fill("你好，你能做什么？");
  await input.press("Enter");
  await expect(page.locator(".message.agent").last()).toContainText("Riffloom 智能体", { timeout: 5_000 });
  await expect(page.getByLabel("Riffloom 智能体介绍")).toHaveCount(0);
  await expect(page.getByRole("tablist", { name: "对话功能" })).toHaveCount(0);
  await expect(page.locator(".message.agent").last()).not.toContainText(/Word|Excel|PPT|微信|定时/);
  await expect(page.locator(".message.agent").last()).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await expect(page.locator(".message.agent").last()).toHaveCSS("box-shadow", "none");

  const isWideDesktop = page.viewportSize()!.width > 1100;
  const expectedMessageCount = isWideDesktop ? 2 : 1;
  if (isWideDesktop) {
    await input.fill("我想拆解一篇小红书内容");
    await input.press("Enter");
    await expect(page.locator(".message.user")).toHaveCount(2);
    await expect(page.locator(".message.agent")).toHaveCount(2);
    await expect(page.locator(".message.agent").last()).toContainText("目前我可以帮你", { timeout: 5_000 });
  }
  await expect(page.locator(".app-shell")).not.toHaveClass(/nav-collapsed/);
  const history = page.getByLabel("历史对话");
  if (isWideDesktop) {
    const savedConversation = history.getByRole("button", { name: /你好，你能做什么/ }).first();
    await expect(history).toBeVisible();
    await expect(savedConversation).toBeVisible({ timeout: 5_000 });
    const primaryNavBox = await page.locator(".primary-nav").boundingBox();
    const historyBox = await history.boundingBox();
    expect(primaryNavBox).not.toBeNull();
    expect(historyBox).not.toBeNull();
    expect(historyBox!.y).toBeGreaterThan(primaryNavBox!.y + primaryNavBox!.height);
    await page.getByRole("button", { name: "折叠导航" }).click();
    await expect(page.locator(".app-shell")).toHaveClass(/nav-collapsed/);
    await page.getByRole("button", { name: "展开导航" }).click();
    await expect(history).toBeVisible();
    await history.getByRole("button", { name: "新对话" }).click();
    await expect(page.locator(".message")).toHaveCount(0);
    await savedConversation.click();
    await expect(page.locator(".message.user")).toHaveCount(expectedMessageCount);
    await expect(page.locator(".message.agent")).toHaveCount(expectedMessageCount);
    await history.getByRole("button", { name: /管理对话 你好，你能做什么/ }).click();
    await page.getByLabel(/你好，你能做什么.*操作/).getByRole("button", { name: "重命名" }).click();
    const renameInput = page.getByRole("textbox", { name: /重命名 你好，你能做什么/ });
    await renameInput.fill("我的 AI 内容助手");
    await renameInput.press("Enter");
    await expect(history.getByRole("button", { name: "我的 AI 内容助手", exact: true })).toBeVisible();
  } else {
    await expect(history).toBeHidden();
  }
  await expect(page.locator(".workspace-title")).toBeHidden();
  if (isWideDesktop) {
    await history.getByRole("button", { name: "新对话" }).click();
    await expect(page.getByRole("tablist", { name: "对话功能" })).toBeVisible();
    await input.fill("Agent 未发送草稿");
    await page.getByRole("tab", { name: "采集" }).click();
    await expect(input).toHaveValue("");
    await input.fill("采集未发送草稿");
    await page.getByRole("tab", { name: "Agent" }).click();
    await expect(input).toHaveValue("Agent 未发送草稿");
    await expect(page.locator(".app-shell")).not.toHaveClass(/nav-collapsed/);
    await history.getByRole("button", { name: "管理对话 我的 AI 内容助手" }).click();
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByLabel("我的 AI 内容助手 操作").getByRole("button", { name: "删除" }).click();
    await expect(history.getByRole("button", { name: "我的 AI 内容助手", exact: true })).toHaveCount(0);
    await expect(page.locator(".message")).toHaveCount(0);
  }
});

test("切换空白任务 Tab 时页头、Tab 和输入框保持原位", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  const intro = page.getByLabel("Riffloom 智能体介绍");
  const tabs = page.getByRole("tablist", { name: "对话功能" });
  const composer = page.locator(".composer");
  const before = await Promise.all([intro.boundingBox(), tabs.boundingBox(), composer.boundingBox()]);

  for (const name of ["采集", "拆解", "创作", "热点"]) {
    await page.getByRole("tab", { name }).click();
    await expect(intro).toBeVisible();
    const after = await Promise.all([intro.boundingBox(), tabs.boundingBox(), composer.boundingBox()]);
    for (let index = 0; index < before.length; index += 1) {
      expect(after[index]).not.toBeNull();
      expect(Math.abs(after[index]!.y - before[index]!.y)).toBeLessThanOrEqual(1);
    }
  }
});

test("引用面板作为浮层且不移动输入框", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  const composer = page.locator(".composer");
  const tabs = page.getByRole("tablist", { name: "对话功能" });
  const initialComposer = await composer.boundingBox();
  const tabsBox = await tabs.boundingBox();
  expect(initialComposer).not.toBeNull();
  expect(tabsBox).not.toBeNull();
  expect(Math.abs(initialComposer!.y - tabsBox!.y - tabsBox!.height - 62)).toBeLessThanOrEqual(1);

  for (const { trigger, panel } of [
    { trigger: "引用知识", panel: "引用知识库" },
    { trigger: "引用技能", panel: "技能选择" },
  ]) {
    await page.getByRole("button", { name: new RegExp(`^${trigger}`) }).click();
    const openComposer = await composer.boundingBox();
    const panelBox = await page.getByLabel(panel).boundingBox();
    expect(openComposer).not.toBeNull();
    expect(panelBox).not.toBeNull();
    expect(Math.abs(openComposer!.y - initialComposer!.y)).toBeLessThanOrEqual(1);
    expect(panelBox!.y).toBeGreaterThanOrEqual(openComposer!.y + openComposer!.height);
    await page.getByRole("button", { name: new RegExp(`^${trigger}`) }).click();
  }

  const input = page.getByRole("textbox", { name: "任务描述" });
  await input.fill("测试对话态浮层位置");
  await input.press("Enter");
  await expect(page.locator(".message.user")).toContainText("测试对话态浮层位置");
  const dockedComposer = await composer.boundingBox();
  await page.getByRole("button", { name: /^引用知识/ }).click();
  const openDockedComposer = await composer.boundingBox();
  const dockedPanel = await page.getByLabel("引用知识库").boundingBox();
  expect(dockedComposer).not.toBeNull();
  expect(openDockedComposer).not.toBeNull();
  expect(dockedPanel).not.toBeNull();
  expect(Math.abs(openDockedComposer!.y - dockedComposer!.y)).toBeLessThanOrEqual(1);
  expect(dockedPanel!.y + dockedPanel!.height).toBeLessThanOrEqual(openDockedComposer!.y);
});

test("切换 Tab 后后台 Agent 回复仍更新所属 Tab", async ({ page }) => {
  test.skip(page.viewportSize()!.width <= 1100, "窄屏使用折叠导航");
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);

  const input = page.getByRole("textbox", { name: "任务描述" });
  await input.fill("请记录这个选题：AI 内容团队如何减少重复工作");
  await input.press("Enter");
  await expect(page.locator(".message.agent").last()).toContainText("正在回复");
  const history = page.getByLabel("历史对话");
  const savedConversation = history.getByRole("button", {
    name: "请记录这个选题：AI 内容团队如何减少重复工作",
    exact: true,
  });
  await expect(savedConversation).toBeVisible();
  await history.getByRole("button", { name: "新对话" }).click();
  await page.getByRole("tab", { name: "采集" }).click();

  await page.getByRole("button", { name: /任务中心/ }).click();
  const taskCard = page.getByLabel("任务中心", { exact: true }).getByRole("article").filter({ hasText: "Riffloom 智能体" }).first();
  await expect(taskCard).toContainText("已完成", { timeout: 5_000 });
  await page.getByRole("button", { name: "关闭任务中心" }).click();
  await page.waitForTimeout(500);
  const taskRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/riffloom/tasks")) taskRequests.push(request.url());
  });
  await page.waitForTimeout(4_200);
  expect(taskRequests.length).toBeLessThanOrEqual(1);

  await savedConversation.click();
  await expect(page.locator(".message.agent")).toHaveCount(1);
  await expect(page.locator(".message.agent").last()).not.toContainText("正在回复");
});

test("任务中心可恢复 Agent 回复", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  await expect(page.getByRole("button", { name: /任务中心/ })).toHaveAccessibleName("任务中心 · 运行中 0");
  const input = page.getByRole("textbox", { name: "任务描述" });
  const firstPrompt = "告诉我 Riffloom 适合处理什么任务";
  const firstAccepted = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/api/riffloom/tasks"));
  await input.fill(firstPrompt);
  await input.press("Enter");
  const firstTaskId = String((await (await firstAccepted).json()).id);

  await page.getByRole("button", { name: /任务中心/ }).click();
  const taskCenter = page.getByLabel("任务中心", { exact: true });
  const taskCard = taskCenter.getByRole("article").filter({ hasText: firstTaskId });
  await expect(taskCard).toContainText("已完成", { timeout: 5_000 });
  await page.getByRole("button", { name: "关闭任务中心" }).click();

  const secondPrompt = "继续介绍适合内测的使用方式";
  const secondAccepted = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/api/riffloom/tasks"));
  await input.fill(secondPrompt);
  await input.press("Enter");
  const secondTaskId = String((await (await secondAccepted).json()).id);
  await page.getByRole("button", { name: /任务中心/ }).click();
  const secondTaskCard = taskCenter.getByRole("article").filter({ hasText: secondTaskId });
  await expect(secondTaskCard).toContainText("已完成", { timeout: 5_000 });
  const restoreLink = secondTaskCard.getByRole("link", { name: "查看回复" });
  await expect(restoreLink).toHaveAttribute("href", /\/chat\?task=task_/);
  await restoreLink.click();

  await expect(page).toHaveURL(/\/chat\?task=task_/);
  await expect(page.locator(".message.user")).toHaveCount(2);
  await expect(page.locator(".message.user").nth(0)).toContainText(firstPrompt);
  await expect(page.locator(".message.user").nth(1)).toContainText(secondPrompt);
  await expect(page.locator(".message.agent").last()).not.toContainText("正在回复");
});

test("空闲资料库不产生周期 API 请求", async ({ page }) => {
  await page.goto("/collections");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  await expect(page.getByRole("textbox", { name: "搜索采集库记录" })).toBeVisible();
  await page.evaluate(() => performance.clearResourceTimings());

  await page.waitForTimeout(6_200);
  const periodicRequests = await page.evaluate(() => performance.getEntriesByType("resource")
    .map((entry) => entry.name)
    .filter((name) => [
      "/api/riffloom/tasks",
      "/api/riffloom/libraries/collections",
      "/api/riffloom/integrations/feishu/bindings",
    ].some((path) => name.includes(path))));

  expect(periodicRequests).toEqual([]);
});

test("并发任务中单个任务结束会只刷新一次当前资料库", async ({ page }) => {
  await page.goto("/collections");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);

  let holdNewestTask = true;
  let taskPolls = 0;
  await page.route("**/api/riffloom/tasks", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    const payload = await response.json();
    taskPolls += 1;
    const newest = payload.items[0];
    if (newest) {
      payload.items = [
        {
          ...newest,
          id: "task_e2e_concurrent_blocker",
          status: "running",
          stage: "并发任务仍在执行",
          progress: 50,
          result_refs: [],
          result_summary: {},
          error: null,
        },
        ...payload.items.map((task: { id: string; status: string }, index: number) =>
          index === 0 && holdNewestTask
            ? { ...task, status: "running", stage: "等待下次轮询", progress: 50 }
            : task,
        ),
      ];
      payload.total = payload.items.length;
    }
    await route.fulfill({ response, json: payload });
  });

  const libraryRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/riffloom/libraries/collections")) libraryRequests.push(request.url());
  });
  await page.getByRole("button", { name: "新建采集" }).click();
  const dialog = page.getByRole("dialog", { name: "新建采集" });
  await dialog.getByRole("button", { name: "填入推荐样本" }).click();
  await dialog.getByLabel("我确认有权使用该输入进行采集").check();
  await dialog.getByRole("button", { name: "创建采集任务" }).click();

  await expect.poll(() => taskPolls).toBeGreaterThanOrEqual(1);
  await expect.poll(() => libraryRequests.length).toBeGreaterThanOrEqual(1);
  libraryRequests.length = 0;
  holdNewestTask = false;

  await expect.poll(() => libraryRequests.length, { timeout: 5_000 }).toBe(1);
  await page.waitForTimeout(2_200);
  expect(libraryRequests).toHaveLength(1);
});

test("Agent 委派原创任务后自动加入任务中心", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  const input = page.getByRole("textbox", { name: "任务描述" });
  await input.fill("请原创一篇面向内容新人的工作流介绍");
  await input.press("Enter");

  const taskCenter = page.getByLabel("任务中心", { exact: true });
  await expect(taskCenter).toBeVisible({ timeout: 5_000 });
  await expect(taskCenter.getByRole("article").filter({ hasText: "文案原创" }).first()).toBeVisible();
});

test("飞书字段映射明确单向同步边界", async ({ page }) => {
  await page.goto("/breakdowns");
  await page.getByRole("button", { name: /配置绑定|字段映射/ }).click();
  await expect(page.getByRole("dialog", { name: "同步到飞书多维表格" })).toBeVisible();
  await expect(page.getByText(/飞书编辑不会反向覆盖 Riffloom/)).toBeVisible();
  await expect(page.getByText(/riffloom_record_id/)).toBeVisible();
});

test("个人菜单执行操作后关闭", async ({ page }) => {
  await page.goto("/chat");
  const profile = page.locator(".profile-button");

  await profile.evaluate((button: HTMLButtonElement) => button.click());
  await page.getByRole("button", { name: "智能体与技能" }).click();
  await expect(page.locator(".profile-menu")).toBeHidden();

  await profile.evaluate((button: HTMLButtonElement) => button.click());
  await page.getByRole("button", { name: "飞书连接" }).click();
  await expect(page.getByRole("dialog", { name: "同步到飞书多维表格" })).toBeVisible();
  await expect(page.locator(".profile-menu")).toBeHidden();
});

test("资料库主操作进入对应任务模式", async ({ page }) => {
  await page.goto("/breakdowns");
  await page.getByRole("button", { name: "新建拆解" }).click();
  await expect(page).toHaveURL(/\/chat\?mode=breakdown$/);
  await expect(page.getByRole("tab", { name: "拆解" })).toHaveAttribute("aria-selected", "true");

  await page.goto("/creations");
  await page.getByRole("button", { name: "开始创作" }).click();
  await expect(page).toHaveURL(/\/chat\?mode=creation$/);
  await expect(page.getByRole("tab", { name: "创作" })).toHaveAttribute("aria-selected", "true");
});

test("图标导航保留可访问名称", async ({ page }) => {
  await page.goto("/chat");
  for (const name of ["对话", "采集库", "拆解库", "创作库", "封面设计"]) {
    await expect(page.getByRole("link", { name, exact: true })).toBeVisible();
  }
});

test("页面切换后任务中心固定在右上角", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);

  async function expectRightAligned() {
    const box = await page.getByRole("button", { name: /任务中心/ }).boundingBox();
    expect(box).not.toBeNull();
    expect(page.viewportSize()!.width - (box!.x + box!.width)).toBeLessThanOrEqual(80);
  }

  await expectRightAligned();
  await page.getByRole("link", { name: "采集库", exact: true }).click();
  await expect(page).toHaveURL(/\/collections$/);
  await expectRightAligned();
  await page.getByRole("link", { name: "创作库", exact: true }).click();
  await expect(page).toHaveURL(/\/creations$/);
  await expectRightAligned();
});

test("API 连接失败时不闪现其他工作区的演示数据", async ({ page }) => {
  await page.route("**/api/riffloom/**", (route) => route.abort());
  await page.goto("/collections");

  await expect(page.getByText("示例用户", { exact: true })).toHaveCount(0);
  await expect(page.getByText("示例内容团队", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/从小白到 AI 万粉博主/)).toHaveCount(0);
});

test("弹窗支持焦点闭环、Esc 关闭和背景滚动锁定", async ({ page }) => {
  await page.goto("/collections");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  const trigger = page.getByRole("button", { name: "新建采集" });
  await trigger.click();

  const dialog = page.getByRole("dialog", { name: "新建采集" });
  const close = dialog.getByRole("button", { name: "关闭新建采集" });
  await expect(close).toBeFocused();
  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe("hidden");
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
  await page.keyboard.press("Escape");

  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe("");

  const feishuTrigger = page.getByRole("button", { name: /配置绑定|字段映射/ });
  await feishuTrigger.click();
  const feishuDialog = page.getByRole("dialog", { name: "同步到飞书多维表格" });
  await expect(feishuDialog.getByRole("button", { name: "关闭飞书设置" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(feishuDialog).toBeHidden();
  await expect(feishuTrigger).toBeFocused();

  await trigger.click();
  const collectionDialog = page.getByRole("dialog", { name: "新建采集" });
  await collectionDialog.getByRole("button", { name: "填入推荐样本" }).click();
  await collectionDialog.getByLabel("我确认有权使用该输入进行采集").check();
  await collectionDialog.getByRole("button", { name: "创建采集任务" }).click();
  const detailTrigger = page.getByRole("button", { name: /查看.*详情/ }).first();
  await expect(detailTrigger).toBeVisible({ timeout: 5_000 });
  await detailTrigger.click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByRole("button", { name: "关闭详情" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(detailTrigger).toBeFocused();
});

test("飞书 sandbox 可全量、幂等增量并暂停恢复", async ({ page }) => {
  await page.goto("/collections");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);

  await page.getByRole("button", { name: "新建采集" }).click();
  const collectionDialog = page.getByRole("dialog", { name: "新建采集" });
  await collectionDialog.getByRole("button", { name: "填入推荐样本" }).click();
  await collectionDialog.getByLabel("我确认有权使用该输入进行采集").check();
  await collectionDialog.getByRole("button", { name: "创建采集任务" }).click();
  await expect(collectionDialog).toBeHidden();
  await expect(page.getByLabel("任务中心", { exact: true })).toBeHidden({ timeout: 5_000 });
  await expect(page.getByText(/可复用内容样本 01/).first()).toBeVisible({ timeout: 5_000 });

  const banner = page.getByLabel("飞书同步状态");
  await banner.getByRole("button", { name: /配置绑定|字段映射/ }).click();
  const feishuDialog = page.getByRole("dialog", { name: "同步到飞书多维表格" });
  await expect(feishuDialog.getByText(/0 EXTERNAL CALLS/)).toBeVisible();
  await feishuDialog.getByLabel(/我确认这是 Riffloom/).check();
  await feishuDialog.getByRole("button", { name: /预检并保存绑定|保存映射/ }).click();
  await expect(feishuDialog).toBeHidden();

  const taskCenter = page.getByLabel("任务中心", { exact: true });
  await expect(taskCenter.getByRole("article").filter({ hasText: "飞书单向同步" }).first()).toContainText(/失败 0/, { timeout: 5_000 });
  await taskCenter.getByRole("button", { name: "关闭任务中心" }).click();
  await expect(banner).toContainText(/\d+ 条稳定映射/);
  await expect(banner).toContainText("0 次外部调用");
  await expect(banner).toContainText("已同步");

  await banner.getByRole("button", { name: "增量同步" }).click();
  const incrementalTask = page.getByLabel("任务中心", { exact: true }).getByRole("article").filter({ hasText: "飞书单向同步" }).first();
  await expect(incrementalTask).toContainText(/跳过 [1-9]\d* · 失败 0/, { timeout: 5_000 });
  await page.getByLabel("任务中心", { exact: true }).getByRole("button", { name: "关闭任务中心" }).click();

  await banner.getByRole("button", { name: "暂停" }).click();
  await expect(banner).toContainText("已暂停");
  await expect(banner.getByRole("button", { name: "增量同步" })).toBeDisabled();
  await banner.getByRole("button", { name: "恢复" }).click();
  await expect(banner).toContainText("已同步");
});

test("视频缺少完整文案时保留采集检查点并给出可操作错误", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  await page.getByRole("button", { name: /一键采集仿写/ }).click();
  await page.getByRole("textbox", { name: "任务描述" }).fill("https://www.xiaohongshu.com/explore/video-demo?xsec_token=e2e 请验证视频文案门禁");
  await page.getByRole("button", { name: "发送" }).click();
  const confirmation = page.getByRole("dialog", { name: "一键采集仿写" });
  await confirmation.getByLabel(/我确认有权/).check();
  await confirmation.getByRole("button", { name: "确认并创建任务" }).click();
  await expect(page).toHaveURL(/task=task_/);
  const taskUrl = page.url();
  const taskId = new URL(taskUrl).searchParams.get("task");
  expect(taskId).toBeTruthy();
  const taskCenter = page.getByLabel("任务中心", { exact: true });
  await expect(taskCenter).toBeVisible();
  const taskCard = taskCenter.getByRole("article").filter({ hasText: taskId! });
  await expect(taskCard.getByText(/缺少完整视频文案/)).toBeVisible({ timeout: 5_000 });
  await expect(taskCard.getByRole("link", { name: /查看所在库/ })).toHaveAttribute("href", /\/collections\?record=col_/);
  await page.goto(taskUrl);
  await expect(page.getByRole("main").getByText(/缺少完整视频文案/)).toBeVisible();
});

test("四类合规沙箱采集可提交并进入独立库视图", async ({ page }) => {
  await page.goto("/collections");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接 · mock-v1/);

  const cases = [
    { tab: "单篇内容", library: "单篇采集库", expected: /可复用内容样本 01/ },
    { tab: "关键词搜索", library: "关键词采集库", expected: /可复用内容样本/ },
    { tab: "博主内容", library: "博主内容库", expected: /可复用内容样本/ },
    { tab: "博主信息", library: "博主信息库", expected: /灵感创作者 01/ },
  ] as const;

  for (const item of cases) {
    await page.getByRole("button", { name: "新建采集" }).first().click();
    const dialog = page.getByRole("dialog", { name: "新建采集" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("tab", { name: new RegExp(item.tab) }).click();
    await dialog.getByLabel("我确认有权使用该输入进行采集").check();
    await dialog.getByRole("button", { name: "创建采集任务" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByLabel("任务中心", { exact: true })).toBeHidden({ timeout: 5_000 });
    await page.getByRole("button", { name: item.library }).click();
    await expect(page.getByText(item.expected).first()).toBeVisible({ timeout: 5_000 });
  }

  await page.getByRole("button", { name: "单篇采集库" }).click();
  await page.getByText(/可复用内容样本 01/).first().click();
  const drawer = page.getByRole("dialog", { name: /可复用内容样本 01/ });
  await expect(drawer.getByRole("button", { name: "来源", exact: true })).toHaveCount(0);
  await expect(drawer.getByText("数据环境", { exact: true })).toHaveCount(0);
  await expect(drawer.getByText("类型", { exact: true })).toHaveCount(0);
  await expect(drawer.getByText("单篇采集", { exact: true })).toHaveCount(0);
  await expect(drawer.getByText("原文链接", { exact: true })).toHaveCount(1);
  const originalLink = drawer.getByRole("link", { name: "原文链接" });
  await expect(originalLink).toHaveAttribute("target", "_blank");
  await expect(originalLink).toHaveAttribute("href", /^https?:\/\//);
  await drawer.getByRole("button", { name: "关闭详情" }).click();

  await page.getByRole("button", { name: "关键词采集库" }).click();
  await page.getByText(/AI 工作流：可复用内容样本/).first().click();
  const keywordDrawer = page.getByRole("dialog");
  await keywordDrawer.getByRole("button", { name: /^(发起|查看)拆解$/ }).click();
  await expect(page).toHaveURL(/\/breakdowns\?record=brk_/, { timeout: 5_000 });
  const breakdownDrawer = page.getByRole("dialog");
  await breakdownDrawer.getByRole("button", { name: "继续创作" }).click();
  await expect(page).toHaveURL(/\/chat\?mode=creation/);
  await expect(page.getByLabel("当前来源")).toContainText("采集库 · AI 工作流：可复用内容样本");
});

test("关键词从输入识别视频，并支持五档最热范围", async ({ page }) => {
  await page.goto("/collections");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  await page.getByRole("button", { name: "新建采集" }).click();
  const dialog = page.getByRole("dialog", { name: "新建采集" });
  await dialog.getByRole("tab", { name: /关键词搜索/ }).click();
  const range = dialog.getByRole("combobox", { name: "最热时间范围" });
  await expect(range.locator("option")).toHaveCount(5);
  await expect(range).toHaveValue("week");
  await dialog.getByRole("textbox", { name: "搜索关键词" }).fill("AI 口播短视频");
  await expect(dialog.getByText(/自动识别为视频笔记/)).toBeVisible();
  await range.selectOption("month");
  await dialog.getByLabel("我确认有权使用该输入进行采集").check();
  const request = page.waitForRequest((item) => item.method() === "POST" && item.url().includes("/collection-tasks"));
  await dialog.getByRole("button", { name: "创建采集任务" }).click();
  expect((await request).postDataJSON().query).toMatchObject({
    content_type: "video",
    publish_time: "month",
    sort: "most-liked",
  });
  await expect(page.getByLabel("任务中心", { exact: true })).toBeHidden({ timeout: 5_000 });
});

test("关键词批量采集部分失败后只重试失败项", async ({ page }) => {
  await page.goto("/collections");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  await page.getByRole("button", { name: "新建采集" }).click();
  const dialog = page.getByRole("dialog", { name: "新建采集" });
  await dialog.getByRole("tab", { name: /关键词搜索/ }).click();
  await dialog.getByRole("textbox", { name: "搜索关键词" }).fill("阶段2验收 [partial]");
  await dialog.getByLabel("我确认有权使用该输入进行采集").check();
  const acceptedResponse = page.waitForResponse((response) => response.request().method() === "POST" && response.url().includes("/collection-tasks"));
  await dialog.getByRole("button", { name: "创建采集任务" }).click();

  const taskCenter = page.getByLabel("任务中心", { exact: true });
  const taskId = String((await (await acceptedResponse).json()).id);
  await expect(taskCenter.getByRole("article")).toHaveCount(1);
  const taskCard = taskCenter.getByRole("article").filter({ hasText: taskId! });
  await expect(taskCard.getByText(/失败 1/)).toBeVisible({ timeout: 5_000 });
  await taskCard.getByRole("button", { name: "只重试失败项" }).click();
  await expect(taskCenter).toBeHidden({ timeout: 5_000 });
  await page.getByRole("button", { name: /任务中心/ }).click();
  await expect(page.getByLabel("任务中心", { exact: true }).getByRole("article").filter({ hasText: taskId! }).getByText(/共 20 项 .* 失败 0/)).toBeVisible();
  await page.getByRole("button", { name: "关闭任务中心" }).click();
});

test("二十条采集结果可批量拆解并批量删除", async ({ page }) => {
  await page.goto("/collections");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  await page.getByRole("button", { name: "博主内容库" }).click();
  await page.getByRole("button", { name: "新建采集" }).click();
  const dialog = page.getByRole("dialog", { name: "新建采集" });
  await dialog.getByRole("tab", { name: /博主内容/ }).click();
  await dialog.getByRole("textbox", { name: "博主主页或 ID" }).fill("creator-020");
  await dialog.getByRole("spinbutton", { name: "近期采集条数" }).fill("20");
  await dialog.getByLabel("我确认有权使用该输入进行采集").check();
  await dialog.getByRole("button", { name: "创建采集任务" }).click();
  await expect(page.getByText(/可复用内容样本/).first()).toBeVisible({ timeout: 5_000 });
  const taskCenter = page.getByLabel("任务中心", { exact: true });
  await expect(taskCenter).toBeHidden();

  await page.getByRole("checkbox", { name: "全选当前结果" }).check();
  const toolbar = page.getByRole("toolbar", { name: "采集库批量操作" });
  await expect(toolbar).toContainText("已选 20 条");
  await toolbar.getByRole("button", { name: "批量拆解" }).click();
  const breakdownTasks = page.getByLabel("任务中心", { exact: true }).getByRole("article").filter({ hasText: "爆款拆解" });
  await expect.poll(() => breakdownTasks.count()).toBeGreaterThanOrEqual(20);
  await expect(breakdownTasks.first()).toContainText("拆解结果已入库", { timeout: 8_000 });
  await page.getByRole("button", { name: "关闭任务中心" }).click();

  const rows = page.locator("tbody tr");
  const before = await rows.count();
  await rows.first().getByRole("checkbox").check();
  page.once("dialog", (confirmation) => confirmation.accept());
  await page.getByRole("toolbar", { name: "采集库批量操作" }).getByRole("button", { name: "批量删除" }).click();
  await expect(page.getByRole("status")).toContainText("已删除 1 条采集记录");
  await expect(rows).toHaveCount(before - 1);
});

test("临时正文拆解、引用知识创作和不可变版本可完整审核", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接 · mock-v1/);
  await page.getByRole("tab", { name: "拆解" }).click();
  await page.getByRole("button", { name: /^引用知识/ }).click();
  await expect(page.getByLabel("引用知识库")).toBeVisible();
  await page.getByRole("textbox", { name: "任务描述" }).fill("收藏了很多工具却无法稳定创作，真正缺少的是来源、拆解和版本之间的连接。请拆解这段内容。");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/结构化拆解已完成/)).toBeVisible({ timeout: 5_000 });
  await page.getByRole("button", { name: /任务中心/ }).click();
  const breakdownTask = page.getByLabel("任务中心", { exact: true }).getByRole("article").filter({ hasText: "爆款拆解" }).first();
  await expect(breakdownTask.getByRole("link", { name: "查看所在库" })).toHaveAttribute("href", /\/breakdowns\?record=brk_/);
  await page.getByRole("button", { name: "关闭任务中心" }).click();
  await page.getByRole("button", { name: /查看拆解结果/ }).click();
  const breakdown = page.getByRole("dialog");
  await expect(breakdown.getByText("内容结构")).toBeVisible();
  await expect(breakdown.getByText("爆款原因")).toBeVisible();
  await breakdown.getByRole("button", { name: "继续创作" }).click();

  await expect(page).toHaveURL(/mode=creation/);
  const sourceChips = page.getByLabel("当前来源");
  await expect(sourceChips.getByRole("button", { name: /^移除来源/ })).toHaveCount(1);
  await expect(sourceChips).toContainText("拆解库");
  await sourceChips.getByRole("button", { name: /^移除来源/ }).click();
  await expect(sourceChips).toBeHidden();
  await page.getByRole("button", { name: /^引用知识/ }).click();
  const picker = page.getByLabel("引用知识库");
  await picker.getByRole("button", { name: /拆解库/ }).click();
  await picker.locator(".knowledge-records button").first().click();
  await page.getByRole("textbox", { name: "任务描述" }).fill("面向内容新人写一篇说明三类任务为什么要分别入库的小红书文案。");
  const creationRequest = page.waitForRequest((request) => request.method() === "POST" && request.url().includes("/creation-tasks"));
  await page.getByRole("button", { name: "发送" }).click();
  expect((await creationRequest).postDataJSON().source_ids).toEqual([]);
  await expect(page.getByText(/创作版本已生成/)).toBeVisible({ timeout: 5_000 });
  await page.getByRole("button", { name: /查看创作结果/ }).click();
  const creation = page.getByRole("dialog");
  await expect(creation.getByText(/当前正文 · v1/)).toBeVisible();
  await creation.getByRole("button", { name: "继续修改" }).click();
  const editor = creation.getByRole("textbox", { name: "新版本正文" });
  const base = await editor.inputValue();
  await editor.fill(`${base}\n\n补充一条人工修改，验证旧版本不会被覆盖。`);
  await creation.getByRole("button", { name: "保存为新版本" }).click();
  await expect(page.getByRole("status")).toContainText("已保存为 v2");
  await creation.getByRole("button", { name: "复制正文" }).click();
  await expect(page.getByRole("status")).toContainText("正文已复制");
  await creation.getByRole("button", { name: "版本", exact: true }).click();
  await expect(creation.getByText(/v2.*当前版本/)).toBeVisible();
  await expect(creation.getByText("v1", { exact: true })).toBeVisible();
  await creation.getByRole("button", { name: "确认采用当前版本" }).click();
  await expect(page.getByRole("status")).toContainText("v2 已通过 API 确认采用");
});

test("近期外部信号可生成五维热点并继续创作", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  await page.getByRole("tab", { name: "热点" }).click();
  await page.getByRole("textbox", { name: "任务描述" }).fill("AI 工作流");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/已根据 TikHub 近 7 天/)).toBeVisible({ timeout: 5_000 });
  await page.getByRole("button", { name: /任务中心/ }).click();
  const trendTask = page.getByLabel("任务中心", { exact: true }).getByRole("article").filter({ hasText: "爆款选题指导" }).first();
  await trendTask.getByRole("link", { name: "查看回复" }).click();
  await expect(page).toHaveURL(/\/chat\?task=task_/);
  await expect(page.getByRole("tablist", { name: "对话功能" })).toHaveCount(0);
  const topics = page.getByLabel("选题建议");
  await expect(topics).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await expect(topics).toHaveCSS("box-shadow", "none");
  await expect(topics.getByText("为什么火").first()).toBeVisible();
  await expect(topics.getByText("目标人群").first()).toBeVisible();
  await expect(topics.getByText("核心价值").first()).toBeVisible();
  await expect(topics.getByText("标题建议").first()).toBeVisible();
  await expect(topics.getByText("我的切入点").first()).toBeVisible();
  await topics.getByRole("button", { name: /用这个选题继续创作/ }).first().click();
  await expect(page.getByRole("textbox", { name: "任务描述" })).toHaveValue(/请围绕标题/);

  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/创作版本已生成/)).toBeVisible({ timeout: 5_000 });
  await page.getByRole("button", { name: /查看创作结果/ }).click();
  const creation = page.getByRole("dialog");
  await creation.getByRole("button", { name: "来源", exact: true }).click();
  await expect(creation.getByText(/task_[a-z0-9]+/).first()).toBeVisible();
});

test("授权素材可生成四个封面方案、创建新修改版本并保存", async ({ page }) => {
  await page.goto("/covers");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  await expect(page.getByText(/mock-cover-v1 · 0 次外部图片调用/)).toBeVisible();

  await page.getByLabel("选择封面素材").setInputFiles({
    name: "authorized-source.png",
    mimeType: "image/png",
    buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64"),
  });
  await page.getByLabel("我确认拥有必要使用权").check();
  await page.getByRole("button", { name: "校验并添加" }).click();
  await expect(page.getByRole("img", { name: "素材 authorized-source.png" })).toBeVisible();

  await page.getByRole("textbox", { name: "封面设计要求" }).fill("制作一张重点清晰的 AI 工作流知识卡片封面");
  await page.getByRole("button", { name: "生成封面" }).click();
  const results = page.getByRole("region", { name: "封面方案" });
  await expect(results.getByRole("img", { name: /封面方案/ })).toHaveCount(4, { timeout: 5_000 });
  await expect(results).toContainText("4 / 4 个成功");

  await results.getByRole("button", { name: "继续修改" }).first().click();
  await page.getByRole("textbox", { name: "封面修改要求" }).fill("标题再醒目一些，同时减少背景装饰元素");
  await page.getByRole("button", { name: "生成修改方案" }).click();
  await expect(results.getByText("第 1 轮修改方案")).toBeVisible({ timeout: 5_000 });
  await expect(results.getByRole("img", { name: /封面方案/ })).toHaveCount(4);

  await results.getByRole("button", { name: "保存" }).first().click();
  await expect(page.getByRole("status")).toContainText("方案 1 已保存");
  await expect(results.getByRole("button", { name: "已保存" }).first()).toBeDisabled();
});

test("资料库单条记录可确认后删除", async ({ page }) => {
  await page.goto("/chat");
  await expect(page.locator(".runtime-state")).toHaveText(/API 已连接/);
  await page.getByRole("tab", { name: "创作" }).click();
  await page.getByRole("textbox", { name: "任务描述" }).fill("写一条用于验收单条删除的测试文案");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/创作版本已生成/)).toBeVisible({ timeout: 5_000 });
  await page.getByRole("button", { name: /查看创作结果/ }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByRole("button", { name: "删除", exact: true })).toBeVisible();
  page.once("dialog", (confirmation) => confirmation.accept());
  await drawer.getByRole("button", { name: "删除", exact: true }).click();
  await expect(drawer).toBeHidden();
  await expect(page.getByRole("status")).toContainText("记录已删除");
});
