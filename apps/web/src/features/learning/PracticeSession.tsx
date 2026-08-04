import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  ExternalLink,
  LoaderCircle,
  MessageCircleQuestion,
  Save,
  XCircle,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { studyApi } from '../../api/client'
import type {
  PracticeAttemptResult,
  PracticeQuestionView,
  PracticeSessionSnapshot,
} from '../../api/types'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { PracticeEvidenceModal } from './PracticeEvidenceModal'
import { PracticeTutorModal, type TutorChatMessage } from './PracticeTutorModal'

interface PracticeSessionProps {
  aiAvailable?: boolean
  session: PracticeSessionSnapshot
  onComplete: (results: PracticeAttemptResult[]) => void
  onExit: () => void
}

interface PracticeProgressDraft {
  answers: Record<string, string>
  hintedQuestionIds: string[]
  questionIndex: number
  skippedQuestionIds: string[]
  version: 2
}

interface LocalAttempt {
  answer: string
  result: PracticeAttemptResult
}

function firstPendingQuestion(questions: PracticeQuestionView[]): number {
  const index = questions.findIndex((question) => !question.answered)
  return index >= 0 ? index : 0
}

function questionTypeLabel(type: PracticeQuestionView['question_type']): string {
  const labels: Record<PracticeQuestionView['question_type'], string> = {
    calculation: '计算题',
    short_answer: '简答题',
    single_choice: '单选题',
    true_false: '判断题',
  }
  return labels[type]
}

function isConstructedResponse(type: PracticeQuestionView['question_type']): boolean {
  return type === 'calculation' || type === 'short_answer'
}

function difficultyLabel(difficulty: PracticeQuestionView['difficulty']): string {
  const labels: Record<PracticeQuestionView['difficulty'], string> = {
    1: '基础',
    2: '中等',
    3: '困难',
  }
  return labels[difficulty] ?? '未知'
}

function practiceProgressKey(sessionId: string): string {
  return `study-agent.learning:practice-progress:${sessionId}`
}

function clearPracticeProgress(sessionId: string): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.removeItem(practiceProgressKey(sessionId))
  } catch {
    // The server remains the source of truth for submitted answers.
  }
}

function readPracticeProgress(session: PracticeSessionSnapshot): PracticeProgressDraft | null {
  if (typeof localStorage === 'undefined') return null

  try {
    const raw = localStorage.getItem(practiceProgressKey(session.id))
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null

    const draft = parsed as {
      answers?: unknown
      hintedQuestionIds?: unknown
      questionIndex?: unknown
      skippedQuestionIds?: unknown
    }
    const pendingQuestions = session.questions.filter((question) => !question.answered)
    const pendingIds = new Set(pendingQuestions.map((question) => question.id))
    const answers: Record<string, string> = {}
    if (typeof draft.answers === 'object' && draft.answers !== null) {
      for (const [questionId, value] of Object.entries(draft.answers)) {
        if (typeof value !== 'string' || !value.trim() || !pendingIds.has(questionId)) continue
        const question = session.questions.find((candidate) => candidate.id === questionId)
        const answerIsValid = question
          ? isConstructedResponse(question.question_type)
            ? value.length <= 8_000
            : question.options.some((option) => option.id === value)
          : false
        if (question?.status === 'ready' && answerIsValid) {
          answers[questionId] = value
        }
      }
    }

    const validIdList = (value: unknown, allowed: Set<string>): string[] =>
      Array.isArray(value)
        ? Array.from(
            new Set(
              value.filter((item): item is string => typeof item === 'string' && allowed.has(item)),
            ),
          )
        : []

    const nonReadyPendingIds = new Set(
      pendingQuestions.filter((question) => question.status !== 'ready').map((question) => question.id),
    )
    const savedIndex = draft.questionIndex
    const questionIndex =
      typeof savedIndex === 'number' &&
      Number.isInteger(savedIndex) &&
      savedIndex >= 0 &&
      savedIndex < session.questions.length
        ? savedIndex
        : firstPendingQuestion(session.questions)

    return {
      answers,
      hintedQuestionIds: validIdList(draft.hintedQuestionIds, pendingIds),
      questionIndex,
      skippedQuestionIds: validIdList(draft.skippedQuestionIds, nonReadyPendingIds),
      version: 2,
    }
  } catch {
    return null
  }
}

function writePracticeProgress(sessionId: string, draft: PracticeProgressDraft): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(practiceProgressKey(sessionId), JSON.stringify(draft))
  } catch {
    // The server remains the source of truth for submitted answers.
  }
}

export function PracticeSession({
  aiAvailable = true,
  session,
  onComplete,
  onExit,
}: PracticeSessionProps) {
  const [restoredProgress] = useState(() => readPracticeProgress(session))
  const [questionIndex, setQuestionIndex] = useState(
    restoredProgress?.questionIndex ?? firstPendingQuestion(session.questions),
  )
  const [draftAnswers, setDraftAnswers] = useState<Record<string, string>>(
    () => restoredProgress?.answers ?? {},
  )
  const [hintedQuestionIds, setHintedQuestionIds] = useState<Set<string>>(
    () => new Set(restoredProgress?.hintedQuestionIds ?? []),
  )
  const [skippedQuestionIds, setSkippedQuestionIds] = useState<Set<string>>(
    () => new Set(restoredProgress?.skippedQuestionIds ?? []),
  )
  const [localAttempts, setLocalAttempts] = useState<Record<string, LocalAttempt>>({})
  const [tutorConversations, setTutorConversations] = useState<
    Record<string, TutorChatMessage[]>
  >({})
  const [submitError, setSubmitError] = useState<unknown>(null)
  const [submitting, setSubmitting] = useState(false)
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [tutorOpen, setTutorOpen] = useState(false)
  const attemptKeys = useRef(new Map<string, string>())

  const currentQuestion = session.questions[questionIndex]
  const currentAttempt = currentQuestion ? localAttempts[currentQuestion.id] : undefined
  const currentAnswered = Boolean(currentQuestion?.answered || currentAttempt)
  const selectedAnswer = currentQuestion
    ? (currentAttempt?.answer ?? currentQuestion.submitted_answer ?? draftAnswers[currentQuestion.id] ?? '')
    : ''
  const answeredIds = useMemo(() => {
    const ids = new Set(
      session.questions.filter((question) => question.answered).map((question) => question.id),
    )
    Object.keys(localAttempts).forEach((id) => ids.add(id))
    return ids
  }, [localAttempts, session.questions])
  const processedIds = useMemo(
    () => new Set([...answeredIds, ...skippedQuestionIds]),
    [answeredIds, skippedQuestionIds],
  )
  const processedCount = processedIds.size
  const allQuestionsProcessed = session.questions.every((question) => processedIds.has(question.id))
  const remainingCount = Math.max(0, session.question_count - processedCount)
  const progressText = `${processedCount} / ${session.question_count}`

  useEffect(() => {
    if (session.status !== 'active') {
      clearPracticeProgress(session.id)
      return
    }
    const pendingIds = new Set(
      session.questions
        .filter((question) => !question.answered && !localAttempts[question.id])
        .map((question) => question.id),
    )
    writePracticeProgress(session.id, {
      answers: Object.fromEntries(
        Object.entries(draftAnswers).filter(([questionId]) => pendingIds.has(questionId)),
      ),
      hintedQuestionIds: [...hintedQuestionIds].filter((id) => pendingIds.has(id)),
      questionIndex,
      skippedQuestionIds: [...skippedQuestionIds].filter((id) => pendingIds.has(id)),
      version: 2,
    })
  }, [
    draftAnswers,
    hintedQuestionIds,
    localAttempts,
    questionIndex,
    session.id,
    session.questions,
    session.status,
    skippedQuestionIds,
  ])

  const feedback = currentQuestion
    ? {
        evidence: currentAttempt?.result.evidence_refs ?? currentQuestion.evidence_refs,
        explanation: currentAttempt?.result.explanation ?? currentQuestion.explanation,
        gradingFeedback:
          currentAttempt?.result.grading_feedback ?? currentQuestion.grading_feedback ?? '',
        masteryReason:
          currentAttempt?.result.mastery.reason ?? currentQuestion.mastery_reason ?? '',
        outcome: currentAttempt?.result.outcome ?? currentQuestion.outcome,
      }
    : null

  const finish = () => {
    clearPracticeProgress(session.id)
    onComplete(Object.values(localAttempts).map((attempt) => attempt.result))
  }

  const goToQuestion = (nextIndex: number) => {
    if (nextIndex < 0 || nextIndex >= session.questions.length) return
    setQuestionIndex(nextIndex)
    setSubmitError(null)
    setEvidenceOpen(false)
    setTutorOpen(false)
  }

  const submit = async () => {
    if (
      !currentQuestion ||
      !selectedAnswer.trim() ||
      currentAnswered ||
      currentQuestion.status !== 'ready'
    ) {
      return
    }
    setSubmitError(null)
    setSubmitting(true)
    const commandKey =
      attemptKeys.current.get(currentQuestion.id) ??
      `practice-attempt-${session.id}-${currentQuestion.id}`
    attemptKeys.current.set(currentQuestion.id, commandKey)
    try {
      const result = await studyApi.submitPracticeAttempt(
        session.id,
        {
          answer: selectedAnswer,
          question_id: currentQuestion.id,
          viewed_hint: hintedQuestionIds.has(currentQuestion.id),
        },
        commandKey,
      )
      setLocalAttempts((current) => ({
        ...current,
        [currentQuestion.id]: { answer: selectedAnswer, result },
      }))
      setDraftAnswers((current) => {
        const next = { ...current }
        delete next[currentQuestion.id]
        return next
      })
    } catch (error) {
      setSubmitError(error)
    } finally {
      setSubmitting(false)
    }
  }

  const skipCurrentQuestion = () => {
    if (!currentQuestion) return
    const nextSkipped = new Set(skippedQuestionIds).add(currentQuestion.id)
    setSkippedQuestionIds(nextSkipped)
    const nextProcessed = new Set([...answeredIds, ...nextSkipped])
    if (session.questions.every((question) => nextProcessed.has(question.id))) {
      clearPracticeProgress(session.id)
      onComplete(Object.values(localAttempts).map((attempt) => attempt.result))
      return
    }
    const nextIndex = session.questions.findIndex(
      (question, index) => index > questionIndex && !nextProcessed.has(question.id),
    )
    if (nextIndex >= 0) goToQuestion(nextIndex)
  }

  if (!currentQuestion) {
    return (
      <section className="page-state" aria-label="练习不可用">
        <CircleAlert aria-hidden="true" size={28} />
        <h3>练习题目不可用</h3>
        <button className="button" onClick={onExit} type="button">
          返回学习台
        </button>
      </section>
    )
  }

  const tutorQuestion: PracticeQuestionView = {
    ...currentQuestion,
    answered: currentAnswered,
  }
  const currentTutorMessages = tutorConversations[currentQuestion.id] ?? []
  const constructedResponse = isConstructedResponse(currentQuestion.question_type)
  const answerReady = Boolean(selectedAnswer.trim())

  return (
    <section className="learning-practice" aria-label="练习作答">
      <header className="learning-practice__header">
        <button className="button button--small" onClick={onExit} type="button">
          <ChevronLeft aria-hidden="true" size={15} />
          返回学习台
        </button>
        <div className="learning-practice__progress" aria-label={`进度 ${progressText}`}>
          <span>第 {questionIndex + 1} 题</span>
          <strong>{progressText}</strong>
        </div>
      </header>

      <div className="learning-practice__track" aria-hidden="true">
        <span style={{ width: `${(processedCount / Math.max(1, session.question_count)) * 100}%` }} />
      </div>

      {currentQuestion.status !== 'ready' ? (
        <div className="source-unavailable learning-practice__notice" role="alert">
          <CircleAlert aria-hidden="true" size={18} />
          <div>
            <strong>
              {currentQuestion.status === 'invalid' ? '题目未通过质量检查' : '题目来源已失效'}
            </strong>
            <p>
              {currentQuestion.status === 'invalid'
                ? '这道题不能继续作答，请跳过并重新生成题目。'
                : '这道题不能继续作答，旧依据不会显示。'}
            </p>
          </div>
        </div>
      ) : null}

      <article className="learning-question">
        <header className="learning-question__header">
          <div className="learning-question__meta">
            <span>{questionTypeLabel(currentQuestion.question_type)}</span>
            <span>难度 {difficultyLabel(currentQuestion.difficulty)}</span>
            <span>依据 {currentQuestion.evidence_refs.length} 条</span>
            {currentQuestion.practice_mode === 'exercise_variant' ? (
              <span>同型变式</span>
            ) : null}
            {hintedQuestionIds.has(currentQuestion.id) ? <span>已使用 AI 提示</span> : null}
          </div>
          <button
            className="button button--small learning-tutor-toggle"
            disabled={!aiAvailable || currentQuestion.status !== 'ready'}
            onClick={() => setTutorOpen(true)}
            title={!aiAvailable ? 'AI Provider 当前不可用' : undefined}
            type="button"
          >
            <MessageCircleQuestion aria-hidden="true" size={15} />
            问 AI
          </button>
        </header>
        <h2>{currentQuestion.prompt}</h2>
        {constructedResponse ? (
          <div className="learning-constructed-answer">
            <label htmlFor={`answer-${currentQuestion.id}`}>你的解答</label>
            <textarea
              disabled={currentAnswered || currentQuestion.status !== 'ready'}
              id={`answer-${currentQuestion.id}`}
              maxLength={8_000}
              onChange={(event) =>
                setDraftAnswers((current) => ({
                  ...current,
                  [currentQuestion.id]: event.target.value,
                }))
              }
              rows={9}
              value={selectedAnswer}
            />
            <span>{selectedAnswer.length} / 8000</span>
          </div>
        ) : (
          <fieldset
            className="learning-options"
            disabled={currentAnswered || currentQuestion.status !== 'ready'}
          >
            <legend className="sr-only">选择答案</legend>
            {currentQuestion.options.map((option) => (
              <label
                className={`learning-option${selectedAnswer === option.id ? ' is-selected' : ''}${currentAnswered ? ' is-locked' : ''}`}
                key={option.id}
              >
                <input
                  checked={selectedAnswer === option.id}
                  name={`question-${currentQuestion.id}`}
                  onChange={() =>
                    setDraftAnswers((current) => ({ ...current, [currentQuestion.id]: option.id }))
                  }
                  type="radio"
                  value={option.id}
                />
                <span className="learning-option__marker" aria-hidden="true" />
                <span>{option.label}</span>
              </label>
            ))}
          </fieldset>
        )}

        {submitError ? <ErrorNotice error={submitError} title="答案未提交" /> : null}

        {currentAnswered && feedback?.outcome ? (
          <section
            className={`learning-feedback ${feedback.outcome === 'correct' ? 'is-correct' : 'is-incorrect'}`}
            aria-label="作答反馈"
          >
            <div className="learning-feedback__heading">
              {feedback.outcome === 'correct' ? (
                <CheckCircle2 aria-hidden="true" size={20} />
              ) : (
                <XCircle aria-hidden="true" size={20} />
              )}
              <strong>{feedback.outcome === 'correct' ? '回答正确' : '需要再巩固'}</strong>
              {feedback.masteryReason ? <span>{feedback.masteryReason}</span> : null}
            </div>
            {feedback.gradingFeedback ? (
              <div className="learning-feedback__detail">
                <strong>AI 判分反馈</strong>
                <p>{feedback.gradingFeedback}</p>
              </div>
            ) : null}
            {feedback.explanation ? (
              <div className="learning-feedback__detail">
                <strong>{constructedResponse ? '参考解答' : '题目解析'}</strong>
                <p>{feedback.explanation}</p>
              </div>
            ) : null}
            <button
              className="button button--small learning-evidence-toggle"
              disabled={feedback.evidence.length === 0}
              onClick={() => setEvidenceOpen(true)}
              type="button"
            >
              <ExternalLink aria-hidden="true" size={15} />
              查看证据原文
            </button>
          </section>
        ) : null}
      </article>

      <footer className="learning-practice__footer">
        <div className="learning-practice__navigation" aria-label="题目导航">
          <button
            className="button button--small"
            disabled={questionIndex === 0}
            onClick={() => goToQuestion(questionIndex - 1)}
            type="button"
          >
            <ChevronLeft aria-hidden="true" size={16} />
            上一题
          </button>
          <button
            className="button button--small"
            disabled={questionIndex >= session.questions.length - 1}
            onClick={() => goToQuestion(questionIndex + 1)}
            type="button"
          >
            下一题
            <ChevronRight aria-hidden="true" size={16} />
          </button>
        </div>
        <div className="learning-practice__command">
          <span className="learning-practice__saved">
            <Save aria-hidden="true" size={14} />
            已自动保存
          </span>
          {currentAnswered ? (
            allQuestionsProcessed ? (
              <button className="button button--primary" onClick={finish} type="button">
                完成练习
                <CheckCircle2 aria-hidden="true" size={16} />
              </button>
            ) : (
              <span className="muted">还有 {remainingCount} 题未完成</span>
            )
          ) : currentQuestion.status === 'ready' ? (
            <button
              className="button button--primary"
              disabled={!answerReady || submitting}
              onClick={() => void submit()}
              type="button"
            >
              {submitting ? (
                <LoaderCircle aria-hidden="true" className="spin" size={16} />
              ) : (
                <ChevronRight aria-hidden="true" size={16} />
              )}
              {submitting
                ? constructedResponse
                  ? 'AI 正在判分'
                  : '正在提交'
                : constructedResponse
                  ? '提交并判分'
                  : '提交答案'}
            </button>
          ) : (
            <button className="button button--primary" onClick={skipCurrentQuestion} type="button">
              跳过此题
              <ChevronRight aria-hidden="true" size={16} />
            </button>
          )}
        </div>
      </footer>

      <PracticeEvidenceModal
        evidence={feedback?.evidence ?? []}
        onClose={() => setEvidenceOpen(false)}
        open={evidenceOpen}
      />
      <PracticeTutorModal
        key={currentQuestion.id}
        messages={currentTutorMessages}
        onClose={() => setTutorOpen(false)}
        onHintUsed={() =>
          setHintedQuestionIds((current) => new Set(current).add(currentQuestion.id))
        }
        onMessagesChange={(messages) =>
          setTutorConversations((current) => ({
            ...current,
            [currentQuestion.id]: messages,
          }))
        }
        open={tutorOpen}
        question={tutorQuestion}
        sessionId={session.id}
      />
    </section>
  )
}
