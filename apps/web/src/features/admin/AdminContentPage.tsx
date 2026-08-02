import { useQuery } from '@tanstack/react-query'
import {
  BookOpen,
  Eye,
  FileText,
  LoaderCircle,
  Network,
  NotebookTabs,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'

import { studyApi } from '../../api/client'
import type { AdminCourse, AdminDocument, AdminNote } from '../../api/types'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { StatusBadge, type StatusTone } from '../../components/ui/StatusBadge'
import { KnowledgeGraphViewer } from '../knowledge-graph/KnowledgeGraphPage'

type ContentTab = 'documents' | 'notes' | 'graph'

const EMPTY_COURSES: AdminCourse[] = []

interface OwnerOption {
  email: string
  key: string
  label: string
}

const contentTabs: ReadonlyArray<{ id: ContentTab; label: string }> = [
  { id: 'documents', label: '资料' },
  { id: 'notes', label: '笔记' },
  { id: 'graph', label: '知识图谱' },
]

const reviewPresentation: Record<
  AdminDocument['review_status'],
  { label: string; tone: StatusTone }
> = {
  pending: { label: '待审核', tone: 'warning' },
  approved: { label: '已通过', tone: 'success' },
  rejected: { label: '未通过', tone: 'danger' },
}

function ownerKey(course: AdminCourse): string {
  return course.owner_account_id ?? `subject:${course.owner_subject}`
}

function ownerLabel(course: AdminCourse): string {
  return course.owner_display_name ?? course.owner_email ?? '未绑定账号'
}

function ownerEmail(course: AdminCourse): string {
  return course.owner_email ?? course.owner_subject
}

function documentStatus(status: string): { label: string; tone: StatusTone } {
  switch (status) {
    case 'ready':
      return { label: '可查看', tone: 'success' }
    case 'failed':
    case 'partial_failed':
      return { label: '处理失败', tone: 'danger' }
    case 'uploading':
      return { label: '上传中', tone: 'info' }
    default:
      return { label: '处理中', tone: 'warning' }
  }
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

export function AdminContentPage() {
  const [owner, setOwner] = useState('all')
  const [selectedCourseId, setSelectedCourseId] = useState<string | null>(null)
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null)
  const [tab, setTab] = useState<ContentTab>('documents')
  const coursesQuery = useQuery({
    queryKey: ['admin', 'courses'],
    queryFn: () => studyApi.listAdminCourses(),
  })
  const courses = coursesQuery.data?.items ?? EMPTY_COURSES
  const owners = useMemo<OwnerOption[]>(() => {
    const uniqueOwners = new Map<string, OwnerOption>()
    for (const course of courses) {
      const key = ownerKey(course)
      if (!uniqueOwners.has(key)) {
        uniqueOwners.set(key, {
          email: ownerEmail(course),
          key,
          label: ownerLabel(course),
        })
      }
    }
    return Array.from(uniqueOwners.values()).sort((first, second) =>
      first.label.localeCompare(second.label, 'zh-CN'),
    )
  }, [courses])
  const visibleCourses =
    owner === 'all' ? courses : courses.filter((course) => ownerKey(course) === owner)
  const selectedCourse =
    visibleCourses.find((course) => course.id === selectedCourseId) ?? visibleCourses[0]
  const courseId = selectedCourse?.id ?? ''
  const documentsQuery = useQuery({
    queryKey: ['admin', 'documents', 'content'],
    queryFn: () => studyApi.listAdminDocuments(),
    enabled: courseId !== '' && tab === 'documents',
  })
  const notesQuery = useQuery({
    queryKey: ['admin', 'courses', courseId, 'notes'],
    queryFn: () => studyApi.listAdminCourseNotes(courseId),
    enabled: courseId !== '' && tab === 'notes',
  })
  const graphQuery = useQuery({
    queryKey: ['admin', 'courses', courseId, 'knowledge-graph'],
    queryFn: () => studyApi.getAdminCourseKnowledgeGraph(courseId),
    enabled: courseId !== '' && tab === 'graph',
    retry: false,
  })
  const documents = (documentsQuery.data?.items ?? []).filter(
    (document) => document.course_id === courseId,
  )
  const notes = notesQuery.data?.items ?? []
  const selectedNote = notes.find((note) => note.id === selectedNoteId) ?? notes[0]

  const selectOwner = (nextOwner: string) => {
    setOwner(nextOwner)
    setSelectedCourseId(null)
    setSelectedNoteId(null)
  }
  const selectCourse = (nextCourseId: string) => {
    setSelectedCourseId(nextCourseId)
    setSelectedNoteId(null)
  }

  return (
    <section className="admin-page admin-content-page">
      <header className="admin-page__header">
        <div>
          <p className="section-kicker">CONTENT</p>
          <h2>用户内容</h2>
          <p>按用户和课程查看其提交的资料、创建的笔记与知识图谱。</p>
        </div>
      </header>

      {coursesQuery.isError ? (
        <ErrorNotice
          error={coursesQuery.error}
          onRetry={() => void coursesQuery.refetch()}
          title="无法读取用户课程"
        />
      ) : coursesQuery.isLoading ? (
        <div className="loading-state loading-state--inline">
          <LoaderCircle aria-hidden="true" className="spin" size={18} />
          <span>读取用户课程</span>
        </div>
      ) : courses.length === 0 ? (
        <div className="admin-content-empty">
          <BookOpen aria-hidden="true" size={24} />
          <p>暂无用户课程</p>
        </div>
      ) : (
        <>
          <div aria-label="用户内容筛选" className="admin-content-picker">
            <label>
              <span>上传者</span>
              <select onChange={(event) => selectOwner(event.target.value)} value={owner}>
                <option value="all">全部用户</option>
                {owners.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.label} · {option.email}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>课程</span>
              <select
                onChange={(event) => selectCourse(event.target.value)}
                value={selectedCourse?.id ?? ''}
              >
                {visibleCourses.map((course) => (
                  <option key={course.id} value={course.id}>
                    {course.title}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {selectedCourse ? (
            <>
              <CourseSummary course={selectedCourse} />
              <div aria-label="用户内容类型" className="admin-tabs" role="tablist">
                {contentTabs.map((item) => (
                  <button
                    aria-controls={`admin-content-panel-${item.id}`}
                    aria-selected={tab === item.id}
                    id={`admin-content-tab-${item.id}`}
                    key={item.id}
                    onClick={() => setTab(item.id)}
                    role="tab"
                    type="button"
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <div
                aria-labelledby={`admin-content-tab-${tab}`}
                id={`admin-content-panel-${tab}`}
                role="tabpanel"
              >
                {tab === 'documents' ? (
                  <DocumentsPanel
                    documents={documents}
                    error={documentsQuery.error}
                    loading={documentsQuery.isLoading}
                    onRetry={() => void documentsQuery.refetch()}
                  />
                ) : tab === 'notes' ? (
                  <NotesPanel
                    error={notesQuery.error}
                    loading={notesQuery.isLoading}
                    notes={notes}
                    onRetry={() => void notesQuery.refetch()}
                    onSelect={setSelectedNoteId}
                    selected={selectedNote}
                  />
                ) : (
                  <GraphPanel
                    error={graphQuery.error}
                    graph={graphQuery.data}
                    loading={graphQuery.isLoading}
                    onRetry={() => void graphQuery.refetch()}
                  />
                )}
              </div>
            </>
          ) : (
            <div className="admin-content-empty">
              <BookOpen aria-hidden="true" size={24} />
              <p>该用户暂无课程</p>
            </div>
          )}
        </>
      )}
    </section>
  )
}

function CourseSummary({ course }: { course: AdminCourse }) {
  return (
    <section aria-label="当前查看课程" className="admin-content-summary">
      <div>
        <span>当前课程</span>
        <strong>{course.title}</strong>
        <small>
          {ownerLabel(course)} · {ownerEmail(course)}
        </small>
      </div>
      <dl>
        <div>
          <dt>资料</dt>
          <dd>{course.document_count}</dd>
        </div>
        <div>
          <dt>笔记</dt>
          <dd>{course.note_count}</dd>
        </div>
      </dl>
    </section>
  )
}

function DocumentsPanel({
  documents,
  error,
  loading,
  onRetry,
}: {
  documents: AdminDocument[]
  error: unknown
  loading: boolean
  onRetry: () => void
}) {
  if (error) return <ErrorNotice error={error} onRetry={onRetry} title="无法读取课程资料" />
  if (loading) {
    return (
      <div className="loading-state loading-state--inline">
        <LoaderCircle aria-hidden="true" className="spin" size={18} />
        <span>读取课程资料</span>
      </div>
    )
  }
  if (documents.length === 0) {
    return (
      <div className="admin-content-empty">
        <FileText aria-hidden="true" size={24} />
        <p>该课程暂无资料</p>
      </div>
    )
  }

  return (
    <div className="admin-table-wrap">
      <table className="admin-table admin-table--content">
        <thead>
          <tr>
            <th>资料</th>
            <th>处理状态</th>
            <th>审核状态</th>
            <th>上传时间</th>
            <th className="table-actions">操作</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((document) => {
            const processing = documentStatus(document.status)
            const review = reviewPresentation[document.review_status]
            return (
              <tr key={document.id}>
                <td>
                  <strong>{document.filename}</strong>
                  <small>
                    {document.media_type}
                    {document.page_count ? ` · ${document.page_count} 页` : ''}
                  </small>
                </td>
                <td>
                  <StatusBadge tone={processing.tone}>{processing.label}</StatusBadge>
                </td>
                <td>
                  <StatusBadge tone={review.tone}>{review.label}</StatusBadge>
                </td>
                <td>{formatDateTime(document.created_at)}</td>
                <td className="table-actions">
                  <a
                    aria-label={`预览 ${document.filename}`}
                    className="icon-button icon-button--small"
                    href={studyApi.adminDocumentContentUrl(document.id)}
                    rel="noreferrer"
                    target="_blank"
                    title={`预览 ${document.filename}`}
                  >
                    <Eye aria-hidden="true" size={16} />
                  </a>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function NotesPanel({
  error,
  loading,
  notes,
  onRetry,
  onSelect,
  selected,
}: {
  error: unknown
  loading: boolean
  notes: AdminNote[]
  onRetry: () => void
  onSelect: (noteId: string) => void
  selected: AdminNote | undefined
}) {
  if (error) return <ErrorNotice error={error} onRetry={onRetry} title="无法读取课程笔记" />
  if (loading) {
    return (
      <div className="loading-state loading-state--inline">
        <LoaderCircle aria-hidden="true" className="spin" size={18} />
        <span>读取课程笔记</span>
      </div>
    )
  }
  if (!selected) {
    return (
      <div className="admin-content-empty">
        <NotebookTabs aria-hidden="true" size={24} />
        <p>该课程暂无笔记</p>
      </div>
    )
  }

  return (
    <div className="admin-note-browser">
      <nav aria-label="课程笔记">
        {notes.map((note) => (
          <button
            aria-current={note.id === selected.id ? 'page' : undefined}
            key={note.id}
            onClick={() => onSelect(note.id)}
            type="button"
          >
            <FileText aria-hidden="true" size={16} />
            <span>
              <strong>{note.title}</strong>
              <small>{note.section_path.join(' / ') || '未分类'}</small>
            </span>
          </button>
        ))}
      </nav>
      <article aria-label="笔记正文" className="admin-note-reader">
        <header>
          <div>
            <span>{selected.generated_by_model ? '自动生成' : '用户创建'}</span>
            <h3>{selected.title}</h3>
          </div>
          <StatusBadge tone={selected.status === 'ready' ? 'success' : 'neutral'}>
            {selected.status === 'ready' ? '可查看' : selected.status}
          </StatusBadge>
        </header>
        <div className="note-preview">
          {selected.body_markdown.trim() ? (
            <ReactMarkdown
              components={{
                img: ({ alt }) => (
                  <span className="muted">[图片已隐藏{alt ? `：${alt}` : ''}]</span>
                ),
              }}
              skipHtml
            >
              {selected.body_markdown}
            </ReactMarkdown>
          ) : (
            <p className="muted">暂无正文</p>
          )}
        </div>
        <footer>更新于 {formatDateTime(selected.updated_at)}</footer>
      </article>
    </div>
  )
}

function GraphPanel({
  error,
  graph,
  loading,
  onRetry,
}: {
  error: unknown
  graph: Awaited<ReturnType<typeof studyApi.getAdminCourseKnowledgeGraph>> | undefined
  loading: boolean
  onRetry: () => void
}) {
  if (error) return <ErrorNotice error={error} onRetry={onRetry} title="无法读取课程知识图谱" />
  if (loading) {
    return (
      <div className="loading-state loading-state--inline">
        <LoaderCircle aria-hidden="true" className="spin" size={18} />
        <span>读取课程知识图谱</span>
      </div>
    )
  }
  const conceptCount = graph?.nodes.filter((node) => node.kind === 'concept').length ?? 0
  if (!graph || conceptCount === 0) {
    return (
      <div className="admin-content-empty">
        <Network aria-hidden="true" size={24} />
        <p>该课程暂无知识图谱</p>
      </div>
    )
  }
  return <KnowledgeGraphViewer graph={graph} readOnly />
}
