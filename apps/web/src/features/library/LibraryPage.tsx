import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FilePlus2, LoaderCircle, Radio, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { studyApi } from '../../api/client'
import type { DocumentRecord } from '../../api/types'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { Modal } from '../../components/ui/Modal'
import { PageHeader } from '../../components/ui/PageHeader'
import { CapabilityBanner } from '../status/CapabilityBanner'
import { useWorkspace } from '../../app/WorkspaceContext'
import { DocumentTable } from './DocumentTable'
import { UploadDialog } from './UploadDialog'
import { useJobEvents } from './useJobEvents'

export function LibraryPage() {
  const { courseId, capabilities, capabilitiesError, capabilitiesLoading } = useWorkspace()
  const queryClient = useQueryClient()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<DocumentRecord | null>(null)
  const [deletionId, setDeletionId] = useState<string | null>(null)

  const documentsQuery = useQuery({
    queryKey: ['documents', courseId],
    queryFn: () => studyApi.listDocuments(courseId),
  })
  const documents = documentsQuery.data ?? []
  const connection = useJobEvents(
    courseId,
    documents.flatMap((document) => (document.parse_job_id ? [document.parse_job_id] : [])),
  )
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
          title="重试未提交"
        />
      ) : null}
      <UploadDialog
        courseId={courseId}
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
