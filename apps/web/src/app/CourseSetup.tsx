import { useMutation } from '@tanstack/react-query'
import { BookPlus, LoaderCircle } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import { ApiError, studyApi } from '../api/client'

interface CourseSetupProps {
  onCreated: (courseId: string) => void
}

export function CourseSetup({ onCreated }: CourseSetupProps) {
  const [title, setTitle] = useState('')
  const createCourse = useMutation({
    mutationFn: () => studyApi.createCourse(title.trim()),
    onSuccess: (course) => onCreated(course.id),
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
      <form className="course-setup__form" onSubmit={onSubmit}>
        <span className="course-setup__icon" aria-hidden="true">
          <BookPlus size={24} />
        </span>
        <div>
          <p className="section-kicker">本地课程</p>
          <h1>创建复习工作区</h1>
        </div>
        <label htmlFor="course-title">课程名称</label>
        <input
          autoFocus
          id="course-title"
          maxLength={255}
          onChange={(event) => setTitle(event.target.value)}
          value={title}
        />
        {errorMessage ? <p className="form-error" role="alert">{errorMessage}</p> : null}
        <button className="button button--primary" disabled={!title.trim() || createCourse.isPending}>
          {createCourse.isPending ? <LoaderCircle aria-hidden="true" className="spin" size={17} /> : null}
          创建课程
        </button>
      </form>
    </main>
  )
}
