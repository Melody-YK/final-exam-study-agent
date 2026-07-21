import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ApiError, studyApi } from '../../api/client'
import type { RuntimeCapabilities } from '../../api/types'
import { documentRecord, problem } from '../../test/fixtures'
import { availableCapabilities, renderInWorkspace } from '../../test/render'
import { LibraryPage } from './LibraryPage'

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
  })
})
