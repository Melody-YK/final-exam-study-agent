import type { ReactNode } from 'react'

export type StatusTone = 'neutral' | 'success' | 'warning' | 'danger' | 'info'

interface StatusBadgeProps {
  children: ReactNode
  tone?: StatusTone
  dot?: boolean
}

export function StatusBadge({ children, tone = 'neutral', dot = true }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-badge--${tone}`}>
      {dot ? <span aria-hidden="true" className="status-badge__dot" /> : null}
      <span>{children}</span>
    </span>
  )
}
