import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ApiError, studyApi } from '../../api/client'
import type { RuntimeCapabilities, StructuredAnswer } from '../../api/types'
import { answeredSnapshot, citationSource, problem } from '../../test/fixtures'
import { availableCapabilities, renderInWorkspace } from '../../test/render'
import { QAPage } from './QAPage'
import { queryRefetchInterval } from './queryPolling'

async function submitQuestion() {
  const input = screen.getByLabelText('课程问题')
  await screen.findByRole('button', { name: '提交问题' })
  return { input, submit: screen.getByRole('button', { name: '提交问题' }) }
}

describe('QAPage', () => {
  it('polls only while the query SSE is connecting or reconnecting', () => {
    const pending = answeredSnapshot({ status: 'retrieving', answer: null })

    expect(queryRefetchInterval(pending, 'connecting')).toBe(10_000)
    expect(queryRefetchInterval(pending, 'reconnecting')).toBe(10_000)
    expect(queryRefetchInterval(pending, 'open')).toBe(false)
    expect(queryRefetchInterval(answeredSnapshot(), 'reconnecting')).toBe(false)
  })

  it('shows retrieval progress until a nonterminal query reaches an answered snapshot', async () => {
    const pending = answeredSnapshot({
      status: 'retrieving',
      answer: null,
      completed_at: null,
    })
    let pushEvent: () => void = () => undefined
    const close = vi.fn()
    vi.spyOn(studyApi, 'createQuery').mockResolvedValue(pending)
    vi.spyOn(studyApi, 'getQuery')
      .mockResolvedValueOnce(pending)
      .mockResolvedValueOnce(answeredSnapshot())
    vi.spyOn(studyApi, 'subscribe').mockImplementation(
      (_path, onEvent) => {
        pushEvent = () =>
          onEvent({
            stream_version: '1',
            sequence: 4,
            occurred_at: '2026-07-19T06:00:00Z',
            trace_id: 'query-sse-test',
            data: { status: 'answered' },
          })
        return close
      },
    )
    const { user } = renderInWorkspace(<QAPage />)
    const { input, submit } = await submitQuestion()

    await user.type(input, '什么是进程？')
    await user.click(submit)

    const retrievalStage = await screen.findByText('检索课程资料')
    expect(retrievalStage.closest('li')).toHaveClass('is-active')
    await waitFor(() => expect(studyApi.subscribe).toHaveBeenCalledWith(
      '/queries/query-1/events',
      expect.any(Function),
      expect.any(Function),
      expect.any(Function),
    ))
    await act(async () => pushEvent())
    expect(await screen.findByText('进程是资源分配的基本单位。')).toBeInTheDocument()
    await waitFor(() => expect(close).toHaveBeenCalledOnce())
  })

  it('renders an answered claim and opens its scoped source with bbox highlighting', async () => {
    vi.spyOn(studyApi, 'createQuery').mockResolvedValue(answeredSnapshot())
    vi.spyOn(studyApi, 'getCitation').mockResolvedValue(citationSource())
    const { container, user } = renderInWorkspace(<QAPage />)
    const { input, submit } = await submitQuestion()

    await user.type(input, '什么是进程？')
    await user.click(submit)

    expect(await screen.findByText('进程是资源分配的基本单位。')).toBeInTheDocument()
    expect(screen.getByText('进程拥有独立地址空间。')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /chapter-1\.png/ }))

    const dialog = await screen.findByRole('dialog', { name: '来源' })
    expect(dialog).toHaveTextContent('进程拥有独立的地址空间。')
    const image = screen.getByRole('img', { name: /chapter-1\.png 页面 3/ })
    fireEvent.load(image)
    const highlight = container.querySelector('.bbox-highlight')
    expect(highlight).toHaveStyle({ left: '10%', top: '20%', width: '40%', height: '8%' })
    expect(studyApi.getCitation).toHaveBeenCalledWith('query-1', 'citation-1')
  })

  it('shows abstention separately and never renders evidence-free answer text', async () => {
    const answer: StructuredAnswer = {
      schema_version: '1.0',
      query_id: 'query-1',
      status: 'abstained',
      answer_markdown: '',
      claims: [],
      citations: [],
      refusal: { code: 'INSUFFICIENT_EVIDENCE', message: '课程资料未覆盖该问题。' },
    }
    vi.spyOn(studyApi, 'createQuery').mockResolvedValue(
      answeredSnapshot({ status: 'abstained', answer }),
    )
    const { user } = renderInWorkspace(<QAPage />)
    const { input, submit } = await submitQuestion()

    await user.type(input, '课外问题')
    await user.click(submit)

    expect(await screen.findByText('依据不足')).toBeInTheDocument()
    expect(screen.getByText('课程资料未覆盖该问题。')).toBeInTheDocument()
    expect(screen.queryByText('进程是资源分配的基本单位。')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /chapter-1/ })).not.toBeInTheDocument()
  })

  it('renders a Provider failure snapshot without manufacturing an answer', async () => {
    vi.spyOn(studyApi, 'createQuery').mockResolvedValue(
      answeredSnapshot({
        status: 'failed',
        answer: null,
        failure_code: 'PROVIDER_TIMEOUT',
      }),
    )
    const { user } = renderInWorkspace(<QAPage />)
    const { input, submit } = await submitQuestion()

    await user.type(input, '解释调度')
    await user.click(submit)

    expect(await screen.findByRole('alert')).toHaveTextContent('Provider 调用失败')
    expect(screen.getByRole('alert')).toHaveTextContent('PROVIDER_TIMEOUT')
    expect(screen.queryByText('进程是资源分配的基本单位。')).not.toBeInTheDocument()
  })

  it('disables submission when the capability API reports no Provider', () => {
    const capabilities: RuntimeCapabilities = {
      ...availableCapabilities,
      provider: { status: 'not_configured', label: '未配置回答模型' },
    }

    renderInWorkspace(<QAPage />, { workspace: { capabilities } })

    expect(screen.getByRole('status')).toHaveTextContent('Provider 未配置')
    expect(screen.getByLabelText('课程问题')).toBeDisabled()
    expect(screen.getByRole('button', { name: '提交问题' })).toBeDisabled()
  })

  it('keeps stale or deleted citation content closed when source lookup returns 404', async () => {
    vi.spyOn(studyApi, 'createQuery').mockResolvedValue(answeredSnapshot())
    vi.spyOn(studyApi, 'getCitation').mockRejectedValue(
      new ApiError(
        problem({
          status: 404,
          code: 'RESOURCE_NOT_FOUND',
          title: '引用来源不存在或已失效',
          detail: null,
        }),
      ),
    )
    const { user } = renderInWorkspace(<QAPage />)
    const { input, submit } = await submitQuestion()
    await user.type(input, '什么是进程？')
    await user.click(submit)
    await user.click(await screen.findByRole('button', { name: /chapter-1\.png/ }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('来源不可用'))
    expect(screen.getByRole('alert')).toHaveTextContent('引用来源不存在或已失效')
    expect(screen.queryByRole('dialog', { name: '来源' })).not.toBeInTheDocument()
    expect(screen.queryByText('引用原文')).not.toBeInTheDocument()
  })
})
