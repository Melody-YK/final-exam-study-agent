import { fireEvent, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { studyApi } from '../../api/client'
import { documentRecord } from '../../test/fixtures'
import { renderInWorkspace } from '../../test/render'
import { UploadDialog } from './UploadDialog'

function UploadHarness({
  onUploaded = () => undefined,
  mineruAvailable = true,
}: {
  onUploaded?: () => void
  mineruAvailable?: boolean
}) {
  const [open, setOpen] = useState(true)
  return (
    <UploadDialog
      courseId="course-1"
      mineruAvailable={mineruAvailable}
      onClose={() => setOpen(false)}
      onUploaded={onUploaded}
      open={open}
    />
  )
}

describe('UploadDialog', () => {
  afterEach(() => vi.restoreAllMocks())

  it('always explains the supported formats and Markdown source positioning', () => {
    renderInWorkspace(<UploadHarness />)

    const input = screen.getByLabelText(/选择文件/)
    expect(input).toHaveAttribute(
      'accept',
      '.pdf,.md,.markdown,.jpg,.jpeg,.png',
    )
    expect(
      screen.getByText('支持 PDF、Markdown（.md/.markdown）、JPG、JPEG、PNG'),
    ).toBeVisible()
    expect(
      screen.getByText('Markdown 将按标题和段落定位来源，单个文件最大 5 MB。'),
    ).toBeVisible()
    expect(screen.queryByLabelText('资料角色')).not.toBeInTheDocument()
  })

  it.each([
    ['lecture.pdf', 'application/pdf'],
    ['outline.md', 'text/markdown'],
    ['outline.markdown', 'text/markdown'],
    ['scan.JPG', 'image/jpeg'],
    ['scan.jpeg', 'image/jpeg'],
    ['scan.PNG', 'image/png'],
  ])(
    'allows the supported file %s to enter the upload flow',
    async (filename, mediaType) => {
      const uploaded = documentRecord({ filename, media_type: mediaType })
      const upload = vi
        .spyOn(studyApi, 'uploadDocument')
        .mockResolvedValue(uploaded)
      const onUploaded = vi.fn()
      const { user } = renderInWorkspace(
        <UploadHarness onUploaded={onUploaded} />,
      )
      const file = new File(['supported body'], filename, { type: mediaType })

      await user.upload(screen.getByLabelText(/选择文件/), file)
      await user.click(screen.getByRole('button', { name: '上传' }))

      expect(upload).toHaveBeenCalledWith(
        'course-1',
        file,
        'corpus',
        expect.any(Function),
        expect.any(AbortSignal),
        'enhanced',
      )
      expect(onUploaded).toHaveBeenCalledWith(uploaded)
    },
  )

  it.each([
    [
      'slides.pptx',
      'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    ],
    [
      'notes.docx',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    ],
    ['scan.tiff', 'image/tiff'],
    ['archive.zip', 'application/zip'],
  ])(
    'rejects the unsupported file %s before upload starts',
    async (filename, mediaType) => {
      const upload = vi.spyOn(studyApi, 'uploadDocument')
      const onUploaded = vi.fn()
      const { user } = renderInWorkspace(
        <UploadHarness onUploaded={onUploaded} />,
      )
      const input = screen.getByLabelText(/选择文件/)

      fireEvent.change(input, {
        target: {
          files: [
            new File(['unsupported body'], filename, { type: mediaType }),
          ],
        },
      })

      expect(await screen.findByRole('alert')).toHaveTextContent(
        '文件格式不支持',
      )
      expect(screen.getByRole('alert')).toHaveTextContent(filename)
      expect(screen.getByRole('alert')).toHaveTextContent(
        'PPTX、DOCX 和 TIFF 当前不可上传',
      )
      expect(screen.getByRole('button', { name: '上传' })).toBeDisabled()
      await user.click(screen.getByRole('button', { name: '上传' }))
      expect(upload).not.toHaveBeenCalled()
      expect(onUploaded).not.toHaveBeenCalled()
    },
  )

  it('rejects an oversized Markdown file before hashing or upload starts', async () => {
    const upload = vi.spyOn(studyApi, 'uploadDocument')
    renderInWorkspace(<UploadHarness />)
    const file = new File([new Uint8Array(5 * 1024 * 1024 + 1)], 'large.md', {
      type: 'text/markdown',
    })

    fireEvent.change(screen.getByLabelText(/选择文件/), {
      target: { files: [file] },
    })

    expect(await screen.findByRole('alert')).toHaveTextContent('Markdown 文件过大')
    expect(screen.getByRole('alert')).toHaveTextContent('不能超过 5 MB')
    expect(screen.getByRole('button', { name: '上传' })).toBeDisabled()
    expect(upload).not.toHaveBeenCalled()
  })

  it('uses the learning role and aborts an in-flight upload on cancel', async () => {
    let uploadSignal: AbortSignal | undefined
    vi.spyOn(studyApi, 'uploadDocument').mockImplementation(
      (_courseId, _file, _role, onProgress, signal) => {
        uploadSignal = signal
        onProgress?.(44)
        return new Promise(() => undefined)
      },
    )
    const { user } = renderInWorkspace(<UploadHarness />)
    const file = new File(['pdf body'], 'questions.pdf', {
      type: 'application/pdf',
    })

    await user.upload(screen.getByLabelText(/选择文件/), file)
    await user.click(screen.getByRole('button', { name: '上传' }))

    await waitFor(() =>
      expect(screen.getByRole('progressbar')).toHaveAttribute(
        'aria-valuenow',
        '44',
      ),
    )
    expect(studyApi.uploadDocument).toHaveBeenCalledWith(
      'course-1',
      file,
      'corpus',
      expect.any(Function),
      expect.any(AbortSignal),
      'enhanced',
    )
    await user.click(screen.getByRole('button', { name: '取消上传' }))

    expect(uploadSignal?.aborted).toBe(true)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('offers MinerU only for PDFs and submits the selected strategy', async () => {
    const upload = vi
      .spyOn(studyApi, 'uploadDocument')
      .mockResolvedValue(documentRecord({ filename: 'chapter.pdf' }))
    const { user } = renderInWorkspace(<UploadHarness />)
    const file = new File(['pdf body'], 'chapter.pdf', {
      type: 'application/pdf',
    })

    await user.upload(screen.getByLabelText(/选择文件/), file)
    await user.click(screen.getByRole('radio', { name: /MinerU/ }))
    await user.click(screen.getByRole('button', { name: '上传' }))

    expect(upload).toHaveBeenCalledWith(
      'course-1',
      file,
      'corpus',
      expect.any(Function),
      expect.any(AbortSignal),
      'mineru',
    )
  })

  it('disables MinerU when the self-hosted service is unavailable', async () => {
    const { user } = renderInWorkspace(
      <UploadHarness mineruAvailable={false} />,
    )
    await user.upload(
      screen.getByLabelText(/选择文件/),
      new File(['pdf body'], 'chapter.pdf', { type: 'application/pdf' }),
    )

    expect(screen.getByRole('radio', { name: /MinerU/ })).toBeDisabled()
    expect(screen.getByText('服务未就绪')).toBeVisible()
  })
})
