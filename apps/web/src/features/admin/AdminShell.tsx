import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  ArrowLeft,
  Database,
  FileCheck2,
  FileStack,
  Gauge,
  LogOut,
  NotebookTabs,
  ShieldCheck,
  Users,
} from 'lucide-react'
import { Link, NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'

import { studyApi } from '../../api/client'
import { useAuth } from '../../app/auth'
import { WorkspaceContext } from '../../app/WorkspaceContext'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { IconButton } from '../../components/ui/IconButton'
import { DemoLabPage } from '../demo-lab/DemoLabPage'
import { AdminUsersPage } from './AdminUsersPage'
import { AdminReviewsPage } from './AdminReviewsPage'

interface AdminShellProps {
  courseId: string | null
  onLeaveCourse: () => void
}

const adminDestinations = [
  { to: '/admin', label: '概览', icon: Gauge, end: true },
  { to: '/admin/users', label: '用户', icon: Users, end: false },
  { to: '/admin/reviews', label: '资料审核', icon: FileCheck2, end: false },
  { to: '/admin/diagnostics', label: '工程诊断', icon: Activity, end: false },
] as const

export function AdminShell({ courseId, onLeaveCourse }: AdminShellProps) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  if (user?.role !== 'admin') return <Navigate replace to="/" />

  const signOut = async () => {
    onLeaveCourse()
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
          <Link className="admin-back-link" to="/">
            <ArrowLeft aria-hidden="true" size={16} />
            返回学习端
          </Link>
        </aside>

        <main id="admin-content">
          <Routes>
            <Route element={<AdminOverviewPage />} index />
            <Route element={<AdminUsersPage />} path="users" />
            <Route element={<AdminReviewsPage />} path="reviews" />
            <Route element={<AdminDiagnosticsPage courseId={courseId} />} path="diagnostics" />
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

function AdminDiagnosticsPage({ courseId }: { courseId: string | null }) {
  const courseQuery = useQuery({
    queryKey: ['course', courseId],
    queryFn: () => studyApi.getCourse(courseId ?? ''),
    enabled: courseId !== null,
  })
  const capabilitiesQuery = useQuery({
    queryKey: ['capabilities'],
    queryFn: () => studyApi.capabilities(),
    retry: false,
  })

  if (courseId === null) {
    return (
      <section className="admin-page page-state">
        <Activity aria-hidden="true" size={28} />
        <h2>尚未选择诊断课程</h2>
        <p>先在学习端进入一门课程，再查看其检索工程链路。</p>
        <Link className="button button--primary" to="/">
          返回学习端
        </Link>
      </section>
    )
  }

  return (
    <WorkspaceContext.Provider
      value={{
        courseId,
        course: courseQuery.data,
        capabilities: capabilitiesQuery.data,
        capabilitiesLoading: capabilitiesQuery.isLoading,
        capabilitiesError: capabilitiesQuery.isError,
      }}
    >
      <DemoLabPage />
    </WorkspaceContext.Provider>
  )
}
