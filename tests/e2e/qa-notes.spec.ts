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
  const editor = page.getByLabel('笔记正文')
  await editor.fill('# 本地草稿\n\n尚未覆盖服务器。')
  await page.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText('笔记已在其他位置更新')).toBeVisible()
  await expect(editor).toHaveValue('# 本地草稿\n\n尚未覆盖服务器。')
  await page.getByRole('button', { name: '载入服务器版本' }).click()
  await expect(editor).toHaveValue('# 进程管理\n\n服务器上的最新正文。')
})

test('demo lab exposes only redacted engineering trace fields', async ({ page }) => {
  await page.getByRole('link', { name: 'Lab' }).click()
  await expect(page.getByRole('heading', { name: '工程链路' })).toBeVisible()
  await expect(page.getByText('BAAI/bge-m3')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Dense' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'BM25' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'RRF' })).toBeVisible()
  await expect(page.getByText('片段 91f2c9a31bb0')).toBeVisible()
  await expect(page.getByText(/api[_-]?key|object[_-]?key|system prompt/i)).toHaveCount(0)
})
