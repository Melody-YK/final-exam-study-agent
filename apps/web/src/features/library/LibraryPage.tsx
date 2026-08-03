import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BookOpen,
  CircleAlert,
  CheckCircle2,
  FilePlus2,
  LoaderCircle,
  MessageSquareText,
  Network,
  Radio,
  Trash2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'

import { studyApi } from '../../api/client'
import type { DocumentRecord } from '../../api/types'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { Modal } from '../../components/ui/Modal'
import { PageHeader } from '../../components/ui/PageHeader'
import { CapabilityBanner } from '../status/CapabilityBanner'
import { useWorkspace } from '../../app/WorkspaceContext'
import { DocumentTable } from './DocumentTable'
import { UploadDialog } from './UploadDialog'
import { useJobEvents, type JobTerminalEvent } from './useJobEvents'
import './library-actions.css'

const MARKDOWN_MEDIA_TYPE = 'text/markdown'
const FAILURE_STATUSES = new Set(['partial_failed', 'failed', 'retry_wait'])
const ACTIVE_DOCUMENT_STATUSES = new Set([
  'uploading',
  'queued',
  'leased',
  'processing',
  'parsed_index_blocked',
  'indexing',
])

type ReadinessBucket = 'ready' | 'review' | 'preparing' | 'attention'

const READINESS_LABELS: ReadonlyArray<{
  bucket: ReadinessBucket
  label: string
}> = [
  { bucket: 'ready', label: '可学习' },
  { bucket: 'review', label: '待审核' },
  { bucket: 'preparing', label: '准备中' },
  { bucket: 'attention', label: '需要处理' },
]

function isStudyReadyDocument(document: DocumentRecord): boolean {
  return (
    document.status === 'ready' &&
    document.review_status === 'approved' &&
    document.active_revision_id !== null &&
    document.active_revision_id !== undefined &&
    document.indexable === true
  )
}

function isNoteReadyDocument(document: DocumentRecord): boolean {
  return (
    isStudyReadyDocument(document) &&
    document.corpus_role === 'corpus' &&
    !/\.pptx?$/.test(document.filename.toLowerCase()) &&
    (document.media_type === 'application/pdf' || document.media_type === MARKDOWN_MEDIA_TYPE)
  )
}

function readinessBucket(document: DocumentRecord): ReadinessBucket {
  if (document.review_status === 'pending') return 'review'
  if (document.review_status === 'rejected') return 'attention'
  if (isStudyReadyDocument(document)) return 'ready'
  if (FAILURE_STATUSES.has(document.status)) return 'attention'
  return 'preparing'
}

interface ReadyStudyActionsProps {
  documents: DocumentRecord[]
  noteWorkflowAvailable: boolean
  providerAvailable: boolean
}

function ReadyStudyActions({
  documents,
  noteWorkflowAvailable,
  providerAvailable,
}: ReadyStudyActionsProps) {
  const counts: Record<ReadinessBucket, number> = {
    ready: 0,
    review: 0,
    preparing: 0,
    attention: 0,
  }
  for (const document of documents) counts[readinessBucket(document)] += 1

  const hasReadyDocument = counts.ready > 0
  const hasNoteReadyDocument = documents.some(isNoteReadyDocument)

  return (
    <section aria-label="学习就绪入口" className="ready-study-actions">
      <div className="ready-study-actions__overview">
        <h3>学习就绪</h3>
        <ul aria-label="资料学习状态" className="ready-study-actions__counts">
          {READINESS_LABELS.map(({ bucket, label }) => (
            <li aria-label={`${label} ${counts[bucket]}`} className={`is-${bucket}`} key={bucket}>
              <span>{label}</span>
              <strong>{counts[bucket]}</strong>
            </li>
          ))}
        </ul>
      </div>
      <nav aria-label="学习快捷操作" className="ready-study-actions__links">
        {hasReadyDocument ? (
          <>
            <Link className="button ready-study-actions__link" to="/graph">
              <Network aria-hidden="true" size={17} />
              查看概念地图
            </Link>
            {providerAvailable ? (
              <Link className="button ready-study-actions__link" to="/qa">
                <MessageSquareText aria-hidden="true" size={17} />
                开始问答
              </Link>
            ) : (
              <span className="ready-study-actions__status">
                <CircleAlert aria-hidden="true" size={16} />
                问答服务不可用
              </span>
            )}
            {noteWorkflowAvailable && hasNoteReadyDocument ? (
              <Link className="button button--primary ready-study-actions__link" to="/notes">
                <BookOpen aria-hidden="true" size={17} />
                生成复习笔记
              </Link>
            ) : (
              <span className="ready-study-actions__status">
                <CircleAlert aria-hidden="true" size={16} />
                {noteWorkflowAvailable ? '暂无可生成笔记的资料' : '笔记生成不可用'}
              </span>
            )}
          </>
        ) : (
          <span className="ready-study-actions__status">
            <CircleAlert aria-hidden="true" size={16} />
            暂无可学习资料
          </span>
        )}
      </nav>
    </section>
  )
}

export function LibraryPage() {
  const { courseId, capabilities, capabilitiesError, capabilitiesLoading } = useWorkspace()
  const queryClient = useQueryClient()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<DocumentRecord | null>(null)
  const [deletionId, setDeletionId] = useState<string | null>(null)
  const [jobNotice, setJobNotice] = useState<{
    tone: 'success' | 'danger'
    message: string
  } | null>(null)
  const previousStatuses = useRef(new Map<string, string>())

  const documentsQuery = useQuery({
    queryKey: ['documents', courseId],
    queryFn: () => studyApi.listDocuments(courseId),
    refetchInterval: (query) => {
      const currentDocuments = query.state.data as DocumentRecord[] | undefined
      return currentDocuments?.some((document) => ACTIVE_DOCUMENT_STATUSES.has(document.status))
        ? 2_000
        : false
    },
  })
  const documents = useMemo(() => documentsQuery.data ?? [], [documentsQuery.data])
  const providerAvailable = capabilities?.provider.status === 'available'
  const noteWorkflowAvailable =
    capabilities?.note_workflow.enabled === true &&
    capabilities.note_workflow.generation.status === 'available'
  const handleTerminalJobEvent = useCallback(
    ({ eventType, jobId }: JobTerminalEvent) => {
      const document = documents.find((item) => item.parse_job_id === jobId)
      if (eventType === 'job.succeeded') {
        if (!document || document.status === 'ready') return
        setJobNotice({ tone: 'success', message: '资料解析完成，正在准备学习入口。' })
        return
      }
      if (!document || FAILURE_STATUSES.has(document.status)) return
      setJobNotice({
        tone: 'danger',
        message:
          eventType === 'job.cancelled' ? '资料解析已取消。' : '资料解析未完成，请检查失败页后重试。',
      })
    },
    [documents],
  )
  const connection = useJobEvents(
    courseId,
    documents.flatMap((document) => (document.parse_job_id ? [document.parse_job_id] : [])),
    handleTerminalJobEvent,
  )

  useEffect(() => {
    if (!documentsQuery.isSuccess) return
    for (const document of documents) {
      const previous = previousStatuses.current.get(document.id)
      if (previous && previous !== 'ready' && document.status === 'ready') {
        setJobNotice({ tone: 'success', message: '资料已准备完成，现在可以学习了。' })
      }
      previousStatuses.current.set(document.id, document.status)
    }
  }, [documents, documentsQuery.isSuccess])
  const deletionQuery = useQuery({
    queryKey: ['deletion', deletionId],
    queryFn: () => studyApi.getDeletion(deletionId ?? ''),
    enabled: deletionId !== null,
    refetchInterval: (query) => (query.state.data?.status === 'completed' ? false : 1_500),
  })

  const retryMutation = useMutation({
    mutationFn: (document: DocumentRecord) =>
      studyApi.retryDocument(document.id, document.failed_pages),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['documents', courseId] }),
  })
  const deleteMutation = useMutation({
    mutationFn: (document: DocumentRecord) => studyApi.deleteDocument(document.id),
    onSuccess: (deletion) => {
      setDeletionId(deletion.deletion_id)
      setDeleteTarget(null)
      void queryClient.invalidateQueries({ queryKey: ['documents', courseId] })
    },
  })

  return (
    <div className="page page--library">
      <PageHeader
        actions={
          <button className="button button--primary" onClick={() => setUploadOpen(true)} type="button">
            <FilePlus2 aria-hidden="true" size={17} />
            添加资料
          </button>
        }
        kicker="Library"
        meta={`${documents.length} 份资料`}
        title="课程资料"
      />
      <CapabilityBanner
        capabilities={capabilities}
        error={capabilitiesError}
        loading={capabilitiesLoading}
      />
      <div className="library-status-line" aria-live="polite">
        <Radio aria-hidden="true" size={15} />
        {connection === 'connected'
          ? '任务事件已连接'
          : connection === 'reconnecting'
            ? '任务事件重连中'
            : '当前无运行任务'}
      </div>
      {jobNotice ? (
        <div className={`library-job-notice library-job-notice--${jobNotice.tone}`} role="status">
          {jobNotice.tone === 'success' ? (
            <CheckCircle2 aria-hidden="true" size={17} />
          ) : (
            <CircleAlert aria-hidden="true" size={17} />
          )}
          <span>{jobNotice.message}</span>
          <button
            aria-label="关闭提示"
            className="library-job-notice__close"
            onClick={() => setJobNotice(null)}
            type="button"
          >
            <X aria-hidden="true" size={16} />
          </button>
        </div>
      ) : null}
      {documentsQuery.isSuccess ? (
        <ReadyStudyActions
          documents={documents}
          noteWorkflowAvailable={noteWorkflowAvailable}
          providerAvailable={providerAvailable}
        />
      ) : null}
      {deletionId ? (
        <div className="cleanup-banner" role="status">
          {deletionQuery.data?.status === 'completed' ? (
            <Trash2 aria-hidden="true" size={17} />
          ) : (
            <LoaderCircle aria-hidden="true" className="spin" size={17} />
          )}
          <span>
            {deletionQuery.data?.status === 'completed'
              ? '资料已不可访问，后台清理完成。'
              : '资料已不可访问，后台清理中。'}
          </span>
        </div>
      ) : null}
      {documentsQuery.isError ? (
        <ErrorNotice
          error={documentsQuery.error}
          onRetry={() => void documentsQuery.refetch()}
          title="无法读取资料"
        />
      ) : documentsQuery.isLoading ? (
        <section className="loading-state" aria-label="正在加载资料">
          <LoaderCircle aria-hidden="true" className="spin" size={20} />
          <span>加载资料</span>
        </section>
      ) : (
        <DocumentTable
          busyDocumentId={retryMutation.variables?.id ?? deleteMutation.variables?.id}
          documents={documents}
          onDelete={setDeleteTarget}
          onRefresh={() => void documentsQuery.refetch()}
          onRetry={(document) => retryMutation.mutate(document)}
        />
      )}
      {retryMutation.isError ? (
        <ErrorNotice
          error={retryMutation.error}
          onRetry={() => retryMutation.variables && retryMutation.mutate(retryMutation.variables)}
          title="解析任务未提交"
        />
      ) : null}
      <UploadDialog
        courseId={courseId}
        mineruAvailable={capabilities?.mineru_parser.status === 'available'}
        onClose={() => setUploadOpen(false)}
        onUploaded={() => void queryClient.invalidateQueries({ queryKey: ['documents', courseId] })}
        open={uploadOpen}
      />
      <Modal
        description="删除后立即无法检索或引用，后台继续清理派生数据。"
        footer={
          <>
            <button className="button" onClick={() => setDeleteTarget(null)} type="button">
              取消
            </button>
            <button
              className="button button--danger"
              disabled={deleteMutation.isPending}
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget)}
              type="button"
            >
              {deleteMutation.isPending ? <LoaderCircle aria-hidden="true" className="spin" size={16} /> : <Trash2 aria-hidden="true" size={16} />}
              确认删除
            </button>
          </>
        }
        onClose={() => setDeleteTarget(null)}
        open={deleteTarget !== null}
        title={`删除 ${deleteTarget?.filename ?? '资料'}`}
      >
        {deleteMutation.isError ? <ErrorNotice error={deleteMutation.error} /> : null}
      </Modal>
    </div>
  )
}
