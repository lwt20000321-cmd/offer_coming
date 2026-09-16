import { useEffect, useState, type FormEvent } from 'react'
import { isMockEnabled } from '../services/api'
import { useSession } from '../stores/SessionProvider'
import { isApiError } from '../utils/apiError'
import { isLlmKeyFormatValid, trimLlmKey } from '../utils/llmKey'

type KeyPanelProps = {
  open: boolean
  onClose: () => void
}

const PLACEHOLDER = '粘贴新的百炼 API Key'
const FORMAT_ERROR = '请粘贴以 sk- 开头的百炼 Key。'

export function KeyPanel({ open, onClose }: KeyPanelProps) {
  const { candidate, updateLlmKey } = useSession()
  const [llmKey, setLlmKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedNotice, setSavedNotice] = useState(false)
  const [unreachable, setUnreachable] = useState(false)

  const keyStatus = candidate && 'llm_key_status' in candidate ? candidate.llm_key_status : undefined
  const hasKey = candidate?.has_llm_api_key === true
  const showUpdate = !hasKey || keyStatus === 'missing' || keyStatus === 'invalid'
  const statusLabel = showUpdate && !savedNotice ? '请更新。完整 Key 不会显示。' : '已保存。完整 Key 不会显示。'

  useEffect(() => {
    if (!open) {
      return
    }
    setLlmKey('')
    setError(null)
    setSavedNotice(false)
    setUnreachable(false)
  }, [open])

  if (!open) {
    return null
  }

  async function onSave(event: FormEvent) {
    event.preventDefault()
    if (saving) {
      return
    }
    const trimmed = trimLlmKey(llmKey)
    if (!isLlmKeyFormatValid(trimmed)) {
      setError(FORMAT_ERROR)
      return
    }
    setSaving(true)
    setError(null)
    setUnreachable(false)
    try {
      const updated = await updateLlmKey(trimmed)
      setLlmKey('')
      setSavedNotice(true)
      setUnreachable(updated.llm_key_probe_status === 'unreachable')
    } catch (caught) {
      setSavedNotice(false)
      setUnreachable(false)
      setError(isApiError(caught) ? caught.errorText || caught.message : '这次没连上，请再试。')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="account-sheet is-on"
      id="sheet-key"
      role="dialog"
      aria-labelledby="key-title"
      aria-modal="true"
    >
      <form className="card account-sheet-card" onSubmit={onSave}>
        <h2 id="key-title" className="account-sheet-title">
          我的key
        </h2>
        <p className="lead">{statusLabel}</p>
        <div className="row account-sheet-row">
          <input
            className="email-input"
            id="new-key"
            name="llm_api_key"
            type="password"
            autoComplete="off"
            placeholder={PLACEHOLDER}
            value={llmKey}
            disabled={saving}
            onChange={(event) => {
              setLlmKey(event.target.value)
              if (error) setError(null)
            }}
            aria-invalid={Boolean(error)}
            aria-describedby={error ? 'key-panel-error' : undefined}
          />
        </div>
        {error ? (
          <p className="account-panel-error" id="key-panel-error" role="alert">
            {error}
          </p>
        ) : null}
        {unreachable ? (
          <p className="hint account-panel-hint" id="key-panel-hint" role="status">
            {isMockEnabled() ? '[Mock] 这次没连上' : '这次没连上'}
          </p>
        ) : null}
        <div className="account-sheet-actions">
          <button className="btn-primary" type="submit" id="save-key" disabled={saving}>
            {saving ? '正在确认 Key' : '保存'}
          </button>
          <button className="switch" type="button" id="close-key" disabled={saving} onClick={onClose}>
            取消
          </button>
        </div>
      </form>
    </div>
  )
}
