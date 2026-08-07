import { useQuery } from '@tanstack/react-query'
import { ChevronDown, CloudOff, LogOut, ShieldCheck, UserRound, Wifi } from 'lucide-react'
import { useState } from 'react'
import { Link, Routes, Route, useLocation, useNavigate } from 'react-router'

import { studyApi } from '../api/client'
import { IconButton } from '../components/ui/IconButton'
import { KnowledgeGraphPage } from '../features/knowledge-graph/KnowledgeGraphPage'
import { LibraryPage } from '../features/library/LibraryPage'
import { NotesPage } from '../features/notes/NotesPage'
import { QAPage } from '../features/qa/QAPage'
import { MobileNav } from './MobileNav'
import { useAuth } from './auth'
import { WorkspaceContext } from './WorkspaceContext'
import { WorkspaceNavigation } from './navigation'

interface WorkspaceShellProps {
  courseId: string
  onLeaveCourse: () => void
}

export function WorkspaceShell({ courseId, onLeaveCourse }: WorkspaceShellProps) {
  const { user, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const [immersiveNotes, setImmersiveNotes] = useState(false)
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)
  const courseQuery = useQuery({
    queryKey: ['course', courseId],
    queryFn: () => studyApi.getCourse(courseId),
  })
  const capabilitiesQuery = useQuery({
    queryKey: ['capabilities'],
    queryFn: () => studyApi.capabilities(),
    retry: false,
  })
  const providerAvailable = capabilitiesQuery.data?.provider.status === 'available'
  const showImmersiveNotes = location.pathname === '/notes' && immersiveNotes

  const confirmDiscardChanges = () =>
    !hasUnsavedChanges || window.confirm('当前有未保存的笔记修改，确定离开吗？')

  const signOut = async () => {
    if (!confirmDiscardChanges()) return
    onLeaveCourse()
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <WorkspaceContext.Provider
      value={{
        courseId,
        course: courseQuery.data,
        capabilities: capabilitiesQuery.data,
        capabilitiesLoading: capabilitiesQuery.isLoading,
        capabilitiesError: capabilitiesQuery.isError,
        immersiveNotes: showImmersiveNotes,
        setImmersiveNotes,
        hasUnsavedChanges,
        setHasUnsavedChanges,
      }}
    >
      <a className="skip-link" href="#workspace-content">
        跳到主要内容
      </a>
      <div className={`app-shell${showImmersiveNotes ? ' app-shell--immersive-notes' : ''}`}>
        <header className="topbar">
          <div className="topbar__identity">
            <span aria-hidden="true" className="brand-mark">
              FD
            </span>
            <div className="topbar__title">
              <p>Finals Desk</p>
              <h1>{courseQuery.data?.title ?? '课程工作区'}</h1>
            </div>
          </div>
          <div className="topbar__actions">
            <span className={`runtime-state ${providerAvailable ? 'is-online' : ''}`}>
              {providerAvailable ? <Wifi aria-hidden="true" size={15} /> : <CloudOff aria-hidden="true" size={15} />}
              {providerAvailable ? 'Provider 可用' : '仅资料模式'}
            </span>
            <span className="topbar-account">
              <UserRound aria-hidden="true" size={15} />
              {user?.display_name}
            </span>
            {user?.role === 'admin' ? (
              <Link
                className="topbar-admin-link"
                onClick={(event) => {
                  if (!confirmDiscardChanges()) event.preventDefault()
                }}
                to="/admin"
              >
                <ShieldCheck aria-hidden="true" size={16} />
                管理端
              </Link>
            ) : null}
            <IconButton label="退出登录" onClick={() => void signOut()} size="small">
              <LogOut aria-hidden="true" size={17} />
            </IconButton>
          </div>
        </header>
        <div className="workspace-layout">
          <aside className="workspace-sidebar">
            <button
              className="course-switcher"
              onClick={() => {
                if (confirmDiscardChanges()) onLeaveCourse()
              }}
              type="button"
            >
              <span>{courseQuery.data?.title ?? '课程'}</span>
              <ChevronDown aria-hidden="true" size={16} />
            </button>
            <WorkspaceNavigation />
            <div className="workspace-sidebar__meta">
              <span>{user?.email}</span>
              <span>{user?.role === 'admin' ? '管理员账号' : '学习账号'}</span>
              <span>API 驱动</span>
            </div>
          </aside>
          <main id="workspace-content" tabIndex={-1}>
            {courseQuery.isError ? (
              <section className="page-state" role="alert">
                <h2>课程不可用</h2>
                <p>无法读取当前课程。</p>
                <button className="button" onClick={onLeaveCourse} type="button">
                  返回课程设置
                </button>
              </section>
            ) : (
              <Routes>
                <Route element={<LibraryPage />} path="/" />
                <Route element={<QAPage />} path="/qa" />
                <Route element={<NotesPage />} path="/notes" />
                <Route element={<KnowledgeGraphPage />} path="/graph" />
              </Routes>
            )}
          </main>
        </div>
        <MobileNav />
      </div>
    </WorkspaceContext.Provider>
  )
}
