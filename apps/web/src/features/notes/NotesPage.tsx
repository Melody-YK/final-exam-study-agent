import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  BookOpen,
  Check,
  FileClock,
  FilePlus2,
  FileText,
  LoaderCircle,
  Pencil,
  Presentation,
  RefreshCw,
  Save,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'

import { ApiError, studyApi } from '../../api/client'
import type {
  DocumentRecord,
  MergedNoteBatchRequest,
  NoteBatchSnapshot,
  NoteBatchStatus,
  NoteBatchStyle,
  NoteGenerationPhase,
  NoteItemSnapshot,
  NoteRecord,
} from '../../api/types'
import { useWorkspace } from '../../app/WorkspaceContext'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { Modal } from '../../components/ui/Modal'
import { PageHeader } from '../../components/ui/PageHeader'
import { StatusBadge } from '../../components/ui/StatusBadge'

const SUPPORTED_PPTX_MEDIA_TYPE =
  'application/vnd.openxmlformats-officedocument.presentationml.presentation'

const TERMINAL_BATCH_STATUSES: NoteBatchStatus[] = [
  'partial_success',
  'succeeded',
  'failed',
  'cancelled',
]

const TERMINAL_ITEM_STATUSES = new Set(['succeeded', 'failed', 'cancelled'])

const NOTE_STYLE_OPTIONS: ReadonlyArray<{
  value: NoteBatchStyle
  label: string
  description: string
}> = [
  { value: 'exam_focus', label: '考前速记', description: '按页提炼为紧凑要点' },
  { value: 'outline', label: '结构提纲', description: '按资料层级组织内容' },
  { value: 'complete', label: '完整讲义', description: '保留更完整的资料正文' },
]

function noteStyleLabel(style: NoteBatchStyle): string {
  return NOTE_STYLE_OPTIONS.find((option) => option.value === style)?.label ?? style
}

function isNoteSourceDocument(document: DocumentRecord): boolean {
  if (
    document.status !== 'ready' ||
    document.review_status !== 'approved' ||
    !document.active_revision_id ||
    document.corpus_role !== 'corpus' ||
    document.indexable !== true ||
    document.filename.toLowerCase().endsWith('.ppt')
  ) {
    return false
  }
  return (
    document.media_type === 'application/pdf' || document.media_type === SUPPORTED_PPTX_MEDIA_TYPE
  )
}

function documentKind(document: DocumentRecord): 'PDF' | 'PPTX' {
  return document.media_type === SUPPORTED_PPTX_MEDIA_TYPE ? 'PPTX' : 'PDF'
}

function noteBatchStorageKey(courseId: string): string {
  return `study-agent.note-batch:${courseId}`
}

function newNoteBatchCommandKey(): string {
  return `note-batch-create-${crypto.randomUUID()}`
}

function isMissingBatch(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.problem.status === 404 || error.problem.code === 'RESOURCE_NOT_FOUND')
  )
}

function batchStatusLabel(status: NoteBatchStatus): string {
  switch (status) {
    case 'queued':
      return '排队中'
    case 'running':
      return '运行中'
    case 'partial_success':
      return '部分成功'
    case 'succeeded':
      return '已完成'
    case 'failed':
      return '失败'
    case 'cancelling':
      return '取消中'
    case 'cancelled':
      return '已取消'
  }
}

function phaseLabel(phase: NoteGenerationPhase | null | undefined): string {
  switch (phase) {
    case 'validating_inputs':
      return '校验输入'
    case 'segmenting':
      return '切分资料'
    case 'retrieving':
      return '检索资料'
    case 'outlining':
      return '整理大纲'
    case 'generating':
      return '生成正文'
    case 'validating_output':
      return '校验输出'
    case 'saving':
      return '保存笔记'
    default:
      return '等待阶段更新'
  }
}

function itemStatusLabel(status: NoteItemSnapshot['status']): string {
  switch (status) {
    case 'queued':
      return '排队中'
    case 'leased':
      return '已领取'
    case 'running':
      return '运行中'
    case 'retry_wait':
      return '等待重试'
    case 'succeeded':
      return '已完成'
    case 'failed':
      return '失败'
    case 'cancelling':
      return '取消中'
    case 'cancelled':
      return '已取消'
  }
}

function batchTone(status: NoteBatchStatus): 'neutral' | 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'succeeded') return 'success'
  if (status === 'partial_success' || status === 'cancelling') return 'warning'
  if (status === 'failed' || status === 'cancelled') return 'danger'
  if (status === 'running') return 'info'
  return 'neutral'
}

function currentBatchItem(batch: NoteBatchSnapshot): NoteItemSnapshot | undefined {
  return (
    batch.items.find((item) => !TERMINAL_ITEM_STATUSES.has(item.status)) ??
    batch.items[batch.items.length - 1]
  )
}

interface CreateNoteDialogProps {
  documents: DocumentRecord[]
  documentsError: unknown
  documentsLoading: boolean
  error: unknown
  onClose: () => void
  onCreate: (input: MergedNoteBatchRequest) => void
  pending: boolean
}

function CreateNoteDialog({
  documents,
  documentsError,
  documentsLoading,
  error,
  onClose,
  onCreate,
  pending,
}: CreateNoteDialogProps) {
  const [section, setSection] = useState('')
  const [style, setStyle] = useState<NoteBatchStyle>('exam_focus')
  const [title, setTitle] = useState('')
  const [selectedIds, setSelectedIds] = useState<string[] | null>(null)
  const eligibleDocuments = useMemo(() => documents.filter(isNoteSourceDocument), [documents])
  const activeSelectedIds = (
    selectedIds ?? eligibleDocuments.map((document) => document.id)
  ).filter((id) => eligibleDocuments.some((document) => document.id === id))
  const sectionPath = section
    .split('/')
    .map((part) => part.trim())
    .filter(Boolean)

  const toggleDocument = (documentId: string) => {
    setSelectedIds((current) => {
      const next = new Set(current ?? eligibleDocuments.map((document) => document.id))
      if (next.has(documentId)) next.delete(documentId)
      else next.add(documentId)
      return Array.from(next)
    })
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (activeSelectedIds.length === 0 || pending) return
    const trimmedTitle = title.trim()
    onCreate({
      schema_version: '1.0',
      mode: 'merged',
      document_ids: activeSelectedIds,
      style,
      ...(sectionPath.length ? { section_path: sectionPath } : {}),
      ...(trimmedTitle ? { title: trimmedTitle } : {}),
    })
  }

  return (
    <Modal
      description="合并模式 · 当前课程"
      footer={
        <>
          <button className="button" onClick={onClose} type="button">
            取消
          </button>
          <button
            className="button button--primary"
            disabled={
              !activeSelectedIds.length || Boolean(documentsError) || documentsLoading || pending
            }
            form="create-note-form"
            type="submit"
          >
            {pending ? (
              <LoaderCircle aria-hidden="true" className="spin" size={16} />
            ) : (
              <FilePlus2 aria-hidden="true" size={16} />
            )}
            创建
          </button>
        </>
      }
      onClose={onClose}
      open
      title="新建笔记"
    >
      <form id="create-note-form" onSubmit={submit}>
        <fieldset
          className="note-batch-documents"
          disabled={Boolean(documentsError) || documentsLoading || pending}
        >
          <legend>资料</legend>
          {documentsLoading ? (
            <div className="loading-state loading-state--inline">
              <LoaderCircle aria-hidden="true" className="spin" size={18} />
              <span>读取可用资料</span>
            </div>
          ) : documentsError ? (
            <ErrorNotice error={documentsError} title="无法读取资料" />
          ) : eligibleDocuments.length ? (
            <>
              <div className="note-batch-documents__meta">
                <span>
                  {activeSelectedIds.length} / {eligibleDocuments.length} 已选择
                </span>
                <span className="muted">已就绪的 PDF/PPTX</span>
              </div>
              <div className="note-batch-documents__list">
                {eligibleDocuments.map((document) => {
                  const checked = activeSelectedIds.includes(document.id)
                  const isPptx = documentKind(document) === 'PPTX'
                  return (
                    <label className="note-batch-document" key={document.id}>
                      <input
                        aria-label={`选择 ${document.filename}`}
                        checked={checked}
                        onChange={() => toggleDocument(document.id)}
                        type="checkbox"
                      />
                      <span className="note-batch-document__check" aria-hidden="true">
                        {checked ? <Check size={14} /> : null}
                      </span>
                      {isPptx ? (
                        <Presentation aria-hidden="true" size={18} />
                      ) : (
                        <FileText aria-hidden="true" size={18} />
                      )}
                      <span className="note-batch-document__details">
                        <strong>{document.filename}</strong>
                        <small>
                          {documentKind(document)} · {document.page_count ?? '未知'} 页
                        </small>
                      </span>
                    </label>
                  )
                })}
              </div>
            </>
          ) : (
            <p className="muted">当前没有可用于生成笔记的资料。</p>
          )}
        </fieldset>
        <div className="note-batch-mode" aria-label="生成方式" role="group">
          <span className="field__label">生成方式</span>
          <span className="note-batch-mode__selected">
            <BookOpen aria-hidden="true" size={15} />
            合并为一篇
          </span>
        </div>
        <fieldset className="note-style-picker">
          <legend>笔记模板</legend>
          <div className="note-style-picker__options">
            {NOTE_STYLE_OPTIONS.map((option) => (
              <label className="note-style-option" key={option.value}>
                <input
                  checked={style === option.value}
                  disabled={pending}
                  name="note-style"
                  onChange={() => setStyle(option.value)}
                  type="radio"
                  value={option.value}
                />
                <span>
                  <strong>{option.label}</strong>
                  <small>{option.description}</small>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <label className="field" htmlFor="new-note-section">
          <span>章节路径（可选）</span>
          <input
            autoFocus
            id="new-note-section"
            maxLength={1000}
            onChange={(event) => setSection(event.target.value)}
            value={section}
          />
        </label>
        <label className="field" htmlFor="new-note-title">
          <span>标题（可选）</span>
          <input
            id="new-note-title"
            maxLength={255}
            onChange={(event) => setTitle(event.target.value)}
            value={title}
          />
        </label>
        {error ? <ErrorNotice error={error} title="新建笔记失败" /> : null}
      </form>
    </Modal>
  )
}

interface NoteEditorProps {
  note: NoteRecord
  providerReady: boolean
  onReload: () => Promise<NoteRecord | undefined>
  onSaved: (note: NoteRecord) => void
}

function NoteEditor({ note, providerReady, onReload, onSaved }: NoteEditorProps) {
  const [draft, setDraft] = useState(note.body_markdown)
  const [viewMode, setViewMode] = useState<'read' | 'edit'>('read')
  const [conflict, setConflict] = useState(false)
  const save = useMutation({
    mutationFn: () => studyApi.updateNote(note.id, draft, note.version),
    onSuccess: (updated) => {
      setConflict(false)
      setDraft(updated.body_markdown)
      setViewMode('read')
      onSaved(updated)
    },
    onError: (error) => {
      if (
        error instanceof ApiError &&
        (error.problem.status === 409 ||
          error.problem.status === 412 ||
          error.problem.code === 'VERSION_CONFLICT')
      ) {
        setConflict(true)
      }
    },
  })
  const reload = useMutation({
    mutationFn: onReload,
    onSuccess: (latest) => {
      if (latest) {
        setDraft(latest.body_markdown)
        onSaved(latest)
      }
      setConflict(false)
      save.reset()
    },
  })
  const regenerate = useMutation({
    mutationFn: () => studyApi.regenerateNote(note.id),
    onSuccess: (updated) => {
      setDraft(updated.body_markdown)
      setViewMode('read')
      onSaved(updated)
    },
  })
  const changed = draft !== note.body_markdown

  return (
    <div className="note-workspace__editor">
      <header className="note-editor__header">
        <div>
          <h3>{note.title}</h3>
          <span>
            版本 {note.version} · 生成 {note.generation}
          </span>
        </div>
        <div>
          <div className="note-mode-switch" aria-label="笔记视图" role="group">
            <button
              aria-pressed={viewMode === 'read'}
              className={`button button--small${viewMode === 'read' ? ' is-active' : ''}`}
              onClick={() => setViewMode('read')}
              type="button"
            >
              <BookOpen aria-hidden="true" size={15} />
              阅读
            </button>
            <button
              aria-pressed={viewMode === 'edit'}
              className={`button button--small${viewMode === 'edit' ? ' is-active' : ''}`}
              onClick={() => setViewMode('edit')}
              type="button"
            >
              <Pencil aria-hidden="true" size={15} />
              编辑
            </button>
          </div>
          <button
            className="button button--small"
            disabled={!providerReady || changed || regenerate.isPending}
            onClick={() => regenerate.mutate()}
            title={!providerReady ? 'Provider 不可用' : changed ? '请先保存当前修改' : undefined}
            type="button"
          >
            {regenerate.isPending ? (
              <LoaderCircle aria-hidden="true" className="spin" size={15} />
            ) : (
              <RefreshCw aria-hidden="true" size={15} />
            )}
            重新生成
          </button>
          {viewMode === 'edit' ? (
            <button
              className="button button--primary button--small"
              disabled={!changed || !draft.trim() || save.isPending}
              onClick={() => save.mutate()}
              type="button"
            >
              {save.isPending ? (
                <LoaderCircle aria-hidden="true" className="spin" size={15} />
              ) : (
                <Save aria-hidden="true" size={15} />
              )}
              保存
            </button>
          ) : null}
        </div>
      </header>
      {changed && viewMode === 'read' ? (
        <div className="note-draft-indicator" role="status">
          <Pencil aria-hidden="true" size={14} />
          有未保存修改
        </div>
      ) : null}
      {conflict ? (
        <div className="conflict-banner" role="alert">
          <AlertTriangle aria-hidden="true" size={18} />
          <div>
            <strong>笔记已在其他位置更新</strong>
            <p>当前草稿未覆盖服务器版本。</p>
          </div>
          <button
            className="button button--small"
            disabled={reload.isPending}
            onClick={() => reload.mutate()}
            type="button"
          >
            {reload.isPending ? (
              <LoaderCircle aria-hidden="true" className="spin" size={15} />
            ) : null}
            载入服务器版本
          </button>
        </div>
      ) : null}
      {save.isError && !conflict ? <ErrorNotice error={save.error} title="保存失败" /> : null}
      {reload.isError ? <ErrorNotice error={reload.error} title="无法载入服务器版本" /> : null}
      {regenerate.isError ? <ErrorNotice error={regenerate.error} title="重新生成失败" /> : null}
      {viewMode === 'edit' ? (
        <>
          <label className="sr-only" htmlFor={`note-body-${note.id}`}>
            笔记正文
          </label>
          <textarea
            className="note-editor"
            id={`note-body-${note.id}`}
            onChange={(event) => setDraft(event.target.value)}
            spellCheck="false"
            value={draft}
          />
        </>
      ) : (
        <article aria-label="笔记阅读视图" className="note-preview">
          {draft.trim() ? (
            <ReactMarkdown skipHtml>{draft}</ReactMarkdown>
          ) : (
            <p className="muted">暂无正文</p>
          )}
        </article>
      )}
    </div>
  )
}

function NoteBatchProgress({ batch }: { batch: NoteBatchSnapshot }) {
  const item = currentBatchItem(batch)
  const failureCode = item?.failure_code

  return (
    <section aria-label="笔记生成进度" aria-live="polite" className="note-batch-progress">
      <header>
        <div>
          <span className="section-kicker">Generation</span>
          <h3>笔记生成批次</h3>
        </div>
        <StatusBadge tone={batchTone(batch.status)}>
          {batchStatusLabel(batch.status)} · {batch.status}
        </StatusBadge>
      </header>
      <div className="note-batch-progress__facts">
        <div>
          <span>模板</span>
          <strong>{noteStyleLabel(batch.style)}</strong>
        </div>
        <div>
          <span>进度</span>
          <strong>
            {batch.completed_items} / {batch.total_items}
          </strong>
        </div>
        <div>
          <span>当前任务</span>
          <strong>{item ? `${itemStatusLabel(item.status)} · ${item.status}` : '等待任务'}</strong>
        </div>
        <div>
          <span>阶段</span>
          <strong>
            {item ? `${phaseLabel(item.phase)} · ${item.phase ?? 'pending'}` : '等待阶段更新'}
          </strong>
        </div>
        <div>
          <span>已用时</span>
          <strong>{item?.elapsed_seconds ?? 0} 秒</strong>
        </div>
      </div>
      {failureCode ? <p className="note-batch-progress__failure">失败代码：{failureCode}</p> : null}
    </section>
  )
}

function SourcesPanel({ note }: { note: NoteRecord }) {
  const hasUnavailableSource = note.sources.some((source) => !source.available || source.stale)

  return (
    <aside className="note-sources" aria-label="笔记来源">
      <header>
        <h3>来源</h3>
        <StatusBadge tone={hasUnavailableSource ? 'warning' : 'success'}>
          {note.sources.length} 条
        </StatusBadge>
      </header>
      {note.sources.length ? (
        <ol>
          {note.sources.map((source) => (
            <li key={source.id}>
              <div>
                <BookOpen aria-hidden="true" size={16} />
                <span>
                  <strong>{source.document_name}</strong>
                  <small>
                    {source.locator.kind === 'slide' ? '幻灯片' : '页'} {source.locator.ordinal}
                  </small>
                </span>
              </div>
              <blockquote>{source.quote}</blockquote>
              <span
                className={`source-state source-state--${!source.available ? 'unavailable' : source.stale ? 'stale' : 'active'}`}
              >
                {!source.available
                  ? `不可用${source.unavailable_reason ? ` · ${source.unavailable_reason}` : ''}`
                  : source.stale
                    ? '旧版本'
                    : '活动来源'}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">无来源</p>
      )}
    </aside>
  )
}

export function NotesPage() {
  const { courseId, capabilities } = useWorkspace()
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [createDialog, setCreateDialog] = useState({ courseId, open: false })
  const batchStorageKey = noteBatchStorageKey(courseId)
  const [activeBatch, setActiveBatch] = useState(() => ({
    courseId,
    id: localStorage.getItem(batchStorageKey),
  }))
  const [createdBatch, setCreatedBatch] = useState<NoteBatchSnapshot | null>(null)
  const completedBatchRef = useRef<string | null>(null)
  const createCommandKeyRef = useRef<string | null>(null)
  const createOpen = createDialog.courseId === courseId && createDialog.open
  const activeBatchId =
    activeBatch.courseId === courseId ? activeBatch.id : localStorage.getItem(batchStorageKey)
  const notesQuery = useQuery({
    queryKey: ['notes', courseId],
    queryFn: () => studyApi.listNotes(courseId),
  })
  const documentsQuery = useQuery({
    queryKey: ['documents', courseId],
    queryFn: () => studyApi.listDocuments(courseId),
    enabled: createOpen,
  })
  const batchQuery = useQuery({
    queryKey: ['note-batch', activeBatchId],
    queryFn: async () => {
      const requestedBatchId = activeBatchId ?? ''
      try {
        return await studyApi.getNoteBatch(requestedBatchId)
      } catch (error) {
        if (!isMissingBatch(error)) throw error
        if (localStorage.getItem(batchStorageKey) === requestedBatchId) {
          localStorage.removeItem(batchStorageKey)
        }
        setActiveBatch((current) =>
          current.courseId === courseId && current.id === requestedBatchId
            ? { courseId, id: null }
            : current,
        )
        return null
      }
    },
    enabled: activeBatchId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && TERMINAL_BATCH_STATUSES.includes(status) ? false : 250
    },
  })
  const notes = notesQuery.data ?? []
  const selected = notes.find((note) => note.id === selectedId) ?? notes[0]
  const providerReady = capabilities?.provider.status === 'available'
  const noteWorkflowReady =
    capabilities?.note_workflow.enabled === true &&
    capabilities?.note_workflow.generation.status === 'available'
  const createBatch = useMutation({
    mutationFn: (input: MergedNoteBatchRequest) => {
      const commandKey = createCommandKeyRef.current ?? newNoteBatchCommandKey()
      createCommandKeyRef.current = commandKey
      return studyApi.createNoteBatch(courseId, input, commandKey)
    },
    onSuccess: (snapshot) => {
      createCommandKeyRef.current = null
      completedBatchRef.current = null
      queryClient.setQueryData(['note-batch', snapshot.id], snapshot)
      localStorage.setItem(batchStorageKey, snapshot.id)
      setCreatedBatch(snapshot)
      setActiveBatch({ courseId, id: snapshot.id })
      setCreateDialog({ courseId, open: false })
    },
  })
  const batchSnapshot =
    batchQuery.data ??
    (createdBatch?.course_id === courseId && createdBatch.id === activeBatchId
      ? createdBatch
      : null)
  const batchInProgress =
    activeBatchId !== null &&
    (!batchSnapshot || !TERMINAL_BATCH_STATUSES.includes(batchSnapshot.status))

  useEffect(() => {
    if (
      !batchSnapshot ||
      !['succeeded', 'partial_success'].includes(batchSnapshot.status) ||
      completedBatchRef.current === batchSnapshot.id
    ) {
      return
    }
    completedBatchRef.current = batchSnapshot.id
    const generatedNoteIds = batchSnapshot.items.flatMap((item) =>
      item.status === 'succeeded' && item.note_id ? [item.note_id] : [],
    )
    const notesQueryKey = ['notes', courseId] as const
    void queryClient
      .invalidateQueries({ queryKey: notesQueryKey, refetchType: 'none' })
      .then(() =>
        queryClient.fetchQuery<NoteRecord[]>({
          queryKey: notesQueryKey,
          queryFn: () => studyApi.listNotes(courseId),
          staleTime: 0,
        }),
      )
      .then((latestNotes) => {
        const generatedNoteId = generatedNoteIds.find((id) =>
          latestNotes.some((note) => note.id === id),
        )
        if (generatedNoteId) setSelectedId(generatedNoteId)
      })
      .catch(() => {
        if (completedBatchRef.current === batchSnapshot.id) completedBatchRef.current = null
      })
  }, [batchSnapshot, courseId, queryClient])

  const replaceNote = (updated: NoteRecord) => {
    queryClient.setQueryData<NoteRecord[]>(['notes', courseId], (current = []) =>
      current.map((note) => (note.id === updated.id ? updated : note)),
    )
  }

  return (
    <div className="page page--notes">
      <PageHeader
        actions={
          <button
            className="button button--primary"
            disabled={!noteWorkflowReady || batchInProgress}
            onClick={() => {
              createBatch.reset()
              createCommandKeyRef.current = newNoteBatchCommandKey()
              setCreateDialog({ courseId, open: true })
            }}
            title={
              !noteWorkflowReady
                ? '笔记生成工作流不可用'
                : batchInProgress
                  ? '当前笔记仍在生成'
                  : undefined
            }
            type="button"
          >
            <FilePlus2 aria-hidden="true" size={17} />
            新建笔记
          </button>
        }
        kicker="Notes"
        meta={`${notes.length} 篇笔记`}
        title="章节笔记"
      />
      {batchSnapshot ? <NoteBatchProgress batch={batchSnapshot} /> : null}
      {batchQuery.isError ? (
        <ErrorNotice
          error={batchQuery.error}
          onRetry={() => void batchQuery.refetch()}
          title="无法更新笔记生成进度"
        />
      ) : null}
      {notesQuery.isError ? (
        <ErrorNotice
          error={notesQuery.error}
          onRetry={() => void notesQuery.refetch()}
          title="无法读取笔记"
        />
      ) : notesQuery.isLoading ? (
        <section className="loading-state">
          <LoaderCircle aria-hidden="true" className="spin" size={20} />
          <span>加载笔记</span>
        </section>
      ) : selected ? (
        <div className="note-workspace">
          <nav aria-label="笔记章节" className="note-tree">
            <h3>章节</h3>
            {notes.map((note) => (
              <button
                aria-current={note.id === selected.id ? 'page' : undefined}
                key={note.id}
                onClick={() => setSelectedId(note.id)}
                type="button"
              >
                <FileClock aria-hidden="true" size={16} />
                <span>
                  <strong>{note.title}</strong>
                  <small>{note.section_path.join(' / ') || '未分类'}</small>
                </span>
              </button>
            ))}
          </nav>
          <NoteEditor
            key={`${selected.id}-${selected.version}`}
            note={selected}
            onReload={async () => {
              const result = await notesQuery.refetch({ throwOnError: true })
              return result.data?.find((note) => note.id === selected.id)
            }}
            onSaved={replaceNote}
            providerReady={providerReady}
          />
          <SourcesPanel note={selected} />
        </div>
      ) : (
        <section className="empty-state">
          <BookOpen aria-hidden="true" size={26} />
          <h3>暂无笔记</h3>
        </section>
      )}
      {createOpen ? (
        <CreateNoteDialog
          documents={documentsQuery.data ?? []}
          documentsError={documentsQuery.error}
          documentsLoading={documentsQuery.isLoading}
          error={createBatch.error}
          onClose={() => {
            if (!createBatch.isPending) {
              createCommandKeyRef.current = null
              createBatch.reset()
              setCreateDialog({ courseId, open: false })
            }
          }}
          onCreate={(input) => createBatch.mutate(input)}
          pending={createBatch.isPending}
        />
      ) : null}
    </div>
  )
}
