import { createPortal } from 'react-dom'
import { BookOpen, FileSearch, GraduationCap, Library, MessageSquareText, Network } from 'lucide-react'
import { useSyncExternalStore } from 'react'
import { NavLink, useLocation } from 'react-router'

import { LearningPage } from '../features/learning/LearningPage'

const destinations = [
  { to: '/', label: '资料', icon: Library, end: true },
  { to: '/qa', label: '问答', icon: MessageSquareText, end: false },
  { to: '/notes', label: '笔记', icon: BookOpen, end: false },
  { to: '/learning', label: '练习', icon: GraduationCap, end: false },
  { to: '/graph', label: '概念地图', icon: Network, end: false },
] as const

interface WorkspaceNavigationProps {
  mobile?: boolean
}

function subscribeToWorkspaceContent(onChange: () => void): () => void {
  if (typeof MutationObserver === 'undefined' || document.body === null) return () => undefined
  const observer = new MutationObserver(onChange)
  observer.observe(document.body, { childList: true, subtree: true })
  return () => observer.disconnect()
}

function getWorkspaceContent(): HTMLElement | null {
  return document.getElementById('workspace-content')
}

function LearningPagePortal() {
  const target = useSyncExternalStore(subscribeToWorkspaceContent, getWorkspaceContent, () => null)

  return target === null ? null : createPortal(<LearningPage />, target)
}

export function WorkspaceNavigation({ mobile = false }: WorkspaceNavigationProps) {
  const location = useLocation()

  return (
    <nav
      aria-label={mobile ? '移动学习视图' : '学习视图'}
      className={mobile ? 'mobile-nav' : 'workspace-nav'}
    >
      {!mobile ? (
        <div className="workspace-nav__label">
          <FileSearch aria-hidden="true" size={15} />
          <span>学习工作区</span>
        </div>
      ) : null}
      {destinations.map(({ to, label, icon: Icon, end }) => (
        <NavLink end={end} key={to} to={to}>
          <Icon aria-hidden="true" size={mobile ? 20 : 18} strokeWidth={1.8} />
          <span>{label}</span>
        </NavLink>
      ))}
      {!mobile && location.pathname === '/learning' ? <LearningPagePortal /> : null}
    </nav>
  )
}
