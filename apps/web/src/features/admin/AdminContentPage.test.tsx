import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'

import { studyApi } from '../../api/client'
import type {
  AdminCourse,
  AdminDocument,
  AdminNote,
  KnowledgeGraphResponse,
} from '../../api/types'
import { createTestQueryClient } from '../../test/render'
import { AdminContentPage } from './AdminContentPage'

vi.mock('../knowledge-graph/KnowledgeGraphPage', () => ({
  KnowledgeGraphViewer: ({
    graph,
    readOnly,
  }: {
    graph: KnowledgeGraphResponse
    readOnly?: boolean
  }) => (
    <div data-read-only={String(readOnly)} data-testid="admin-graph-viewer">
      只读知识图谱 {graph.course_id}
    </div>
  ),
}))

const firstCourse: AdminCourse = {
  id: 'course-1',
  title: '操作系统',
  lifecycle: 'active',
  owner_account_id: 'account-1',
  owner_email: 'student@example.com',
  owner_display_name: '复习同学',
  owner_subject: 'account:student@example.com',
  document_count: 1,
  note_count: 1,
  created_at: '2026-07-20T08:00:00Z',
  updated_at: '2026-07-24T08:00:00Z',
}

const secondCourse: AdminCourse = {
  id: 'course-2',
  title: '数据结构',
  lifecycle: 'active',
  owner_account_id: 'account-2',
  owner_email: 'reader@example.com',
  owner_display_name: '旁听同学',
  owner_subject: 'account:reader@example.com',
  document_count: 1,
  note_count: 1,
  created_at: '2026-07-21T08:00:00Z',
  updated_at: '2026-07-25T08:00:00Z',
}

function adminDocument(course: AdminCourse, id: string, filename: string): AdminDocument {
  return {
    id,
    course_id: course.id,
    course_title: course.title,
    owner_account_id: course.owner_account_id,
    owner_email: course.owner_email,
    owner_display_name: course.owner_display_name,
    owner_subject: course.owner_subject,
    filename,
    media_type: 'application/pdf',
    size_bytes: 1024,
    corpus_role: 'corpus',
    status: 'ready',
    page_count: 8,
    review_status: 'approved',
    review_note: null,
    reviewed_by_account_id: 'account-admin',
    reviewed_by_email: 'admin@example.com',
    reviewed_at: '2026-07-24T09:00:00Z',
    created_at: '2026-07-24T08:00:00Z',
    updated_at: '2026-07-24T09:00:00Z',
  }
}

function adminNote(courseId: string): AdminNote {
  return {
    id: 'note-course-2',
    course_id: courseId,
    section_path: ['第三章', '树'],
    title: '树与图笔记',
    body_markdown:
      '# 树结构\n\n遍历方法包括深度优先与广度优先。\n\n![外链图](https://example.com/tracker.png)',
    version: 2,
    generation: 1,
    generated_by_model: false,
    status: 'ready',
    created_at: '2026-07-25T08:00:00Z',
    updated_at: '2026-07-25T09:00:00Z',
  }
}

function graph(courseId: string): KnowledgeGraphResponse {
  return {
    course_id: courseId,
    tokenizer_version: 'jieba-test',
    active_document_count: 1,
    included_document_count: 1,
    source_chunk_count: 2,
    node_limit: 14,
    edge_limit: 30,
    truncated: false,
    nodes: [
      {
        id: 'concept:tree',
        kind: 'concept',
        label: '树',
        occurrences_truncated: false,
      },
    ],
    edges: [],
  }
}

function renderPage() {
  const queryClient = createTestQueryClient()
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }
  return render(<AdminContentPage />, { wrapper: Wrapper })
}

describe('AdminContentPage', () => {
  it('browses another users course documents, notes, and read-only graph', async () => {
    vi.spyOn(studyApi, 'listAdminCourses').mockResolvedValue({
      items: [firstCourse, secondCourse],
    })
    vi.spyOn(studyApi, 'listAdminDocuments').mockResolvedValue({
      items: [
        adminDocument(firstCourse, 'document-1', '进程管理.pdf'),
        adminDocument(secondCourse, 'document-2', '树与图.pdf'),
      ],
    })
    const listNotes = vi
      .spyOn(studyApi, 'listAdminCourseNotes')
      .mockImplementation(async (courseId) => ({
        items: courseId === secondCourse.id ? [adminNote(courseId)] : [],
      }))
    const getGraph = vi
      .spyOn(studyApi, 'getAdminCourseKnowledgeGraph')
      .mockImplementation(async (courseId) => graph(courseId))
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByRole('heading', { name: '用户内容' })).toBeInTheDocument()
    expect(await screen.findByText('进程管理.pdf')).toBeInTheDocument()
    expect(screen.queryByText('树与图.pdf')).not.toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('上传者'), secondCourse.owner_account_id!)

    expect(screen.getByLabelText('课程')).toHaveValue(secondCourse.id)
    expect(await screen.findByText('树与图.pdf')).toBeInTheDocument()
    expect(screen.queryByText('进程管理.pdf')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '预览 树与图.pdf' })).toHaveAttribute(
      'href',
      '/api/v1/admin/documents/document-2/content',
    )

    await user.click(screen.getByRole('tab', { name: '笔记' }))

    expect(await screen.findByRole('heading', { name: '树与图笔记' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '树结构' })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: '笔记正文' })).toHaveTextContent(
      '遍历方法包括深度优先与广度优先。',
    )
    expect(screen.queryByRole('img', { name: '外链图' })).not.toBeInTheDocument()
    expect(screen.getByText('[图片已隐藏：外链图]')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /新建|编辑|重新生成/ })).not.toBeInTheDocument()
    expect(listNotes).toHaveBeenCalledWith(secondCourse.id)

    await user.click(screen.getByRole('tab', { name: '知识图谱' }))

    expect(await screen.findByTestId('admin-graph-viewer')).toHaveAttribute(
      'data-read-only',
      'true',
    )
    expect(screen.getByTestId('admin-graph-viewer')).toHaveTextContent(secondCourse.id)
    expect(getGraph).toHaveBeenCalledWith(secondCourse.id)
  })
})
