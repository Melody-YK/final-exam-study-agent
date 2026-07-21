import { useQuery } from '@tanstack/react-query'
import { ChevronDown, CloudOff, LogOut, Wifi } from 'lucide-react'
import { Routes, Route } from 'react-router-dom'

import { studyApi } from '../api/client'
import { IconButton } from '../components/ui/IconButton'
import { DemoLabPage } from '../features/demo-lab/DemoLabPage'
import { LibraryPage } from '../features/library/LibraryPage'
import { NotesPage } from '../features/notes/NotesPage'
import { QAPage } from '../features/qa/QAPage'
import { MobileNav } from './MobileNav'
import { WorkspaceContext } from './WorkspaceContext'
import { WorkspaceNavigation } from './navigation'

interface WorkspaceShellProps {
  courseId: string
  onLeaveCourse: () => void
}

export function WorkspaceShell({ courseId, onLeaveCourse }: WorkspaceShellProps) {
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
      <a className="skip-link" href="#workspace-content">
        跳到主要内容
      </a>
      <div className="app-shell">
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
            <IconButton label="退出当前课程" onClick={onLeaveCourse} size="small">
              <LogOut aria-hidden="true" size={17} />
            </IconButton>
          </div>
        </header>
        <div className="workspace-layout">
          <aside className="workspace-sidebar">
            <button className="course-switcher" onClick={onLeaveCourse} type="button">
              <span>{courseQuery.data?.title ?? '课程'}</span>
              <ChevronDown aria-hidden="true" size={16} />
            </button>
            <WorkspaceNavigation />
            <div className="workspace-sidebar__meta">
              <span>本地单用户</span>
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
                <Route element={<DemoLabPage />} path="/lab" />
              </Routes>
            )}
          </main>
        </div>
        <MobileNav />
      </div>
    </WorkspaceContext.Provider>
  )
}
