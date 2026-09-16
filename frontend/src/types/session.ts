export type ApiEnvelope<T> = {
  success: boolean
  data: T | null
  error: string | null
  error_code: string | null
  message: string | null
  timestamp: string
  request_id: string
  metadata: Record<string, unknown>
}

export type LlmKeyStatus = 'missing' | 'saved' | 'invalid'

export type LlmKeyProbeStatus = 'ok' | 'unreachable'

export type CandidatePublic = {
  id: string
  email: string
  resume_filename: string
  resume_parse_ok: boolean
  has_llm_api_key: boolean
  llm_key_status: LlmKeyStatus
  created_at: string
}

export type SessionStartPublic = {
  session_token: string
  candidate: CandidatePublic
  conversation_id: string
  llm_key_probe_status: LlmKeyProbeStatus | null
}

export type SessionCreate = {
  email: string
  llm_api_key?: string
}

export type CandidateTodayPublic = {
  beijing_date: string
  is_question_day: boolean
  is_rest_day: boolean
  question_set_status: string
  counseling_active: boolean
}

export type CandidateCurrentPublic = {
  id: string
  email: string
  resume_filename: string
  resume_parse_ok: boolean
  application_count: number
  waiting_interview_count: number
  today: CandidateTodayPublic
  has_llm_api_key?: boolean
  llm_key_status?: LlmKeyStatus
}

export type LlmKeyUpdate = {
  llm_api_key: string
}

export type LlmKeyUpdatePublic = {
  has_llm_api_key: boolean
  llm_key_status: LlmKeyStatus
  llm_key_probe_status: LlmKeyProbeStatus
}

export type SessionRevokePublic = {
  revoked: true
}
