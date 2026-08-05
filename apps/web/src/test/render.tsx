import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderOptions } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement, ReactNode } from 'react'
import { MemoryRouter } from 'react-router'

import type { Course, RuntimeCapabilities } from '../api/types'
import { WorkspaceContext, type WorkspaceState } from '../app/WorkspaceContext'

export const availableCapabilities: RuntimeCapabilities = {
  provider: { status: 'available', label: '回答模型可用' },
  embedding: { status: 'available', label: 'Embedding 可用' },
  vision: { status: 'available', label: '多模态复核可用' },
  native_parser: { status: 'available', label: '原生解析可用' },
  ocr_parser: { status: 'worker_required', label: '需要本地 OCR Worker' },
  mineru_parser: { status: 'worker_required', label: '需要自建 MinerU 服务' },
  demo_lab_enabled: true,
  note_workflow: {
    enabled: true,
    generation: { status: 'available', label: '异步笔记生成已就绪' },
    export: { status: 'available', label: 'DOCX 导出已就绪' },
    eta: { status: 'available', label: '数值 ETA 已启用' },
  },
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
