import axios from 'axios'
import { mockCreateSession } from '../mocks'
import type { ApiEnvelope, SessionCreate, SessionStartPublic } from '../types/session'
import { ApiError, unwrapEnvelope } from '../utils/apiError'
import api, { isMockEnabled } from './api'

export async function createSession(payload: SessionCreate): Promise<SessionStartPublic> {
  if (isMockEnabled()) {
    const result = await mockCreateSession(payload.email, payload.llm_api_key)
    return unwrapEnvelope(result.status, result.data)
  }
  const body: SessionCreate = { email: payload.email }
  const key = payload.llm_api_key?.trim()
  if (key) {
    body.llm_api_key = key
  }
  try {
    const response = await api.post<ApiEnvelope<SessionStartPublic>>('/sessions', body, {
      timeout: key ? 60000 : 10000,
    })
    return unwrapEnvelope(response.status, response.data)
  } catch (error) {
    if (axios.isAxiosError(error) && error.response) {
      const body = error.response.data as ApiEnvelope<SessionStartPublic> | undefined
      throw new ApiError(error.response.status, body?.error_code ?? null, body?.error ?? null)
    }
    throw error
  }
}
