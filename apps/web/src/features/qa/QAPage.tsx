import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  ArrowUp,
  Check,
  Circle,
  ExternalLink,
  History,
  LoaderCircle,
  MessageSquarePlus,
  Search,
  ShieldCheck,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { ApiError, studyApi } from '../../api/client'
import type {
  Citation,
  CitationSource,
  ConversationRecord,
  QuerySnapshot,
} from '../../api/types'
import { useWorkspace } from '../../app/WorkspaceContext'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { PageHeader } from '../../components/ui/PageHeader'
import { StatusBadge } from '../../components/ui/StatusBadge'
import { SourceViewer } from '../source-viewer/SourceViewer'
import {
  isTerminal,
  queryRefetchInterval,
  type QueryStreamConnection,
} from './queryPolling'

const stages = [
  { key: 'retrieval', label: '检索课程资料' },
  { key: 'generation', label: '生成有据回答' },
  { key: 'validation', label: '校验引用' },
] as const

function suggestedQuestionFromRouteState(state: unknown): string | null {
  if (typeof state !== 'object' || state === null || Array.isArray(state)) return null
  const candidate = state as Record<string, unknown>
  if (candidate.startNewConversation !== true || typeof candidate.suggestedQuestion !== 'string') {
    return null
  }
  const suggestedQuestion = candidate.suggestedQuestion.trim()
  return suggestedQuestion.length > 0 && suggestedQuestion.length <= 2000
    ? suggestedQuestion
    : null
}

function activeStageIndex(snapshot: QuerySnapshot | undefined): number {
  if (!snapshot) return -1
  const stage = snapshot.status
  if (stage.includes('retriev')) return 0
  if (stage.includes('generat')) return 1
  if (stage.includes('validat')) return 2
  return isTerminal(snapshot) ? 3 : 0
}

function CitationButton({ citation, onOpen }: { citation: Citation; onOpen: () => void }) {
  return (
    <button className="citation-button" onClick={onOpen} type="button">
      <span>{citation.document_name}</span>
      <small>
        {citation.locator.kind === 'slide' ? '幻灯片' : '页'} {citation.locator.ordinal}
      </small>
      <ExternalLink aria-hidden="true" size={14} />
    </button>
  )
}

function queryTimeLabel(createdAt: string): string {
  const date = new Date(createdAt)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function questionTitle(question: string): string {
  const normalized = question.trim().replaceAll(/\s+/g, ' ')
  return normalized.length <= 60 ? normalized : `${normalized.slice(0, 59).trimEnd()}…`
}

function upsertConversation(
  conversations: ConversationRecord[] | undefined,
  conversation: ConversationRecord,
): ConversationRecord[] {
  return [
    conversation,
    ...(conversations ?? []).filter((item) => item.id !== conversation.id),
  ].toSorted((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))
}

function QueryTurn({
  initialSnapshot,
  onOpenCitation,
}: {
  initialSnapshot: QuerySnapshot
  onOpenCitation: (queryId: string, citationId: string) => void
}) {
  const queryClient = useQueryClient()
  const [streamConnection, setStreamConnection] = useState<
    Exclude<QueryStreamConnection, 'connecting'>
  >('reconnecting')
  const query = useQuery({
    queryKey: ['query', initialSnapshot.id],
    queryFn: () => studyApi.getQuery(initialSnapshot.id),
    enabled: !isTerminal(initialSnapshot),
    initialData: initialSnapshot,
    refetchInterval: (current) => queryRefetchInterval(current.state.data, streamConnection),
  })
  const snapshot =
    initialSnapshot.status === 'invalidated' ? initialSnapshot : query.data
  const streamActive = !isTerminal(snapshot)

  useEffect(() => {
    if (initialSnapshot.status !== 'invalidated') return
    queryClient.setQueryData<QuerySnapshot>(['query', initialSnapshot.id], initialSnapshot)
  }, [initialSnapshot, queryClient])

  useEffect(() => {
    queryClient.setQueryData<QuerySnapshot[]>(
      ['conversation-queries', snapshot.conversation_id],
      (current = []) => {
        const exists = current.some((item) => item.id === snapshot.id)
        const updated = exists
          ? current.map((item) => (item.id === snapshot.id ? snapshot : item))
          : [...current, snapshot]
        return updated.toSorted(
          (left, right) => Date.parse(left.created_at) - Date.parse(right.created_at),
        )
      },
    )
  }, [queryClient, snapshot])

  useEffect(() => {
    if (!streamActive) return
    const refresh = () => {
      void queryClient.invalidateQueries({ queryKey: ['query', snapshot.id] })
    }
    const markOpenAndRefresh = () => {
      setStreamConnection('open')
      refresh()
    }
    const markReconnectingAndRefresh = () => {
      setStreamConnection('reconnecting')
      refresh()
    }
    return studyApi.subscribe(
      `/queries/${snapshot.id}/events`,
      markOpenAndRefresh,
      markReconnectingAndRefresh,
      markOpenAndRefresh,
    )
  }, [queryClient, snapshot.id, streamActive])

  const answer = snapshot.answer
  const stageIndex = activeStageIndex(snapshot)
  const snapshotFailure =
    snapshot.status === 'failed' ? snapshot.failure_code ?? 'QUERY_FAILED' : null
  const snapshotProviderError = snapshotFailure?.startsWith('PROVIDER_') === true

  return (
    <div className="qa-turn" data-status={snapshot.status}>
      <article className="question-entry">
        <span>问题</span>
        <p>{snapshot.question}</p>
      </article>
      {!isTerminal(snapshot) ? (
        <ol className="answer-stages" aria-label={`${snapshot.question}的回答进度`}>
          {stages.map(({ key, label }, index) => {
            const complete = stageIndex > index
            const active = stageIndex === index
            return (
              <li className={active ? 'is-active' : complete ? 'is-complete' : ''} key={key}>
                {complete ? (
                  <Check aria-hidden="true" size={15} />
                ) : active ? (
                  <LoaderCircle aria-hidden="true" className="spin" size={15} />
                ) : (
                  <Circle aria-hidden="true" size={15} />
                )}
                <span>{label}</span>
              </li>
            )
          })}
        </ol>
      ) : null}
      {answer?.status === 'answered' ? (
        <article className="answer-entry">
          <header>
            <StatusBadge tone="success">已有来源</StatusBadge>
          </header>
          <div className="answer-markdown">{answer.answer_markdown}</div>
          <div className="claim-list">
            {answer.claims.map((claim) => (
              <section className="claim" key={claim.id}>
                <p>{claim.text}</p>
                <div className="citation-list">
                  {claim.citation_ids.map((citationId) => {
                    const citation = answer.citations.find((item) => item.id === citationId)
                    return citation ? (
                      <CitationButton
                        citation={citation}
                        key={citation.id}
                        onOpen={() => onOpenCitation(snapshot.id, citation.id)}
                      />
                    ) : null
                  })}
                </div>
              </section>
            ))}
          </div>
        </article>
      ) : null}
      {answer?.status === 'abstained' ? (
        <article className="abstained-entry">
          <ShieldCheck aria-hidden="true" size={20} />
          <div>
            <StatusBadge tone="warning">依据不足</StatusBadge>
            <p>{answer.refusal?.message ?? '当前课程资料没有足够依据回答该问题。'}</p>
          </div>
        </article>
      ) : null}
      {snapshot.status === 'invalidated' ? (
        <article className="abstained-entry">
          <AlertCircle aria-hidden="true" size={20} />
          <div>
            <StatusBadge tone="warning">来源已失效</StatusBadge>
            <p>这条回答依赖的资料已删除或更换，请重新提问。</p>
          </div>
        </article>
      ) : null}
      {snapshotFailure ? (
        <div className="provider-gate" role="alert">
          <AlertCircle aria-hidden="true" size={18} />
          <div>
            <strong>{snapshotProviderError ? 'Provider 调用失败' : '回答失败'}</strong>
            <p>{snapshotFailure}</p>
          </div>
        </div>
      ) : null}
      {query.isError ? <ErrorNotice error={query.error} title="无法更新回答状态" /> : null}
    </div>
  )
}

export function QAPage() {
  const { courseId, capabilities, capabilitiesError, capabilitiesLoading } = useWorkspace()
  const queryClient = useQueryClient()
  const location = useLocation()
  const navigate = useNavigate()
  const routeSuggestedQuestion = suggestedQuestionFromRouteState(location.state)
  const [question, setQuestion] = useState(() => routeSuggestedQuestion ?? '')
  const [requestedConversationId, setRequestedConversationId] = useState<
    string | null | undefined
  >(() => (routeSuggestedQuestion === null ? undefined : null))
  const [source, setSource] = useState<{
    courseId: string
    queryId: string
    value: CitationSource
  } | null>(null)
  const threadEndRef = useRef<HTMLDivElement>(null)
  const providerReady = capabilities?.provider.status === 'available'

  useEffect(() => {
    if (routeSuggestedQuestion === null) return
    void navigate(
      {
        pathname: location.pathname,
        search: location.search,
        hash: location.hash,
      },
      { replace: true, state: null },
    )
  }, [location.hash, location.pathname, location.search, navigate, routeSuggestedQuestion])

  const conversations = useQuery({
    queryKey: ['conversations', courseId],
    queryFn: () => studyApi.listConversations(courseId),
  })
  const conversationStateReady = conversations.isSuccess
  const conversationEntries = useMemo(
    () =>
      (conversations.data ?? [])
        .filter((item) => item.course_id === courseId)
        .toSorted((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at)),
    [conversations.data, courseId],
  )
  const latestConversationId = conversationEntries[0]?.id ?? null
  const requestedConversationAvailable = conversationEntries.some(
    (item) => item.id === requestedConversationId,
  )
  const conversationId =
    requestedConversationId === undefined
      ? latestConversationId
      : requestedConversationId === null
        ? null
        : requestedConversationAvailable
          ? requestedConversationId
          : latestConversationId
  const selectedConversation = conversationEntries.find((item) => item.id === conversationId)

  const conversationQueries = useQuery({
    queryKey: ['conversation-queries', conversationId],
    queryFn: () => studyApi.listConversationQueries(conversationId ?? ''),
    enabled: conversationId !== null,
    staleTime: 5_000,
  })
  const conversationHistoryReady =
    conversationId === null || conversationQueries.isSuccess
  const turns = useMemo(
    () =>
      (conversationQueries.data ?? [])
        .filter((item) => item.conversation_id === conversationId)
        .toSorted((left, right) => Date.parse(left.created_at) - Date.parse(right.created_at)),
    [conversationId, conversationQueries.data],
  )

  const createConversation = useMutation({
    mutationFn: () => studyApi.createConversation(courseId),
    onSuccess: (conversation) => {
      queryClient.setQueryData<ConversationRecord[]>(
        ['conversations', courseId],
        (current) => upsertConversation(current, conversation),
      )
      queryClient.setQueryData(['conversation-queries', conversation.id], [])
      setRequestedConversationId(conversation.id)
      setSource(null)
    },
  })

  const createQuery = useMutation({
    mutationFn: async (prompt: string) => {
      const targetConversation = selectedConversation
      const snapshot = targetConversation
        ? await studyApi.createQuery(courseId, prompt, targetConversation.id)
        : await studyApi.createQuery(courseId, prompt)
      return { conversation: targetConversation, prompt, snapshot }
    },
    onSuccess: ({ conversation, prompt, snapshot }) => {
      const baseConversation: ConversationRecord = conversation ?? {
        id: snapshot.conversation_id,
        course_id: snapshot.course_id,
        title: questionTitle(prompt),
        turn_count: 0,
        latest_query_id: null,
        latest_question: null,
        created_at: snapshot.created_at,
        updated_at: snapshot.created_at,
      }
      const updatedConversation: ConversationRecord = {
        ...baseConversation,
        title:
          baseConversation.turn_count === 0 && baseConversation.title === '新会话'
            ? questionTitle(prompt)
            : baseConversation.title,
        turn_count: baseConversation.turn_count + 1,
        latest_query_id: snapshot.id,
        latest_question: snapshot.question,
        updated_at: snapshot.created_at,
      }
      queryClient.setQueryData<ConversationRecord[]>(
        ['conversations', courseId],
        (current) => upsertConversation(current, updatedConversation),
      )
      queryClient.setQueryData(['query', snapshot.id], snapshot)
      queryClient.setQueryData<QuerySnapshot[]>(
        ['conversation-queries', snapshot.conversation_id],
        (current = []) =>
          [...current.filter((item) => item.id !== snapshot.id), snapshot].toSorted(
            (left, right) => Date.parse(left.created_at) - Date.parse(right.created_at),
          ),
      )
      setRequestedConversationId(snapshot.conversation_id)
      setQuestion('')
    },
  })

  const citationMutation = useMutation({
    mutationFn: ({ queryId, citationId }: { courseId: string; queryId: string; citationId: string }) =>
      studyApi.getCitation(queryId, citationId),
    onSuccess: (value, variables) =>
      setSource({ courseId: variables.courseId, queryId: variables.queryId, value }),
  })

  const latestTurnId = turns.at(-1)?.id
  useEffect(() => {
    if (!latestTurnId) return
    requestAnimationFrame(() => threadEndRef.current?.scrollIntoView?.({ block: 'nearest' }))
  }, [conversationId, latestTurnId])

  const providerError =
    createQuery.error instanceof ApiError &&
    [
      'PROVIDER_NOT_CONFIGURED',
      'PROVIDER_TIMEOUT',
      'PROVIDER_UNAVAILABLE',
      'PROVIDER_RATE_LIMITED',
    ].includes(createQuery.error.problem.code)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const prompt = question.trim()
    if (
      !providerReady ||
      !prompt ||
      !conversationStateReady ||
      !conversationHistoryReady ||
      createQuery.isPending ||
      createConversation.isPending
    )
      return
    setSource(null)
    createQuery.reset()
    createQuery.mutate(prompt)
  }

  return (
    <div className="page page--qa">
      <PageHeader kicker="QA" meta="回答只使用当前课程的活动来源" title="课程问答" />
      {!capabilitiesLoading && !providerReady ? (
        <div className="provider-gate" role="status">
          <AlertCircle aria-hidden="true" size={18} />
          <div>
            <strong>{capabilitiesError ? 'Provider 状态不可用' : 'Provider 未配置'}</strong>
            <p>资料浏览保持可用，问答提交已关闭。</p>
          </div>
        </div>
      ) : null}
      <section aria-labelledby="qa-conversations-title" className="qa-conversations">
        <div className="qa-conversations__heading">
          <History aria-hidden="true" size={16} />
          <h2 id="qa-conversations-title">会话</h2>
          {conversations.isFetching ? <span>正在同步</span> : null}
          <button
            className="button button--small qa-conversations__new"
            disabled={
              !conversationStateReady || createConversation.isPending || createQuery.isPending
            }
            onClick={() => createConversation.mutate()}
            type="button"
          >
            {createConversation.isPending ? (
              <LoaderCircle aria-hidden="true" className="spin" size={15} />
            ) : (
              <MessageSquarePlus aria-hidden="true" size={15} />
            )}
            新建会话
          </button>
        </div>
        {conversations.isError ? (
          <ErrorNotice error={conversations.error} title="无法加载会话" />
        ) : conversations.isPending ? (
          <p className="qa-conversations__empty">正在加载会话</p>
        ) : conversationEntries.length === 0 ? (
          <p className="qa-conversations__empty">暂无会话，直接提问即可开始</p>
        ) : (
          <ol aria-label="问答会话" className="qa-conversations__list">
            {conversationEntries.map((conversation) => (
              <li key={conversation.id}>
                <button
                  aria-current={conversation.id === conversationId ? 'true' : undefined}
                  className={
                    conversation.id === conversationId
                      ? 'qa-conversations__item is-active'
                      : 'qa-conversations__item'
                  }
                  disabled={createQuery.isPending}
                  onClick={() => {
                    setRequestedConversationId(conversation.id)
                    setSource(null)
                  }}
                  type="button"
                >
                  <span>{conversation.title}</span>
                  <small>
                    {conversation.turn_count} 轮
                    {queryTimeLabel(conversation.updated_at)
                      ? ` · ${queryTimeLabel(conversation.updated_at)}`
                      : ''}
                  </small>
                  {conversation.latest_question ? <em>{conversation.latest_question}</em> : null}
                </button>
              </li>
            ))}
          </ol>
        )}
        {createConversation.isError ? (
          <ErrorNotice error={createConversation.error} title="无法新建会话" />
        ) : null}
      </section>
      <section
        aria-label={selectedConversation ? `${selectedConversation.title}消息` : '会话消息'}
        aria-live="polite"
        className="qa-thread"
      >
        {conversations.isPending ? (
          <div className="qa-empty">
            <LoaderCircle aria-hidden="true" className="spin" size={26} />
            <h3>正在加载会话</h3>
          </div>
        ) : conversationQueries.isError ? (
          <ErrorNotice error={conversationQueries.error} title="无法加载会话内容" />
        ) : conversationId !== null && conversationQueries.isPending ? (
          <div className="qa-empty">
            <LoaderCircle aria-hidden="true" className="spin" size={26} />
            <h3>正在加载会话</h3>
          </div>
        ) : turns.length === 0 ? (
          <div className="qa-empty">
            <Search aria-hidden="true" size={26} />
            <h3>{conversationId ? '在此会话中提出第一个问题' : '输入第一个问题开始会话'}</h3>
          </div>
        ) : (
          turns.map((snapshot) => (
            <QueryTurn
              initialSnapshot={snapshot}
              key={snapshot.id}
              onOpenCitation={(queryId, citationId) =>
                citationMutation.mutate({ courseId, queryId, citationId })
              }
            />
          ))
        )}
        {providerError ? (
          <ErrorNotice error={createQuery.error} title="Provider 调用失败" />
        ) : createQuery.isError ? (
          <ErrorNotice error={createQuery.error} title="问题未提交" />
        ) : null}
        {citationMutation.isError ? (
          <ErrorNotice error={citationMutation.error} title="来源不可用" />
        ) : null}
        <div aria-hidden="true" ref={threadEndRef} />
      </section>
      <form className="question-composer" onSubmit={submit}>
        <label className="sr-only" htmlFor="course-question">
          课程问题
        </label>
        <textarea
          disabled={!providerReady || capabilitiesLoading}
          id="course-question"
          maxLength={2000}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={providerReady ? '输入课程问题' : 'Provider 不可用'}
          rows={2}
          value={question}
        />
        <button
          aria-label="提交问题"
          className="composer-submit"
          disabled={
            !providerReady ||
            !question.trim() ||
            !conversationStateReady ||
            !conversationHistoryReady ||
            createQuery.isPending ||
            createConversation.isPending
          }
          type="submit"
        >
          {createQuery.isPending ? (
            <LoaderCircle aria-hidden="true" className="spin" size={18} />
          ) : (
            <ArrowUp aria-hidden="true" size={18} />
          )}
        </button>
      </form>
      <SourceViewer
        onClose={() => setSource(null)}
        source={source?.courseId === courseId ? source.value : null}
      />
    </div>
  )
}
