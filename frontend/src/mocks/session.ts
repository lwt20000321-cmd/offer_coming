import type {
  ApiEnvelope,
  CandidateCurrentPublic,
  CandidatePublic,
  LlmKeyProbeStatus,
  LlmKeyStatus,
  SessionStartPublic,
} from '../types/session'
import { isValidEmail, normalizeEmail } from '../utils/email'
import { isLlmKeyFormatValid, trimLlmKey, waitForLlmKeyProbe } from '../utils/llmKey'
import { isAllowedResume, resumeHint } from '../utils/resume'

export type MockCandidate = {
  id: string
  email: string
  resume_filename: string
  resume_parse_ok: boolean
  has_llm_api_key: boolean
  llm_key_status: LlmKeyStatus
  created_at: string
  application_count: number
  waiting_interview_count: number
}

type MockHttp<T> = {
  status: number
  data: ApiEnvelope<T>
}

const KNOWN_EMAIL = 'lin@example.com'
const KNOWN_FILENAME = '[Mock] 林晓_产品简历.pdf'
const NO_KEY_EMAIL = 'nokey@example.com'
const NO_KEY_FILENAME = '[Mock] 待补贴Key简历.pdf'
const MOCK_INVALID_KEY = 'sk-invalid'
const MOCK_UNREACHABLE_KEY = 'sk-timeout'

const NEED_ONBOARDING = '请先填写邮箱、粘贴百炼 Key 并上传简历，才能和小凹对话。'
const KEY_INVALID = '这把百炼 Key 不能用，请检查后重贴。'
const KEY_REQUIRED = '这个邮箱还没有百炼 Key，请补贴后才能进入。'
const ENTER_OK = '[Mock] 已进入小凹'
const ENTER_UNREACHABLE =
  '[Mock] 已进入小凹。这次没连上百炼，之后若失败请到「我的key」再试。'
const CONTINUE_OK = '[Mock] 已接上原来的简历和任务'
const CONTINUE_UNREACHABLE =
  '[Mock] 已接上原来的简历和任务。这次没连上百炼，之后若失败请到「我的key」再试。'

let requestSeq = 0
const tokens = new Map<string, string>()
const candidates = new Map<string, MockCandidate>()
const MOCK_STORE_KEY = 'offer_coming_mock_store_v1'

function persistMockStore(): void {
  try {
    const payload = {
      tokens: [...tokens.entries()],
      candidates: [...candidates.entries()],
    }
    assertNoKeyLeak(payload)
    localStorage.setItem(MOCK_STORE_KEY, JSON.stringify(payload))
  } catch {
    // quota / private mode
  }
}

function hydrateMockStore(): boolean {
  try {
    const raw = localStorage.getItem(MOCK_STORE_KEY)
    if (!raw) {
      return false
    }
    const parsed = JSON.parse(raw) as {
      tokens?: [string, string][]
      candidates?: [string, MockCandidate][]
    }
    if (!Array.isArray(parsed.tokens) || !Array.isArray(parsed.candidates)) {
      return false
    }
    tokens.clear()
    candidates.clear()
    for (const [token, id] of parsed.tokens) {
      if (typeof token === 'string' && typeof id === 'string') {
        tokens.set(token, id)
      }
    }
    for (const [email, row] of parsed.candidates) {
      if (typeof email === 'string' && row && typeof row.id === 'string') {
        candidates.set(email, row)
      }
    }
    return candidates.size > 0
  } catch {
    return false
  }
}

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
    request_id: `req_mock_${String(requestSeq).padStart(3, '0')}`,
    metadata: {},
  }
}

function fail<T>(status: number, error: string, errorCode: string): MockHttp<T> {
  return {
    status,
    data: envelope<T>(false, null, { error, error_code: errorCode, message: null }),
  }
}

export function assertNoKeyLeak(payload: unknown): void {
  const text = JSON.stringify(payload)
  if (
    text.includes('"llm_api_key"') ||
    text.includes(MOCK_INVALID_KEY) ||
    text.includes(MOCK_UNREACHABLE_KEY)
  ) {
    throw new Error('Mock 响应禁止回显 Key')
  }
}

function seedCandidate(
  email: string,
  filename: string,
  hasKey: boolean,
  id: string,
): MockCandidate {
  const seeded: MockCandidate = {
    id,
    email,
    resume_filename: filename,
    resume_parse_ok: true,
    has_llm_api_key: hasKey,
    llm_key_status: hasKey ? 'saved' : 'missing',
    created_at: '2026-09-13T06:40:00Z',
    application_count: hasKey ? 2 : 0,
    waiting_interview_count: hasKey ? 1 : 0,
  }
  candidates.set(email, seeded)
  return seeded
}

if (!hydrateMockStore()) {
  seedCandidate(KNOWN_EMAIL, KNOWN_FILENAME, true, 'c_mock_known')
  seedCandidate(NO_KEY_EMAIL, NO_KEY_FILENAME, false, 'c_mock_nokey')
} else {
  if (!candidates.has(KNOWN_EMAIL)) {
    seedCandidate(KNOWN_EMAIL, KNOWN_FILENAME, true, 'c_mock_known')
  }
  if (!candidates.has(NO_KEY_EMAIL)) {
    seedCandidate(NO_KEY_EMAIL, NO_KEY_FILENAME, false, 'c_mock_nokey')
  }
}

export function mockFilename(original: string): string {
  return original.startsWith('[Mock]') ? original : `[Mock] ${original}`
}

function toCandidatePublic(candidate: MockCandidate): CandidatePublic {
  return {
    id: candidate.id,
    email: candidate.email,
    resume_filename: candidate.resume_filename,
    resume_parse_ok: candidate.resume_parse_ok,
    has_llm_api_key: candidate.has_llm_api_key,
    llm_key_status: candidate.llm_key_status,
    created_at: candidate.created_at,
  }
}

function toSessionStart(
  candidate: MockCandidate,
  message: string,
  status: number,
  probeStatus: LlmKeyProbeStatus | null,
): MockHttp<SessionStartPublic> {
  const session_token = `mock-session-${candidate.id}-${Date.now()}`
  tokens.set(session_token, candidate.id)
  const dto: SessionStartPublic = {
    session_token,
    candidate: toCandidatePublic(candidate),
    conversation_id: `cv_${candidate.id}`,
    llm_key_probe_status: probeStatus,
  }
  const result = {
    status,
    data: envelope(true, dto, { message }),
  }
  assertNoKeyLeak(result)
  persistMockStore()
  return result
}

function findById(id: string): MockCandidate | undefined {
  return [...candidates.values()].find((item) => item.id === id)
}

export function classifySubmittedKey(
  keyRaw: string | null | undefined,
): 'missing' | 'format' | 'invalid' | 'unreachable' | 'ok' {
  if (keyRaw == null || trimLlmKey(keyRaw) === '') {
    return 'missing'
  }
  const trimmed = trimLlmKey(keyRaw)
  if (!isLlmKeyFormatValid(trimmed)) {
    return 'format'
  }
  if (trimmed === MOCK_INVALID_KEY) {
    return 'invalid'
  }
  if (trimmed === MOCK_UNREACHABLE_KEY) {
    return 'unreachable'
  }
  return 'ok'
}

export async function mockCreateCandidate(
  emailRaw: string,
  resume: File | null,
  llmApiKey: string | null,
): Promise<MockHttp<SessionStartPublic>> {
  const email = normalizeEmail(emailRaw)
  const keyKind = classifySubmittedKey(llmApiKey)
  if (!email || !resume || keyKind === 'missing' || keyKind === 'format') {
    return fail(400, NEED_ONBOARDING, 'VALIDATION_ERROR')
  }
  if (!isValidEmail(email)) {
    return fail(400, NEED_ONBOARDING, 'VALIDATION_ERROR')
  }
  if (!isAllowedResume(resume)) {
    return fail(400, resumeHint(), 'VALIDATION_ERROR')
  }
  const existing = candidates.get(email)
  if (existing) {
    return fail(409, '这个邮箱已有记录，请直接用邮箱进入', 'CONFLICT')
  }
  await waitForLlmKeyProbe()
  if (keyKind === 'invalid') {
    return fail(400, KEY_INVALID, 'LLM_KEY_INVALID')
  }
  const candidate: MockCandidate = {
    id: `c_mock_${requestSeq + 1}`,
    email,
    resume_filename: mockFilename(resume.name),
    resume_parse_ok: true,
    has_llm_api_key: true,
    llm_key_status: 'saved',
    created_at: nowIso(),
    application_count: 0,
    waiting_interview_count: 0,
  }
  candidates.set(email, candidate)
  const unreachable = keyKind === 'unreachable'
  return toSessionStart(
    candidate,
    unreachable ? ENTER_UNREACHABLE : ENTER_OK,
    201,
    unreachable ? 'unreachable' : 'ok',
  )
}

export async function mockCreateSession(
  emailRaw: string,
  llmApiKey?: string | null,
): Promise<MockHttp<SessionStartPublic>> {
  const email = normalizeEmail(emailRaw)
  if (!email || !isValidEmail(email)) {
    return fail(400, '请填写已用过的邮箱，才能接上记录。', 'VALIDATION_ERROR')
  }
  const existing = candidates.get(email)
  if (!existing) {
    return fail(404, '这个邮箱还没有简历记录，请先上传简历并粘贴 Key。', 'NOT_FOUND')
  }
  if (existing.has_llm_api_key) {
    return toSessionStart(existing, CONTINUE_OK, 200, null)
  }
  const keyKind = classifySubmittedKey(llmApiKey)
  if (keyKind === 'missing') {
    return fail(409, KEY_REQUIRED, 'LLM_KEY_REQUIRED')
  }
  if (keyKind === 'format') {
    return fail(400, NEED_ONBOARDING, 'VALIDATION_ERROR')
  }
  await waitForLlmKeyProbe()
  if (keyKind === 'invalid') {
    return fail(400, KEY_INVALID, 'LLM_KEY_INVALID')
  }
  existing.has_llm_api_key = true
  existing.llm_key_status = 'saved'
  const unreachable = keyKind === 'unreachable'
  return toSessionStart(
    existing,
    unreachable ? CONTINUE_UNREACHABLE : CONTINUE_OK,
    200,
    unreachable ? 'unreachable' : 'ok',
  )
}

export function toCurrentPublic(candidate: MockCandidate): CandidateCurrentPublic {
  return {
    id: candidate.id,
    email: candidate.email,
    resume_filename: candidate.resume_filename,
    resume_parse_ok: candidate.resume_parse_ok,
    application_count: candidate.application_count,
    waiting_interview_count: candidate.waiting_interview_count,
    has_llm_api_key: candidate.has_llm_api_key,
    llm_key_status: candidate.llm_key_status,
    today: {
      beijing_date: '2026-09-13',
      is_question_day: true,
      is_rest_day: false,
      question_set_status: 'in_progress',
      counseling_active: false,
    },
  }
}

export function mockCandidateByToken(token: string | null): MockCandidate | null {
  if (!token || !tokens.has(token)) {
    return null
  }
  return findById(tokens.get(token) as string) ?? null
}

export function mockCandidateById(id: string): MockCandidate | null {
  return findById(id) ?? null
}

export function revokeMockToken(token: string | null): boolean {
  if (!token || !tokens.has(token)) {
    return false
  }
  tokens.delete(token)
  persistMockStore()
  return true
}

export function mockGetCurrentCandidate(token: string | null): MockHttp<CandidateCurrentPublic> {
  const candidate = mockCandidateByToken(token)
  if (!candidate) {
    return fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED')
  }
  const result = {
    status: 200,
    data: envelope(true, toCurrentPublic(candidate), { message: '[Mock] ok' }),
  }
  assertNoKeyLeak(result)
  persistMockStore()
  return result
}
