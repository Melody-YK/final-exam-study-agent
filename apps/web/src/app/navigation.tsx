import { BookOpen, FileSearch, Library, MessageSquareText, Network } from 'lucide-react'
import { NavLink } from 'react-router-dom'

const destinations = [
  { to: '/', label: '资料', icon: Library, end: true },
  { to: '/qa', label: '问答', icon: MessageSquareText, end: false },
  { to: '/notes', label: '笔记', icon: BookOpen, end: false },
  { to: '/graph', label: '知识图谱', icon: Network, end: false },
] as const

interface WorkspaceNavigationProps {
  mobile?: boolean
}

export function WorkspaceNavigation({ mobile = false }: WorkspaceNavigationProps) {
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
    </nav>
  )
}
