import { Bot, LoaderCircle, Send } from 'lucide-react'
import { type FormEvent, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'

import { studyApi } from '../../api/client'
import type { PracticeQuestionView, PracticeTutorMessage } from '../../api/types'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { Modal } from '../../components/ui/Modal'

interface PracticeTutorModalProps {
  onClose: () => void
  onHintUsed: () => void
  open: boolean
  question: PracticeQuestionView
  sessionId: string
}

function sourceLabel(evidence: PracticeTutorMessage['evidence_refs'][number]): string {
  const location = evidence.locator.kind === 'section' ? '节' : '页'
  return `${evidence.document_name ?? '课程资料'} · 第 ${evidence.locator.ordinal} ${location}`
}

export function PracticeTutorModal({
  onClose,
  onHintUsed,
  open,
  question,
  sessionId,
}: PracticeTutorModalProps) {
  const [messages, setMessages] = useState<PracticeTutorMessage[]>([])
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [retryTurn, setRetryTurn] = useState<{
    id: string
    message: string
  } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const transcriptRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    let active = true
    void Promise.resolve().then(async () => {
      if (!active) return
      setLoadingHistory(true)
      setError(null)
      try {
        const conversation = await studyApi.getPracticeTutorConversation(sessionId, question.id)
        if (active) setMessages(conversation.messages)
      } catch (caught) {
        if (active) setError(caught)
      } finally {
        if (active) setLoadingHistory(false)
      }
    })
    return () => {
      active = false
    }
  }, [open, question.id, sessionId])

  useEffect(() => {
    if (!open) return
    const frame = window.requestAnimationFrame(() => {
      const transcript = transcriptRef.current
      if (transcript) transcript.scrollTop = transcript.scrollHeight
    })
    return () => window.cancelAnimationFrame(frame)
  }, [loadingHistory, messages, open, submitting])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const message = draft.trim()
    if (!message || submitting) return
    const turnId = retryTurn?.message === message ? retryTurn.id : crypto.randomUUID()

    setDraft('')
    setError(null)
    setSubmitting(true)
    try {
      const response = await studyApi.askPracticeTutor(sessionId, question.id, {
        message,
        turn_id: turnId,
      })
      const userMessage: PracticeTutorMessage = {
        id: `local-${response.message_id}`,
        role: 'user',
        content: message,
        intent: response.intent,
        evidence_refs: [],
        created_at: response.created_at,
      }
      const assistantMessage: PracticeTutorMessage = {
        id: response.message_id,
        role: 'assistant',
        content: response.answer_markdown,
        intent: response.intent,
        mode: response.mode,
        evidence_refs: response.evidence_refs,
        created_at: response.created_at,
      }
      setMessages((current) => [...current, userMessage, assistantMessage])
      setRetryTurn(null)
      if (response.mode === 'hint') onHintUsed()
    } catch (caught) {
      setError(caught)
      setDraft(message)
      setRetryTurn({ id: turnId, message })
    } finally {
      setSubmitting(false)
    }
  }

  const currentMode = question.answered ? '解析模式' : '提示模式'
  const close = () => {
    setError(null)
    onClose()
  }

  return (
    <Modal description={currentMode} onClose={close} open={open} size="wide" title="问 AI">
      <div className="learning-tutor">
        <div className="learning-tutor__question">
          <span>当前题目</span>
          <strong>{question.prompt}</strong>
        </div>
        <div aria-live="polite" className="learning-tutor__transcript" ref={transcriptRef}>
          {loadingHistory ? (
            <div className="learning-tutor__empty" role="status">
              <LoaderCircle aria-hidden="true" className="spin" size={24} />
              正在恢复对话
            </div>
          ) : messages.length === 0 ? (
            <div className="learning-tutor__empty">
              <Bot aria-hidden="true" size={24} />
              <p>还没有对话。</p>
            </div>
          ) : (
            messages.map((message) => (
              <article
                className={`learning-tutor-message learning-tutor-message--${message.role}`}
                key={message.id}
              >
                <span>{message.role === 'assistant' ? 'AI' : '你'}</span>
                <div className="learning-tutor-message__content">
                  {message.role === 'assistant' ? (
                    <ReactMarkdown skipHtml>{message.content}</ReactMarkdown>
                  ) : (
                    <p>{message.content}</p>
                  )}
                </div>
                {message.evidence_refs.length > 0 ? (
                  <footer>
                    {message.evidence_refs.map((item) => (
                      <span key={`${item.revision_id}-${item.chunk_id}`}>{sourceLabel(item)}</span>
                    ))}
                  </footer>
                ) : null}
              </article>
            ))
          )}
          {submitting ? (
            <div className="learning-tutor__thinking" role="status">
              <LoaderCircle aria-hidden="true" className="spin" size={16} />
              正在整理回答
            </div>
          ) : null}
        </div>
        {error ? <ErrorNotice error={error} title="AI 暂时没有回答" /> : null}
        <form className="learning-tutor__composer" onSubmit={(event) => void submit(event)}>
          <label className="sr-only" htmlFor={`practice-tutor-${question.id}`}>
            向 AI 提问
          </label>
          <textarea
            autoFocus
            id={`practice-tutor-${question.id}`}
            maxLength={1_000}
            onChange={(event) => {
              setDraft(event.target.value)
              setRetryTurn(null)
            }}
            placeholder="输入你的问题"
            rows={3}
            value={draft}
          />
          <button
            aria-label="发送问题"
            className="button button--primary"
            disabled={!draft.trim() || submitting}
            type="submit"
          >
            <Send aria-hidden="true" size={16} />
            发送
          </button>
        </form>
      </div>
    </Modal>
  )
}
