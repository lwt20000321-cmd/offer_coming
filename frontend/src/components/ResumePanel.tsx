import { useEffect, useRef, useState } from 'react'
import { isMockEnabled } from '../services/api'
import { useSession } from '../stores/SessionProvider'
import { isApiError } from '../utils/apiError'
import { isAllowedResume, resumeHint } from '../utils/resume'

type ResumePanelProps = {
  open: boolean
  onClose: () => void
}

export function ResumePanel({ open, onClose }: ResumePanelProps) {
  const { candidate, replaceResume } = useSession()
  const inputRef = useRef<HTMLInputElement>(null)
  const [selected, setSelected] = useState<File | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const currentName = candidate?.resume_filename ?? '尚未在档'

  useEffect(() => {
    if (!open) {
      return
    }
    setError(null)
    setSuccess(null)
    setSelected(null)
    if (inputRef.current) {
      inputRef.current.value = ''
    }
  }, [open])

  if (!open) {
    return null
  }

  async function onSave() {
    if (!selected || saving) {
      return
    }
    if (!isAllowedResume(selected)) {
      setError(resumeHint())
      return
    }
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const updated = await replaceResume(selected)
      setSuccess(`已换成 ${updated.resume_filename}`)
    } catch (caught) {
      setError(isApiError(caught) ? caught.errorText || caught.message : '这次没换上，请再选一份简历。')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="account-sheet is-on"
      id="sheet-resume"
      role="dialog"
      aria-labelledby="resume-title"
      aria-modal="true"
    >
      <div className="card account-sheet-card">
        <h2 id="resume-title" className="account-sheet-title">
          我的简历
        </h2>
        <p className="lead" id="resume-on-file">
          当前在档：{currentName}
        </p>
        <div className="row account-sheet-row">
          <button
            className={`file-btn${selected ? ' has-file' : ''}`}
            type="button"
            id="replace-resume-btn"
            disabled={saving}
            onClick={() => inputRef.current?.click()}
          >
            {selected ? selected.name : '更换简历'}
          </button>
          <input
            id="replace-resume"
            ref={inputRef}
            type="file"
            accept=".pdf,.docx,.txt"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0] ?? null
              setSelected(file)
              setError(null)
              setSuccess(null)
            }}
          />
          <button className="btn-primary" type="button" disabled={!selected || saving} onClick={() => void onSave()}>
            {saving ? '保存中' : '保存'}
          </button>
        </div>
        {error ? (
          <p className="account-panel-error" id="resume-panel-error" role="alert">
            {error}
          </p>
        ) : null}
        {success ? (
          <p className="account-panel-success" role="status">
            {success}
          </p>
        ) : null}
        {isMockEnabled() ? <p className="account-panel-meta">[Mock] 换简历后不必再填邮箱或 Key</p> : null}
        <button className="btn-primary account-sheet-done" type="button" id="close-resume" onClick={onClose}>
          完成
        </button>
      </div>
    </div>
  )
}
