import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ApiError, studyApi } from '../../api/client'
import type { RuntimeCapabilities } from '../../api/types'
import { documentRecord, problem } from '../../test/fixtures'
import { availableCapabilities, renderInWorkspace } from '../../test/render'
import { LibraryPage } from './LibraryPage'

async function expectReadinessCount(label: string, count: number) {
  expect(
    await screen.findByRole('listitem', { name: `${label} ${count}` }),
  ).toBeInTheDocument()
}

describe('LibraryPage', () => {
  it('renders document states and supports retry and deletion cleanup', async () => {
    const documents = [
      documentRecord({
        id: 'ready-document',
        filename: 'ready.pdf',
        preview_revision_id: 'preview-2',
      }),
      documentRecord({
        id: 'failed-document',
        filename: 'partial.pdf',
        status: 'partial_failed',
        active_revision_id: null,
        failed_pages: [2, 5],
        error_code: 'PARSER_PAGE_FAILED',
      }),
      documentRecord({
        id: 'processing-document',
        filename: 'processing.pdf',
        status: 'processing',
        active_revision_id: null,
        parse_job_id: 'parse-job-1',
        progress: { phase: 'parsing', completed_pages: 3, total_pages: 10 },
      }),
      documentRecord({
        id: 'queued-document',
        filename: 'queued.pdf',
        status: 'queued',
        active_revision_id: null,
      }),
      documentRecord({
        id: 'excluded-document',
        filename: 'answers.pdf',
        corpus_role: 'excluded',
        indexable: false,
      }),
    ]
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue(documents)
    vi.spyOn(studyApi, 'subscribe').mockReturnValue(vi.fn())
    vi.spyOn(studyApi, 'retryDocument').mockResolvedValue(documents[1]!)
    vi.spyOn(studyApi, 'deleteDocument').mockResolvedValue({
      deletion_id: 'deletion-1',
      status: 'pending',
    })
    vi.spyOn(studyApi, 'getDeletion').mockResolvedValue({
      id: 'deletion-1',
      target_id: 'ready-document',
      target_type: 'document',
      deletion_epoch: 1,
      status: 'completed',
      attempt_count: 1,
      completed_at: '2026-07-19T04:02:00Z',
    })

    const { user } = renderInWorkspace(<LibraryPage />)

    expect(await screen.findByText('ready.pdf')).toBeInTheDocument()
    expect(screen.getByText('活动版 + 待确认预览')).toBeInTheDocument()
    expect(screen.getByText('部分失败')).toBeInTheDocument()
    expect(screen.getByText('PARSER_PAGE_FAILED')).toBeInTheDocument()
    expect(screen.getByText('解析中')).toBeInTheDocument()
    expect(screen.getByText('等待 Worker')).toBeInTheDocument()
    expect(screen.getByText('3/10 页')).toBeInTheDocument()
    expect(studyApi.subscribe).toHaveBeenCalledWith(
      '/parse-jobs/parse-job-1/events',
      expect.any(Function),
      expect.any(Function),
      expect.any(Function),
    )
    expect(screen.getByText('排除')).toHaveClass('muted')

    await user.click(screen.getByRole('button', { name: '重试失败页' }))
    expect(studyApi.retryDocument).toHaveBeenCalledWith('failed-document', [2, 5])

    await user.click(screen.getAllByRole('button', { name: '删除资料' })[0]!)
    const dialog = screen.getByRole('dialog', { name: '删除 ready.pdf' })
    expect(within(dialog).getByText(/立即无法检索或引用/)).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: '删除 ready.pdf' })).not.toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: '删除资料' })[0]!)
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '确认删除' }))

    await waitFor(() => expect(studyApi.deleteDocument).toHaveBeenCalledWith('ready-document'))
    expect(await screen.findByText('资料已不可访问，后台清理完成。')).toBeInTheDocument()
  })

  it('keeps Library visible while reporting unavailable runtime capabilities', async () => {
    const capabilities: RuntimeCapabilities = {
      ...availableCapabilities,
      provider: { status: 'not_configured', label: '未配置回答模型' },
      native_parser: { status: 'unavailable', label: '原生解析不可用' },
      ocr_parser: { status: 'worker_required', label: '需要本地 OCR Worker' },
    }
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([documentRecord()])

    renderInWorkspace(<LibraryPage />, { workspace: { capabilities } })

    expect(await screen.findByText('chapter-1.pdf')).toBeInTheDocument()
    expect(screen.getByText('未配置回答模型')).toBeInTheDocument()
    expect(screen.getByText('原生解析不可用')).toBeInTheDocument()
    expect(screen.getByText('需要本地 OCR Worker')).toBeInTheDocument()
  })

  it('derives study readiness from every required predicate with review precedence', async () => {
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([
      documentRecord({ id: 'ready' }),
      documentRecord({ id: 'processing', status: 'processing' }),
      documentRecord({ id: 'missing-revision', active_revision_id: null }),
      documentRecord({ id: 'not-indexable', indexable: false }),
      documentRecord({
        id: 'pending-failure',
        status: 'partial_failed',
        review_status: 'pending',
      }),
      documentRecord({ id: 'approved-failure', status: 'failed' }),
      documentRecord({ id: 'retry-wait', status: 'retry_wait' }),
      documentRecord({ id: 'rejected', review_status: 'rejected' }),
    ])

    renderInWorkspace(<LibraryPage />)

    expect(await screen.findByRole('region', { name: '学习就绪入口' })).toBeInTheDocument()
    await expectReadinessCount('可学习', 1)
    await expectReadinessCount('待审核', 1)
    await expectReadinessCount('准备中', 3)
    await expectReadinessCount('需要处理', 3)
  })

  it('links ready PDF and Markdown sources to every available study workflow', async () => {
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([
      documentRecord({ id: 'pdf-source' }),
      documentRecord({
        id: 'markdown-source',
        filename: 'scheduler.md',
        media_type: 'text/markdown',
      }),
    ])

    renderInWorkspace(<LibraryPage />)

    const actions = await screen.findByRole('region', { name: '学习就绪入口' })
    expect(await within(actions).findByRole('link', { name: '查看概念地图' })).toHaveAttribute(
      'href',
      '/graph',
    )
    expect(within(actions).getByRole('link', { name: '开始问答' })).toHaveAttribute('href', '/qa')
    expect(within(actions).getByRole('link', { name: '生成复习笔记' })).toHaveAttribute(
      'href',
      '/notes',
    )
  })

  it('uses status text instead of links when provider and note generation are unavailable', async () => {
    const capabilities: RuntimeCapabilities = {
      ...availableCapabilities,
      provider: { status: 'not_configured', label: '未配置回答模型' },
      note_workflow: {
        ...availableCapabilities.note_workflow,
        enabled: false,
      },
    }
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([documentRecord()])

    renderInWorkspace(<LibraryPage />, { workspace: { capabilities } })

    const actions = await screen.findByRole('region', { name: '学习就绪入口' })
    expect(await within(actions).findByRole('link', { name: '查看概念地图' })).toHaveAttribute(
      'href',
      '/graph',
    )
    expect(within(actions).queryByRole('link', { name: '开始问答' })).not.toBeInTheDocument()
    expect(within(actions).queryByRole('link', { name: '生成复习笔记' })).not.toBeInTheDocument()
    expect(within(actions).getByText('问答服务不可用')).toBeInTheDocument()
    expect(within(actions).getByText('笔记生成不可用')).toBeInTheDocument()
  })

  it('gates notes when the generation capability is unavailable', async () => {
    const capabilities: RuntimeCapabilities = {
      ...availableCapabilities,
      note_workflow: {
        ...availableCapabilities.note_workflow,
        generation: { status: 'unavailable', label: '笔记生成未启用' },
      },
    }
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([documentRecord()])

    renderInWorkspace(<LibraryPage />, { workspace: { capabilities } })

    const actions = await screen.findByRole('region', { name: '学习就绪入口' })
    expect(await within(actions).findByRole('link', { name: '开始问答' })).toHaveAttribute(
      'href',
      '/qa',
    )
    expect(within(actions).queryByRole('link', { name: '生成复习笔记' })).not.toBeInTheDocument()
    expect(within(actions).getByText('笔记生成不可用')).toBeInTheDocument()
  })

  it('does not offer notes when the only study-ready source is an image', async () => {
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([
      documentRecord({ id: 'image', filename: 'diagram.png', media_type: 'image/png' }),
    ])

    renderInWorkspace(<LibraryPage />)

    const actions = await screen.findByRole('region', { name: '学习就绪入口' })
    await expectReadinessCount('可学习', 1)
    expect(await within(actions).findByRole('link', { name: '查看概念地图' })).toBeInTheDocument()
    expect(within(actions).getByRole('link', { name: '开始问答' })).toBeInTheDocument()
    expect(within(actions).queryByRole('link', { name: '生成复习笔记' })).not.toBeInTheDocument()
    expect(within(actions).getByText('暂无可生成笔记的资料')).toBeInTheDocument()
  })

  it('matches note eligibility for corpus role, media type, and legacy PPT filenames', async () => {
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([
      documentRecord({ id: 'questions', corpus_role: 'questions' }),
      documentRecord({
        id: 'legacy-ppt',
        filename: 'legacy.ppt',
        media_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      }),
      documentRecord({ id: 'unsupported-media', media_type: 'application/octet-stream' }),
    ])

    renderInWorkspace(<LibraryPage />)

    const actions = await screen.findByRole('region', { name: '学习就绪入口' })
    await expectReadinessCount('可学习', 3)
    expect(await within(actions).findByRole('link', { name: '查看概念地图' })).toBeInTheDocument()
    expect(within(actions).getByRole('link', { name: '开始问答' })).toBeInTheDocument()
    expect(within(actions).queryByRole('link', { name: '生成复习笔记' })).not.toBeInTheDocument()
    expect(within(actions).getByText('暂无可生成笔记的资料')).toBeInTheDocument()
  })

  it('does not render readiness before the document projection resolves', () => {
    vi.spyOn(studyApi, 'listDocuments').mockReturnValue(new Promise(() => undefined))

    renderInWorkspace(<LibraryPage />)

    expect(screen.getByRole('region', { name: '正在加载资料' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '学习就绪入口' })).not.toBeInTheDocument()
  })

  it('shows ProblemDetails when the document projection is unavailable', async () => {
    vi.spyOn(studyApi, 'listDocuments').mockRejectedValue(
      new ApiError(
        problem({
          status: 503,
          code: 'WORKSPACE_UNAVAILABLE',
          title: '资料投影不可用',
          detail: '请检查本地 API。',
        }),
      ),
    )

    renderInWorkspace(<LibraryPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('无法读取资料')
    expect(screen.getByRole('alert')).toHaveTextContent('请检查本地 API。')
    expect(screen.queryByRole('region', { name: '学习就绪入口' })).not.toBeInTheDocument()
  })
})
