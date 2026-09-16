import type {
  ApiEnvelope,
  CandidateCurrentPublic,
  LlmKeyUpdatePublic,
  SessionRevokePublic,
} from '../types/session'
import { isAllowedResume, resumeHint } from '../utils/resume'
import { trimLlmKey, waitForLlmKeyProbe } from '../utils/llmKey'
import { mockExpireCurrentToken } from './conversation'
import {
  assertNoKeyLeak,
  classifySubmittedKey,
  mockCandidateByToken,
  mockFilename,
  revokeMockToken,
  toCurrentPublic,
} from './session'

type MockHttp<T> = {
  status: number
  data: ApiEnvelope<T>
}

let requestSeq = 0

function nowIso(): string {
  return new Date().toISOString()
}

function envelope<T>(
  success: boolean,
  data: T | null,
  extra: { error?: string | null; error_code?: string | null; message?: string | null },
): ApiEnvelope<T> {
  requestSeq += 1
  return {
    success,
    data,
    error: extra.error ?? null,
    error_code: extra.error_code ?? null,
    message: extra.message ?? null,
    timestamp: nowIso(),
    request_id: `req_mock_acct_${String(requestSeq).padStart(3, '0')}`,
    metadata: {},
  }
}

function fail<T>(status: number, error: string, errorCode: string): MockHttp<T> {
  return {
    status,
    data: envelope<T>(false, null, { error, error_code: errorCode, message: null }),
  }
}

const KEY_INVALID = '这把百炼 Key 不能用，请检查后重贴。'
const KEY_FORMAT = '请粘贴以 sk- 开头的百炼 Key。'
const NO_KEY = '还没有可用的百炼 Key，请先到「我的key」保存后再换简历。'
const RESUME_OK_PREFIX = '[Mock] 已换成'
const KEY_SAVED = '[Mock] 已保存新的百炼 Key'
const KEY_UNREACHABLE =
  '[Mock] 已保存新的百炼 Key。这次没连上百炼，之后若失败请到「我的key」再试。'
const SIGNOUT_OK = '[Mock] 已退出本机接续'
const LLM_KEY_MAX_LENGTH = 256

export function mockReplaceResume(
  token: string | null,
  resume: File | null,
): MockHttp<CandidateCurrentPublic> {
  const candidate = mockCandidateByToken(token)
  if (!candidate) {
    return fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED')
  }
  if (!candidate.has_llm_api_key) {
    return fail(403, NO_KEY, 'LLM_KEY_REQUIRED')
  }
  if (!resume) {
    return fail(400, '请选择一份简历文件。', 'VALIDATION_ERROR')
  }
  if (!isAllowedResume(resume) || resume.size === 0) {
    return fail(400, resumeHint(), 'VALIDATION_ERROR')
  }
  candidate.resume_filename = mockFilename(resume.name)
  candidate.resume_parse_ok = true
  const dto = toCurrentPublic(candidate)
  const result = {
    status: 200,
    data: envelope(true, dto, { message: `${RESUME_OK_PREFIX} ${dto.resume_filename}` }),
  }
  assertNoKeyLeak(result)
  return result
}

export async function mockUpdateLlmKey(
  token: string | null,
  llmApiKey: string | null,
): Promise<MockHttp<LlmKeyUpdatePublic>> {
  const candidate = mockCandidateByToken(token)
  if (!candidate) {
    return fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED')
  }
  const trimmed = llmApiKey == null ? '' : trimLlmKey(llmApiKey)
  const keyKind = classifySubmittedKey(trimmed)
  if (keyKind === 'missing' || keyKind === 'format' || trimmed.length > LLM_KEY_MAX_LENGTH) {
    return fail(400, KEY_FORMAT, 'VALIDATION_ERROR')
  }
  await waitForLlmKeyProbe()
  if (keyKind === 'invalid') {
    return fail(400, KEY_INVALID, 'LLM_KEY_INVALID')
  }
  candidate.has_llm_api_key = true
  candidate.llm_key_status = 'saved'
  const unreachable = keyKind === 'unreachable'
  const dto: LlmKeyUpdatePublic = {
    has_llm_api_key: true,
    llm_key_status: 'saved',
    llm_key_probe_status: unreachable ? 'unreachable' : 'ok',
  }
  const result = {
    status: 200,
    data: envelope(true, dto, { message: unreachable ? KEY_UNREACHABLE : KEY_SAVED }),
  }
  assertNoKeyLeak(result)
  return result
}

export function mockRevokeSession(token: string | null): MockHttp<SessionRevokePublic> {
  const existed = revokeMockToken(token)
  mockExpireCurrentToken(token)
  if (!existed) {
    return fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED')
  }
  const result = {
    status: 200,
    data: envelope(true, { revoked: true as const }, { message: SIGNOUT_OK }),
  }
  assertNoKeyLeak(result)
  return result
}
