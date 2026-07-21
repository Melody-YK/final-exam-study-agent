import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ApiError, studyApi } from '../../api/client'
import type { RuntimeCapabilities } from '../../api/types'
import { noteRecord, problem } from '../../test/fixtures'
import { availableCapabilities, renderInWorkspace } from '../../test/render'
import { NotesPage } from './NotesPage'

describe('NotesPage', () => {
  it('edits a note while preserving active, stale, and unavailable source states', async () => {
    const note = noteRecord({
      sources: [
        noteRecord().sources[0]!,
        {
          ...noteRecord().sources[0]!,
          id: 'source-stale',
          document_name: 'old.pdf',
          stale: true,
        },
        {
          ...noteRecord().sources[0]!,
          id: 'source-unavailable',
          document_name: 'deleted.pdf',
          available: false,
          unavailable_reason: 'SOURCE_DELETED',
        },
      ],
    })
    const updated = noteRecord({
      body_markdown: '# 更新正文',
      version: 2,
      generation: 1,
      sources: note.sources,
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([note])
    vi.spyOn(studyApi, 'updateNote').mockResolvedValue(updated)
    const { user } = renderInWorkspace(<NotesPage />)

    const editor = await screen.findByLabelText('笔记正文')
    await user.clear(editor)
    await user.type(editor, '# 更新正文')
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(studyApi.updateNote).toHaveBeenCalledWith('note-1', '# 更新正文', 1),
    )
    expect(await screen.findByText(/版本 2/)).toBeInTheDocument()
    expect(screen.getByText('活动来源')).toBeInTheDocument()
    expect(screen.getByText('旧版本')).toBeInTheDocument()
    expect(screen.getByText('不可用 · SOURCE_DELETED')).toBeInTheDocument()
    expect(screen.getByText('deleted.pdf')).toBeInTheDocument()
  })

  it('detects a version conflict and loads the latest server note', async () => {
    const initial = noteRecord()
    const latest = noteRecord({
      body_markdown: '# 服务器正文',
      version: 2,
      generation: 1,
      updated_at: '2026-07-19T04:03:00Z',
    })
    vi.spyOn(studyApi, 'listNotes').mockResolvedValueOnce([initial]).mockResolvedValue([latest])
    vi.spyOn(studyApi, 'updateNote').mockRejectedValue(
      new ApiError(
        problem({
          status: 412,
          code: 'VERSION_CONFLICT',
          title: '笔记版本冲突',
          detail: '当前版本为 2',
        }),
      ),
    )
    const { user } = renderInWorkspace(<NotesPage />)
    const editor = await screen.findByLabelText('笔记正文')

    await user.clear(editor)
    await user.type(editor, '# 本地草稿')
    await user.click(screen.getByRole('button', { name: '保存' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('笔记已在其他位置更新')
    expect(screen.getByRole('alert')).toHaveTextContent('当前草稿未覆盖服务器版本')
    await user.click(screen.getByRole('button', { name: '载入服务器版本' }))

    await waitFor(() => expect(studyApi.listNotes).toHaveBeenCalledTimes(2))
    expect(await screen.findByLabelText('笔记正文')).toHaveValue('# 服务器正文')
    expect(screen.queryByText('笔记已在其他位置更新')).not.toBeInTheDocument()
  })

  it('collects the title and section path required by note creation', async () => {
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([])
    vi.spyOn(studyApi, 'createNote').mockResolvedValue(
      noteRecord({ section_path: ['第二章', '内存'], title: '虚拟内存' }),
    )
    const { user } = renderInWorkspace(<NotesPage />)
    await screen.findByText('暂无笔记')

    await user.click(screen.getByRole('button', { name: '新建笔记' }))
    const section = screen.getByLabelText('章节路径')
    await user.clear(section)
    await user.type(section, '第二章 / 内存')
    await user.type(screen.getByLabelText('标题'), '虚拟内存')
    await user.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() =>
      expect(studyApi.createNote).toHaveBeenCalledWith(
        'course-1',
        ['第二章', '内存'],
        '虚拟内存',
      ),
    )
    expect(await screen.findByRole('heading', { name: '虚拟内存' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('gates generation actions while leaving existing Markdown editable', async () => {
    const capabilities: RuntimeCapabilities = {
      ...availableCapabilities,
      provider: { status: 'not_configured', label: '未配置回答模型' },
    }
    vi.spyOn(studyApi, 'listNotes').mockResolvedValue([noteRecord()])

    renderInWorkspace(<NotesPage />, { workspace: { capabilities } })

    expect(await screen.findByLabelText('笔记正文')).toBeEnabled()
    expect(screen.getByRole('button', { name: '新建笔记' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重新生成' })).toBeDisabled()
  })
})
