import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { studyApi } from '../api/client'
import { availableCapabilities } from '../test/render'
import { App } from './App'

describe('App', () => {
  it('creates a course and opens the API-driven study workspace', async () => {
    const user = userEvent.setup()
    vi.spyOn(studyApi, 'currentUser').mockResolvedValue({
      id: 'account-1',
      email: 'student@example.com',
      display_name: '复习同学',
      role: 'user',
    })
    vi.spyOn(studyApi, 'listCourses').mockResolvedValue([])
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

    expect(
      await screen.findByRole('heading', { name: '创建复习工作区' }),
    ).toBeInTheDocument()
    await user.type(screen.getByLabelText('课程名称'), '操作系统')
    await user.click(screen.getByRole('button', { name: '创建课程' }))

    expect(await screen.findByRole('heading', { name: '操作系统' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: '学习视图' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: '移动学习视图' })).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: '概念地图' })).toHaveLength(2)
    expect(screen.queryByRole('link', { name: 'Lab' })).not.toBeInTheDocument()
    expect(localStorage.getItem('study-agent.course-id:account-1')).toBe('course-1')
    expect(studyApi.createCourse).toHaveBeenCalledWith('操作系统')
  })

  it('redirects an anonymous visitor to login', async () => {
    vi.spyOn(studyApi, 'currentUser').mockResolvedValue(null)

    render(<App />)

    expect(await screen.findByRole('heading', { name: '登录 Finals Desk' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '注册账号' })).toHaveAttribute('href', '/register')
  })

  it('logs in and opens the course setup for a new user', async () => {
    const user = userEvent.setup()
    vi.spyOn(studyApi, 'currentUser').mockResolvedValue(null)
    vi.spyOn(studyApi, 'login').mockResolvedValue({
      id: 'account-2',
      email: 'new@example.com',
      display_name: '新同学',
      role: 'user',
    })
    vi.spyOn(studyApi, 'listCourses').mockResolvedValue([])

    render(<App />)

    await user.type(await screen.findByLabelText('邮箱'), 'new@example.com')
    await user.type(screen.getByLabelText('密码'), 'correct-password')
    await user.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByRole('heading', { name: '创建复习工作区' })).toBeInTheDocument()
    expect(studyApi.login).toHaveBeenCalledWith({
      email: 'new@example.com',
      password: 'correct-password',
    })
  })

  it('registers an invited user with the provided invite code', async () => {
    window.history.pushState({}, '', '/register')
    const user = userEvent.setup()
    vi.spyOn(studyApi, 'currentUser').mockResolvedValue(null)
    vi.spyOn(studyApi, 'register').mockResolvedValue({
      id: 'account-invited',
      email: 'invited@example.com',
      display_name: '受邀同学',
      role: 'user',
    })
    vi.spyOn(studyApi, 'listCourses').mockResolvedValue([])

    render(<App />)

    await user.type(await screen.findByLabelText('邀请码'), 'invite-code-123456')
    await user.type(screen.getByLabelText('姓名'), '受邀同学')
    await user.type(screen.getByLabelText('邮箱'), 'invited@example.com')
    await user.type(screen.getByLabelText('密码'), 'correct-password')
    await user.click(screen.getByRole('button', { name: '创建账号' }))

    expect(await screen.findByRole('heading', { name: '创建复习工作区' })).toBeInTheDocument()
    expect(studyApi.register).toHaveBeenCalledWith({
      email: 'invited@example.com',
      password: 'correct-password',
      display_name: '受邀同学',
      invite_code: 'invite-code-123456',
    })
    window.history.pushState({}, '', '/')
  })

  it('restores an existing course for a returning user without relying on shared local storage', async () => {
    const user = userEvent.setup()
    vi.spyOn(studyApi, 'currentUser').mockResolvedValue({
      id: 'account-returning',
      email: 'returning@example.com',
      display_name: '复习同学',
      role: 'user',
    })
    vi.spyOn(studyApi, 'listCourses').mockResolvedValue([
      { id: 'course-existing', title: '操作系统', lifecycle: 'active' },
    ])
    vi.spyOn(studyApi, 'getCourse').mockResolvedValue({
      id: 'course-existing',
      title: '操作系统',
      lifecycle: 'active',
    })
    vi.spyOn(studyApi, 'capabilities').mockResolvedValue(availableCapabilities)
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([])

    render(<App />)

    expect(await screen.findByRole('heading', { name: '选择课程' })).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: /操作系统/ }))

    expect(await screen.findByRole('heading', { name: '操作系统' })).toBeInTheDocument()
    expect(localStorage.getItem('study-agent.course-id:account-returning')).toBe('course-existing')
  })

  it('sends an authenticated admin away from learning routes without loading courses', async () => {
    window.history.pushState({}, '', '/notes')
    vi.spyOn(studyApi, 'currentUser').mockResolvedValue({
      id: 'account-admin',
      email: 'admin@example.com',
      display_name: '本地管理员',
      role: 'admin',
    })
    const listCourses = vi.spyOn(studyApi, 'listCourses')
    vi.spyOn(studyApi, 'adminDiagnostics').mockResolvedValue({
      active_accounts: 2,
      account_capacity: 10,
      available_account_seats: 8,
      totals: {
        accounts: 2,
        active_sessions: 1,
        courses: 1,
        documents: 3,
        notes: 4,
      },
      runtime: {
        app_mode: 'local',
        database: 'postgresql',
        demo_lab_enabled: true,
      },
    })

    render(<App />)

    expect(await screen.findByRole('heading', { name: '运行概览' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/admin')
    expect(listCourses).not.toHaveBeenCalled()
    expect(screen.queryByRole('heading', { name: '选择课程' })).not.toBeInTheDocument()
    window.history.pushState({}, '', '/')
  })

  it('opens the management console after an admin logs in', async () => {
    const user = userEvent.setup()
    vi.spyOn(studyApi, 'currentUser').mockResolvedValue(null)
    vi.spyOn(studyApi, 'login').mockResolvedValue({
      id: 'account-admin',
      email: 'admin@example.com',
      display_name: '本地管理员',
      role: 'admin',
    })
    const listCourses = vi.spyOn(studyApi, 'listCourses')
    vi.spyOn(studyApi, 'adminDiagnostics').mockResolvedValue({
      active_accounts: 2,
      account_capacity: 10,
      available_account_seats: 8,
      totals: {
        accounts: 2,
        active_sessions: 1,
        courses: 1,
        documents: 3,
        notes: 4,
      },
      runtime: {
        app_mode: 'local',
        database: 'postgresql',
        demo_lab_enabled: true,
      },
    })

    render(<App />)

    await user.type(await screen.findByLabelText('邮箱'), 'admin@example.com')
    await user.type(screen.getByLabelText('密码'), 'correct-password')
    await user.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByRole('heading', { name: '运行概览' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/admin')
    expect(listCourses).not.toHaveBeenCalled()
  })

  it('shows the management console only to an admin', async () => {
    window.history.pushState({}, '', '/admin')
    vi.spyOn(studyApi, 'currentUser').mockResolvedValue({
      id: 'account-admin',
      email: 'admin@example.com',
      display_name: '本地管理员',
      role: 'admin',
    })
    vi.spyOn(studyApi, 'adminDiagnostics').mockResolvedValue({
      active_accounts: 2,
      account_capacity: 10,
      available_account_seats: 8,
      totals: {
        accounts: 2,
        active_sessions: 1,
        courses: 1,
        documents: 3,
        notes: 4,
      },
      runtime: {
        app_mode: 'local',
        database: 'postgresql',
        demo_lab_enabled: true,
      },
    })

    render(<App />)

    expect(await screen.findByRole('heading', { name: '运行概览' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: '管理视图' })).toBeInTheDocument()
    window.history.pushState({}, '', '/')
  })

  it('clears the signed-in account and selected course on logout', async () => {
    const user = userEvent.setup()
    localStorage.setItem('study-agent.course-id:account-logout', 'course-logout')
    vi.spyOn(studyApi, 'currentUser').mockResolvedValue({
      id: 'account-logout',
      email: 'student@example.com',
      display_name: '复习同学',
      role: 'user',
    })
    vi.spyOn(studyApi, 'getCourse').mockResolvedValue({
      id: 'course-logout',
      title: '操作系统',
      lifecycle: 'active',
    })
    vi.spyOn(studyApi, 'capabilities').mockResolvedValue(availableCapabilities)
    vi.spyOn(studyApi, 'listDocuments').mockResolvedValue([])
    vi.spyOn(studyApi, 'logout').mockResolvedValue()

    render(<App />)

    await user.click(await screen.findByRole('button', { name: '退出登录' }))

    expect(await screen.findByRole('heading', { name: '登录 Finals Desk' })).toBeInTheDocument()
    expect(localStorage.getItem('study-agent.course-id:account-logout')).toBeNull()
    expect(studyApi.logout).toHaveBeenCalledOnce()
  })

  it('does not reuse another accounts selected course', async () => {
    localStorage.setItem('study-agent.course-id:account-admin', 'course-admin')
    vi.spyOn(studyApi, 'currentUser').mockResolvedValue({
      id: 'account-student',
      email: 'student@example.com',
      display_name: '复习同学',
      role: 'user',
    })
    vi.spyOn(studyApi, 'listCourses').mockResolvedValue([])
    const getCourse = vi.spyOn(studyApi, 'getCourse')

    render(<App />)

    expect(
      await screen.findByRole('heading', { name: '创建复习工作区' }),
    ).toBeInTheDocument()
    expect(getCourse).not.toHaveBeenCalled()
  })

  it('allows a user without courses to log out', async () => {
    const user = userEvent.setup()
    vi.spyOn(studyApi, 'currentUser').mockResolvedValue({
      id: 'account-empty',
      email: 'empty@example.com',
      display_name: '新同学',
      role: 'user',
    })
    vi.spyOn(studyApi, 'listCourses').mockResolvedValue([])
    vi.spyOn(studyApi, 'logout').mockResolvedValue()

    render(<App />)

    await user.click(await screen.findByRole('button', { name: '退出登录' }))

    expect(await screen.findByRole('heading', { name: '登录 Finals Desk' })).toBeInTheDocument()
    expect(studyApi.logout).toHaveBeenCalledOnce()
  })
})
