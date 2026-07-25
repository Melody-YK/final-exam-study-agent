import { screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { renderInWorkspace } from '../../test/render'
import {
  describeGraphRelationship,
  KnowledgeGraphPage,
} from './KnowledgeGraphPage'
import {
  knowledgeGraphApi,
  type KnowledgeGraphNode,
  type KnowledgeGraphResponse,
} from './knowledgeGraphApi'

const flowMocks = vi.hoisted(() => ({
  fitView: vi.fn(),
  renderedEdges: [] as Array<{
    id: string
    markerEnd?: unknown
    style?: { strokeDasharray?: string }
  }>,
}))

vi.mock('@xyflow/react', async () => {
  const React = await import('react')
  return {
    Background: () => null,
    Controls: () => null,
    MarkerType: { ArrowClosed: 'arrow-closed' },
    ReactFlowProvider: ({ children }: { children: ReactNode }) => children,
    ReactFlow: ({
      children,
      edges,
      nodes,
      onNodeClick,
    }: {
      children: ReactNode
      edges: typeof flowMocks.renderedEdges
      nodes: Array<{
        ariaLabel?: string
        data: { label: ReactNode }
        id: string
      }>
      onNodeClick?: (event: unknown, node: (typeof nodes)[number]) => void
    }) => {
      flowMocks.renderedEdges = edges
      return (
        <div aria-label="课程概念地图画布">
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
      )
    },
    useNodesState: (initialNodes: unknown[]) => {
      const [nodes, setNodes] = React.useState(initialNodes)
      return [nodes, setNodes, vi.fn()]
    },
    useReactFlow: () => ({ fitView: flowMocks.fitView }),
  }
})

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

function conceptNode(id: string, label: string): KnowledgeGraphNode {
  return {
    id,
    kind: 'concept',
    label,
    document_id: null,
    revision_id: null,
    page_count: null,
    frequency: 2,
    document_count: 1,
    occurrence_count: 1,
    occurrences: [],
    occurrences_truncated: false,
  }
}

function actionableGraphFixture(): KnowledgeGraphResponse {
  const graph = graphFixture()
  return {
    ...graph,
    nodes: [
      ...graph.nodes,
      conceptNode('concept:scheduling', '调度'),
      conceptNode('concept:thread', '线程'),
      conceptNode('concept:memory', '内存'),
    ],
    edges: [
      ...graph.edges,
      {
        id: 'edge:process-scheduling',
        source: 'concept:process',
        target: 'concept:scheduling',
        kind: 'co_occurs',
        weight: 4,
      },
      {
        id: 'edge:process-thread',
        source: 'concept:process',
        target: 'concept:thread',
        kind: 'co_occurs',
        weight: 3,
      },
      {
        id: 'edge:scheduling-thread',
        source: 'concept:scheduling',
        target: 'concept:thread',
        kind: 'co_occurs',
        weight: 2,
      },
    ],
  }
}

function LocationProbe() {
  const location = useLocation()
  return (
    <output data-testid="location">
      {JSON.stringify({ pathname: location.pathname, state: location.state })}
    </output>
  )
}

describe('KnowledgeGraphPage', () => {
  beforeEach(() => {
    flowMocks.fitView.mockClear()
    flowMocks.renderedEdges = []
  })

  it('describes deterministic relationship semantics and handles undirected ordering', () => {
    const graph = actionableGraphFixture()
    const nodesById = new Map(graph.nodes.map((node) => [node.id, node]))

    expect(describeGraphRelationship(graph.edges[0]!, nodesById)).toBe(
      '操作系统包含资料“01-process.pdf”。',
    )
    expect(describeGraphRelationship(graph.edges[1]!, nodesById)).toBe(
      '资料“01-process.pdf”包含概念“进程”4 次。',
    )
    expect(describeGraphRelationship(graph.edges[2]!, nodesById)).toBe(
      '概念“进程”和“调度”共同出现在 4 个内容片段中。',
    )
    expect(
      describeGraphRelationship(graph.edges[2]!, nodesById, 'concept:scheduling'),
    ).toBe('概念“调度”和“进程”共同出现在 4 个内容片段中。')
    expect(
      describeGraphRelationship(
        { ...graph.edges[2]!, source: 'concept:missing' },
        nodesById,
      ),
    ).toBeNull()
  })

  it('renders a stable loading state while the graph request is pending', () => {
    vi.spyOn(knowledgeGraphApi, 'getCourseKnowledgeGraph').mockImplementation(
      () => new Promise<KnowledgeGraphResponse>(() => undefined),
    )

    renderInWorkspace(<KnowledgeGraphPage />)

    expect(screen.getByText('加载概念地图')).toBeInTheDocument()
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

    await screen.findByLabelText('课程概念地图画布')
    await user.click(screen.getByRole('button', { name: '适配概念地图视图' }))

    await waitFor(() =>
      expect(flowMocks.fitView).toHaveBeenCalledWith({ duration: 240, padding: 0.18 }),
    )
  })

  it('focuses on direct neighbors and only the selected nodes incident edges', async () => {
    vi.spyOn(knowledgeGraphApi, 'getCourseKnowledgeGraph').mockResolvedValue(
      actionableGraphFixture(),
    )
    const { user } = renderInWorkspace(<KnowledgeGraphPage />)

    await screen.findByLabelText('课程概念地图画布')
    await user.click(screen.getByRole('button', { name: '查看概念：进程' }))

    expect(screen.getByRole('button', { name: '查看课程：操作系统' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看概念：内存' })).toBeInTheDocument()
    const relationships = screen.getByRole('list', { name: '当前节点关系' })
    expect(
      within(relationships).getByText('资料“01-process.pdf”包含概念“进程”4 次。'),
    ).toBeInTheDocument()
    expect(
      within(relationships).getByText('概念“进程”和“调度”共同出现在 4 个内容片段中。'),
    ).toBeInTheDocument()
    expect(
      within(relationships).queryByText('概念“调度”和“线程”共同出现在 2 个内容片段中。'),
    ).not.toBeInTheDocument()

    flowMocks.fitView.mockClear()
    await user.click(screen.getByRole('button', { name: '仅看关联' }))

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '查看课程：操作系统' })).not.toBeInTheDocument()
    })
    expect(screen.queryByRole('button', { name: '查看概念：内存' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看资料：01-process.pdf' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看概念：调度' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看概念：线程' })).toBeInTheDocument()
    expect(flowMocks.renderedEdges.map((edge) => edge.id)).toEqual([
      'edge:document-concept',
      'edge:process-scheduling',
      'edge:process-thread',
    ])
    await waitFor(() =>
      expect(flowMocks.fitView).toHaveBeenCalledWith({ duration: 240, padding: 0.18 }),
    )

    await user.click(screen.getByRole('button', { name: '全部' }))

    expect(await screen.findByRole('button', { name: '查看课程：操作系统' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '查看概念：内存' })).toBeInTheDocument()
    expect(flowMocks.renderedEdges).toHaveLength(actionableGraphFixture().edges.length)
  })

  it('renders co-occurrence as dashed and undirected', async () => {
    vi.spyOn(knowledgeGraphApi, 'getCourseKnowledgeGraph').mockResolvedValue(
      actionableGraphFixture(),
    )

    renderInWorkspace(<KnowledgeGraphPage />)

    await screen.findByLabelText('课程概念地图画布')
    const contains = flowMocks.renderedEdges.find((edge) => edge.id === 'edge:course-document')
    const coOccurrence = flowMocks.renderedEdges.find(
      (edge) => edge.id === 'edge:process-scheduling',
    )
    expect(contains).toHaveProperty('markerEnd')
    expect(coOccurrence).not.toHaveProperty('markerEnd')
    expect(coOccurrence?.style?.strokeDasharray).toBe('5 4')
  })

  it('hands a bounded concept suggestion to a fresh QA draft', async () => {
    vi.spyOn(knowledgeGraphApi, 'getCourseKnowledgeGraph').mockResolvedValue(graphFixture())
    const { user } = renderInWorkspace(
      <>
        <KnowledgeGraphPage />
        <LocationProbe />
      </>,
    )

    await user.click(await screen.findByRole('button', { name: '查看概念：进程' }))
    await user.click(screen.getByRole('button', { name: '围绕此概念提问' }))

    expect(screen.getByTestId('location')).toHaveTextContent(
      JSON.stringify({
        pathname: '/qa',
        state: {
          suggestedQuestion: '请解释“进程”，并结合课程资料说明它与相关概念的联系。',
          startNewConversation: true,
        },
      }),
    )
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

    expect(await screen.findByLabelText('课程概念地图画布')).toBeInTheDocument()
    expect(getGraph).toHaveBeenCalledTimes(2)
  })
})
