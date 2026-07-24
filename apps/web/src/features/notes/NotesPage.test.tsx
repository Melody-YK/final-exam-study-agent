import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ApiError, studyApi } from '../../api/client'
import type { NoteBatchSnapshot, RuntimeCapabilities } from '../../api/types'
import { documentRecord, noteRecord, problem } from '../../test/fixtures'
import { availableCapabilities, renderInWorkspace } from '../../test/render'
import { NotesPage } from './NotesPage'

function noteBatchSnapshot(
  overrides: Partial<NoteBatchSnapshot> = {},
): NoteBatchSnapshot {
  const status = overrides.status ?? 'running'
  const succeeded = status === 'succeeded'
  return {
    schema_version: '1.0',
    id: 'note-batch-1',
    command_kind: 'create',
    retry_of_batch_id: null,
    course_id: 'course-1',
    mode: 'merged',
    style: 'exam_focus',
    title: null,
    title_prefix: null,
    section_path: ['未分类'],
    target_note_id: null,
    target_note_version: null,
    target_note_version_sha256: null,
    status,
    completed_items: succeeded ? 1 : 0,
    total_items: 1,
    inputs: [],
    coverage_units: [],
    items: [
      {
        id: 'note-item-1',
        input_ids: ['note-input-1'],
        status: succeeded ? 'succeeded' : 'running',
        phase: succeeded ? null : 'generating',
        elapsed_seconds: succeeded ? 12 : 7,
        eta: null,
        eta_unavailable_reason: succeeded ? 'terminal' : 'insufficient_history',
        attempt: 1,
        note_id: succeeded ? 'note-generated' : null,
        failure_code: null,
        retryable_in_new_batch: false,
      },
    ],
    last_event_sequence: succeeded ? 4 : 2,
    created_at: '2026-07-23T04:00:00Z',
    started_at: '2026-07-23T04:00:01Z',
    completed_at: succeeded ? '2026-07-23T04:00:12Z' : null,
    ...overrides,
  }
}

describe('NotesPage', () => {
  it('edits a note while preserving active, stale, and unavailable source states', async () => {
    const note = noteRecord({
      sources: [
        noteRecord().sources[0]!,
        {
          ...noteRecord().sources[0]!,
          id: 'source-stale',
          document_name: 'old.pdf',
          stale: true,
        },
        {
          ...noteRecord().sources[0]!,
          id: 'source-unavailable',
          document_name: 'deleted.pdf',
          available: false,
          unavailable_reason: 'SOURCE_DELETED',
        },
      ],
    })
    const updated = noteRecord({
      body_markdown: '# 更新正文',
      version: 2,
      generation: 1,
      sources: note.sources,
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([note])
    vi.spyOn(studyApi, 'updateNote').mockResolvedValue(updated)
    const { user } = renderInWorkspace(<NotesPage />)

    expect(await screen.findByLabelText('笔记阅读视图')).toHaveTextContent('原始正文')
    await user.click(screen.getByRole('button', { name: '编辑' }))
    const editor = screen.getByLabelText('笔记正文')
    await user.clear(editor)
    await user.type(editor, '# 更新正文')
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(studyApi.updateNote).toHaveBeenCalledWith('note-1', '# 更新正文', 1),
    )
    expect(await screen.findByText(/版本 2/)).toBeInTheDocument()
    expect(screen.getByLabelText('笔记阅读视图')).toHaveTextContent('更新正文')
    expect(screen.getByText('活动来源')).toBeInTheDocument()
    expect(screen.getByText('旧版本')).toBeInTheDocument()
    expect(screen.getByText('不可用 · SOURCE_DELETED')).toBeInTheDocument()
    expect(screen.getByText('deleted.pdf')).toBeInTheDocument()
  })

  it('detects a version conflict and loads the latest server note', async () => {
    const initial = noteRecord()
    const latest = noteRecord({
      body_markdown: '# 服务器正文',
      version: 2,
      generation: 1,
      updated_at: '2026-07-19T04:03:00Z',
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValueOnce([initial]).mockResolvedValue([latest])
    vi.spyOn(studyApi, 'updateNote').mockRejectedValue(
      new ApiError(
        problem({
          status: 412,
          code: 'VERSION_CONFLICT',
          title: '笔记版本冲突',
          detail: '当前版本为 2',
        }),
      ),
    )
    const { user } = renderInWorkspace(<NotesPage />)
    await screen.findByLabelText('笔记阅读视图')
    await user.click(screen.getByRole('button', { name: '编辑' }))
    const editor = screen.getByLabelText('笔记正文')

    await user.clear(editor)
    await user.type(editor, '# 本地草稿')
    await user.click(screen.getByRole('button', { name: '保存' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('笔记已在其他位置更新')
    expect(screen.getByRole('alert')).toHaveTextContent('当前草稿未覆盖服务器版本')
    await user.click(screen.getByRole('button', { name: '载入服务器版本' }))

    await waitFor(() => expect(studyApi.listNotes).toHaveBeenCalledTimes(2))
    expect(await screen.findByLabelText('笔记阅读视图')).toHaveTextContent('服务器正文')
    await user.click(screen.getByRole('button', { name: '编辑' }))
    expect(screen.getByLabelText('笔记正文')).toHaveValue('# 服务器正文')
    expect(screen.queryByText('笔记已在其他位置更新')).not.toBeInTheDocument()
  })

  it('creates a merged batch, reports progress, then selects the generated note', async () => {
    const generated = noteRecord({
      id: 'note-generated',
      section_path: ['第二章', '内存'],
      title: '虚拟内存',
      body_markdown: '# 虚拟内存\n\n> 笔记模板: 考前速记\n\n生成后的正文。',
    })
    const readyPdf = documentRecord()
    const readyPptx = documentRecord({
      id: 'slides-ready',
      filename: 'memory.pptx',
      media_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      active_revision_id: 'revision-slides',
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValueOnce([]).mockResolvedValue([generated])
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([
      readyPdf,
      readyPptx,
      documentRecord({ id: 'image-ready', filename: 'scan.png', media_type: 'image/png' }),
      documentRecord({ id: 'pdf-queued', filename: 'queued.pdf', status: 'queued' }),
      documentRecord({
        id: 'pdf-non-corpus',
        filename: 'question-bank.pdf',
        corpus_role: 'questions',
      }),
      documentRecord({
        id: 'pdf-not-indexable',
        filename: 'not-indexable.pdf',
        indexable: false,
      }),
      documentRecord({
        id: 'pdf-wrong-media',
        filename: 'extension-only.pdf',
        media_type: 'application/octet-stream',
      }),
      documentRecord({
        id: 'legacy-ppt-filename',
        filename: 'legacy-slides.PPT',
        media_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      }),
    ])
    vi.spyOn(studyApi, 'createNoteBatch').mockResolvedValue(noteBatchSnapshot())
    let finishBatch!: (snapshot: NoteBatchSnapshot) => void
    vi.spyOn(studyApi, 'getNoteBatch').mockImplementation(
      () => new Promise((resolve) => {
        finishBatch = resolve
      }),
    )
    const { user } = renderInWorkspace(<NotesPage />)
    await screen.findByText('暂无笔记')

    await user.click(screen.getByRole('button', { name: '新建笔记' }))
    expect(await screen.findByText('chapter-1.pdf')).toBeInTheDocument()
    expect(screen.getByText('memory.pptx')).toBeInTheDocument()
    expect(screen.queryByText('scan.png')).not.toBeInTheDocument()
    expect(screen.queryByText('queued.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('question-bank.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('not-indexable.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('extension-only.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('legacy-slides.PPT')).not.toBeInTheDocument()
    await user.type(screen.getByLabelText('章节路径（可选）'), '第二章 / 内存')
    await user.type(screen.getByLabelText('标题（可选）'), '虚拟内存')
    await user.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() =>
      expect(studyApi.createNoteBatch).toHaveBeenCalledWith('course-1', {
        schema_version: '1.0',
        mode: 'merged',
        document_ids: ['document-1', 'slides-ready'],
        style: 'exam_focus',
        section_path: ['第二章', '内存'],
        title: '虚拟内存',
      }, expect.stringMatching(/^note-batch-create-/)),
    )
    expect(await screen.findByLabelText('笔记生成进度')).toHaveTextContent('running')
    expect(screen.getByLabelText('笔记生成进度')).toHaveTextContent('考前速记')
    expect(screen.getByLabelText('笔记生成进度')).toHaveTextContent('generating')
    expect(screen.getByLabelText('笔记生成进度')).toHaveTextContent('7 秒')
    expect(screen.getByRole('button', { name: '新建笔记' })).toBeDisabled()
    await waitFor(() => expect(studyApi.getNoteBatch).toHaveBeenCalledWith('note-batch-1'))
    finishBatch(noteBatchSnapshot({ status: 'succeeded' }))

    await waitFor(() => expect(studyApi.listNotes).toHaveBeenCalledTimes(2))
    const generatedButton = await screen.findByRole('button', { name: /虚拟内存/ })
    expect(generatedButton).toHaveAttribute('aria-current', 'page')
    expect(screen.getByLabelText('笔记阅读视图')).toHaveTextContent('生成后的正文')
    expect(screen.getByLabelText('笔记阅读视图')).toHaveTextContent('笔记模板: 考前速记')
    expect(screen.getByLabelText('笔记生成进度')).toHaveTextContent('succeeded')
  })

  it('omits optional title and section fields from a merged batch', async () => {
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([])
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([documentRecord()])
    vi.spyOn(studyApi, 'createNoteBatch').mockResolvedValue(noteBatchSnapshot())
    vi.spyOn(studyApi, 'getNoteBatch').mockImplementation(
      () => new Promise<NoteBatchSnapshot>(() => undefined),
    )
    const { user } = renderInWorkspace(<NotesPage />)
    await screen.findByText('暂无笔记')

    await user.click(screen.getByRole('button', { name: '新建笔记' }))
    await screen.findByText('chapter-1.pdf')
    await user.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() =>
      expect(studyApi.createNoteBatch).toHaveBeenCalledWith('course-1', {
        schema_version: '1.0',
        mode: 'merged',
        document_ids: ['document-1'],
        style: 'exam_focus',
      }, expect.stringMatching(/^note-batch-create-/)),
    )
  })

  it.each([
    ['结构提纲', 'outline'],
    ['完整讲义', 'complete'],
  ] as const)('submits the selected %s template', async (label, style) => {
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([])
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([documentRecord()])
    const createBatch = vi.spyOn(studyApi, 'createNoteBatch').mockResolvedValue(
      noteBatchSnapshot({ style }),
    )
    vi.spyOn(studyApi, 'getNoteBatch').mockImplementation(
      () => new Promise<NoteBatchSnapshot>(() => undefined),
    )
    const { user } = renderInWorkspace(<NotesPage />)
    await screen.findByText('暂无笔记')

    await user.click(screen.getByRole('button', { name: '新建笔记' }))
    await user.click(await screen.findByRole('radio', { name: new RegExp(label) }))
    await user.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() =>
      expect(createBatch).toHaveBeenCalledWith(
        'course-1',
        expect.objectContaining({ style }),
        expect.stringMatching(/^note-batch-create-/),
      ),
    )
  })

  it('reuses a command key after errors and resets it after an explicit close', async () => {
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([])
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([documentRecord()])
    const createBatch = vi
      .spyOn(studyApi, 'createNoteBatch')
      .mockRejectedValueOnce(new Error('连接中断'))
      .mockRejectedValueOnce(new Error('响应丢失'))
      .mockResolvedValue(noteBatchSnapshot())
    vi.spyOn(studyApi, 'getNoteBatch').mockImplementation(
      () => new Promise<NoteBatchSnapshot>(() => undefined),
    )
    const { user } = renderInWorkspace(<NotesPage />)
    await screen.findByText('暂无笔记')

    await user.click(screen.getByRole('button', { name: '新建笔记' }))
    await screen.findByText('chapter-1.pdf')
    await user.click(screen.getByRole('button', { name: '创建' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('连接中断')

    await user.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() => expect(createBatch).toHaveBeenCalledTimes(2))
    expect(createBatch.mock.calls[0]?.[2]).toBe(createBatch.mock.calls[1]?.[2])
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('响应丢失'))

    await user.click(screen.getByRole('button', { name: '取消' }))
    await user.click(screen.getByRole('button', { name: '新建笔记' }))
    await user.click(await screen.findByRole('button', { name: '创建' }))
    await waitFor(() => expect(createBatch).toHaveBeenCalledTimes(3))

    expect(createBatch.mock.calls[2]?.[2]).not.toBe(createBatch.mock.calls[0]?.[2])
    expect(localStorage.getItem('study-agent.note-batch:course-1')).toBe('note-batch-1')
    expect(screen.getByRole('button', { name: '新建笔记' })).toBeDisabled()
  })

  it('restores and polls the active batch stored for the current course', async () => {
    localStorage.setItem('study-agent.note-batch:course-1', 'note-batch-restored')
    localStorage.setItem('study-agent.note-batch:another-course', 'note-batch-other')
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([])
    const getBatch = vi.spyOn(studyApi, 'getNoteBatch').mockResolvedValue(
      noteBatchSnapshot({ id: 'note-batch-restored' }),
    )

    renderInWorkspace(<NotesPage />)

    await waitFor(() => expect(getBatch).toHaveBeenCalledWith('note-batch-restored'))
    expect(await screen.findByLabelText('笔记生成进度')).toHaveTextContent('running')
    expect(screen.getByRole('button', { name: '新建笔记' })).toBeDisabled()
  })

  it('clears a stale restored batch after a not-found response', async () => {
    localStorage.setItem('study-agent.note-batch:course-1', 'note-batch-missing')
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([])
    const getBatch = vi.spyOn(studyApi, 'getNoteBatch').mockRejectedValue(
      new ApiError(
        problem({
          status: 404,
          code: 'RESOURCE_NOT_FOUND',
          title: '批次不存在',
        }),
      ),
    )

    renderInWorkspace(<NotesPage />)

    await waitFor(() => expect(getBatch).toHaveBeenCalledWith('note-batch-missing'))
    await waitFor(() =>
      expect(localStorage.getItem('study-agent.note-batch:course-1')).toBeNull(),
    )
    expect(screen.getByRole('button', { name: '新建笔记' })).toBeEnabled()
    expect(screen.queryByText('无法更新笔记生成进度')).not.toBeInTheDocument()
  })

  it('preserves a restored batch and offers retry after a transient polling error', async () => {
    localStorage.setItem('study-agent.note-batch:course-1', 'note-batch-temporary-error')
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([])
    const getBatch = vi.spyOn(studyApi, 'getNoteBatch').mockRejectedValue(
      new ApiError(
        problem({
          status: 503,
          code: 'SERVICE_UNAVAILABLE',
          title: '服务暂时不可用',
          retryable: true,
        }),
      ),
    )
    const { user } = renderInWorkspace(<NotesPage />)

    expect(await screen.findByText('无法更新笔记生成进度')).toBeInTheDocument()
    expect(localStorage.getItem('study-agent.note-batch:course-1')).toBe(
      'note-batch-temporary-error',
    )
    expect(screen.getByRole('button', { name: '新建笔记' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(getBatch).toHaveBeenCalledTimes(2))
  })

  it('disables creation without eligible documents', async () => {
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([])
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([
      documentRecord({
        active_revision_id: null,
        filename: 'not-active.pdf',
        status: 'parsed_index_blocked',
      }),
    ])
    const createBatch = vi.spyOn(studyApi, 'createNoteBatch')
    const { user } = renderInWorkspace(<NotesPage />)
    await screen.findByText('暂无笔记')

    await user.click(screen.getByRole('button', { name: '新建笔记' }))
    expect(await screen.findByText('当前没有可用于生成笔记的资料。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '创建' })).toBeDisabled()
    expect(createBatch).not.toHaveBeenCalled()
  })

  it('renders Markdown without mounting embedded HTML', async () => {
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([
      noteRecord({
        body_markdown: '# 安全标题\n\n**重点**\n\n<script data-testid="unsafe">alert(1)</script>',
      }),
    ])

    renderInWorkspace(<NotesPage />)

    expect(await screen.findByRole('heading', { name: '安全标题' })).toBeInTheDocument()
    expect(screen.getByText('重点').tagName).toBe('STRONG')
    expect(screen.queryByTestId('unsafe')).not.toBeInTheDocument()
  })

  it('keeps batch creation available without a provider while gating legacy regeneration', async () => {
    const capabilities: RuntimeCapabilities = {
      ...availableCapabilities,
      provider: { status: 'not_configured', label: '未配置回答模型' },
    }
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([noteRecord()])
    const { user } = renderInWorkspace(<NotesPage />, { workspace: { capabilities } })

    await screen.findByLabelText('笔记阅读视图')
    expect(screen.getByRole('button', { name: '新建笔记' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '重新生成' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '编辑' }))
    expect(screen.getByLabelText('笔记正文')).toBeEnabled()
  })

  it('disables batch creation when the note workflow capability is unavailable', async () => {
    const capabilities: RuntimeCapabilities = {
      ...availableCapabilities,
      note_workflow: {
        ...availableCapabilities.note_workflow,
        enabled: false,
        generation: { status: 'unavailable', label: '异步笔记生成未启用' },
      },
    }
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([])

    renderInWorkspace(<NotesPage />, { workspace: { capabilities } })

    await screen.findByText('暂无笔记')
    expect(screen.getByRole('button', { name: '新建笔记' })).toBeDisabled()
  })
})
