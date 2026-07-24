import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Eye, FileCheck2, X } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import { studyApi } from '../../api/client'
import type { AdminDocument, AdminDocumentReviewRequest } from '../../api/types'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { IconButton } from '../../components/ui/IconButton'
import { Modal } from '../../components/ui/Modal'
import { StatusBadge, type StatusTone } from '../../components/ui/StatusBadge'

type ReviewFilter = 'all' | AdminDocument['review_status']
type ReviewDecision = AdminDocumentReviewRequest['review_status']

const reviewPresentation: Record<
  AdminDocument['review_status'],
  { label: string; tone: StatusTone }
> = {
  pending: { label: '待审核', tone: 'warning' },
  approved: { label: '已通过', tone: 'success' },
  rejected: { label: '未通过', tone: 'danger' },
}

export function AdminReviewsPage() {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState<ReviewFilter>('pending')
  const [target, setTarget] = useState<AdminDocument | null>(null)
  const [decision, setDecision] = useState<ReviewDecision>('approved')
  const [reviewNote, setReviewNote] = useState('')
  const documentsQuery = useQuery({
    queryKey: ['admin', 'documents', filter],
    queryFn: () => studyApi.listAdminDocuments(filter === 'all' ? undefined : filter),
  })
  const reviewDocument = useMutation({
    mutationFn: ({ id, input }: { id: string; input: AdminDocumentReviewRequest }) =>
      studyApi.reviewAdminDocument(id, input),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['admin', 'documents'] }),
        queryClient.invalidateQueries({ queryKey: ['admin', 'diagnostics'] }),
      ])
      setTarget(null)
    },
  })

  const openReview = (document: AdminDocument, nextDecision: ReviewDecision) => {
    reviewDocument.reset()
    setTarget(document)
    setDecision(nextDecision)
    setReviewNote('')
  }
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (target === null) return
    reviewDocument.mutate({
      id: target.id,
      input: {
        review_status: decision,
        review_note: reviewNote.trim() || null,
      },
    })
  }

  return (
    <section className="admin-page">
      <header className="admin-page__header">
        <div>
          <p className="section-kicker">REVIEW</p>
          <h2>资料审核</h2>
          <p>检查用户上传的原文件，并决定是否允许进入知识库。</p>
        </div>
      </header>

      <div aria-label="审核状态" className="admin-tabs" role="tablist">
        {(
          [
            ['pending', '待审核'],
            ['approved', '已通过'],
            ['rejected', '未通过'],
            ['all', '全部'],
          ] as const
        ).map(([value, label]) => (
          <button
            aria-selected={filter === value}
            key={value}
            onClick={() => setFilter(value)}
            role="tab"
            type="button"
          >
            {label}
          </button>
        ))}
      </div>

      {documentsQuery.isError ? (
        <ErrorNotice
          error={documentsQuery.error}
          onRetry={() => void documentsQuery.refetch()}
          title="无法读取审核队列"
        />
      ) : null}

      <div className="admin-table-wrap" aria-busy={documentsQuery.isLoading}>
        <table className="admin-table admin-table--reviews">
          <thead>
            <tr>
              <th>资料</th>
              <th>上传者</th>
              <th>课程</th>
              <th>解析状态</th>
              <th>审核状态</th>
              <th>上传时间</th>
              <th className="table-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            {(documentsQuery.data?.items ?? []).map((document) => {
              const review = reviewPresentation[document.review_status]
              return (
                <tr key={document.id}>
                  <td>
                    <strong>{document.filename}</strong>
                    <small>
                      {document.media_type} · {formatBytes(document.size_bytes)}
                    </small>
                  </td>
                  <td>
                    <strong>{document.owner_display_name ?? '未绑定账号'}</strong>
                    <small>{document.owner_email ?? document.owner_subject}</small>
                  </td>
                  <td>{document.course_title}</td>
                  <td>
                    <span>{document.status}</span>
                    {document.page_count ? <small>{document.page_count} 页</small> : null}
                  </td>
                  <td>
                    <StatusBadge tone={review.tone}>{review.label}</StatusBadge>
                    {document.review_note ? <small>{document.review_note}</small> : null}
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
                    {document.review_status === 'pending' ? (
                      <>
                        <IconButton
                          label={`通过 ${document.filename}`}
                          onClick={() => openReview(document, 'approved')}
                          size="small"
                        >
                          <Check aria-hidden="true" size={16} />
                        </IconButton>
                        <IconButton
                          label={`拒绝 ${document.filename}`}
                          onClick={() => openReview(document, 'rejected')}
                          size="small"
                        >
                          <X aria-hidden="true" size={16} />
                        </IconButton>
                      </>
                    ) : null}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!documentsQuery.isLoading && documentsQuery.data?.items.length === 0 ? (
          <div className="admin-review-empty">
            <FileCheck2 aria-hidden="true" size={24} />
            <p>{filter === 'pending' ? '没有待审核资料' : '该状态下暂无资料'}</p>
          </div>
        ) : null}
      </div>

      <Modal
        description={
          decision === 'approved'
            ? '通过后，资料会进入索引并可用于问答、图谱和笔记。'
            : '未通过的资料会保留，但不会进入任何复习内容。'
        }
        footer={
          <>
            <button
              className="button button--secondary"
              onClick={() => setTarget(null)}
              type="button"
            >
              取消
            </button>
            <button
              className={
                decision === 'approved' ? 'button button--primary' : 'button button--danger'
              }
              disabled={reviewDocument.isPending}
              form="document-review-form"
              type="submit"
            >
              {reviewDocument.isPending
                ? '提交中...'
                : decision === 'approved'
                  ? '确认通过'
                  : '确认拒绝'}
            </button>
          </>
        }
        onClose={() => {
          if (!reviewDocument.isPending) setTarget(null)
        }}
        open={target !== null}
        title={decision === 'approved' ? '通过资料' : '拒绝资料'}
      >
        <form id="document-review-form" onSubmit={submit}>
          <label className="field">
            <span>{decision === 'rejected' ? '拒绝原因' : '审核备注'}</span>
            <textarea
              maxLength={500}
              onChange={(event) => setReviewNote(event.target.value)}
              placeholder={decision === 'rejected' ? '请说明文件存在的问题' : '可选，仅管理员可见'}
              required={decision === 'rejected'}
              rows={4}
              value={reviewNote}
            />
          </label>
          {reviewDocument.isError ? (
            <ErrorNotice error={reviewDocument.error} title="审核决定未保存" />
          ) : null}
        </form>
      </Modal>
    </section>
  )
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}
