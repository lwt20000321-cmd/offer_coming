import axios from 'axios'
import {
  mockCreateApplication,
  mockDeleteApplication,
  mockGetApplications,
  mockPatchApplication,
} from '../mocks/applications'
import { mockCandidateIdFromToken } from '../mocks/conversation'
import type {
  ApplicationCreate,
  ApplicationDeletePublic,
  ApplicationPatchPublic,
  ApplicationPublic,
  ApplicationsListPublic,
  ApplicationUpdate,
} from '../types/applications'
import type { ApiEnvelope } from '../types/session'
import { ApiError, unwrapEnvelope } from '../utils/apiError'
import { readSessionToken } from '../utils/storage'
import api, { isMockEnabled } from './api'

export type {
  ApplicationCreate,
  ApplicationDeletePublic,
  ApplicationPatchPublic,
  ApplicationPublic,
  ApplicationsListPublic,
  ApplicationUpdate,
}

function fromAxios<T>(error: unknown): never {
  if (axios.isAxiosError(error) && error.response) {
    const body = error.response.data as ApiEnvelope<T> | undefined
    throw new ApiError(error.response.status, body?.error_code ?? null, body?.error ?? null)
  }
  throw error
}

function mockAuth(): { tokenOk: boolean; candidateId: string | null } {
  const candidateId = mockCandidateIdFromToken(readSessionToken())
  return { tokenOk: Boolean(candidateId), candidateId }
}

export async function listApplications(): Promise<ApplicationPublic[]> {
  if (isMockEnabled()) {
    const { tokenOk, candidateId } = mockAuth()
    const result = mockGetApplications(tokenOk, candidateId)
    return unwrapEnvelope(result.status, result.data).applications
  }
  try {
    const response = await api.get<ApiEnvelope<ApplicationsListPublic>>('/applications')
    return unwrapEnvelope(response.status, response.data).applications
  } catch (error) {
    return fromAxios(error)
  }
}

export async function createApplication(body: ApplicationCreate = {}): Promise<ApplicationPublic> {
  if (isMockEnabled()) {
    const { tokenOk, candidateId } = mockAuth()
    const result = mockCreateApplication(tokenOk, candidateId, body)
    return unwrapEnvelope(result.status, result.data)
  }
  try {
    const response = await api.post<ApiEnvelope<ApplicationPublic>>('/applications', body)
    return unwrapEnvelope(response.status, response.data)
  } catch (error) {
    return fromAxios(error)
  }
}

export async function patchApplication(
  applicationId: string,
  body: ApplicationUpdate,
): Promise<ApplicationPatchPublic> {
  if (isMockEnabled()) {
    const { tokenOk, candidateId } = mockAuth()
    const result = mockPatchApplication(tokenOk, candidateId, applicationId, body)
    return unwrapEnvelope(result.status, result.data)
  }
  try {
    const response = await api.patch<ApiEnvelope<ApplicationPatchPublic>>(
      `/applications/${applicationId}`,
      body,
    )
    return unwrapEnvelope(response.status, response.data)
  } catch (error) {
    return fromAxios(error)
  }
}

export async function deleteApplication(applicationId: string): Promise<ApplicationDeletePublic> {
  if (isMockEnabled()) {
    const { tokenOk, candidateId } = mockAuth()
    const result = mockDeleteApplication(tokenOk, candidateId, applicationId)
    return unwrapEnvelope(result.status, result.data)
  }
  try {
    const response = await api.delete<ApiEnvelope<ApplicationDeletePublic>>(`/applications/${applicationId}`)
    return unwrapEnvelope(response.status, response.data)
  } catch (error) {
    return fromAxios(error)
  }
}
