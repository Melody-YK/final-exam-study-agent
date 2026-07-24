import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { studyApi } from '../../api/client'
import type { AdminDocument } from '../../api/types'
import { createTestQueryClient } from '../../test/render'
import { AdminReviewsPage } from './AdminReviewsPage'

const pendingDocument: AdminDocument = {
  id: 'document-pending',
  course_id: 'course-1',
  course_title: '操作系统',
  owner_account_id: 'account-student',
  owner_email: 'student@example.com',
  owner_display_name: '复习同学',
  owner_subject: 'student@example.com',
  filename: '文件系统.pptx',
  media_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  size_bytes: 524_288,
  corpus_role: 'corpus',
  status: 'parsed_index_blocked',
  page_count: 18,
  review_status: 'pending',
  review_note: null,
  reviewed_by_account_id: null,
  reviewed_by_email: null,
  reviewed_at: null,
  created_at: '2026-07-23T08:00:00Z',
  updated_at: '2026-07-23T08:05:00Z',
}

function renderPage() {
  const queryClient = createTestQueryClient()

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }

  return {
    queryClient,
    user: userEvent.setup(),
    ...render(<AdminReviewsPage />, { wrapper: Wrapper }),
  }
}

describe('AdminReviewsPage', () => {
  it('loads the pending queue and links to the protected original content', async () => {
    const listDocuments = vi
      .spyOn(studyApi, 'listAdminDocuments')
      .mockResolvedValue({ items: [pendingDocument] })

    renderPage()

    expect(await screen.findByText('文件系统.pptx')).toBeVisible()
    const documentRow = screen.getByRole('row', {
      name: /文件系统\.pptx/,
    })
    expect(within(documentRow).getByText('复习同学')).toBeVisible()
    expect(within(documentRow).getByText('待审核')).toBeVisible()
    expect(listDocuments).toHaveBeenCalledWith('pending')
    expect(screen.getByRole('link', { name: '预览 文件系统.pptx' })).toHaveAttribute(
      'href',
      '/api/v1/admin/documents/document-pending/content',
    )
    expect(screen.getByRole('link', { name: '预览 文件系统.pptx' })).toHaveAttribute(
      'target',
      '_blank',
    )
  })

  it('approves a pending document and refreshes the active queue', async () => {
    let items = [pendingDocument]
    const listDocuments = vi
      .spyOn(studyApi, 'listAdminDocuments')
      .mockImplementation(async () => ({ items }))
    const reviewDocument = vi
      .spyOn(studyApi, 'reviewAdminDocument')
      .mockImplementation(async (_id, input) => {
        const reviewed = {
          ...pendingDocument,
          review_status: input.review_status,
          review_note: input.review_note ?? null,
          reviewed_by_account_id: 'account-admin',
          reviewed_by_email: 'admin@example.com',
          reviewed_at: '2026-07-24T08:10:00Z',
        } satisfies AdminDocument
        items = []
        return reviewed
      })
    const { user } = renderPage()

    await user.click(await screen.findByRole('button', { name: '通过 文件系统.pptx' }))
    const dialog = screen.getByRole('dialog', { name: '通过资料' })
    await user.click(within(dialog).getByRole('button', { name: '确认通过' }))

    await waitFor(() =>
      expect(reviewDocument).toHaveBeenCalledWith('document-pending', {
        review_status: 'approved',
        review_note: null,
      }),
    )
    await waitFor(() => expect(listDocuments.mock.calls.length).toBeGreaterThan(1))
    expect(await screen.findByText('没有待审核资料')).toBeVisible()
    expect(screen.queryByRole('dialog', { name: '通过资料' })).not.toBeInTheDocument()
  })

  it('requires a reason before rejecting and submits the trimmed note', async () => {
    let items = [pendingDocument]
    vi.spyOn(studyApi, 'listAdminDocuments').mockImplementation(async () => ({
      items,
    }))
    const reviewDocument = vi
      .spyOn(studyApi, 'reviewAdminDocument')
      .mockImplementation(async (_id, input) => {
        items = []
        return {
          ...pendingDocument,
          review_status: 'rejected',
          review_note: input.review_note ?? null,
          reviewed_by_account_id: 'account-admin',
          reviewed_by_email: 'admin@example.com',
          reviewed_at: '2026-07-24T08:10:00Z',
        }
      })
    const { user } = renderPage()

    await user.click(await screen.findByRole('button', { name: '拒绝 文件系统.pptx' }))
    const dialog = screen.getByRole('dialog', { name: '拒绝资料' })
    const reason = within(dialog).getByLabelText('拒绝原因')
    expect(reason).toBeRequired()

    await user.click(within(dialog).getByRole('button', { name: '确认拒绝' }))
    expect(reviewDocument).not.toHaveBeenCalled()
    expect(reason).toBeInvalid()

    await user.type(reason, '  文件内容与课程无关  ')
    await user.click(within(dialog).getByRole('button', { name: '确认拒绝' }))

    await waitFor(() =>
      expect(reviewDocument).toHaveBeenCalledWith('document-pending', {
        review_status: 'rejected',
        review_note: '文件内容与课程无关',
      }),
    )
    expect(await screen.findByText('没有待审核资料')).toBeVisible()
  })
})
