import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, Copy, Settings2, TicketPlus, UserRoundCog } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import type { AdminAccount, AdminAccountUpdate } from '../../api/types'
import { studyApi } from '../../api/client'
import { useAuth } from '../../app/auth'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { IconButton } from '../../components/ui/IconButton'
import { Modal } from '../../components/ui/Modal'
import { StatusBadge } from '../../components/ui/StatusBadge'

type AccessTab = 'users' | 'invitations'

const invitationLabels = {
  available: { label: '可使用', tone: 'success' },
  used: { label: '已使用', tone: 'neutral' },
  revoked: { label: '已撤销', tone: 'danger' },
  expired: { label: '已过期', tone: 'warning' },
} as const

export function AdminUsersPage() {
  const [tab, setTab] = useState<AccessTab>('users')

  return (
    <section className="admin-page">
      <header className="admin-page__header">
        <div>
          <p className="section-kicker">ACCESS</p>
          <h2>用户与访问</h2>
          <p>管理账号权限、使用状态和注册邀请。</p>
        </div>
      </header>

      <div aria-label="访问管理视图" className="admin-tabs" role="tablist">
        <button
          aria-selected={tab === 'users'}
          onClick={() => setTab('users')}
          role="tab"
          type="button"
        >
          用户
        </button>
        <button
          aria-selected={tab === 'invitations'}
          onClick={() => setTab('invitations')}
          role="tab"
          type="button"
        >
          邀请码
        </button>
      </div>

      {tab === 'users' ? <UsersTable /> : <InvitationsTable />}
    </section>
  )
}

function UsersTable() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [target, setTarget] = useState<AdminAccount | null>(null)
  const [role, setRole] = useState<AdminAccount['role']>('user')
  const [status, setStatus] = useState<AdminAccount['status']>('active')
  const [adminNote, setAdminNote] = useState('')
  const usersQuery = useQuery({
    queryKey: ['admin', 'users'],
    queryFn: () => studyApi.listAdminUsers(),
  })
  const updateUser = useMutation({
    mutationFn: ({ id, input }: { id: string; input: AdminAccountUpdate }) =>
      studyApi.updateAdminUser(id, input),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['admin', 'users'] }),
        queryClient.invalidateQueries({ queryKey: ['admin', 'diagnostics'] }),
      ])
      setTarget(null)
    },
  })

  const openEditor = (account: AdminAccount) => {
    updateUser.reset()
    setTarget(account)
    setRole(account.role)
    setStatus(account.status)
    setAdminNote(account.admin_note ?? '')
  }
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (target === null) return
    const isCurrentAccount = target.id === user?.id
    updateUser.mutate({
      id: target.id,
      input: {
        ...(isCurrentAccount ? {} : { role, status }),
        admin_note: adminNote.trim() || null,
      },
    })
  }

  return (
    <>
      {usersQuery.isError ? (
        <ErrorNotice
          error={usersQuery.error}
          onRetry={() => void usersQuery.refetch()}
          title="无法读取用户"
        />
      ) : null}
      <div className="admin-table-wrap" aria-busy={usersQuery.isLoading}>
        <table className="admin-table admin-table--users">
          <thead>
            <tr>
              <th>用户</th>
              <th>角色</th>
              <th>状态</th>
              <th>管理备注</th>
              <th className="table-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            {(usersQuery.data?.items ?? []).map((account) => (
              <tr key={account.id}>
                <td>
                  <strong>
                    {account.display_name}
                    {account.id === user?.id ? (
                      <small className="current-account">当前账号</small>
                    ) : null}
                  </strong>
                  <small>{account.email}</small>
                </td>
                <td>
                  <span className={`role-badge role-badge--${account.role}`}>
                    {account.role === 'admin' ? '管理员' : '用户'}
                  </span>
                </td>
                <td>
                  <StatusBadge tone={account.status === 'active' ? 'success' : 'danger'}>
                    {account.status === 'active' ? '正常' : '已停用'}
                  </StatusBadge>
                </td>
                <td className="admin-note-cell">{account.admin_note ?? '—'}</td>
                <td className="table-actions">
                  <IconButton
                    label={`管理 ${account.display_name}`}
                    onClick={() => openEditor(account)}
                    size="small"
                  >
                    <Settings2 aria-hidden="true" size={16} />
                  </IconButton>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!usersQuery.isLoading && usersQuery.data?.items.length === 0 ? (
          <p className="admin-table__empty">暂无用户</p>
        ) : null}
      </div>

      <Modal
        description={
          target?.id === user?.id
            ? '当前账号只能修改管理备注，不能更改自己的角色或状态。'
            : '账号停用后，现有登录会话会立即失效。'
        }
        footer={
          <>
            <button
              className="button button--secondary"
              onClick={() => setTarget(null)}
              type="button"
            >
              取消
            </button>
            <button
              className="button button--primary"
              disabled={updateUser.isPending}
              form="admin-user-form"
              type="submit"
            >
              {updateUser.isPending ? '保存中...' : '保存'}
            </button>
          </>
        }
        onClose={() => {
          if (!updateUser.isPending) setTarget(null)
        }}
        open={target !== null}
        title={target ? `管理 ${target.display_name}` : '管理用户'}
      >
        <form id="admin-user-form" onSubmit={submit}>
          <label className="field">
            <span>角色</span>
            <select
              disabled={target?.id === user?.id}
              onChange={(event) => setRole(event.target.value as AdminAccount['role'])}
              value={role}
            >
              <option value="user">用户</option>
              <option value="admin">管理员</option>
            </select>
          </label>
          <label className="field">
            <span>账号状态</span>
            <select
              disabled={target?.id === user?.id}
              onChange={(event) => setStatus(event.target.value as AdminAccount['status'])}
              value={status}
            >
              <option value="active">正常</option>
              <option value="suspended">停用</option>
            </select>
          </label>
          <label className="field">
            <span>管理备注</span>
            <textarea
              maxLength={1000}
              onChange={(event) => setAdminNote(event.target.value)}
              placeholder="仅管理员可见"
              rows={4}
              value={adminNote}
            />
          </label>
          {updateUser.isError ? (
            <ErrorNotice error={updateUser.error} title="用户设置未保存" />
          ) : null}
        </form>
      </Modal>
    </>
  )
}

function InvitationsTable() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [expiresInDays, setExpiresInDays] = useState(7)
  const invitationsQuery = useQuery({
    queryKey: ['admin', 'invitations'],
    queryFn: () => studyApi.listAdminInvitations(),
  })
  const diagnosticsQuery = useQuery({
    queryKey: ['admin', 'diagnostics'],
    queryFn: () => studyApi.adminDiagnostics(),
  })
  const availableSeats = diagnosticsQuery.data?.available_account_seats
  const capacityReached = availableSeats === 0
  const refreshInvitationCapacity = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['admin', 'invitations'] }),
      queryClient.invalidateQueries({ queryKey: ['admin', 'diagnostics'] }),
    ])
  const createInvitation = useMutation({
    mutationFn: () => studyApi.createAdminInvitation(expiresInDays),
    onSuccess: refreshInvitationCapacity,
    onError: refreshInvitationCapacity,
  })
  const revokeInvitation = useMutation({
    mutationFn: (id: string) => studyApi.revokeAdminInvitation(id),
    onSuccess: refreshInvitationCapacity,
  })

  const closeCreate = () => {
    if (createInvitation.isPending) return
    setCreateOpen(false)
    createInvitation.reset()
  }

  return (
    <>
      <div className="admin-table-toolbar">
        <div className="admin-table-toolbar__summary">
          <p>邀请码单次有效，使用后自动失效。</p>
          <div
            aria-busy={diagnosticsQuery.isLoading}
            aria-label="账号容量"
            aria-live="polite"
            className="admin-capacity-summary"
          >
            {diagnosticsQuery.data ? (
              <>
                <span>
                  {`活跃账号 ${diagnosticsQuery.data.active_accounts} / ${diagnosticsQuery.data.account_capacity}`}
                </span>
                <span>{`剩余席位 ${diagnosticsQuery.data.available_account_seats}`}</span>
              </>
            ) : diagnosticsQuery.isError ? (
              <span>账号容量暂不可用</span>
            ) : (
              <span>账号容量读取中</span>
            )}
          </div>
        </div>
        <button
          className="button button--primary"
          disabled={capacityReached}
          onClick={() => {
            if (capacityReached) return
            createInvitation.reset()
            setCreateOpen(true)
          }}
          title={capacityReached ? '账号容量已满' : undefined}
          type="button"
        >
          <TicketPlus aria-hidden="true" size={16} />
          创建邀请码
        </button>
      </div>
      {invitationsQuery.isError ? (
        <ErrorNotice
          error={invitationsQuery.error}
          onRetry={() => void invitationsQuery.refetch()}
          title="无法读取邀请码"
        />
      ) : null}
      {diagnosticsQuery.isError ? (
        <ErrorNotice
          error={diagnosticsQuery.error}
          onRetry={() => void diagnosticsQuery.refetch()}
          title="无法读取账号容量"
        />
      ) : null}
      {revokeInvitation.isError ? (
        <ErrorNotice error={revokeInvitation.error} title="邀请码未撤销" />
      ) : null}
      <div className="admin-table-wrap" aria-busy={invitationsQuery.isLoading}>
        <table className="admin-table">
          <thead>
            <tr>
              <th>创建时间</th>
              <th>过期时间</th>
              <th>状态</th>
              <th className="table-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            {(invitationsQuery.data?.items ?? []).map((invitation) => {
              const presentation = invitationLabels[invitation.status]
              return (
                <tr key={invitation.id}>
                  <td>{formatDateTime(invitation.created_at)}</td>
                  <td>{formatDateTime(invitation.expires_at)}</td>
                  <td>
                    <StatusBadge tone={presentation.tone}>{presentation.label}</StatusBadge>
                  </td>
                  <td className="table-actions">
                    {invitation.status === 'available' ? (
                      <IconButton
                        disabled={revokeInvitation.isPending}
                        label="撤销邀请码"
                        onClick={() => revokeInvitation.mutate(invitation.id)}
                        size="small"
                      >
                        <Ban aria-hidden="true" size={16} />
                      </IconButton>
                    ) : null}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!invitationsQuery.isLoading && invitationsQuery.data?.items.length === 0 ? (
          <p className="admin-table__empty">暂无邀请码</p>
        ) : null}
      </div>

      <Modal
        description="邀请码明文只显示这一次，请在关闭前发送给受邀用户。"
        footer={
          createInvitation.data ? (
            <button className="button button--primary" onClick={closeCreate} type="button">
              完成
            </button>
          ) : (
            <>
              <button className="button button--secondary" onClick={closeCreate} type="button">
                取消
              </button>
              <button
                className="button button--primary"
                disabled={createInvitation.isPending || capacityReached}
                onClick={() => {
                  if (!capacityReached) createInvitation.mutate()
                }}
                title={capacityReached ? '账号容量已满' : undefined}
                type="button"
              >
                {createInvitation.isPending ? '创建中...' : '创建'}
              </button>
            </>
          )
        }
        onClose={closeCreate}
        open={createOpen}
        title="创建邀请码"
      >
        {createInvitation.data ? (
          <div className="invitation-code">
            <UserRoundCog aria-hidden="true" size={20} />
            <code>{createInvitation.data.code}</code>
            <IconButton
              label="复制邀请码"
              onClick={() => void navigator.clipboard.writeText(createInvitation.data.code)}
              size="small"
            >
              <Copy aria-hidden="true" size={16} />
            </IconButton>
          </div>
        ) : (
          <label className="field">
            <span>有效期</span>
            <select
              onChange={(event) => setExpiresInDays(Number(event.target.value))}
              value={expiresInDays}
            >
              <option value={1}>1 天</option>
              <option value={7}>7 天</option>
              <option value={14}>14 天</option>
              <option value={30}>30 天</option>
            </select>
          </label>
        )}
        {createInvitation.isError ? (
          <ErrorNotice error={createInvitation.error} title="邀请码未创建" />
        ) : null}
      </Modal>
    </>
  )
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}
