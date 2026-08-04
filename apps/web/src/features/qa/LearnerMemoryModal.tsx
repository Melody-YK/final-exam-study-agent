import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, LoaderCircle, Plus, Save, Trash2 } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'

import { studyApi } from '../../api/client'
import type {
  LearnerMemoryCreate,
  LearnerMemoryPatch,
  LearnerMemoryRecord,
  LearnerMemoryType,
} from '../../api/types'
import { ErrorNotice } from '../../components/ui/ErrorNotice'
import { IconButton } from '../../components/ui/IconButton'
import { Modal } from '../../components/ui/Modal'

const memoryTypeLabels: Record<LearnerMemoryType, string> = {
  preference: '解释偏好',
  confirmed_misconception: '已确认误解',
  learning_goal: '学习目标',
}

const memoryTypes = Object.keys(memoryTypeLabels) as LearnerMemoryType[]

interface MemoryEditorProps {
  deleting: boolean
  memory: LearnerMemoryRecord
  saving: boolean
  onDelete: (memoryId: string) => void
  onSave: (memoryId: string, input: LearnerMemoryPatch) => void
}

function MemoryEditor({ deleting, memory, saving, onDelete, onSave }: MemoryEditorProps) {
  const contentId = useId()
  const typeId = useId()
  const [content, setContent] = useState(memory.content)
  const [memoryType, setMemoryType] = useState<LearnerMemoryType>(memory.memory_type)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const normalizedContent = content.trim()
  const dirty = normalizedContent !== memory.content || memoryType !== memory.memory_type

  return (
    <article className="learner-memory-row">
      <div className="learner-memory-row__fields">
        <label htmlFor={typeId}>
          类型
          <select
            id={typeId}
            onChange={(event) => setMemoryType(event.target.value as LearnerMemoryType)}
            value={memoryType}
          >
            {memoryTypes.map((type) => (
              <option key={type} value={type}>
                {memoryTypeLabels[type]}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={contentId}>
          内容
          <textarea
            id={contentId}
            maxLength={1_000}
            onChange={(event) => setContent(event.target.value)}
            rows={2}
            value={content}
          />
        </label>
      </div>
      <div className="learner-memory-row__actions">
        {confirmingDelete ? (
          <>
            <button
              className="button button--danger button--small"
              disabled={deleting}
              onClick={() => onDelete(memory.id)}
              type="button"
            >
              {deleting ? <LoaderCircle aria-hidden="true" className="spin" size={14} /> : null}
              确认删除
            </button>
            <button
              className="button button--ghost button--small"
              disabled={deleting}
              onClick={() => setConfirmingDelete(false)}
              type="button"
            >
              取消
            </button>
          </>
        ) : (
          <>
            <IconButton
              disabled={!dirty || !normalizedContent || saving}
              label="保存记忆"
              onClick={() =>
                onSave(memory.id, {
                  memory_type: memoryType,
                  content: normalizedContent,
                })
              }
              size="small"
            >
              {saving ? (
                <LoaderCircle aria-hidden="true" className="spin" size={16} />
              ) : (
                <Save aria-hidden="true" size={16} />
              )}
            </IconButton>
            <IconButton
              disabled={saving}
              label="删除记忆"
              onClick={() => setConfirmingDelete(true)}
              size="small"
            >
              <Trash2 aria-hidden="true" size={16} />
            </IconButton>
          </>
        )}
      </div>
    </article>
  )
}

interface LearnerMemoryModalProps {
  courseId: string
  onClose: () => void
  open: boolean
}

export function LearnerMemoryModal({ courseId, onClose, open }: LearnerMemoryModalProps) {
  const queryClient = useQueryClient()
  const [newContent, setNewContent] = useState('')
  const [newType, setNewType] = useState<LearnerMemoryType>('preference')
  const memories = useQuery({
    queryKey: ['learner-memories', courseId],
    queryFn: () => studyApi.listLearnerMemories(courseId),
    enabled: open,
  })
  const createMemory = useMutation({
    mutationFn: (input: LearnerMemoryCreate) => studyApi.createLearnerMemory(courseId, input),
    onSuccess: (created) => {
      queryClient.setQueryData<LearnerMemoryRecord[]>(
        ['learner-memories', courseId],
        (current = []) => [created, ...current.filter((memory) => memory.id !== created.id)],
      )
      setNewContent('')
    },
  })
  const updateMemory = useMutation({
    mutationFn: ({ memoryId, input }: { memoryId: string; input: LearnerMemoryPatch }) =>
      studyApi.updateLearnerMemory(memoryId, input),
    onSuccess: (updated) => {
      queryClient.setQueryData<LearnerMemoryRecord[]>(
        ['learner-memories', courseId],
        (current = []) => current.map((memory) => (memory.id === updated.id ? updated : memory)),
      )
    },
  })
  const deleteMemory = useMutation({
    mutationFn: (memoryId: string) => studyApi.deleteLearnerMemory(memoryId),
    onSuccess: (_value, memoryId) => {
      queryClient.setQueryData<LearnerMemoryRecord[]>(
        ['learner-memories', courseId],
        (current = []) => current.filter((memory) => memory.id !== memoryId),
      )
    },
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const content = newContent.trim()
    if (!content || createMemory.isPending) return
    createMemory.mutate({ memory_type: newType, content })
  }
  const error = memories.error ?? createMemory.error ?? updateMemory.error ?? deleteMemory.error
  const close = () => {
    setNewContent('')
    createMemory.reset()
    updateMemory.reset()
    deleteMemory.reset()
    onClose()
  }

  return (
    <Modal description="当前课程" onClose={close} open={open} size="wide" title="学习记忆">
      <div className="learner-memory-manager">
        <form className="learner-memory-create" onSubmit={submit}>
          <label>
            类型
            <select
              onChange={(event) => setNewType(event.target.value as LearnerMemoryType)}
              value={newType}
            >
              {memoryTypes.map((type) => (
                <option key={type} value={type}>
                  {memoryTypeLabels[type]}
                </option>
              ))}
            </select>
          </label>
          <label>
            内容
            <input
              autoFocus
              maxLength={1_000}
              onChange={(event) => setNewContent(event.target.value)}
              value={newContent}
            />
          </label>
          <button
            className="button button--primary"
            disabled={!newContent.trim() || createMemory.isPending}
            type="submit"
          >
            {createMemory.isPending ? (
              <LoaderCircle aria-hidden="true" className="spin" size={16} />
            ) : (
              <Plus aria-hidden="true" size={16} />
            )}
            添加
          </button>
        </form>
        {error ? <ErrorNotice error={error} title="学习记忆未更新" /> : null}
        {memories.isPending ? (
          <div className="learner-memory-empty" role="status">
            <LoaderCircle aria-hidden="true" className="spin" size={22} />
            正在读取
          </div>
        ) : (memories.data ?? []).length === 0 ? (
          <div className="learner-memory-empty">
            <Brain aria-hidden="true" size={22} />
            暂无学习记忆
          </div>
        ) : (
          <div className="learner-memory-list">
            {(memories.data ?? []).map((memory) => (
              <MemoryEditor
                deleting={deleteMemory.isPending && deleteMemory.variables === memory.id}
                key={`${memory.id}-${memory.updated_at}`}
                memory={memory}
                onDelete={(memoryId) => deleteMemory.mutate(memoryId)}
                onSave={(memoryId, input) => updateMemory.mutate({ memoryId, input })}
                saving={updateMemory.isPending && updateMemory.variables?.memoryId === memory.id}
              />
            ))}
          </div>
        )}
      </div>
    </Modal>
  )
}
