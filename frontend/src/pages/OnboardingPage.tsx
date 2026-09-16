import { useRef, useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router'
import { XiaaoMark } from '../components/XiaaoMark'
import { useSession } from '../stores/SessionProvider'
import { isApiError } from '../utils/apiError'
import { isValidEmail } from '../utils/email'
import { isLlmKeyFormatValid, trimLlmKey } from '../utils/llmKey'
import { isAllowedResume, resumeHint } from '../utils/resume'
import { readSessionToken } from '../utils/storage'

const NEED_ALL = '请先填写邮箱、粘贴 Key 并上传简历，才能和小凹对话。'
const NEED_EMAIL = '请填写已用过的邮箱，才能接上记录。'

export default function OnboardingPage() {
  const navigate = useNavigate()
  const {
    status,
    startWithResume,
    continueWithEmail,
    gateMessage,
    entryNotice,
    clearGateMessage,
  } = useSession()
  const [email, setEmail] = useState('')
  const [llmKey, setLlmKey] = useState('')
  const [resume, setResume] = useState<File | null>(null)
  const [resumeOnly, setResumeOnly] = useState(false)
  const [showKeyRow, setShowKeyRow] = useState(true)
  const [emailError, setEmailError] = useState<string | null>(null)
  const [keyError, setKeyError] = useState<string | null>(null)
  const [resumeError, setResumeError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [confirmingKey, setConfirmingKey] = useState(false)
  const resumeInputRef = useRef<HTMLInputElement>(null)

  const visibleError = formError || gateMessage

  function resetErrors() {
    setEmailError(null)
    setKeyError(null)
    setResumeError(null)
    setFormError(null)
    clearGateMessage()
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    resetErrors()

    const emailOk = isValidEmail(email)
    const needFile = !resumeOnly && !resume
    const needKey = showKeyRow
    const keyValue = trimLlmKey(llmKey)
    const keyOk = !needKey || isLlmKeyFormatValid(keyValue)

    if (!emailOk) {
      if (!email.trim()) {
        setEmailError(resumeOnly ? NEED_EMAIL : NEED_ALL)
      } else {
        setEmailError('请填写有效的邮箱地址。')
      }
    }
    if (needKey && !keyOk) {
      setKeyError(NEED_ALL)
    }
    if (needFile) {
      setResumeError(NEED_ALL)
    }
    if (!emailOk || needFile || (needKey && !keyOk)) {
      return
    }
    if (!resumeOnly && resume && !isAllowedResume(resume)) {
      setResumeError(resumeHint())
      return
    }

    const sendingKey = needKey
    setSubmitting(true)
    setConfirmingKey(sendingKey)
    try {
      if (resumeOnly) {
        await continueWithEmail(email, sendingKey ? keyValue : undefined)
      } else {
        await startWithResume(email, resume as File, keyValue)
      }
      navigate('/chat')
    } catch (error) {
      if (isApiError(error) && error.errorCode === 'LLM_KEY_REQUIRED') {
        setShowKeyRow(true)
        setFormError(error.errorText || error.message)
      } else if (isApiError(error)) {
        setFormError(error.errorText || error.message)
      } else {
        setFormError('现在连不上，请稍后再试。')
      }
    } finally {
      setSubmitting(false)
      setConfirmingKey(false)
    }
  }

  const primaryLabel = confirmingKey
    ? '正在确认 Key'
    : resumeOnly
      ? '接上记录 ↗'
      : '开始聊聊 ↗'

  if (status === 'authenticated') {
    return <Navigate to="/chat" replace />
  }
  if (status === 'loading' && readSessionToken()) {
    return (
      <p className="restore-status" role="status">
        正在接上…
      </p>
    )
  }

  return (
    <div className="stage">
      <div className="hero">
        <XiaaoMark />
        <h1 className="hero-title">
          <span className="hero-brand">小凹</span>，你的专属求职搭子
        </h1>
        <p className="lead">准备面试，跟进投递。也听你说说心事。</p>
      </div>
      <form className="card" onSubmit={onSubmit} noValidate>
        <div className="row">
          <span className="icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8">
              <rect x="3" y="6" width="18" height="12" rx="2" />
              <path d="m4 7 8 6 8-6" />
            </svg>
          </span>
          <input
            className="email-input"
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            placeholder="留下你的收件邮箱"
            value={email}
            onChange={(event) => {
              setEmail(event.target.value)
              if (emailError) setEmailError(null)
              if (gateMessage) clearGateMessage()
            }}
            aria-invalid={Boolean(emailError)}
            aria-describedby={emailError ? 'email-error' : undefined}
          />
        </div>
        {emailError ? (
          <p className="field-error" id="email-error">
            {emailError}
          </p>
        ) : null}

        {showKeyRow ? (
          <>
            <div className="row" id="key-row">
              <span className="icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <circle cx="8" cy="12" r="3" />
                  <path d="M11 12h10v3H14" />
                </svg>
              </span>
              <input
                className="email-input"
                id="llm-key"
                name="llm_api_key"
                type="password"
                autoComplete="off"
                placeholder="粘贴你的百炼 API Key"
                value={llmKey}
                onChange={(event) => {
                  setLlmKey(event.target.value)
                  if (keyError) setKeyError(null)
                }}
                aria-invalid={Boolean(keyError)}
                aria-describedby={keyError ? 'key-error' : undefined}
              />
            </div>
            {keyError ? (
              <p className="field-error" id="key-error">
                {keyError}
              </p>
            ) : null}
          </>
        ) : null}

        {resumeOnly ? (
          <div className="row">
            <button className="btn-primary" type="submit" disabled={submitting}>
              {primaryLabel}
            </button>
          </div>
        ) : (
          <div className="row">
            <span className="icon" aria-hidden="true">
              +
            </span>
            <button
              className={`file-btn${resume ? ' has-file' : ''}`}
              type="button"
              onClick={() => resumeInputRef.current?.click()}
            >
              {resume ? resume.name : '添加一份简历'}
            </button>
            <input
              id="resume"
              ref={resumeInputRef}
              name="resume"
              type="file"
              accept=".pdf,.docx,.txt"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0] ?? null
                setResume(file)
                if (resumeError) setResumeError(null)
              }}
            />
            <button className="btn-primary" type="submit" disabled={submitting}>
              {primaryLabel}
            </button>
          </div>
        )}
        {resumeError ? (
          <p className="field-error" id="resume-error">
            {resumeError}
          </p>
        ) : null}
        {visibleError ? <p className="form-error">{visibleError}</p> : null}
        {entryNotice ? (
          <p className="hint" role="status">
            {entryNotice}
          </p>
        ) : null}
      </form>
      <p className="hint">
        出题日，一起完成五道题。
        <br />
        北京时间 10:00–24:00，未完成时每小时对话和邮件提醒，越晚越严厉。
      </p>
      <button
        className="switch"
        type="button"
        onClick={() => {
          setResumeOnly((current) => {
            const next = !current
            setShowKeyRow(!next)
            return next
          })
          resetErrors()
        }}
      >
        {resumeOnly ? '我是第一次来，要上传简历和 Key' : '我换了设备，只用邮箱接上'}
      </button>
      <p className="foot">下次回来，接着聊。</p>
    </div>
  )
}
