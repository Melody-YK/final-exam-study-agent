import { AlertTriangle, FileWarning, LoaderCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { CitationSource } from '../../api/types'
import { Modal } from '../../components/ui/Modal'

interface SourceViewerProps {
  source: CitationSource | null
  onClose: () => void
}

function BboxOverlay({ source }: { source: CitationSource }) {
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

function PdfCanvas({ source }: { source: CitationSource }) {
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

function SlideOrImage({ source }: { source: CitationSource }) {
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
          alt={`${source.document_name} ${source.locator.kind === 'slide' ? '幻灯片' : '页面'} ${source.locator.ordinal}`}
          onError={() => setState('failed')}
          onLoad={() => setState('ready')}
          src={source.read_url}
        />
        {state === 'ready' ? <BboxOverlay source={source} /> : null}
      </div>
    </div>
  )
}

function sourceMediaKind(source: CitationSource): 'pdf' | 'image' | 'unsupported' {
  const mediaType =
    typeof source.media_type === 'string' ? source.media_type.toLowerCase() : ''
  if (mediaType === 'application/pdf') return 'pdf'
  if (mediaType.startsWith('image/')) return 'image'
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
          ? `${source.document_name} · ${source.locator.kind === 'slide' ? '幻灯片' : '第'} ${source.locator.ordinal}`
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
                  <dd>
                    {source.locator.kind === 'slide' ? '幻灯片' : '页'} {source.locator.ordinal}
                  </dd>
                </div>
              </dl>
            </aside>
          </div>
        )
      ) : null}
    </Modal>
  )
}
