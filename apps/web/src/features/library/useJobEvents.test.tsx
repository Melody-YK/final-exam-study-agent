import { QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { studyApi } from '../../api/client'
import { createTestQueryClient } from '../../test/render'
import { useJobEvents } from './useJobEvents'

describe('useJobEvents', () => {
  it('tracks open, reconnecting, event invalidation, and cleanup by job key', () => {
    const queryClient = createTestQueryClient()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const close = vi.fn()
    let openStream: () => void = () => undefined
    let failStream: () => void = () => undefined
    let emitEvent: () => void = () => undefined
    vi.spyOn(studyApi, 'subscribe').mockImplementation((_path, onEvent, onError, onOpen) => {
      openStream = () => onOpen?.()
      failStream = () => onError?.()
      emitEvent = () =>
        onEvent({
          stream_version: '1',
          sequence: 1,
          occurred_at: '2026-07-19T04:00:00Z',
          trace_id: 'trace-1',
          data: { status: 'parsing' },
        })
      return close
    })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result, rerender } = renderHook(
      ({ jobs }) => useJobEvents('course-1', jobs),
      { initialProps: { jobs: ['job-1'] }, wrapper },
    )

    expect(result.current).toBe('reconnecting')
    act(openStream)
    expect(result.current).toBe('connected')
    act(failStream)
    expect(result.current).toBe('reconnecting')
    act(emitEvent)
    expect(result.current).toBe('connected')
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['documents', 'course-1'] })

    rerender({ jobs: [] })
    expect(result.current).toBe('idle')
    expect(close).toHaveBeenCalledOnce()
  })
})
