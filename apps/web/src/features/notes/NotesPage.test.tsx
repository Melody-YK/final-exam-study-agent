import { act, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { StrictMode } from 'react'

import { ApiError, studyApi } from '../../api/client'
import type { NoteBatchSnapshot, NoteRecord, RuntimeCapabilities } from '../../api/types'
import { documentRecord, noteRecord, problem, sourcePreview } from '../../test/fixtures'
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
  it('imports a Markdown file as a user-authored note without source links', async () => {
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([])
    const imported = noteRecord({
      id: 'imported-note',
      title: '外部复习提纲',
      body_markdown: '# 外部复习提纲\n\n用户整理的正文。',
      generated_by_model: false,
      sources: [],
      knowledge_points: [],
    })
    const importNote = vi.spyOn(studyApi, 'importNote').mockResolvedValue(imported)
    const { user } = renderInWorkspace(<NotesPage />)

    await user.click(await screen.findByRole('button', { name: '导入笔记' }))
    const file = new File(['# 外部复习提纲\n\n用户整理的正文。'], 'outline.md', {
      type: 'text/markdown',
    })
    Object.defineProperty(file, 'text', {
      value: vi.fn().mockResolvedValue('# 外部复习提纲\n\n用户整理的正文。'),
    })
    await user.upload(screen.getByLabelText('Markdown 文件'), file)
    await waitFor(() => expect(screen.getByLabelText('标题')).toHaveValue('外部复习提纲'))
    await user.type(screen.getByLabelText('章节路径（可选）'), '导入资料 / 第一章')
    await user.click(screen.getByRole('button', { name: '导入' }))

    await waitFor(() =>
      expect(importNote).toHaveBeenCalledWith('course-1', {
        title: '外部复习提纲',
        section_path: ['导入资料', '第一章'],
        body_markdown: '# 外部复习提纲\n\n用户整理的正文。',
      }),
    )
    expect(await screen.findByLabelText('笔记阅读视图')).toHaveTextContent('用户整理的正文。')
    expect(screen.queryByLabelText('知识点来源')).not.toBeInTheDocument()
  })

  it('exports the saved Markdown body without the legacy source section', async () => {
    const note = noteRecord({
      body_markdown: '# 进程基础\n\n正文。\n\n## 来源对应\n\n- source-1',
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([note])
    const createObjectURL = vi.fn().mockReturnValue('blob:note')
    const revokeObjectURL = vi.fn()
    class BlobMockClass {
      constructor(readonly parts: unknown[]) {}
    }
    const BlobMock = vi.fn(BlobMockClass)
    vi.stubGlobal('Blob', BlobMock)
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: createObjectURL,
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: revokeObjectURL,
    })
    const anchorClick = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined)
    const { user } = renderInWorkspace(<NotesPage />)

    await user.click(await screen.findByRole('button', { name: '导出 Markdown' }))

    expect(BlobMock).toHaveBeenCalledWith(['# 进程基础\n\n正文。\n'], {
      type: 'text/markdown;charset=utf-8',
    })
    expect(anchorClick).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:note')
  })

  it('keeps legacy notes readable when knowledge-point sources are absent', async () => {
    const legacyNote = { ...noteRecord(), knowledge_points: undefined } as unknown as NoteRecord
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([legacyNote])

    renderInWorkspace(<NotesPage />)

    expect(await screen.findByLabelText('笔记阅读视图')).toHaveTextContent('原始正文')
    expect(screen.queryByLabelText('知识点来源')).not.toBeInTheDocument()
  })

  it('shows a source jump next to each knowledge point', async () => {
    const note = noteRecord({
      body_markdown: '# 进程\n\n进程拥有独立的地址空间。',
      knowledge_points: [
        {
          id: 'knowledge-point-1',
          text: '进程拥有独立的地址空间。',
          source_ids: ['note-source-1'],
        },
      ],
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([note])
    vi.spyOn(studyApi, 'getNoteSourcePreview').mockResolvedValue(sourcePreview())
    const { user } = renderInWorkspace(
      <StrictMode>
        <NotesPage />
      </StrictMode>,
    )

    const article = await screen.findByLabelText('笔记阅读视图')
    const point = within(article).getByText('进程拥有独立的地址空间。')
    expect(within(point).getByRole('button', { name: /chapter-1\.pdf/ })).toBeInTheDocument()
    expect(screen.queryByLabelText('笔记来源')).not.toBeInTheDocument()
    await user.click(within(point).getByRole('button', { name: /chapter-1\.pdf/ }))
    expect(studyApi.getNoteSourcePreview).toHaveBeenCalledWith('note-1', 'note-source-1')
  })

  it('separates note switching from the current note heading outline', async () => {
    const note = noteRecord({
      body_markdown: '# 进程\n\n概念总览。\n\n## 调度\n\n调度决定下一个运行任务。\n\n### 时间片\n\n轮转分配 CPU 时间。',
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([note])
    const scrollIntoView = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    })
    const { user } = renderInWorkspace(<NotesPage />)

    const switcher = await screen.findByLabelText('切换笔记')
    expect(within(switcher).getByRole('button', { name: /进程基础/ })).toBeInTheDocument()
    const outline = screen.getByRole('navigation', { name: '正文目录' })
    expect(within(outline).getByRole('link', { name: '进程' })).toHaveAttribute(
      'href',
      '#note-heading-1-进程',
    )
    expect(within(outline).getByRole('link', { name: '调度' })).toHaveAttribute(
      'href',
      '#note-heading-5-调度',
    )
    expect(screen.getByRole('heading', { name: '调度' })).toHaveAttribute(
      'id',
      'note-heading-5-调度',
    )

    await user.click(within(outline).getByRole('link', { name: '调度' }))
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' })
  })

  it('groups notes by section path and filters the switcher by search', async () => {
    const first = noteRecord({
      id: 'note-process',
      section_path: ['第一章', '进程管理'],
      title: '进程基础',
    })
    const second = noteRecord({
      id: 'note-index',
      section_path: ['第二章', '数据库'],
      title: '索引笔记',
      body_markdown: '# 索引\n\nB+ 树可以加速查询。',
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([first, second])
    const { user } = renderInWorkspace(<NotesPage />)

    const switcher = await screen.findByLabelText('切换笔记')
    expect(within(switcher).getByText('第一章')).toBeInTheDocument()
    expect(within(switcher).getByText('进程管理')).toBeInTheDocument()
    expect(within(switcher).getByText('第二章')).toBeInTheDocument()
    expect(within(switcher).getByRole('button', { name: '索引笔记' })).toBeInTheDocument()

    const search = within(switcher).getByRole('searchbox', { name: '搜索笔记' })
    await user.type(search, '索引')
    expect(within(switcher).getByRole('button', { name: '索引笔记' })).toBeInTheDocument()
    expect(within(switcher).queryByRole('button', { name: '进程基础' })).not.toBeInTheDocument()

    await user.click(within(switcher).getByRole('button', { name: '清除笔记搜索' }))
    expect(within(switcher).getByRole('button', { name: '进程基础' })).toBeInTheDocument()
  })

  it('moves legacy source mappings out of the visible note body', async () => {
    const note = noteRecord({
      body_markdown:
        '# 进程\n\n- **进程**拥有独立的地址空间。\n\n### 来源对应\n- 进程拥有独立的地址空间。 (来源: chapter-1.pdf · 第 3 页)',
      knowledge_points: [
        {
          id: 'knowledge-point-legacy',
          text: '进程拥有独立的地址空间。',
          source_ids: ['note-source-1'],
        },
      ],
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([note])

    renderInWorkspace(<NotesPage />)

    const article = await screen.findByLabelText('笔记阅读视图')
    expect(article).not.toHaveTextContent('来源对应')
    expect(within(article).getByRole('button', { name: /chapter-1\.pdf/ })).toBeInTheDocument()
  })

  it('renders streamed note preview deltas while a batch is running', async () => {
    localStorage.setItem('study-agent.note-batch:course-1', 'note-batch-stream')
    let pushEvent: ((event: { event_type: string; data: { delta?: string } }) => void) | null = null
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([])
    vi.spyOn(studyApi, 'getNoteBatch').mockResolvedValue(noteBatchSnapshot())
    vi.spyOn(studyApi, 'subscribe').mockImplementation((_path, onEvent) => {
      pushEvent = onEvent as typeof pushEvent
      return vi.fn()
    })
    renderInWorkspace(<NotesPage />)

    await screen.findByLabelText('笔记生成进度')
    await act(async () => {
      pushEvent?.({ event_type: 'note.preview.delta', data: { delta: '# 实时预览' } })
    })
    expect(screen.getByLabelText('笔记实时预览')).toHaveTextContent('实时预览')
  })

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
    expect(screen.getByRole('button', { name: '重新生成' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(studyApi.updateNote).toHaveBeenCalledWith('note-1', '# 更新正文', 1),
    )
    expect(await screen.findByText(/版本 2/)).toBeInTheDocument()
    expect(screen.getByLabelText('笔记阅读视图')).toHaveTextContent('更新正文')
    expect(screen.queryByLabelText('笔记来源')).not.toBeInTheDocument()
  })

  it('opens an active Markdown note source at its validated section', async () => {
    const note = noteRecord({
      body_markdown: '# 进程\n\n进程拥有独立的地址空间。',
      knowledge_points: [
        {
          id: 'knowledge-point-1',
          text: '进程拥有独立的地址空间。',
          source_ids: ['note-source-1'],
        },
      ],
      sources: [
        {
          ...noteRecord().sources[0]!,
          document_name: 'outline.md',
          locator: { kind: 'section', ordinal: 2 },
        },
      ],
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([note])
    vi.spyOn(studyApi, 'getNoteSourcePreview').mockResolvedValue(
      sourcePreview({
        document_name: 'outline.md',
        locator: { kind: 'section', ordinal: 2 },
        section_path: ['进程管理', '调度'],
        media_type: 'text/markdown',
        read_url: '/api/v1/notes/note-1/sources/note-source-1/preview/content',
      }),
    )
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('# 基础\n\n前一章。\n\n## 调度\n\n**就绪队列**', {
          status: 200,
          headers: { 'Content-Type': 'text/markdown' },
        }),
      ),
    )
    const { user } = renderInWorkspace(<NotesPage />)

    await user.click(await screen.findByRole('button', { name: /查看原文 · outline\.md/ }))

    expect(studyApi.getNoteSourcePreview).toHaveBeenCalledWith('note-1', 'note-source-1')
    expect(await screen.findByRole('heading', { name: '调度' })).toBeInTheDocument()
    expect(screen.getByText('就绪队列').tagName).toBe('STRONG')
    expect(screen.queryByText('前一章。')).not.toBeInTheDocument()
    expect(screen.getAllByText('进程管理 / 调度').length).toBeGreaterThan(0)
  })

  it('shows a preview failure beside the note source that was opened', async () => {
    const firstSource = noteRecord().sources[0]!
    const note = noteRecord({
      body_markdown: '# 进程\n\n进程拥有独立的地址空间。',
      knowledge_points: [
        {
          id: 'knowledge-point-1',
          text: '进程拥有独立的地址空间。',
          source_ids: ['note-source-1', 'note-source-2'],
        },
      ],
      sources: [
        firstSource,
        {
          ...firstSource,
          id: 'note-source-2',
          document_name: 'legacy-slides.pptx',
        },
      ],
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([note])
    vi.spyOn(studyApi, 'getNoteSourcePreview').mockRejectedValue(
      new ApiError(
        problem({
          title: 'PPTX 预览页不可用',
          detail: '请先转换为 PDF 后重新上传。',
        }),
      ),
    )
    const { user } = renderInWorkspace(<NotesPage />)

    const openButtons = await screen.findAllByRole('button', { name: /查看原文/ })
    await user.click(openButtons[1]!)

    const alert = await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: /legacy-slides\.pptx/ })).toBeVisible()
    expect(alert).toHaveTextContent('请先转换为 PDF 后重新上传。')
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
    const readyMarkdown = documentRecord({
      id: 'markdown-ready',
      filename: 'memory.md',
      media_type: 'text/markdown',
      active_revision_id: 'revision-markdown',
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValueOnce([]).mockResolvedValue([generated])
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([
      readyPdf,
      readyMarkdown,
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
    expect(screen.getByText('memory.md')).toBeInTheDocument()
    expect(screen.getByText('已就绪的 PDF/Markdown')).toBeInTheDocument()
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
        document_ids: ['document-1', 'markdown-ready'],
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

  it('regenerates a workflow note through a durable batch and selects its new output', async () => {
    const capabilities: RuntimeCapabilities = {
      ...availableCapabilities,
      provider: { status: 'not_configured', label: '未配置回答模型' },
    }
    const workflowNote = noteRecord({
      id: 'workflow-note',
      origin_batch_id: 'origin-batch',
      generated_by_model: false,
      title: '原批次笔记',
    })
    const regenerated = noteRecord({
      id: workflowNote.id,
      origin_batch_id: workflowNote.origin_batch_id,
      generated_by_model: false,
      title: workflowNote.title,
      body_markdown: `# ${workflowNote.title}\n\n新正文`,
      version: 2,
      generation: 2,
    })
    vi.spyOn(studyApi, 'listNotes')
      .mockResolvedValueOnce([workflowNote])
      .mockResolvedValue([regenerated])
    const legacyRegenerate = vi.spyOn(studyApi, 'regenerateNote')
    const createRegeneration = vi
      .spyOn(studyApi, 'createNoteRegenerationBatch')
      .mockResolvedValue(
        noteBatchSnapshot({
          id: 'regeneration-batch',
          command_kind: 'regeneration',
          target_note_id: workflowNote.id,
          target_note_version: workflowNote.version,
          target_note_version_sha256: 'a'.repeat(64),
        }),
      )
    let finishBatch!: (snapshot: NoteBatchSnapshot) => void
    vi.spyOn(studyApi, 'getNoteBatch').mockImplementation(
      () => new Promise((resolve) => {
        finishBatch = resolve
      }),
    )
    const { user } = renderInWorkspace(<NotesPage />, { workspace: { capabilities } })

    await screen.findByLabelText('笔记阅读视图')
    expect(screen.getByRole('button', { name: '重新生成' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: '重新生成' }))

    await waitFor(() =>
      expect(createRegeneration).toHaveBeenCalledWith(
        workflowNote.id,
        workflowNote.version,
        expect.stringMatching(/^note-batch-regenerate-/),
      ),
    )
    expect(legacyRegenerate).not.toHaveBeenCalled()
    expect(await screen.findByLabelText('笔记生成进度')).toHaveTextContent('running')
    expect(screen.getByRole('button', { name: '重新生成' })).toBeDisabled()
    const succeeded = noteBatchSnapshot({
      id: 'regeneration-batch',
      command_kind: 'regeneration',
      target_note_id: workflowNote.id,
      target_note_version: workflowNote.version,
      target_note_version_sha256: 'a'.repeat(64),
      status: 'succeeded',
    })
    finishBatch({
      ...succeeded,
      items: succeeded.items.map((item) => ({ ...item, note_id: workflowNote.id })),
    })

    await waitFor(() => expect(studyApi.listNotes).toHaveBeenCalledTimes(2))
    expect(await screen.findByRole('button', { name: /原批次笔记/ })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(screen.getAllByRole('button', { name: /原批次笔记/ })).toHaveLength(1)
    expect(screen.getByText(/版本 2 · 生成 2 · 本地摘录演示/)).toBeInTheDocument()
    expect(screen.getByLabelText('笔记阅读视图')).toHaveTextContent('新正文')
  })

  it('keeps the regeneration command key until a workflow batch is accepted', async () => {
    const workflowNote = noteRecord({ origin_batch_id: 'origin-batch' })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([workflowNote])
    const createRegeneration = vi
      .spyOn(studyApi, 'createNoteRegenerationBatch')
      .mockRejectedValueOnce(new Error('连接中断'))
      .mockResolvedValue(
        noteBatchSnapshot({
          id: 'regeneration-batch',
          command_kind: 'regeneration',
          target_note_id: workflowNote.id,
          target_note_version: workflowNote.version,
          target_note_version_sha256: 'a'.repeat(64),
        }),
      )
    vi.spyOn(studyApi, 'getNoteBatch').mockImplementation(
      () => new Promise<NoteBatchSnapshot>(() => undefined),
    )
    const { user } = renderInWorkspace(<NotesPage />)

    await screen.findByLabelText('笔记阅读视图')
    await user.click(screen.getByRole('button', { name: '重新生成' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('连接中断')
    await user.click(screen.getByRole('button', { name: '重新生成' }))

    await waitFor(() => expect(createRegeneration).toHaveBeenCalledTimes(2))
    expect(createRegeneration.mock.calls[0]?.[2]).toBe(createRegeneration.mock.calls[1]?.[2])
  })

  it('scopes regeneration command keys to the note and version target', async () => {
    const first = noteRecord({
      id: 'workflow-note-1',
      origin_batch_id: 'origin-batch-1',
      title: '第一篇批次笔记',
    })
    const second = noteRecord({
      id: 'workflow-note-2',
      origin_batch_id: 'origin-batch-2',
      title: '第二篇批次笔记',
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([first, second])
    const createRegeneration = vi
      .spyOn(studyApi, 'createNoteRegenerationBatch')
      .mockRejectedValue(new Error('连接中断'))
    const { user } = renderInWorkspace(<NotesPage />)

    await screen.findByLabelText('笔记阅读视图')
    await user.click(screen.getByRole('button', { name: '重新生成' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('连接中断')
    await user.click(screen.getByRole('button', { name: /第二篇批次笔记/ }))
    await user.click(screen.getByRole('button', { name: '重新生成' }))
    await waitFor(() => expect(createRegeneration).toHaveBeenCalledTimes(2))

    expect(createRegeneration.mock.calls[0]?.[2]).not.toBe(
      createRegeneration.mock.calls[1]?.[2],
    )
  })

  it('keeps legacy note regeneration on the immediate provider endpoint', async () => {
    const initial = noteRecord({ origin_batch_id: null })
    const updated = noteRecord({
      origin_batch_id: null,
      body_markdown: '# Provider 重新生成\n\n更新正文',
      version: 2,
      generation: 2,
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([initial])
    const legacyRegenerate = vi.spyOn(studyApi, 'regenerateNote').mockResolvedValue(updated)
    const createRegeneration = vi.spyOn(studyApi, 'createNoteRegenerationBatch')
    const { user } = renderInWorkspace(<NotesPage />)

    await screen.findByLabelText('笔记阅读视图')
    await user.click(screen.getByRole('button', { name: '重新生成' }))

    await waitFor(() => expect(legacyRegenerate).toHaveBeenCalledWith(initial.id))
    expect(createRegeneration).not.toHaveBeenCalled()
    expect(await screen.findByLabelText('笔记阅读视图')).toHaveTextContent('更新正文')
  })

  it('previews all template contracts without starting a generation batch', async () => {
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([])
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([documentRecord()])
    const createBatch = vi.spyOn(studyApi, 'createNoteBatch')
    const getBatch = vi.spyOn(studyApi, 'getNoteBatch')
    const { user } = renderInWorkspace(<NotesPage />)
    await screen.findByText('暂无笔记')

    await user.click(screen.getByRole('button', { name: '新建笔记' }))

    expect(await screen.findAllByRole('radio')).toHaveLength(3)
    const exam = screen.getByRole('radio', { name: /考前速记/ })
    const outline = screen.getByRole('radio', { name: /结构提纲/ })
    const complete = screen.getByRole('radio', { name: /完整讲义/ })
    expect(exam).toBeChecked()
    expect(outline).not.toBeChecked()
    expect(complete).not.toBeChecked()

    expect(screen.getByText('最短 · 最多 12 条')).toBeInTheDocument()
    expect(screen.getByText('定义、条件、区别和公式优先')).toBeInTheDocument()
    expect(screen.getByText('中等 · 最多 30 条')).toBeInTheDocument()
    expect(screen.getByText('按资料和来源位置快速梳理层级')).toBeInTheDocument()
    expect(screen.getByText('最长 · 最多 40 条 / 12,000 字符')).toBeInTheDocument()
    expect(screen.getByText('按来源顺序保留完整上下文')).toBeInTheDocument()

    const examSample = screen.getByLabelText('考前速记结构示例')
    const outlineSample = screen.getByLabelText('结构提纲结构示例')
    const completeSample = screen.getByLabelText('完整讲义结构示例')
    expect(examSample.children).toHaveLength(3)
    expect(examSample).toHaveTextContent('资料名称• 高频定义或公式• 关键条件与区别')
    expect(outlineSample.children).toHaveLength(3)
    expect(outlineSample).toHaveTextContent('1. 资料名称1.1 来源位置1. 关键知识点')
    expect(completeSample.children).toHaveLength(3)
    expect(completeSample).toHaveTextContent('资料名称来源位置来源正文段落')
    expect(examSample).toHaveAttribute('aria-current', 'true')
    expect(outlineSample).not.toHaveAttribute('aria-current')
    expect(completeSample).not.toHaveAttribute('aria-current')

    await user.click(outline)

    expect(outline).toBeChecked()
    expect(examSample).not.toHaveAttribute('aria-current')
    expect(outlineSample).toHaveAttribute('aria-current', 'true')
    expect(completeSample).not.toHaveAttribute('aria-current')
    expect(createBatch).not.toHaveBeenCalled()
    expect(getBatch).not.toHaveBeenCalled()
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
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([
      noteRecord({ origin_batch_id: 'origin-batch' }),
    ])
    const getBatch = vi.spyOn(studyApi, 'getNoteBatch').mockResolvedValue(
      noteBatchSnapshot({ id: 'note-batch-restored' }),
    )

    renderInWorkspace(<NotesPage />)

    await waitFor(() => expect(getBatch).toHaveBeenCalledWith('note-batch-restored'))
    expect(await screen.findByLabelText('笔记生成进度')).toHaveTextContent('running')
    expect(screen.getByRole('button', { name: '新建笔记' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重新生成' })).toBeDisabled()
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
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([
      noteRecord({ origin_batch_id: 'origin-batch' }),
    ])

    renderInWorkspace(<NotesPage />, { workspace: { capabilities } })

    await screen.findByLabelText('笔记阅读视图')
    expect(screen.getByRole('button', { name: '新建笔记' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重新生成' })).toBeDisabled()
  })
})
