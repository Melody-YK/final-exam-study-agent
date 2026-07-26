import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'

import { ApiError, studyApi } from '../../api/client'
import type { AuthState } from '../../app/auth'
import { AuthContext } from '../../app/auth'
import { problem } from '../../test/fixtures'
import { createTestQueryClient } from '../../test/render'
import { AdminUsersPage } from './AdminUsersPage'

const currentAdmin = {
  id: 'account-admin',
  email: 'admin@example.com',
  display_name: '本地管理员',
  role: 'admin' as const,
}

const adminAccount = {
  ...currentAdmin,
  status: 'active' as const,
  admin_note: '系统负责人',
  created_at: '2026-07-20T08:00:00Z',
}

const studentAccount = {
  id: 'account-student',
  email: 'student@example.com',
  display_name: '复习同学',
  role: 'user' as const,
  status: 'active' as const,
  admin_note: null,
  created_at: '2026-07-21T08:00:00Z',
}

function adminDiagnostics(
  overrides: Partial<{
    account_capacity: number
    active_accounts: number
    available_account_seats: number
  }> = {},
) {
  return {
    active_accounts: 2,
    account_capacity: 10,
    available_account_seats: 7,
    totals: {
      accounts: 2,
      active_sessions: 1,
      courses: 1,
      documents: 3,
      notes: 4,
    },
    runtime: {
      app_mode: 'local' as const,
      database: 'postgresql' as const,
      demo_lab_enabled: true,
    },
    ...overrides,
  }
}

function renderPage() {
  const queryClient = createTestQueryClient()
  const auth: AuthState = {
    user: currentAdmin,
    loading: false,
    error: null,
    refresh: vi.fn(),
    setCurrentUser: vi.fn(),
    logout: vi.fn(),
  }

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AuthContext.Provider value={auth}>{children}</AuthContext.Provider>
        </MemoryRouter>
      </QueryClientProvider>
    )
  }

  return {
    user: userEvent.setup(),
    ...render(<AdminUsersPage />, { wrapper: Wrapper }),
  }
}

describe('AdminUsersPage', () => {
  it('saves another users role, status, and bounded admin note', async () => {
    vi.spyOn(studyApi, 'listAdminUsers').mockResolvedValue({
      items: [adminAccount, studentAccount],
    })
    const updateUser = vi.spyOn(studyApi, 'updateAdminUser').mockResolvedValue({
      ...studentAccount,
      role: 'admin',
      status: 'suspended',
      admin_note: '暂停使用，等待资料确认',
    })
    const { user } = renderPage()

    await user.click(await screen.findByRole('button', { name: '管理 复习同学' }))
    const dialog = screen.getByRole('dialog', { name: '管理 复习同学' })
    await user.selectOptions(within(dialog).getByLabelText('角色'), 'admin')
    await user.selectOptions(within(dialog).getByLabelText('账号状态'), 'suspended')
    await user.type(within(dialog).getByLabelText('管理备注'), '暂停使用，等待资料确认')
    await user.click(within(dialog).getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(updateUser).toHaveBeenCalledWith('account-student', {
        role: 'admin',
        status: 'suspended',
        admin_note: '暂停使用，等待资料确认',
      }),
    )
  })

  it('disables role and status controls for the current account', async () => {
    vi.spyOn(studyApi, 'listAdminUsers').mockResolvedValue({
      items: [adminAccount, studentAccount],
    })
    const updateUser = vi
      .spyOn(studyApi, 'updateAdminUser')
      .mockResolvedValue({ ...adminAccount, admin_note: '轮值管理员' })
    const { user } = renderPage()

    await user.click(await screen.findByRole('button', { name: '管理 本地管理员' }))
    const dialog = screen.getByRole('dialog', { name: '管理 本地管理员' })
    expect(within(dialog).getByLabelText('角色')).toBeDisabled()
    expect(within(dialog).getByLabelText('账号状态')).toBeDisabled()
    await user.clear(within(dialog).getByLabelText('管理备注'))
    await user.type(within(dialog).getByLabelText('管理备注'), '轮值管理员')
    await user.click(within(dialog).getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(updateUser).toHaveBeenCalledWith('account-admin', {
        admin_note: '轮值管理员',
      }),
    )
  })

  it('shows a newly created invitation code only in its creation dialog and revokes an available code', async () => {
    const availableInvitation = {
      id: 'invitation-existing',
      created_by_account_id: 'account-admin',
      used_by_account_id: null,
      status: 'available' as const,
      created_at: '2026-07-23T08:00:00Z',
      expires_at: '2026-07-30T08:00:00Z',
      used_at: null,
      revoked_at: null,
    }
    vi.spyOn(studyApi, 'listAdminUsers').mockResolvedValue({
      items: [adminAccount],
    })
    vi.spyOn(studyApi, 'listAdminInvitations').mockResolvedValue({
      items: [availableInvitation],
    })
    const diagnostics = vi
      .spyOn(studyApi, 'adminDiagnostics')
      .mockResolvedValue(adminDiagnostics())
    const revokeInvitation = vi.spyOn(studyApi, 'revokeAdminInvitation').mockResolvedValue()
    const createInvitation = vi.spyOn(studyApi, 'createAdminInvitation').mockResolvedValue({
      ...availableInvitation,
      id: 'invitation-new',
      code: 'invite-code-plaintext-123456',
    })
    const { user } = renderPage()

    await user.click(screen.getByRole('tab', { name: '邀请码' }))
    expect(await screen.findByLabelText('账号容量')).toHaveTextContent('活跃账号 2 / 10')
    expect(screen.getByLabelText('账号容量')).toHaveTextContent('剩余席位 7')
    await user.click(await screen.findByRole('button', { name: '撤销邀请码' }))
    await waitFor(() => expect(revokeInvitation).toHaveBeenCalledWith('invitation-existing'))
    await waitFor(() => expect(diagnostics).toHaveBeenCalledTimes(2))

    await user.click(screen.getByRole('button', { name: '创建邀请码' }))
    const createDialog = screen.getByRole('dialog', { name: '创建邀请码' })
    await user.selectOptions(within(createDialog).getByLabelText('有效期'), '14')
    await user.click(within(createDialog).getByRole('button', { name: '创建' }))

    expect(await within(createDialog).findByText('invite-code-plaintext-123456')).toBeVisible()
    expect(createInvitation).toHaveBeenCalledWith(14)
    expect(diagnostics).toHaveBeenCalledTimes(3)
    await user.click(within(createDialog).getByRole('button', { name: '完成' }))
    expect(screen.queryByText('invite-code-plaintext-123456')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '创建邀请码' }))
    expect(screen.queryByText('invite-code-plaintext-123456')).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: '创建邀请码' })).toHaveTextContent('有效期')
  })

  it('disables invitation creation when no account seats remain', async () => {
    vi.spyOn(studyApi, 'listAdminUsers').mockResolvedValue({
      items: [adminAccount, studentAccount],
    })
    vi.spyOn(studyApi, 'listAdminInvitations').mockResolvedValue({ items: [] })
    vi.spyOn(studyApi, 'adminDiagnostics').mockResolvedValue(
      adminDiagnostics({ account_capacity: 2, available_account_seats: 0 }),
    )
    const createInvitation = vi.spyOn(studyApi, 'createAdminInvitation')
    const { user } = renderPage()

    await user.click(screen.getByRole('tab', { name: '邀请码' }))

    expect(await screen.findByLabelText('账号容量')).toHaveTextContent('活跃账号 2 / 2')
    expect(screen.getByLabelText('账号容量')).toHaveTextContent('剩余席位 0')
    const createButton = screen.getByRole('button', { name: '创建邀请码' })
    expect(createButton).toBeDisabled()
    await user.click(createButton)
    expect(screen.queryByRole('dialog', { name: '创建邀请码' })).not.toBeInTheDocument()
    expect(createInvitation).not.toHaveBeenCalled()
  })

  it('keeps the dialog open and refreshes capacity after a creation race loses the last seat', async () => {
    vi.spyOn(studyApi, 'listAdminUsers').mockResolvedValue({
      items: [adminAccount, studentAccount],
    })
    vi.spyOn(studyApi, 'listAdminInvitations').mockResolvedValue({ items: [] })
    const diagnostics = vi
      .spyOn(studyApi, 'adminDiagnostics')
      .mockResolvedValueOnce(
        adminDiagnostics({ account_capacity: 3, available_account_seats: 1 }),
      )
      .mockResolvedValue(
        adminDiagnostics({
          account_capacity: 3,
          active_accounts: 3,
          available_account_seats: 0,
        }),
      )
    vi.spyOn(studyApi, 'createAdminInvitation').mockRejectedValue(
      new ApiError(
        problem({
          status: 409,
          code: 'ACCOUNT_CAPACITY_REACHED',
          title: '账号容量已满',
          detail: '最后一个席位已被其他请求占用。',
        }),
      ),
    )
    const { user } = renderPage()

    await user.click(screen.getByRole('tab', { name: '邀请码' }))
    expect(await screen.findByLabelText('账号容量')).toHaveTextContent('剩余席位 1')
    await user.click(screen.getByRole('button', { name: '创建邀请码' }))
    const dialog = screen.getByRole('dialog', { name: '创建邀请码' })
    await user.click(within(dialog).getByRole('button', { name: '创建' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('邀请码未创建')
    expect(within(dialog).getByRole('alert')).toHaveTextContent(
      '最后一个席位已被其他请求占用。',
    )
    await waitFor(() => expect(screen.getByLabelText('账号容量')).toHaveTextContent('剩余席位 0'))
    expect(within(dialog).getByRole('button', { name: '创建' })).toBeDisabled()
    expect(diagnostics).toHaveBeenCalledTimes(2)
  })
})
