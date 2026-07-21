import { fireEvent, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { CitationSource } from '../../api/types'
import { citationSource } from '../../test/fixtures'
import { renderInWorkspace } from '../../test/render'
import { SourceViewer } from './SourceViewer'

describe('SourceViewer', () => {
  it('does not render source media or quoted content after the read URL expires', () => {
    renderInWorkspace(
      <SourceViewer
        onClose={() => undefined}
        source={citationSource({ read_url_expires_at: '2020-01-01T00:00:00Z' })}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('来源链接已过期')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByText('引用原文')).not.toBeInTheDocument()
  })

  it('shows the quote but fails closed when no renderable page resource is available', () => {
    renderInWorkspace(
      <SourceViewer
        onClose={() => undefined}
        source={citationSource({
          document_name: 'lecture.pptx',
          locator: { kind: 'slide', ordinal: 7 },
          media_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
          read_url: '/api/v1/sources/rendered-page.png',
        })}
      />,
    )

    expect(screen.getByRole('status')).toHaveTextContent('当前格式无法内嵌预览')
    expect(screen.getByText('引用原文')).toBeInTheDocument()
    expect(screen.getByText('进程拥有独立的地址空间。')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('fails closed instead of crashing when a stale response omits the media type', () => {
    const staleSource = {
      ...citationSource(),
      media_type: undefined,
    } as unknown as CitationSource

    renderInWorkspace(<SourceViewer onClose={() => undefined} source={staleSource} />)

    expect(screen.getByRole('status')).toHaveTextContent('当前格式无法内嵌预览')
    expect(screen.getByText('引用原文')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('renders a persisted slide image and anchors bounding boxes to the image surface', () => {
    const { container } = renderInWorkspace(
      <SourceViewer
        onClose={() => undefined}
        source={citationSource({
          document_name: 'lecture.pptx',
          locator: { kind: 'slide', ordinal: 7 },
          media_type: 'image/png',
          read_url: '/api/v1/queries/query-1/citations/citation-1/content',
          bounding_boxes: [{ x: 0.1, y: 0.2, width: 0.3, height: 0.15 }],
        })}
      />,
    )

    const image = screen.getByRole('img', { name: 'lecture.pptx 幻灯片 7' })
    fireEvent.load(image)

    const surface = image.closest('.source-media__surface')
    const highlight = container.querySelector<HTMLElement>('.bbox-highlight')
    expect(surface).not.toBeNull()
    expect(surface).toContainElement(highlight)
    expect(highlight).toHaveStyle({
      left: '10%',
      top: '20%',
      width: '30%',
      height: '15%',
    })
    expect(screen.getByText('幻灯片 7')).toBeInTheDocument()
  })
})
