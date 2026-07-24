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
  Network,
} from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'

import { useWorkspace } from '../../app/WorkspaceContext'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { PageHeader } from '../../components/ui/PageHeader'
import { StatusBadge } from '../../components/ui/StatusBadge'
import {
  knowledgeGraphApi,
  type KnowledgeGraphEdge,
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
        kicker="知识脉络"
        meta={
          graph
            ? `${graph.included_document_count} 份资料 · ${conceptCount} 个高频概念`
            : '当前课程资料'
        }
        title="课程知识图谱"
      />
      {graphQuery.isLoading ? (
        <section className="knowledge-graph-state loading-state" aria-live="polite">
          <LoaderCircle aria-hidden="true" className="spin" size={20} />
          <span>加载知识图谱</span>
        </section>
      ) : graphQuery.isError ? (
        <ErrorNotice
          error={graphQuery.error}
          onRetry={() => void graphQuery.refetch()}
          title="知识图谱不可用"
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
  const selectedNode = graph.nodes.find((node) => node.id === selectedNodeId) ?? graph.nodes[0]
  const layout = useMemo(() => buildFlowGraph(graph), [graph])

  return (
    <>
      <section className="knowledge-graph-summary" aria-label="知识图谱摘要">
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
      </section>
      <section className="knowledge-graph-workspace" aria-label="知识图谱工作区">
        <ReactFlowProvider>
          <GraphCanvas
            edges={layout.edges}
            nodes={layout.nodes}
            onSelectNode={setSelectedNodeId}
          />
        </ReactFlowProvider>
        {selectedNode ? <NodeDetails node={selectedNode} /> : null}
      </section>
    </>
  )
}

function GraphCanvas({
  nodes: initialNodes,
  edges,
  onSelectNode,
}: {
  nodes: GraphFlowNode[]
  edges: GraphFlowEdge[]
  onSelectNode: (nodeId: string) => void
}) {
  const [nodes, , onNodesChange] = useNodesState(initialNodes)
  const { fitView } = useReactFlow<GraphFlowNode, GraphFlowEdge>()

  return (
    <div className="knowledge-graph-canvas">
      <button
        aria-label="适配知识图谱视图"
        className="knowledge-graph-fit"
        onClick={() => void fitView({ duration: 240, padding: 0.18 })}
        title="适配视图"
        type="button"
      >
        <Focus aria-hidden="true" size={17} />
      </button>
      <ReactFlow<GraphFlowNode, GraphFlowEdge>
        aria-label="课程知识图谱画布"
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

function NodeDetails({ node }: { node: KnowledgeGraphNode }) {
  return (
    <aside className="knowledge-graph-details" aria-label="节点详情">
      <header>
        <span>{nodeKindLabels[node.kind]}</span>
        <h3>{node.label}</h3>
      </header>
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
        </>
      )}
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
    markerEnd: { type: MarkerType.ArrowClosed, color },
    style: {
      stroke: color,
      strokeDasharray: edge.kind === 'co_occurs' ? '5 4' : undefined,
      strokeWidth: Math.min(3, 1 + Math.log2(edge.weight + 1) * 0.45),
    },
    labelStyle: { fill: '#725016', fontSize: 11, fontWeight: 700 },
    labelBgStyle: { fill: '#fffdf7', fillOpacity: 0.92 },
  }
}
