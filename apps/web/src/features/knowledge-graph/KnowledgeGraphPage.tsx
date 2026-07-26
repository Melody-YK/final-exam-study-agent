import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react'
import { useQuery } from '@tanstack/react-query'
import {
  BookOpen,
  FileSearch,
  Focus,
  Hash,
  LoaderCircle,
  MessageSquarePlus,
  Network,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'

import { useWorkspace } from '../../app/WorkspaceContext'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { PageHeader } from '../../components/ui/PageHeader'
import { StatusBadge } from '../../components/ui/StatusBadge'
import {
  knowledgeGraphApi,
  type KnowledgeGraphEdge,
  type KnowledgeGraphEdgeKind,
  type KnowledgeGraphNode,
  type KnowledgeGraphNodeKind,
  type KnowledgeGraphResponse,
} from './knowledgeGraphApi'

import '@xyflow/react/dist/style.css'
import './knowledge-graph.css'

type FlowNodeData = {
  label: ReactNode
  kind: KnowledgeGraphNodeKind
} & Record<string, unknown>
type GraphFlowNode = Node<FlowNodeData>
type GraphFlowEdge = Edge

const nodeKindLabels: Record<KnowledgeGraphNodeKind, string> = {
  course: '课程',
  document: '资料',
  concept: '概念',
}

const relationshipLegend: Array<{
  kind: KnowledgeGraphEdgeKind
  label: string
}> = [
  { kind: 'contains', label: '课程包含资料' },
  { kind: 'mentions', label: '资料包含概念（出现次数）' },
  { kind: 'co_occurs', label: '概念共同出现（内容片段数）' },
]

// Exported for direct semantic contract tests; this module remains the feature boundary.
// eslint-disable-next-line react-refresh/only-export-components
export function describeGraphRelationship(
  edge: KnowledgeGraphEdge,
  nodesById: ReadonlyMap<string, KnowledgeGraphNode>,
  selectedNodeId?: string,
): string | null {
  const source = nodesById.get(edge.source)
  const target = nodesById.get(edge.target)
  if (!source || !target) return null

  if (edge.kind === 'contains') {
    return `${source.label}包含资料“${target.label}”。`
  }
  if (edge.kind === 'mentions') {
    return `资料“${source.label}”包含概念“${target.label}”${edge.weight} 次。`
  }

  const [first, second] = selectedNodeId === target.id ? [target, source] : [source, target]
  return `概念“${first.label}”和“${second.label}”共同出现在 ${edge.weight} 个内容片段中。`
}

function conceptQuestion(label: string): string {
  const prefix = '请解释“'
  const suffix = '”，并结合课程资料说明它与相关概念的联系。'
  const availableLabelLength = 2000 - prefix.length - suffix.length
  return `${prefix}${label.trim().slice(0, availableLabelLength)}${suffix}`
}

export function KnowledgeGraphPage() {
  const { courseId } = useWorkspace()
  const graphQuery = useQuery({
    queryKey: ['knowledge-graph', courseId],
    queryFn: ({ signal }) => knowledgeGraphApi.getCourseKnowledgeGraph(courseId, signal),
    retry: false,
  })
  const graph = graphQuery.data
  const conceptCount = graph?.nodes.filter((node) => node.kind === 'concept').length ?? 0

  return (
    <div className="page page--knowledge-graph">
      <PageHeader
        kicker="概念脉络"
        meta={
          graph
            ? `${graph.included_document_count} 份资料 · ${conceptCount} 个高频概念`
            : '当前课程资料'
        }
        title="课程概念地图"
      />
      {graphQuery.isLoading ? (
        <section className="knowledge-graph-state loading-state" aria-live="polite">
          <LoaderCircle aria-hidden="true" className="spin" size={20} />
          <span>加载概念地图</span>
        </section>
      ) : graphQuery.isError ? (
        <ErrorNotice
          error={graphQuery.error}
          onRetry={() => void graphQuery.refetch()}
          title="概念地图不可用"
        />
      ) : graph && conceptCount === 0 ? (
        <section className="knowledge-graph-state">
          <FileSearch aria-hidden="true" size={25} />
          <h3>暂无高频概念</h3>
          <p>{graph.active_document_count === 0 ? '当前课程没有已就绪资料。' : '当前资料中的概念频次不足。'}</p>
        </section>
      ) : graph ? (
        <GraphExperience key={`${graph.course_id}:${graph.source_chunk_count}`} graph={graph} />
      ) : null}
    </div>
  )
}

function GraphExperience({ graph }: { graph: KnowledgeGraphResponse }) {
  const [selectedNodeId, setSelectedNodeId] = useState(graph.nodes[0]?.id ?? null)
  const [viewMode, setViewMode] = useState<'all' | 'related'>('all')
  const navigate = useNavigate()
  const selectedNode = graph.nodes.find((node) => node.id === selectedNodeId) ?? graph.nodes[0]
  const layout = useMemo(() => buildFlowGraph(graph), [graph])
  const nodesById = useMemo(
    () => new Map(graph.nodes.map((node) => [node.id, node])),
    [graph.nodes],
  )
  const visibleGraph = useMemo(() => {
    if (viewMode === 'all' || !selectedNode) {
      return {
        edges: layout.edges,
        nodes: layout.nodes,
        responseEdges: graph.edges,
      }
    }

    const visibleNodeIds = new Set([selectedNode.id])
    const visibleEdgeIds = new Set<string>()
    const responseEdges: KnowledgeGraphEdge[] = []
    for (const edge of graph.edges) {
      if (edge.source !== selectedNode.id && edge.target !== selectedNode.id) continue
      visibleNodeIds.add(edge.source)
      visibleNodeIds.add(edge.target)
      visibleEdgeIds.add(edge.id)
      responseEdges.push(edge)
    }
    return {
      edges: layout.edges.filter((edge) => visibleEdgeIds.has(edge.id)),
      nodes: layout.nodes.filter((node) => visibleNodeIds.has(node.id)),
      responseEdges,
    }
  }, [graph.edges, layout, selectedNode, viewMode])
  const selectedRelationships = useMemo(
    () =>
      selectedNode
        ? visibleGraph.responseEdges
            .filter(
              (edge) => edge.source === selectedNode.id || edge.target === selectedNode.id,
            )
            .map((edge) => describeGraphRelationship(edge, nodesById, selectedNode.id))
            .filter((description): description is string => description !== null)
        : [],
    [nodesById, selectedNode, visibleGraph.responseEdges],
  )

  return (
    <>
      <section className="knowledge-graph-toolbar" aria-label="概念地图控制">
        <div className="knowledge-graph-summary" aria-label="概念地图摘要">
          <div>
            <BookOpen aria-hidden="true" size={17} />
            <span>资料</span>
            <strong>
              {graph.included_document_count}/{graph.active_document_count}
            </strong>
          </div>
          <div>
            <Hash aria-hidden="true" size={17} />
            <span>概念</span>
            <strong>{graph.nodes.filter((node) => node.kind === 'concept').length}</strong>
          </div>
          <div>
            <Network aria-hidden="true" size={17} />
            <span>关系</span>
            <strong>{graph.edges.length}</strong>
          </div>
          <div>
            <span>内容片段</span>
            <strong>{graph.source_chunk_count}</strong>
          </div>
          {graph.truncated ? <StatusBadge tone="warning">已聚焦核心概念</StatusBadge> : null}
        </div>
        <div className="knowledge-graph-toolbar__row">
          <ul aria-label="关系图例" className="knowledge-graph-legend">
            {relationshipLegend.map((item) => (
              <li key={item.kind}>
                <span
                  aria-hidden="true"
                  className={`knowledge-graph-legend__line is-${item.kind}`}
                />
                <span>{item.label}</span>
              </li>
            ))}
          </ul>
          <div aria-label="显示范围" className="knowledge-graph-view-mode" role="group">
            <button
              aria-pressed={viewMode === 'all'}
              className={viewMode === 'all' ? 'is-active' : undefined}
              onClick={() => setViewMode('all')}
              type="button"
            >
              全部
            </button>
            <button
              aria-pressed={viewMode === 'related'}
              className={viewMode === 'related' ? 'is-active' : undefined}
              onClick={() => setViewMode('related')}
              type="button"
            >
              仅看关联
            </button>
          </div>
        </div>
      </section>
      <section className="knowledge-graph-workspace" aria-label="概念地图工作区">
        <ReactFlowProvider>
          <GraphCanvas
            edges={visibleGraph.edges}
            nodes={visibleGraph.nodes}
            onSelectNode={setSelectedNodeId}
            viewMode={viewMode}
          />
        </ReactFlowProvider>
        {selectedNode ? (
          <NodeDetails
            node={selectedNode}
            onAskConcept={() =>
              navigate('/qa', {
                state: {
                  suggestedQuestion: conceptQuestion(selectedNode.label),
                  startNewConversation: true,
                },
              })
            }
            relationships={selectedRelationships}
          />
        ) : null}
      </section>
    </>
  )
}

function GraphCanvas({
  nodes: initialNodes,
  edges,
  onSelectNode,
  viewMode,
}: {
  nodes: GraphFlowNode[]
  edges: GraphFlowEdge[]
  onSelectNode: (nodeId: string) => void
  viewMode: 'all' | 'related'
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes)
  const { fitView } = useReactFlow<GraphFlowNode, GraphFlowEdge>()
  const positions = useRef(new Map<string, GraphFlowNode['position']>())
  const previousViewMode = useRef(viewMode)

  useEffect(() => {
    setNodes((currentNodes) => {
      const currentById = new Map(currentNodes.map((node) => [node.id, node]))
      currentNodes.forEach((node) => positions.current.set(node.id, node.position))
      return initialNodes.map((node) => ({
        ...node,
        position:
          currentById.get(node.id)?.position ?? positions.current.get(node.id) ?? node.position,
      }))
    })
  }, [initialNodes, setNodes])

  useEffect(() => {
    if (previousViewMode.current === viewMode) return
    previousViewMode.current = viewMode
    const frame = requestAnimationFrame(() => {
      void fitView({ duration: 240, padding: 0.18 })
    })
    return () => cancelAnimationFrame(frame)
  }, [fitView, viewMode])

  return (
    <div className="knowledge-graph-canvas">
      <button
        aria-label="适配概念地图视图"
        className="knowledge-graph-fit"
        onClick={() => void fitView({ duration: 240, padding: 0.18 })}
        title="适配视图"
        type="button"
      >
        <Focus aria-hidden="true" size={17} />
      </button>
      <ReactFlow<GraphFlowNode, GraphFlowEdge>
        aria-label="课程概念地图画布"
        deleteKeyCode={null}
        edges={edges}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        maxZoom={1.8}
        minZoom={0.28}
        nodes={nodes}
        nodesConnectable={false}
        onNodeClick={(_, node) => onSelectNode(node.id)}
        onNodesChange={onNodesChange}
      >
        <Background color="#d7ddd8" gap={22} size={1} />
        <Controls position="bottom-left" showInteractive={false} />
      </ReactFlow>
    </div>
  )
}

function NodeDetails({
  node,
  onAskConcept,
  relationships,
}: {
  node: KnowledgeGraphNode
  onAskConcept: () => void
  relationships: string[]
}) {
  return (
    <aside className="knowledge-graph-details" aria-label="节点详情">
      <header>
        <span>{nodeKindLabels[node.kind]}</span>
        <h3>{node.label}</h3>
      </header>
      {node.kind === 'concept' ? (
        <button
          className="button button--primary button--small knowledge-graph-details__ask"
          onClick={onAskConcept}
          type="button"
        >
          <MessageSquarePlus aria-hidden="true" size={15} />
          围绕此概念提问
        </button>
      ) : null}
      {node.kind === 'course' ? (
        <p className="knowledge-graph-details__muted">当前课程中已就绪的资料与高频概念。</p>
      ) : node.kind === 'document' ? (
        <dl className="knowledge-graph-facts">
          <div>
            <dt>页数</dt>
            <dd>{node.page_count ?? '未知'}</dd>
          </div>
        </dl>
      ) : (
        <>
          <dl className="knowledge-graph-concept-facts">
            <div>
              <dt>出现次数</dt>
              <dd>{node.frequency}</dd>
            </div>
            <div>
              <dt>资料数</dt>
              <dd>{node.document_count}</dd>
            </div>
            <div>
              <dt>片段数</dt>
              <dd>{node.occurrence_count}</dd>
            </div>
          </dl>
        </>
      )}
      <section className="knowledge-graph-relationships">
        <h4>直接关系</h4>
        {relationships.length > 0 ? (
          <ul aria-label="当前节点关系">
            {relationships.map((relationship) => (
              <li key={relationship}>{relationship}</li>
            ))}
          </ul>
        ) : (
          <p className="knowledge-graph-details__muted">当前显示范围内没有直接关系。</p>
        )}
      </section>
      {node.kind === 'concept' ? (
        <div className="knowledge-graph-occurrences">
          <h4>来源位置</h4>
          <ol>
            {(node.occurrences ?? []).map((occurrence) => (
              <li key={`${occurrence.chunk_id}:${occurrence.chunk_ordinal}`}>
                <div>
                  <strong>{occurrence.document_name}</strong>
                  <span>
                    第 {occurrence.page_ordinal} 页 · 第 {occurrence.chunk_ordinal}{' '}
                    个内容片段 · 出现 {occurrence.count} 次
                  </span>
                </div>
                <p>{occurrence.excerpt}</p>
              </li>
            ))}
          </ol>
          {node.occurrences_truncated ? (
            <p className="knowledge-graph-details__muted">仅显示前 12 个来源位置。</p>
          ) : null}
        </div>
      ) : null}
    </aside>
  )
}

function buildFlowGraph(graph: KnowledgeGraphResponse): {
  nodes: GraphFlowNode[]
  edges: GraphFlowEdge[]
} {
  const documents = graph.nodes.filter((node) => node.kind === 'document')
  const concepts = graph.nodes.filter((node) => node.kind === 'concept')
  const course = graph.nodes.find((node) => node.kind === 'course')
  const graphWidth = 690
  const documentColumns = Math.min(3, Math.max(1, documents.length))
  const documentRows = Math.ceil(documents.length / documentColumns)
  const conceptColumns = Math.min(3, Math.max(1, concepts.length))
  const conceptStartY = 150 + documentRows * 104 + 44
  const nodes: GraphFlowNode[] = []

  if (course) {
    nodes.push(toFlowNode(course, { x: (graphWidth - 190) / 2, y: 10 }))
  }
  documents.forEach((node, index) => {
    nodes.push(
      toFlowNode(
        node,
        gridPosition(index, documents.length, documentColumns, graphWidth, 150, 104),
      ),
    )
  })
  concepts.forEach((node, index) => {
    nodes.push(
      toFlowNode(
        node,
        gridPosition(index, concepts.length, conceptColumns, graphWidth, conceptStartY, 94),
      ),
    )
  })
  return { nodes, edges: graph.edges.map(toFlowEdge) }
}

function gridPosition(
  index: number,
  total: number,
  columns: number,
  graphWidth: number,
  startY: number,
  rowGap: number,
): { x: number; y: number } {
  const row = Math.floor(index / columns)
  const column = index % columns
  const rowStart = row * columns
  const rowItems = Math.min(columns, total - rowStart)
  const columnGap = 210
  const rowWidth = (rowItems - 1) * columnGap
  return {
    x: graphWidth / 2 - rowWidth / 2 - 88 + column * columnGap,
    y: startY + row * rowGap,
  }
}

function toFlowNode(
  node: KnowledgeGraphNode,
  position: { x: number; y: number },
): GraphFlowNode {
  const meta =
    node.kind === 'concept'
      ? `${node.frequency ?? 0} 次`
      : node.kind === 'document'
        ? `${node.page_count ?? 0} 页`
        : '课程根节点'
  return {
    id: node.id,
    position,
    ariaLabel: `查看${nodeKindLabels[node.kind]}：${node.label}`,
    className: `knowledge-node knowledge-node--${node.kind}`,
    data: {
      kind: node.kind,
      label: (
        <span className="knowledge-node__content">
          <strong>{node.label}</strong>
          <small>{meta}</small>
        </span>
      ),
    },
  }
}

function toFlowEdge(edge: KnowledgeGraphEdge): GraphFlowEdge {
  const color =
    edge.kind === 'contains' ? '#527965' : edge.kind === 'mentions' ? '#4777a4' : '#a06d17'
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: edge.kind === 'co_occurs' ? 'straight' : 'smoothstep',
    label: edge.kind === 'co_occurs' ? String(edge.weight) : undefined,
    ...(edge.kind === 'co_occurs'
      ? {}
      : { markerEnd: { type: MarkerType.ArrowClosed, color } }),
    style: {
      stroke: color,
      strokeDasharray: edge.kind === 'co_occurs' ? '5 4' : undefined,
      strokeWidth: Math.min(3, 1 + Math.log2(edge.weight + 1) * 0.45),
    },
    labelStyle: { fill: '#725016', fontSize: 11, fontWeight: 700 },
    labelBgStyle: { fill: '#fffdf7', fillOpacity: 0.92 },
  }
}
