import { QueryClientProvider } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { BrowserRouter } from 'react-router-dom'

import { CourseSetup } from './CourseSetup'
import { createWorkspaceQueryClient } from './queryClient'
import { WorkspaceShell } from './WorkspaceShell'

const COURSE_STORAGE_KEY = 'study-agent.course-id'

export function App() {
  const queryClient = useMemo(() => createWorkspaceQueryClient(), [])
  const [courseId, setCourseId] = useState(() => localStorage.getItem(COURSE_STORAGE_KEY))

  const selectCourse = (id: string) => {
    localStorage.setItem(COURSE_STORAGE_KEY, id)
    setCourseId(id)
  }

  const leaveCourse = () => {
    localStorage.removeItem(COURSE_STORAGE_KEY)
    queryClient.clear()
    setCourseId(null)
  }

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {courseId ? (
          <WorkspaceShell courseId={courseId} onLeaveCourse={leaveCourse} />
        ) : (
          <CourseSetup onCreated={selectCourse} />
        )}
      </BrowserRouter>
    </QueryClientProvider>
  )
}
