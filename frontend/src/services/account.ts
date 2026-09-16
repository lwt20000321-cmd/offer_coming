import axios from 'axios'
import { mockReplaceResume, mockRevokeSession, mockUpdateLlmKey } from '../mocks'
import type {
  ApiEnvelope,
  CandidateCurrentPublic,
  LlmKeyUpdate,
  LlmKeyUpdatePublic,
  SessionRevokePublic,
} from '../types/session'
import { ApiError, unwrapEnvelope } from '../utils/apiError'
import { readSessionToken } from '../utils/storage'
import api, { isMockEnabled } from './api'

export type LlmKeyUpdateResult = LlmKeyUpdatePublic & {
  message: string | null
}

function fromAxios<T>(error: unknown): never {
  if (axios.isAxiosError(error) && error.response) {
    const body = error.response.data as ApiEnvelope<T> | undefined
    throw new ApiError(error.response.status, body?.error_code ?? null, body?.error ?? null)
  }
  throw error
}

export async function replaceCurrentResume(resume: File): Promise<CandidateCurrentPublic> {
  if (isMockEnabled()) {
    const result = mockReplaceResume(readSessionToken(), resume)
    return unwrapEnvelope(result.status, result.data)
  }
  try {
    const form = new FormData()
    form.append('resume', resume)
    const response = await api.post<ApiEnvelope<CandidateCurrentPublic>>(
      '/candidates/current/resume',
      form,
      { timeout: 60000 },
    )
    return unwrapEnvelope(response.status, response.data)
  } catch (error) {
    return fromAxios(error)
  }
}

export async function updateCurrentLlmKey(llmApiKey: string): Promise<LlmKeyUpdateResult> {
  if (isMockEnabled()) {
    const result = await mockUpdateLlmKey(readSessionToken(), llmApiKey)
    const data = unwrapEnvelope(result.status, result.data)
    return { ...data, message: result.data.message }
  }
  try {
    const payload: LlmKeyUpdate = { llm_api_key: llmApiKey }
    const response = await api.put<ApiEnvelope<LlmKeyUpdatePublic>>(
      '/candidates/current/llm-key',
      payload,
      { timeout: 60000 },
    )
    const data = unwrapEnvelope(response.status, response.data)
    return { ...data, message: response.data.message }
  } catch (error) {
    return fromAxios(error)
  }
}

export async function revokeCurrentSession(): Promise<SessionRevokePublic | null> {
  if (isMockEnabled()) {
    const result = mockRevokeSession(readSessionToken())
    if (result.status === 401) {
      throw new ApiError(result.status, result.data.error_code, result.data.error)
    }
    return unwrapEnvelope(result.status, result.data)
  }
  try {
    const response = await api.delete<ApiEnvelope<SessionRevokePublic>>('/sessions/current')
    return unwrapEnvelope(response.status, response.data)
  } catch (error) {
    return fromAxios(error)
  }
}
