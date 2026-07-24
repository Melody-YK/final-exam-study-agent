import { describe, expect, it, vi } from 'vitest'

import { KnowledgeGraphApiError, knowledgeGraphApi } from './knowledgeGraphApi'

const validGraph = {
  course_id: 'course/one',
  tokenizer_version: 'jieba-test',
  active_document_count: 0,
  included_document_count: 0,
  source_chunk_count: 0,
  node_limit: 64,
  edge_limit: 160,
  truncated: false,
  nodes: [],
  edges: [],
}

describe('knowledgeGraphApi', () => {
  it('encodes the course id and forwards the query abort signal', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(validGraph), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()

    await expect(
      knowledgeGraphApi.getCourseKnowledgeGraph('course/one', controller.signal),
    ).resolves.toEqual(validGraph)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/courses/course%2Fone/knowledge-graph?node_limit=14&edge_limit=30',
      {
        credentials: 'include',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      },
    )
  })

  it('uses the safe problem detail for an API failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: '课程不存在' }), {
          status: 404,
          headers: { 'Content-Type': 'application/problem+json' },
        }),
      ),
    )

    const error = await knowledgeGraphApi
      .getCourseKnowledgeGraph('missing')
      .catch((reason: unknown) => reason)

    expect(error).toBeInstanceOf(KnowledgeGraphApiError)
    expect(error).toMatchObject({ status: 404, message: '课程不存在' })
  })

  it('rejects malformed success responses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ course_id: 'course-1' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(knowledgeGraphApi.getCourseKnowledgeGraph('course-1')).rejects.toMatchObject({
      status: 502,
      message: '知识图谱响应格式无效。',
    })
  })
})
