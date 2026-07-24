import { screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { renderInWorkspace } from '../../test/render'
import { KnowledgeGraphPage } from './KnowledgeGraphPage'
import {
  knowledgeGraphApi,
  type KnowledgeGraphResponse,
} from './knowledgeGraphApi'

const flowMocks = vi.hoisted(() => ({ fitView: vi.fn() }))

vi.mock('@xyflow/react', () => ({
  Background: () => null,
  Controls: () => null,
  MarkerType: { ArrowClosed: 'arrow-closed' },
  ReactFlowProvider: ({ children }: { children: ReactNode }) => children,
  ReactFlow: ({
    children,
    nodes,
    onNodeClick,
  }: {
    children: ReactNode
    nodes: Array<{
      ariaLabel?: string
      data: { label: ReactNode }
      id: string
    }>
    onNodeClick?: (event: unknown, node: (typeof nodes)[number]) => void
  }) => (
    <div aria-label="课程知识图谱画布">
      {nodes.map((node) => (
        <button
          aria-label={node.ariaLabel}
          key={node.id}
          onClick={() => onNodeClick?.({}, node)}
          type="button"
        >
          {node.data.label}
        </button>
      ))}
      {children}
    </div>
  ),
  useNodesState: (nodes: unknown[]) => [nodes, vi.fn(), vi.fn()],
  useReactFlow: () => ({ fitView: flowMocks.fitView }),
}))

function graphFixture(
  overrides: Partial<KnowledgeGraphResponse> = {},
): KnowledgeGraphResponse {
  return {
    course_id: 'course-1',
    tokenizer_version: 'jieba-test:abc123',
    active_document_count: 1,
    included_document_count: 1,
    source_chunk_count: 2,
    node_limit: 64,
    edge_limit: 160,
    truncated: false,
    nodes: [
      {
        id: 'course:course-1',
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
        id: 'document:document-1',
        kind: 'document',
        label: '01-process.pdf',
        document_id: 'document-1',
        revision_id: 'revision-1',
        page_count: 8,
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
        frequency: 4,
        document_count: 1,
        occurrence_count: 2,
        occurrences: [
          {
            document_id: 'document-1',
            document_name: '01-process.pdf',
            revision_id: 'revision-1',
            chunk_id: 'revision-1:chunk:1',
            page_ordinal: 1,
            chunk_ordinal: 1,
            count: 2,
            excerpt: '进程是资源分配和调度的基本单位。',
          },
        ],
        occurrences_truncated: false,
      },
    ],
    edges: [
      {
        id: 'edge:course-document',
        source: 'course:course-1',
        target: 'document:document-1',
        kind: 'contains',
        weight: 1,
      },
      {
        id: 'edge:document-concept',
        source: 'document:document-1',
        target: 'concept:process',
        kind: 'mentions',
        weight: 4,
      },
    ],
    ...overrides,
  }
}

describe('KnowledgeGraphPage', () => {
  it('renders a stable loading state while the graph request is pending', () => {
    vi.spyOn(knowledgeGraphApi, 'getCourseKnowledgeGraph').mockImplementation(
      () => new Promise<KnowledgeGraphResponse>(() => undefined),
    )

    renderInWorkspace(<KnowledgeGraphPage />)

    expect(screen.getByText('加载知识图谱')).toBeInTheDocument()
  })

  it('selects nodes and exposes traceable concept occurrences', async () => {
    vi.spyOn(knowledgeGraphApi, 'getCourseKnowledgeGraph').mockResolvedValue(graphFixture())
    const { user } = renderInWorkspace(<KnowledgeGraphPage />)

    expect(await screen.findByText('1 份资料 · 1 个高频概念')).toBeInTheDocument()
    expect(screen.getByText('当前课程中已就绪的资料与高频概念。')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '查看概念：进程' }))

    expect(screen.getByRole('heading', { name: '进程' })).toBeInTheDocument()
    expect(screen.getByText('第 1 页 · 第 1 个内容片段 · 出现 2 次')).toBeInTheDocument()
    expect(screen.getByText('进程是资源分配和调度的基本单位。')).toBeInTheDocument()
    expect(screen.queryByText('revision-1:chunk:1')).not.toBeInTheDocument()
  })

  it('fits the rendered graph from the icon control', async () => {
    vi.spyOn(knowledgeGraphApi, 'getCourseKnowledgeGraph').mockResolvedValue(graphFixture())
    const { user } = renderInWorkspace(<KnowledgeGraphPage />)

    await screen.findByLabelText('课程知识图谱画布')
    await user.click(screen.getByRole('button', { name: '适配知识图谱视图' }))

    expect(flowMocks.fitView).toHaveBeenCalledWith({ duration: 240, padding: 0.18 })
  })

  it('renders a source-aware empty state when no concepts qualify', async () => {
    vi.spyOn(knowledgeGraphApi, 'getCourseKnowledgeGraph').mockResolvedValue(
      graphFixture({
        active_document_count: 0,
        included_document_count: 0,
        source_chunk_count: 0,
        nodes: graphFixture().nodes.slice(0, 1),
        edges: [],
      }),
    )

    renderInWorkspace(<KnowledgeGraphPage />)

    expect(await screen.findByText('暂无高频概念')).toBeInTheDocument()
    expect(screen.getByText('当前课程没有已就绪资料。')).toBeInTheDocument()
  })

  it('shows an error and retries the independent API request', async () => {
    const getGraph = vi
      .spyOn(knowledgeGraphApi, 'getCourseKnowledgeGraph')
      .mockRejectedValueOnce(new Error('本地知识图谱 API 不可用'))
      .mockResolvedValueOnce(graphFixture())
    const { user } = renderInWorkspace(<KnowledgeGraphPage />)

    expect(await screen.findByText('本地知识图谱 API 不可用')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByLabelText('课程知识图谱画布')).toBeInTheDocument()
    expect(getGraph).toHaveBeenCalledTimes(2)
  })
})
