import { useMutation } from '@tanstack/react-query'
import { ArrowRight, BookOpenCheck, LockKeyhole, UserPlus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'

import { studyApi } from '../../api/client'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { useAuth } from '../../app/auth'

interface AuthPageProps {
  mode: 'login' | 'register'
}

interface RedirectState {
  from?: string
}

export function AuthPage({ mode }: AuthPageProps) {
  const { user, setCurrentUser } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const isRegister = mode === 'register'
  const destination = (location.state as RedirectState | null)?.from ?? '/'

  const authMutation = useMutation({
    mutationFn: () =>
      isRegister
        ? studyApi.register({
            email: email.trim(),
            password,
            display_name: displayName.trim(),
            ...(inviteCode.trim() ? { invite_code: inviteCode.trim() } : {}),
          })
        : studyApi.login({ email: email.trim(), password }),
    onSuccess: (account) => {
      setCurrentUser(account)
      navigate(destination, { replace: true })
    },
  })

  if (user !== null) return <Navigate replace to="/" />

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    authMutation.mutate()
  }

  return (
    <main className="auth-page">
      <section className="auth-brand" aria-label="Finals Desk">
        <span className="auth-brand__mark" aria-hidden="true">
          FD
        </span>
        <div>
          <p>FINALS DESK</p>
          <h1>把课程资料变成可复习的知识。</h1>
          <div className="auth-brand__signal">
            <BookOpenCheck aria-hidden="true" size={18} />
            <span>资料、问答、图谱和笔记统一在课程工作区</span>
          </div>
        </div>
      </section>

      <section className="auth-panel">
        <div className="auth-form-wrap">
          <div className="auth-heading">
            <span aria-hidden="true" className="auth-heading__icon">
              {isRegister ? <UserPlus size={20} /> : <LockKeyhole size={20} />}
            </span>
            <div>
              <p>{isRegister ? '创建账号' : '欢迎回来'}</p>
              <h2>{isRegister ? '开始你的复习工作区' : '登录 Finals Desk'}</h2>
            </div>
          </div>

          <form className="auth-form" onSubmit={submit}>
            {isRegister ? (
              <>
                <label>
                  <span>邀请码</span>
                  <input
                    autoComplete="off"
                    maxLength={512}
                    onChange={(event) => setInviteCode(event.target.value)}
                    placeholder="由管理员提供"
                    value={inviteCode}
                  />
                </label>
                <label>
                  <span>姓名</span>
                  <input
                    autoComplete="name"
                    maxLength={100}
                    onChange={(event) => setDisplayName(event.target.value)}
                    placeholder="你的称呼"
                    required
                    value={displayName}
                  />
                </label>
              </>
            ) : null}
            <label>
              <span>邮箱</span>
              <input
                autoComplete="email"
                inputMode="email"
                maxLength={254}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="name@example.com"
                required
                type="email"
                value={email}
              />
            </label>
            <label>
              <span>密码</span>
              <input
                autoComplete={isRegister ? 'new-password' : 'current-password'}
                minLength={8}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="至少 8 位"
                required
                type="password"
                value={password}
              />
            </label>

            {authMutation.isError ? (
              <ErrorNotice
                error={authMutation.error}
                title={isRegister ? '注册未完成' : '登录未完成'}
              />
            ) : null}

            <button
              className="button button--primary auth-submit"
              disabled={authMutation.isPending}
              type="submit"
            >
              {authMutation.isPending ? '正在提交...' : isRegister ? '创建账号' : '登录'}
              <ArrowRight aria-hidden="true" size={17} />
            </button>
          </form>

          <p className="auth-switch">
            {isRegister ? '已有账号？' : '还没有账号？'}{' '}
            <Link to={isRegister ? '/login' : '/register'}>
              {isRegister ? '直接登录' : '注册账号'}
            </Link>
          </p>
        </div>
      </section>
    </main>
  )
}
