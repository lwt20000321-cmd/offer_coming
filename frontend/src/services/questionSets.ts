import axios from 'axios'
import { mockCandidateIdFromToken } from '../mocks/conversation'
import { mockGetQuestionSet, type QuestionPublic, type QuestionSetPublic } from '../mocks/questions'
import type { ApiEnvelope } from '../types/session'
import { ApiError, unwrapEnvelope } from '../utils/apiError'
import { readSessionToken } from '../utils/storage'
import api, { isMockEnabled } from './api'

export type { QuestionPublic, QuestionSetPublic }

function fromAxios<T>(error: unknown): never {
  if (axios.isAxiosError(error) && error.response) {
    const body = error.response.data as ApiEnvelope<T> | undefined
    throw new ApiError(error.response.status, body?.error_code ?? null, body?.error ?? null)
  }
  throw error
}

export async function getCurrentQuestionSet(): Promise<QuestionSetPublic> {
  if (isMockEnabled()) {
    const token = readSessionToken()
    const result = mockGetQuestionSet(Boolean(mockCandidateIdFromToken(token)), mockCandidateIdFromToken(token))
    return unwrapEnvelope(result.status, result.data)
  }
  try {
    const response = await api.get<ApiEnvelope<QuestionSetPublic>>('/question-sets/current')
    return unwrapEnvelope(response.status, response.data)
  } catch (error) {
    return fromAxios(error)
  }
}
