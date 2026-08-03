import { ArrowRight, BookOpenCheck, CircleAlert, RotateCcw, Target } from 'lucide-react'

import type { LearningSummary as LearningSummaryData, ReviewQueueItem } from '../../api/types'

const masteryLabels: Record<ReviewQueueItem['mastery_level'], string> = {
  new: '未开始',
  learning: '学习中',
  review: '待巩固',
  mastered: '已掌握',
}

interface LearningSummaryProps {
  summary: LearningSummaryData
  reviewQueue: ReviewQueueItem[]
  onStartReview?: (item: ReviewQueueItem) => void
  onBackToOverview?: () => void
}

function accuracyLabel(value: number): string {
  return `${Math.round(value * 100)}%`
}

export function LearningSummary({
  summary,
  reviewQueue,
  onStartReview,
  onBackToOverview,
}: LearningSummaryProps) {
  const weakUnits = summary.weak_units?.length ? summary.weak_units : reviewQueue

  return (
    <div className="learning-summary" aria-label="学习结果">
      <header className="learning-summary__header">
        <div>
          <p className="learning-kicker">本次练习</p>
          <h3>结果已保存</h3>
          <p className="learning-summary__action">{summary.next_action}</p>
        </div>
        <BookOpenCheck aria-hidden="true" className="learning-summary__header-icon" size={30} />
      </header>

      <section className="learning-summary__metrics" aria-label="练习统计">
        <div>
          <Target aria-hidden="true" size={18} />
          <span>正确率</span>
          <strong>{accuracyLabel(summary.accuracy)}</strong>
        </div>
        <div>
          <BookOpenCheck aria-hidden="true" size={18} />
          <span>答对题目</span>
          <strong>
            {summary.correct_questions} / {summary.total_questions}
          </strong>
        </div>
        <div>
          <RotateCcw aria-hidden="true" size={18} />
          <span>待复习</span>
          <strong>{summary.due_review_count} 项</strong>
        </div>
      </section>

      <section className="learning-summary__section" aria-labelledby="learning-summary-weak">
        <div className="learning-summary__section-heading">
          <div>
            <p className="learning-kicker">下一步</p>
            <h4 id="learning-summary-weak">优先复习薄弱点</h4>
          </div>
          <span className="learning-count">{weakUnits.length} 项</span>
        </div>
        {weakUnits.length === 0 ? (
          <div className="learning-summary__empty">
            <BookOpenCheck aria-hidden="true" size={20} />
            <span>目前没有到期的复习项，继续保持。</span>
          </div>
        ) : (
          <ul className="learning-review-list">
            {weakUnits.map((item) => {
              const sourceAvailable = item.source_status === 'valid'
              return (
                <li key={item.learning_unit_id} className="learning-review-item">
                  <div className="learning-review-item__copy">
                    <strong>{item.label}</strong>
                    <span>
                      {masteryLabels[item.mastery_level]} · 薄弱度 {Math.round(item.weakness_score * 100)}%
                    </span>
                    <small>
                      {sourceAvailable
                        ? `下次复习 ${new Date(item.next_review_at).toLocaleDateString('zh-CN')}`
                        : '来源已失效，暂不可复习'}
                    </small>
                  </div>
                  {onStartReview ? (
                    <button
                      className="button button--small"
                      disabled={!sourceAvailable}
                      onClick={() => onStartReview(item)}
                      type="button"
                    >
                      <ArrowRight aria-hidden="true" size={15} />
                      {sourceAvailable ? '开始复习' : '不可用'}
                    </button>
                  ) : null}
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <div className="learning-summary__footer">
        <p>
          <CircleAlert aria-hidden="true" size={15} />
          结果和复习安排来自本次作答，不会因为刷新页面丢失。
        </p>
        {onBackToOverview ? (
          <button className="button button--primary" onClick={onBackToOverview} type="button">
            返回学习台
          </button>
        ) : null}
      </div>
    </div>
  )
}
