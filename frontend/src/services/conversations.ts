import axios from 'axios'
import type { ApiEnvelope, CandidateCurrentPublic } from '../types/session'
import {
  mockEnsureKbAsk,
  mockExpireCurrentToken,
  mockGetCurrentConversation,
  mockInjectNudge,
  mockListMessages,
  mockSendMessageStream,
  type AgentDeltaEvent,
  type AgentDoneEvent,
  type AgentErrorEvent,
  type AgentStatusEvent,
  type ConversationPublic,
  type MessagePublic,
  type MessagesListPublic,
} from '../mocks/conversation'
import { ApiError, unwrapEnvelope } from '../utils/apiError'
import { readSessionToken } from '../utils/storage'
import api, { isMockEnabled, notifyUnauthorized } from './api'

export type {
  ConversationPublic,
  MessagePublic,
  MessagesListPublic,
  AgentStatusEvent,
  AgentDeltaEvent,
  AgentDoneEvent,
  AgentErrorEvent,
}

export type SendMessageHandlers = {
  onStatus: (payload: AgentStatusEvent) => void
  onDelta: (payload: AgentDeltaEvent) => void
  onDone: (payload: AgentDoneEvent) => void
  onError: (payload: AgentErrorEvent) => void
}

function fromAxios<T>(error: unknown): never {
  if (axios.isAxiosError(error) && error.response) {
    const body = error.response.data as ApiEnvelope<T> | undefined
    throw new ApiError(error.response.status, body?.error_code ?? null, body?.error ?? null)
  }
  throw error
}

function throwHttp(status: number, body: ApiEnvelope<null>): never {
  throw new ApiError(status, body.error_code, body.error)
}

export async function getCurrentConversation(): Promise<ConversationPublic> {
  if (isMockEnabled()) {
    const result = mockGetCurrentConversation(readSessionToken())
    return unwrapEnvelope(result.status, result.data)
  }
  try {
    const response = await api.get<ApiEnvelope<ConversationPublic>>('/conversations/current')
    return unwrapEnvelope(response.status, response.data)
  } catch (error) {
    return fromAxios(error)
  }
}

export async function listMessages(
  conversationId: string,
  afterId?: string,
  limit = 200,
): Promise<MessagePublic[]> {
  if (isMockEnabled()) {
    const result = mockListMessages(readSessionToken(), conversationId, afterId, limit)
    return unwrapEnvelope(result.status, result.data).messages
  }
  try {
    const response = await api.get<ApiEnvelope<MessagesListPublic>>(
      `/conversations/${conversationId}/messages`,
      { params: afterId ? { after_id: afterId, limit } : { limit } },
    )
    return unwrapEnvelope(response.status, response.data).messages
  } catch (error) {
    return fromAxios(error)
  }
}

function parseSseChunk(
  buffer: string,
): { events: Array<{ event: string; data: string }>; rest: string } {
  const events: Array<{ event: string; data: string }> = []
  const parts = buffer.split('\n\n')
  const rest = parts.pop() ?? ''
  for (const block of parts) {
    let eventName = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trim())
      }
    }
    if (dataLines.length > 0) {
      events.push({ event: eventName, data: dataLines.join('\n') })
    }
  }
  return { events, rest }
}

function dispatchSse(eventName: string, raw: string, handlers: SendMessageHandlers): void {
  const payload = JSON.parse(raw) as Record<string, unknown>
  if (eventName === 'status') {
    handlers.onStatus(payload as AgentStatusEvent)
    return
  }
  if (eventName === 'delta') {
    handlers.onDelta(payload as AgentDeltaEvent)
    return
  }
  if (eventName === 'done') {
    handlers.onDone(payload as unknown as AgentDoneEvent)
    return
  }
  if (eventName === 'error') {
    handlers.onError(payload as unknown as AgentErrorEvent)
  }
}

async function sendViaFetch(
  conversationId: string,
  content: string,
  handlers: SendMessageHandlers,
  file?: File | null,
): Promise<void> {
  const token = readSessionToken()
  const baseURL = import.meta.env.VITE_API_BASE_URL || '/api'
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }
  let body: BodyInit
  if (file) {
    const form = new FormData()
    if (content.trim()) {
      form.append('content', content)
    }
    form.append('file', file)
    body = form
  } else {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify({ content })
  }
  const response = await fetch(`${baseURL}/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers,
    body,
  })
  const contentType = response.headers.get('content-type') ?? ''
  if (!response.ok || !contentType.includes('text/event-stream')) {
    let body: ApiEnvelope<null> | undefined
    try {
      body = (await response.json()) as ApiEnvelope<null>
    } catch {
      body = undefined
    }
    if (response.status === 401) {
      notifyUnauthorized()
    }
    throw new ApiError(response.status, body?.error_code ?? null, body?.error ?? '请求失败')
  }
  if (!response.body) {
    throw new ApiError(500, 'INTERNAL_ERROR', '小凹这一轮没有返回内容。')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      break
    }
    buffer += decoder.decode(value, { stream: true })
    const parsed = parseSseChunk(buffer)
    buffer = parsed.rest
    for (const item of parsed.events) {
      dispatchSse(item.event, item.data, handlers)
    }
  }
}

export async function sendConversationMessage(
  conversationId: string,
  content: string,
  handlers: SendMessageHandlers,
  file?: File | null,
): Promise<void> {
  if (isMockEnabled()) {
    for await (const event of mockSendMessageStream(readSessionToken(), conversationId, content, file)) {
      if (event.event === 'http_error') {
        throwHttp(event.status, event.body)
      }
      if (event.event === 'status') {
        handlers.onStatus(event.data)
      } else if (event.event === 'delta') {
        handlers.onDelta(event.data)
      } else if (event.event === 'done') {
        handlers.onDone(event.data)
      } else if (event.event === 'error') {
        handlers.onError(event.data)
      }
    }
    return
  }
  await sendViaFetch(conversationId, content, handlers, file)
}

export function ensureRejectedKnowledgeAsk(applicationId: string): MessagePublic[] {
  if (!isMockEnabled()) {
    return []
  }
  const result = mockEnsureKbAsk(readSessionToken(), applicationId)
  if (result.status !== 200 || !result.data.success || !result.data.data) {
    return []
  }
  return result.data.data.messages
}

export async function injectMockNudge(kind: 'morning' | 'evening' | 'email_fail'): Promise<MessagePublic[]> {
  if (!isMockEnabled()) {
    return []
  }
  const result = mockInjectNudge(readSessionToken(), kind)
  return unwrapEnvelope(result.status, result.data).messages
}

export function expireMockSession(): void {
  if (isMockEnabled()) {
    mockExpireCurrentToken(readSessionToken())
  }
}

export type { CandidateCurrentPublic }
