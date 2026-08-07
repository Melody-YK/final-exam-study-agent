export interface LearningLaunchState {
  knowledgePointTexts: string[]
  noteId: string
  noteTitle: string
  sourceKeys: string[]
}

interface LearningLaunchCandidate {
  knowledgePointTexts?: unknown
  noteId?: unknown
  noteTitle?: unknown
  sourceKeys?: unknown
}

export function learningSourceKey(source: {
  chunk_id: string
  document_id: string
  revision_id: string
}): string {
  return [source.document_id, source.revision_id, source.chunk_id].join('\u001f')
}

export function parseLearningLaunchState(value: unknown): LearningLaunchState | null {
  if (typeof value !== 'object' || value === null) return null
  const candidate = value as LearningLaunchCandidate
  if (
    typeof candidate.noteId !== 'string' ||
    typeof candidate.noteTitle !== 'string' ||
    !Array.isArray(candidate.sourceKeys) ||
    !Array.isArray(candidate.knowledgePointTexts)
  ) {
    return null
  }
  const sourceKeys = candidate.sourceKeys.filter(
    (key): key is string => typeof key === 'string' && key.length > 0,
  )
  const knowledgePointTexts = candidate.knowledgePointTexts.filter(
    (text): text is string => typeof text === 'string' && text.trim().length > 0,
  )
  if (!candidate.noteId || !candidate.noteTitle.trim()) return null
  return {
    knowledgePointTexts,
    noteId: candidate.noteId,
    noteTitle: candidate.noteTitle,
    sourceKeys,
  }
}
