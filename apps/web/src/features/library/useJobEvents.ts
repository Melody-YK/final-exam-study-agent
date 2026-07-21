import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { studyApi } from '../../api/client'
import type { JobEventData } from '../../api/types'

export type EventConnectionState = 'idle' | 'connected' | 'reconnecting'

export function useJobEvents(courseId: string, jobIds: string[]): EventConnectionState {
  const queryClient = useQueryClient()
  const [streamState, setStreamState] = useState<{
    key: string
    connection: Exclude<EventConnectionState, 'idle'>
  }>({ key: '', connection: 'reconnecting' })
  const key = [...new Set(jobIds)].sort().join(',')

  useEffect(() => {
    if (!key) return
    const closeStreams = key.split(',').map((jobId) =>
      studyApi.subscribe<JobEventData>(
        `/parse-jobs/${jobId}/events`,
        () => {
          setStreamState({ key, connection: 'connected' })
          void queryClient.invalidateQueries({ queryKey: ['documents', courseId] })
        },
        () => setStreamState({ key, connection: 'reconnecting' }),
        () => setStreamState({ key, connection: 'connected' }),
      ),
    )
    return () => closeStreams.forEach((close) => close())
  }, [courseId, key, queryClient])

  if (!key) return 'idle'
  return streamState.key === key ? streamState.connection : 'reconnecting'
}
