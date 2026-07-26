import { useQuery } from '@tanstack/react-query'
import { Activity, Braces, Clock3, Database, FlaskConical, LoaderCircle, ShieldCheck } from 'lucide-react'

import { studyApi } from '../../api/client'
import { useWorkspace } from '../../app/WorkspaceContext'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { PageHeader } from '../../components/ui/PageHeader'
import { StatusBadge } from '../../components/ui/StatusBadge'

const routeLabels = {
  dense: 'Dense',
  lexical: 'BM25',
  rrf: 'RRF',
  rerank: 'Rerank',
} as const

const timingStageOrder = ['dense', 'lexical', 'fusion', 'rerank', 'total'] as const

const timingStageLabels: Record<(typeof timingStageOrder)[number], { detail: string; label: string }> = {
  dense: {
    detail: 'Dense · 按语义相似度召回资料片段',
    label: '语义检索',
  },
  lexical: {
    detail: 'BM25 · 按原文关键词召回资料片段',
    label: '关键词检索',
  },
  fusion: {
    detail: 'RRF · 合并语义与关键词候选排序',
    label: '结果融合',
  },
  rerank: {
    detail: 'Rerank · 再次按问题相关性排序',
    label: '精细重排',
  },
  total: {
    detail: 'Total · 含资料读取，不含 AI 回答生成',
    label: '检索总耗时',
  },
}

function orderedTimingEntries(timings: Record<string, number>): Array<readonly [string, number]> {
  const knownEntries = timingStageOrder.reduce<Array<readonly [string, number]>>((entries, name) => {
    const duration = timings[name]
    if (duration !== undefined) {
      entries.push([name, duration])
    }
    return entries
  }, [])
  const knownNames = new Set<string>(timingStageOrder)
  const extraEntries = Object.entries(timings).filter(([name]) => !knownNames.has(name))

  return [...knownEntries, ...extraEntries]
}

export function DemoLabPage() {
  const { courseId, capabilities, capabilitiesLoading } = useWorkspace()
  const enabled = capabilities?.demo_lab_enabled === true
  const traceQuery = useQuery({
    queryKey: ['lab-trace', courseId],
    queryFn: () => studyApi.getLabTrace(courseId),
    enabled,
    retry: false,
  })
  const trace = traceQuery.data
  const maxTiming = trace ? Math.max(1, ...Object.values(trace.timings_ms)) : 1

  return (
    <div className="page page--lab">
      <PageHeader kicker="Demo Lab" meta="只读脱敏 Trace" title="工程链路" />
      {!capabilitiesLoading && !enabled ? (
        <section className="page-state">
          <FlaskConical aria-hidden="true" size={24} />
          <h3>Demo Lab 已关闭</h3>
        </section>
      ) : traceQuery.isLoading ? (
        <section className="loading-state">
          <LoaderCircle aria-hidden="true" className="spin" size={20} />
          <span>加载 Trace</span>
        </section>
      ) : traceQuery.isError ? (
        <ErrorNotice error={traceQuery.error} onRetry={() => void traceQuery.refetch()} title="Trace 不可用" />
      ) : trace ? (
        <div className="lab-layout">
          <section className="lab-summary" aria-label="链路摘要">
            <div>
              <Activity aria-hidden="true" size={18} />
              <span>模式</span>
              <strong>{trace.mode}</strong>
            </div>
            <div>
              <Braces aria-hidden="true" size={18} />
              <span>Parser</span>
              <strong>{trace.parser_backend ?? '不可用'}</strong>
            </div>
            <div>
              <Database aria-hidden="true" size={18} />
              <span>Embedding</span>
              <strong>{trace.embedding_model ?? '不可用'}</strong>
            </div>
            <div>
              <ShieldCheck aria-hidden="true" size={18} />
              <span>引用校验</span>
              <strong>{trace.citation_validation ?? '不可用'}</strong>
            </div>
          </section>
          <section className="lab-section">
            <header>
              <h3>检索候选</h3>
              <StatusBadge tone="info">{trace.candidates.length} 条</StatusBadge>
            </header>
            <div className="trace-table-wrap">
              <table className="trace-table">
                <thead>
                  <tr>
                    <th scope="col">路线</th>
                    <th scope="col">排名</th>
                    <th scope="col">分数</th>
                    <th scope="col">来源</th>
                  </tr>
                </thead>
                <tbody>
                  {trace.candidates.map((candidate) => (
                    <tr key={`${candidate.route}-${candidate.chunk_id}`}>
                      <td>{routeLabels[candidate.route]}</td>
                      <td>{candidate.rank}</td>
                      <td>{candidate.score.toFixed(4)}</td>
                      <td>片段 {candidate.chunk_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="lab-section lab-timings">
            <header>
              <h3>阶段耗时</h3>
              <Clock3 aria-hidden="true" size={17} />
            </header>
            <dl>
              {orderedTimingEntries(trace.timings_ms).map(([name, duration]) => (
                <div key={name}>
                  <dt>
                    <span>{timingStageLabels[name as keyof typeof timingStageLabels]?.label ?? name}</span>
                    {name in timingStageLabels ? (
                      <small>{timingStageLabels[name as keyof typeof timingStageLabels].detail}</small>
                    ) : null}
                  </dt>
                  <dd>
                    <span style={{ width: `${Math.max(2, (duration / maxTiming) * 100)}%` }} />
                    <strong>{duration.toFixed(1)} ms</strong>
                  </dd>
                </div>
              ))}
            </dl>
          </section>
          <section className="lab-footer-band">
            <span>Revision {trace.revision_id ?? '不可用'}</span>
            <span>Tokenizer {trace.tokenizer_version ?? '不可用'}</span>
            <span>
              输入{' '}
              {trace.usage?.input_tokens == null ? '不可用' : `${trace.usage.input_tokens} tokens`}
            </span>
            <span>
              输出{' '}
              {trace.usage?.output_tokens == null ? '不可用' : `${trace.usage.output_tokens} tokens`}
            </span>
            <span>
              费用{' '}
              {trace.usage?.estimated_cost == null
                ? '不可用'
                : trace.usage.estimated_cost.toFixed(6)}
            </span>
            {trace.refusal_reason ? <span>拒答：{trace.refusal_reason}</span> : null}
          </section>
        </div>
      ) : null}
    </div>
  )
}
