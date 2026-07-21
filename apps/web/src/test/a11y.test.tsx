import { screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { Modal } from '../components/ui/Modal'
import { renderInWorkspace } from './render'

function ModalHarness() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button onClick={() => setOpen(true)} type="button">
        打开对话框
      </button>
      <Modal
        footer={<button type="button">完成</button>}
        onClose={() => setOpen(false)}
        open={open}
        title="键盘测试"
      >
        <button type="button">正文操作</button>
      </Modal>
    </>
  )
}

describe('dialog keyboard accessibility', () => {
  it('contains focus, closes on Escape, and restores focus to the opener', async () => {
    const { user } = renderInWorkspace(<ModalHarness />)
    const opener = screen.getByRole('button', { name: '打开对话框' })
    await user.click(opener)

    const dialog = screen.getByRole('dialog', { name: '键盘测试' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    await waitFor(() => expect(screen.getByRole('button', { name: '关闭' })).toHaveFocus())
    expect(document.body.style.overflow).toBe('hidden')

    await user.tab({ shift: true })
    expect(screen.getByRole('button', { name: '完成' })).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: '关闭' })).toHaveFocus()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(opener).toHaveFocus())
    expect(document.body.style.overflow).toBe('')
  })
})
