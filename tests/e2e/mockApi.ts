import { createHash } from 'node:crypto'

import type { Page, Route } from '@playwright/test'

const courseId = 'course-e2e'
const secondaryCourseId = 'course-data-structures-e2e'
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

function learningEvidence(quote: string, chunkId: string) {
  return {
    chunk_id: chunkId,
    content_sha256: 'b'.repeat(64),
    document_id: 'document-ready',
    document_name: '操作系统课程.pdf',
    locator: { kind: 'page', ordinal: 6 },
    quote,
    revision_id: 'revision-active',
  }
}

function learningUnit(
  id: string,
  label: string,
  kind: 'section' | 'concept',
  status: 'available' | 'stale' | 'unavailable' = 'available',
) {
  return {
    id,
    course_id: courseId,
    canonical_key: id,
    label,
    kind,
    parent_id: null,
    status,
    mastery_level: 'new' as const,
    next_review_at: null,
    sources: [
      {
        document_id: 'document-ready',
        revision_id: 'revision-active',
        chunk_id: `${id}-chunk`,
        content_sha256: 'b'.repeat(64),
        locator: { kind: 'page', ordinal: 6 },
        status: status === 'available' ? 'valid' : status === 'stale' ? 'stale' : 'unavailable',
      },
    ],
  }
}

function learningUnitEvidence(overrides: Record<string, unknown> = {}) {
  return {
    id: 'unit-process-source',
    unit_id: 'unit-process',
    source_id: 'unit-process-source',
    supplement_id: null,
    origin: 'parsed' as const,
    role: null,
    document_id: 'document-ready',
    document_name: '操作系统课程.pdf',
    revision_id: 'revision-active',
    chunk_id: 'unit-process-chunk',
    content_sha256: 'b'.repeat(64),
    locator: { kind: 'page' as const, ordinal: 6 },
    text: '原解析的进程管理片段，缺少完整题干和参考解答。',
    is_primary: true,
    practice_status: 'insufficient_evidence' as const,
    confidence_note: '有效正文不足，等待用户补充完整原型。',
    created_at: '2026-08-02T08:00:00Z',
    ...overrides,
  }
}

function practiceQuestion(
  id: string,
  learningUnitId: string,
  prompt: string,
  correctAnswer: string,
  quote: string,
) {
  return {
    id,
    learning_unit_id: learningUnitId,
    prompt,
    question_type: 'single_choice' as const,
    difficulty: 1,
    options: [
      { id: 'a', label: '进程负责资源分配' },
      { id: 'b', label: '线程负责资源分配' },
    ],
    status: 'ready' as const,
    evidence_refs: [learningEvidence(quote, `${id}-chunk`)],
    correctAnswer,
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
        document_id: 'document-markdown-ready',
        revision_id: 'revision-markdown-active',
        chunk_id: 'chunk-markdown-e2e',
        document_name: '调度算法.md',
        locator: { kind: 'section', ordinal: 2 },
        quote: '进程是资源分配的基本单位。',
        bounding_boxes: [],
        provenance: ['markdown-native@1'],
        available: true,
        stale: false,
        unavailable_reason: null,
      },
    ],
    knowledge_points: [
      {
        id: 'knowledge-point-e2e',
        text: '进程是资源分配的基本单位。',
        source_ids: ['note-source-e2e'],
      },
    ],
    created_at: '2026-07-19T05:00:00Z',
    updated_at: '2026-07-19T05:10:00Z',
  }
}

function adminNote(
  id: string,
  targetCourseId: string,
  title: string,
  bodyMarkdown: string,
  sectionPath: string[],
) {
  return {
    id,
    course_id: targetCourseId,
    section_path: sectionPath,
    title,
    body_markdown: bodyMarkdown,
    version: 1,
    generation: 1,
    generated_by_model: true,
    status: 'ready',
    created_at: '2026-07-22T07:00:00Z',
    updated_at: '2026-07-23T09:00:00Z',
  }
}

function adminKnowledgeGraph({
  targetCourseId,
  courseTitle,
  documentId,
  documentName,
  conceptId,
  conceptLabel,
}: {
  targetCourseId: string
  courseTitle: string
  documentId: string
  documentName: string
  conceptId: string
  conceptLabel: string
}) {
  return {
    course_id: targetCourseId,
    tokenizer_version: 'jieba-v1',
    active_document_count: 1,
    included_document_count: 1,
    source_chunk_count: 6,
    node_limit: 14,
    edge_limit: 30,
    truncated: false,
    nodes: [
      {
        id: `course:${targetCourseId}`,
        kind: 'course',
        label: courseTitle,
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
        id: `document:${documentId}`,
        kind: 'document',
        label: documentName,
        document_id: documentId,
        revision_id: `revision:${documentId}`,
        page_count: 20,
        frequency: null,
        document_count: null,
        occurrence_count: null,
        occurrences: [],
        occurrences_truncated: false,
      },
      {
        id: `concept:${conceptId}`,
        kind: 'concept',
        label: conceptLabel,
        document_id: null,
        revision_id: null,
        page_count: null,
        frequency: 8,
        document_count: 1,
        occurrence_count: 1,
        occurrences: [],
        occurrences_truncated: false,
      },
    ],
    edges: [
      {
        id: `edge:contains:${documentId}`,
        source: `course:${targetCourseId}`,
        target: `document:${documentId}`,
        kind: 'contains',
        weight: 1,
      },
      {
        id: `edge:mentions:${conceptId}`,
        source: `document:${documentId}`,
        target: `concept:${conceptId}`,
        kind: 'mentions',
        weight: 8,
      },
    ],
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
        '## 进程与线程.pdf\n\n- 进程是资源分配的基本单位。\n- 线程是调度的基本单位。\n\n## 调度算法.md\n\n- 就绪队列决定调度候选。',
    },
    outline: {
      label: '结构提纲',
      content:
        '## 1. 进程与线程.pdf\n\n### 1.1 第 1 页\n\n1. 进程与线程\n2. 调度与同步\n3. 死锁处理\n\n## 2. 调度算法.md\n\n### 2.1 章节: 进程调度',
    },
    complete: {
      label: '完整讲义',
      content:
        '## 进程与线程.pdf\n\n### 第 1 页\n\n进程是资源分配的基本单位，线程是调度的基本单位。完整讲义按来源顺序保留资料中的定义、例子和上下文。\n\n## 调度算法.md\n\n### 章节: 进程调度\n\n就绪队列决定调度候选。',
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
  accountCapacity?: number
  accountRole?: 'admin' | 'user'
  authenticated?: boolean
  includeNoteEligibilityDriftDocuments?: boolean
  invitationCapacityRaceOnce?: boolean
  noteBatchPollsBeforeSuccess?: number
  practiceBatchPollsBeforeSuccess?: number
  practiceBatchPartialSuccess?: boolean
  learningQuestionStale?: boolean
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
    {
      id: 'account-student-b-e2e',
      email: 'lilin@example.com',
      display_name: '李琳',
      role: 'user' as const,
      status: 'active' as const,
      admin_note: null,
      created_at: '2026-07-22T08:00:00Z',
    },
  ]
  const adminCourses = [
    {
      id: courseId,
      title: '操作系统',
      lifecycle: 'active',
      owner_account_id: 'account-student-e2e',
      owner_email: 'student@example.com',
      owner_display_name: '复习同学',
      owner_subject: 'student@example.com',
      document_count: 3,
      note_count: 1,
      created_at: '2026-07-20T08:00:00Z',
      updated_at: '2026-07-24T08:00:00Z',
    },
    {
      id: secondaryCourseId,
      title: '数据结构',
      lifecycle: 'active',
      owner_account_id: 'account-student-b-e2e',
      owner_email: 'lilin@example.com',
      owner_display_name: '李琳',
      owner_subject: 'lilin@example.com',
      document_count: 1,
      note_count: 1,
      created_at: '2026-07-21T08:30:00Z',
      updated_at: '2026-07-25T08:30:00Z',
    },
  ]
  const adminNotes = new Map([
    [
      courseId,
      [
        adminNote(
          'note-e2e',
          courseId,
          '进程管理',
          '# 进程管理\n\n进程是资源分配的基本单位。',
          ['第二章', '进程管理'],
        ),
      ],
    ],
    [
      secondaryCourseId,
      [
        adminNote(
          'note-data-structures-e2e',
          secondaryCourseId,
          'AVL 树复习笔记',
          '# AVL 树复习笔记\n\n平衡因子用于判断旋转方向。',
          ['第六章', '平衡树'],
        ),
      ],
    ],
  ])
  const adminGraphs = new Map([
    [
      courseId,
      adminKnowledgeGraph({
        targetCourseId: courseId,
        courseTitle: '操作系统',
        documentId: 'document-ready',
        documentName: '进程与线程.pdf',
        conceptId: 'process',
        conceptLabel: '进程',
      }),
    ],
    [
      secondaryCourseId,
      adminKnowledgeGraph({
        targetCourseId: secondaryCourseId,
        courseTitle: '数据结构',
        documentId: 'document-tree-slides',
        documentName: '树与图.pptx',
        conceptId: 'balance-factor',
        conceptLabel: '平衡因子',
      }),
    ],
  ])
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
  const accountCapacity = options.accountCapacity ?? 10
  const capacityNow = new Date('2026-07-24T08:00:00Z')
  let invitationCapacityRacePending = options.invitationCapacityRaceOnce ?? false
  const activeAccountCount = () =>
    adminAccounts.filter((account) => account.status === 'active').length
  const availableInvitationCount = () =>
    invitations.filter(
      (invitation) =>
        invitation.used_at === null &&
        invitation.revoked_at === null &&
        new Date(invitation.expires_at).getTime() > capacityNow.getTime(),
    ).length
  const availableAccountSeats = () =>
    Math.max(accountCapacity - activeAccountCount() - availableInvitationCount(), 0)
  const noteBatchPollsBeforeSuccess = options.noteBatchPollsBeforeSuccess ?? 3
  const practiceBatchPollsBeforeSuccess = options.practiceBatchPollsBeforeSuccess ?? 2
  const providerAvailable = options.providerAvailable ?? true
  const learningUnits = [
    learningUnit('unit-process', '进程管理', 'section'),
    learningUnit('unit-scheduling', '进程调度', 'concept'),
    learningUnit('unit-stale', '旧版资料引用', 'concept', 'stale'),
  ]
  const learningQuestions = [
    practiceQuestion('question-process', 'unit-process', '进程在系统中主要承担什么职责？', 'a', '进程是资源分配的基本单位。'),
    practiceQuestion('question-scheduling', 'unit-scheduling', '线程在调度模型中通常承担什么职责？', 'b', '线程是调度的基本单位。'),
  ]
  const reviewQueue = [
    {
      learning_unit_id: 'unit-scheduling',
      label: '进程调度',
      kind: 'concept' as const,
      mastery_level: 'learning' as const,
      weakness_score: 0.72,
      next_review_at: '2026-08-02T09:00:00Z',
      source_status: 'valid' as const,
    },
  ]
  let learningSummary = {
    course_id: courseId,
    accuracy: 0,
    correct_questions: 0,
    total_questions: 0,
    due_review_count: reviewQueue.length,
    next_action: '先复习进程调度，再开始下一组题。',
    units: learningUnits,
    weak_units: reviewQueue,
  }
  let learningBatchPolls = 0
  let learningBatchId = 'practice-batch-e2e'
  let learningBatchPayload: { learning_unit_ids: string[]; question_count: number } | null = null
  let learningBatchQuestionIds: string[] = []
  let learningBatchCommandKey: string | null = null
  let learningEvidenceSupplement: Record<string, unknown> | null = null
  let practiceSession: Record<string, unknown> | null = null
  const practiceAttempts = new Map<string, Record<string, unknown>>()
  let notesVersion = 1
  let generatedNoteRecord: ReturnType<typeof generatedNote> | null = null
  let importedNoteRecord: ReturnType<typeof note> | null = null
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
  let secondaryAdminDocuments = [
    document({
      id: 'document-tree-slides',
      course_id: secondaryCourseId,
      filename: '树与图.pptx',
      media_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      status: 'ready',
      review_status: 'approved',
      active_revision_id: 'revision-tree-slides',
      preview_revision_id: null,
      indexable: true,
      page_count: 20,
    }),
  ]
  type MockReviewMetadata = {
    review_note: string | null
    reviewed_by_account_id: string | null
    reviewed_by_email: string | null
    reviewed_at: string | null
  }
  const reviewMetadata = new Map<string, MockReviewMetadata>()
  const allAdminDocuments = () => [...documents, ...secondaryAdminDocuments]
  const adminDocument = (item: (typeof documents)[number]) => {
    const review = reviewMetadata.get(item.id) ?? {
      review_note: null,
      reviewed_by_account_id: null,
      reviewed_by_email: null,
      reviewed_at: null,
    }
    const secondaryCourse = item.course_id === secondaryCourseId
    return {
      id: item.id,
      course_id: item.course_id,
      course_title: secondaryCourse ? '数据结构' : '操作系统',
      owner_account_id: secondaryCourse ? 'account-student-b-e2e' : 'account-student-e2e',
      owner_email: secondaryCourse ? 'lilin@example.com' : 'student@example.com',
      owner_display_name: secondaryCourse ? '李琳' : '复习同学',
      owner_subject: secondaryCourse ? 'lilin@example.com' : 'student@example.com',
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
        id: 'document-markdown-ready',
        filename: '调度算法.md',
        media_type: 'text/markdown',
        active_revision_id: 'revision-markdown-active',
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

  function practiceBatchSnapshot(status: 'queued' | 'running' | 'partial_success' | 'succeeded' | 'failed') {
    const totalItems = learningBatchQuestionIds.length
    const completedItems = status === 'succeeded' || status === 'partial_success' ? totalItems : 0
    return {
      id: learningBatchId,
      course_id: courseId,
      learning_unit_ids: (learningBatchPayload?.learning_unit_ids ?? []).slice(),
      target_question_count: learningBatchPayload?.question_count ?? 0,
      total_items: totalItems,
      completed_items: completedItems,
      status,
      phase: status === 'running' ? 'generating' : status === 'queued' ? 'validating_inputs' : null,
      question_ids: learningBatchQuestionIds,
      items: learningQuestions.map((question) => ({
        id: `practice-item-${question.id}`,
        attempt_count: status === 'queued' ? 0 : 1,
        failure_code: null,
        question_id: learningBatchQuestionIds.includes(question.id) ? question.id : null,
        status: learningBatchQuestionIds.includes(question.id)
          ? status === 'queued' || status === 'running'
            ? 'queued'
            : 'succeeded'
          : 'failed',
      })),
      failure_code: status === 'failed' ? 'PRACTICE_PROVIDER_UNAVAILABLE' : null,
      created_at: '2026-08-02T08:00:00Z',
      started_at: status === 'queued' ? null : '2026-08-02T08:00:01Z',
      completed_at: status === 'succeeded' || status === 'partial_success' ? '2026-08-02T08:00:03Z' : null,
    }
  }

  function practiceSessionSnapshot() {
    const questions = learningQuestions
      .filter((question) => learningBatchQuestionIds.includes(question.id))
      .map((question) => {
        const attempt = Array.from(practiceAttempts.values()).find(
          (attempt) => attempt.question_id === question.id,
        )
        const answered = attempt !== undefined
        const mastery = attempt?.mastery as { reason?: string } | undefined
        return {
          id: question.id,
          learning_unit_id: question.learning_unit_id,
          prompt: question.prompt,
          question_type: question.question_type,
          difficulty: question.difficulty,
          options: question.options,
          status: options.learningQuestionStale && question.id === 'question-scheduling' ? 'stale' : question.status,
          evidence_refs:
            options.learningQuestionStale && question.id === 'question-scheduling'
              ? []
              : question.evidence_refs,
          answered,
          outcome: attempt?.outcome ?? null,
          submitted_answer: answered ? attempt.submitted_answer : null,
          explanation: answered ? attempt.explanation : null,
          mastery_reason: answered ? (mastery?.reason ?? '掌握度已更新。') : null,
          viewed_hint: answered ? Boolean(attempt.viewed_hint) : null,
        }
      })
    const complete = questions.length > 0 && questions.every((question) => question.answered)
    return {
      id: 'practice-session-e2e',
      course_id: courseId,
      question_count: questions.length,
      questions,
      started_at: '2026-08-02T08:01:00Z',
      completed_at: complete ? '2026-08-02T08:03:00Z' : null,
      status: complete ? 'completed' : 'active',
    }
  }

  if (accountRole !== 'admin' && options.seedCourseSelection !== false) {
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
      if (
        current.status === 'suspended' &&
        payload.status === 'active' &&
        availableAccountSeats() === 0
      ) {
        return route.fulfill({
          status: 409,
          json: problem(409, 'ACCOUNT_CAPACITY_REACHED', '账号容量已满'),
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
      if (invitationCapacityRacePending) {
        invitationCapacityRacePending = false
        if (availableAccountSeats() > 0) {
          invitations = [
            {
              id: 'invitation-e2e-capacity-race',
              created_by_account_id: mockAccount.id,
              used_by_account_id: null,
              status: 'available',
              created_at: capacityNow.toISOString(),
              expires_at: new Date(
                capacityNow.getTime() + payload.expires_in_days * 24 * 60 * 60 * 1_000,
              ).toISOString(),
              used_at: null,
              revoked_at: null,
            },
            ...invitations,
          ]
        }
        return route.fulfill({
          status: 409,
          json: problem(409, 'ACCOUNT_CAPACITY_REACHED', '账号容量已满'),
        })
      }
      if (availableAccountSeats() === 0) {
        return route.fulfill({
          status: 409,
          json: problem(409, 'ACCOUNT_CAPACITY_REACHED', '账号容量已满'),
        })
      }
      invitationSequence += 1
      const createdAt = capacityNow
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
          active_accounts: activeAccountCount(),
          account_capacity: accountCapacity,
          available_account_seats: availableAccountSeats(),
          totals: {
            accounts: adminAccounts.length,
            active_sessions: authenticated ? 1 : 0,
            courses: adminCourses.length,
            documents: allAdminDocuments().length,
            notes:
              Array.from(adminNotes.values()).reduce((total, items) => total + items.length, 0) +
              (generatedNoteRecord ? 1 : 0),
          },
          runtime: {
            app_mode: 'local',
            database: 'postgresql',
            demo_lab_enabled: true,
          },
        },
      })
    }
    if (method === 'GET' && path === '/admin/courses') {
      if (!authenticated || accountRole !== 'admin') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'FORBIDDEN', '权限不足'),
        })
      }
      return route.fulfill({ json: { items: adminCourses } })
    }
    const adminCourseNotesMatch = path.match(/^\/admin\/courses\/([^/]+)\/notes$/)
    if (method === 'GET' && adminCourseNotesMatch !== null) {
      if (!authenticated || accountRole !== 'admin') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'FORBIDDEN', '权限不足'),
        })
      }
      const requestedCourseId = decodeURIComponent(adminCourseNotesMatch[1]!)
      if (!adminCourses.some((course) => course.id === requestedCourseId)) {
        return route.fulfill({
          status: 404,
          json: problem(404, 'RESOURCE_NOT_FOUND', '课程不存在'),
        })
      }
      return route.fulfill({ json: { items: adminNotes.get(requestedCourseId) ?? [] } })
    }
    const adminCourseGraphMatch = path.match(/^\/admin\/courses\/([^/]+)\/knowledge-graph$/)
    if (method === 'GET' && adminCourseGraphMatch !== null) {
      if (!authenticated || accountRole !== 'admin') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'FORBIDDEN', '权限不足'),
        })
      }
      const requestedCourseId = decodeURIComponent(adminCourseGraphMatch[1]!)
      const graph = adminGraphs.get(requestedCourseId)
      if (graph === undefined) {
        return route.fulfill({
          status: 404,
          json: problem(404, 'RESOURCE_NOT_FOUND', '课程不存在'),
        })
      }
      return route.fulfill({ json: graph })
    }
    if (method === 'GET' && path === '/admin/documents') {
      if (!authenticated || accountRole !== 'admin') {
        return route.fulfill({
          status: 403,
          json: problem(403, 'FORBIDDEN', '权限不足'),
        })
      }
      const reviewStatus = url.searchParams.get('review_status')
      const items = allAdminDocuments()
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
      const current = allAdminDocuments().find((item) => item.id === documentId)
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
      const current = allAdminDocuments().find((item) => item.id === documentId)
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
      secondaryAdminDocuments = secondaryAdminDocuments.map((item) =>
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
        json: adminDocument(allAdminDocuments().find((item) => item.id === documentId)!),
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
          vision: {
            status: providerStatus,
            label: providerAvailable ? '多模态复核可用' : '未配置',
          },
          native_parser: { status: 'available', label: '原生解析可用' },
          ocr_parser: {
            status: 'worker_required',
            label: '需要本地 OCR Worker',
          },
          mineru_parser: {
            status: 'worker_required',
            label: '需要自建 MinerU 服务',
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
    if (method === 'GET' && path === `/courses/${courseId}/learning-units`) {
      return route.fulfill({ json: learningUnits })
    }
    if (
      method === 'GET' &&
      path === `/courses/${courseId}/learning-units/unit-process/evidence`
    ) {
      return route.fulfill({
        json: [
          learningUnitEvidence({
            is_primary: learningEvidenceSupplement === null,
          }),
          ...(learningEvidenceSupplement === null ? [] : [learningEvidenceSupplement]),
        ],
      })
    }
    if (
      method === 'POST' &&
      path === `/courses/${courseId}/learning-units/unit-process/evidence-supplements`
    ) {
      const payload = request.postDataJSON() as { role: string; text: string }
      learningEvidenceSupplement = learningUnitEvidence({
        id: 'unit-process-supplement',
        source_id: 'unit-process-source',
        supplement_id: 'unit-process-supplement',
        origin: 'user_supplied' as const,
        role: payload.role,
        content_sha256: 'c'.repeat(64),
        text: payload.text,
        is_primary: true,
        practice_status: 'ready' as const,
        confidence_note: '已采用用户补充的完整原型。',
        created_at: '2026-08-02T08:01:00Z',
      })
      return route.fulfill({ status: 201, json: learningEvidenceSupplement })
    }
    if (method === 'GET' && path === `/courses/${courseId}/learning-summary`) {
      return route.fulfill({ json: learningSummary })
    }
    if (method === 'GET' && path === `/courses/${courseId}/review-queue`) {
      return route.fulfill({ json: reviewQueue })
    }
    if (method === 'POST' && path === `/courses/${courseId}/practice-batches`) {
      if (!providerAvailable) {
        return route.fulfill({
          status: 503,
          json: problem(503, 'PRACTICE_PROVIDER_UNAVAILABLE', 'Provider 未配置，不能生成新题目'),
        })
      }
      const commandKey = request.headers()['idempotency-key'] ?? null
      if (commandKey !== null && commandKey === learningBatchCommandKey) {
        return route.fulfill({ status: 202, json: practiceBatchSnapshot('queued') })
      }
      learningBatchCommandKey = commandKey
      learningBatchPayload = request.postDataJSON() as typeof learningBatchPayload
      learningBatchPolls = 0
      learningBatchId = 'practice-batch-e2e'
      const requestedCount = learningBatchPayload?.question_count ?? learningQuestions.length
      learningBatchQuestionIds = learningQuestions.slice(0, Math.min(requestedCount, learningQuestions.length)).map((question) => question.id)
      return route.fulfill({ status: 202, json: practiceBatchSnapshot('queued') })
    }
    if (method === 'GET' && path === `/practice-batches/${learningBatchId}`) {
      learningBatchPolls += 1
      if (learningBatchPolls < practiceBatchPollsBeforeSuccess) {
        return route.fulfill({ json: practiceBatchSnapshot('running') })
      }
      if (options.practiceBatchPartialSuccess) {
        learningBatchQuestionIds = learningBatchQuestionIds.slice(0, 1)
        return route.fulfill({ json: practiceBatchSnapshot('partial_success') })
      }
      return route.fulfill({ json: practiceBatchSnapshot('succeeded') })
    }
    if (method === 'POST' && path === `/courses/${courseId}/practice-sessions`) {
      const payload = request.postDataJSON() as { question_ids: string[] }
      learningBatchQuestionIds = payload.question_ids.filter((id) => learningQuestions.some((question) => question.id === id))
      practiceAttempts.clear()
      practiceSession = practiceSessionSnapshot()
      return route.fulfill({ status: 201, json: practiceSession })
    }
    if (method === 'GET' && path === '/practice-sessions/practice-session-e2e') {
      practiceSession = practiceSessionSnapshot()
      return route.fulfill({ json: practiceSession })
    }
    if (
      method === 'POST' &&
      /^\/practice-sessions\/practice-session-e2e\/questions\/[^/]+\/tutor$/.test(path)
    ) {
      const questionId = path.split('/')[4]
      const question = learningQuestions.find((item) => item.id === questionId)
      if (!question) {
        return route.fulfill({
          status: 404,
          json: problem(404, 'RESOURCE_NOT_FOUND', '练习题不存在'),
        })
      }
      const answered = Array.from(practiceAttempts.values()).some(
        (attempt) => attempt.question_id === question.id,
      )
      return route.fulfill({
        json: {
          message_id: `tutor-message-${question.id}`,
          mode: answered ? 'review' : 'hint',
          answer_markdown: answered
            ? '进程负责资源分配，线程负责执行调度。'
            : '先比较题干强调的是资源归属还是执行调度。',
          evidence_refs: question.evidence_refs,
        },
      })
    }
    if (method === 'POST' && path === '/practice-sessions/practice-session-e2e/attempts') {
      const commandKey = request.headers()['idempotency-key']
      if (commandKey && practiceAttempts.has(commandKey)) {
        return route.fulfill({ status: 201, json: practiceAttempts.get(commandKey) })
      }
      const payload = request.postDataJSON() as {
        question_id: string
        answer: string
        viewed_hint?: boolean
      }
      const question = learningQuestions.find((item) => item.id === payload.question_id)
      if (!question || !learningBatchQuestionIds.includes(payload.question_id)) {
        return route.fulfill({ status: 409, json: problem(409, 'PRACTICE_QUESTION_INVALID', '题目不属于当前练习') })
      }
      if (options.learningQuestionStale && question.id === 'question-scheduling') {
        return route.fulfill({ status: 409, json: problem(409, 'PRACTICE_SOURCE_STALE', '题目来源已失效') })
      }
      const correct = question.correctAnswer === payload.answer
      const result = {
        id: `attempt-${payload.question_id}`,
        question_id: payload.question_id,
        outcome: correct ? 'correct' : 'incorrect',
        score: correct ? 1 : 0,
        explanation: correct ? '答案可以直接由当前课程资料中的定义得到。' : '请回到来源定义，重新区分进程与线程的职责。',
        evidence_refs: question.evidence_refs,
        submitted_answer: payload.answer,
        viewed_hint: payload.viewed_hint ?? false,
        mastery: {
          learning_unit_id: question.learning_unit_id,
          previous_level: 'new',
          level: correct ? 'learning' : 'new',
          reason: correct ? '首次正确，掌握度上升一级。' : '本次回答不正确，安排再次复习。',
          next_review_at: '2026-08-03T09:00:00Z',
        },
      }
      if (commandKey) practiceAttempts.set(commandKey, result)
      const correctQuestions = Array.from(practiceAttempts.values()).filter((attempt) => attempt.outcome === 'correct').length
      learningSummary = {
        ...learningSummary,
        accuracy: practiceAttempts.size === 0 ? 0 : correctQuestions / practiceAttempts.size,
        correct_questions: correctQuestions,
        total_questions: practiceAttempts.size,
        next_action: '继续复习进程调度，巩固刚才答错或待复习的单元。',
      }
      return route.fulfill({ status: 201, json: result })
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
        concept_context?: {
          label: string
          anchors: Array<{
            document_id: string
            revision_id: string
            chunk_id: string
          }>
        }
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
    const noteSourceMatch = path.match(
      /^\/notes\/([^/]+)\/sources\/note-source-e2e\/preview$/,
    )
    if (method === 'GET' && noteSourceMatch) {
      const noteId = noteSourceMatch[1] ?? 'note-e2e'
      return route.fulfill({
        json: {
          source_id: 'note-source-e2e',
          document_id: 'document-markdown-ready',
          revision_id: 'revision-markdown-active',
          chunk_id: 'chunk-markdown-e2e',
          document_name: '调度算法.md',
          locator: { kind: 'section', ordinal: 2 },
          section_path: ['调度算法', '进程调度'],
          quote: '进程是资源分配的基本单位。',
          bounding_boxes: [],
          provenance: ['markdown-native@1'],
          media_type: 'text/markdown',
          read_url: `/api/v1/notes/${noteId}/sources/note-source-e2e/preview/content`,
          read_url_expires_at: '2099-01-01T00:00:00Z',
        },
      })
    }
    if (
      method === 'GET' &&
      /^\/notes\/[^/]+\/sources\/note-source-e2e\/preview\/content$/.test(path)
    ) {
      return route.fulfill({
        body: [
          '# 调度基础',
          '',
          '前一章节内容。',
          '',
          '## 进程调度',
          '',
          '就绪队列决定调度候选。',
          '',
          '![远程示意图](https://example.invalid/scheduling.png)',
        ].join('\n'),
        contentType: 'text/markdown',
      })
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
    if (method === 'POST' && path === `/courses/${courseId}/notes/import`) {
      const payload = request.postDataJSON() as {
        title: string
        section_path: string[]
        body_markdown: string
      }
      importedNoteRecord = {
        ...note(notesVersion),
        id: 'imported-note-e2e',
        section_path: payload.section_path,
        title: payload.title,
        body_markdown: payload.body_markdown,
        generated_by_model: false,
        origin_batch_id: null,
        sources: [],
        knowledge_points: [],
        updated_at: '2026-07-19T05:30:00Z',
      }
      return route.fulfill({ status: 201, json: importedNoteRecord })
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
          ...(importedNoteRecord ? [importedNoteRecord] : []),
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
    if (
      method === 'GET' &&
      path ===
        `/courses/${courseId}/knowledge-graph/sources/revision-markdown-active/chunk-process/preview`
    ) {
      return route.fulfill({
        json: {
          source_id: 'chunk-process',
          document_id: 'document-markdown-ready',
          revision_id: 'revision-markdown-active',
          chunk_id: 'chunk-process',
          document_name: '调度算法.md',
          locator: { kind: 'section', ordinal: 2 },
          section_path: ['调度算法', '进程调度'],
          quote: '进程是资源分配的基本单位，线程是调度的基本单位。',
          bounding_boxes: [],
          provenance: ['markdown-native@1'],
          media_type: 'text/markdown',
          read_url:
            `/api/v1/courses/${courseId}/knowledge-graph/sources/` +
            'revision-markdown-active/chunk-process/preview/content',
          read_url_expires_at: '2099-01-01T00:00:00Z',
        },
      })
    }
    if (
      method === 'GET' &&
      path ===
        `/courses/${courseId}/knowledge-graph/sources/` +
          'revision-markdown-active/chunk-process/preview/content'
    ) {
      return route.fulfill({
        body: '# 调度基础\n\n前一章节内容。\n\n## 进程调度\n\n就绪队列决定调度候选。',
        contentType: 'text/markdown',
      })
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
              id: 'document:document-markdown-ready',
              kind: 'document',
              label: '调度算法.md',
              document_id: 'document-markdown-ready',
              revision_id: 'revision-markdown-active',
              page_count: 16,
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
                  document_id: 'document-markdown-ready',
                  document_name: '调度算法.md',
                  revision_id: 'revision-markdown-active',
                  chunk_id: 'chunk-process',
                  locator_kind: 'section',
                  page_ordinal: 2,
                  section_path: ['调度算法', '进程调度'],
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
              id: 'edge:contains:document-markdown-ready',
              source: `course:${courseId}`,
              target: 'document:document-markdown-ready',
              kind: 'contains',
              weight: 1,
            },
            {
              id: 'edge:mentions:process',
              source: 'document:document-markdown-ready',
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
