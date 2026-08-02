import { FileImage, FileText, Presentation, RefreshCw, RotateCcw, Trash2 } from 'lucide-react'

import type { DocumentRecord } from '../../api/types'
import { IconButton } from '../../components/ui/IconButton'
import { StatusBadge, type StatusTone } from '../../components/ui/StatusBadge'

const failureStatuses = new Set(['partial_failed', 'failed'])
const deletionStatuses = new Set(['deleted', 'deleting'])

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

function statusPresentation(document: DocumentRecord): { label: string; tone: StatusTone } {
  if (deletionStatuses.has(document.status)) return { label: '删除中', tone: 'neutral' }
  if (document.review_status === 'pending') return { label: '待管理员审核', tone: 'warning' }
  if (document.review_status === 'rejected') return { label: '审核未通过', tone: 'danger' }
  if (failureStatuses.has(document.status)) return { label: '处理失败', tone: 'danger' }
  if (document.status === 'ready' && document.indexable) {
    return { label: '可学习', tone: 'success' }
  }
  if (document.corpus_role !== 'corpus') return { label: '不参与学习', tone: 'neutral' }
  return { label: '处理中', tone: 'info' }
}

function readableProgress(document: DocumentRecord): {
  completedPages: number
  totalPages: number
} | null {
  if (document.status === 'ready') return null
  const progress = document.progress
  if (!progress) return null
  const completedPages = progress.completed_pages
  const totalPages = progress.total_pages
  if (typeof totalPages !== 'number' || totalPages <= 0) return null
  return {
    completedPages: typeof completedPages === 'number' && completedPages >= 0 ? completedPages : 0,
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
            <th scope="col">状态</th>
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
            const status = statusPresentation(document)
            const canRetry =
              document.review_status === 'approved' &&
              ['partial_failed', 'failed', 'retry_wait'].includes(document.status)
            const canReparse =
              document.review_status === 'approved' &&
              document.status === 'ready' &&
              document.corpus_role === 'corpus' &&
              document.media_type === 'application/pdf'
            const progress = readableProgress(document)
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
                <td data-label="状态">
                  <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
                  {progress ? (
                    <small className="table-progress">
                      {progress.completedPages}/{progress.totalPages} 页
                    </small>
                  ) : null}
                </td>
                <td data-label="页数">{document.page_count ?? '—'}</td>
                <td className="table-actions" data-label="操作">
                  {canRetry || canReparse ? (
                    <IconButton
                      disabled={busyDocumentId === document.id}
                      label={
                        document.failed_pages?.length
                          ? '重试失败页'
                          : canReparse
                            ? '重新解析'
                            : '重试解析'
                      }
                      onClick={() => onRetry(document)}
                      size="small"
                    >
                      {document.failed_pages?.length ? (
                        <RotateCcw aria-hidden="true" size={16} />
                      ) : (
                        <RefreshCw aria-hidden="true" size={16} />
                      )}
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
