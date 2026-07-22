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
      version === 1 ? '# 进程管理\n\n进程是资源分配的基本单位。' : '# 进程管理\n\n服务器上的最新正文。',
    version,
    generation: 2,
    generated_by_model: true,
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

function answered(
  question: string,
  queryId: string,
  conversationId: string,
  createdAt: string,
) {
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

function abstained(
  question: string,
  queryId: string,
  conversationId: string,
  createdAt: string,
) {
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
      refusal: { code: 'INSUFFICIENT_EVIDENCE', message: '当前课程资料没有足够依据。' },
    },
  }
}

export interface MockApiOptions {
  providerAvailable?: boolean
}

export async function installMockApi(page: Page, options: MockApiOptions = {}) {
  const providerAvailable = options.providerAvailable ?? true
  let notesVersion = 1
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
      active_revision_id: null,
      preview_revision_id: 'revision-preview',
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

  await page.addInitScript((id) => localStorage.setItem('study-agent.course-id', id), courseId)
  await page.route('**/api/v1/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace('/api/v1', '')
    const method = request.method()

    if (method === 'GET' && path === `/courses/${courseId}`) {
      return route.fulfill({ json: { id: courseId, title: '操作系统', lifecycle: 'active' } })
    }
    if (method === 'GET' && path === '/capabilities') {
      const providerStatus = providerAvailable ? 'available' : 'not_configured'
      return route.fulfill({
        json: {
          provider: { status: providerStatus, label: providerAvailable ? '可用' : '未配置' },
          embedding: { status: providerStatus, label: providerAvailable ? '可用' : '未配置' },
          native_parser: { status: 'available', label: '原生解析可用' },
          ocr_parser: { status: 'worker_required', label: '需要本地 OCR Worker' },
          demo_lab_enabled: true,
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
          }),
          upload: { id: 'upload-e2e', url: '/api/v1/uploads/upload-e2e', expires_at: '2099-01-01T00:00:00Z' },
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
        progress: { phase: 'queued', completed_pages: 0, total_pages: current.page_count },
        error_code: null,
      }
      documents = documents.map((item) => (item.id === id ? retried : item))
      return route.fulfill({ status: 202, json: retried })
    }
    if (method === 'DELETE' && path.startsWith('/documents/')) {
      const id = path.split('/')[2]
      documents = documents.filter((item) => item.id !== id)
      return route.fulfill({ status: 202, json: { deletion_id: 'deletion-e2e', status: 'pending' } })
    }
    if (method === 'GET' && path === '/deletions/deletion-e2e') {
      return route.fulfill({ json: { id: 'deletion-e2e', target_id: 'document-ready', target_type: 'document', deletion_epoch: 1, status: 'completed', attempt_count: 1, completed_at: '2026-07-19T05:30:00Z' } })
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
    if (
      method === 'GET' &&
      /^\/queries\/query-e2e-\d+\/citations\/citation-e2e$/.test(path)
    ) {
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
    if (method === 'GET' && path === `/courses/${courseId}/notes`) {
      return route.fulfill({ json: [note(notesVersion)] })
    }
    if (method === 'PATCH' && path === '/notes/note-e2e') {
      if (request.headers()['if-match'] !== '"1"') {
        return route.fulfill({
          status: 428,
          json: problem(428, 'PRECONDITION_REQUIRED', '需要 If-Match'),
        })
      }
      notesVersion = 2
      return route.fulfill({ status: 412, json: problem(412, 'VERSION_CONFLICT', '笔记版本冲突') })
    }
    if (method === 'POST' && path === '/notes/note-e2e/regenerate') {
      return route.fulfill({ json: note(notesVersion) })
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
            { chunk_id: '288b62dff910', route: 'lexical', rank: 2, score: 0.81 },
            { chunk_id: '7901f8f1894d', route: 'rrf', rank: 1, score: 0.031 },
          ],
          citation_validation: 'passed',
          refusal_reason: null,
          timings_ms: { dense: 12.3, lexical: 4.2, total: 21.8 },
          usage: { input_tokens: 120, output_tokens: 36, estimated_cost: null },
        },
      })
    }
    return route.fulfill({ status: 404, json: problem(404, 'RESOURCE_NOT_FOUND', `未模拟 ${method} ${path}`) })
  })
}
