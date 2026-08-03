import { describe, expect, it, vi } from 'vitest'

import {
  answeredSnapshot,
  documentRecord,
  noteRecord,
  problem,
  sourcePreview,
} from '../test/fixtures'
import type { NoteBatchSnapshot } from './types'
import { StudyApiClient } from './client'

describe('StudyApiClient', () => {
  it('uses cookie credentials for the account session lifecycle', async () => {
    const account = {
      id: 'account-1',
      email: 'student@example.com',
      display_name: '复习同学',
      role: 'user' as const,
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify(problem({ status: 401, code: 'AUTH_REQUIRED', title: '需要登录' })),
          {
            status: 401,
            headers: { 'Content-Type': 'application/problem+json' },
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(account), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new StudyApiClient('/api/v1')

    await expect(client.currentUser()).resolves.toBeNull()
    await expect(
      client.login({
        email: 'student@example.com',
        password: 'correct-password',
      }),
    ).resolves.toEqual(account)
    await expect(client.logout()).resolves.toBeUndefined()

    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({ credentials: 'include' }),
    )
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/v1/auth/login')
    expect(fetchMock.mock.calls[2]?.[0]).toBe('/api/v1/auth/logout')
  })

  it('sends invite-only registration and bounded admin access commands', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response('{}', {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response('{}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response('{}', {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response('{"items":[]}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new StudyApiClient('/api/v1')

    await client.register({
      email: 'student@example.com',
      password: 'correct-password',
      display_name: '复习同学',
      invite_code: 'invite-code-123456',
    })
    await client.updateAdminUser('account-2', {
      role: 'admin',
      status: 'active',
      admin_note: '课程助教',
    })
    await client.createAdminInvitation(14)
    await client.listAdminInvitations()
    await client.revokeAdminInvitation('invitation-1')

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/auth/register',
      '/api/v1/admin/users/account-2',
      '/api/v1/admin/invitations',
      '/api/v1/admin/invitations',
      '/api/v1/admin/invitations/invitation-1',
    ])
    expect(JSON.parse(String((fetchMock.mock.calls[0]?.[1] as RequestInit).body))).toEqual({
      email: 'student@example.com',
      password: 'correct-password',
      display_name: '复习同学',
      invite_code: 'invite-code-123456',
    })
    expect(fetchMock.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({ method: 'PATCH', credentials: 'include' }),
    )
    expect(JSON.parse(String((fetchMock.mock.calls[1]?.[1] as RequestInit).body))).toEqual({
      role: 'admin',
      status: 'active',
      admin_note: '课程助教',
    })
    expect(JSON.parse(String((fetchMock.mock.calls[2]?.[1] as RequestInit).body))).toEqual({
      expires_in_days: 14,
    })
    expect(fetchMock.mock.calls[4]?.[1]).toEqual(
      expect.objectContaining({ method: 'DELETE', credentials: 'include' }),
    )
  })

  it('sends only the optional conversation and graph context fields that are provided', async () => {
    const snapshot = answeredSnapshot()
    const fetchMock = vi.fn().mockImplementation(async () =>
      Promise.resolve(
        new Response(JSON.stringify(snapshot), {
          status: 202,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
    vi.stubGlobal('fetch', fetchMock)
    const client = new StudyApiClient('/api/v1')

    await client.createQuery('course-1', '首次提问')
    await client.createQuery('course-1', '追问', 'conversation-1')
    await client.createQuery('course-1', '图谱提问', undefined, {
      label: '进程',
      anchors: [
        {
          document_id: 'document-1',
          revision_id: 'revision-1',
          chunk_id: 'chunk-1',
        },
      ],
    })

    const firstInit = fetchMock.mock.calls[0]?.[1] as RequestInit
    const followUpInit = fetchMock.mock.calls[1]?.[1] as RequestInit
    const graphInit = fetchMock.mock.calls[2]?.[1] as RequestInit
    expect(JSON.parse(String(firstInit.body))).toEqual({
      question: '首次提问',
    })
    expect(JSON.parse(String(followUpInit.body))).toEqual({
      question: '追问',
      conversation_id: 'conversation-1',
    })
    expect(JSON.parse(String(graphInit.body))).toEqual({
      question: '图谱提问',
      concept_context: {
        label: '进程',
        anchors: [
          {
            document_id: 'document-1',
            revision_id: 'revision-1',
            chunk_id: 'chunk-1',
          },
        ],
      },
    })
  })

  it('lists the latest course queries with an explicit bounded limit', async () => {
    const snapshots = [answeredSnapshot()]
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshots), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const client = new StudyApiClient('/api/v1')

    await expect(client.listQueries('course-1')).resolves.toEqual(snapshots)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/courses/course-1/queries?limit=50',
      expect.objectContaining({ headers: expect.any(Headers) }),
    )
  })

  it('uses the generated note and knowledge-graph source preview routes', async () => {
    const preview = sourcePreview()
    const fetchMock = vi.fn().mockImplementation(
      async () =>
        new Response(JSON.stringify(preview), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const client = new StudyApiClient('/api/v1')

    await expect(client.getNoteSourcePreview('note/1', 'source/1')).resolves.toEqual(preview)
    await expect(
      client.getKnowledgeGraphSourcePreview('course/1', 'revision/1', 'chunk/1'),
    ).resolves.toEqual(preview)

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/notes/note%2F1/sources/source%2F1/preview',
      '/api/v1/courses/course%2F1/knowledge-graph/sources/revision%2F1/chunk%2F1/preview',
    ])
  })

  it('uses admin-scoped read routes for user course content', async () => {
    const fetchMock = vi.fn().mockImplementation(
      async () =>
        new Response('{}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const client = new StudyApiClient('/api/v1')

    await client.listAdminCourses()
    await client.listAdminCourseNotes('course/1')
    await client.getAdminCourseKnowledgeGraph('course/1')

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/admin/courses',
      '/api/v1/admin/courses/course%2F1/notes',
      '/api/v1/admin/courses/course%2F1/knowledge-graph?node_limit=14&edge_limit=30',
    ])
    expect(fetchMock.mock.calls.every(([, init]) => init.credentials === 'include')).toBe(true)
  })

  it('hashes and completes the browser upload sequence without bypassing the API', async () => {
    const digest = new Uint8Array(32).fill(0xab).buffer
    vi.stubGlobal('crypto', {
      randomUUID: () => 'uuid-1',
      subtle: { digest: vi.fn().mockResolvedValue(digest) },
    })
    const file = new File(['pdf body'], 'notes.pdf', {
      type: 'application/pdf',
    })
    Object.defineProperty(file, 'arrayBuffer', {
      value: vi.fn().mockResolvedValue(new Uint8Array([1, 2, 3]).buffer),
    })
    const created = {
      document: documentRecord({ id: 'document-upload' }),
      upload: {
        id: 'upload-1',
        url: '/api/v1/uploads/upload-1',
        expires_at: '2099-01-01T00:00:00Z',
      },
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(created), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ upload_session_id: 'upload-1', status: 'uploaded' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify(documentRecord({ id: 'document-upload', status: 'uploaded' })),
          {
            status: 202,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      )
    vi.stubGlobal('fetch', fetchMock)
    const progress = vi.fn()
    const client = new StudyApiClient('/api/v1')

    await client.uploadDocument('course-1', file, 'corpus', progress)

    expect(fetchMock).toHaveBeenCalledTimes(3)
    const [createUrl, createInit] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(createUrl).toBe('/api/v1/courses/course-1/documents')
    expect(JSON.parse(String(createInit.body))).toMatchObject({
      filename: 'notes.pdf',
      media_type: 'application/pdf',
      sha256: 'ab'.repeat(32),
      corpus_role: 'corpus',
    })
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/v1/uploads/upload-1')
    const [completeUrl, completeInit] = fetchMock.mock.calls[2] as [string, RequestInit]
    expect(completeUrl).toBe('/api/v1/documents/document-upload/upload:complete')
    expect(new Headers(completeInit.headers).get('Idempotency-Key')).toBe('upload-complete-uuid-1')
    expect(JSON.parse(String(completeInit.body))).toEqual({
      upload_session_id: 'upload-1',
      parser_strategy: 'enhanced',
    })
    expect(progress.mock.calls.map(([value]) => value)).toEqual([8, 24, 82, 100])
  })

  it('normalizes an untyped Markdown file to the backend media contract', async () => {
    const digest = new Uint8Array(32).fill(0xcd).buffer
    vi.stubGlobal('crypto', {
      randomUUID: () => 'uuid-1',
      subtle: { digest: vi.fn().mockResolvedValue(digest) },
    })
    const file = new File(['# 复习提纲'], 'outline.markdown')
    Object.defineProperty(file, 'arrayBuffer', {
      value: vi.fn().mockResolvedValue(new TextEncoder().encode('# 复习提纲').buffer),
    })
    const created = {
      document: documentRecord({ id: 'markdown-upload', media_type: 'text/markdown' }),
      upload: {
        id: 'upload-markdown',
        url: '/api/v1/uploads/upload-markdown',
        expires_at: '2099-01-01T00:00:00Z',
      },
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(created), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ upload_session_id: 'upload-markdown', status: 'uploaded' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(created.document), {
          status: 202,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await new StudyApiClient('/api/v1').uploadDocument('course-1', file, 'corpus')

    const createInit = fetchMock.mock.calls[0]?.[1] as RequestInit
    const uploadInit = fetchMock.mock.calls[1]?.[1] as RequestInit
    expect(JSON.parse(String(createInit.body))).toMatchObject({
      filename: 'outline.markdown',
      media_type: 'text/markdown',
    })
    expect(new Headers(uploadInit.headers).get('Content-Type')).toBe('text/markdown')
  })

  it('sends the optimistic note version in If-Match', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(noteRecord({ version: 4 })), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const client = new StudyApiClient('/api/v1')

    await client.updateNote('note-1', '# 更新', 3)

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/notes/note-1')
    expect(init.method).toBe('PATCH')
    expect(new Headers(init.headers).get('If-Match')).toBe('"3"')
    expect(JSON.parse(String(init.body))).toEqual({ body_markdown: '# 更新' })
  })

  it('sends the required title when creating a note', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(noteRecord()), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const client = new StudyApiClient('/api/v1')

    await client.createNote('course-1', ['第一章', '进程'], '进程基础')

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toEqual({
      section_path: ['第一章', '进程'],
      title: '进程基础',
    })
  })

  it('imports a Markdown note through the dedicated user-authored route', async () => {
    const imported = noteRecord({ generated_by_model: false, sources: [], knowledge_points: [] })
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(imported), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const client = new StudyApiClient('/api/v1')

    await expect(
      client.importNote('course/1', {
        title: '导入笔记',
        section_path: ['第一章'],
        body_markdown: '# 导入笔记\n\n正文',
      }),
    ).resolves.toEqual(imported)

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/courses/course/1/notes/import')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({
      title: '导入笔记',
      section_path: ['第一章'],
      body_markdown: '# 导入笔记\n\n正文',
    })
  })

  it('creates and reads an idempotent note batch', async () => {
    const snapshot = { id: 'note-batch-1' } as NoteBatchSnapshot
    const fetchMock = vi.fn().mockImplementation(
      async () =>
        new Response(JSON.stringify(snapshot), {
          status: 202,
          headers: { 'Content-Type': 'application/json' },
        }),
    )
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('crypto', { randomUUID: () => 'uuid-1' })
    const client = new StudyApiClient('/api/v1')

    await client.createNoteBatch(
      'course-1',
      {
        schema_version: '1.0',
        mode: 'merged',
        document_ids: ['document-1'],
        style: 'exam_focus',
      },
      'note-batch-command-1',
    )
    await client.getNoteBatch('note-batch-1')

    const [createUrl, createInit] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(createUrl).toBe('/api/v1/courses/course-1/note-batches')
    expect(createInit.method).toBe('POST')
    expect(new Headers(createInit.headers).get('Idempotency-Key')).toBe('note-batch-command-1')
    expect(JSON.parse(String(createInit.body))).toEqual({
      schema_version: '1.0',
      mode: 'merged',
      document_ids: ['document-1'],
      style: 'exam_focus',
    })
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/v1/note-batches/note-batch-1')
  })

  it('starts an exact-version idempotent note regeneration batch', async () => {
    const snapshot = { id: 'note-batch-regenerated' } as NoteBatchSnapshot
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        status: 202,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const client = new StudyApiClient('/api/v1')

    await client.createNoteRegenerationBatch('note-1', 3, 'note-regenerate-command-1')

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const headers = new Headers(init.headers)
    expect(url).toBe('/api/v1/notes/note-1/regeneration-batches')
    expect(init.method).toBe('POST')
    expect(headers.get('If-Match')).toBe('"3"')
    expect(headers.get('Idempotency-Key')).toBe('note-regenerate-command-1')
  })

  it('maps an API ProblemDetails response without replacing its code or trace', async () => {
    const apiProblem = problem({
      status: 409,
      code: 'INDEX_UNAVAILABLE',
      title: '索引不可用',
      trace_id: 'trace-api-1',
    })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(apiProblem), {
          status: 409,
          headers: { 'Content-Type': 'application/problem+json' },
        }),
      ),
    )
    const client = new StudyApiClient('/api/v1')

    await expect(client.getCourse('course-1')).rejects.toMatchObject({
      name: 'ApiError',
      problem: { code: 'INDEX_UNAVAILABLE', trace_id: 'trace-api-1' },
    })
  })

  it('adapts EventSource lifecycle and closes the stream', () => {
    class FakeEventSource {
      static latest: FakeEventSource
      readonly close = vi.fn()
      readonly listeners = new Map<string, Set<(event: { data: string }) => void>>()
      onopen: (() => void) | null = null
      onmessage: ((event: { data: string }) => void) | null = null
      onerror: (() => void) | null = null

      constructor(readonly url: string) {
        FakeEventSource.latest = this
      }

      readonly addEventListener = vi.fn(
        (eventType: string, listener: (event: { data: string }) => void) => {
          const listeners = this.listeners.get(eventType) ?? new Set()
          listeners.add(listener)
          this.listeners.set(eventType, listeners)
        },
      )

      readonly removeEventListener = vi.fn(
        (eventType: string, listener: (event: { data: string }) => void) => {
          this.listeners.get(eventType)?.delete(listener)
        },
      )

      dispatch(eventType: string, data: string): void {
        this.listeners.get(eventType)?.forEach((listener) => listener({ data }))
      }
    }
    vi.stubGlobal('EventSource', FakeEventSource)
    const client = new StudyApiClient('/api/v1')
    const onEvent = vi.fn()
    const onError = vi.fn()
    const onOpen = vi.fn()

    const close = client.subscribe('/parse-jobs/job-1/events', onEvent, onError, onOpen)
    const stream = FakeEventSource.latest
    stream.onopen?.()
    stream.dispatch(
      'job.page_checkpointed',
      JSON.stringify({
        stream_version: '1',
        sequence: 3,
        occurred_at: '2026-07-19T04:00:00Z',
        trace_id: 'trace-sse',
        event_type: 'job.page_checkpointed',
        data: { status: 'parsing' },
      }),
    )
    stream.onerror?.()
    close()

    expect(stream.url).toBe('/api/v1/parse-jobs/job-1/events')
    expect(onOpen).toHaveBeenCalledOnce()
    expect(onEvent).toHaveBeenCalledWith(
      expect.objectContaining({ sequence: 3, data: { status: 'parsing' } }),
    )
    expect(stream.addEventListener).toHaveBeenCalledWith(
      'job.page_checkpointed',
      expect.any(Function),
    )
    expect(onError).toHaveBeenCalledOnce()
    expect(stream.removeEventListener).toHaveBeenCalledWith(
      'job.page_checkpointed',
      expect.any(Function),
    )
    expect(stream.close).toHaveBeenCalledOnce()
  })
})
