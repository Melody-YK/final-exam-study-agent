import { createHash } from 'node:crypto'

import type { Page, Route } from '@playwright/test'

const courseId = 'course-e2e'
const transparentPng = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+7Z1xWQAAAABJRU5ErkJggg==',
  'base64',
)

const problem = (status: number, code: string, title: string) => ({
  type: 'about:blank',
  title,
  status,
  code,
  detail: null,
  instance: null,
  trace_id: 'e2e-trace',
  retryable: false,
  retry_after_ms: null,
  field_errors: [],
})

function document(overrides: Record<string, unknown> = {}) {
  return {
    id: 'document-ready',
    course_id: courseId,
    filename: '进程与线程.pdf',
    media_type: 'application/pdf',
    corpus_role: 'corpus',
    verified_sha256: 'a'.repeat(64),
    status: 'ready',
    review_status: 'approved',
    preview_revision_id: null,
    active_revision_id: 'revision-active',
    deletion_epoch: 0,
    indexable: true,
    page_count: 24,
    parse_job_id: null,
    progress: {},
    failed_pages: [],
    updated_at: '2026-07-19T05:00:00Z',
    error_code: null,
    ...overrides,
  }
}

function note(version = 1) {
  return {
    id: 'note-e2e',
    course_id: courseId,
    section_path: ['第二章', '进程管理'],
    title: '进程管理',
    body_markdown:
      version === 1
        ? '# 进程管理\n\n进程是资源分配的基本单位。'
        : '# 进程管理\n\n服务器上的最新正文。',
    version,
    generation: 2,
    generated_by_model: true,
    origin_batch_id: null,
    status: 'ready',
    sources: [
      {
        id: 'note-source-e2e',
        evidence_id: 'citation-e2e',
        document_id: 'document-ready',
        revision_id: 'revision-active',
        chunk_id: 'chunk-e2e',
        document_name: '进程与线程.pdf',
        locator: { kind: 'page', ordinal: 6 },
        quote: '进程是资源分配的基本单位。',
        bounding_boxes: [],
        provenance: ['pdf-native@1'],
        available: true,
        stale: false,
        unavailable_reason: null,
      },
    ],
    created_at: '2026-07-19T05:00:00Z',
    updated_at: '2026-07-19T05:10:00Z',
  }
}

function generatedNote(
  title: string,
  sectionPath: string[],
  style: 'exam_focus' | 'outline' | 'complete',
) {
  const rendered = {
    exam_focus: {
      label: '考前速记',
      content:
        '## 进程与线程.pdf\n\n- 进程是资源分配的基本单位。\n- 线程是调度的基本单位。',
    },
    outline: {
      label: '结构提纲',
      content:
        '## 1. 进程与线程.pdf\n\n### 1.1 第 1 页\n\n1. 进程与线程\n2. 调度与同步\n3. 死锁处理',
    },
    complete: {
      label: '完整讲义',
      content:
        '## 进程与线程.pdf\n\n### 第 1 页\n\n进程是资源分配的基本单位，线程是调度的基本单位。完整讲义按来源顺序保留资料中的定义、例子和上下文。',
    },
  }[style]
  return {
    ...note(1),
    id: 'note-generated-e2e',
    origin_batch_id: 'note-batch-e2e',
    section_path: sectionPath,
    title,
    body_markdown: `# ${title}\n\n> 笔记模板: ${rendered.label}\n\n${rendered.content}`,
    generation: 1,
    created_at: '2026-07-19T05:40:00Z',
    updated_at: '2026-07-19T05:40:00Z',
  }
}

function answered(question: string, queryId: string, conversationId: string, createdAt: string) {
  return {
    id: queryId,
    course_id: courseId,
    conversation_id: conversationId,
    question,
    status: 'answered',
    answer: {
      schema_version: '1.0',
      query_id: queryId,
      status: 'answered',
      answer_markdown: '进程是资源分配的基本单位，线程是调度的基本单位。',
      claims: [
        {
          id: 'claim-e2e',
          text: '进程是资源分配的基本单位。',
          citation_ids: ['citation-e2e'],
        },
      ],
      citations: [
        {
          id: 'citation-e2e',
          document_id: 'document-ready',
          revision_id: 'revision-active',
          chunk_id: 'chunk-e2e',
          document_name: '进程页面.png',
          locator: { kind: 'page', ordinal: 6 },
          quote: '进程是资源分配的基本单位。',
          bounding_boxes: [{ x: 0.12, y: 0.22, width: 0.52, height: 0.09 }],
        },
      ],
      refusal: null,
    },
    failure_code: null,
    usage: { input_tokens: 120, output_tokens: 36, total_tokens: 156 },
    trace: {
      trace_id: 'query-trace-e2e',
      retrieval_snapshot_id: 'snapshot-e2e',
      retrieval_trace_id: 'retrieval-e2e',
    },
    created_at: createdAt,
    completed_at: new Date(Date.parse(createdAt) + 1_000).toISOString(),
  }
}

function abstained(question: string, queryId: string, conversationId: string, createdAt: string) {
  return {
    ...answered(question, queryId, conversationId, createdAt),
    status: 'abstained',
    answer: {
      schema_version: '1.0',
      query_id: queryId,
      status: 'abstained',
      answer_markdown: '',
      claims: [],
      citations: [],
      refusal: {
        code: 'INSUFFICIENT_EVIDENCE',
        message: '当前课程资料没有足够依据。',
      },
    },
  }
}

export interface MockApiOptions {
  accountRole?: 'admin' | 'user'
  authenticated?: boolean
  includeNoteEligibilityDriftDocuments?: boolean
  noteBatchPollsBeforeSuccess?: number
  providerAvailable?: boolean
  seedCourseSelection?: boolean
}

export async function installMockApi(page: Page, options: MockApiOptions = {}) {
  const accountRole = options.accountRole ?? 'user'
  let authenticated = options.authenticated ?? true
  const mockAccount = {
    id: 'account-e2e',
    email: accountRole === 'admin' ? 'admin@example.com' : 'student@example.com',
    display_name: accountRole === 'admin' ? '本地管理员' : '复习同学',
    role: accountRole,
  }
  type MockAdminAccount = typeof mockAccount & {
    status: 'active' | 'suspended'
    admin_note: string | null
    created_at: string
  }
  type MockInvitation = {
    id: string
    created_by_account_id: string
    used_by_account_id: string | null
    status: 'available' | 'used' | 'revoked' | 'expired'
    created_at: string
    expires_at: string
    used_at: string | null
    revoked_at: string | null
  }
  let signedInAccount = mockAccount
  let adminAccounts: MockAdminAccount[] = [
    {
      ...mockAccount,
      status: 'active' as const,
      admin_note: accountRole === 'admin' ? '本地演示负责人' : null,
      created_at: '2026-07-20T08:00:00Z',
    },
    {
      id: 'account-student-e2e',
      email: 'student@example.com',
      display_name: '复习同学',
      role: 'user' as const,
      status: 'active' as const,
      admin_note: null,
      created_at: '2026-07-21T08:00:00Z',
    },
  ]
  const registrationInviteCode = 'e2e-invite-code-123456'
  let invitationSequence = 1
  let invitations: MockInvitation[] = [
    {
      id: 'invitation-e2e-1',
      created_by_account_id: 'account-e2e',
      used_by_account_id: null,
      status: 'available',
      created_at: '2026-07-23T08:00:00Z',
      expires_at: '2026-07-30T08:00:00Z',
      used_at: null,
      revoked_at: null,
    },
  ]
  const noteBatchPollsBeforeSuccess = options.noteBatchPollsBeforeSuccess ?? 3
  const providerAvailable = options.providerAvailable ?? true
  let notesVersion = 1
  let generatedNoteRecord: ReturnType<typeof generatedNote> | null = null
  let noteBatchPolls = 0
  let noteBatchCompletionApplied = false
  let noteBatchId = 'note-batch-e2e'
  let noteBatchCommand: 'create' | 'regeneration' = 'create'
  let regenerationTargetNoteId: string | null = null
  let regenerationTargetVersion: number | null = null
  let noteBatchPayload:
    | {
        mode: 'merged'
        document_ids: string[]
        style: 'exam_focus' | 'outline' | 'complete'
        title?: string
        section_path?: string[]
      }
    | undefined
  let conversationSequence = 0
  let querySequence = 0
  type MockQuerySnapshot = ReturnType<typeof answered> | ReturnType<typeof abstained>
  type MockConversation = {
    id: string
    course_id: string
    title: string
    turn_count: number
    latest_query_id: string | null
    latest_question: string | null
    created_at: string
    updated_at: string
  }
  const conversations: MockConversation[] = []
  const conversationQueries = new Map<string, MockQuerySnapshot[]>()
  const queries = new Map<string, MockQuerySnapshot>()
  const mockTimestamp = (sequence: number) =>
    new Date(Date.parse('2026-07-19T05:20:00Z') + sequence * 1_000).toISOString()
  const createConversation = (title: string): MockConversation => {
    conversationSequence += 1
    const createdAt = mockTimestamp(conversationSequence)
    const conversation = {
      id: `conversation-e2e-${conversationSequence}`,
      course_id: courseId,
      title,
      turn_count: 0,
      latest_query_id: null,
      latest_question: null,
      created_at: createdAt,
      updated_at: createdAt,
    }
    conversations.push(conversation)
    conversationQueries.set(conversation.id, [])
    return conversation
  }
  let uploadDeclaration:
    | {
        filename: string
        media_type: string
        size_bytes: number
        sha256: string
        corpus_role: string
      }
    | undefined
  let uploadedSha: string | undefined
  let documents = [
    document(),
    document({
      id: 'document-preview',
      filename: '文件系统.pptx',
      media_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      status: 'parsed_index_blocked',
      review_status: 'pending',
      active_revision_id: null,
      preview_revision_id: 'revision-preview',
      indexable: false,
      page_count: 18,
    }),
    document({
      id: 'document-failed',
      filename: '扫描试题.png',
      media_type: 'image/png',
      status: 'partial_failed',
      active_revision_id: null,
      failed_pages: [2],
      page_count: 3,
      error_code: 'OCR_PROFILE_UNAVAILABLE',
    }),
  ]
  type MockReviewMetadata = {
    review_note: string | null
    reviewed_by_account_id: string | null
    reviewed_by_email: string | null
    reviewed_at: string | null
  }
  const reviewMetadata = new Map<string, MockReviewMetadata>()
  const adminDocument = (item: (typeof documents)[number]) => {
    const review = reviewMetadata.get(item.id) ?? {
      review_note: null,
      reviewed_by_account_id: null,
      reviewed_by_email: null,
      reviewed_at: null,
    }
    return {
      id: item.id,
      course_id: item.course_id,
      course_title: '操作系统',
      owner_account_id: 'account-student-e2e',
      owner_email: 'student@example.com',
      owner_display_name: '复习同学',
      owner_subject: 'student@example.com',
      filename: item.filename,
      media_type: item.media_type,
      size_bytes: item.id === 'document-preview' ? 524_288 : 262_144,
      corpus_role: item.corpus_role,
      status: item.status,
      page_count: item.page_count,
      review_status: item.review_status,
      ...review,
      created_at: '2026-07-23T08:00:00Z',
      updated_at: item.updated_at,
    }
  }
  if (options.includeNoteEligibilityDriftDocuments) {
    documents.push(
      document({
        id: 'document-slides-ready',
        filename: '调度算法.pptx',
        media_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        active_revision_id: 'revision-slides-active',
        page_count: 16,
      }),
      document({
        id: 'document-non-corpus',
        filename: '题库.pdf',
        corpus_role: 'questions',
      }),
      document({
        id: 'document-not-indexable',
        filename: '未索引.pdf',
        indexable: false,
      }),
      document({
        id: 'document-extension-only',
        filename: '伪装资料.pdf',
        media_type: 'application/octet-stream',
      }),
      document({
        id: 'document-legacy-ppt-filename',
        filename: '旧版课件.ppt',
        media_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      }),
    )
  }

  function noteBatchSnapshot(status: 'queued' | 'running' | 'succeeded') {
    const payload = noteBatchPayload ?? {
      mode: 'merged' as const,
      document_ids: [],
      style: 'exam_focus' as const,
    }
    const selectedDocuments = payload.document_ids.map(
      (id) => documents.find((item) => item.id === id) ?? document({ id }),
    )
    const succeeded = status === 'succeeded'
    const itemStatus = succeeded ? 'succeeded' : status === 'running' ? 'running' : 'queued'
    return {
      schema_version: '1.0',
      id: noteBatchId,
      command_kind: noteBatchCommand,
      retry_of_batch_id: null,
      course_id: courseId,
      mode: 'merged',
      style: payload.style,
      title: payload.title?.trim() || null,
      title_prefix: null,
      section_path: payload.section_path?.length ? payload.section_path : ['未分类'],
      target_note_id: noteBatchCommand === 'regeneration' ? regenerationTargetNoteId : null,
      target_note_version:
        noteBatchCommand === 'regeneration' ? regenerationTargetVersion : null,
      target_note_version_sha256:
        noteBatchCommand === 'regeneration' ? 'a'.repeat(64) : null,
      status,
      completed_items: succeeded ? 1 : 0,
      total_items: 1,
      inputs: selectedDocuments.map((selected, index) => ({
        id: `note-input-e2e-${index + 1}`,
        ordinal: index + 1,
        document_id: selected.id,
        revision_id: selected.active_revision_id ?? 'revision-active',
        deletion_epoch: selected.deletion_epoch,
        document_name: selected.filename,
        media_type: selected.media_type,
        content_sha256: selected.verified_sha256,
        index_manifest_at_submit: 'manifest-e2e',
      })),
      coverage_units: [],
      items: [
        {
          id: 'note-item-e2e',
          input_ids: selectedDocuments.map((_, index) => `note-input-e2e-${index + 1}`),
          status: itemStatus,
          phase: status === 'running' ? 'generating' : null,
          elapsed_seconds: succeeded ? 12 : status === 'running' ? 7 : 0,
          eta: null,
          eta_unavailable_reason: succeeded
            ? 'terminal'
            : status === 'running'
              ? 'insufficient_history'
              : 'not_started',
          attempt: status === 'queued' ? 0 : 1,
          note_id:
            noteBatchCommand === 'regeneration'
              ? regenerationTargetNoteId
              : (generatedNoteRecord?.id ?? null),
          failure_code: null,
          retryable_in_new_batch: false,
        },
      ],
      last_event_sequence: noteBatchPolls,
      created_at: '2026-07-19T05:35:00Z',
      started_at: status === 'queued' ? null : '2026-07-19T05:35:01Z',
      completed_at: succeeded ? '2026-07-19T05:35:12Z' : null,
    }
  }

  if (options.seedCourseSelection !== false) {
    await page.addInitScript(
      (id) => localStorage.setItem('study-agent.course-id:account-e2e', id),
      courseId,
    )
  }
  await page.route('**/api/v1/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace('/api/v1', '')
    const method = request.method()

    if (method === 'GET' && path === '/auth/me') {
      return authenticated
        ? route.fulfill({ json: signedInAccount })
        : route.fulfill({
            status: 401,
            json: problem(401, 'AUTH_REQUIRED', '需要登录'),
          })
    }
    if (method === 'POST' && path === '/auth/login') {
      authenticated = true
      signedInAccount = mockAccount
      return route.fulfill({ json: signedInAccount })
    }
    if (method === 'POST' && path === '/auth/register') {
      const payload = request.postDataJSON() as {
        email: string
        display_name: string
        invite_code?: string
      }
      const invitation = invitations.find((item) => item.id === 'invitation-e2e-1')
      if (payload.invite_code !== registrationInviteCode || invitation?.status !== 'available') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'AUTH_FORBIDDEN', '注册邀请无效'),
        })
      }
      authenticated = true
      signedInAccount = {
        ...mockAccount,
        email: payload.email,
        display_name: payload.display_name,
        role: 'user',
      }
      invitations = invitations.map((item) =>
        item.id === invitation.id
          ? {
              ...item,
              status: 'used',
              used_by_account_id: signedInAccount.id,
              used_at: '2026-07-24T08:00:00Z',
            }
          : item,
      )
      return route.fulfill({
        status: 201,
        json: signedInAccount,
      })
    }
    if (method === 'POST' && path === '/auth/logout') {
      authenticated = false
      return route.fulfill({ status: 204 })
    }
    if (method === 'GET' && path === '/admin/users') {
      if (!authenticated || accountRole !== 'admin') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'FORBIDDEN', '权限不足'),
        })
      }
      return route.fulfill({ json: { items: adminAccounts } })
    }
    const adminUserMatch = path.match(/^\/admin\/users\/([^/]+)$/)
    if (method === 'PATCH' && adminUserMatch !== null) {
      if (!authenticated || accountRole !== 'admin') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'FORBIDDEN', '权限不足'),
        })
      }
      const accountId = decodeURIComponent(adminUserMatch[1]!)
      const accountIndex = adminAccounts.findIndex((account) => account.id === accountId)
      if (accountIndex === -1) {
        return route.fulfill({
          status: 404,
          json: problem(404, 'RESOURCE_NOT_FOUND', '用户不存在'),
        })
      }
      const payload = request.postDataJSON() as Partial<
        Pick<MockAdminAccount, 'role' | 'status' | 'admin_note'>
      >
      const current = adminAccounts[accountIndex]!
      if (
        current.id === mockAccount.id &&
        (payload.role !== undefined || payload.status !== undefined)
      ) {
        return route.fulfill({
          status: 409,
          json: problem(409, 'STATE_CONFLICT', '不能修改当前账号的角色或状态'),
        })
      }
      const updated = { ...current, ...payload }
      adminAccounts = adminAccounts.map((account) =>
        account.id === updated.id ? updated : account,
      )
      return route.fulfill({ json: updated })
    }
    if (method === 'GET' && path === '/admin/invitations') {
      if (!authenticated || accountRole !== 'admin') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'FORBIDDEN', '权限不足'),
        })
      }
      return route.fulfill({ json: { items: invitations } })
    }
    if (method === 'POST' && path === '/admin/invitations') {
      if (!authenticated || accountRole !== 'admin') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'FORBIDDEN', '权限不足'),
        })
      }
      const payload = request.postDataJSON() as { expires_in_days: number }
      invitationSequence += 1
      const createdAt = new Date('2026-07-24T08:00:00Z')
      const invitation: MockInvitation = {
        id: `invitation-e2e-${invitationSequence}`,
        created_by_account_id: mockAccount.id,
        used_by_account_id: null,
        status: 'available',
        created_at: createdAt.toISOString(),
        expires_at: new Date(
          createdAt.getTime() + payload.expires_in_days * 24 * 60 * 60 * 1_000,
        ).toISOString(),
        used_at: null,
        revoked_at: null,
      }
      invitations = [invitation, ...invitations]
      return route.fulfill({
        status: 201,
        json: {
          ...invitation,
          code: `created-invite-code-${invitationSequence}-123456`,
        },
      })
    }
    const invitationMatch = path.match(/^\/admin\/invitations\/([^/]+)$/)
    if (method === 'DELETE' && invitationMatch !== null) {
      if (!authenticated || accountRole !== 'admin') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'FORBIDDEN', '权限不足'),
        })
      }
      const invitationId = decodeURIComponent(invitationMatch[1]!)
      invitations = invitations.map((invitation) =>
        invitation.id === invitationId
          ? {
              ...invitation,
              status: 'revoked',
              revoked_at: '2026-07-24T08:05:00Z',
            }
          : invitation,
      )
      return route.fulfill({ status: 204 })
    }
    if (method === 'GET' && path === '/admin/diagnostics') {
      if (!authenticated || accountRole !== 'admin') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'FORBIDDEN', '权限不足'),
        })
      }
      return route.fulfill({
        json: {
          totals: {
            accounts: 1,
            active_sessions: authenticated ? 1 : 0,
            courses: 1,
            documents: documents.length,
            notes: generatedNoteRecord ? 2 : 1,
          },
          runtime: {
            app_mode: 'local',
            database: 'postgresql',
            demo_lab_enabled: true,
          },
        },
      })
    }
    if (method === 'GET' && path === '/admin/documents') {
      if (!authenticated || accountRole !== 'admin') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'FORBIDDEN', '权限不足'),
        })
      }
      const reviewStatus = url.searchParams.get('review_status')
      const items = documents
        .filter((item) => reviewStatus === null || item.review_status === reviewStatus)
        .map(adminDocument)
      return route.fulfill({ json: { items } })
    }
    const adminDocumentContentMatch = path.match(/^\/admin\/documents\/([^/]+)\/content$/)
    if (method === 'GET' && adminDocumentContentMatch !== null) {
      if (!authenticated || accountRole !== 'admin') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'FORBIDDEN', '权限不足'),
        })
      }
      const documentId = decodeURIComponent(adminDocumentContentMatch[1]!)
      const current = documents.find((item) => item.id === documentId)
      if (current === undefined) {
        return route.fulfill({
          status: 404,
          json: problem(404, 'RESOURCE_NOT_FOUND', '资料不存在'),
        })
      }
      return route.fulfill({
        body: Buffer.from('%PDF-1.7\nmock review content'),
        contentType: current.media_type,
        headers: {
          'Cache-Control': 'private, no-store',
          'Content-Disposition': `inline; filename*=UTF-8''${encodeURIComponent(current.filename)}`,
        },
      })
    }
    const adminDocumentReviewMatch = path.match(/^\/admin\/documents\/([^/]+)\/review$/)
    if (method === 'POST' && adminDocumentReviewMatch !== null) {
      if (!authenticated || accountRole !== 'admin') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'FORBIDDEN', '权限不足'),
        })
      }
      const documentId = decodeURIComponent(adminDocumentReviewMatch[1]!)
      const current = documents.find((item) => item.id === documentId)
      if (current === undefined) {
        return route.fulfill({
          status: 404,
          json: problem(404, 'RESOURCE_NOT_FOUND', '资料不存在'),
        })
      }
      const payload = request.postDataJSON() as {
        review_status?: string
        review_note?: string | null
      }
      const reviewNote = payload.review_note?.trim() || null
      if (
        !['approved', 'rejected'].includes(payload.review_status ?? '') ||
        (payload.review_status === 'rejected' && reviewNote === null)
      ) {
        return route.fulfill({
          status: 422,
          json: problem(422, 'VALIDATION_ERROR', '审核决定无效'),
        })
      }
      if (current.review_status !== 'pending' && current.review_status !== payload.review_status) {
        return route.fulfill({
          status: 409,
          json: problem(409, 'STATE_CONFLICT', '资料已经完成审核'),
        })
      }
      documents = documents.map((item) =>
        item.id === documentId
          ? {
              ...item,
              review_status: payload.review_status,
              indexable: payload.review_status === 'approved' && item.corpus_role === 'corpus',
              updated_at: '2026-07-24T08:10:00Z',
            }
          : item,
      )
      reviewMetadata.set(documentId, {
        review_note: reviewNote,
        reviewed_by_account_id: mockAccount.id,
        reviewed_by_email: mockAccount.email,
        reviewed_at: '2026-07-24T08:10:00Z',
      })
      return route.fulfill({
        json: adminDocument(documents.find((item) => item.id === documentId)!),
      })
    }

    if (method === 'GET' && path === `/courses/${courseId}`) {
      return route.fulfill({
        json: { id: courseId, title: '操作系统', lifecycle: 'active' },
      })
    }
    if (method === 'GET' && path === '/courses') {
      return route.fulfill({
        json: [{ id: courseId, title: '操作系统', lifecycle: 'active' }],
      })
    }
    if (method === 'GET' && path === '/capabilities') {
      const providerStatus = providerAvailable ? 'available' : 'not_configured'
      return route.fulfill({
        json: {
          provider: {
            status: providerStatus,
            label: providerAvailable ? '可用' : '未配置',
          },
          embedding: {
            status: providerStatus,
            label: providerAvailable ? '可用' : '未配置',
          },
          native_parser: { status: 'available', label: '原生解析可用' },
          ocr_parser: {
            status: 'worker_required',
            label: '需要本地 OCR Worker',
          },
          demo_lab_enabled: true,
          note_workflow: {
            enabled: true,
            generation: {
              status: 'available',
              label: '本地异步笔记生成可用',
            },
            export: {
              status: 'unavailable',
              label: 'DOCX 导出未启用',
              error_code: 'NOTE_EXPORT_UNAVAILABLE',
            },
            eta: {
              status: 'unavailable',
              label: '数值 ETA 未启用',
              error_code: 'NOTE_ETA_UNAVAILABLE',
            },
          },
        },
      })
    }
    if (method === 'GET' && path === `/courses/${courseId}/documents`) {
      return route.fulfill({ json: documents })
    }
    if (method === 'POST' && path === `/courses/${courseId}/documents`) {
      uploadDeclaration = request.postDataJSON() as typeof uploadDeclaration
      return route.fulfill({
        status: 201,
        json: {
          document: document({
            id: 'document-upload',
            filename: uploadDeclaration?.filename,
            media_type: uploadDeclaration?.media_type,
            corpus_role: uploadDeclaration?.corpus_role,
            verified_sha256: uploadDeclaration?.sha256,
            status: 'uploading',
            review_status: 'pending',
            indexable: false,
          }),
          upload: {
            id: 'upload-e2e',
            url: '/api/v1/uploads/upload-e2e',
            expires_at: '2099-01-01T00:00:00Z',
          },
        },
      })
    }
    if (method === 'PUT' && path === '/uploads/upload-e2e') {
      const bytes = request.postDataBuffer()
      uploadedSha =
        bytes === null
          ? uploadDeclaration?.sha256
          : createHash('sha256').update(bytes).digest('hex')
      if (
        !uploadDeclaration ||
        uploadDeclaration.sha256 !== uploadedSha ||
        (bytes !== null && uploadDeclaration.size_bytes !== bytes.byteLength)
      ) {
        return route.fulfill({
          status: 409,
          json: {
            ...problem(409, 'UPLOAD_HASH_MISMATCH', '上传内容与声明不一致'),
            detail: `declared=${uploadDeclaration?.size_bytes}/${uploadDeclaration?.sha256}; received=${bytes?.byteLength ?? 'unavailable'}/${uploadedSha}`,
          },
        })
      }
      return route.fulfill({
        json: {
          upload_session_id: 'upload-e2e',
          status: 'uploaded',
          size_bytes: bytes?.byteLength ?? uploadDeclaration.size_bytes,
          sha256: uploadedSha,
        },
      })
    }
    if (method === 'POST' && path === '/documents/document-upload/upload:complete') {
      if (!uploadDeclaration || uploadedSha !== uploadDeclaration.sha256) {
        return route.fulfill({
          status: 409,
          json: problem(409, 'UPLOAD_NOT_VERIFIED', '上传尚未验证'),
        })
      }
      const uploaded = document({
        id: 'document-upload',
        filename: uploadDeclaration.filename,
        media_type: uploadDeclaration.media_type,
        corpus_role: uploadDeclaration.corpus_role,
        verified_sha256: uploadedSha,
        status: 'queued',
        review_status: 'pending',
        indexable: false,
        parse_job_id: 'parse-job-upload',
        progress: { phase: 'queued', completed_pages: 0, total_pages: null },
      })
      documents = [uploaded, ...documents]
      return route.fulfill({ status: 202, json: uploaded })
    }
    if (method === 'POST' && path.endsWith('/parse-jobs')) {
      const id = path.split('/')[2]
      const current = documents.find((item) => item.id === id) ?? document({ id })
      const retried = {
        ...current,
        status: 'queued',
        parse_job_id: 'parse-job-retry',
        progress: {
          phase: 'queued',
          completed_pages: 0,
          total_pages: current.page_count,
        },
        error_code: null,
      }
      documents = documents.map((item) => (item.id === id ? retried : item))
      return route.fulfill({ status: 202, json: retried })
    }
    if (method === 'DELETE' && path.startsWith('/documents/')) {
      const id = path.split('/')[2]
      documents = documents.filter((item) => item.id !== id)
      return route.fulfill({
        status: 202,
        json: { deletion_id: 'deletion-e2e', status: 'pending' },
      })
    }
    if (method === 'GET' && path === '/deletions/deletion-e2e') {
      return route.fulfill({
        json: {
          id: 'deletion-e2e',
          target_id: 'document-ready',
          target_type: 'document',
          deletion_epoch: 1,
          status: 'completed',
          attempt_count: 1,
          completed_at: '2026-07-19T05:30:00Z',
        },
      })
    }
    if (method === 'GET' && path === `/courses/${courseId}/conversations`) {
      const ordered = [...conversations].sort(
        (left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at),
      )
      return route.fulfill({ json: ordered })
    }
    if (method === 'POST' && path === `/courses/${courseId}/conversations`) {
      const payload = request.postDataJSON() as { title?: string | null }
      const conversation = createConversation(payload.title?.trim() || '新会话')
      return route.fulfill({ status: 201, json: conversation })
    }
    if (method === 'GET' && /^\/conversations\/[^/]+\/queries$/.test(path)) {
      const conversationId = decodeURIComponent(path.split('/')[2] ?? '')
      const history = conversationQueries.get(conversationId)
      if (history === undefined) {
        return route.fulfill({
          status: 404,
          json: problem(404, 'RESOURCE_NOT_FOUND', '会话不存在'),
        })
      }
      const ordered = [...history].sort(
        (left, right) => Date.parse(left.created_at) - Date.parse(right.created_at),
      )
      return route.fulfill({ json: ordered })
    }
    if (method === 'POST' && path === `/courses/${courseId}/queries`) {
      const payload = request.postDataJSON() as {
        question: string
        conversation_id?: string | null
      }
      let conversation: MockConversation | undefined
      if (typeof payload.conversation_id === 'string') {
        conversation = conversations.find((item) => item.id === payload.conversation_id)
        if (conversation === undefined) {
          return route.fulfill({
            status: 404,
            json: problem(404, 'RESOURCE_NOT_FOUND', '会话不存在'),
          })
        }
      } else {
        conversation = createConversation(payload.question.trim())
      }
      querySequence += 1
      const queryId = `query-e2e-${querySequence}`
      const createdAt = mockTimestamp(100 + querySequence)
      const snapshot = payload.question.includes('课件外')
        ? abstained(payload.question, queryId, conversation.id, createdAt)
        : answered(payload.question, queryId, conversation.id, createdAt)
      const history = conversationQueries.get(conversation.id)
      if (history === undefined) {
        return route.fulfill({
          status: 404,
          json: problem(404, 'RESOURCE_NOT_FOUND', '会话不存在'),
        })
      }
      if (conversation.turn_count === 0 && conversation.title === '新会话') {
        conversation.title = payload.question.trim()
      }
      history.push(snapshot)
      queries.set(queryId, snapshot)
      conversation.turn_count += 1
      conversation.latest_query_id = queryId
      conversation.latest_question = payload.question
      conversation.updated_at = createdAt
      return route.fulfill({
        status: 202,
        json: snapshot,
      })
    }
    if (method === 'GET' && /^\/queries\/[^/]+$/.test(path)) {
      const queryId = decodeURIComponent(path.split('/')[2] ?? '')
      const snapshot = queries.get(queryId)
      if (snapshot === undefined) {
        return route.fulfill({
          status: 404,
          json: problem(404, 'RESOURCE_NOT_FOUND', '问答不存在'),
        })
      }
      return route.fulfill({ json: snapshot })
    }
    if (method === 'GET' && /^\/queries\/query-e2e-\d+\/citations\/citation-e2e$/.test(path)) {
      const queryId = path.split('/')[2] ?? ''
      if (!queries.has(queryId)) {
        return route.fulfill({
          status: 404,
          json: problem(404, 'RESOURCE_NOT_FOUND', '问答不存在'),
        })
      }
      return route.fulfill({
        json: {
          citation_id: 'citation-e2e',
          document_id: 'document-ready',
          revision_id: 'revision-active',
          chunk_id: 'chunk-e2e',
          document_name: '进程页面.png',
          locator: { kind: 'page', ordinal: 6 },
          quote: '进程是资源分配的基本单位。',
          bounding_boxes: [{ x: 0.12, y: 0.22, width: 0.52, height: 0.09 }],
          provenance: ['pdf-native@1'],
          media_type: 'image/png',
          read_url: '/api/v1/e2e/sources/citation-e2e.png',
          read_url_expires_at: '2099-01-01T00:00:00Z',
        },
      })
    }
    if (method === 'GET' && path === '/e2e/sources/citation-e2e.png') {
      return route.fulfill({ body: transparentPng, contentType: 'image/png' })
    }
    if (method === 'POST' && path === `/courses/${courseId}/note-batches`) {
      noteBatchPayload = request.postDataJSON() as typeof noteBatchPayload
      noteBatchPolls = 0
      noteBatchCompletionApplied = false
      noteBatchId = 'note-batch-e2e'
      noteBatchCommand = 'create'
      regenerationTargetNoteId = null
      regenerationTargetVersion = null
      return route.fulfill({ status: 202, json: noteBatchSnapshot('queued') })
    }
    if (
      method === 'POST' &&
      generatedNoteRecord !== null &&
      path === `/notes/${generatedNoteRecord.id}/regeneration-batches`
    ) {
      if (
        request.headers()['if-match'] !== `"${generatedNoteRecord.version}"` ||
        !request.headers()['idempotency-key']
      ) {
        return route.fulfill({
          status: 428,
          json: problem(428, 'PRECONDITION_REQUIRED', '需要版本和幂等键'),
        })
      }
      noteBatchPolls = 0
      noteBatchCompletionApplied = false
      noteBatchId = 'note-regeneration-batch-e2e'
      noteBatchCommand = 'regeneration'
      regenerationTargetNoteId = generatedNoteRecord.id
      regenerationTargetVersion = generatedNoteRecord.version
      return route.fulfill({ status: 202, json: noteBatchSnapshot('queued') })
    }
    if (method === 'GET' && /^\/note-batches\/[^/]+$/.test(path)) {
      if (!noteBatchPayload || path !== `/note-batches/${noteBatchId}`) {
        return route.fulfill({
          status: 404,
          json: problem(404, 'RESOURCE_NOT_FOUND', '批次不存在'),
        })
      }
      noteBatchPolls += 1
      const status = noteBatchPolls >= noteBatchPollsBeforeSuccess ? 'succeeded' : 'running'
      if (status === 'succeeded' && !noteBatchCompletionApplied) {
        if (noteBatchCommand === 'regeneration' && generatedNoteRecord !== null) {
          generatedNoteRecord = {
            ...generatedNoteRecord,
            body_markdown: `${generatedNoteRecord.body_markdown}\n\n## 重新生成结果\n\n已通过异步批次重新生成。`,
            version: generatedNoteRecord.version + 1,
            generation: generatedNoteRecord.generation + 1,
            updated_at: '2026-07-19T05:45:00Z',
          }
        } else {
          generatedNoteRecord = generatedNote(
            noteBatchPayload.title?.trim() || '合并课程笔记',
            noteBatchPayload.section_path?.length ? noteBatchPayload.section_path : ['未分类'],
            noteBatchPayload.style,
          )
        }
        noteBatchCompletionApplied = true
      }
      return route.fulfill({ json: noteBatchSnapshot(status) })
    }
    if (method === 'GET' && path === `/courses/${courseId}/notes`) {
      return route.fulfill({
        json: [
          note(notesVersion),
          ...(generatedNoteRecord ? [generatedNoteRecord] : []),
        ],
      })
    }
    if (method === 'PATCH' && path === '/notes/note-e2e') {
      if (request.headers()['if-match'] !== '"1"') {
        return route.fulfill({
          status: 428,
          json: problem(428, 'PRECONDITION_REQUIRED', '需要 If-Match'),
        })
      }
      notesVersion = 2
      return route.fulfill({
        status: 412,
        json: problem(412, 'VERSION_CONFLICT', '笔记版本冲突'),
      })
    }
    if (method === 'POST' && path === '/notes/note-e2e/regenerate') {
      return route.fulfill({ json: note(notesVersion) })
    }
    if (method === 'GET' && path === `/courses/${courseId}/knowledge-graph`) {
      return route.fulfill({
        json: {
          course_id: courseId,
          tokenizer_version: 'jieba-v1',
          active_document_count: 2,
          included_document_count: 2,
          source_chunk_count: 18,
          node_limit: 64,
          edge_limit: 160,
          truncated: false,
          nodes: [
            {
              id: `course:${courseId}`,
              kind: 'course',
              label: '操作系统',
              document_id: null,
              revision_id: null,
              page_count: null,
              frequency: null,
              document_count: null,
              occurrence_count: null,
              occurrences: [],
              occurrences_truncated: false,
            },
            {
              id: 'document:document-ready',
              kind: 'document',
              label: '进程与线程.pdf',
              document_id: 'document-ready',
              revision_id: 'revision-active',
              page_count: 24,
              frequency: null,
              document_count: null,
              occurrence_count: null,
              occurrences: [],
              occurrences_truncated: false,
            },
            {
              id: 'concept:process',
              kind: 'concept',
              label: '进程',
              document_id: null,
              revision_id: null,
              page_count: null,
              frequency: 9,
              document_count: 1,
              occurrence_count: 3,
              occurrences: [
                {
                  document_id: 'document-ready',
                  document_name: '进程与线程.pdf',
                  revision_id: 'revision-active',
                  chunk_id: 'chunk-process',
                  page_ordinal: 6,
                  chunk_ordinal: 2,
                  count: 3,
                  excerpt: '进程是资源分配的基本单位，线程是调度的基本单位。',
                },
              ],
              occurrences_truncated: false,
            },
            {
              id: 'concept:scheduling',
              kind: 'concept',
              label: '调度',
              document_id: null,
              revision_id: null,
              page_count: null,
              frequency: 6,
              document_count: 1,
              occurrence_count: 2,
              occurrences: [],
              occurrences_truncated: false,
            },
          ],
          edges: [
            {
              id: 'edge:contains:document-ready',
              source: `course:${courseId}`,
              target: 'document:document-ready',
              kind: 'contains',
              weight: 1,
            },
            {
              id: 'edge:mentions:process',
              source: 'document:document-ready',
              target: 'concept:process',
              kind: 'mentions',
              weight: 9,
            },
            {
              id: 'edge:co-occurs:process:scheduling',
              source: 'concept:process',
              target: 'concept:scheduling',
              kind: 'co_occurs',
              weight: 4,
            },
          ],
        },
      })
    }
    if (method === 'GET' && path === `/courses/${courseId}/lab/trace`) {
      return route.fulfill({
        json: {
          trace_id: 'trace-redacted-e2e',
          mode: 'rrf',
          revision_id: null,
          parser_backend: 'pdf-native',
          tokenizer_version: 'jieba-v1',
          embedding_model: 'BAAI/bge-m3',
          candidates: [
            { chunk_id: '91f2c9a31bb0', route: 'dense', rank: 1, score: 0.92 },
            {
              chunk_id: '288b62dff910',
              route: 'lexical',
              rank: 2,
              score: 0.81,
            },
            { chunk_id: '7901f8f1894d', route: 'rrf', rank: 1, score: 0.031 },
          ],
          citation_validation: 'passed',
          refusal_reason: null,
          timings_ms: { dense: 12.3, lexical: 4.2, total: 21.8 },
          usage: { input_tokens: 120, output_tokens: 36, estimated_cost: null },
        },
      })
    }
    return route.fulfill({
      status: 404,
      json: problem(404, 'RESOURCE_NOT_FOUND', `未模拟 ${method} ${path}`),
    })
  })
}
