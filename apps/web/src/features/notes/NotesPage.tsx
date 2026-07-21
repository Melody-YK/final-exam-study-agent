import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  BookOpen,
  FileClock,
  FilePlus2,
  LoaderCircle,
  RefreshCw,
  Save,
} from 'lucide-react'
import { useState, type FormEvent } from 'react'

import { ApiError, studyApi } from '../../api/client'
import type { NoteRecord } from '../../api/types'
import { useWorkspace } from '../../app/WorkspaceContext'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { Modal } from '../../components/ui/Modal'
import { PageHeader } from '../../components/ui/PageHeader'
import { StatusBadge } from '../../components/ui/StatusBadge'

interface CreateNoteDialogProps {
  defaultSectionPath: string[]
  error: unknown
  onClose: () => void
  onCreate: (input: { sectionPath: string[]; title: string }) => void
  pending: boolean
}

function CreateNoteDialog({
  defaultSectionPath,
  error,
  onClose,
  onCreate,
  pending,
}: CreateNoteDialogProps) {
  const [section, setSection] = useState(defaultSectionPath.join(' / ') || '未分类')
  const [title, setTitle] = useState('')
  const sectionPath = section
    .split('/')
    .map((part) => part.trim())
    .filter(Boolean)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!title.trim() || sectionPath.length === 0 || pending) return
    onCreate({ sectionPath, title: title.trim() })
  }

  return (
    <Modal
      footer={
        <>
          <button className="button" onClick={onClose} type="button">
            取消
          </button>
          <button
            className="button button--primary"
            disabled={!title.trim() || sectionPath.length === 0 || pending}
            form="create-note-form"
            type="submit"
          >
            {pending ? <LoaderCircle aria-hidden="true" className="spin" size={16} /> : <FilePlus2 aria-hidden="true" size={16} />}
            创建
          </button>
        </>
      }
      onClose={onClose}
      open
      title="新建笔记"
    >
      <form id="create-note-form" onSubmit={submit}>
        <label className="field" htmlFor="new-note-section">
          <span>章节路径</span>
          <input
            autoFocus
            id="new-note-section"
            maxLength={1000}
            onChange={(event) => setSection(event.target.value)}
            value={section}
          />
        </label>
        <label className="field" htmlFor="new-note-title">
          <span>标题</span>
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
  const [conflict, setConflict] = useState(false)
  const save = useMutation({
    mutationFn: () => studyApi.updateNote(note.id, draft, note.version),
    onSuccess: (updated) => {
      setConflict(false)
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
      if (latest) onSaved(latest)
      setConflict(false)
      save.reset()
    },
  })
  const regenerate = useMutation({
    mutationFn: () => studyApi.regenerateNote(note.id),
    onSuccess: onSaved,
  })
  const changed = draft !== note.body_markdown

  return (
    <div className="note-workspace__editor">
      <header className="note-editor__header">
        <div>
          <h3>{note.title}</h3>
          <span>版本 {note.version} · 生成 {note.generation}</span>
        </div>
        <div>
          <button
            className="button button--small"
            disabled={!providerReady || changed || regenerate.isPending}
            onClick={() => regenerate.mutate()}
            title={!providerReady ? 'Provider 不可用' : changed ? '请先保存当前修改' : undefined}
            type="button"
          >
            {regenerate.isPending ? <LoaderCircle aria-hidden="true" className="spin" size={15} /> : <RefreshCw aria-hidden="true" size={15} />}
            重新生成
          </button>
          <button
            className="button button--primary button--small"
            disabled={!changed || !draft.trim() || save.isPending}
            onClick={() => save.mutate()}
            type="button"
          >
            {save.isPending ? <LoaderCircle aria-hidden="true" className="spin" size={15} /> : <Save aria-hidden="true" size={15} />}
            保存
          </button>
        </div>
      </header>
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
            {reload.isPending ? <LoaderCircle aria-hidden="true" className="spin" size={15} /> : null}
            载入服务器版本
          </button>
        </div>
      ) : null}
      {save.isError && !conflict ? <ErrorNotice error={save.error} title="保存失败" /> : null}
      {reload.isError ? <ErrorNotice error={reload.error} title="无法载入服务器版本" /> : null}
      {regenerate.isError ? <ErrorNotice error={regenerate.error} title="重新生成失败" /> : null}
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
    </div>
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
                  <small>{source.locator.kind === 'slide' ? '幻灯片' : '页'} {source.locator.ordinal}</small>
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
  const [createOpen, setCreateOpen] = useState(false)
  const notesQuery = useQuery({
    queryKey: ['notes', courseId],
    queryFn: () => studyApi.listNotes(courseId),
  })
  const notes = notesQuery.data ?? []
  const selected = notes.find((note) => note.id === selectedId) ?? notes[0]
  const providerReady = capabilities?.provider.status === 'available'
  const createNote = useMutation({
    mutationFn: (input: { sectionPath: string[]; title: string }) =>
      studyApi.createNote(courseId, input.sectionPath, input.title),
    onSuccess: (note) => {
      queryClient.setQueryData<NoteRecord[]>(['notes', courseId], (current = []) => [
        ...current,
        note,
      ])
      setSelectedId(note.id)
      setCreateOpen(false)
    },
  })

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
            disabled={!providerReady}
            onClick={() => {
              createNote.reset()
              setCreateOpen(true)
            }}
            title={!providerReady ? 'Provider 不可用' : undefined}
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
      {notesQuery.isError ? (
        <ErrorNotice error={notesQuery.error} onRetry={() => void notesQuery.refetch()} title="无法读取笔记" />
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
          defaultSectionPath={selected?.section_path ?? ['未分类']}
          error={createNote.error}
          onClose={() => {
            if (!createNote.isPending) setCreateOpen(false)
          }}
          onCreate={(input) => createNote.mutate(input)}
          pending={createNote.isPending}
        />
      ) : null}
    </div>
  )
}
