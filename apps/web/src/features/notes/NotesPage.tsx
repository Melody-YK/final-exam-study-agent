import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  BookOpen,
  Check,
  Eye,
  FileClock,
  FilePlus2,
  FileText,
  LoaderCircle,
  Pencil,
  RefreshCw,
  Save,
  Search,
  X,
} from 'lucide-react'
import ReactMarkdown, { type Components } from 'react-markdown'
import React, { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import type { Element as HastElement } from 'hast'

import { ApiError, studyApi } from '../../api/client'
import type {
  DocumentRecord,
  MergedNoteBatchRequest,
  NoteBatchSnapshot,
  NoteBatchStatus,
  NoteBatchStyle,
  NoteGenerationEventData,
  NoteGenerationPhase,
  NoteItemSnapshot,
  NoteRecord,
  SourcePreview,
} from '../../api/types'
import { useWorkspace } from '../../app/WorkspaceContext'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { Modal } from '../../components/ui/Modal'
import { PageHeader } from '../../components/ui/PageHeader'
import { StatusBadge } from '../../components/ui/StatusBadge'
import { SourceViewer } from '../source-viewer/SourceViewer'
import { formatSourceLocator } from '../source-viewer/sourceLocator'

const MARKDOWN_MEDIA_TYPE = 'text/markdown'

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
  density: string
  intendedUse: string
  structure: readonly [string, string, string]
}> = [
  {
    value: 'exam_focus',
    label: '考前速记',
    density: '最短 · 最多 12 条',
    intendedUse: '定义、条件、区别和公式优先',
    structure: ['资料名称', '• 高频定义或公式', '• 关键条件与区别'],
  },
  {
    value: 'outline',
    label: '结构提纲',
    density: '中等 · 最多 30 条',
    intendedUse: '按资料和来源位置快速梳理层级',
    structure: ['1. 资料名称', '1.1 来源位置', '1. 关键知识点'],
  },
  {
    value: 'complete',
    label: '完整讲义',
    density: '最长 · 最多 40 条 / 12,000 字符',
    intendedUse: '按来源顺序保留完整上下文',
    structure: ['资料名称', '来源位置', '来源正文段落'],
  },
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
    /\.pptx?$/.test(document.filename.toLowerCase())
  ) {
    return false
  }
  return document.media_type === 'application/pdf' || document.media_type === MARKDOWN_MEDIA_TYPE
}

function documentKind(document: DocumentRecord): 'PDF' | 'Markdown' {
  return document.media_type === MARKDOWN_MEDIA_TYPE ? 'Markdown' : 'PDF'
}

function noteBatchStorageKey(courseId: string): string {
  return `study-agent.note-batch:${courseId}`
}

function newNoteBatchCommandKey(): string {
  return `note-batch-create-${crypto.randomUUID()}`
}

function newNoteRegenerationCommandKey(): string {
  return `note-batch-regenerate-${crypto.randomUUID()}`
}

function noteRegenerationTargetKey(noteId: string, version: number): string {
  return `${noteId}:${version}`
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

function markdownText(children: React.ReactNode): string {
  return React.Children.toArray(children)
    .map((child) => {
      if (typeof child === 'string' || typeof child === 'number') return String(child)
      if (React.isValidElement<{ children?: React.ReactNode }>(child)) {
        return markdownText(child.props.children)
      }
      return ''
    })
    .join('')
}

function normalizeMarkdownText(value: string): string {
  return value.replace(/\s+/g, ' ').trim()
}

function stripLegacySourceMapping(body: string): string {
  return body.replace(/^#{2,6}\s+来源对应\s*$[\s\S]*/m, '').trim()
}

interface MarkdownHeading {
  id: string
  level: number
  line: number
  text: string
}

function headingText(value: string): string {
  return value
    .replace(/\s+#+\s*$/, '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[`*_~]/g, '')
    .trim()
}

function headingAnchorId(text: string, line: number): string {
  const slug = text
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)
  return `note-heading-${line}-${slug || 'section'}`
}

function extractMarkdownHeadings(body: string): MarkdownHeading[] {
  return stripLegacySourceMapping(body)
    .split(/\r?\n/)
    .flatMap((line, index) => {
      const match = /^(#{1,6})\s+(.+?)\s*$/.exec(line)
      if (!match) return []
      const rawLevel = match[1]
      const rawText = match[2]
      if (!rawLevel || !rawText) return []
      const text = headingText(rawText)
      if (!text) return []
      const lineNumber = index + 1
      return [{
        id: headingAnchorId(text, lineNumber),
        level: rawLevel.length,
        line: lineNumber,
        text,
      }]
    })
}

interface NoteSectionNode {
  children: NoteSectionNode[]
  label: string
  notes: NoteRecord[]
  path: string[]
}

function noteSectionPath(note: NoteRecord): string[] {
  return note.section_path.length ? note.section_path : ['未分类']
}

function buildNoteSectionTree(notes: NoteRecord[]): NoteSectionNode[] {
  const roots: NoteSectionNode[] = []
  for (const note of notes) {
    let level = roots
    const path: string[] = []
    const sectionPath = noteSectionPath(note)
    for (const [index, label] of sectionPath.entries()) {
      path.push(label)
      let node = level.find((candidate) => candidate.label === label)
      if (!node) {
        node = { children: [], label, notes: [], path: [...path] }
        level.push(node)
      }
      if (index === sectionPath.length - 1) {
        node.notes.push(note)
      }
      level = node.children
    }
  }
  return roots
}

function noteMatchesSearch(note: NoteRecord, query: string): boolean {
  const haystack = [note.title, note.section_path.join(' / '), note.body_markdown]
    .join('\n')
    .toLocaleLowerCase()
  return haystack.includes(query.toLocaleLowerCase())
}

function matchingKnowledgePoint(
  text: string,
  points: NoteRecord['knowledge_points'],
) {
  const normalized = normalizeMarkdownText(text)
  if (!normalized) return undefined
  return points.find((point) => {
    const pointText = normalizeMarkdownText(point.text)
    return (
      normalized === pointText ||
      normalized.startsWith(`${pointText} (来源:`) ||
      normalized.startsWith(`${pointText}（来源：`)
    )
  })
}

function NoteMarkdown({ body, note }: { body: string; note: NoteRecord }) {
  const [preview, setPreview] = useState<SourcePreview | null>(null)
  const previewMutation = useMutation({
    mutationFn: (sourceId: string) => studyApi.getNoteSourcePreview(note.id, sourceId),
    onSuccess: setPreview,
  })
  const points = note.knowledge_points ?? []
  const sourceById = useMemo(
    () => new Map(note.sources.map((source) => [source.id, source])),
    [note.sources],
  )
  const sourceLinks = (point: NoteRecord['knowledge_points'][number]) => (
    <span aria-label="知识点来源" className="note-inline-sources">
      {point.source_ids.map((sourceId) => {
        const source = sourceById.get(sourceId)
        if (!source) return null
        return source.available && !source.stale ? (
          <button
            className="button button--small note-source-open"
            disabled={previewMutation.isPending}
            key={source.id}
            onClick={() => previewMutation.mutate(source.id)}
            type="button"
          >
            {previewMutation.isPending && previewMutation.variables === source.id ? (
              <LoaderCircle aria-hidden="true" className="spin" size={14} />
            ) : (
              <Eye aria-hidden="true" size={14} />
            )}
            查看原文 · {source.document_name} · {formatSourceLocator(source.locator)}
          </button>
        ) : (
          <span className="source-state source-state--unavailable" key={source.id}>
            来源不可用
          </span>
        )
      })}
    </span>
  )

  const renderBlock = (Tag: 'p' | 'li') => ({ children }: { children?: React.ReactNode }) => {
    const renderedText = markdownText(children)
    const point = matchingKnowledgePoint(renderedText, points)
    return (
      <Tag>
        {children}
        {point ? sourceLinks(point) : null}
      </Tag>
    )
  }

  const renderHeading =
    (Tag: 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6') =>
    ({ children, node }: { children?: React.ReactNode; node?: HastElement }) => {
      const text = headingText(markdownText(children))
      const line = node?.position?.start.line ?? 0
      return (
        <Tag id={headingAnchorId(text, line)}>
          {children}
        </Tag>
      )
    }

  const components: Components = {
    h1: renderHeading('h1'),
    h2: renderHeading('h2'),
    h3: renderHeading('h3'),
    h4: renderHeading('h4'),
    h5: renderHeading('h5'),
    h6: renderHeading('h6'),
    li: renderBlock('li'),
    p: renderBlock('p'),
  }

  return (
    <>
      <ReactMarkdown components={components} skipHtml>
        {stripLegacySourceMapping(body)}
      </ReactMarkdown>
      {previewMutation.isError ? (
        <div className="note-inline-source-error">
          <ErrorNotice error={previewMutation.error} title="原文不可用" />
        </div>
      ) : null}
      <SourceViewer onClose={() => setPreview(null)} source={preview} />
    </>
  )
}

function NoteSwitcherTree({
  nodes,
  onSelect,
  selectedId,
}: {
  nodes: NoteSectionNode[]
  onSelect: (noteId: string) => void
  selectedId: string
}) {
  return (
    <ul className="note-switcher__tree">
      {nodes.map((node) => (
        <li key={node.path.join('\u0000') || node.label}>
          <div
            className="note-section-node"
            style={{ paddingLeft: `${8 + (node.path.length - 1) * 12}px` }}
          >
            {node.label}
          </div>
          {node.notes.length ? (
            <ul className="note-switcher__notes">
              {node.notes.map((note) => (
                <li key={note.id}>
                  <button
                    aria-label={note.title}
                    aria-current={note.id === selectedId ? 'page' : undefined}
                    onClick={() => onSelect(note.id)}
                    type="button"
                  >
                    <FileClock aria-hidden="true" size={16} />
                    <span>
                      <strong>{note.title}</strong>
                      <small>{noteSectionPath(note).join(' / ')}</small>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          {node.children.length ? (
            <NoteSwitcherTree nodes={node.children} onSelect={onSelect} selectedId={selectedId} />
          ) : null}
        </li>
      ))}
    </ul>
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
                <span className="muted">已就绪的 PDF/Markdown</span>
              </div>
              <div className="note-batch-documents__list">
                {eligibleDocuments.map((document) => {
                  const checked = activeSelectedIds.includes(document.id)
                  const kind = documentKind(document)
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
                      <FileText aria-hidden="true" size={18} />
                      <span className="note-batch-document__details">
                        <strong>{document.filename}</strong>
                        <small>
                          {kind} · {document.page_count ?? '未知'}{' '}
                          {kind === 'Markdown' ? '个章节' : '页'}
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
                <span className="note-style-option__details">
                  <span className="note-style-option__heading">
                    <strong>{option.label}</strong>
                    <span className="note-style-option__density">{option.density}</span>
                  </span>
                  <small>{option.intendedUse}</small>
                  <span
                    aria-current={style === option.value ? 'true' : undefined}
                    aria-label={`${option.label}结构示例`}
                    className="note-style-option__sample"
                  >
                    {option.structure.map((line) => (
                      <code key={line}>{line}</code>
                    ))}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="field">
          <label htmlFor="new-note-section">
            <span>章节路径（可选）</span>
            <input
              autoFocus
              id="new-note-section"
              maxLength={1000}
              onChange={(event) => setSection(event.target.value)}
              placeholder="例如：第一章 / 进程管理 / 调度"
              value={section}
            />
          </label>
          <small className="field__hint" id="new-note-section-hint">
            保存后会在笔记切换区按层级归类
          </small>
        </div>
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
  batchInProgress: boolean
  note: NoteRecord
  noteWorkflowReady: boolean
  onRegenerate: () => void
  providerReady: boolean
  regenerationError: unknown
  regenerationPending: boolean
  regenerating: boolean
  onReload: () => Promise<NoteRecord | undefined>
  onSaved: (note: NoteRecord) => void
}

function NoteEditor({
  batchInProgress,
  note,
  noteWorkflowReady,
  onRegenerate,
  providerReady,
  regenerationError,
  regenerationPending,
  regenerating,
  onReload,
  onSaved,
}: NoteEditorProps) {
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
  const changed = draft !== note.body_markdown
  const workflowGenerated = note.origin_batch_id !== null
  const regenerationReady = workflowGenerated ? noteWorkflowReady : providerReady
  const regenerationUnavailableTitle = workflowGenerated
    ? '笔记生成工作流不可用'
    : 'Provider 不可用'

  return (
    <div className="note-workspace__editor">
      <header className="note-editor__header">
        <div>
          <h3>{note.title}</h3>
          <span>
            版本 {note.version} · 生成 {note.generation} ·{' '}
            {note.generated_by_model ? 'DeepSeek 生成' : '本地摘录演示'}
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
            disabled={
              !regenerationReady || changed || batchInProgress || regenerationPending
            }
            onClick={onRegenerate}
            title={
              !regenerationReady
                ? regenerationUnavailableTitle
                : changed
                  ? '请先保存当前修改'
                  : batchInProgress
                    ? '当前笔记批次仍在生成'
                    : regenerationPending
                      ? '正在启动重新生成'
                      : undefined
            }
            type="button"
          >
            {regenerating ? (
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
      {regenerationError ? <ErrorNotice error={regenerationError} title="重新生成失败" /> : null}
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
            <NoteMarkdown body={draft} note={note} />
          ) : (
            <p className="muted">暂无正文</p>
          )}
        </article>
      )}
    </div>
  )
}

function NoteBatchProgress({
  batch,
  previewMarkdown,
}: {
  batch: NoteBatchSnapshot
  previewMarkdown?: string
}) {
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
      {previewMarkdown?.trim() ? (
        <article aria-label="笔记实时预览" className="note-generation-preview">
          <header>
            <strong>实时预览</strong>
            <span>生成中，尚未保存</span>
          </header>
          <ReactMarkdown skipHtml>{previewMarkdown}</ReactMarkdown>
        </article>
      ) : null}
    </section>
  )
}

export function NotesPage() {
  const { courseId, capabilities } = useWorkspace()
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [noteSearch, setNoteSearch] = useState('')
  const [createDialog, setCreateDialog] = useState({ courseId, open: false })
  const batchStorageKey = noteBatchStorageKey(courseId)
  const [activeBatch, setActiveBatch] = useState(() => ({
    courseId,
    id: localStorage.getItem(batchStorageKey),
  }))
  const [createdBatch, setCreatedBatch] = useState<NoteBatchSnapshot | null>(null)
  const [previewMarkdown, setPreviewMarkdown] = useState('')
  const completedBatchRef = useRef<string | null>(null)
  const createCommandKeyRef = useRef<string | null>(null)
  const regenerationCommandKeysRef = useRef(new Map<string, string>())
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
  const notes = useMemo(() => notesQuery.data ?? [], [notesQuery.data])
  const selected = notes.find((note) => note.id === selectedId) ?? notes[0]
  const normalizedNoteSearch = noteSearch.trim()
  const visibleNotes = useMemo(
    () =>
      normalizedNoteSearch
        ? notes.filter((note) => noteMatchesSearch(note, normalizedNoteSearch))
        : notes,
    [normalizedNoteSearch, notes],
  )
  const noteSectionTree = useMemo(() => buildNoteSectionTree(visibleNotes), [visibleNotes])
  const providerReady = capabilities?.provider.status === 'available'
  const noteWorkflowReady =
    capabilities?.note_workflow.enabled === true &&
    capabilities?.note_workflow.generation.status === 'available'
  const replaceNote = (updated: NoteRecord) => {
    queryClient.setQueryData<NoteRecord[]>(['notes', courseId], (current = []) =>
      current.map((note) => (note.id === updated.id ? updated : note)),
    )
  }
  const activateBatch = (snapshot: NoteBatchSnapshot) => {
    completedBatchRef.current = null
    setPreviewMarkdown('')
    queryClient.setQueryData(['note-batch', snapshot.id], snapshot)
    localStorage.setItem(batchStorageKey, snapshot.id)
    setCreatedBatch(snapshot)
    setActiveBatch({ courseId, id: snapshot.id })
  }
  const createBatch = useMutation({
    mutationFn: (input: MergedNoteBatchRequest) => {
      const commandKey = createCommandKeyRef.current ?? newNoteBatchCommandKey()
      createCommandKeyRef.current = commandKey
      return studyApi.createNoteBatch(courseId, input, commandKey)
    },
    onSuccess: (snapshot) => {
      createCommandKeyRef.current = null
      activateBatch(snapshot)
      setCreateDialog({ courseId, open: false })
    },
  })
  const regenerateBatch = useMutation({
    mutationFn: ({ noteId, version }: { noteId: string; version: number }) => {
      const targetKey = noteRegenerationTargetKey(noteId, version)
      const commandKey =
        regenerationCommandKeysRef.current.get(targetKey) ?? newNoteRegenerationCommandKey()
      regenerationCommandKeysRef.current.set(targetKey, commandKey)
      return studyApi.createNoteRegenerationBatch(noteId, version, commandKey)
    },
    onSuccess: (snapshot, target) => {
      regenerationCommandKeysRef.current.delete(
        noteRegenerationTargetKey(target.noteId, target.version),
      )
      activateBatch(snapshot)
    },
  })
  const regenerateLegacy = useMutation({
    mutationFn: (noteId: string) => studyApi.regenerateNote(noteId),
    onSuccess: replaceNote,
  })
  const batchSnapshot =
    batchQuery.data ??
    (createdBatch?.course_id === courseId && createdBatch.id === activeBatchId
      ? createdBatch
      : null)
  const batchInProgress =
    activeBatchId !== null &&
    (!batchSnapshot || !TERMINAL_BATCH_STATUSES.includes(batchSnapshot.status))
  const selectedHeadings = selected ? extractMarkdownHeadings(selected.body_markdown) : []

  useEffect(() => {
    if (!activeBatchId || !batchInProgress) {
      return
    }
    return studyApi.subscribe<NoteGenerationEventData>(
      `/note-batches/${encodeURIComponent(activeBatchId)}/events`,
      (event) => {
        if (event.event_type === 'note.preview.delta' && event.data.delta) {
          setPreviewMarkdown((current) => current + event.data.delta)
        }
      },
    )
  }, [activeBatchId, batchInProgress])

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

  const regenerationPending = regenerateBatch.isPending || regenerateLegacy.isPending

  return (
    <div className="page page--notes">
      <PageHeader
        actions={
          <button
            className="button button--primary"
            disabled={!noteWorkflowReady || batchInProgress || regenerateBatch.isPending}
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
                  : regenerateBatch.isPending
                    ? '正在启动重新生成'
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
        title="学习笔记"
      />
      {batchSnapshot ? (
        <NoteBatchProgress
          batch={batchSnapshot}
          previewMarkdown={batchInProgress ? previewMarkdown : undefined}
        />
      ) : null}
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
          <aside aria-label="笔记导航" className="note-tree">
            <section aria-label="切换笔记" className="note-switcher">
              <h3>切换笔记</h3>
              <div className="note-search">
                <Search aria-hidden="true" size={15} />
                <label className="sr-only" htmlFor="note-search-input">
                  搜索笔记
                </label>
                <input
                  id="note-search-input"
                  onChange={(event) => setNoteSearch(event.target.value)}
                  placeholder="搜索标题、路径或正文"
                  type="search"
                  value={noteSearch}
                />
                {noteSearch ? (
                  <button
                    aria-label="清除笔记搜索"
                    className="note-search__clear"
                    onClick={() => setNoteSearch('')}
                    title="清除搜索"
                    type="button"
                  >
                    <X aria-hidden="true" size={14} />
                  </button>
                ) : null}
              </div>
              {visibleNotes.length ? (
                <NoteSwitcherTree
                  nodes={noteSectionTree}
                  onSelect={setSelectedId}
                  selectedId={selected.id}
                />
              ) : (
                <p className="muted note-search__empty">没有匹配的笔记</p>
              )}
            </section>
            <nav aria-label="正文目录" className="note-outline">
              <h3>正文目录</h3>
              {selectedHeadings.length ? (
                <ol>
                  {selectedHeadings.map((heading) => (
                    <li key={heading.id}>
                      <a
                        href={`#${heading.id}`}
                        onClick={(event) => {
                          event.preventDefault()
                          document.getElementById(heading.id)?.scrollIntoView({
                            behavior: 'smooth',
                            block: 'start',
                          })
                        }}
                        style={{ paddingLeft: `${8 + (heading.level - 1) * 12}px` }}
                      >
                        {heading.text}
                      </a>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="muted">正文暂无标题</p>
              )}
            </nav>
          </aside>
          <NoteEditor
            batchInProgress={batchInProgress}
            key={`${selected.id}-${selected.version}`}
            note={selected}
            noteWorkflowReady={noteWorkflowReady}
            onRegenerate={() => {
              if (selected.origin_batch_id !== null) {
                regenerateBatch.mutate({ noteId: selected.id, version: selected.version })
              } else {
                regenerateLegacy.mutate(selected.id)
              }
            }}
            onReload={async () => {
              const result = await notesQuery.refetch({ throwOnError: true })
              return result.data?.find((note) => note.id === selected.id)
            }}
            onSaved={replaceNote}
            providerReady={providerReady}
            regenerationError={
              selected.origin_batch_id !== null &&
              regenerateBatch.variables?.noteId === selected.id
                ? regenerateBatch.error
                : selected.origin_batch_id === null && regenerateLegacy.variables === selected.id
                  ? regenerateLegacy.error
                  : null
            }
            regenerationPending={regenerationPending || createBatch.isPending}
            regenerating={
              (selected.origin_batch_id !== null &&
                regenerateBatch.isPending &&
                regenerateBatch.variables?.noteId === selected.id) ||
              (selected.origin_batch_id === null &&
                regenerateLegacy.isPending &&
                regenerateLegacy.variables === selected.id)
            }
          />
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
