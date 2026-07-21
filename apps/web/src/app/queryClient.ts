import { QueryClient } from '@tanstack/react-query'

export function createWorkspaceQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failureCount, error) => {
          if (error instanceof Error && error.name === 'ApiError') {
            return false
          }
          return failureCount < 2
        },
        refetchOnWindowFocus: false,
        staleTime: 5_000,
      },
      mutations: { retry: false },
    },
  })
}
