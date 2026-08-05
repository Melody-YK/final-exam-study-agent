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

test('admin login opens the management console without a personal course workspace', async ({
  page,
}) => {
  await installMockApi(page, {
    accountRole: 'admin',
    authenticated: false,
  })
  const personalCourseRequests: string[] = []
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (path === '/api/v1/courses' || path.startsWith('/api/v1/courses/')) {
      personalCourseRequests.push(`${request.method()} ${path}`)
    }
  })
  await page.goto('/')

  await page.getByLabel('邮箱').fill('admin@example.com')
  await page.getByLabel('密码').fill('correct-password')
  await page.getByRole('button', { name: '登录' }).click()

  await expect(page).toHaveURL(/\/admin$/)
  await expect(page.getByRole('heading', { name: '管理控制台' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '运行概览' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '选择课程' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '创建复习工作区' })).toHaveCount(0)
  await expect(page.getByRole('navigation', { name: '学习视图' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '创建课程' })).toHaveCount(0)
  expect(personalCourseRequests).toEqual([])
  await expect
    .poll(() =>
      page.evaluate(() => localStorage.getItem('study-agent.course-id:account-e2e')),
    )
    .toBeNull()
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
      fetch('/api/v1/admin/courses'),
      fetch('/api/v1/admin/courses/course-e2e/notes'),
      fetch('/api/v1/admin/courses/course-e2e/knowledge-graph'),
    ])
    return responses.map((response) => response.status)
  })
  expect(forbiddenStatuses).toEqual([403, 403, 403, 403, 403, 403])
})

test('admin can update another users access controls', async ({ page }) => {
  await installMockApi(page, { accountRole: 'admin' })
  await page.goto('/admin')

  await expect(page.getByRole('heading', { name: '运行概览' })).toBeVisible()
  await page.getByRole('link', { name: '用户', exact: true }).click()
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
})

test('admin can inspect every users course content without study creation actions', async ({
  page,
}) => {
  await installMockApi(page, { accountRole: 'admin' })
  const writeRequests: string[] = []
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    if (path.startsWith('/api/v1/') && request.method() !== 'GET') {
      writeRequests.push(`${request.method()} ${path}`)
    }
  })
  await page.goto('/')

  await expect(page).toHaveURL(/\/admin$/)
  await expect(page.getByRole('heading', { name: '运行概览' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '学习视图' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '选择课程' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '创建课程' })).toHaveCount(0)

  await page.getByRole('link', { name: '用户内容' }).click()
  await expect(page).toHaveURL(/\/admin\/content$/)
  await expect(page.getByRole('heading', { name: '用户内容' })).toBeVisible()
  await expect(page.getByLabel('上传者')).toHaveValue('all')
  const ownerOptions = page.getByLabel('上传者').locator('option')
  await expect(ownerOptions).toHaveCount(3)
  await expect(page.getByLabel('上传者')).toContainText('全部用户')
  await expect(page.getByLabel('上传者')).toContainText('李琳 · lilin@example.com')
  await expect(page.getByLabel('上传者')).toContainText('复习同学 · student@example.com')

  const courseSummary = page.getByLabel('当前查看课程')
  await expect(courseSummary).toContainText('操作系统')
  await expect(courseSummary).toContainText('复习同学')
  await expect(page.getByText('文件系统.pptx')).toBeVisible()
  await expect(page.getByRole('link', { name: '预览 文件系统.pptx' })).toBeVisible()

  await page.getByLabel('上传者').selectOption('account-student-b-e2e')
  const courseSelect = page.getByRole('combobox').nth(1)
  await expect(courseSelect).toHaveValue('course-data-structures-e2e')
  await expect(courseSummary).toContainText('数据结构')
  await expect(courseSummary).toContainText('李琳')
  await expect(page.getByText('树与图.pptx')).toBeVisible()
  await expect(page.getByRole('link', { name: '预览 树与图.pptx' })).toBeVisible()
  await expect(page.getByText('文件系统.pptx')).toHaveCount(0)

  await page.getByRole('tab', { name: '笔记' }).click()
  const noteBody = page.getByRole('article', { name: '笔记正文' })
  await expect(
    noteBody.getByRole('heading', { level: 1, name: 'AVL 树复习笔记' }),
  ).toBeVisible()
  await expect(noteBody).toContainText('平衡因子用于判断旋转方向。')

  await page.getByRole('tab', { name: '知识图谱' }).click()
  await expect(page.getByLabel('课程概念地图画布')).toBeVisible()
  const balanceFactor = page.locator('.knowledge-node--concept').filter({ hasText: '平衡因子' })
  await expect(balanceFactor).toBeVisible()
  await balanceFactor.click()
  await expect(page.getByRole('complementary', { name: '节点详情' })).toContainText('平衡因子')

  await expect(
    page.getByRole('button', {
      name: /新建笔记|编辑|保存|重新生成|围绕此概念提问|查看原文/,
    }),
  ).toHaveCount(0)
  await expect(page.getByRole('button', { name: '创建课程' })).toHaveCount(0)
  expect(writeRequests).toEqual([])
})

test('admin invitation capacity refreshes after a concurrent last-seat race', async ({ page }) => {
  await installMockApi(page, {
    accountCapacity: 5,
    accountRole: 'admin',
    invitationCapacityRaceOnce: true,
  })
  await page.goto('/admin/users')

  await expect(page.getByRole('heading', { name: '用户与访问' })).toBeVisible()
  await page.getByRole('tab', { name: '邀请码' }).click()
  const capacity = page.getByLabel('账号容量')
  await expect(capacity).toContainText('活跃账号 3 / 5')
  await expect(capacity).toContainText('剩余席位 1')

  const openCreate = page.getByRole('button', { name: '创建邀请码' })
  await expect(openCreate).toBeEnabled()
  await openCreate.click()
  const dialog = page.getByRole('dialog', { name: '创建邀请码' })
  const capacityConflict = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      response.url().endsWith('/api/v1/admin/invitations'),
  )
  await dialog.getByRole('button', { name: '创建' }).click()

  const conflictResponse = await capacityConflict
  expect(conflictResponse.status()).toBe(409)
  const conflictProblem = await conflictResponse.json()
  expect(conflictProblem).toEqual(
    expect.objectContaining({
      status: 409,
      code: 'ACCOUNT_CAPACITY_REACHED',
    }),
  )
  expect(JSON.stringify(conflictProblem)).not.toContain('created-invite-code')
  await expect(dialog.getByRole('alert')).toContainText('邀请码未创建')
  await expect(dialog.getByRole('alert')).toContainText('账号容量已满')

  await expect(capacity).toContainText('剩余席位 0')
  await expect(openCreate).toBeDisabled()
  await expect(dialog.getByRole('button', { name: '创建' })).toBeDisabled()
  await expect(page.getByText(/created-invite-code/)).toHaveCount(0)
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
    details.getByText('资料“调度算法.md”包含概念“进程”9 次。'),
  ).toBeVisible()
  await expect(
    details.getByText('概念“进程”和“调度”共同出现在 4 个内容片段中。'),
  ).toBeVisible()
  await expect(details.getByText('调度算法.md', { exact: true })).toBeVisible()
  await expect(details.getByText(/调度算法 \/ 进程调度/)).toBeVisible()
  await expect(details.getByText('进程是资源分配的基本单位，线程是调度的基本单位。')).toBeVisible()
  await expect(page.getByText('Tokenizer')).toHaveCount(0)
  await expect(page.getByText(/Chunk/)).toHaveCount(0)

  await details.getByRole('button', { name: '查看原文' }).click()
  const sourceViewer = page.getByRole('dialog', { name: '来源' })
  await expect(sourceViewer.getByRole('heading', { name: '进程调度' })).toBeVisible()
  await expect(sourceViewer).not.toContainText('前一章节内容。')
  await sourceViewer.getByRole('button', { name: '关闭' }).click()

  await page.getByRole('button', { name: '仅看关联' }).click()
  await expect(page.locator('.knowledge-node')).toHaveCount(3)
  await expect(page.locator('.knowledge-node--course')).toHaveCount(0)

  const queryRequests: Array<Record<string, unknown>> = []
  page.on('request', (request) => {
    if (
      request.method() === 'POST' &&
      request.url().endsWith('/api/v1/courses/course-e2e/queries')
    ) {
      queryRequests.push(request.postDataJSON() as Record<string, unknown>)
    }
  })
  await details.getByRole('button', { name: '围绕此概念提问' }).click()

  await expect(page).toHaveURL(/\/qa$/)
  const composer = page.getByLabel('课程问题')
  await expect(composer).toHaveValue(
    '根据当前课程资料，概括“进程”在课程内容中的含义，并说明它与直接关联概念的联系。',
  )
  await expect(page.getByText('输入第一个问题开始会话')).toBeVisible()
  await expect(page.getByText('已有课程来源')).toHaveCount(0)
  expect(queryRequests).toEqual([])

  await page.getByRole('button', { name: '提交问题' }).click()
  await expect(page.getByText('已有课程来源')).toBeVisible()
  expect(queryRequests).toEqual([
    {
      question:
        '根据当前课程资料，概括“进程”在课程内容中的含义，并说明它与直接关联概念的联系。',
      concept_context: {
        label: '进程',
        anchors: [
          {
            document_id: 'document-markdown-ready',
            revision_id: 'revision-markdown-active',
            chunk_id: 'chunk-process',
          },
        ],
      },
    },
  ])
})
