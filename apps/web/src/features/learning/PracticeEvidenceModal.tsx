import { BookOpenText, FileText } from 'lucide-react'

import type { EvidenceReference } from '../../api/types'
import { Modal } from '../../components/ui/Modal'

interface PracticeEvidenceModalProps {
  evidence: EvidenceReference[]
  onClose: () => void
  open: boolean
}

function locatorLabel(evidence: EvidenceReference): string {
  const unit = evidence.locator.kind === 'page' ? '页' : evidence.locator.kind === 'slide' ? '页幻灯片' : '节'
  return `第 ${evidence.locator.ordinal} ${unit}`
}

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 6)}...${value.slice(-4)}` : value
}

export function PracticeEvidenceModal({ evidence, onClose, open }: PracticeEvidenceModalProps) {
  return (
    <Modal
      description={`${evidence.length} 条当前有效来源`}
      onClose={onClose}
      open={open}
      size="wide"
      title="证据原文"
    >
      {evidence.length === 0 ? (
        <div className="learning-evidence-reader__empty">
          <BookOpenText aria-hidden="true" size={24} />
          <p>当前题目没有可显示的有效来源。</p>
        </div>
      ) : (
        <ol className="learning-evidence-reader">
          {evidence.map((item, index) => (
            <li key={`${item.revision_id}-${item.chunk_id}`}>
              <header className="learning-evidence-reader__header">
                <span className="learning-evidence-reader__index">来源 {index + 1}</span>
                <span>
                  <FileText aria-hidden="true" size={15} />
                  {item.document_name ?? `文档 ${shortId(item.document_id)}`}
                </span>
                <span>{locatorLabel(item)}</span>
              </header>
              <blockquote>
                <p>{item.quote}</p>
              </blockquote>
              <details className="learning-evidence-reader__details">
                <summary>来源标识</summary>
                <dl>
                  <div>
                    <dt>Revision</dt>
                    <dd>{item.revision_id}</dd>
                  </div>
                  <div>
                    <dt>片段</dt>
                    <dd>{item.chunk_id}</dd>
                  </div>
                </dl>
              </details>
            </li>
          ))}
        </ol>
      )}
    </Modal>
  )
}
