import { expect, test, type Page } from "@playwright/test";

import { installMockApi } from "./mockApi";

async function selectFirstScope(page: Page) {
  await page.locator("label.learning-unit").first().click();
}

test.beforeEach(async ({ page }) => {
  await installMockApi(page);
  await page.goto("/");
  await page.getByRole("link", { name: "练习" }).first().click();
  await selectFirstScope(page);
});

test("learning loop completes a batch, shows evidence, and exposes the next review action", async ({
  page,
}) => {
  await expect(page.getByRole("heading", { name: "主动练习" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "从哪些内容开始？" }),
  ).toBeVisible();

  await page.getByRole("button", { name: /开始生成题目/ }).click();
  await expect(page.getByText("题目已就绪")).toBeVisible();
  await page.getByRole("button", { name: /开始作答/ }).click();

  await expect(page.getByText("进程在系统中主要承担什么职责？")).toBeVisible();
  await page
    .locator("label.learning-option")
    .filter({ hasText: "进程负责资源分配" })
    .click();
  await page.getByRole("button", { name: /提交答案/ }).click();
  await expect(page.getByText("回答正确")).toBeVisible();
  await page.getByRole("button", { name: "查看证据原文" }).click();
  await expect(page.getByRole("blockquote")).toContainText(
    "进程是资源分配的基本单位。",
  );
  await expect(page.getByText("操作系统课程.pdf")).toBeVisible();
  await page.getByRole("button", { name: "关闭" }).click();
  await page.getByRole("button", { name: "下一题" }).click();

  await expect(
    page.getByText("线程在调度模型中通常承担什么职责？"),
  ).toBeVisible();
  await page.getByRole("button", { name: "上一题" }).click();
  await expect(page.getByText("回答正确")).toBeVisible();
  await expect(
    page.getByRole("radio", { name: "进程负责资源分配" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "下一题" }).click();
  await page
    .locator("label.learning-option")
    .filter({ hasText: "进程负责资源分配" })
    .click();
  await page.getByRole("button", { name: /提交答案/ }).click();
  await expect(page.getByText("需要再巩固")).toBeVisible();
  await page.getByRole("button", { name: "完成练习" }).click();

  await expect(page.getByRole("heading", { name: "结果已保存" })).toBeVisible();
  await expect(
    page.getByText("继续复习进程调度，巩固刚才答错或待复习的单元。"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "开始复习" })).toBeEnabled();

  await page.getByRole("button", { name: "开始复习" }).click();
  await expect(page.getByRole("heading", { name: "练习准备" })).toBeVisible();
  await expect(page.getByText("题目已就绪")).toBeVisible();
});

test("restores an unsubmitted answer after refreshing the practice page", async ({
  page,
}) => {
  await page.getByRole("button", { name: /开始生成题目/ }).click();
  await expect(page.getByText("题目已就绪")).toBeVisible();
  await page.getByRole("button", { name: /开始作答/ }).click();

  const option = page.getByRole("radio", { name: "进程负责资源分配" });
  await option.check();
  await page.reload();

  await expect(page.getByText("进程在系统中主要承担什么职责？")).toBeVisible();
  await expect(option).toBeChecked();
});

test("keeps drafts across questions and exposes a resume action after leaving practice", async ({
  page,
}) => {
  await page.getByRole("button", { name: /开始生成题目/ }).click();
  await expect(page.getByText("题目已就绪")).toBeVisible();
  await page.getByRole("button", { name: /开始作答/ }).click();

  await page.getByRole("radio", { name: "线程负责资源分配" }).check();
  await page.getByRole("button", { name: "下一题" }).click();
  await page.getByRole("radio", { name: "进程负责资源分配" }).check();
  await page.getByRole("button", { name: "上一题" }).click();
  await expect(
    page.getByRole("radio", { name: "线程负责资源分配" }),
  ).toBeChecked();

  await page.getByRole("button", { name: "返回学习台" }).click();
  await expect(page.getByRole("button", { name: "继续上次练习" })).toBeVisible();
  await page.getByRole("button", { name: "继续上次练习" }).click();
  await expect(
    page.getByRole("radio", { name: "线程负责资源分配" }),
  ).toBeChecked();
});

test("asks AI for a source-bound hint before submitting", async ({ page }) => {
  await page.getByRole("button", { name: /开始生成题目/ }).click();
  await expect(page.getByText("题目已就绪")).toBeVisible();
  await page.getByRole("button", { name: /开始作答/ }).click();

  await page.getByRole("button", { name: "问 AI" }).click();
  await page.getByLabel("向 AI 提问").fill("给我一点提示");
  await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.getByText("先比较题干强调的是资源归属还是执行调度。")).toBeVisible();
  await page.getByRole("button", { name: "关闭" }).click();
  await expect(page.getByText("已使用 AI 提示")).toBeVisible();
});

test("Provider unavailable disables new generation and stale source stays unavailable", async ({
  page,
}) => {
  await page.goto("/");
  await installMockApi(page, { providerAvailable: false });
  await page.goto("/learning");
  await expect(page.getByText("Provider 不可用")).toBeVisible();
  await expect(
    page.getByRole("button", { name: /开始生成题目/ }),
  ).toBeDisabled();
});

test("partial batches remain startable", async ({ page }) => {
  await page.goto("/");
  await installMockApi(page, { practiceBatchPartialSuccess: true });
  await page.goto("/learning");
  await selectFirstScope(page);
  await page.getByRole("button", { name: /开始生成题目/ }).click();
  await expect(page.getByText("部分完成")).toBeVisible();
  await page.getByRole("button", { name: /开始作答/ }).click();

  await page
    .locator("label.learning-option")
    .filter({ hasText: "进程负责资源分配" })
    .click();
  await page.getByRole("button", { name: /提交答案/ }).click();
  await page.getByRole("button", { name: /完成练习/ }).click();
  await expect(page.getByRole("heading", { name: "结果已保存" })).toBeVisible();
});

test("stale questions fail closed without submitting an attempt", async ({
  page,
}) => {
  await page.goto("/");
  await installMockApi(page, { learningQuestionStale: true });
  await page.goto("/learning");
  await selectFirstScope(page);
  await page.getByRole("button", { name: /开始生成题目/ }).click();
  await page.getByRole("button", { name: /开始作答/ }).click();
  await page
    .locator("label.learning-option")
    .filter({ hasText: "进程负责资源分配" })
    .click();
  await page.getByRole("button", { name: /提交答案/ }).click();
  await page.getByRole("button", { name: "下一题" }).click();
  await expect(page.getByText("题目来源已失效")).toBeVisible();
  await expect(page.getByRole("button", { name: "跳过此题" })).toBeVisible();
});
