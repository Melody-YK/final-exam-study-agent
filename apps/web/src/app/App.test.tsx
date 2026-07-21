import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { studyApi } from '../api/client'
import { availableCapabilities } from '../test/render'
import { App } from './App'

describe('App', () => {
  it('creates a course and opens the API-driven study workspace', async () => {
    const user = userEvent.setup()
    vi.spyOn(studyApi, 'createCourse').mockResolvedValue({
      id: 'course-1',
      title: '操作系统',
      lifecycle: 'active',
    })
    vi.spyOn(studyApi, 'getCourse').mockResolvedValue({
      id: 'course-1',
      title: '操作系统',
      lifecycle: 'active',
    })
    vi.spyOn(studyApi, 'capabilities').mockResolvedValue(availableCapabilities)
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([])

    render(<App />)

    expect(screen.getByRole('heading', { name: '创建复习工作区' })).toBeInTheDocument()
    await user.type(screen.getByLabelText('课程名称'), '操作系统')
    await user.click(screen.getByRole('button', { name: '创建课程' }))

    expect(await screen.findByRole('heading', { name: '操作系统' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: '学习视图' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: '移动学习视图' })).toBeInTheDocument()
    expect(localStorage.getItem('study-agent.course-id')).toBe('course-1')
    expect(studyApi.createCourse).toHaveBeenCalledWith('操作系统')
  })
})
