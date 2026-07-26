import { AlertTriangle, FileWarning, LoaderCircle } from 'lucide-react'
import { fromMarkdown } from 'mdast-util-from-markdown'
import ReactMarkdown from 'react-markdown'
import { useEffect, useRef, useState } from 'react'

import type { BoundingBox, SourceLocator } from '../../api/types'
import { Modal } from '../../components/ui/Modal'
import { formatSourceLocator } from './sourceLocator'

export interface SourcePreview {
  document_name: string
  revision_id: string
  locator: SourceLocator
  section_path?: string[]
  quote: string
  bounding_boxes: BoundingBox[]
  media_type: string
  read_url: string
  read_url_expires_at: string
}

interface SourceViewerProps {
  source: SourcePreview | null
  onClose: () => void
}

function locatorLabel(source: SourcePreview): string {
  return formatSourceLocator(source.locator, source.section_path)
}

function BboxOverlay({ source }: { source: SourcePreview }) {
  return (
    <div className="bbox-layer" aria-hidden="true">
      {source.bounding_boxes.map((box, index) => (
        <span
          className="bbox-highlight"
          key={`${box.x}-${box.y}-${index}`}
          style={{
            left: `${box.x * 100}%`,
            top: `${box.y * 100}%`,
            width: `${box.width * 100}%`,
            height: `${box.height * 100}%`,
          }}
        />
      ))}
    </div>
  )
}

function PdfCanvas({ source }: { source: SourcePreview }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading')

  useEffect(() => {
    let cancelled = false
    let destroy: (() => Promise<void>) | undefined
    const render = async () => {
      try {
        const pdfjs = await import('pdfjs-dist')
        pdfjs.GlobalWorkerOptions.workerSrc = new URL(
          'pdfjs-dist/build/pdf.worker.min.mjs',
          import.meta.url,
        ).toString()
        const task = pdfjs.getDocument({ url: source.read_url, withCredentials: true })
        destroy = () => task.destroy()
        const document = await task.promise
        const page = await document.getPage(source.locator.ordinal)
        const viewport = page.getViewport({ scale: 1.35 })
        const canvas = canvasRef.current
        if (!canvas || cancelled) return
        canvas.width = viewport.width
        canvas.height = viewport.height
        const context = canvas.getContext('2d')
        if (!context) throw new Error('Canvas context unavailable')
        await page.render({ canvas, canvasContext: context, viewport }).promise
        if (!cancelled) setState('ready')
      } catch {
        if (!cancelled) setState('failed')
      }
    }
    void render()
    return () => {
      cancelled = true
      if (destroy) void destroy()
    }
  }, [source.locator.ordinal, source.read_url])

  return (
    <div className="source-media source-media--pdf">
      {state === 'loading' ? (
        <span className="source-media__state">
          <LoaderCircle aria-hidden="true" className="spin" size={20} />
          加载页面
        </span>
      ) : null}
      {state === 'failed' ? (
        <span className="source-media__state" role="alert">
          <FileWarning aria-hidden="true" size={20} />
          无法渲染 PDF 页面
        </span>
      ) : null}
      <div className="source-media__surface">
        <canvas aria-label={`PDF 第 ${source.locator.ordinal} 页`} ref={canvasRef} />
        {state === 'ready' ? <BboxOverlay source={source} /> : null}
      </div>
    </div>
  )
}

function SlideOrImage({ source }: { source: SourcePreview }) {
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading')

  return (
    <div className="source-media source-media--image">
      {state === 'loading' ? (
        <span className="source-media__state">
          <LoaderCircle aria-hidden="true" className="spin" size={20} />
          加载页面
        </span>
      ) : null}
      {state === 'failed' ? (
        <span className="source-media__state" role="alert">
          <FileWarning aria-hidden="true" size={20} />
          无法渲染来源页面
        </span>
      ) : null}
      <div className="source-media__surface">
        <img
          alt={`${source.document_name} ${locatorLabel(source)}`}
          onError={() => setState('failed')}
          onLoad={() => setState('ready')}
          src={source.read_url}
        />
        {state === 'ready' ? <BboxOverlay source={source} /> : null}
      </div>
    </div>
  )
}

interface MarkdownSection {
  body: string
  label: string
}

interface MarkdownNode {
  type?: unknown
  value?: unknown
  alt?: unknown
  position?: { start?: { offset?: number } }
  children?: MarkdownNode[]
}

function textContent(node: unknown): string {
  if (typeof node !== 'object' || node === null) return ''
  const candidate = node as { value?: unknown; alt?: unknown; children?: unknown[] }
  if (typeof candidate.value === 'string') return candidate.value
  if (typeof candidate.alt === 'string') return candidate.alt
  return (candidate.children ?? []).map(textContent).join('')
}

function nodeOffset(node: MarkdownNode): number | null {
  const offset = node.position?.start?.offset
  return typeof offset === 'number' ? offset : null
}

function markdownSection(markdown: string, ordinal: number): MarkdownSection | null {
  const tree = fromMarkdown(markdown)
  const headings: Array<{ offset: number; label: string }> = []
  const contentOffsets: number[] = []

  const collectHeadings = (node: unknown) => {
    if (typeof node !== 'object' || node === null) return
    const candidate = node as MarkdownNode
    const offset = nodeOffset(candidate)
    const label = textContent(candidate).trim()
    if (candidate.type === 'heading' && offset !== null && label) {
      headings.push({
        offset,
        label,
      })
    }
    if (
      offset !== null &&
      ((candidate.type === 'paragraph' && label) ||
        ((candidate.type === 'code' || candidate.type === 'html') &&
          typeof candidate.value === 'string' &&
          candidate.value.trim()))
    ) {
      contentOffsets.push(offset)
    }
    candidate.children?.forEach(collectHeadings)
  }
  collectHeadings(tree)
  headings.sort((left, right) => left.offset - right.offset)

  const sections: MarkdownSection[] = []
  const firstHeadingOffset = headings[0]?.offset ?? markdown.length
  const preamble = markdown.slice(0, firstHeadingOffset).trim()
  if (preamble && contentOffsets.some((offset) => offset < firstHeadingOffset)) {
    sections.push({ body: preamble, label: '文档开头' })
  }
  headings.forEach((heading, index) => {
    const end = headings[index + 1]?.offset ?? markdown.length
    sections.push({
      body: markdown.slice(heading.offset, end).trim(),
      label: heading.label || `章节 ${sections.length + 1}`,
    })
  })

  return sections[ordinal - 1] ?? null
}

function MarkdownSource({ source }: { source: SourcePreview }) {
  const [state, setState] = useState<
    | { status: 'loading' }
    | { status: 'failed' }
    | { status: 'unavailable' }
    | { status: 'ready'; section: MarkdownSection }
  >({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    const load = async () => {
      try {
        const response = await fetch(source.read_url, {
          credentials: 'include',
          headers: { Accept: 'text/markdown' },
          signal: controller.signal,
        })
        if (!response.ok) throw new Error('Markdown source request failed')
        const body = await response.text()
        if (!controller.signal.aborted) {
          const section = markdownSection(body, source.locator.ordinal)
          setState(section ? { status: 'ready', section } : { status: 'unavailable' })
        }
      } catch {
        if (!controller.signal.aborted) setState({ status: 'failed' })
      }
    }
    void load()
    return () => controller.abort()
  }, [source.locator.ordinal, source.read_url])

  return (
    <div className="source-media source-media--markdown">
      {state.status === 'loading' ? (
        <span className="source-media__state">
          <LoaderCircle aria-hidden="true" className="spin" size={20} />
          加载章节
        </span>
      ) : state.status === 'failed' ? (
        <span className="source-media__state" role="alert">
          <FileWarning aria-hidden="true" size={20} />
          无法读取 Markdown 章节
        </span>
      ) : state.status === 'unavailable' ? (
        <span className="source-media__state" role="alert">
          <FileWarning aria-hidden="true" size={20} />
          Markdown 章节定位已失效，请重新生成来源
        </span>
      ) : (
        <article aria-label={`Markdown 章节：${state.section.label}`}>
          <ReactMarkdown
            components={{
              img: ({ alt }) => (
                <span className="source-markdown__blocked-image" role="note">
                  外部图片未加载{alt ? `：${alt}` : ''}
                </span>
              ),
            }}
            skipHtml
          >
            {state.section.body}
          </ReactMarkdown>
        </article>
      )}
    </div>
  )
}

function sourceMediaKind(
  source: SourcePreview,
): 'pdf' | 'image' | 'markdown' | 'unsupported' {
  const mediaType =
    typeof source.media_type === 'string' ? source.media_type.toLowerCase() : ''
  if (mediaType === 'application/pdf') return 'pdf'
  if (mediaType.startsWith('image/')) return 'image'
  if (mediaType === 'text/markdown') return 'markdown'
  return 'unsupported'
}

function isExpired(expiresAt: string): boolean {
  const expires = Date.parse(expiresAt)
  return !Number.isFinite(expires) || expires <= Date.now()
}

export function SourceViewer({ source, onClose }: SourceViewerProps) {
  const expired = source ? isExpired(source.read_url_expires_at) : false
  const mediaKind = source ? sourceMediaKind(source) : 'unsupported'
  return (
    <Modal
      description={
        source
          ? `${source.document_name} · ${locatorLabel(source)}`
          : undefined
      }
      onClose={onClose}
      open={source !== null}
      size="wide"
      title="来源"
    >
      {source ? (
        expired ? (
          <div className="source-unavailable" role="alert">
            <AlertTriangle aria-hidden="true" size={20} />
            <div>
              <strong>来源已失效</strong>
              <p>来源链接已过期，请关闭后重新打开该引用。</p>
            </div>
          </div>
        ) : (
          <div className="source-viewer">
            {mediaKind === 'pdf' ? (
              <PdfCanvas source={source} />
            ) : mediaKind === 'image' ? (
              <SlideOrImage source={source} />
            ) : mediaKind === 'markdown' ? (
              <MarkdownSource source={source} />
            ) : (
              <div className="source-media source-media--unsupported" role="status">
                <FileWarning aria-hidden="true" size={22} />
                <strong>当前格式无法内嵌预览</strong>
                <p>API 未提供可渲染的页面资源。</p>
              </div>
            )}
            <aside className="source-quote">
              <p className="section-kicker">引用原文</p>
              <blockquote>{source.quote}</blockquote>
              <dl>
                <div>
                  <dt>版本</dt>
                  <dd>{source.revision_id.slice(0, 8)}</dd>
                </div>
                <div>
                  <dt>定位</dt>
                  <dd>{locatorLabel(source)}</dd>
                </div>
              </dl>
            </aside>
          </div>
        )
      ) : null}
    </Modal>
  )
}
