import {
  FileImage,
  FileText,
  Presentation,
  RefreshCw,
  RotateCcw,
  Trash2,
} from 'lucide-react'

import type { DocumentRecord } from '../../api/types'
import { IconButton } from '../../components/ui/IconButton'
import { StatusBadge, type StatusTone } from '../../components/ui/StatusBadge'

const roleLabels: Record<string, string> = {
  corpus: '课程资料',
  questions: '题目',
  gold_answers: '答案',
  ocr_gold: 'OCR 标注',
  excluded: '排除',
}

const statusLabels: Record<string, { label: string; tone: StatusTone }> = {
  created: { label: '等待上传', tone: 'neutral' },
  uploaded: { label: '已上传', tone: 'info' },
  queued: { label: '等待 Worker', tone: 'warning' },
  parsing: { label: '解析中', tone: 'info' },
  processing: { label: '解析中', tone: 'info' },
  retry_wait: { label: '等待重试', tone: 'warning' },
  partial_failed: { label: '部分失败', tone: 'danger' },
  failed: { label: '失败', tone: 'danger' },
  parsed_index_blocked: { label: '待索引', tone: 'warning' },
  indexing: { label: '索引中', tone: 'info' },
  ready: { label: '可问答', tone: 'success' },
  deleted: { label: '清理中', tone: 'neutral' },
  deleting: { label: '清理中', tone: 'neutral' },
}

interface DocumentTableProps {
  documents: DocumentRecord[]
  busyDocumentId?: string | null
  onDelete: (document: DocumentRecord) => void
  onRetry: (document: DocumentRecord) => void
  onRefresh: () => void
}

function FileKindIcon({ mediaType }: { mediaType: string }) {
  if (mediaType.includes('presentation')) return <Presentation aria-hidden="true" size={19} />
  if (mediaType.startsWith('image/')) return <FileImage aria-hidden="true" size={19} />
  return <FileText aria-hidden="true" size={19} />
}

function revisionLabel(document: DocumentRecord): string {
  if (document.active_revision_id && document.preview_revision_id) return '活动版 + 待确认预览'
  if (document.active_revision_id) return '活动版本'
  if (document.preview_revision_id) return '预览待激活'
  return '尚无版本'
}

function readableProgress(progress: DocumentRecord['progress']): {
  completedPages: number
  totalPages: number
} | null {
  if (!progress) return null
  const completedPages = progress.completed_pages
  const totalPages = progress.total_pages
  if (typeof totalPages !== 'number' || totalPages <= 0) return null
  return {
    completedPages:
      typeof completedPages === 'number' && completedPages >= 0 ? completedPages : 0,
    totalPages,
  }
}

export function DocumentTable({
  documents,
  busyDocumentId,
  onDelete,
  onRetry,
  onRefresh,
}: DocumentTableProps) {
  if (documents.length === 0) {
    return (
      <section className="empty-state">
        <FileText aria-hidden="true" size={26} />
        <h3>暂无资料</h3>
      </section>
    )
  }

  return (
    <div className="document-table-wrap">
      <table className="document-table">
        <thead>
          <tr>
            <th scope="col">资料</th>
            <th scope="col">角色</th>
            <th scope="col">状态</th>
            <th scope="col">版本</th>
            <th scope="col">页数</th>
            <th className="table-actions" scope="col">
              <IconButton label="刷新资料" onClick={onRefresh} size="small">
                <RefreshCw aria-hidden="true" size={16} />
              </IconButton>
            </th>
          </tr>
        </thead>
        <tbody>
          {documents.map((document) => {
            const status = statusLabels[document.status] ?? {
              label: document.status,
              tone: 'neutral' as const,
            }
            const canRetry = ['partial_failed', 'failed', 'retry_wait'].includes(
              document.status,
            )
            const progress = readableProgress(document.progress)
            return (
              <tr key={document.id}>
                <td data-label="资料">
                  <div className="document-name">
                    <span className="document-name__icon">
                      <FileKindIcon mediaType={document.media_type} />
                    </span>
                    <span>
                      <strong>{document.filename}</strong>
                      <small>{document.media_type}</small>
                    </span>
                  </div>
                </td>
                <td data-label="角色">
                  <span className={document.indexable ? '' : 'muted'}>
                    {roleLabels[document.corpus_role] ?? document.corpus_role}
                  </span>
                </td>
                <td data-label="状态">
                  <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
                  {progress ? (
                    <small className="table-progress">
                      {progress.completedPages}/{progress.totalPages} 页
                    </small>
                  ) : null}
                  {document.error_code ? <small className="table-error">{document.error_code}</small> : null}
                </td>
                <td data-label="版本">
                  <span className="revision-state">{revisionLabel(document)}</span>
                </td>
                <td data-label="页数">{document.page_count ?? '—'}</td>
                <td className="table-actions" data-label="操作">
                  {canRetry ? (
                    <IconButton
                      disabled={busyDocumentId === document.id}
                      label={document.failed_pages?.length ? '重试失败页' : '重试解析'}
                      onClick={() => onRetry(document)}
                      size="small"
                    >
                      {document.failed_pages?.length ? <RotateCcw aria-hidden="true" size={16} /> : <RefreshCw aria-hidden="true" size={16} />}
                    </IconButton>
                  ) : null}
                  <IconButton
                    disabled={busyDocumentId === document.id}
                    label="删除资料"
                    onClick={() => onDelete(document)}
                    size="small"
                  >
                    <Trash2 aria-hidden="true" size={16} />
                  </IconButton>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
