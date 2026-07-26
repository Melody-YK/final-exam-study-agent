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

  const studyActions = page.getByRole('region', { name: '学习就绪入口' })
  await expect(studyActions.getByRole('listitem', { name: '可学习 1' })).toBeVisible()
  await expect(studyActions.getByRole('listitem', { name: '待审核 1' })).toBeVisible()
  await expect(studyActions.getByRole('listitem', { name: '准备中 0' })).toBeVisible()
  await expect(studyActions.getByRole('listitem', { name: '需要处理 1' })).toBeVisible()
  await expect(studyActions.getByRole('link', { name: '查看概念地图' })).toHaveAttribute(
    'href',
    '/graph',
  )
  await expect(studyActions.getByRole('link', { name: '生成复习笔记' })).toHaveAttribute(
    'href',
    '/notes',
  )
  await expect(studyActions.getByRole('link', { name: '开始问答' })).toHaveCount(0)
  await expect(studyActions.getByText('问答服务不可用')).toBeVisible()

  await studyActions.getByRole('link', { name: '查看概念地图' }).click()
  await expect(page.getByRole('heading', { name: '课程概念地图' })).toBeVisible()
  await page.getByRole('link', { name: '资料' }).click()
  await expect(page.getByRole('heading', { name: '课程资料' })).toBeVisible()

  await page.getByRole('link', { name: '问答' }).click()
  await expect(page.getByText('Provider 未配置')).toBeVisible()
  await expect(page.getByLabel('课程问题')).toBeDisabled()
  await expect(page.getByRole('button', { name: '提交问题' })).toBeDisabled()
  await page.getByRole('link', { name: '资料' }).click()
  await expect(page.getByRole('heading', { name: '课程资料' })).toBeVisible()

  await page.getByRole('button', { name: '重试失败页' }).click()
  await expect(page.getByText('等待 Worker')).toBeVisible()

  await page.getByRole('button', { name: '删除资料' }).first().click()
  await expect(page.getByRole('dialog', { name: /删除/ })).toBeVisible()
  await page.getByRole('button', { name: '确认删除' }).click()
  await expect(page.getByText('资料已不可访问，后台清理完成。')).toBeVisible()
})

test('Markdown upload performs the declared three-step flow with a normalized MIME', async ({ page }) => {
  let declaredMediaType: string | undefined
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      request.url().endsWith('/api/v1/courses/course-e2e/documents')
    ) {
      declaredMediaType = (request.postDataJSON() as { media_type?: string }).media_type
    }
  })
  await page.getByRole('button', { name: '添加资料' }).click()
  await page.locator('input[type="file"]').setInputFiles({
    name: '复习提纲.md',
    mimeType: '',
    buffer: Buffer.from('# 复习提纲\n\n进程调度。'),
  })
  await page.getByRole('button', { name: '上传' }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(page.getByText('复习提纲.md')).toBeVisible()
  await expect(page.getByText('等待 Worker')).toBeVisible()
  expect(declaredMediaType).toBe('text/markdown')
})

test('unsupported presentation is rejected before an upload request', async ({ page }) => {
  let documentCreateRequests = 0
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      request.url().endsWith('/api/v1/courses/course-e2e/documents')
    ) {
      documentCreateRequests += 1
    }
  })

  await page.getByRole('button', { name: '添加资料' }).click()
  const dialog = page.getByRole('dialog', { name: '添加资料' })
  await dialog.locator('input[type="file"]').setInputFiles({
    name: '课程课件.pptx',
    mimeType: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    buffer: Buffer.from('PK\u0003\u0004fixture'),
  })

  await expect(dialog.getByRole('alert')).toContainText('文件格式不支持')
  await expect(dialog.getByRole('alert')).toContainText('请选择 PDF、Markdown')
  await expect(dialog.getByRole('button', { name: '上传' })).toBeDisabled()
  expect(documentCreateRequests).toBe(0)
})
