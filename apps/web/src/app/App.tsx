import { QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router'

import { ErrorNotice } from '../components/ui/ErrorNotice'
import { AdminShell } from '../features/admin/AdminShell'
import { AuthPage } from '../features/auth/AuthPage'
import { useAuth } from './auth'
import { AuthProvider } from './AuthProvider'
import { CourseSetup } from './CourseSetup'
import { createWorkspaceQueryClient } from './queryClient'
import { WorkspaceShell } from './WorkspaceShell'

const COURSE_STORAGE_KEY = 'study-agent.course-id'

function accountCourseStorageKey(accountId: string): string {
  return `${COURSE_STORAGE_KEY}:${accountId}`
}

export function App() {
  const queryClient = useMemo(() => createWorkspaceQueryClient(), [])

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

function AppRoutes() {
  const queryClient = useQueryClient()
  const [selectedCourses, setSelectedCourses] = useState<Record<string, string>>({})
  const auth = useAuth()
  const location = useLocation()
  const accountId = auth.user?.id
  const courseId = accountId
    ? (selectedCourses[accountId] ?? localStorage.getItem(accountCourseStorageKey(accountId)))
    : null

  const selectCourse = (id: string) => {
    if (accountId === undefined) return
    localStorage.setItem(accountCourseStorageKey(accountId), id)
    setSelectedCourses((current) => ({ ...current, [accountId]: id }))
  }

  const leaveCourse = () => {
    if (accountId !== undefined) {
      localStorage.removeItem(accountCourseStorageKey(accountId))
      setSelectedCourses((current) => {
        const next = { ...current }
        delete next[accountId]
        return next
      })
    }
    localStorage.removeItem(COURSE_STORAGE_KEY)
    queryClient.removeQueries({
      predicate: (query) => query.queryKey[0] !== 'auth',
    })
  }

  if (auth.loading) {
    return (
      <main className="page-state page-state--full" aria-busy="true">
        <span className="brand-mark" aria-hidden="true">
          FD
        </span>
        <h1>正在打开工作区</h1>
      </main>
    )
  }

  if (auth.error !== null) {
    return (
      <main className="page-state page-state--full">
        <ErrorNotice
          error={auth.error}
          onRetry={() => void auth.refresh()}
          title="无法连接账号服务"
        />
      </main>
    )
  }

  return (
    <Routes>
      <Route element={<AuthPage mode="login" />} path="/login" />
      <Route element={<AuthPage mode="register" />} path="/register" />
      <Route
        element={
          auth.user === null ? (
            <Navigate replace state={{ from: location.pathname }} to="/login" />
          ) : auth.user.role !== 'admin' ? (
            <Navigate replace to="/" />
          ) : (
            <AdminShell courseId={courseId} onLeaveCourse={leaveCourse} />
          )
        }
        path="/admin/*"
      />
      <Route
        element={
          auth.user === null ? (
            <Navigate replace state={{ from: location.pathname }} to="/login" />
          ) : courseId ? (
            <WorkspaceShell courseId={courseId} onLeaveCourse={leaveCourse} />
          ) : (
            <CourseSetup
              account={auth.user}
              onSelected={selectCourse}
              onSignOut={async () => {
                leaveCourse()
                await auth.logout()
              }}
            />
          )
        }
        path="*"
      />
    </Routes>
  )
}
