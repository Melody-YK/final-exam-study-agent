import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { studyApi } from '../../api/client'
import type { LabTrace, RuntimeCapabilities } from '../../api/types'
import { labTrace } from '../../test/fixtures'
import { availableCapabilities, renderInWorkspace } from '../../test/render'
import { DemoLabPage } from './DemoLabPage'

describe('DemoLabPage', () => {
  it('renders only typed redacted trace fields', async () => {
    const trace = {
      ...labTrace({ refusal_reason: 'INSUFFICIENT_EVIDENCE' }),
      system_prompt: 'private system prompt',
      object_key: 'private/course-1/original.pdf',
      provider_payload: { api_key: 'secret-key' },
    } as LabTrace & {
      system_prompt: string
      object_key: string
      provider_payload: { api_key: string }
    }
    vi.spyOn(studyApi, 'getLabTrace').mockResolvedValue(trace)

    renderInWorkspace(<DemoLabPage />)

    expect(await screen.findByText('pymupdf')).toBeInTheDocument()
    expect(screen.getByText('Dense')).toBeInTheDocument()
    expect(screen.getByText('BM25')).toBeInTheDocument()
    expect(screen.getByText('RRF')).toBeInTheDocument()
    expect(screen.getByText('片段 91f2c9a31bb0')).toBeInTheDocument()
    expect(screen.getByText('87.2 ms')).toBeInTheDocument()
    expect(screen.getByText('Revision revision-1')).toBeInTheDocument()
    expect(screen.getByText('Tokenizer v1')).toBeInTheDocument()
    expect(screen.getByText('费用 0.001200')).toBeInTheDocument()
    expect(screen.getByText('拒答：INSUFFICIENT_EVIDENCE')).toBeInTheDocument()
    expect(screen.queryByText('private system prompt')).not.toBeInTheDocument()
    expect(screen.queryByText('private/course-1/original.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('secret-key')).not.toBeInTheDocument()
  })

  it('labels missing persisted facts as unavailable instead of synthesizing defaults', async () => {
    vi.spyOn(studyApi, 'getLabTrace').mockResolvedValue(
      labTrace({
        revision_id: null,
        parser_backend: null,
        tokenizer_version: null,
        embedding_model: null,
        citation_validation: null,
        usage: null,
      } as Partial<LabTrace>),
    )

    renderInWorkspace(<DemoLabPage />)

    expect(await screen.findByText('Revision 不可用')).toBeInTheDocument()
    expect(screen.getByText('Tokenizer 不可用')).toBeInTheDocument()
    expect(screen.getByText('输入 不可用')).toBeInTheDocument()
    expect(screen.getByText('输出 不可用')).toBeInTheDocument()
    expect(screen.getByText('费用 不可用')).toBeInTheDocument()
    expect(screen.queryByText('输入 0 tokens')).not.toBeInTheDocument()
  })

  it('explains retrieval timings in execution order', async () => {
    vi.spyOn(studyApi, 'getLabTrace').mockResolvedValue(
      labTrace({
        timings_ms: {
          total: 20,
          rerank: 4,
          fusion: 2,
          lexical: 7,
          dense: 6,
        },
      }),
    )

    renderInWorkspace(<DemoLabPage />)

    await screen.findByText('语义检索')
    expect(screen.getByText('Dense · 按语义相似度召回资料片段')).toBeInTheDocument()
    expect(screen.getByText('BM25 · 按原文关键词召回资料片段')).toBeInTheDocument()
    expect(screen.getByText('RRF · 合并语义与关键词候选排序')).toBeInTheDocument()
    expect(screen.getByText('Rerank · 再次按问题相关性排序')).toBeInTheDocument()
    expect(screen.getByText('Total · 含资料读取，不含 AI 回答生成')).toBeInTheDocument()

    const terms = screen.getAllByRole('term').map((term) => term.textContent)
    expect(terms).toEqual([
      '语义检索Dense · 按语义相似度召回资料片段',
      '关键词检索BM25 · 按原文关键词召回资料片段',
      '结果融合RRF · 合并语义与关键词候选排序',
      '精细重排Rerank · 再次按问题相关性排序',
      '检索总耗时Total · 含资料读取，不含 AI 回答生成',
    ])
  })

  it('does not request a trace when the capability flag is closed', () => {
    const capabilities: RuntimeCapabilities = {
      ...availableCapabilities,
      demo_lab_enabled: false,
    }
    const getTrace = vi.spyOn(studyApi, 'getLabTrace')

    renderInWorkspace(<DemoLabPage />, { workspace: { capabilities } })

    expect(screen.getByText('Demo Lab 已关闭')).toBeInTheDocument()
    expect(getTrace).not.toHaveBeenCalled()
  })
})
