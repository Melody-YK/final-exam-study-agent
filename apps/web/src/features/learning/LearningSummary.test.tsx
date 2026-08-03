import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { LearningSummary, ReviewQueueItem } from '../../api/types'
import { renderInWorkspace } from '../../test/render'
import { LearningSummary as LearningSummaryView } from './LearningSummary'

const validItem: ReviewQueueItem = {
  learning_unit_id: 'unit-1',
  label: '进程调度',
  kind: 'concept',
  mastery_level: 'learning',
  weakness_score: 0.75,
  next_review_at: '2026-08-03T09:00:00Z',
  source_status: 'valid',
}

const staleItem: ReviewQueueItem = {
  ...validItem,
  learning_unit_id: 'unit-stale',
  label: '旧版资料引用',
  source_status: 'stale',
}

const summary: LearningSummary = {
  course_id: 'course-1',
  accuracy: 0.75,
  correct_questions: 3,
  total_questions: 4,
  due_review_count: 2,
  next_action: '先复习进程调度，再开始下一组题。',
  units: [],
  weak_units: [validItem, staleItem],
}

describe('LearningSummary', () => {
  it('shows the saved result and disables a review item whose source is stale', async () => {
    const onStartReview = vi.fn()
    const { user } = renderInWorkspace(
      <LearningSummaryView
        onBackToOverview={vi.fn()}
        onStartReview={onStartReview}
        reviewQueue={[validItem, staleItem]}
        summary={summary}
      />,
    )

    expect(screen.getByRole('heading', { name: '结果已保存' })).toBeInTheDocument()
    expect(screen.getByText('先复习进程调度，再开始下一组题。')).toBeInTheDocument()
    const reviewButtons = screen.getAllByRole('button', { name: /开始复习|不可用/ })
    expect(reviewButtons[0]).toBeEnabled()
    expect(reviewButtons[1]).toBeDisabled()
    await user.click(reviewButtons[0]!)
    expect(onStartReview).toHaveBeenCalledWith(validItem)
  })
})
