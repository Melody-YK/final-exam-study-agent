import type {
  CitationSource,
  ConversationCreate,
  ConversationRecord,
  CorpusRole,
  Course,
  CourseCreate,
  DeletionAccepted,
  DeletionRecord,
  DocumentCreate,
  DocumentRecord,
  DocumentUploadCreated,
  EventEnvelope,
  LabTrace,
  NoteCreate,
  NotePatch,
  NoteRecord,
  ParseRetryRequest,
  ProblemDetails,
  QueryCreate,
  QuerySnapshot,
  RuntimeCapabilities,
  UploadCompleteRequest,
} from './types'

const DEFAULT_API_BASE = '/api/v1'
const EVENT_TYPES = [
  'job.artifact_uploaded',
  'job.cancelled',
  'job.checkpoint',
  'job.complete',
  'job.fail',
  'job.failed',
  'job.heartbeat',
  'job.leased',
  'job.page_checkpointed',
  'job.partial_failed',
  'job.queued',
  'job.requeued',
  'job.retry_scheduled',
  'job.start',
  'job.started',
  'job.succeeded',
  'answer.delta',
  'generation.started',
  'query.completed',
  'query.created',
  'query.failed',
  'retrieval.completed',
  'retrieval.started',
  'stream.reset',
] as const

export class ApiError extends Error {
  readonly problem: ProblemDetails

  constructor(problem: ProblemDetails) {
    super(problem.detail ?? problem.title)
    this.name = 'ApiError'
    this.problem = problem
  }
}

function fallbackProblem(response: Response): ProblemDetails {
  return {
    type: 'about:blank',
    title: response.statusText || '请求失败',
    status: response.status,
    code: 'UNEXPECTED_RESPONSE',
    detail: null,
    instance: null,
    trace_id: response.headers.get('X-Trace-ID') ?? 'unavailable',
    retryable: response.status >= 500,
    retry_after_ms: null,
    field_errors: [],
  }
}

async function responseJson<T>(response: Response): Promise<T> {
  if (response.ok) {
    return (await response.json()) as T
  }
  let problem = fallbackProblem(response)
  try {
    problem = (await response.json()) as ProblemDetails
  } catch {
    // A stable local fallback keeps proxy and network failures readable.
  }
  throw new ApiError(problem)
}

function idempotencyKey(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`
}

function jsonBody<T>(body: T): string {
  return JSON.stringify(body)
}

export async function sha256File(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, '0')).join(
    '',
  )
}

export class StudyApiClient {
  constructor(private readonly baseUrl = DEFAULT_API_BASE) {}

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const headers = new Headers(init?.headers)
    if (init?.body !== undefined && !(init.body instanceof Blob) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
    const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers })
    return responseJson<T>(response)
  }

  createCourse(title: string): Promise<Course> {
    return this.request('/courses', { method: 'POST', body: jsonBody<CourseCreate>({ title }) })
  }

  getCourse(courseId: string): Promise<Course> {
    return this.request(`/courses/${courseId}`)
  }

  listDocuments(courseId: string): Promise<DocumentRecord[]> {
    return this.request(`/courses/${courseId}/documents`)
  }

  async uploadDocument(
    courseId: string,
    file: File,
    corpusRole: CorpusRole,
    onProgress?: (progress: number) => void,
    signal?: AbortSignal,
  ): Promise<DocumentRecord> {
    const digest = await sha256File(file)
    onProgress?.(8)
    const created = await this.request<DocumentUploadCreated>(`/courses/${courseId}/documents`, {
      method: 'POST',
      signal,
      body: jsonBody<DocumentCreate>({
        filename: file.name,
        media_type: file.type || 'application/octet-stream',
        size_bytes: file.size,
        sha256: digest,
        corpus_role: corpusRole,
      }),
    })
    onProgress?.(24)
    const uploadResponse = await fetch(created.upload.url, {
      method: 'PUT',
      headers: { 'Content-Type': file.type || 'application/octet-stream' },
      body: file,
      signal,
    })
    await responseJson(uploadResponse)
    onProgress?.(82)
    const document = await this.request<DocumentRecord>(
      `/documents/${created.document.id}/upload:complete`,
      {
        method: 'POST',
        signal,
        headers: { 'Idempotency-Key': idempotencyKey('upload-complete') },
        body: jsonBody<UploadCompleteRequest>({ upload_session_id: created.upload.id }),
      },
    )
    onProgress?.(100)
    return document
  }

  retryDocument(documentId: string, failedPages?: number[]): Promise<DocumentRecord> {
    return this.request(`/documents/${documentId}/parse-jobs`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey('parse-retry') },
      body: jsonBody<ParseRetryRequest>({ failed_pages: failedPages ?? null }),
    })
  }

  deleteDocument(documentId: string): Promise<DeletionAccepted> {
    return this.request(`/documents/${documentId}`, {
      method: 'DELETE',
      headers: { 'Idempotency-Key': idempotencyKey('delete-document') },
    })
  }

  getDocument(documentId: string): Promise<DocumentRecord> {
    return this.request(`/documents/${documentId}`)
  }

  getDeletion(deletionId: string): Promise<DeletionRecord> {
    return this.request(`/deletions/${deletionId}`)
  }

  capabilities(): Promise<RuntimeCapabilities> {
    return this.request('/capabilities')
  }

  createConversation(courseId: string, title?: string): Promise<ConversationRecord> {
    return this.request(`/courses/${courseId}/conversations`, {
      method: 'POST',
      body: jsonBody<ConversationCreate>(title ? { title } : {}),
    })
  }

  listConversations(courseId: string): Promise<ConversationRecord[]> {
    return this.request(`/courses/${courseId}/conversations`)
  }

  listConversationQueries(conversationId: string, limit = 100): Promise<QuerySnapshot[]> {
    return this.request(`/conversations/${conversationId}/queries?limit=${limit}`)
  }

  createQuery(
    courseId: string,
    question: string,
    conversationId?: string,
  ): Promise<QuerySnapshot> {
    return this.request(`/courses/${courseId}/queries`, {
      method: 'POST',
      body: jsonBody<QueryCreate>({
        question,
        ...(conversationId === undefined ? {} : { conversation_id: conversationId }),
      }),
    })
  }

  listQueries(courseId: string, limit = 50): Promise<QuerySnapshot[]> {
    return this.request(`/courses/${courseId}/queries?limit=${limit}`)
  }

  getQuery(queryId: string): Promise<QuerySnapshot> {
    return this.request(`/queries/${queryId}`)
  }

  getCitation(queryId: string, citationId: string): Promise<CitationSource> {
    return this.request(`/queries/${queryId}/citations/${citationId}`)
  }

  listNotes(courseId: string): Promise<NoteRecord[]> {
    return this.request(`/courses/${courseId}/notes`)
  }

  createNote(courseId: string, sectionPath: string[], title: string): Promise<NoteRecord> {
    return this.request(`/courses/${courseId}/notes`, {
      method: 'POST',
      body: jsonBody<NoteCreate>({ section_path: sectionPath, title }),
    })
  }

  updateNote(noteId: string, bodyMarkdown: string, version: number): Promise<NoteRecord> {
    return this.request(`/notes/${noteId}`, {
      method: 'PATCH',
      headers: { 'If-Match': `"${version}"` },
      body: jsonBody<NotePatch>({ body_markdown: bodyMarkdown }),
    })
  }

  regenerateNote(noteId: string): Promise<NoteRecord> {
    return this.request(`/notes/${noteId}/regenerate`, { method: 'POST' })
  }

  getLabTrace(courseId: string): Promise<LabTrace> {
    return this.request(`/courses/${courseId}/lab/trace`)
  }

  subscribe<T>(
    path: string,
    onEvent: (event: EventEnvelope<T>) => void,
    onError?: () => void,
    onOpen?: () => void,
  ): () => void {
    const stream = new EventSource(`${this.baseUrl}${path}`)
    stream.onopen = () => onOpen?.()
    const handleEvent = (message: MessageEvent<string>) => {
      onEvent(JSON.parse(message.data) as EventEnvelope<T>)
    }
    stream.onmessage = handleEvent
    EVENT_TYPES.forEach((eventType) => stream.addEventListener(eventType, handleEvent))
    stream.onerror = () => onError?.()
    return () => {
      EVENT_TYPES.forEach((eventType) => stream.removeEventListener(eventType, handleEvent))
      stream.close()
    }
  }
}

export const studyApi = new StudyApiClient()
