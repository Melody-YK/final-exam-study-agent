import { AlertCircle, RotateCcw } from 'lucide-react'

import { ApiError } from '../../api/client'

interface ErrorNoticeProps {
  error: unknown
  onRetry?: () => void
  title?: string
}

export function ErrorNotice({ error, onRetry, title = '操作未完成' }: ErrorNoticeProps) {
  const message =
    error instanceof ApiError
      ? error.problem.detail ?? error.problem.title
      : error instanceof Error
        ? error.message
        : '本地 API 暂时不可用。'

  return (
    <div className="error-notice" role="alert">
      <AlertCircle aria-hidden="true" size={18} />
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
      {onRetry ? (
        <button className="button button--small" onClick={onRetry} type="button">
          <RotateCcw aria-hidden="true" size={15} />
          重试
        </button>
      ) : null}
    </div>
  )
}
