import type { components } from './generated/schema'

type Schema<Name extends keyof components['schemas']> = components['schemas'][Name]

export type CorpusRole = Schema<'CorpusRole'>

// ProblemDetails and SSE envelopes are runtime contracts that OpenAPI does not model yet.
export interface ProblemDetails {
  type: string
  title: string
  status: number
  code: string
  detail: string | null
  instance: string | null
  trace_id: string
  retryable: boolean
  retry_after_ms: number | null
  field_errors: Array<{
    location: Array<string | number>
    code: string
    message: string
  }>
}

export type Course = Schema<'CourseResponse'>
export type CourseCreate = Schema<'CourseCreate'>

type DocumentResponse = Schema<'DocumentResponse'>
type WorkspaceDocumentResponse = Schema<'WorkspaceDocumentResponse'>

export type DocumentRecord = DocumentResponse &
  Partial<Omit<WorkspaceDocumentResponse, keyof DocumentResponse>>
export type DocumentCreate = Schema<'DocumentCreate'>
export type UploadSession = Schema<'UploadSessionResponse'>
export type DocumentUploadCreated = Schema<'DocumentUploadCreated'>
export type UploadCompleteRequest = Schema<'UploadCompleteRequest'>
export type PdfParserStrategy = Schema<'PdfParserStrategy'>
export type ParseRetryRequest = Schema<'ParseRetryRequest'>
export type DeletionAccepted = Schema<'DeletionAccepted'>
export type DeletionRecord = Schema<'DeletionResponse'>

export type CapabilityState = Schema<'CapabilityResponse'>
export type RuntimeCapabilities = Schema<'RuntimeCapabilitiesResponse'>

export type SourceLocator = Schema<'SourceLocator'>
export type BoundingBox = Schema<'BoundingBox'>

export type LearningSourceStatus = Schema<'LearningSourceStatus'>
export type LearningUnit = Schema<'LearningUnit'>
export type LearningUnitKind = Schema<'LearningUnitKind'>
export type LearningUnitStatus = Schema<'LearningUnitStatus'>
export type LearningSummary = Schema<'LearningSummary'>
export type ReviewQueueItem = Schema<'ReviewQueueItem'>
export type MasteryLevel = Schema<'MasteryLevel'>
export type MasteryUpdate = Schema<'MasteryUpdate'>
export type AttemptOutcome = Schema<'AttemptOutcome'>
export type EvidenceReference = Schema<'EvidenceReference'>
export type QuestionOption = Schema<'QuestionOption'>
export type QuestionType = Schema<'QuestionType'>
export type QuestionStatus = Schema<'QuestionStatus'>
export type PracticeBatchStatus = Schema<'PracticeBatchStatus'>
export type PracticeBatchPhase = Schema<'PracticeBatchPhase'>
export type PracticeBatchRequest = Schema<'PracticeBatchRequest'>
export type PracticeBatchSnapshot = Schema<'PracticeBatchSnapshot'>
export type PracticeSessionStatus = Schema<'PracticeSessionStatus'>
export type PracticeSessionRequest = Schema<'PracticeSessionRequest'>
export type PracticeSessionSnapshot = Schema<'PracticeSessionSnapshot'>
export type PracticeQuestionView = Schema<'PracticeQuestionView'>
export type PracticeAttemptRequest = Schema<'PracticeAttemptRequest'>
export type PracticeAttemptResult = Schema<'PracticeAttemptResult'>
export type PracticeTutorMode = Schema<'PracticeTutorMode'>
export type PracticeTutorRequest = Schema<'PracticeTutorRequest'>
export type PracticeTutorResponse = Schema<'PracticeTutorResponse'>
export type PracticeTutorTurn = Schema<'PracticeTutorTurn'>

type GeneratedCitation = Schema<'Citation'>
export type Citation = Omit<GeneratedCitation, 'bounding_boxes'> & {
  bounding_boxes: BoundingBox[]
}

type GeneratedClaim = Schema<'Claim'>
export type AnswerClaim = Omit<GeneratedClaim, 'citation_ids'> & {
  citation_ids: string[]
}

type GeneratedStructuredAnswer = Schema<'StructuredAnswer'>
export type StructuredAnswer = Omit<GeneratedStructuredAnswer, 'claims' | 'citations'> & {
  claims: AnswerClaim[]
  citations: Citation[]
}

type GeneratedQueryResponse = Schema<'QueryResponse'>
export type QuerySnapshot = Omit<GeneratedQueryResponse, 'answer'> & {
  answer: StructuredAnswer | null
}
export type QueryCreate = Schema<'QueryCreate'>
export type QueryConceptContext = Schema<'QueryConceptContext'>

export type ConversationRecord = Schema<'ConversationResponse'>
export type ConversationCreate = Schema<'ConversationCreate'>

export interface JobEventData {
  status?: string
  phase?: string
  page_count?: number
  preview_revision_id?: string | null
  completed_pages?: number
  total_pages?: number
  code?: string
  failed_pages?: number[]
  retryable?: boolean
}

export type CitationSource = Schema<'CitationSourceResponse'>
export type SourcePreview = Schema<'SourcePreviewResponse'>

export type NoteSource = Schema<'NoteSourceResponse'>
export type NoteKnowledgePoint = Schema<'NoteKnowledgePointResponse'>
export type NoteRecord = Schema<'NoteResponse'>
export type NoteCreate = Schema<'NoteCreate'>
export type NotePatch = Schema<'NotePatch'>
export type NoteImport = Schema<'NoteImport'>
export type NoteBatchStyle = Schema<'NoteBatchStyle'>
type GeneratedMergedNoteBatchRequest = Schema<'MergedNoteBatchRequest'>
export type MergedNoteBatchRequest = Omit<GeneratedMergedNoteBatchRequest, 'style'> & {
  style: NoteBatchStyle
}
export type NoteBatchStatus = Schema<'NoteBatchStatus'>
export type NoteGenerationPhase = Schema<'NoteGenerationPhase'>
export type NoteItemSnapshot = Schema<'NoteItemSnapshot'>
type GeneratedNoteBatchSnapshot = Schema<'LocalDemoNoteBatchSnapshot'>
export type NoteBatchSnapshot = Omit<GeneratedNoteBatchSnapshot, 'style'> & {
  style: NoteBatchStyle
}

export interface NoteGenerationEventData {
  delta?: string
  phase?: string
  status?: string
  failure_code?: string | null
}

export type RetrievalCandidate = Schema<'LabCandidateResponse'>
export type LabTrace = Schema<'LabTraceResponse'>

export type AuthUser = Schema<'AccountResponse'>
export type AccountRole = AuthUser['role']
export type RegisterRequest = Schema<'RegisterRequest'>
export type LoginRequest = Schema<'LoginRequest'>
export type AdminUserList = Schema<'AdminUsersResponse'>
export type AdminAccount = Schema<'AdminAccountResponse'>
export type AdminAccountUpdate = Schema<'AdminAccountUpdateRequest'>
export type AdminInvitation = Schema<'InvitationResponse'>
export type AdminInvitationCreate = Schema<'InvitationCreateResponse'>
export type AdminInvitations = Schema<'AdminInvitationsResponse'>
export type CreateInvitationRequest = Schema<'CreateInvitationRequest'>
export type AdminDiagnostics = Schema<'AdminDiagnosticsResponse'>
export type AdminDocument = Schema<'AdminDocumentResponse'>
export type AdminDocuments = Schema<'AdminDocumentsResponse'>
export type AdminDocumentReviewRequest = Schema<'AdminDocumentReviewRequest'>
export type AdminCourse = Schema<'AdminCourseResponse'>
export type AdminCourses = Schema<'AdminCoursesResponse'>
export type AdminNote = Schema<'AdminNoteResponse'>
export type AdminNotes = Schema<'AdminNotesResponse'>

export type KnowledgeGraphOccurrence = Schema<'KnowledgeGraphOccurrenceResponse'>
export type KnowledgeGraphNode = Schema<'KnowledgeGraphNodeResponse'>
export type KnowledgeGraphNodeKind = KnowledgeGraphNode['kind']
export type KnowledgeGraphEdge = Schema<'KnowledgeGraphEdgeResponse'>
export type KnowledgeGraphEdgeKind = KnowledgeGraphEdge['kind']
export type KnowledgeGraphResponse = Schema<'KnowledgeGraphResponse'>

export interface EventEnvelope<T = Record<string, unknown>> {
  stream_version: '1'
  sequence: number
  occurred_at: string
  trace_id: string
  event_type: string
  data: T
}
