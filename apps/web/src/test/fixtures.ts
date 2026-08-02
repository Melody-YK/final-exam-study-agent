import type {
  Citation,
  CitationSource,
  DocumentRecord,
  LabTrace,
  NoteRecord,
  ProblemDetails,
  QuerySnapshot,
  SourcePreview,
  StructuredAnswer,
} from '../api/types'

export function problem(overrides: Partial<ProblemDetails> = {}): ProblemDetails {
  return {
    type: 'about:blank',
    title: '请求失败',
    status: 500,
    code: 'UNEXPECTED_ERROR',
    detail: '请求未完成',
    instance: null,
    trace_id: 'trace-test',
    retryable: false,
    retry_after_ms: null,
    field_errors: [],
    ...overrides,
  }
}

export function documentRecord(overrides: Partial<DocumentRecord> = {}): DocumentRecord {
  return {
    id: 'document-1',
    course_id: 'course-1',
    filename: 'chapter-1.pdf',
    media_type: 'application/pdf',
    corpus_role: 'corpus',
    verified_sha256: 'a'.repeat(64),
    status: 'ready',
    review_status: 'approved',
    preview_revision_id: null,
    active_revision_id: 'revision-1',
    deletion_epoch: 0,
    indexable: true,
    page_count: 12,
    failed_pages: [],
    updated_at: '2026-07-19T04:00:00Z',
    error_code: null,
    ...overrides,
  }
}

export const citation: Citation = {
  id: 'citation-1',
  document_id: 'document-1',
  revision_id: 'revision-1',
  chunk_id: 'chunk-1',
  document_name: 'chapter-1.png',
  locator: { kind: 'page', ordinal: 3 },
  quote: '进程拥有独立的地址空间。',
  bounding_boxes: [{ x: 0.1, y: 0.2, width: 0.4, height: 0.08 }],
}

export function answeredSnapshot(overrides: Partial<QuerySnapshot> = {}): QuerySnapshot {
  const answer: StructuredAnswer = {
    schema_version: '1.0',
    query_id: 'query-1',
    status: 'answered',
    answer_markdown: '进程是资源分配的基本单位。',
    claims: [
      {
        id: 'claim-1',
        text: '进程拥有独立地址空间。',
        citation_ids: [citation.id],
      },
    ],
    citations: [citation],
    refusal: null,
  }
  return {
    id: 'query-1',
    course_id: 'course-1',
    conversation_id: 'conversation-1',
    question: '什么是进程？',
    status: 'answered',
    answer,
    failure_code: null,
    usage: { input_tokens: 18, output_tokens: 12 },
    trace: {
      trace_id: 'query-trace-1',
      retrieval_snapshot_id: 'snapshot-1',
      retrieval_trace_id: 'retrieval-trace-1',
    },
    created_at: '2026-07-19T04:00:00Z',
    completed_at: '2026-07-19T04:00:01Z',
    ...overrides,
  }
}

export function citationSource(overrides: Partial<CitationSource> = {}): CitationSource {
  return {
    citation_id: citation.id,
    document_id: citation.document_id,
    revision_id: citation.revision_id,
    chunk_id: citation.chunk_id,
    document_name: citation.document_name,
    locator: citation.locator,
    quote: citation.quote,
    bounding_boxes: citation.bounding_boxes,
    provenance: ['native'],
    read_url: '/api/v1/sources/chapter-1.png',
    read_url_expires_at: '2099-01-01T00:00:00Z',
    media_type: 'image/png',
    ...overrides,
  }
}

export function sourcePreview(overrides: Partial<SourcePreview> = {}): SourcePreview {
  return {
    source_id: 'note-source-1',
    document_id: citation.document_id,
    revision_id: citation.revision_id,
    chunk_id: citation.chunk_id,
    document_name: citation.document_name,
    locator: citation.locator,
    section_path: [],
    quote: citation.quote,
    bounding_boxes: citation.bounding_boxes,
    provenance: ['native'],
    read_url: '/api/v1/sources/chapter-1.png',
    read_url_expires_at: '2099-01-01T00:00:00Z',
    media_type: 'image/png',
    ...overrides,
  }
}

export function noteRecord(overrides: Partial<NoteRecord> = {}): NoteRecord {
  return {
    id: 'note-1',
    course_id: 'course-1',
    section_path: ['第一章', '进程'],
    title: '进程基础',
    body_markdown: '# 进程\n\n原始正文',
    version: 1,
    generation: 1,
    generated_by_model: true,
    origin_batch_id: null,
    status: 'ready',
    sources: [
      {
        id: 'note-source-1',
        evidence_id: 'evidence-1',
        document_id: 'document-1',
        revision_id: 'revision-1',
        chunk_id: 'chunk-1',
        document_name: 'chapter-1.pdf',
        locator: { kind: 'page', ordinal: 3 },
        quote: '进程拥有独立的地址空间。',
        bounding_boxes: [],
        provenance: ['native'],
        available: true,
        stale: false,
        unavailable_reason: null,
      },
    ],
    knowledge_points: [],
    created_at: '2026-07-19T04:00:00Z',
    updated_at: '2026-07-19T04:00:00Z',
    ...overrides,
  }
}

export function labTrace(overrides: Partial<LabTrace> = {}): LabTrace {
  return {
    trace_id: 'trace-redacted',
    mode: 'hybrid',
    revision_id: 'revision-1',
    parser_backend: 'pymupdf',
    tokenizer_version: 'v1',
    embedding_model: 'text-embedding-model',
    candidates: [
      { chunk_id: '91f2c9a31bb0', route: 'dense', rank: 1, score: 0.92 },
      { chunk_id: '288b62dff910', route: 'lexical', rank: 2, score: 0.81 },
      { chunk_id: '7901f8f1894d', route: 'rrf', rank: 1, score: 0.73 },
    ],
    citation_validation: 'passed',
    refusal_reason: null,
    timings_ms: { retrieval: 12.4, generation: 87.2 },
    usage: { input_tokens: 100, output_tokens: 40, estimated_cost: 0.0012 },
    ...overrides,
  }
}
