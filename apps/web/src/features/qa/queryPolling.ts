import type { QuerySnapshot } from '../../api/types'

export type QueryStreamConnection = 'connecting' | 'open' | 'reconnecting'

export function isTerminal(snapshot: QuerySnapshot | undefined): boolean {
  return Boolean(
    snapshot?.answer || ['failed', 'abstained', 'answered'].includes(snapshot?.status ?? ''),
  )
}

export function queryRefetchInterval(
  snapshot: QuerySnapshot | undefined,
  connection: QueryStreamConnection,
): false | 10_000 {
  return isTerminal(snapshot) || connection === 'open' ? false : 10_000
}
