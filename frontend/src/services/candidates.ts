import axios from 'axios'
import { mockCreateCandidate, mockGetCurrentCandidate } from '../mocks'
import type { ApiEnvelope, CandidateCurrentPublic, SessionStartPublic } from '../types/session'
import { ApiError, unwrapEnvelope } from '../utils/apiError'
import { readSessionToken } from '../utils/storage'
import api, { isMockEnabled } from './api'

function fromAxios<T>(error: unknown): never {
  if (axios.isAxiosError(error) && error.response) {
    const body = error.response.data as ApiEnvelope<T> | undefined
    throw new ApiError(error.response.status, body?.error_code ?? null, body?.error ?? null)
  }
  throw error
}

export async function createCandidate(
  email: string,
  resume: File,
  llmApiKey: string,
): Promise<SessionStartPublic> {
  if (isMockEnabled()) {
    const result = await mockCreateCandidate(email, resume, llmApiKey)
    return unwrapEnvelope(result.status, result.data)
  }
  try {
    const form = new FormData()
    form.append('email', email)
    form.append('resume', resume)
    form.append('llm_api_key', llmApiKey)
    const response = await api.post<ApiEnvelope<SessionStartPublic>>('/candidates', form, {
      timeout: 60000,
    })
    return unwrapEnvelope(response.status, response.data)
  } catch (error) {
    return fromAxios(error)
  }
}

export async function getCurrentCandidate(): Promise<CandidateCurrentPublic> {
  if (isMockEnabled()) {
    const result = mockGetCurrentCandidate(readSessionToken())
    return unwrapEnvelope(result.status, result.data)
  }
  try {
    const response = await api.get<ApiEnvelope<CandidateCurrentPublic>>('/candidates/current')
    return unwrapEnvelope(response.status, response.data)
  } catch (error) {
    return fromAxios(error)
  }
}
