import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { studyApi } from '../../api/client'
import type { JobEventData } from '../../api/types'

export type EventConnectionState = 'idle' | 'connected' | 'reconnecting'

export type JobTerminalEventType =
  | 'job.succeeded'
  | 'job.partial_failed'
  | 'job.failed'
  | 'job.cancelled'

export interface JobTerminalEvent {
  jobId: string
  eventType: JobTerminalEventType
  data: JobEventData
}

const TERMINAL_EVENT_TYPES: ReadonlySet<string> = new Set([
  'job.succeeded',
  'job.partial_failed',
  'job.failed',
  'job.cancelled',
])

export function useJobEvents(
  courseId: string,
  jobIds: string[],
  onTerminal?: (event: JobTerminalEvent) => void,
): EventConnectionState {
  const queryClient = useQueryClient()
  const onTerminalRef = useRef(onTerminal)
  const seenTerminalEvents = useRef(new Set<string>())
  const [streamState, setStreamState] = useState<{
    key: string
    connection: Exclude<EventConnectionState, 'idle'>
  }>({ key: '', connection: 'reconnecting' })
  const key = [...new Set(jobIds)].sort().join(',')

  useEffect(() => {
    onTerminalRef.current = onTerminal
  }, [onTerminal])

  useEffect(() => {
    if (!key) return
    const closeStreams = key.split(',').map((jobId) =>
      studyApi.subscribe<JobEventData>(
        `/parse-jobs/${jobId}/events`,
        (event) => {
          setStreamState({ key, connection: 'connected' })
          void queryClient.invalidateQueries({ queryKey: ['documents', courseId] })

          if (
            event.event_type &&
            TERMINAL_EVENT_TYPES.has(event.event_type) &&
            !seenTerminalEvents.current.has(`${jobId}:${event.sequence}`)
          ) {
            seenTerminalEvents.current.add(`${jobId}:${event.sequence}`)
            onTerminalRef.current?.({
              jobId,
              eventType: event.event_type as JobTerminalEventType,
              data: event.data,
            })
          }
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
