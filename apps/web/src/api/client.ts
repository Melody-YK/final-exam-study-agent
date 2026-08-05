import type {
  AdminAccount,
  AdminAccountUpdate,
  AdminCourses,
  AdminDiagnostics,
  AdminDocument,
  AdminDocumentReviewRequest,
  AdminDocuments,
  AdminInvitationCreate,
  AdminInvitations,
  AdminNotes,
  AdminUserList,
  AuthUser,
  CitationSource,
  ConversationCreate,
  ConversationRecord,
  CorpusRole,
  Course,
  CourseCreate,
  CreateInvitationRequest,
  DeletionAccepted,
  DeletionRecord,
  DocumentCreate,
  DocumentRecord,
  DocumentUploadCreated,
  EventEnvelope,
  KnowledgeGraphResponse,
  LabTrace,
  LoginRequest,
  LearningSummary,
  LearningUnit,
  LearningUnitEvidenceItem,
  LearningUnitEvidenceSupplementRequest,
  VisionEvidenceReview,
  LearnerMemoryCreate,
  LearnerMemoryPatch,
  LearnerMemoryRecord,
  MergedNoteBatchRequest,
  NoteBatchSnapshot,
  NoteCreate,
  NoteImport,
  NotePatch,
  NoteRecord,
  ParseRetryRequest,
  PdfParserStrategy,
  ProblemDetails,
  PracticeAttemptRequest,
  PracticeAttemptResult,
  PracticeBatchRequest,
  PracticeBatchSnapshot,
  PracticeSessionRequest,
  PracticeSessionSnapshot,
  PracticeTutorConversation,
  PracticeTutorRequest,
  PracticeTutorResponse,
  QueryCreate,
  QueryConceptContext,
  QuerySnapshot,
  RegisterRequest,
  RuntimeCapabilities,
  ReviewQueueItem,
  SourcePreview,
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
  'retrieval.planned',
  'retrieval.started',
  'note.batch.created',
  'note.batch.running',
  'note.batch.cancelling',
  'note.batch.succeeded',
  'note.batch.failed',
  'note.batch.cancelled',
  'note.item.leased',
  'note.item.running',
  'note.item.phase',
  'note.item.succeeded',
  'note.item.failed',
  'note.item.cancelling',
  'note.item.cancelled',
  'note.preview.delta',
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
    if (response.status === 204) return undefined as T
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

const uploadMediaTypes: Readonly<Record<string, string>> = {
  pdf: 'application/pdf',
  md: 'text/markdown',
  markdown: 'text/markdown',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
}

function uploadMediaType(file: File): string {
  const separator = file.name.lastIndexOf('.')
  const extension = separator < 0 ? '' : file.name.slice(separator + 1).toLowerCase()
  return uploadMediaTypes[extension] ?? (file.type || 'application/octet-stream')
}

function jsonBody<T>(body: T): string {
  return JSON.stringify(body)
}

export async function sha256File(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, '0')).join('')
}

export class StudyApiClient {
  constructor(private readonly baseUrl = DEFAULT_API_BASE) {}

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const headers = new Headers(init?.headers)
    if (init?.body !== undefined && !(init.body instanceof Blob) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      credentials: 'include',
      headers,
    })
    return responseJson<T>(response)
  }

  createCourse(title: string): Promise<Course> {
    return this.request('/courses', {
      method: 'POST',
      body: jsonBody<CourseCreate>({ title }),
    })
  }

  listCourses(): Promise<Course[]> {
    return this.request('/courses')
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
    parserStrategy: PdfParserStrategy = 'enhanced',
  ): Promise<DocumentRecord> {
    const digest = await sha256File(file)
    const mediaType = uploadMediaType(file)
    onProgress?.(8)
    const created = await this.request<DocumentUploadCreated>(`/courses/${courseId}/documents`, {
      method: 'POST',
      signal,
      body: jsonBody<DocumentCreate>({
        filename: file.name,
        media_type: mediaType,
        size_bytes: file.size,
        sha256: digest,
        corpus_role: corpusRole,
      }),
    })
    onProgress?.(24)
    const uploadResponse = await fetch(created.upload.url, {
      method: 'PUT',
      headers: { 'Content-Type': mediaType },
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
        body: jsonBody<UploadCompleteRequest>({
          upload_session_id: created.upload.id,
          parser_strategy: parserStrategy,
        }),
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

  async currentUser(): Promise<AuthUser | null> {
    try {
      return await this.request('/auth/me')
    } catch (error) {
      if (error instanceof ApiError && error.problem.status === 401) return null
      throw error
    }
  }

  register(input: RegisterRequest): Promise<AuthUser> {
    return this.request('/auth/register', {
      method: 'POST',
      body: jsonBody(input),
    })
  }

  login(input: LoginRequest): Promise<AuthUser> {
    return this.request('/auth/login', {
      method: 'POST',
      body: jsonBody(input),
    })
  }

  logout(): Promise<void> {
    return this.request('/auth/logout', { method: 'POST' })
  }

  listAdminUsers(): Promise<AdminUserList> {
    return this.request('/admin/users')
  }

  updateAdminUser(accountId: string, input: AdminAccountUpdate): Promise<AdminAccount> {
    return this.request(`/admin/users/${accountId}`, {
      method: 'PATCH',
      body: jsonBody(input),
    })
  }

  listAdminInvitations(): Promise<AdminInvitations> {
    return this.request('/admin/invitations')
  }

  createAdminInvitation(expiresInDays = 7): Promise<AdminInvitationCreate> {
    return this.request('/admin/invitations', {
      method: 'POST',
      body: jsonBody<CreateInvitationRequest>({
        expires_in_days: expiresInDays,
      }),
    })
  }

  revokeAdminInvitation(invitationId: string): Promise<void> {
    return this.request(`/admin/invitations/${invitationId}`, {
      method: 'DELETE',
    })
  }

  adminDiagnostics(): Promise<AdminDiagnostics> {
    return this.request('/admin/diagnostics')
  }

  listAdminDocuments(reviewStatus?: AdminDocument['review_status']): Promise<AdminDocuments> {
    const query = reviewStatus ? `?review_status=${encodeURIComponent(reviewStatus)}` : ''
    return this.request(`/admin/documents${query}`)
  }

  reviewAdminDocument(
    documentId: string,
    input: AdminDocumentReviewRequest,
  ): Promise<AdminDocument> {
    return this.request(`/admin/documents/${encodeURIComponent(documentId)}/review`, {
      method: 'POST',
      body: jsonBody(input),
    })
  }

  adminDocumentContentUrl(documentId: string): string {
    return `${this.baseUrl}/admin/documents/${encodeURIComponent(documentId)}/content`
  }

  listAdminCourses(): Promise<AdminCourses> {
    return this.request('/admin/courses')
  }

  listAdminCourseNotes(courseId: string): Promise<AdminNotes> {
    return this.request(`/admin/courses/${encodeURIComponent(courseId)}/notes`)
  }

  getAdminCourseKnowledgeGraph(courseId: string): Promise<KnowledgeGraphResponse> {
    return this.request(
      `/admin/courses/${encodeURIComponent(courseId)}/knowledge-graph?node_limit=14&edge_limit=30`,
    )
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

  listLearnerMemories(courseId: string): Promise<LearnerMemoryRecord[]> {
    return this.request(`/courses/${encodeURIComponent(courseId)}/learner-memories`)
  }

  createLearnerMemory(courseId: string, input: LearnerMemoryCreate): Promise<LearnerMemoryRecord> {
    return this.request(`/courses/${encodeURIComponent(courseId)}/learner-memories`, {
      method: 'POST',
      body: jsonBody(input),
    })
  }

  updateLearnerMemory(memoryId: string, input: LearnerMemoryPatch): Promise<LearnerMemoryRecord> {
    return this.request(`/learner-memories/${encodeURIComponent(memoryId)}`, {
      method: 'PUT',
      body: jsonBody(input),
    })
  }

  deleteLearnerMemory(memoryId: string): Promise<void> {
    return this.request(`/learner-memories/${encodeURIComponent(memoryId)}`, {
      method: 'DELETE',
    })
  }

  createQuery(
    courseId: string,
    question: string,
    conversationId?: string,
    conceptContext?: QueryConceptContext,
  ): Promise<QuerySnapshot> {
    return this.request(`/courses/${courseId}/queries`, {
      method: 'POST',
      body: jsonBody<QueryCreate>({
        question,
        ...(conversationId === undefined ? {} : { conversation_id: conversationId }),
        ...(conceptContext === undefined ? {} : { concept_context: conceptContext }),
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

  getNoteSourcePreview(noteId: string, sourceId: string): Promise<SourcePreview> {
    return this.request(
      `/notes/${encodeURIComponent(noteId)}/sources/${encodeURIComponent(sourceId)}/preview`,
    )
  }

  getKnowledgeGraphSourcePreview(
    courseId: string,
    revisionId: string,
    chunkId: string,
  ): Promise<SourcePreview> {
    return this.request(
      `/courses/${encodeURIComponent(courseId)}/knowledge-graph/sources/${encodeURIComponent(revisionId)}/${encodeURIComponent(chunkId)}/preview`,
    )
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

  importNote(courseId: string, input: NoteImport): Promise<NoteRecord> {
    return this.request(`/courses/${courseId}/notes/import`, {
      method: 'POST',
      body: jsonBody(input),
    })
  }

  createNoteBatch(
    courseId: string,
    input: MergedNoteBatchRequest,
    commandKey?: string,
  ): Promise<NoteBatchSnapshot> {
    return this.request(`/courses/${courseId}/note-batches`, {
      method: 'POST',
      headers: {
        'Idempotency-Key': commandKey ?? idempotencyKey('note-batch-create'),
      },
      body: jsonBody(input),
    })
  }

  getNoteBatch(batchId: string): Promise<NoteBatchSnapshot> {
    return this.request(`/note-batches/${batchId}`)
  }

  createNoteRegenerationBatch(
    noteId: string,
    version: number,
    commandKey?: string,
  ): Promise<NoteBatchSnapshot> {
    return this.request(`/notes/${noteId}/regeneration-batches`, {
      method: 'POST',
      headers: {
        'If-Match': `"${version}"`,
        'Idempotency-Key': commandKey ?? idempotencyKey('note-batch-regenerate'),
      },
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

  listLearningUnits(courseId: string): Promise<LearningUnit[]> {
    return this.request(`/courses/${encodeURIComponent(courseId)}/learning-units`)
  }

  regenerateLearningUnits(courseId: string): Promise<LearningUnit[]> {
    return this.request(`/courses/${encodeURIComponent(courseId)}/learning-units/regenerate`, {
      method: 'POST',
    })
  }

  listLearningUnitEvidence(courseId: string, unitId: string): Promise<LearningUnitEvidenceItem[]> {
    return this.request(
      `/courses/${encodeURIComponent(courseId)}/learning-units/${encodeURIComponent(unitId)}/evidence`,
    )
  }

  createLearningUnitEvidenceSupplement(
    courseId: string,
    unitId: string,
    input: LearningUnitEvidenceSupplementRequest,
  ): Promise<LearningUnitEvidenceItem> {
    return this.request(
      `/courses/${encodeURIComponent(courseId)}/learning-units/${encodeURIComponent(unitId)}/evidence-supplements`,
      {
        method: 'POST',
        body: jsonBody(input),
      },
    )
  }

  revokeLearningUnitEvidenceSupplement(
    courseId: string,
    unitId: string,
    supplementId: string,
  ): Promise<void> {
    return this.request(
      `/courses/${encodeURIComponent(courseId)}/learning-units/${encodeURIComponent(unitId)}/evidence-supplements/${encodeURIComponent(supplementId)}`,
      { method: 'DELETE' },
    )
  }

  reviewLearningUnitEvidenceWithVision(
    courseId: string,
    unitId: string,
    sourceId: string,
  ): Promise<VisionEvidenceReview> {
    return this.request(
      `/courses/${encodeURIComponent(courseId)}/learning-units/${encodeURIComponent(unitId)}/evidence/${encodeURIComponent(sourceId)}/vision-review`,
      { method: 'POST' },
    )
  }

  getLearningSummary(courseId: string): Promise<LearningSummary> {
    return this.request(`/courses/${encodeURIComponent(courseId)}/learning-summary`)
  }

  getReviewQueue(courseId: string): Promise<ReviewQueueItem[]> {
    return this.request(`/courses/${encodeURIComponent(courseId)}/review-queue`)
  }

  createPracticeBatch(
    courseId: string,
    input: PracticeBatchRequest,
    commandKey?: string,
  ): Promise<PracticeBatchSnapshot> {
    return this.request(`/courses/${encodeURIComponent(courseId)}/practice-batches`, {
      method: 'POST',
      headers: {
        'Idempotency-Key': commandKey ?? idempotencyKey('practice-batch-create'),
      },
      body: jsonBody(input),
    })
  }

  getPracticeBatch(batchId: string): Promise<PracticeBatchSnapshot> {
    return this.request(`/practice-batches/${encodeURIComponent(batchId)}`)
  }

  createPracticeSession(
    courseId: string,
    input: PracticeSessionRequest,
  ): Promise<PracticeSessionSnapshot> {
    return this.request(`/courses/${encodeURIComponent(courseId)}/practice-sessions`, {
      method: 'POST',
      body: jsonBody(input),
    })
  }

  getPracticeSession(sessionId: string): Promise<PracticeSessionSnapshot> {
    return this.request(`/practice-sessions/${encodeURIComponent(sessionId)}`)
  }

  getPracticeTutorConversation(
    sessionId: string,
    questionId: string,
  ): Promise<PracticeTutorConversation> {
    return this.request(
      `/practice-sessions/${encodeURIComponent(sessionId)}/questions/${encodeURIComponent(questionId)}/tutor`,
    )
  }

  submitPracticeAttempt(
    sessionId: string,
    input: PracticeAttemptRequest,
    commandKey?: string,
  ): Promise<PracticeAttemptResult> {
    return this.request(`/practice-sessions/${encodeURIComponent(sessionId)}/attempts`, {
      method: 'POST',
      headers: {
        'Idempotency-Key': commandKey ?? idempotencyKey('practice-attempt'),
      },
      body: jsonBody(input),
    })
  }

  askPracticeTutor(
    sessionId: string,
    questionId: string,
    input: PracticeTutorRequest,
  ): Promise<PracticeTutorResponse> {
    return this.request(
      `/practice-sessions/${encodeURIComponent(sessionId)}/questions/${encodeURIComponent(questionId)}/tutor`,
      {
        method: 'POST',
        body: jsonBody(input),
      },
    )
  }

  subscribe<T>(
    path: string,
    onEvent: (event: EventEnvelope<T>) => void,
    onError?: () => void,
    onOpen?: () => void,
  ): () => void {
    if (typeof EventSource === 'undefined') {
      onError?.()
      return () => undefined
    }
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
