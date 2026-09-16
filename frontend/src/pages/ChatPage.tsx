import { useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { XiaaoMark } from '../components/XiaaoMark'
import { useChat } from '../hooks/useChat'
import type { ApplicationPublic } from '../services/applications'
import type { MessagePublic } from '../services/conversations'
import type { QuestionPublic } from '../services/questionSets'
import { useSession } from '../stores/SessionProvider'

const COMPOSER_MIN_PX = 48
const COMPOSER_MAX_PX = 96

function fitComposerHeight(node: HTMLTextAreaElement) {
  if (node.value === '') {
    node.classList.remove('is-tall')
    node.style.height = `${COMPOSER_MIN_PX}px`
    return
  }
  node.classList.add('is-tall')
  node.style.height = 'auto'
  const next = Math.min(Math.max(node.scrollHeight, COMPOSER_MIN_PX), COMPOSER_MAX_PX)
  if (next <= COMPOSER_MIN_PX && !node.value.includes('\n')) {
    node.classList.remove('is-tall')
    node.style.height = `${COMPOSER_MIN_PX}px`
    return
  }
  node.style.height = `${next}px`
}

function progressLine(item: ApplicationPublic): string {
  const name = `${item.company_name.replace('[Mock] ', '')}${item.role_title ? ` ${item.role_title.replace('[Mock] ', '')}` : ''}`
  if (item.normalized_status === 'rejected') {
    return `${name} · ${item.status_text} · 不再出题`
  }
  const when = item.deadline ?? item.progress_text.replace('[Mock] ', '')
  return `${name} · ${item.status_text} · ${when}`
}

function userVisibleText(content: string) {
  return content.replace(/^(你：\s*)+/, '')
}

function MessageBody({
  message,
  questions,
}: {
  message: MessagePublic
  questions: QuestionPublic[]
}) {
  if (message.role === 'user') {
    return (
      <div className="chat-msg is-user">
        <p className="chat-say chat-say-user">{userVisibleText(message.content)}</p>
      </div>
    )
  }
  if (message.message_type === 'questions') {
    return (
      <div className="chat-msg is-bot">
        <div className="chat-bubble-stack">
          {questions.length > 0 ? (
            <ol className="chat-q-list">
              {questions.map((question, index) => (
                <li className="chat-q" key={question.id}>
                  <b>{index + 1}.</b>
                  <div>{question.prompt}</div>
                </li>
              ))}
            </ol>
          ) : (
            <p className="chat-say">{message.content}</p>
          )}
        </div>
      </div>
    )
  }
  if (message.message_type === 'error_notice') {
    return (
      <div className="chat-msg is-bot">
        <p className="chat-say chat-danger" role="alert">
          {message.content}
        </p>
      </div>
    )
  }
  return (
    <div className="chat-msg is-bot">
      <p className="chat-say">{message.content}</p>
    </div>
  )
}

export default function ChatPage() {
  const { entryNotice } = useSession()
  const {
    messages,
    applications,
    questionSet,
    snapshot,
    input,
    setInput,
    sending,
    status,
    streamText,
    loadError,
    sendError,
    mood,
    send,
    runNudgeDemo,
    simulateUnauthorized,
    mockEnabled,
  } = useChat()
  const [progressOpen, setProgressOpen] = useState(false)
  const [composerActive, setComposerActive] = useState(false)
  const threadRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const questions = questionSet?.questions ?? []
  const waiting = snapshot?.waiting_interview_count ?? applications.filter((item) => item.normalized_status === 'waiting_interview').length
  const answered = questions.filter((item) => Boolean(item.answer)).length
  const total = questions.length
  const canSend = Boolean(input.trim()) && !sending
  const firstQuestionIndex = messages.findIndex((message) => message.message_type === 'questions')

  const summary = useMemo(() => {
    if (total > 0) {
      return `今日 ${answered}/${total} 题　·　${waiting} 个岗位等待面试`
    }
    return `今日暂无题目　·　${waiting} 个岗位等待面试`
  }, [answered, total, waiting])

  useEffect(() => {
    const node = threadRef.current
    if (node) {
      node.scrollTop = node.scrollHeight
    }
  }, [messages, streamText, status])

  useLayoutEffect(() => {
    const node = inputRef.current
    if (node) {
      fitComposerHeight(node)
    }
  }, [input])

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void send()
  }

  function onComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void send()
    }
  }

  return (
    <div className="stage chat-page">
      <style>{CHAT_CSS}</style>
      <div className="page-head chat-head">
        <div className="page-head-row">
          <XiaaoMark />
          <p className="chat-meta">
            {summary}
            {'　·　'}
            <button
              className="chat-link"
              type="button"
              onClick={() => setProgressOpen((open) => !open)}
            >
              {progressOpen ? '收起进度 ↑' : '查看进度 ↓'}
            </button>
          </p>
        </div>
      </div>
      {entryNotice ? (
        <p className="chat-entry-notice" role="status">
          {entryNotice}
        </p>
      ) : null}
      <div className={`chat-progress${progressOpen ? ' is-on' : ''}`}>
        {applications.length === 0 ? (
          <p>还没有记下的岗位。把已投链接发给我。{mockEnabled ? ' [Mock]' : ''}</p>
        ) : (
          applications.map((item) => (
            <p key={item.id}>{progressLine(item)}</p>
          ))
        )}
      </div>
      <div className="chat-panel">
        <div className="chat-panel-body">
        <div className="chat-thread" id="thread" ref={threadRef}>
        <div className="chat-thread-inner">
        <p className="chat-who">
          <i />
          小凹
        </p>
        {messages.map((message, index) => {
          if (message.message_type === 'questions' && index !== firstQuestionIndex) {
            return null
          }
          return (
            <MessageBody
              key={message.id}
              message={message}
              questions={message.message_type === 'questions' ? questions : []}
            />
          )
        })}
        {sending && !streamText ? (
          <div className="chat-msg is-bot">
            <p className="chat-status is-thinking" role="status">
              {mockEnabled ? `[Mock] ${status?.text || '小凹正在思考...'}` : status?.text || '小凹正在思考...'}
            </p>
          </div>
        ) : null}
        {streamText ? (
          <div className="chat-msg is-bot">
            <p className="chat-say">
              {streamText}
              <span className="chat-caret" aria-hidden="true" />
            </p>
          </div>
        ) : null}
        {sendError ? (
          <div className="chat-msg is-bot">
            <p className="chat-danger" role="alert">
              {sendError}
            </p>
          </div>
        ) : null}
        {loadError ? (
          <div className="chat-msg is-bot">
            <p className="chat-danger" role="alert">
              {loadError}
            </p>
          </div>
        ) : null}
        </div>
        </div>
        <form className="composer" onSubmit={onSubmit}>
          <div className="composer-row">
            <div className="composer-field">
              <label className="composer-sr" htmlFor="chat-input">
                和小凹说说
              </label>
              {input || composerActive ? null : (
                <span className="composer-ph" aria-hidden="true">
                  和小凹说说...
                </span>
              )}
              <textarea
                id="chat-input"
                ref={inputRef}
                value={input}
                onChange={(event) => {
                  setInput(event.target.value)
                  fitComposerHeight(event.target)
                }}
                onKeyDown={onComposerKey}
                onFocus={() => setComposerActive(true)}
                onBlur={() => setComposerActive(false)}
                disabled={sending}
                rows={1}
              />
            </div>
            <button className="chat-send" type="submit" disabled={!canSend} aria-label="发送">
              ↑
            </button>
          </div>
        </form>
        </div>
      </div>
      <div className="chat-toolbar">
        <div className="chat-bar">
          <span>{mood}</span>
          <span>北京时间 10:00 · 下次提醒 11:00</span>
        </div>
        {mockEnabled ? (
          <div className="chat-bar">
            <span className="chat-demo">
              <button className="chat-link" type="button" onClick={() => void runNudgeDemo('morning')}>
                演示早间催促
              </button>
              <button className="chat-link" type="button" onClick={() => void runNudgeDemo('evening')}>
                演示晚间催促
              </button>
              <button className="chat-link" type="button" onClick={() => void runNudgeDemo('email_fail')}>
                模拟邮件失败
              </button>
              <button className="chat-link" type="button" onClick={() => void simulateUnauthorized()}>
                模拟接续失效
              </button>
            </span>
            <span>Mock 演示，不会发真实邮件</span>
          </div>
        ) : null}
      </div>
    </div>
  )
}

const CHAT_CSS = `
.chat-page {
  flex: 1;
  min-height: 0;
  position: relative;
}
.chat-head.page-head {
  overflow: visible;
  padding-bottom: 23px;
}
.chat-head .mark {
  margin: 0;
  justify-content: flex-start;
  align-items: flex-end;
  height: 32px;
  overflow: visible;
}
.chat-head .mark-logo {
  height: 56px;
  margin-top: -24px;
}
.chat-head .page-head-row {
  align-items: flex-end;
  min-height: 32px;
}
.chat-entry-notice {
  flex: none;
  margin: 0 0 8px;
  color: var(--mute);
  font-size: 13px;
  font-weight: 400;
  line-height: 1.45;
}
.chat-meta {
  margin: 0;
  flex: 1;
  min-width: 0;
  color: var(--mute);
  font-size: 14px;
  line-height: 1.6;
}
.chat-panel {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-radius: 32px;
  box-shadow: 0 20px 60px rgba(10, 10, 10, 0.05);
  overflow: visible;
  background: transparent;
}
.chat-panel-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border-radius: 32px;
  overflow: hidden;
}
.chat-thread {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding: 20px 24px 12px;
  scrollbar-width: none;
  -ms-overflow-style: none;
}
.chat-thread::-webkit-scrollbar {
  width: 0;
  height: 0;
  display: none;
}
.chat-thread-inner {
  margin-top: auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.chat-who {
  display: flex;
  align-items: center;
  align-self: flex-start;
  gap: 8px;
  font-weight: 600;
  margin: 0 0 2px;
  font-size: 13px;
}
.chat-who i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ink);
  display: inline-block;
}
.chat-msg {
  display: flex;
  max-width: 76%;
  min-width: 0;
}
.chat-msg.is-bot { align-self: flex-start; }
.chat-msg.is-user { align-self: flex-end; }
.chat-bubble-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  width: 100%;
}
.chat-say {
  margin: 0;
  padding: 10px 14px;
  border-radius: 18px;
  color: var(--ink-soft);
  font-size: 14px;
  font-weight: 400;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.is-bot .chat-say {
  background: var(--pill);
  border-bottom-left-radius: 6px;
}
.chat-say-user {
  background: var(--pill);
  color: var(--ink);
  border-bottom-right-radius: 6px;
}
.chat-q-list {
  list-style: none;
  margin: 0;
  padding: 10px 14px;
  border-radius: 18px;
  border-bottom-left-radius: 6px;
  background: var(--pill);
}
.chat-q {
  display: grid;
  grid-template-columns: 24px 1fr;
  gap: 6px 10px;
  margin: 0 0 12px;
  color: var(--ink-soft);
  font-size: 14px;
  line-height: 1.55;
}
.chat-q b { font-weight: 600; color: var(--ink); }
.chat-q:last-child { margin-bottom: 0; }
.chat-q span {
  display: block;
  margin-top: 4px;
  color: var(--mute);
  font-size: 13px;
  font-weight: 400;
  line-height: 1.45;
}
.chat-progress {
  display: none;
  flex: none;
  margin: 0 0 8px;
  padding: 12px 18px;
  border-radius: 20px;
  background: rgba(255,255,255,0.7);
  color: var(--ink-soft);
  font-size: 14px;
  line-height: 1.6;
}
.chat-progress.is-on { display: block; }
.chat-progress p { margin: 0 0 8px; }
.chat-progress p:last-child { margin-bottom: 0; }
.composer {
  flex: none;
  min-height: 0;
  padding: 4px 24px 20px;
  background: transparent;
  box-shadow: none;
  border-radius: 0;
}
.composer-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.composer-field {
  position: relative;
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  min-height: 48px;
  border: 1px solid rgba(10, 10, 10, 0.08);
  border-radius: 16px;
  background: #fff;
  padding: 0 16px;
}
.composer-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
.composer-ph {
  position: absolute;
  left: 16px;
  top: 0;
  height: 48px;
  display: flex;
  align-items: center;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  color: var(--mute);
  pointer-events: none;
  white-space: nowrap;
}
.composer textarea {
  display: block;
  width: 100%;
  height: 48px;
  min-height: 48px;
  max-height: 96px;
  border: 0;
  resize: none;
  font-family: inherit;
  font-size: 14px;
  font-weight: 400;
  line-height: 48px;
  color: var(--ink);
  caret-color: var(--ink);
  outline: none;
  background: transparent;
  border-radius: 0;
  padding: 0;
  overflow-x: hidden;
  overflow-y: hidden;
  scrollbar-width: none;
}
.composer textarea.is-tall {
  line-height: 24px;
  padding: 12px 0;
  overflow-y: auto;
}
.composer textarea::-webkit-scrollbar {
  width: 0;
  height: 0;
  display: none;
}
.composer textarea:hover { background: transparent; }
.composer textarea:active { background: transparent; }
.composer textarea:focus-visible {
  outline: none;
}
.composer-field:focus-within {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.composer textarea:disabled {
  color: #a1a1aa;
  cursor: not-allowed;
}
.chat-send {
  flex: none;
  width: 44px;
  height: 44px;
  border: 0;
  border-radius: 999px;
  background: var(--ink);
  color: var(--on-ink);
  font-size: 18px;
  cursor: pointer;
}
.chat-send:hover { background: #27272a; }
.chat-send:active { background: #18181b; }
.chat-send:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.chat-send:disabled {
  background: #d4d4d8;
  color: #a1a1aa;
  cursor: not-allowed;
}
.chat-status {
  margin: 0;
  padding: 2px 4px;
  color: var(--mute);
  font-size: 12px;
  font-weight: 400;
  line-height: 1.45;
}
.chat-status.is-thinking {
  animation: think-pulse 1.2s ease-in-out infinite;
}
.chat-caret {
  display: inline-block;
  width: 2px;
  height: 0.9em;
  margin-left: 3px;
  background: currentColor;
  vertical-align: -0.08em;
  animation: caret-blink 1s step-end infinite;
}
@media (prefers-reduced-motion: reduce) {
  .chat-status.is-thinking,
  .chat-caret {
    animation: none;
  }
}
@keyframes think-pulse {
  0%, 100% { opacity: 0.55; }
  50% { opacity: 1; }
}
@keyframes caret-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
.chat-danger {
  margin: 0;
  padding: 10px 14px;
  border-radius: 18px;
  border-bottom-left-radius: 6px;
  background: #fff5f4;
  color: var(--danger);
  font-size: 13px;
  font-weight: 400;
  line-height: 1.45;
}
.is-bot .chat-say.chat-danger {
  background: #fff5f4;
  color: var(--danger);
}
.chat-toolbar {
  position: fixed;
  left: 28px;
  right: 28px;
  bottom: 12px;
  z-index: 3;
  display: flex;
  flex-direction: column;
  gap: 4px;
  pointer-events: none;
}
.chat-bar {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex: none;
  color: var(--mute);
  font-size: 13px;
  line-height: 1.45;
  pointer-events: none;
}
.chat-bar .chat-link { pointer-events: auto; }
.chat-demo { display: flex; flex-wrap: wrap; gap: 12px; }
.chat-link {
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
  border-radius: 8px;
  padding: 0;
}
.chat-link:hover { color: var(--ink-soft); }
.chat-link:active { color: var(--ink); }
.chat-link:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.chat-link:disabled {
  color: #a1a1aa;
  cursor: not-allowed;
}
@media (max-width: 768px) {
  .chat-toolbar { left: 16px; right: 16px; bottom: 10px; }
  .chat-panel,
  .chat-panel-body { border-radius: 24px; }
  .chat-bar { flex-direction: column; }
}
`
