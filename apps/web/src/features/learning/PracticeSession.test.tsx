import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { studyApi } from '../../api/client'
import type { PracticeAttemptResult, PracticeSessionSnapshot } from '../../api/types'
import { renderInWorkspace } from '../../test/render'
import { PracticeSession } from './PracticeSession'

const source = {
  chunk_id: 'chunk-1',
  content_sha256: 'a'.repeat(64),
  document_id: 'document-1',
  document_name: '操作系统.pdf',
  locator: { kind: 'section' as const, ordinal: 2 },
  quote: '进程是资源分配的基本单位。',
  revision_id: 'revision-1',
}

const session: PracticeSessionSnapshot = {
  id: 'session-1',
  course_id: 'course-1',
  question_count: 1,
  started_at: '2026-08-02T08:01:00Z',
  completed_at: null,
  status: 'active',
  questions: [
    {
      id: 'question-1',
      learning_unit_id: 'unit-1',
      prompt: '进程是什么？',
      question_type: 'single_choice',
      practice_mode: 'knowledge_recall',
      difficulty: 1,
      options: [
        { id: 'a', label: '资源分配的基本单位' },
        { id: 'b', label: '调度的基本单位' },
      ],
      status: 'ready',
      evidence_refs: [source],
      answered: false,
      outcome: null,
    },
  ],
}

const attempt: PracticeAttemptResult = {
  id: 'attempt-1',
  question_id: 'question-1',
  outcome: 'correct',
  score: 1,
  explanation: '资料明确说明进程是资源分配的基本单位。',
  evidence_refs: [source],
  mastery: {
    learning_unit_id: 'unit-1',
    previous_level: 'new',
    level: 'learning',
    reason: '首次正确，掌握度上升一级。',
    next_review_at: '2026-08-03T09:00:00Z',
  },
}

describe('PracticeSession', () => {
  it('saves and restores an unsubmitted answer for the same session', async () => {
    const progressKey = 'study-agent.learning:practice-progress:session-1'
    const first = renderInWorkspace(
      <PracticeSession onComplete={vi.fn()} onExit={vi.fn()} session={session} />,
    )

    await first.user.click(screen.getByRole('radio', { name: '调度的基本单位' }))
    expect(JSON.parse(localStorage.getItem(progressKey) ?? '{}')).toEqual({
      questionIndex: 0,
      answers: { 'question-1': 'b' },
      hintedQuestionIds: [],
      skippedQuestionIds: [],
      version: 2,
    })
    first.unmount()

    renderInWorkspace(<PracticeSession onComplete={vi.fn()} onExit={vi.fn()} session={session} />)

    expect(screen.getByRole('radio', { name: '调度的基本单位' })).toBeChecked()
  })

  it('keeps a calculation answer as free text, restores it, and shows AI grading feedback', async () => {
    const calculationSession: PracticeSessionSnapshot = {
      ...session,
      questions: [
        {
          ...session.questions[0]!,
          id: 'question-calculation',
          prompt: '页面大小为 128 字节，逻辑地址为 390，求页号和页内偏移。',
          question_type: 'calculation',
          practice_mode: 'exercise_variant',
          options: [],
        },
      ],
    }
    const calculationAttempt: PracticeAttemptResult = {
      ...attempt,
      question_id: 'question-calculation',
      grading_feedback: '页号和页内偏移均正确，列式完整。',
      explanation: '390 = 3 × 128 + 6，因此页号为 3，页内偏移为 6 字节。',
    }
    const first = renderInWorkspace(
      <PracticeSession onComplete={vi.fn()} onExit={vi.fn()} session={calculationSession} />,
    )

    await first.user.type(screen.getByLabelText('你的解答'), '390 = 3 × 128 + 6')
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    expect(
      JSON.parse(localStorage.getItem('study-agent.learning:practice-progress:session-1') ?? '{}'),
    ).toMatchObject({
      answers: { 'question-calculation': '390 = 3 × 128 + 6' },
    })
    first.unmount()

    const submit = vi.spyOn(studyApi, 'submitPracticeAttempt').mockResolvedValue(calculationAttempt)
    const restored = renderInWorkspace(
      <PracticeSession onComplete={vi.fn()} onExit={vi.fn()} session={calculationSession} />,
    )
    expect(screen.getByLabelText('你的解答')).toHaveValue('390 = 3 × 128 + 6')
    await restored.user.click(screen.getByRole('button', { name: '提交并判分' }))

    expect(await screen.findByText('AI 判分反馈')).toBeInTheDocument()
    expect(screen.getByText('页号和页内偏移均正确，列式完整。')).toBeInTheDocument()
    expect(screen.getByText('参考解答')).toBeInTheDocument()
    expect(submit).toHaveBeenCalledWith(
      'session-1',
      expect.objectContaining({ answer: '390 = 3 × 128 + 6' }),
      expect.any(String),
    )
  })

  it('saves the current question index after moving to the next question', async () => {
    const secondQuestion: PracticeSessionSnapshot['questions'][number] = {
      ...session.questions[0]!,
      id: 'question-2',
      prompt: '调度是什么？',
    }
    const multiQuestionSession: PracticeSessionSnapshot = {
      ...session,
      question_count: 2,
      questions: [session.questions[0]!, secondQuestion],
    }
    vi.spyOn(studyApi, 'submitPracticeAttempt').mockResolvedValue(attempt)
    const { user } = renderInWorkspace(
      <PracticeSession onComplete={vi.fn()} onExit={vi.fn()} session={multiQuestionSession} />,
    )

    await user.click(screen.getByRole('radio', { name: '资源分配的基本单位' }))
    await user.click(screen.getByRole('button', { name: /提交答案/ }))
    await user.click(screen.getByRole('button', { name: '下一题' }))

    expect(screen.getByRole('heading', { name: '调度是什么？' })).toBeInTheDocument()
    expect(
      JSON.parse(localStorage.getItem('study-agent.learning:practice-progress:session-1') ?? '{}'),
    ).toMatchObject({ questionIndex: 1, answers: {} })
  })

  it('keeps drafts for multiple questions and supports returning to the previous question', async () => {
    const secondQuestion: PracticeSessionSnapshot['questions'][number] = {
      ...session.questions[0]!,
      id: 'question-2',
      prompt: '线程是什么？',
    }
    const multiQuestionSession: PracticeSessionSnapshot = {
      ...session,
      question_count: 2,
      questions: [session.questions[0]!, secondQuestion],
    }
    const first = renderInWorkspace(
      <PracticeSession onComplete={vi.fn()} onExit={vi.fn()} session={multiQuestionSession} />,
    )

    await first.user.click(screen.getByRole('radio', { name: '调度的基本单位' }))
    await first.user.click(screen.getByRole('button', { name: '下一题' }))
    await first.user.click(screen.getByRole('radio', { name: '资源分配的基本单位' }))
    await first.user.click(screen.getByRole('button', { name: '上一题' }))

    expect(screen.getByRole('heading', { name: '进程是什么？' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '调度的基本单位' })).toBeChecked()
    expect(
      JSON.parse(localStorage.getItem('study-agent.learning:practice-progress:session-1') ?? '{}'),
    ).toMatchObject({
      answers: { 'question-1': 'b', 'question-2': 'a' },
      questionIndex: 0,
    })
    first.unmount()

    renderInWorkspace(
      <PracticeSession onComplete={vi.fn()} onExit={vi.fn()} session={multiQuestionSession} />,
    )
    expect(screen.getByRole('radio', { name: '调度的基本单位' })).toBeChecked()
    await screen.findByText('已自动保存')
  })

  it('keeps submitted feedback available after moving back from the next question', async () => {
    const secondQuestion: PracticeSessionSnapshot['questions'][number] = {
      ...session.questions[0]!,
      id: 'question-2',
      prompt: '线程是什么？',
    }
    const multiQuestionSession: PracticeSessionSnapshot = {
      ...session,
      question_count: 2,
      questions: [session.questions[0]!, secondQuestion],
    }
    vi.spyOn(studyApi, 'submitPracticeAttempt').mockResolvedValue(attempt)
    const { user } = renderInWorkspace(
      <PracticeSession onComplete={vi.fn()} onExit={vi.fn()} session={multiQuestionSession} />,
    )

    await user.click(screen.getByRole('radio', { name: '资源分配的基本单位' }))
    await user.click(screen.getByRole('button', { name: /提交答案/ }))
    await screen.findByText('回答正确')
    await user.click(screen.getByRole('button', { name: '下一题' }))
    await user.click(screen.getByRole('button', { name: '上一题' }))

    expect(screen.getByText('回答正确')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '资源分配的基本单位' })).toBeDisabled()
  })

  it('marks a successful pre-submit AI conversation as a viewed hint', async () => {
    vi.spyOn(studyApi, 'getPracticeTutorConversation').mockResolvedValue({
      conversation_id: null,
      has_earlier_messages: false,
      messages: [],
      question_id: 'question-1',
      session_id: 'session-1',
    })
    vi.spyOn(studyApi, 'askPracticeTutor').mockResolvedValue({
      answer_markdown: '先比较资源归属和执行调度的区别。',
      conversation_id: 'conversation-1',
      created_at: '2026-08-02T08:02:00Z',
      evidence_refs: [source],
      intent: 'hint',
      message_id: 'message-1',
      mode: 'hint',
    })
    const submit = vi.spyOn(studyApi, 'submitPracticeAttempt').mockResolvedValue(attempt)
    const { user } = renderInWorkspace(
      <PracticeSession onComplete={vi.fn()} onExit={vi.fn()} session={session} />,
    )

    await user.click(screen.getByRole('button', { name: '问 AI' }))
    await user.type(screen.getByLabelText('向 AI 提问'), '给我一点提示')
    await user.click(screen.getByRole('button', { name: '发送问题' }))
    expect(await screen.findByText('先比较资源归属和执行调度的区别。')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '关闭' }))
    await user.click(screen.getByRole('radio', { name: '资源分配的基本单位' }))
    await user.click(screen.getByRole('button', { name: /提交答案/ }))

    expect(submit).toHaveBeenCalledWith(
      'session-1',
      expect.objectContaining({ viewed_hint: true }),
      expect.any(String),
    )
  })

  it('restores the server-side tutor transcript when the modal opens', async () => {
    const getConversation = vi.spyOn(studyApi, 'getPracticeTutorConversation').mockResolvedValue({
      conversation_id: 'conversation-1',
      has_earlier_messages: false,
      messages: [
        {
          content: '你能给个例子吗?',
          created_at: '2026-08-02T08:02:00Z',
          evidence_refs: [],
          id: 'message-user-1',
          intent: 'example',
          role: 'user',
        },
        {
          content: '可以类比公司分配预算和员工执行任务。',
          created_at: '2026-08-02T08:02:01Z',
          evidence_refs: [source],
          id: 'message-assistant-1',
          intent: 'example',
          mode: 'hint',
          role: 'assistant',
        },
      ],
      question_id: 'question-1',
      session_id: 'session-1',
    })
    const { user } = renderInWorkspace(
      <PracticeSession onComplete={vi.fn()} onExit={vi.fn()} session={session} />,
    )

    await user.click(screen.getByRole('button', { name: '问 AI' }))

    expect(await screen.findByText('你能给个例子吗?')).toBeInTheDocument()
    expect(screen.getByText('可以类比公司分配预算和员工执行任务。')).toBeInTheDocument()
    expect(getConversation).toHaveBeenCalledWith('session-1', 'question-1')
  })

  it('submits once, shows explanation, and opens evidence before completing', async () => {
    const submit = vi.spyOn(studyApi, 'submitPracticeAttempt').mockResolvedValue(attempt)
    const onComplete = vi.fn()
    const { user } = renderInWorkspace(
      <PracticeSession onComplete={onComplete} onExit={vi.fn()} session={session} />,
    )

    await user.click(screen.getByRole('radio', { name: '资源分配的基本单位' }))
    expect(screen.getByText('难度 基础')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /提交答案/ }))
    expect(await screen.findByText('回答正确')).toBeInTheDocument()
    expect(screen.getByText('资料明确说明进程是资源分配的基本单位。')).toBeInTheDocument()
    expect(submit).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: '查看证据原文' }))
    expect(screen.getByRole('blockquote')).toHaveTextContent('进程是资源分配的基本单位。')
    expect(screen.getByText('操作系统.pdf')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '关闭' }))
    await user.click(screen.getByRole('button', { name: /完成练习/ }))
    expect(onComplete).toHaveBeenCalledWith([attempt])
    expect(localStorage.getItem('study-agent.learning:practice-progress:session-1')).toBeNull()
  })

  it('fails closed for a stale question and does not submit an answer', async () => {
    const staleSession = {
      ...session,
      questions: session.questions.map((question) => ({
        ...question,
        status: 'stale' as const,
      })),
    }
    const submit = vi.spyOn(studyApi, 'submitPracticeAttempt')
    const onComplete = vi.fn()
    const { user } = renderInWorkspace(
      <PracticeSession onComplete={onComplete} onExit={vi.fn()} session={staleSession} />,
    )

    expect(screen.getByText('题目来源已失效')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '跳过此题' }))
    expect(submit).not.toHaveBeenCalled()
    expect(onComplete).toHaveBeenCalledWith([])
  })

  it('explains when a question was rejected by quality checks', () => {
    const invalidSession = {
      ...session,
      questions: session.questions.map((question) => ({
        ...question,
        evidence_refs: [],
        status: 'invalid' as const,
      })),
    }

    renderInWorkspace(
      <PracticeSession onComplete={vi.fn()} onExit={vi.fn()} session={invalidSession} />,
    )

    expect(screen.getByText('题目未通过质量检查')).toBeInTheDocument()
    expect(screen.getByText('这道题不能继续作答，请跳过并重新生成题目。')).toBeInTheDocument()
  })

  it('shows when a question was generated as an exercise variant', () => {
    const variantSession: PracticeSessionSnapshot = {
      ...session,
      questions: session.questions.map((question) => ({
        ...question,
        practice_mode: 'exercise_variant',
      })),
    }

    renderInWorkspace(
      <PracticeSession onComplete={vi.fn()} onExit={vi.fn()} session={variantSession} />,
    )

    expect(screen.getByText('同型变式')).toBeInTheDocument()
  })
})
