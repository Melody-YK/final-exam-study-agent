import { FileUp, LoaderCircle, X } from 'lucide-react'
import { useRef, useState, type FormEvent } from 'react'

import { studyApi } from '../../api/client'
import type { DocumentRecord } from '../../api/types'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { Modal } from '../../components/ui/Modal'

const supportedUploadExtensions = new Set([
  'pdf',
  'md',
  'markdown',
  'jpg',
  'jpeg',
  'png',
])
const maxMarkdownUploadBytes = 5 * 1024 * 1024

interface FileSelectionError {
  error: Error
  title: string
}

interface UploadDialogProps {
  courseId: string
  open: boolean
  onClose: () => void
  onUploaded: (document: DocumentRecord) => void
}

function isSupportedUpload(file: File): boolean {
  const separator = file.name.lastIndexOf('.')
  if (separator < 0) return false
  return supportedUploadExtensions.has(
    file.name.slice(separator + 1).toLowerCase(),
  )
}

function isMarkdownUpload(file: File): boolean {
  const extension = file.name.slice(file.name.lastIndexOf('.') + 1).toLowerCase()
  return extension === 'md' || extension === 'markdown'
}

function fileSelectionError(file: File): FileSelectionError | null {
  if (!isSupportedUpload(file)) {
    return {
      error: new Error(
        `暂不支持“${file.name}”。请选择 PDF、Markdown（.md/.markdown）、JPG、JPEG 或 PNG 文件；PPTX、DOCX 和 TIFF 当前不可上传。`,
      ),
      title: '文件格式不支持',
    }
  }
  if (isMarkdownUpload(file) && file.size > maxMarkdownUploadBytes) {
    return {
      error: new Error('Markdown 单个文件不能超过 5 MB；请拆分章节或转换为 PDF。'),
      title: 'Markdown 文件过大',
    }
  }
  return null
}

export function UploadDialog({
  courseId,
  open,
  onClose,
  onUploaded,
}: UploadDialogProps) {
  const [file, setFile] = useState<File | null>(null)
  const [progress, setProgress] = useState(0)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [fileError, setFileError] = useState<FileSelectionError | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const resetAndClose = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setFile(null)
    setProgress(0)
    setPending(false)
    setError(null)
    setFileError(null)
    onClose()
  }

  const selectFile = (selectedFile: File | null) => {
    setProgress(0)
    setError(null)
    if (selectedFile === null) {
      setFile(null)
      setFileError(null)
      return
    }
    const selectionError = fileSelectionError(selectedFile)
    if (selectionError) {
      setFile(null)
      setFileError(selectionError)
      return
    }
    setFile(selectedFile)
    setFileError(null)
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!file || pending) return
    const selectionError = fileSelectionError(file)
    if (selectionError) {
      setFile(null)
      setFileError(selectionError)
      return
    }
    const controller = new AbortController()
    abortRef.current = controller
    setPending(true)
    setError(null)
    try {
      const document = await studyApi.uploadDocument(
        courseId,
        file,
        'corpus',
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
      description="上传课程资料"
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
            {pending ? (
              <LoaderCircle aria-hidden="true" className="spin" size={16} />
            ) : (
              <FileUp aria-hidden="true" size={16} />
            )}
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
          <span className="file-picker__copy">
            <strong>{file?.name ?? '选择文件'}</strong>
            <small id="document-file-formats">
              支持 PDF、Markdown（.md/.markdown）、JPG、JPEG、PNG
            </small>
            <small id="document-file-markdown">
              Markdown 将按标题和段落定位来源，单个文件最大 5 MB。
            </small>
          </span>
          <input
            accept=".pdf,.md,.markdown,.jpg,.jpeg,.png"
            aria-describedby="document-file-formats document-file-markdown"
            aria-invalid={fileError ? true : undefined}
            disabled={pending}
            id="document-file"
            onChange={(event) => {
              const selectedFile = event.target.files?.[0] ?? null
              selectFile(selectedFile)
              if (selectedFile && fileSelectionError(selectedFile))
                event.target.value = ''
            }}
            type="file"
          />
        </label>
        {fileError ? (
          <ErrorNotice error={fileError.error} title={fileError.title} />
        ) : null}
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
