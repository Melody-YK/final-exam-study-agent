import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  ArrowUp,
  Check,
  Circle,
  ExternalLink,
  LoaderCircle,
  Search,
  ShieldCheck,
} from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'

import { ApiError, studyApi } from '../../api/client'
import type { Citation, CitationSource, QuerySnapshot } from '../../api/types'
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

export function QAPage() {
  const { courseId, capabilities, capabilitiesError, capabilitiesLoading } = useWorkspace()
  const queryClient = useQueryClient()
  const [question, setQuestion] = useState('')
  const [queryId, setQueryId] = useState<string | null>(null)
  const [source, setSource] = useState<CitationSource | null>(null)
  const [streamState, setStreamState] = useState<{
    queryId: string
    connection: Exclude<QueryStreamConnection, 'connecting'>
  }>({ queryId: '', connection: 'reconnecting' })
  const providerReady = capabilities?.provider.status === 'available'

  const createQuery = useMutation({
    mutationFn: () => studyApi.createQuery(courseId, question.trim()),
    onSuccess: (snapshot) => setQueryId(snapshot.id),
  })
  const createdSnapshot =
    queryId && createQuery.data?.id === queryId ? createQuery.data : undefined
  const streamConnection: QueryStreamConnection =
    queryId && streamState.queryId === queryId ? streamState.connection : 'connecting'
  const query = useQuery({
    queryKey: ['query', queryId],
    queryFn: () => studyApi.getQuery(queryId ?? ''),
    enabled: queryId !== null && !isTerminal(createdSnapshot),
    initialData: createdSnapshot,
    refetchInterval: (current) =>
      queryRefetchInterval(current.state.data, streamConnection),
  })
  const citationMutation = useMutation({
    mutationFn: ({ queryId: id, citationId }: { queryId: string; citationId: string }) =>
      studyApi.getCitation(id, citationId),
    onSuccess: setSource,
  })
  const snapshot = query.data ?? createQuery.data
  const streamActive = queryId !== null && !isTerminal(snapshot)

  useEffect(() => {
    if (!queryId || !streamActive) return
    const refresh = () => {
      void queryClient.invalidateQueries({ queryKey: ['query', queryId] })
    }
    const markOpenAndRefresh = () => {
      setStreamState({ queryId, connection: 'open' })
      refresh()
    }
    const markReconnectingAndRefresh = () => {
      setStreamState({ queryId, connection: 'reconnecting' })
      refresh()
    }
    return studyApi.subscribe(
      `/queries/${queryId}/events`,
      markOpenAndRefresh,
      markReconnectingAndRefresh,
      markOpenAndRefresh,
    )
  }, [queryClient, queryId, streamActive])

  const answer = snapshot?.answer
  const stageIndex = activeStageIndex(snapshot)
  const providerError =
    createQuery.error instanceof ApiError &&
    ['PROVIDER_NOT_CONFIGURED', 'PROVIDER_TIMEOUT', 'PROVIDER_UNAVAILABLE', 'PROVIDER_RATE_LIMITED'].includes(
      createQuery.error.problem.code,
    )
  const snapshotFailure = snapshot?.status === 'failed' ? snapshot.failure_code ?? 'QUERY_FAILED' : null
  const snapshotProviderError = snapshotFailure?.startsWith('PROVIDER_') === true

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!providerReady || !question.trim() || createQuery.isPending) return
    setQueryId(null)
    createQuery.reset()
    createQuery.mutate()
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
      <section className="qa-thread" aria-live="polite">
        {snapshot ? (
          <article className="question-entry">
            <span>问题</span>
            <p>{snapshot.question}</p>
          </article>
        ) : (
          <div className="qa-empty">
            <Search aria-hidden="true" size={26} />
            <h3>等待问题</h3>
          </div>
        )}
        {snapshot && !isTerminal(snapshot) ? (
          <ol className="answer-stages" aria-label="回答进度">
            {stages.map(({ key, label }, index) => {
              const complete = stageIndex > index
              const active = stageIndex === index
              return (
                <li className={active ? 'is-active' : complete ? 'is-complete' : ''} key={key}>
                  {complete ? <Check aria-hidden="true" size={15} /> : active ? <LoaderCircle aria-hidden="true" className="spin" size={15} /> : <Circle aria-hidden="true" size={15} />}
                  <span>{label}</span>
                </li>
              )
            })}
          </ol>
        ) : null}
        {answer?.status === 'answered' && snapshot ? (
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
                          onOpen={() => citationMutation.mutate({ queryId: snapshot.id, citationId: citation.id })}
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
        {snapshotFailure ? (
          <div className="provider-gate" role="alert">
            <AlertCircle aria-hidden="true" size={18} />
            <div>
              <strong>{snapshotProviderError ? 'Provider 调用失败' : '回答失败'}</strong>
              <p>{snapshotFailure}</p>
            </div>
          </div>
        ) : null}
        {providerError ? (
          <ErrorNotice error={createQuery.error} title="Provider 调用失败" />
        ) : createQuery.isError ? (
          <ErrorNotice error={createQuery.error} title="问题未提交" />
        ) : null}
        {query.isError ? <ErrorNotice error={query.error} title="无法更新回答状态" /> : null}
        {citationMutation.isError ? <ErrorNotice error={citationMutation.error} title="来源不可用" /> : null}
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
          disabled={!providerReady || !question.trim() || createQuery.isPending}
          type="submit"
        >
          {createQuery.isPending ? <LoaderCircle aria-hidden="true" className="spin" size={18} /> : <ArrowUp aria-hidden="true" size={18} />}
        </button>
      </form>
      <SourceViewer onClose={() => setSource(null)} source={source} />
    </div>
  )
}
