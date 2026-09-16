import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router'
import { listApplications, type ApplicationPublic } from '../services/applications'
import { isMockEnabled } from '../services/api'
import {
  ensureRejectedKnowledgeAsk,
  expireMockSession,
  getCurrentConversation,
  injectMockNudge,
  listMessages,
  sendConversationMessage,
  type CandidateCurrentPublic,
  type MessagePublic,
} from '../services/conversations'
import { getCurrentQuestionSet, type QuestionSetPublic } from '../services/questionSets'
import { useSession } from '../stores/SessionProvider'
import { isApiError } from '../utils/apiError'
import { readChatDraft, writeChatDraft } from './useChatDraft'

const POLL_MS = 20_000

export type ChatStatus = {
  stage: string
  text: string
} | null

function mergeById(current: MessagePublic[], incoming: MessagePublic[]): MessagePublic[] {
  const map = new Map<string, MessagePublic>()
  for (const item of current) {
    if (!item.id.startsWith('local_')) {
      map.set(item.id, item)
    }
  }
  for (const item of incoming) {
    map.set(item.id, item)
  }
  return [...map.values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))
}

type ChatLocationState = {
  knowledgeAsk?: boolean
  applicationId?: string
}

export function useChat() {
  const location = useLocation()
  const locationState = (location.state as ChatLocationState | null) ?? null
  const pendingAskId = locationState?.knowledgeAsk ? locationState.applicationId : undefined
  const { conversationId: sessionConversationId, candidate, signOutLocal } = useSession()
  const [conversationId, setConversationId] = useState<string | null>(sessionConversationId)
  const [messages, setMessages] = useState<MessagePublic[]>([])
  const [applications, setApplications] = useState<ApplicationPublic[]>([])
  const [questionSet, setQuestionSet] = useState<QuestionSetPublic | null>(null)
  const [snapshot, setSnapshot] = useState<CandidateCurrentPublic | null>(
    candidate && 'today' in candidate ? candidate : null,
  )
  const [input, setInputState] = useState(readChatDraft)
  const [sending, setSending] = useState(false)
  const [status, setStatus] = useState<ChatStatus>(null)
  const [streamText, setStreamText] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)
  const [mood, setMood] = useState('准备面试')
  const lastIdRef = useRef<string | undefined>(undefined)
  const sendingRef = useRef(false)
  const inputRef = useRef(input)
  inputRef.current = input

  const setInput = useCallback((value: string) => {
    setInputState(value)
    writeChatDraft(value)
  }, [])

  const handleUnauthorized = useCallback(
    (error: unknown): boolean => {
      if (isApiError(error) && error.status === 401) {
        writeChatDraft(inputRef.current)
        signOutLocal()
        return true
      }
      return false
    },
    [signOutLocal],
  )

  const refreshSide = useCallback(async () => {
    const [apps, questions] = await Promise.all([listApplications(), getCurrentQuestionSet()])
    setApplications(apps)
    setQuestionSet(questions)
  }, [])

  const loadAll = useCallback(async () => {
    const conversation = await getCurrentConversation()
    setConversationId(conversation.id)
    if (pendingAskId) {
      ensureRejectedKnowledgeAsk(pendingAskId)
    }
    const history = await listMessages(conversation.id)
    setMessages(history)
    lastIdRef.current = history.at(-1)?.id
    await refreshSide()
  }, [pendingAskId, refreshSide])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        await loadAll()
        if (!cancelled) {
          setLoadError(null)
        }
      } catch (error) {
        if (cancelled) {
          return
        }
        if (handleUnauthorized(error)) {
          return
        }
        setLoadError(isApiError(error) ? error.errorText || error.message : '现在连不上对话，请稍后再试。')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [handleUnauthorized, loadAll])

  const poll = useCallback(async () => {
    if (!conversationId || sendingRef.current) {
      return
    }
    try {
      const incoming = await listMessages(conversationId, lastIdRef.current)
      if (incoming.length > 0) {
        setMessages((current) => {
          const next = mergeById(current, incoming)
          lastIdRef.current = next.at(-1)?.id
          return next
        })
        await refreshSide()
      }
    } catch (error) {
      handleUnauthorized(error)
    }
  }, [conversationId, handleUnauthorized, refreshSide])

  useEffect(() => {
    const timer = window.setInterval(() => {
      void poll()
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [poll])

  const send = useCallback(async (file?: File | null): Promise<boolean> => {
    const text = input.trim()
    if ((!text && !file) || !conversationId || sendingRef.current) {
      return false
    }
    setSendError(null)
    setSending(true)
    sendingRef.current = true
    const display = file ? (text ? `${text}\n[附件] ${file.name}` : `[附件] ${file.name}`) : text
    const localUser: MessagePublic = {
      id: `local_${Date.now()}`,
      role: 'user',
      message_type: 'chat',
      content: display,
      created_at: new Date().toISOString(),
    }
    setMessages((current) => [...current, localUser])
    setInput('')
    setStreamText('')
    setStatus({ stage: 'thinking', text: '小凹正在思考...' })
    try {
      let streamFailed = false
      await sendConversationMessage(
        conversationId,
        text,
        {
          onStatus: (payload) => {
            setStatus({ stage: payload.stage, text: payload.text })
          },
          onDelta: (payload) => {
            setStatus(null)
            setStreamText((current) => current + payload.text)
          },
          onDone: (payload) => {
            setSnapshot(payload.snapshot)
            setMood((current) =>
              payload.snapshot.today.counseling_active
                ? '正在听你说'
                : current === '正在听你说'
                  ? '心情慢慢回来了'
                  : current,
            )
            setMessages((current) => mergeById(current, [payload.message]))
          },
          onError: (payload) => {
            streamFailed = true
            setStatus(null)
            const notice: MessagePublic = {
              id: `local_err_${Date.now()}`,
              role: 'assistant',
              message_type: 'error_notice',
              content: payload.error,
              created_at: new Date().toISOString(),
            }
            setMessages((current) => [...current, notice])
          },
        },
        file,
      )
      const history = await listMessages(conversationId)
      setMessages((current) => {
        const next = mergeById(current, history)
        lastIdRef.current = next.at(-1)?.id
        return next
      })
      await refreshSide()
      if (streamFailed) {
        setSendError(null)
      }
      return true
    } catch (error) {
      try {
        const history = await listMessages(conversationId)
        setMessages((current) => {
          const next = mergeById(
            current.filter((item) => item.id !== localUser.id),
            history,
          )
          lastIdRef.current = next.at(-1)?.id
          return next
        })
      } catch {
        setMessages((current) => current.filter((item) => item.id !== localUser.id))
      }
      setInput(text)
      if (handleUnauthorized(error)) {
        return false
      }
      const message = isApiError(error) ? error.errorText || error.message : '这一句没发出去，请再试一次。'
      setSendError(message)
      return false
    } finally {
      setStatus(null)
      setStreamText('')
      setSending(false)
      sendingRef.current = false
    }
  }, [conversationId, handleUnauthorized, input, refreshSide, setInput])

  const runNudgeDemo = useCallback(
    async (kind: 'morning' | 'evening' | 'email_fail') => {
      if (!isMockEnabled() || !conversationId) {
        return
      }
      try {
        const inserted = await injectMockNudge(kind)
        if (inserted.length > 0) {
          setMessages((current) => mergeById(current, inserted))
          lastIdRef.current = inserted.at(-1)?.id ?? lastIdRef.current
        }
      } catch (error) {
        handleUnauthorized(error)
      }
    },
    [conversationId, handleUnauthorized],
  )

  const simulateUnauthorized = useCallback(async () => {
    writeChatDraft(inputRef.current)
    expireMockSession()
    try {
      if (conversationId) {
        await listMessages(conversationId)
      }
    } catch (error) {
      if (!handleUnauthorized(error)) {
        signOutLocal()
      }
    }
  }, [conversationId, handleUnauthorized, signOutLocal])

  return {
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
    mockEnabled: isMockEnabled(),
  }
}
