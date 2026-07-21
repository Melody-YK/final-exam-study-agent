import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderOptions } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement, ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'

import type { Course, RuntimeCapabilities } from '../api/types'
import { WorkspaceContext, type WorkspaceState } from '../app/WorkspaceContext'

export const availableCapabilities: RuntimeCapabilities = {
  provider: { status: 'available', label: '回答模型可用' },
  embedding: { status: 'available', label: 'Embedding 可用' },
  native_parser: { status: 'available', label: '原生解析可用' },
  ocr_parser: { status: 'worker_required', label: '需要本地 OCR Worker' },
  demo_lab_enabled: true,
}

const course: Course = { id: 'course-1', title: '操作系统', lifecycle: 'active' }

interface WorkspaceRenderOptions extends Omit<RenderOptions, 'wrapper'> {
  route?: string
  workspace?: Partial<WorkspaceState>
}

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { gcTime: Infinity, retry: false },
      mutations: { retry: false },
    },
  })
}

export function renderInWorkspace(
  ui: ReactElement,
  { route = '/', workspace, ...options }: WorkspaceRenderOptions = {},
) {
  const queryClient = createTestQueryClient()
  const state: WorkspaceState = {
    courseId: course.id,
    course,
    capabilities: availableCapabilities,
    capabilitiesLoading: false,
    capabilitiesError: false,
    ...workspace,
  }

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>
          <WorkspaceContext.Provider value={state}>{children}</WorkspaceContext.Provider>
        </MemoryRouter>
      </QueryClientProvider>
    )
  }

  return {
    queryClient,
    user: userEvent.setup(),
    ...render(ui, { wrapper: Wrapper, ...options }),
  }
}
