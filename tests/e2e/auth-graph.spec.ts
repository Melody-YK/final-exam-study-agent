import { expect, test } from '@playwright/test'

import { installMockApi } from './mockApi'

test('anonymous user can log in and reach the study workspace', async ({ page }) => {
  await installMockApi(page, {
    authenticated: false,
    seedCourseSelection: false,
  })
  await page.goto('/')

  await expect(page.getByRole('heading', { name: '登录 Finals Desk' })).toBeVisible()
  await page.getByLabel('邮箱').fill('student@example.com')
  await page.getByLabel('密码').fill('correct-password')
  await page.getByRole('button', { name: '登录' }).click()

  await expect(page.getByRole('heading', { name: '选择课程' })).toBeVisible()
  await page.getByRole('button', { name: /操作系统/ }).click()
  await expect(page.getByRole('heading', { name: '课程资料' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '学习视图' })).toBeVisible()
})

test('an invited user can register and enter the study workspace', async ({ page }) => {
  await installMockApi(page, {
    authenticated: false,
    seedCourseSelection: false,
  })
  await page.goto('/register')

  await expect(page.getByRole('heading', { name: '开始你的复习工作区' })).toBeVisible()
  await page.getByLabel('邀请码').fill('e2e-invite-code-123456')
  await page.getByLabel('姓名').fill('受邀同学')
  await page.getByLabel('邮箱').fill('invited@example.com')
  await page.getByLabel('密码').fill('correct-password')
  const registrationRequest = page.waitForRequest(
    (request) => request.method() === 'POST' && request.url().endsWith('/api/v1/auth/register'),
  )
  await page.getByRole('button', { name: '创建账号' }).click()

  expect((await registrationRequest).postDataJSON()).toEqual({
    invite_code: 'e2e-invite-code-123456',
    display_name: '受邀同学',
    email: 'invited@example.com',
    password: 'correct-password',
  })
  await expect(page.getByRole('heading', { name: '选择课程' })).toBeVisible()
  await page.getByRole('button', { name: /操作系统/ }).click()
  await expect(page.getByRole('heading', { name: '课程资料' })).toBeVisible()
})

test('ordinary users cannot open the admin console', async ({ page }) => {
  await installMockApi(page, { accountRole: 'user' })
  await page.goto('/admin')

  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('heading', { name: '课程资料' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '管理控制台' })).toHaveCount(0)
  await expect(page.getByText('待管理员审核')).toBeVisible()

  const forbiddenStatuses = await page.evaluate(async () => {
    const responses = await Promise.all([
      fetch('/api/v1/admin/documents'),
      fetch('/api/v1/admin/documents/document-preview/content'),
      fetch('/api/v1/admin/documents/document-preview/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          review_status: 'approved',
          review_note: null,
        }),
      }),
    ])
    return responses.map((response) => response.status)
  })
  expect(forbiddenStatuses).toEqual([403, 403, 403])
})

test('admin can update another users access controls and inspect engineering diagnostics', async ({
  page,
}) => {
  await installMockApi(page, { accountRole: 'admin' })
  await page.goto('/admin')

  await expect(page.getByRole('heading', { name: '运行概览' })).toBeVisible()
  await page.getByRole('link', { name: '用户' }).click()
  await expect(page.getByRole('heading', { name: '用户与访问' })).toBeVisible()
  await expect(page.getByText('admin@example.com')).toBeVisible()
  const studentRow = page.getByRole('row').filter({ hasText: 'student@example.com' })
  await studentRow.getByRole('button', { name: '管理 复习同学' }).click()
  const userDialog = page.getByRole('dialog', { name: '管理 复习同学' })
  await userDialog.getByLabel('角色').selectOption('admin')
  await userDialog.getByLabel('账号状态').selectOption('suspended')
  await userDialog.getByLabel('管理备注').fill('演示期间暂停访问')
  await userDialog.getByRole('button', { name: '保存' }).click()

  await expect(userDialog).not.toBeVisible()
  await expect(studentRow).toContainText('管理员')
  await expect(studentRow).toContainText('已停用')
  await expect(studentRow).toContainText('演示期间暂停访问')

  await page.getByRole('link', { name: '工程诊断' }).click()
  await expect(page.getByRole('heading', { name: '工程链路' })).toBeVisible()
  await expect(page.getByText('检索候选')).toBeVisible()
  await expect(page.getByText('BAAI/bge-m3')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Dense' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'BM25' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'RRF' })).toBeVisible()
  await expect(page.getByText('片段 91f2c9a31bb0')).toBeVisible()
  await expect(page.getByText(/api[_-]?key|object[_-]?key|system prompt/i)).toHaveCount(0)
})

test('admin can inspect the original upload and reject a pending document', async ({ page }) => {
  await installMockApi(page, { accountRole: 'admin' })
  await page.goto('/admin/reviews')

  await expect(page.getByRole('heading', { name: '资料审核' })).toBeVisible()
  const documentRow = page.getByRole('row').filter({ hasText: '文件系统.pptx' })
  await expect(documentRow).toContainText('待审核')
  const previewLink = documentRow.getByRole('link', {
    name: '预览 文件系统.pptx',
  })
  await expect(previewLink).toHaveAttribute(
    'href',
    '/api/v1/admin/documents/document-preview/content',
  )
  const previewHref = await previewLink.getAttribute('href')
  const previewResponse = await page.evaluate(async (href) => {
    const response = await fetch(href!)
    return {
      status: response.status,
      contentType: response.headers.get('content-type'),
      disposition: response.headers.get('content-disposition'),
    }
  }, previewHref)
  expect(previewResponse).toEqual({
    status: 200,
    contentType: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    disposition: "inline; filename*=UTF-8''%E6%96%87%E4%BB%B6%E7%B3%BB%E7%BB%9F.pptx",
  })

  const reviewRequest = page.waitForRequest(
    (request) =>
      request.method() === 'POST' &&
      request.url().endsWith('/api/v1/admin/documents/document-preview/review'),
  )
  await documentRow.getByRole('button', { name: '拒绝 文件系统.pptx' }).click()
  const dialog = page.getByRole('dialog', { name: '拒绝资料' })
  await dialog.getByLabel('拒绝原因').fill('课件内容与课程无关')
  await dialog.getByRole('button', { name: '确认拒绝' }).click()

  expect((await reviewRequest).postDataJSON()).toEqual({
    review_status: 'rejected',
    review_note: '课件内容与课程无关',
  })
  await expect(dialog).not.toBeVisible()
  await expect(page.getByText('没有待审核资料')).toBeVisible()

  await page.getByRole('tab', { name: '未通过' }).click()
  const rejectedRow = page.getByRole('row').filter({ hasText: '文件系统.pptx' })
  await expect(rejectedRow).toContainText('未通过')
  await expect(rejectedRow).toContainText('课件内容与课程无关')
})

test('concept map focuses relationships and prepares a fresh QA draft', async ({ page }) => {
  await installMockApi(page)
  await page.goto('/graph')

  await expect(page.getByRole('heading', { name: '课程概念地图' })).toBeVisible()
  await expect(page.getByLabel('课程概念地图画布')).toBeVisible()
  await page.locator('.knowledge-node--concept').filter({ hasText: '进程' }).click()

  const details = page.getByRole('complementary', { name: '节点详情' })
  await expect(details.getByRole('heading', { name: '进程' })).toBeVisible()
  await expect(
    details.getByText('资料“进程与线程.pdf”包含概念“进程”9 次。'),
  ).toBeVisible()
  await expect(
    details.getByText('概念“进程”和“调度”共同出现在 4 个内容片段中。'),
  ).toBeVisible()
  await expect(details.getByText('进程与线程.pdf', { exact: true })).toBeVisible()
  await expect(details.getByText(/第 6 页/)).toBeVisible()
  await expect(details.getByText('进程是资源分配的基本单位，线程是调度的基本单位。')).toBeVisible()
  await expect(page.getByText('Tokenizer')).toHaveCount(0)
  await expect(page.getByText(/Chunk/)).toHaveCount(0)

  await page.getByRole('button', { name: '仅看关联' }).click()
  await expect(page.locator('.knowledge-node')).toHaveCount(3)
  await expect(page.locator('.knowledge-node--course')).toHaveCount(0)

  const queryRequests: string[] = []
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      request.url().endsWith('/api/v1/courses/course-e2e/queries')
    ) {
      queryRequests.push(request.url())
    }
  })
  await details.getByRole('button', { name: '围绕此概念提问' }).click()

  await expect(page).toHaveURL(/\/qa$/)
  const composer = page.getByLabel('课程问题')
  await expect(composer).toHaveValue(
    '请解释“进程”，并结合课程资料说明它与相关概念的联系。',
  )
  await expect(page.getByText('输入第一个问题开始会话')).toBeVisible()
  await expect(page.getByText('已有来源')).toHaveCount(0)
  expect(queryRequests).toEqual([])

  await page.getByRole('button', { name: '提交问题' }).click()
  await expect(page.getByText('已有来源')).toBeVisible()
  expect(queryRequests).toHaveLength(1)
})
