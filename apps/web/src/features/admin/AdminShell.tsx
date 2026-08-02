import { useQuery } from '@tanstack/react-query'
import {
  Database,
  FileCheck2,
  FileStack,
  Gauge,
  LibraryBig,
  LogOut,
  NotebookTabs,
  ShieldCheck,
  Users,
} from 'lucide-react'
import { NavLink, Navigate, Route, Routes, useNavigate } from 'react-router'

import { studyApi } from '../../api/client'
import { useAuth } from '../../app/auth'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { IconButton } from '../../components/ui/IconButton'
import { AdminContentPage } from './AdminContentPage'
import { AdminReviewsPage } from './AdminReviewsPage'
import { AdminUsersPage } from './AdminUsersPage'

const adminDestinations = [
  { to: '/admin', label: '概览', icon: Gauge, end: true },
  { to: '/admin/users', label: '用户', icon: Users, end: false },
  { to: '/admin/content', label: '用户内容', icon: LibraryBig, end: false },
  { to: '/admin/reviews', label: '资料审核', icon: FileCheck2, end: false },
] as const

export function AdminShell() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  if (user?.role !== 'admin') return <Navigate replace to="/" />

  const signOut = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="admin-shell">
      <header className="admin-topbar">
        <div className="admin-topbar__brand">
          <span aria-hidden="true" className="brand-mark">
            FD
          </span>
          <div>
            <p>FINALS DESK</p>
            <h1>管理控制台</h1>
          </div>
        </div>
        <div className="admin-topbar__actions">
          <span className="admin-account">
            <strong>{user.display_name}</strong>
            <small>管理员</small>
          </span>
          <IconButton label="退出登录" onClick={() => void signOut()} size="small">
            <LogOut aria-hidden="true" size={17} />
          </IconButton>
        </div>
      </header>

      <div className="admin-layout">
        <aside className="admin-sidebar">
          <div className="admin-sidebar__label">
            <ShieldCheck aria-hidden="true" size={15} />
            系统管理
          </div>
          <nav aria-label="管理视图" className="admin-nav">
            {adminDestinations.map(({ to, label, icon: Icon, end }) => (
              <NavLink end={end} key={to} to={to}>
                <Icon aria-hidden="true" size={18} />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>
        </aside>

        <main id="admin-content">
          <Routes>
            <Route element={<AdminOverviewPage />} index />
            <Route element={<AdminUsersPage />} path="users" />
            <Route element={<AdminContentPage />} path="content/*" />
            <Route element={<AdminReviewsPage />} path="reviews" />
            <Route element={<Navigate replace to="/admin" />} path="*" />
          </Routes>
        </main>
      </div>
    </div>
  )
}

function AdminOverviewPage() {
  const diagnosticsQuery = useQuery({
    queryKey: ['admin', 'diagnostics'],
    queryFn: () => studyApi.adminDiagnostics(),
  })

  return (
    <section className="admin-page">
      <header className="admin-page__header">
        <div>
          <p className="section-kicker">ADMIN</p>
          <h2>运行概览</h2>
          <p>本地演示环境的账号、资料与任务状态。</p>
        </div>
      </header>

      {diagnosticsQuery.isError ? (
        <ErrorNotice
          error={diagnosticsQuery.error}
          onRetry={() => void diagnosticsQuery.refetch()}
          title="无法读取运行概览"
        />
      ) : null}

      <div className="admin-metrics" aria-busy={diagnosticsQuery.isLoading}>
        <Metric icon={Users} label="用户" value={diagnosticsQuery.data?.totals.accounts} />
        <Metric icon={FileStack} label="课程" value={diagnosticsQuery.data?.totals.courses} />
        <Metric icon={Database} label="资料" value={diagnosticsQuery.data?.totals.documents} />
        <Metric icon={NotebookTabs} label="笔记" value={diagnosticsQuery.data?.totals.notes} />
      </div>

      <dl className="admin-runtime-list">
        <div>
          <dt>数据库</dt>
          <dd>{diagnosticsQuery.data?.runtime.database ?? '读取中'}</dd>
        </div>
        <div>
          <dt>应用模式</dt>
          <dd>{diagnosticsQuery.data?.runtime.app_mode ?? '读取中'}</dd>
        </div>
        <div>
          <dt>活跃会话</dt>
          <dd>{diagnosticsQuery.data?.totals.active_sessions ?? '读取中'}</dd>
        </div>
      </dl>
    </section>
  )
}

function Metric({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Users
  label: string
  value: number | undefined
}) {
  return (
    <div>
      <Icon aria-hidden="true" size={19} />
      <span>{label}</span>
      <strong>{value ?? '--'}</strong>
    </div>
  )
}
