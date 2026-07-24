import { useMutation, useQuery } from '@tanstack/react-query'
import { BookOpen, BookPlus, ChevronRight, LoaderCircle, LogOut } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import { ApiError, studyApi } from '../api/client'
import type { AuthUser } from '../api/types'
import { ErrorNotice } from '../components/ui/ErrorNotice'
import { IconButton } from '../components/ui/IconButton'

interface CourseSetupProps {
  account: AuthUser
  onSelected: (courseId: string) => void
  onSignOut: () => Promise<void>
}

export function CourseSetup({ account, onSelected, onSignOut }: CourseSetupProps) {
  const [title, setTitle] = useState('')
  const coursesQuery = useQuery({
    queryKey: ['courses'],
    queryFn: () => studyApi.listCourses(),
  })
  const createCourse = useMutation({
    mutationFn: () => studyApi.createCourse(title.trim()),
    onSuccess: (course) => onSelected(course.id),
  })

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (title.trim()) createCourse.mutate()
  }

  const errorMessage = createCourse.error
    ? createCourse.error instanceof ApiError
      ? createCourse.error.problem.title
      : '无法创建课程，请检查本地 API。'
    : null

  return (
    <main className="course-setup">
      <section className="course-setup__panel">
        <header className="course-setup__header">
          <div className="course-setup__title">
            <span className="course-setup__icon" aria-hidden="true">
              <BookOpen size={23} />
            </span>
            <div>
              <p className="section-kicker">课程工作区</p>
              <h1>选择课程</h1>
            </div>
          </div>
          <div className="course-setup__account">
            <span>
              <strong>{account.display_name}</strong>
              <small>{account.email}</small>
            </span>
            <IconButton label="退出登录" onClick={() => void onSignOut()} size="small">
              <LogOut aria-hidden="true" size={17} />
            </IconButton>
          </div>
        </header>

        {coursesQuery.isError ? (
          <ErrorNotice
            error={coursesQuery.error}
            onRetry={() => void coursesQuery.refetch()}
            title="无法读取课程"
          />
        ) : coursesQuery.isLoading ? (
          <div className="loading-state loading-state--inline">
            <LoaderCircle aria-hidden="true" className="spin" size={17} />
            <span>读取课程</span>
          </div>
        ) : coursesQuery.data?.length ? (
          <div className="course-setup__list" aria-label="已有课程">
            {coursesQuery.data.map((course) => (
              <button key={course.id} onClick={() => onSelected(course.id)} type="button">
                <span className="course-setup__course-icon" aria-hidden="true">
                  <BookOpen size={18} />
                </span>
                <span>
                  <strong>{course.title}</strong>
                  <small>{course.lifecycle === 'active' ? '正在复习' : course.lifecycle}</small>
                </span>
                <ChevronRight aria-hidden="true" size={18} />
              </button>
            ))}
          </div>
        ) : (
          <p className="course-setup__empty">还没有课程，从下面创建一个工作区。</p>
        )}

        <form className="course-setup__form" onSubmit={onSubmit}>
          <div>
            <p className="section-kicker">本地课程</p>
            <h2>创建复习工作区</h2>
          </div>
          <label htmlFor="course-title">课程名称</label>
          <input
            id="course-title"
            maxLength={255}
            onChange={(event) => setTitle(event.target.value)}
            value={title}
          />
          {errorMessage ? (
            <p className="form-error" role="alert">
              {errorMessage}
            </p>
          ) : null}
          <button
            className="button button--primary"
            disabled={!title.trim() || createCourse.isPending}
            type="submit"
          >
            {createCourse.isPending ? (
              <LoaderCircle aria-hidden="true" className="spin" size={17} />
            ) : (
              <BookPlus aria-hidden="true" size={17} />
            )}
            创建课程
          </button>
        </form>
      </section>
    </main>
  )
}
