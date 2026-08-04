import { QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, studyApi } from '../../api/client'
import type {
  ConversationRecord,
  LearnerMemoryRecord,
  QuerySnapshot,
  RuntimeCapabilities,
  StructuredAnswer,
} from '../../api/types'
import { WorkspaceContext } from '../../app/WorkspaceContext'
import { answeredSnapshot, citationSource, problem } from '../../test/fixtures'
import { availableCapabilities, createTestQueryClient, renderInWorkspace } from '../../test/render'
import { QAPage } from './QAPage'
import { queryRefetchInterval } from './queryPolling'

async function submitQuestion(question = '什么是进程？') {
  const input = screen.getByLabelText('课程问题')
  const submit = screen.getByRole('button', { name: '提交问题' })
  await screen.findByRole('button', { name: '提交问题' })
  const user = (await import('@testing-library/user-event')).default.setup()
  await user.type(input, question)
  await user.click(submit)
  return { input, submit, user }
}

function conversation(
  id: string,
  title: string,
  overrides: Partial<ConversationRecord> = {},
): ConversationRecord {
  return {
    id,
    course_id: 'course-1',
    title,
    turn_count: 0,
    latest_query_id: null,
    latest_question: null,
    created_at: '2026-07-19T04:00:00Z',
    updated_at: '2026-07-19T04:00:00Z',
    ...overrides,
  }
}

function historySnapshot(
  id: string,
  conversationId: string,
  question: string,
  answerMarkdown: string,
  createdAt: string,
): QuerySnapshot {
  const snapshot = answeredSnapshot({
    id,
    conversation_id: conversationId,
    question,
    created_at: createdAt,
  })
  return {
    ...snapshot,
    answer: snapshot.answer
      ? {
          ...snapshot.answer,
          query_id: id,
          answer_markdown: answerMarkdown,
          claims: [],
          citations: [],
        }
      : null,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}

function RouteStateProbe() {
  const location = useLocation()
  return <output data-testid="qa-route-state">{JSON.stringify(location.state)}</output>
}

function RouteStateExperience() {
  return (
    <>
      <QAPage />
      <RouteStateProbe />
    </>
  )
}

function renderWithRouteState(state: unknown) {
  const queryClient = createTestQueryClient()

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[{ pathname: '/qa', state }]}>
          <WorkspaceContext.Provider
            value={{
              courseId: 'course-1',
              course: {
                id: 'course-1',
                title: '操作系统',
                lifecycle: 'active',
              },
              capabilities: availableCapabilities,
              capabilitiesLoading: false,
              capabilitiesError: false,
            }}
          >
            {children}
          </WorkspaceContext.Provider>
        </MemoryRouter>
      </QueryClientProvider>
    )
  }

  return {
    queryClient,
    user: userEvent.setup(),
    ...render(<RouteStateExperience />, { wrapper: Wrapper }),
  }
}

describe('QAPage', () => {
  beforeEach(() => {
    vi.spyOn(studyApi, 'listConversations').mockResolvedValue([])
    vi.spyOn(studyApi, 'listConversationQueries').mockResolvedValue([])
    vi.spyOn(studyApi, 'createConversation').mockResolvedValue(
      conversation('conversation-1', '新会话'),
    )
  })

  it('consumes a concept suggestion once as an unsent draft in a fresh conversation', async () => {
    const existing = conversation('conversation-existing', '已有会话', {
      turn_count: 1,
      latest_query_id: 'query-existing',
      latest_question: '已有问题',
    })
    vi.mocked(studyApi.listConversations).mockResolvedValue([existing])
    const createQuery = vi.spyOn(studyApi, 'createQuery').mockResolvedValue(answeredSnapshot())
    const suggestedQuestion = '请解释“进程”，并结合课程资料说明它与相关概念的联系。'
    const { rerender, user } = renderWithRouteState({
      suggestedQuestion,
      startNewConversation: true,
    })

    expect(screen.getByLabelText('课程问题')).toHaveValue(suggestedQuestion)
    expect(await screen.findByRole('button', { name: /已有会话/ })).not.toHaveAttribute(
      'aria-current',
    )
    expect(screen.getByText('输入第一个问题开始会话')).toBeInTheDocument()
    expect(studyApi.listConversationQueries).not.toHaveBeenCalled()
    expect(createQuery).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByTestId('qa-route-state')).toHaveTextContent('null'))

    await user.clear(screen.getByLabelText('课程问题'))
    await user.type(screen.getByLabelText('课程问题'), '保留我的修改')
    rerender(<RouteStateExperience />)

    expect(screen.getByLabelText('课程问题')).toHaveValue('保留我的修改')
    expect(createQuery).not.toHaveBeenCalled()
  })

  it('submits validated graph anchors with the concept draft after route state is cleared', async () => {
    const createQuery = vi.spyOn(studyApi, 'createQuery').mockResolvedValue(answeredSnapshot())
    const suggestedQuestion =
      '根据当前课程资料，概括“进程”在课程内容中的含义，并说明它与直接关联概念的联系。'
    const conceptContext = {
      label: '进程',
      anchors: [
        {
          document_id: 'document-1',
          revision_id: 'revision-1',
          chunk_id: 'chunk-1',
        },
      ],
    }
    const { user } = renderWithRouteState({
      suggestedQuestion,
      startNewConversation: true,
      conceptContext,
    })

    await screen.findByText('暂无会话，直接提问即可开始')
    await waitFor(() => expect(screen.getByTestId('qa-route-state')).toHaveTextContent('null'))
    await user.click(screen.getByRole('button', { name: '提交问题' }))

    expect(createQuery).toHaveBeenCalledWith(
      'course-1',
      suggestedQuestion,
      undefined,
      conceptContext,
    )
  })

  it('drops graph anchors when the edited question no longer names the concept', async () => {
    const createQuery = vi.spyOn(studyApi, 'createQuery').mockResolvedValue(answeredSnapshot())
    const { user } = renderWithRouteState({
      suggestedQuestion:
        '根据当前课程资料，概括“进程”在课程内容中的含义，并说明它与直接关联概念的联系。',
      startNewConversation: true,
      conceptContext: {
        label: '进程',
        anchors: [
          {
            document_id: 'document-1',
            revision_id: 'revision-1',
            chunk_id: 'chunk-1',
          },
        ],
      },
    })

    const composer = screen.getByLabelText('课程问题')
    await user.clear(composer)
    await user.type(composer, '请说明线程是什么')
    await user.click(screen.getByRole('button', { name: '提交问题' }))

    expect(createQuery).toHaveBeenCalledWith('course-1', '请说明线程是什么')
  })

  it.each([
    ['array state', [{ suggestedQuestion: '不应采用', startNewConversation: true }]],
    ['missing new-conversation flag', { suggestedQuestion: '不应采用' }],
    ['blank suggestion', { suggestedQuestion: '   ', startNewConversation: true }],
    ['oversized suggestion', { suggestedQuestion: '问'.repeat(2001), startNewConversation: true }],
  ])('ignores malformed concept draft route state: %s', async (_label, state) => {
    const createQuery = vi.spyOn(studyApi, 'createQuery').mockResolvedValue(answeredSnapshot())

    renderWithRouteState(state)

    expect(screen.getByLabelText('课程问题')).toHaveValue('')
    await screen.findByText('暂无会话，直接提问即可开始')
    expect(createQuery).not.toHaveBeenCalled()
  })

  it('polls only while the query SSE is connecting or reconnecting', () => {
    const pending = answeredSnapshot({ status: 'retrieving', answer: null })

    expect(queryRefetchInterval(pending, 'connecting')).toBe(10_000)
    expect(queryRefetchInterval(pending, 'reconnecting')).toBe(10_000)
    expect(queryRefetchInterval(pending, 'open')).toBe(false)
    expect(queryRefetchInterval(answeredSnapshot(), 'reconnecting')).toBe(false)
  })

  it('shows retrieval progress until a nonterminal query reaches an answered snapshot', async () => {
    const pending = answeredSnapshot({
      status: 'retrieving',
      answer: null,
      completed_at: null,
    })
    let pushEvent: () => void = () => undefined
    const close = vi.fn()
    vi.spyOn(studyApi, 'createQuery').mockResolvedValue(pending)
    vi.spyOn(studyApi, 'getQuery')
      .mockResolvedValueOnce(pending)
      .mockResolvedValueOnce(answeredSnapshot())
    vi.spyOn(studyApi, 'subscribe').mockImplementation((_path, onEvent) => {
      pushEvent = () =>
        onEvent({
          stream_version: '1',
          sequence: 4,
          occurred_at: '2026-07-19T06:00:00Z',
          trace_id: 'query-sse-test',
          event_type: 'query.completed',
          data: { status: 'answered' },
        })
      return close
    })
    renderInWorkspace(<QAPage />)

    await submitQuestion()

    const retrievalStage = await screen.findByText('检索课程资料')
    expect(retrievalStage.closest('li')).toHaveClass('is-active')
    await waitFor(() =>
      expect(studyApi.subscribe).toHaveBeenCalledWith(
        '/queries/query-1/events',
        expect.any(Function),
        expect.any(Function),
        expect.any(Function),
      ),
    )
    await act(async () => pushEvent())
    expect(await screen.findByText('进程是资源分配的基本单位。')).toBeInTheDocument()
    await waitFor(() => expect(close).toHaveBeenCalledOnce())
  })

  it('renders an answered claim and opens its query-scoped source', async () => {
    vi.spyOn(studyApi, 'createQuery').mockResolvedValue(answeredSnapshot())
    vi.spyOn(studyApi, 'getCitation').mockResolvedValue(citationSource())
    const { container } = renderInWorkspace(<QAPage />)

    const { user } = await submitQuestion()

    expect(await screen.findByText('进程是资源分配的基本单位。')).toBeInTheDocument()
    expect(screen.getByText('进程拥有独立地址空间。')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /chapter-1\.png/ }))

    const dialog = await screen.findByRole('dialog', { name: '来源' })
    expect(dialog).toHaveTextContent('进程拥有独立的地址空间。')
    const image = screen.getByRole('img', { name: /chapter-1\.png 第 3 页/ })
    fireEvent.load(image)
    const highlight = container.querySelector('.bbox-highlight')
    expect(highlight).toHaveStyle({
      left: '10%',
      top: '20%',
      width: '40%',
      height: '8%',
    })
    expect(studyApi.getCitation).toHaveBeenCalledWith('query-1', 'citation-1')
  })

  it('labels Markdown citations as sections instead of pages', async () => {
    const snapshot = answeredSnapshot()
    const sourceCitation = snapshot.answer?.citations[0]
    if (!sourceCitation) throw new Error('answered fixture must include a citation')
    const markdownCitation = {
      ...sourceCitation,
      document_name: 'outline.md',
      locator: { kind: 'section' as const, ordinal: 2 },
    }
    vi.spyOn(studyApi, 'createQuery').mockResolvedValue({
      ...snapshot,
      answer: {
        ...snapshot.answer!,
        citations: [markdownCitation],
      },
    })
    renderInWorkspace(<QAPage />)

    await submitQuestion()

    const citationButton = await screen.findByRole('button', {
      name: /outline\.md/,
    })
    expect(citationButton).toHaveTextContent('章节 2')
    expect(citationButton).not.toHaveTextContent('页 2')
  })

  it('shows migrated questions as one chronological thread and restores it after remount', async () => {
    const migrated = conversation('conversation-migrated', '历史问答', {
      turn_count: 2,
      latest_query_id: 'query-latest',
      latest_question: '最新问题',
      updated_at: '2026-07-19T04:00:00Z',
    })
    const older = historySnapshot(
      'query-older',
      migrated.id,
      '旧问题',
      '旧回答',
      '2026-07-18T04:00:00Z',
    )
    const latest = historySnapshot(
      'query-latest',
      migrated.id,
      '最新问题',
      '最新回答',
      '2026-07-19T04:00:00Z',
    )
    vi.mocked(studyApi.listConversations).mockResolvedValue([migrated])
    vi.mocked(studyApi.listConversationQueries).mockResolvedValue([latest, older])

    const firstRender = renderInWorkspace(<QAPage />)

    expect(await screen.findByText('旧回答')).toBeInTheDocument()
    expect(screen.getByText('最新回答')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /历史问答/ })).toHaveAttribute('aria-current', 'true')
    const questions = firstRender.container.querySelectorAll('.question-entry p')
    expect(questions[0]).toHaveTextContent('旧问题')
    expect(questions[1]).toHaveTextContent('最新问题')

    firstRender.unmount()
    renderInWorkspace(<QAPage />)

    expect(await screen.findByText('旧回答')).toBeInTheDocument()
    expect(screen.getByText('最新回答')).toBeInTheDocument()
    expect(studyApi.listConversations).toHaveBeenCalledTimes(2)
    expect(studyApi.listConversationQueries).toHaveBeenCalledTimes(2)
  })

  it('keeps a server-invalidated turn closed when an older answered snapshot is cached', async () => {
    const migrated = conversation('conversation-invalidated', '历史问答', {
      turn_count: 1,
      latest_query_id: 'query-invalidated',
      latest_question: '旧问题',
    })
    const cachedAnswer = historySnapshot(
      'query-invalidated',
      migrated.id,
      '旧问题',
      '已失效的旧回答',
      '2026-07-19T04:00:00Z',
    )
    const invalidated: QuerySnapshot = {
      ...cachedAnswer,
      status: 'invalidated',
      answer: null,
    }
    const history = deferred<QuerySnapshot[]>()
    vi.mocked(studyApi.listConversations).mockResolvedValue([migrated])
    vi.mocked(studyApi.listConversationQueries).mockReturnValue(history.promise)
    const { queryClient } = renderInWorkspace(<QAPage />)
    queryClient.setQueryData(['query', cachedAnswer.id], cachedAnswer)

    await act(async () => history.resolve([invalidated]))

    expect(await screen.findByText('来源已失效')).toBeInTheDocument()
    expect(screen.queryByText('已失效的旧回答')).not.toBeInTheDocument()
    await waitFor(() =>
      expect(queryClient.getQueryData<QuerySnapshot>(['query', cachedAnswer.id])).toMatchObject({
        status: 'invalidated',
        answer: null,
      }),
    )
    expect(
      queryClient.getQueryData<QuerySnapshot[]>(['conversation-queries', migrated.id]),
    ).toEqual([
      expect.objectContaining({
        id: cachedAnswer.id,
        status: 'invalidated',
        answer: null,
      }),
    ])
  })

  it('switches between conversations and loads each available thread', async () => {
    const olderConversation = conversation('conversation-older', '同步机制复习', {
      turn_count: 1,
      updated_at: '2026-07-18T04:00:00Z',
    })
    const latestConversation = conversation('conversation-latest', '进程复习', {
      turn_count: 1,
      updated_at: '2026-07-19T04:00:00Z',
    })
    const older = historySnapshot(
      'query-older',
      olderConversation.id,
      '什么是临界区？',
      '临界区是访问临界资源的代码。',
      '2026-07-18T04:00:00Z',
    )
    const latest = historySnapshot(
      'query-latest',
      latestConversation.id,
      '什么是进程？',
      '进程是程序的一次执行。',
      '2026-07-19T04:00:00Z',
    )
    vi.mocked(studyApi.listConversations).mockResolvedValue([olderConversation, latestConversation])
    vi.mocked(studyApi.listConversationQueries).mockImplementation(async (id) =>
      id === olderConversation.id ? [older] : [latest],
    )
    const { user } = renderInWorkspace(<QAPage />)

    expect(await screen.findByText('进程是程序的一次执行。')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /同步机制复习/ }))

    expect(await screen.findByText('临界区是访问临界资源的代码。')).toBeInTheDocument()
    expect(screen.queryByText('进程是程序的一次执行。')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /同步机制复习/ })).toHaveAttribute(
      'aria-current',
      'true',
    )
  })

  it('waits for selected conversation history before enabling submission', async () => {
    const existing = conversation('conversation-existing', '已有会话', {
      turn_count: 1,
    })
    const history = deferred<QuerySnapshot[]>()
    const created = answeredSnapshot({
      conversation_id: existing.id,
      question: '继续解释',
    })
    vi.mocked(studyApi.listConversations).mockResolvedValue([existing])
    vi.mocked(studyApi.listConversationQueries).mockReturnValue(history.promise)
    const createQuery = vi.spyOn(studyApi, 'createQuery').mockResolvedValue(created)
    const { user } = renderInWorkspace(<QAPage />)

    await screen.findByRole('button', { name: /已有会话/ })
    await user.type(screen.getByLabelText('课程问题'), '继续解释')
    const submit = screen.getByRole('button', { name: '提交问题' })
    expect(submit).toBeDisabled()
    await user.click(submit)
    expect(createQuery).not.toHaveBeenCalled()

    await act(async () => history.resolve([]))
    await waitFor(() => expect(submit).toBeEnabled())
    await user.click(submit)

    await waitFor(() =>
      expect(createQuery).toHaveBeenCalledWith('course-1', '继续解释', existing.id),
    )
  })

  it('locks conversation navigation while a query is pending', async () => {
    const older = conversation('conversation-older', '同步机制复习', {
      updated_at: '2026-07-18T04:00:00Z',
    })
    const latest = conversation('conversation-latest', '进程复习', {
      updated_at: '2026-07-19T04:00:00Z',
    })
    const pendingQuery = deferred<QuerySnapshot>()
    vi.mocked(studyApi.listConversations).mockResolvedValue([older, latest])
    vi.mocked(studyApi.listConversationQueries).mockResolvedValue([])
    const createQuery = vi.spyOn(studyApi, 'createQuery').mockReturnValue(pendingQuery.promise)
    const { user } = renderInWorkspace(<QAPage />)

    const latestButton = await screen.findByRole('button', {
      name: /进程复习/,
    })
    const olderButton = screen.getByRole('button', { name: /同步机制复习/ })
    const newConversation = screen.getByRole('button', { name: '新建会话' })
    await user.type(screen.getByLabelText('课程问题'), '继续解释')
    await user.click(screen.getByRole('button', { name: '提交问题' }))

    await waitFor(() => expect(newConversation).toBeDisabled())
    expect(latestButton).toBeDisabled()
    expect(olderButton).toBeDisabled()
    await user.click(olderButton)
    await user.click(newConversation)
    expect(latestButton).toHaveAttribute('aria-current', 'true')
    expect(studyApi.createConversation).not.toHaveBeenCalled()
    expect(createQuery).toHaveBeenCalledWith('course-1', '继续解释', latest.id)

    await act(async () =>
      pendingQuery.resolve(answeredSnapshot({ conversation_id: latest.id, question: '继续解释' })),
    )
    await waitFor(() => expect(newConversation).toBeEnabled())
    expect(latestButton).toBeEnabled()
    expect(olderButton).toBeEnabled()
  })

  it('creates and selects a new conversation, then sends into it', async () => {
    const existing = conversation('conversation-existing', '已有会话', {
      turn_count: 1,
      updated_at: '2026-07-18T04:00:00Z',
    })
    const createdConversation = conversation('conversation-new', '新会话', {
      updated_at: '2026-07-20T04:00:00Z',
    })
    const createdQuery = historySnapshot(
      'query-new',
      createdConversation.id,
      '继续解释',
      '这是新会话中的回答。',
      '2026-07-20T04:01:00Z',
    )
    vi.mocked(studyApi.listConversations).mockResolvedValue([existing])
    vi.mocked(studyApi.listConversationQueries).mockResolvedValue([])
    vi.mocked(studyApi.createConversation).mockResolvedValue(createdConversation)
    vi.spyOn(studyApi, 'createQuery').mockResolvedValue(createdQuery)
    const { user } = renderInWorkspace(<QAPage />)

    await screen.findByRole('button', { name: /已有会话/ })
    await user.click(screen.getByRole('button', { name: '新建会话' }))

    expect(await screen.findByText('在此会话中提出第一个问题')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /新会话/ })).toHaveAttribute('aria-current', 'true')
    await user.type(screen.getByLabelText('课程问题'), '继续解释')
    await user.click(screen.getByRole('button', { name: '提交问题' }))

    expect(await screen.findByText('这是新会话中的回答。')).toBeInTheDocument()
    expect(studyApi.createQuery).toHaveBeenCalledWith(
      'course-1',
      '继续解释',
      createdConversation.id,
    )
  })

  it('lets the query API create the first conversation atomically and caches the returned thread', async () => {
    const snapshot = answeredSnapshot({
      conversation_id: 'conversation-from-query',
    })
    vi.spyOn(studyApi, 'createQuery').mockResolvedValue(snapshot)
    const { queryClient } = renderInWorkspace(<QAPage />)

    expect(await screen.findByText('暂无会话，直接提问即可开始')).toBeInTheDocument()
    await submitQuestion()

    expect(studyApi.createConversation).not.toHaveBeenCalled()
    expect(studyApi.createQuery).toHaveBeenCalledWith('course-1', '什么是进程？')
    expect(await screen.findByText('进程是资源分配的基本单位。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /什么是进程？/ })).toHaveAttribute(
      'aria-current',
      'true',
    )
    expect(queryClient.getQueryData<ConversationRecord[]>(['conversations', 'course-1'])).toEqual([
      expect.objectContaining({
        id: 'conversation-from-query',
        course_id: 'course-1',
        title: '什么是进程？',
        turn_count: 1,
        latest_query_id: snapshot.id,
      }),
    ])
  })

  it('lets the learner inspect, add, correct, and delete course memories', async () => {
    const memory: LearnerMemoryRecord = {
      id: 'memory-1',
      course_id: 'course-1',
      memory_type: 'preference',
      content: '我喜欢先看例子',
      confidence: 1,
      source_kind: 'explicit_user',
      last_confirmed_at: '2026-08-04T10:00:00Z',
      created_at: '2026-08-04T10:00:00Z',
      updated_at: '2026-08-04T10:00:00Z',
    }
    const created: LearnerMemoryRecord = {
      ...memory,
      id: 'memory-2',
      memory_type: 'learning_goal',
      content: '我的目标是掌握进程调度',
      source_kind: 'manual',
      updated_at: '2026-08-04T10:01:00Z',
    }
    vi.spyOn(studyApi, 'listLearnerMemories').mockResolvedValue([memory])
    const createMemory = vi.spyOn(studyApi, 'createLearnerMemory').mockResolvedValue(created)
    const updateMemory = vi.spyOn(studyApi, 'updateLearnerMemory').mockResolvedValue({
      ...memory,
      content: '我更喜欢先看类比',
      source_kind: 'manual',
      updated_at: '2026-08-04T10:02:00Z',
    })
    const deleteMemory = vi.spyOn(studyApi, 'deleteLearnerMemory').mockResolvedValue()
    const { user } = renderInWorkspace(<QAPage />)

    await user.click(await screen.findByRole('button', { name: '学习记忆' }))
    const dialog = await screen.findByRole('dialog', { name: '学习记忆' })
    expect(within(dialog).getByDisplayValue('我喜欢先看例子')).toBeVisible()

    const createType = within(dialog).getAllByLabelText('类型')[0]!
    const createContent = within(dialog).getAllByLabelText('内容')[0]!
    await user.selectOptions(createType, 'learning_goal')
    await user.type(createContent, '我的目标是掌握进程调度')
    await user.click(within(dialog).getByRole('button', { name: '添加' }))
    await waitFor(() =>
      expect(createMemory).toHaveBeenCalledWith('course-1', {
        memory_type: 'learning_goal',
        content: '我的目标是掌握进程调度',
      }),
    )
    expect(await within(dialog).findByDisplayValue('我的目标是掌握进程调度')).toBeVisible()

    const existingContent = within(dialog).getByDisplayValue('我喜欢先看例子')
    const existingRow = existingContent.closest('article')
    expect(existingRow).not.toBeNull()
    await user.clear(existingContent)
    await user.type(existingContent, '我更喜欢先看类比')
    await user.click(within(existingRow!).getByRole('button', { name: '保存记忆' }))
    await waitFor(() =>
      expect(updateMemory).toHaveBeenCalledWith('memory-1', {
        memory_type: 'preference',
        content: '我更喜欢先看类比',
      }),
    )

    const updatedContent = await within(dialog).findByDisplayValue('我更喜欢先看类比')
    const updatedRow = updatedContent.closest('article')
    expect(updatedRow).not.toBeNull()
    await user.click(within(updatedRow!).getByRole('button', { name: '删除记忆' }))
    await user.click(within(updatedRow!).getByRole('button', { name: '确认删除' }))
    await waitFor(() => expect(deleteMemory).toHaveBeenCalledWith('memory-1'))
    expect(within(dialog).queryByDisplayValue('我更喜欢先看类比')).not.toBeInTheDocument()
  })

  it('disables question submission when conversation state failed to load', async () => {
    vi.mocked(studyApi.listConversations).mockRejectedValue(
      new ApiError(problem({ status: 503, code: 'UNAVAILABLE', title: '会话加载失败' })),
    )
    const createQuery = vi.spyOn(studyApi, 'createQuery').mockResolvedValue(answeredSnapshot())
    const { user } = renderInWorkspace(<QAPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('无法加载会话')
    await user.type(screen.getByLabelText('课程问题'), '不应提交')
    expect(screen.getByRole('button', { name: '提交问题' })).toBeDisabled()
    expect(createQuery).not.toHaveBeenCalled()
    expect(studyApi.createConversation).not.toHaveBeenCalled()
  })

  it('shows abstention separately in a conversation turn', async () => {
    const answer: StructuredAnswer = {
      schema_version: '1.0',
      query_id: 'query-1',
      status: 'abstained',
      answer_markdown: '',
      claims: [],
      citations: [],
      refusal: {
        code: 'INSUFFICIENT_EVIDENCE',
        message: '课程资料未覆盖该问题。',
      },
    }
    vi.spyOn(studyApi, 'createQuery').mockResolvedValue(
      answeredSnapshot({ status: 'abstained', answer }),
    )
    renderInWorkspace(<QAPage />)

    await submitQuestion('课外问题')

    expect(await screen.findByText('依据不足')).toBeInTheDocument()
    expect(screen.getByText('课程资料未覆盖该问题。')).toBeInTheDocument()
    expect(screen.queryByText('进程是资源分配的基本单位。')).not.toBeInTheDocument()
  })

  it('renders a Provider failure turn without manufacturing an answer', async () => {
    vi.spyOn(studyApi, 'createQuery').mockResolvedValue(
      answeredSnapshot({
        status: 'failed',
        answer: null,
        failure_code: 'PROVIDER_TIMEOUT',
      }),
    )
    renderInWorkspace(<QAPage />)

    await submitQuestion('解释调度')

    expect(await screen.findByRole('alert')).toHaveTextContent('Provider 调用失败')
    expect(screen.getByRole('alert')).toHaveTextContent('PROVIDER_TIMEOUT')
    expect(screen.queryByText('进程是资源分配的基本单位。')).not.toBeInTheDocument()
  })

  it('disables submission when the capability API reports no Provider', () => {
    const capabilities: RuntimeCapabilities = {
      ...availableCapabilities,
      provider: { status: 'not_configured', label: '未配置回答模型' },
    }

    renderInWorkspace(<QAPage />, { workspace: { capabilities } })

    expect(screen.getByRole('status')).toHaveTextContent('Provider 未配置')
    expect(screen.getByLabelText('课程问题')).toBeDisabled()
    expect(screen.getByRole('button', { name: '提交问题' })).toBeDisabled()
  })

  it('keeps stale citation content closed when source lookup returns 404', async () => {
    vi.spyOn(studyApi, 'createQuery').mockResolvedValue(answeredSnapshot())
    vi.spyOn(studyApi, 'getCitation').mockRejectedValue(
      new ApiError(
        problem({
          status: 404,
          code: 'RESOURCE_NOT_FOUND',
          title: '引用来源不存在或已失效',
          detail: null,
        }),
      ),
    )
    renderInWorkspace(<QAPage />)

    const { user } = await submitQuestion()
    await user.click(await screen.findByRole('button', { name: /chapter-1\.png/ }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('来源不可用'))
    expect(screen.getByRole('alert')).toHaveTextContent('引用来源不存在或已失效')
    expect(screen.queryByRole('dialog', { name: '来源' })).not.toBeInTheDocument()
  })
})
