import { screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { studyApi } from '../../api/client'
import { renderInWorkspace } from '../../test/render'
import { UploadDialog } from './UploadDialog'

function UploadHarness() {
  const [open, setOpen] = useState(true)
  return (
    <UploadDialog
      courseId="course-1"
      onClose={() => setOpen(false)}
      onUploaded={() => undefined}
      open={open}
    />
  )
}

describe('UploadDialog', () => {
  it('passes the selected role and aborts an in-flight upload on cancel', async () => {
    let uploadSignal: AbortSignal | undefined
    vi.spyOn(studyApi, 'uploadDocument').mockImplementation(
      (_courseId, _file, _role, onProgress, signal) => {
        uploadSignal = signal
        onProgress?.(44)
        return new Promise(() => undefined)
      },
    )
    const { user } = renderInWorkspace(<UploadHarness />)
    const file = new File(['pdf body'], 'questions.pdf', { type: 'application/pdf' })

    await user.upload(screen.getByLabelText(/选择文件/), file)
    await user.selectOptions(screen.getByLabelText('资料角色'), 'questions')
    await user.click(screen.getByRole('button', { name: '上传' }))

    await waitFor(() => expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '44'))
    expect(studyApi.uploadDocument).toHaveBeenCalledWith(
      'course-1',
      file,
      'questions',
      expect.any(Function),
      expect.any(AbortSignal),
    )
    await user.click(screen.getByRole('button', { name: '取消上传' }))

    expect(uploadSignal?.aborted).toBe(true)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
