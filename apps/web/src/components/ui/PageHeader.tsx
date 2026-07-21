import type { ReactNode } from 'react'

interface PageHeaderProps {
  kicker: string
  title: string
  meta?: string
  actions?: ReactNode
}

export function PageHeader({ kicker, title, meta, actions }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div>
        <p className="section-kicker">{kicker}</p>
        <h2>{title}</h2>
        {meta ? <p className="page-header__meta">{meta}</p> : null}
      </div>
      {actions ? <div className="page-header__actions">{actions}</div> : null}
    </header>
  )
}
