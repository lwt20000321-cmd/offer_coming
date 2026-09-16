import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import {
  replaceCurrentResume,
  revokeCurrentSession,
  updateCurrentLlmKey,
  type LlmKeyUpdateResult,
} from '../services/account'
import { isMockEnabled, onUnauthorized } from '../services/api'
import { createCandidate, getCurrentCandidate } from '../services/candidates'
import { createSession } from '../services/sessions'
import type { CandidateCurrentPublic, CandidatePublic, SessionStartPublic } from '../types/session'
import { ApiError } from '../utils/apiError'
import { clearSessionToken, readSessionToken, writeSessionToken } from '../utils/storage'

export type SessionStatus = 'loading' | 'authenticated' | 'anonymous'

type SessionContextValue = {
  status: SessionStatus
  candidate: CandidatePublic | CandidateCurrentPublic | null
  conversationId: string | null
  gateMessage: string | null
  entryNotice: string | null
  clearGateMessage: () => void
  requestChatAccess: () => boolean
  requestAppsAccess: () => boolean
  restoreSession: () => Promise<boolean>
  startWithResume: (email: string, resume: File, llmApiKey: string) => Promise<SessionStartPublic>
  continueWithEmail: (email: string, llmApiKey?: string) => Promise<SessionStartPublic>
  replaceResume: (resume: File) => Promise<CandidateCurrentPublic>
  updateLlmKey: (llmApiKey: string) => Promise<LlmKeyUpdateResult>
  signOut: () => Promise<void>
  signOutLocal: () => void
}

const SessionContext = createContext<SessionContextValue | null>(null)

const GATE_CHAT = '还不能进对话。请先填写邮箱、粘贴 Key 并上传简历，或只用已有邮箱接上。'
const GATE_APPS = '还不能进去。请先填写邮箱、粘贴 Key 并上传简历，或只用已有邮箱接上。'
const UNREACHABLE_NOTICE = '已进入小凹。这次没连上百炼，之后若失败请到「我的key」再试。'

function toCandidatePublic(started: SessionStartPublic): CandidatePublic {
  return started.candidate
}

function noticeFromStart(started: SessionStartPublic): string | null {
  if (started.llm_key_probe_status !== 'unreachable') {
    return null
  }
  return isMockEnabled() ? `[Mock] ${UNREACHABLE_NOTICE}` : UNREACHABLE_NOTICE
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SessionStatus>('loading')
  const [candidate, setCandidate] = useState<CandidatePublic | CandidateCurrentPublic | null>(null)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [gateMessage, setGateMessage] = useState<string | null>(null)
  const [entryNotice, setEntryNotice] = useState<string | null>(null)

  const signOutLocal = useCallback(() => {
    clearSessionToken()
    setCandidate(null)
    setConversationId(null)
    setStatus('anonymous')
    setEntryNotice(null)
  }, [])

  const persistStart = useCallback((started: SessionStartPublic) => {
    writeSessionToken(started.session_token)
    setCandidate(toCandidatePublic(started))
    setConversationId(started.conversation_id)
    setStatus('authenticated')
    setGateMessage(null)
    setEntryNotice(noticeFromStart(started))
    return started
  }, [])

  const restoreSession = useCallback(async () => {
    const token = readSessionToken()
    if (!token) {
      setStatus('anonymous')
      return false
    }
    try {
      const current = await getCurrentCandidate()
      if (current.has_llm_api_key === false) {
        clearSessionToken()
        setCandidate(null)
        setConversationId(null)
        setStatus('anonymous')
        return false
      }
      setCandidate(current)
      setConversationId(`cv_${current.id}`)
      setStatus('authenticated')
      return true
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        signOutLocal()
        return false
      }
      setStatus('anonymous')
      return false
    }
  }, [signOutLocal])

  useEffect(() => {
    void restoreSession()
  }, [restoreSession])

  useEffect(() => {
    return onUnauthorized(() => {
      setCandidate(null)
      setConversationId(null)
      setStatus('anonymous')
      setEntryNotice(null)
    })
  }, [])

  const requestProtectedAccess = useCallback(
    (message: string) => {
      if (status === 'authenticated') {
        setGateMessage(null)
        return true
      }
      setGateMessage(message)
      return false
    },
    [status],
  )

  const requestChatAccess = useCallback(() => {
    return requestProtectedAccess(GATE_CHAT)
  }, [requestProtectedAccess])

  const requestAppsAccess = useCallback(() => {
    return requestProtectedAccess(GATE_APPS)
  }, [requestProtectedAccess])

  const clearGateMessage = useCallback(() => {
    setGateMessage(null)
  }, [])

  const startWithResume = useCallback(
    async (email: string, resume: File, llmApiKey: string) => {
      const started = await createCandidate(email, resume, llmApiKey)
      return persistStart(started)
    },
    [persistStart],
  )

  const continueWithEmail = useCallback(
    async (email: string, llmApiKey?: string) => {
      const started = await createSession(llmApiKey ? { email, llm_api_key: llmApiKey } : { email })
      return persistStart(started)
    },
    [persistStart],
  )

  const replaceResume = useCallback(async (resume: File) => {
    const current = await replaceCurrentResume(resume)
    setCandidate(current)
    return current
  }, [])

  const updateLlmKey = useCallback(async (llmApiKey: string) => {
    const updated = await updateCurrentLlmKey(llmApiKey)
    setCandidate((current) =>
      current
        ? {
            ...current,
            has_llm_api_key: updated.has_llm_api_key,
            llm_key_status: updated.llm_key_status,
          }
        : current,
    )
    return updated
  }, [])

  const signOut = useCallback(async () => {
    try {
      await revokeCurrentSession()
    } catch {
      // 401 视为已退出；网络失败也清本机，避免卡在已进入状态
    }
    signOutLocal()
  }, [signOutLocal])

  const value = useMemo(
    () => ({
      status,
      candidate,
      conversationId,
      gateMessage,
      entryNotice,
      clearGateMessage,
      requestChatAccess,
      requestAppsAccess,
      restoreSession,
      startWithResume,
      continueWithEmail,
      replaceResume,
      updateLlmKey,
      signOut,
      signOutLocal,
    }),
    [
      status,
      candidate,
      conversationId,
      gateMessage,
      entryNotice,
      clearGateMessage,
      requestChatAccess,
      requestAppsAccess,
      restoreSession,
      startWithResume,
      continueWithEmail,
      replaceResume,
      updateLlmKey,
      signOut,
      signOutLocal,
    ],
  )

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext)
  if (!value) {
    throw new Error('useSession 必须在 SessionProvider 内使用')
  }
  return value
}
