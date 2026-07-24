import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, type ReactNode } from 'react'

import { studyApi } from '../api/client'
import { AUTH_QUERY_KEY, AuthContext, type AuthState } from './auth'

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const currentUserQuery = useQuery({
    queryKey: AUTH_QUERY_KEY,
    queryFn: () => studyApi.currentUser(),
    retry: false,
    staleTime: 30_000,
  })

  const value = useMemo<AuthState>(
    () => ({
      user: currentUserQuery.data ?? null,
      loading: currentUserQuery.isLoading,
      error: currentUserQuery.error,
      refresh: () => currentUserQuery.refetch(),
      setCurrentUser: (user) => queryClient.setQueryData(AUTH_QUERY_KEY, user),
      logout: async () => {
        await studyApi.logout()
        queryClient.removeQueries({
          predicate: (query) => query.queryKey[0] !== 'auth',
        })
        queryClient.setQueryData(AUTH_QUERY_KEY, null)
      },
    }),
    [currentUserQuery, queryClient],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
