import { expect, test } from '@playwright/test'

import { installMockApi } from './mockApi'

test.beforeEach(async ({ page }) => {
  await installMockApi(page)
  await page.goto('/')
})

test('answered and abstained queries remain visibly distinct', async ({ page }) => {
  await page.getByRole('link', { name: '问答' }).first().click()
  const composer = page.getByLabel('课程问题')
  await composer.fill('什么是进程？')
  await page.getByRole('button', { name: '提交问题' }).click()
  await expect(page.getByText('已有来源')).toBeVisible()
  await expect(page.getByText('进程是资源分配的基本单位，线程是调度的基本单位。')).toBeVisible()

  await page.getByRole('button', { name: /进程页面\.png/ }).click()
  await expect(page.getByRole('dialog', { name: '来源' })).toBeVisible()
  await expect(page.getByText('引用原文')).toBeVisible()
  await expect(page.getByRole('img', { name: '进程页面.png 页面 6' })).toBeVisible()
  await expect(page.locator('.bbox-highlight')).toHaveCount(1)
  await page.getByRole('button', { name: '关闭' }).click()

  await composer.fill('课件外的量子问题')
  await page.getByRole('button', { name: '提交问题' }).click()
  await expect(page.getByText('依据不足')).toBeVisible()
  await expect(page.getByText('当前课程资料没有足够依据。')).toBeVisible()
})

test('note conflict preserves the draft until the user reloads', async ({ page }) => {
  await page.getByRole('link', { name: '笔记' }).first().click()
  await expect(page.getByLabel('笔记阅读视图')).toBeVisible()
  await page.getByRole('button', { name: '编辑' }).click()
  const editor = page.getByLabel('笔记正文')
  await editor.fill('# 本地草稿\n\n尚未覆盖服务器。')
  await page.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText('笔记已在其他位置更新')).toBeVisible()
  await expect(editor).toHaveValue('# 本地草稿\n\n尚未覆盖服务器。')
  await page.getByRole('button', { name: '载入服务器版本' }).click()
  await expect(page.getByLabel('笔记阅读视图')).toContainText('服务器上的最新正文。')
})

test('ordinary workspace exposes the knowledge graph instead of engineering trace', async ({ page }) => {
  await page.getByRole('link', { name: '知识图谱' }).first().click()
  await expect(page.getByRole('heading', { name: '课程知识图谱' })).toBeVisible()
  await expect(page.getByLabel('课程知识图谱画布')).toBeVisible()
  await expect(page.getByText('RRF')).toHaveCount(0)
  await expect(page.getByText('Tokenizer')).toHaveCount(0)
})
