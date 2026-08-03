import { Bot, LoaderCircle, Send } from 'lucide-react'
import { type FormEvent, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'

import { studyApi } from '../../api/client'
import type {
  EvidenceReference,
  PracticeQuestionView,
  PracticeTutorMode,
} from '../../api/types'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { Modal } from '../../components/ui/Modal'

export interface TutorChatMessage {
  content: string
  evidence: EvidenceReference[]
  mode?: PracticeTutorMode
  role: 'user' | 'assistant'
}

interface PracticeTutorModalProps {
  messages: TutorChatMessage[]
  onClose: () => void
  onHintUsed: () => void
  onMessagesChange: (messages: TutorChatMessage[]) => void
  open: boolean
  question: PracticeQuestionView
  sessionId: string
}

function sourceLabel(evidence: EvidenceReference): string {
  const location = evidence.locator.kind === 'section' ? '节' : '页'
  return `${evidence.document_name ?? '课程资料'} · 第 ${evidence.locator.ordinal} ${location}`
}

export function PracticeTutorModal({
  messages,
  onClose,
  onHintUsed,
  onMessagesChange,
  open,
  question,
  sessionId,
}: PracticeTutorModalProps) {
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [submitting, setSubmitting] = useState(false)
  const transcriptRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const frame = window.requestAnimationFrame(() => {
      const transcript = transcriptRef.current
      if (transcript) transcript.scrollTop = transcript.scrollHeight
    })
    return () => window.cancelAnimationFrame(frame)
  }, [messages, open])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const message = draft.trim()
    if (!message || submitting) return

    const userMessage: TutorChatMessage = {
      content: message,
      evidence: [],
      role: 'user',
    }
    const pendingMessages = [...messages, userMessage]
    onMessagesChange(pendingMessages)
    setDraft('')
    setError(null)
    setSubmitting(true)
    try {
      const response = await studyApi.askPracticeTutor(sessionId, question.id, {
        history: messages.slice(-8).map(({ content, role }) => ({ content, role })),
        message,
      })
      onMessagesChange([
        ...pendingMessages,
        {
          content: response.answer_markdown,
          evidence: response.evidence_refs,
          mode: response.mode,
          role: 'assistant',
        },
      ])
      if (response.mode === 'hint') onHintUsed()
    } catch (caught) {
      setError(caught)
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
    <Modal
      description={currentMode}
      onClose={close}
      open={open}
      size="wide"
      title="问 AI"
    >
      <div className="learning-tutor">
        <div className="learning-tutor__question">
          <span>当前题目</span>
          <strong>{question.prompt}</strong>
        </div>
        <div
          aria-live="polite"
          className="learning-tutor__transcript"
          ref={transcriptRef}
        >
          {messages.length === 0 ? (
            <div className="learning-tutor__empty">
              <Bot aria-hidden="true" size={24} />
              <p>还没有对话。</p>
            </div>
          ) : (
            messages.map((message, index) => (
              <article
                className={`learning-tutor-message learning-tutor-message--${message.role}`}
                key={`${message.role}-${index}`}
              >
                <span>{message.role === 'assistant' ? 'AI' : '你'}</span>
                <div className="learning-tutor-message__content">
                  {message.role === 'assistant' ? (
                    <ReactMarkdown skipHtml>{message.content}</ReactMarkdown>
                  ) : (
                    <p>{message.content}</p>
                  )}
                </div>
                {message.evidence.length > 0 ? (
                  <footer>
                    {message.evidence.map((item) => (
                      <span key={`${item.revision_id}-${item.chunk_id}`}>
                        {sourceLabel(item)}
                      </span>
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
            onChange={(event) => setDraft(event.target.value)}
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
