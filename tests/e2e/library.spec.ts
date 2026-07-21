import { expect, test } from '@playwright/test'

import { installMockApi } from './mockApi'

test.beforeEach(async ({ page }) => {
  await installMockApi(page, { providerAvailable: false })
  await page.goto('/')
})

test('library exposes parse/index states and no-provider boundary', async ({ page }) => {
  await expect(page.getByRole('heading', { name: '课程资料' })).toBeVisible()
  await expect(page.getByText('仅资料模式')).toBeVisible()
  await expect(page.getByText('可问答')).toBeVisible()
  await expect(page.getByText('待索引')).toBeVisible()
  await expect(page.getByText('部分失败')).toBeVisible()

  await page.getByRole('link', { name: '问答' }).click()
  await expect(page.getByText('Provider 未配置')).toBeVisible()
  await expect(page.getByLabel('课程问题')).toBeDisabled()
  await expect(page.getByRole('button', { name: '提交问题' })).toBeDisabled()
  await page.getByRole('link', { name: '资料' }).click()

  await page.getByRole('button', { name: '重试失败页' }).click()
  await expect(page.getByText('等待 Worker')).toBeVisible()

  await page.getByRole('button', { name: '删除资料' }).first().click()
  await expect(page.getByRole('dialog', { name: /删除/ })).toBeVisible()
  await page.getByRole('button', { name: '确认删除' }).click()
  await expect(page.getByText('资料已不可访问，后台清理完成。')).toBeVisible()
})

test('upload performs the declared three-step flow', async ({ page }) => {
  await page.getByRole('button', { name: '添加资料' }).click()
  await page.locator('input[type="file"]').setInputFiles({
    name: '新资料.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.7\nfixture'),
  })
  await page.getByRole('button', { name: '上传' }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(page.getByText('新资料.pdf')).toBeVisible()
  await expect(page.getByText('等待 Worker')).toBeVisible()
})
