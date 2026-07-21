import { CloudOff, Cpu, ScanText, Wifi } from 'lucide-react'

import type { CapabilityState, RuntimeCapabilities } from '../../api/types'

interface CapabilityBannerProps {
  capabilities: RuntimeCapabilities | undefined
  loading?: boolean
  error?: boolean
}

function capabilityText(state: CapabilityState | undefined, fallback: string): string {
  if (!state) return fallback
  return state.label
}

export function CapabilityBanner({ capabilities, loading = false, error = false }: CapabilityBannerProps) {
  const providerReady = capabilities?.provider.status === 'available'
  const parserReady = capabilities?.native_parser.status === 'available'
  const ocrReady = capabilities?.ocr_parser.status === 'available'

  return (
    <section className="capability-strip" aria-label="运行能力">
      <div className={providerReady ? 'is-available' : 'is-unavailable'}>
        {providerReady ? <Wifi aria-hidden="true" size={16} /> : <CloudOff aria-hidden="true" size={16} />}
        <span>
          <strong>问答</strong>
          {loading ? '检查中' : error ? 'API 未报告' : capabilityText(capabilities?.provider, '未配置')}
        </span>
      </div>
      <div className={parserReady ? 'is-available' : 'is-unavailable'}>
        <Cpu aria-hidden="true" size={16} />
        <span>
          <strong>原生解析</strong>
          {loading ? '检查中' : capabilityText(capabilities?.native_parser, '状态未知')}
        </span>
      </div>
      <div className={ocrReady ? 'is-available' : 'is-unavailable'}>
        <ScanText aria-hidden="true" size={16} />
        <span>
          <strong>OCR</strong>
          {loading ? '检查中' : capabilityText(capabilities?.ocr_parser, '需要本地 Worker')}
        </span>
      </div>
    </section>
  )
}
