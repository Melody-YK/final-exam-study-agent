import { FileUp, LoaderCircle, X } from 'lucide-react'
import { useRef, useState, type FormEvent } from 'react'

import { studyApi } from '../../api/client'
import type { CorpusRole, DocumentRecord } from '../../api/types'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { Modal } from '../../components/ui/Modal'

const roles: Array<{ value: CorpusRole; label: string }> = [
  { value: 'corpus', label: '课程资料' },
  { value: 'questions', label: '题目' },
  { value: 'gold_answers', label: '答案（不入索引）' },
  { value: 'ocr_gold', label: 'OCR 标注（不入索引）' },
  { value: 'excluded', label: '排除' },
]

interface UploadDialogProps {
  courseId: string
  open: boolean
  onClose: () => void
  onUploaded: (document: DocumentRecord) => void
}

export function UploadDialog({ courseId, open, onClose, onUploaded }: UploadDialogProps) {
  const [file, setFile] = useState<File | null>(null)
  const [role, setRole] = useState<CorpusRole>('corpus')
  const [progress, setProgress] = useState(0)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const abortRef = useRef<AbortController | null>(null)

  const resetAndClose = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setFile(null)
    setProgress(0)
    setPending(false)
    setError(null)
    onClose()
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!file || pending) return
    const controller = new AbortController()
    abortRef.current = controller
    setPending(true)
    setError(null)
    try {
      const document = await studyApi.uploadDocument(
        courseId,
        file,
        role,
        setProgress,
        controller.signal,
      )
      onUploaded(document)
      resetAndClose()
    } catch (caught) {
      if (controller.signal.aborted) return
      setError(caught)
      setPending(false)
    }
  }

  return (
    <Modal
      description="PDF、PPTX、PNG、JPEG 或 TIFF"
      footer={
        <>
          <button className="button" onClick={resetAndClose} type="button">
            {pending ? <X aria-hidden="true" size={16} /> : null}
            {pending ? '取消上传' : '取消'}
          </button>
          <button
            className="button button--primary"
            disabled={!file || pending}
            form="document-upload-form"
            type="submit"
          >
            {pending ? <LoaderCircle aria-hidden="true" className="spin" size={16} /> : <FileUp aria-hidden="true" size={16} />}
            上传
          </button>
        </>
      }
      onClose={resetAndClose}
      open={open}
      title="添加资料"
    >
      <form id="document-upload-form" onSubmit={(event) => void submit(event)}>
        <label className="file-picker" htmlFor="document-file">
          <FileUp aria-hidden="true" size={22} />
          <span>{file?.name ?? '选择文件'}</span>
          <input
            accept=".pdf,.pptx,.png,.jpg,.jpeg,.tif,.tiff"
            id="document-file"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            type="file"
          />
        </label>
        <label className="field" htmlFor="corpus-role">
          <span>资料角色</span>
          <select
            id="corpus-role"
            onChange={(event) => setRole(event.target.value as CorpusRole)}
            value={role}
          >
            {roles.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <div
          aria-label={`上传进度 ${progress}%`}
          aria-valuemax={100}
          aria-valuemin={0}
          aria-valuenow={progress}
          className="progress-track"
          role="progressbar"
        >
          <span style={{ width: `${progress}%` }} />
        </div>
        {error ? <ErrorNotice error={error} title="上传失败" /> : null}
      </form>
    </Modal>
  )
}
