import { expect, test } from '@playwright/test'

import { installMockApi } from './mockApi'

test.beforeEach(async ({ page }, testInfo) => {
  await installMockApi(page, {
    includeNoteEligibilityDriftDocuments: true,
    noteBatchPollsBeforeSuccess: testInfo.title.includes('reload') ? 8 : 3,
    providerAvailable: false,
  })
  await page.goto('/')
})

test('creates a merged note batch and opens the generated note', async ({ page }) => {
  await page.getByRole('link', { name: '笔记' }).first().click()
  await page.getByRole('button', { name: '新建笔记' }).click()

  const dialog = page.getByRole('dialog', { name: '新建笔记' })
  await expect(dialog.getByText('进程与线程.pdf')).toBeVisible()
  await expect(dialog.getByText('调度算法.md')).toBeVisible()
  await expect(dialog.getByText('文件系统.pptx')).toHaveCount(0)
  await expect(dialog.getByText('扫描试题.png')).toHaveCount(0)
  await expect(dialog.getByText('题库.pdf')).toHaveCount(0)
  await expect(dialog.getByText('未索引.pdf')).toHaveCount(0)
  await expect(dialog.getByText('伪装资料.pdf')).toHaveCount(0)
  await expect(dialog.getByText('旧版课件.ppt')).toHaveCount(0)
  await expect(dialog.getByRole('radio', { name: /考前速记/ })).toBeChecked()
  await expect(dialog.getByText('最短 · 最多 12 条')).toBeVisible()
  await expect(dialog.getByText('中等 · 最多 30 条')).toBeVisible()
  await expect(dialog.getByText('最长 · 最多 40 条 \/ 12,000 字符')).toBeVisible()
  await expect(dialog.getByLabel('考前速记结构示例')).toContainText('资料名称')
  await expect(dialog.getByLabel('考前速记结构示例')).toContainText('• 高频定义或公式')
  await expect(dialog.getByLabel('考前速记结构示例')).toContainText('• 关键条件与区别')
  await expect(dialog.getByLabel('考前速记结构示例')).toHaveAttribute('aria-current', 'true')
  await dialog.getByLabel('章节路径（可选）').fill('期末 / 操作系统')
  await dialog.getByLabel('标题（可选）').fill('期末复习')
  await dialog.getByRole('button', { name: '创建' }).click()

  const progress = page.getByLabel('笔记生成进度')
  await expect(progress).toContainText('running')
  await expect(progress).toContainText('generating')
  await expect(progress).toContainText('7 秒')
  await expect(progress).toContainText('succeeded')

  const generatedEntry = page.getByRole('button', { name: /期末复习/ })
  await expect(generatedEntry).toHaveAttribute('aria-current', 'page')
  const preview = page.getByLabel('笔记阅读视图')
  await expect(preview.getByRole('heading', { name: '期末复习' })).toBeVisible()
  await expect(preview).toContainText('线程是调度的基本单位。')

  await page.getByRole('button', { name: '编辑' }).click()
  await expect(page.getByLabel('笔记正文')).toHaveValue(/进程与线程\.pdf/)
  await page.getByRole('button', { name: '阅读' }).click()
  await expect(preview).toBeVisible()

  const widths = await page.evaluate(() => ({
    viewport: window.innerWidth,
    body: document.body.scrollWidth,
    root: document.documentElement.scrollWidth,
  }))
  expect(widths.body).toBeLessThanOrEqual(widths.viewport + 1)
  expect(widths.root).toBeLessThanOrEqual(widths.viewport + 1)
})

test('selected note template changes the generated preview', async ({ page }) => {
  await page.getByRole('link', { name: '笔记' }).first().click()
  await page.getByRole('button', { name: '新建笔记' }).click()

  const dialog = page.getByRole('dialog', { name: '新建笔记' })
  await dialog.getByRole('radio', { name: /结构提纲/ }).check()
  await expect(dialog.getByLabel('考前速记结构示例')).not.toHaveAttribute('aria-current')
  await expect(dialog.getByLabel('结构提纲结构示例')).toHaveAttribute('aria-current', 'true')
  await expect(dialog.getByLabel('结构提纲结构示例')).toContainText('1. 资料名称')
  await expect(dialog.getByLabel('结构提纲结构示例')).toContainText('1.1 来源位置')
  await expect(dialog.getByLabel('结构提纲结构示例')).toContainText('1. 关键知识点')
  await dialog.getByLabel('标题（可选）').fill('结构化复习')
  await dialog.getByRole('button', { name: '创建' }).click()

  await expect(page.getByLabel('笔记生成进度')).toContainText('succeeded')
  const preview = page.getByLabel('笔记阅读视图')
  await expect(preview).toContainText('笔记模板: 结构提纲')
  await expect(preview).toContainText('进程与线程')
  await expect(preview).toContainText('死锁处理')
  await expect(preview).not.toContainText('线程是调度的基本单位。')
})

test('regenerates a workflow note through a batch and opens the new output', async ({ page }) => {
  await page.getByRole('link', { name: '笔记' }).first().click()
  await page.getByRole('button', { name: '新建笔记' }).click()

  const dialog = page.getByRole('dialog', { name: '新建笔记' })
  await dialog.getByLabel('标题（可选）').fill('异步重生成验证')
  await dialog.getByRole('button', { name: '创建' }).click()
  const progress = page.getByLabel('笔记生成进度')
  await expect(progress).toContainText('succeeded')

  await page.getByRole('button', { name: '重新生成' }).click()
  await expect(page.getByRole('button', { name: '重新生成' })).toBeDisabled()
  await expect(progress).toContainText('running')
  await expect(progress).toContainText('succeeded')

  await expect(page.getByLabel('笔记阅读视图')).toContainText('已通过异步批次重新生成。')
  const entries = page.getByRole('button', { name: /异步重生成验证/ })
  await expect(entries).toHaveCount(1)
  await expect(entries).toHaveAttribute('aria-current', 'page')
  await expect(page.getByText('版本 2 · 生成 2')).toBeVisible()

  const repeatedTerminalRead = await page.evaluate(async () => {
    await fetch('/api/v1/note-batches/note-regeneration-batch-e2e')
    await fetch('/api/v1/note-batches/note-regeneration-batch-e2e')
    const response = await fetch('/api/v1/courses/course-e2e/notes')
    const notes = (await response.json()) as Array<{
      title: string
      version: number
      generation: number
    }>
    const regenerated = notes.find((note) => note.title === '异步重生成验证')
    return regenerated
      ? { version: regenerated.version, generation: regenerated.generation }
      : null
  })
  expect(repeatedTerminalRead).toEqual({ version: 2, generation: 2 })
})

test('restores a running note batch after reload', async ({ page }) => {
  await page.getByRole('link', { name: '笔记' }).first().click()
  await page.getByRole('button', { name: '新建笔记' }).click()
  const dialog = page.getByRole('dialog', { name: '新建笔记' })
  await dialog.getByLabel('标题（可选）').fill('刷新恢复验证')
  await dialog.getByRole('button', { name: '创建' }).click()

  const progress = page.getByLabel('笔记生成进度')
  await expect(progress).toContainText('running')
  await expect(page.getByRole('button', { name: '新建笔记' })).toBeDisabled()
  await expect
    .poll(() =>
      page.evaluate(() => localStorage.getItem('study-agent.note-batch:course-e2e')),
    )
    .toBe('note-batch-e2e')

  await page.reload()

  await expect(page.getByLabel('笔记生成进度')).toContainText('running')
  await expect(page.getByRole('button', { name: '新建笔记' })).toBeDisabled()
  await expect(page.getByLabel('笔记生成进度')).toContainText('succeeded')
  await expect(page.getByRole('button', { name: '新建笔记' })).toBeEnabled()
  await expect(page.getByRole('button', { name: /刷新恢复验证/ })).toHaveAttribute(
    'aria-current',
    'page',
  )
})
