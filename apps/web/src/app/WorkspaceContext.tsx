import { createContext, useContext } from 'react'

import type { Course, RuntimeCapabilities } from '../api/types'

export interface WorkspaceState {
  courseId: string
  course: Course | undefined
  capabilities: RuntimeCapabilities | undefined
  capabilitiesLoading: boolean
  capabilitiesError: boolean
}

export const WorkspaceContext = createContext<WorkspaceState | null>(null)

export function useWorkspace(): WorkspaceState {
  const state = useContext(WorkspaceContext)
  if (state === null) throw new Error('WorkspaceContext is unavailable')
  return state
}
