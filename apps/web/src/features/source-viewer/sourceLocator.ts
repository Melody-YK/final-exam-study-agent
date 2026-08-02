import type { SourceLocator } from '../../api/types'

export function formatSourceLocator(locator: SourceLocator, sectionPath?: string[]): string {
  if (locator.kind === 'slide') return `幻灯片 ${locator.ordinal}`
  if (locator.kind === 'section') {
    const path = sectionPath?.filter((part) => part.trim()).join(' / ')
    return path || `章节 ${locator.ordinal}`
  }
  return `第 ${locator.ordinal} 页`
}
