import axios from 'axios'
import {
  mockDeleteKnowledgeItem,
  mockGetKnowledgeItem,
  mockGetKnowledgeItems,
  mockUploadKnowledgeItem,
} from '../mocks/knowledge'
import { mockCandidateIdFromToken } from '../mocks/conversation'
import type {
  KnowledgeItemDeletePublic,
  KnowledgeItemListPublic,
  KnowledgeItemPublic,
  KnowledgeItemsListPublic,
} from '../types/knowledge'
import type { ApiEnvelope } from '../types/session'
import { ApiError, unwrapEnvelope } from '../utils/apiError'
import { readSessionToken } from '../utils/storage'
import api, { isMockEnabled } from './api'

export type {
  KnowledgeItemDeletePublic,
  KnowledgeItemListPublic,
  KnowledgeItemPublic,
  KnowledgeItemsListPublic,
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

export async function listKnowledgeItems(applicationId?: string): Promise<KnowledgeItemListPublic[]> {
  if (isMockEnabled()) {
    const { tokenOk, candidateId } = mockAuth()
    const result = mockGetKnowledgeItems(tokenOk, candidateId, applicationId)
    return unwrapEnvelope(result.status, result.data).items
  }
  try {
    const response = await api.get<ApiEnvelope<KnowledgeItemsListPublic>>('/knowledge-items', {
      params: applicationId ? { application_id: applicationId } : undefined,
    })
    return unwrapEnvelope(response.status, response.data).items
  } catch (error) {
    return fromAxios(error)
  }
}

export async function uploadKnowledgeItem(file: File): Promise<KnowledgeItemPublic> {
  if (isMockEnabled()) {
    const { tokenOk, candidateId } = mockAuth()
    const name = file.name.toLowerCase()
    const readable = name.endsWith('.txt') || name.endsWith('.md')
    const body = readable ? (await file.text()).trim() : `已收录文件 ${file.name}`
    const result = mockUploadKnowledgeItem(tokenOk, candidateId, {
      title: file.name.replace(/\.[^.]+$/, '') || file.name,
      body,
    })
    return unwrapEnvelope(result.status, result.data)
  }
  const form = new FormData()
  form.append('file', file)
  try {
    const response = await api.post<ApiEnvelope<KnowledgeItemPublic>>('/knowledge-items', form)
    return unwrapEnvelope(response.status, response.data)
  } catch (error) {
    return fromAxios(error)
  }
}

export async function getKnowledgeItem(knowledgeItemId: string): Promise<KnowledgeItemPublic> {
  if (isMockEnabled()) {
    const { tokenOk, candidateId } = mockAuth()
    const result = mockGetKnowledgeItem(tokenOk, candidateId, knowledgeItemId)
    return unwrapEnvelope(result.status, result.data)
  }
  try {
    const response = await api.get<ApiEnvelope<KnowledgeItemPublic>>(`/knowledge-items/${knowledgeItemId}`)
    return unwrapEnvelope(response.status, response.data)
  } catch (error) {
    return fromAxios(error)
  }
}

export async function deleteKnowledgeItem(knowledgeItemId: string): Promise<KnowledgeItemDeletePublic> {
  if (isMockEnabled()) {
    const { tokenOk, candidateId } = mockAuth()
    const result = mockDeleteKnowledgeItem(tokenOk, candidateId, knowledgeItemId)
    return unwrapEnvelope(result.status, result.data)
  }
  try {
    const response = await api.delete<ApiEnvelope<KnowledgeItemDeletePublic>>(
      `/knowledge-items/${knowledgeItemId}`,
    )
    return unwrapEnvelope(response.status, response.data)
  } catch (error) {
    return fromAxios(error)
  }
}
