import type {
  KnowledgeGraphEdge,
  KnowledgeGraphEdgeKind,
  KnowledgeGraphNode,
  KnowledgeGraphNodeKind,
  KnowledgeGraphOccurrence,
  KnowledgeGraphResponse,
} from '../../api/types'

export type {
  KnowledgeGraphEdge,
  KnowledgeGraphEdgeKind,
  KnowledgeGraphNode,
  KnowledgeGraphNodeKind,
  KnowledgeGraphOccurrence,
  KnowledgeGraphResponse,
}

export class KnowledgeGraphApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'KnowledgeGraphApiError'
  }
}

async function getCourseKnowledgeGraph(
  courseId: string,
  signal?: AbortSignal,
): Promise<KnowledgeGraphResponse> {
  const response = await fetch(
    `/api/v1/courses/${encodeURIComponent(courseId)}/knowledge-graph?node_limit=14&edge_limit=30`,
    {
      credentials: 'include',
      headers: { Accept: 'application/json' },
      signal,
    },
  )
  if (!response.ok) {
    let message = '无法读取当前课程的知识图谱。'
    try {
      const problem = (await response.json()) as { detail?: unknown; title?: unknown }
      if (typeof problem.detail === 'string' && problem.detail.trim()) {
        message = problem.detail
      } else if (typeof problem.title === 'string' && problem.title.trim()) {
        message = problem.title
      }
    } catch {
      // Keep the stable local error when the response is not JSON.
    }
    throw new KnowledgeGraphApiError(response.status, message)
  }
  const body: unknown = await response.json()
  if (!isKnowledgeGraphResponse(body)) {
    throw new KnowledgeGraphApiError(502, '知识图谱响应格式无效。')
  }
  return body
}

function isKnowledgeGraphResponse(value: unknown): value is KnowledgeGraphResponse {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<KnowledgeGraphResponse>
  return (
    typeof candidate.course_id === 'string' &&
    typeof candidate.tokenizer_version === 'string' &&
    typeof candidate.active_document_count === 'number' &&
    typeof candidate.included_document_count === 'number' &&
    typeof candidate.source_chunk_count === 'number' &&
    typeof candidate.truncated === 'boolean' &&
    Array.isArray(candidate.nodes) &&
    Array.isArray(candidate.edges)
  )
}

export const knowledgeGraphApi = { getCourseKnowledgeGraph }
