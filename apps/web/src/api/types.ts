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
export type ParseRetryRequest = Schema<'ParseRetryRequest'>
export type DeletionAccepted = Schema<'DeletionAccepted'>
export type DeletionRecord = Schema<'DeletionResponse'>

export type CapabilityState = Schema<'CapabilityResponse'>
export type RuntimeCapabilities = Schema<'RuntimeCapabilitiesResponse'>

export type SourceLocator = Schema<'SourceLocator'>
export type BoundingBox = Schema<'BoundingBox'>

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

export type ConversationRecord = Schema<'ConversationResponse'>
export type ConversationCreate = Schema<'ConversationCreate'>

export interface JobEventData {
  status?: string
  phase?: string
  completed_pages?: number
  total_pages?: number
  code?: string
  failed_pages?: number[]
}

export type CitationSource = Schema<'CitationSourceResponse'>

export type NoteSource = Schema<'NoteSourceResponse'>
export type NoteRecord = Schema<'NoteResponse'>
export type NoteCreate = Schema<'NoteCreate'>
export type NotePatch = Schema<'NotePatch'>

export type RetrievalCandidate = Schema<'LabCandidateResponse'>
export type LabTrace = Schema<'LabTraceResponse'>

export interface EventEnvelope<T = Record<string, unknown>> {
  stream_version: '1'
  sequence: number
  occurred_at: string
  trace_id: string
  data: T
}
