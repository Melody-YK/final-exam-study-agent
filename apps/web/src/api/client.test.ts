import { describe, expect, it, vi } from 'vitest'

import { noteRecord, problem } from '../test/fixtures'
import { documentRecord } from '../test/fixtures'
import { StudyApiClient } from './client'

describe('StudyApiClient', () => {
  it('hashes and completes the browser upload sequence without bypassing the API', async () => {
    const digest = new Uint8Array(32).fill(0xab).buffer
    vi.stubGlobal('crypto', {
      randomUUID: () => 'uuid-1',
      subtle: { digest: vi.fn().mockResolvedValue(digest) },
    })
    const file = new File(['pdf body'], 'notes.pdf', { type: 'application/pdf' })
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
        new Response(JSON.stringify(documentRecord({ id: 'document-upload', status: 'uploaded' })), {
          status: 202,
          headers: { 'Content-Type': 'application/json' },
        }),
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
    expect(new Headers(completeInit.headers).get('Idempotency-Key')).toBe(
      'upload-complete-uuid-1',
    )
    expect(JSON.parse(String(completeInit.body))).toEqual({ upload_session_id: 'upload-1' })
    expect(progress.mock.calls.map(([value]) => value)).toEqual([8, 24, 82, 100])
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
