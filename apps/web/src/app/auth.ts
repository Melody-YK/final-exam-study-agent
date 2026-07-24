import { createContext, useContext } from 'react'

import type { AuthUser } from '../api/types'

export const AUTH_QUERY_KEY = ['auth', 'me'] as const

export interface AuthState {
  user: AuthUser | null
  loading: boolean
  error: unknown
  refresh: () => Promise<unknown>
  setCurrentUser: (user: AuthUser) => void
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const state = useContext(AuthContext)
  if (state === null) throw new Error('AuthContext is unavailable')
  return state
}
