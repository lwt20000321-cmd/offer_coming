import type {
  ApplicationCreate,
  ApplicationDeletePublic,
  ApplicationPatchPublic,
  ApplicationPublic,
  ApplicationsListPublic,
  ApplicationUpdate,
  NormalizedStatus,
} from '../types/applications'
import type { ApiEnvelope } from '../types/session'
import { normalizeApplicationStatus } from '../constants/applicationStatus'

export type {
  ApplicationCreate,
  ApplicationDeletePublic,
  ApplicationPatchPublic,
  ApplicationPublic,
  ApplicationsListPublic,
  ApplicationUpdate,
}

type ApplicationRecord = ApplicationPublic & {
  has_related_knowledge: boolean
}

type MockHttp<T> = {
  status: number
  data: ApiEnvelope<T>
}

const KNOWN_ID = 'c_mock_known'
const STORAGE_KEY = 'offer_coming.mock.applications'
const store = new Map<string, ApplicationRecord[]>()
let appSeq = 0
let requestSeq = 0
let hydrated = false

function nowIso(): string {
  return new Date().toISOString()
}

export function todayBeijingDate(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
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
    request_id: `req_mock_app_${String(requestSeq).padStart(3, '0')}`,
    metadata: {},
  }
}

function fail<T>(status: number, error: string, errorCode: string): MockHttp<T> {
  return {
    status,
    data: envelope<T>(false, null, { error, error_code: errorCode, message: null }),
  }
}

function withMockLabel(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) {
    return ''
  }
  return trimmed.startsWith('[Mock]') ? trimmed : `[Mock] ${trimmed}`
}

function normalizeStatus(statusText: string): NormalizedStatus {
  return normalizeApplicationStatus(statusText)
}

function deadlineFromInterview(interviewAt: string | null): string | null {
  if (!interviewAt) {
    return null
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(interviewAt)) {
    return interviewAt
  }
  const parsed = new Date(interviewAt)
  if (Number.isNaN(parsed.getTime())) {
    return interviewAt.slice(0, 10)
  }
  return parsed.toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
}

function toListPublic(item: ApplicationRecord): ApplicationPublic {
  return {
    id: item.id,
    company_name: item.company_name,
    role_title: item.role_title,
    jd_url: item.jd_url,
    exam_points: item.exam_points,
    progress_text: item.progress_text,
    status_text: item.status_text,
    normalized_status: item.normalized_status,
    applied_at: item.applied_at,
    interview_at: item.interview_at,
    interview_summary: item.interview_summary,
    deadline: item.deadline,
    updated_at: item.updated_at,
  }
}

function toPatchPublic(item: ApplicationRecord, knowledgeReviewPending: boolean): ApplicationPatchPublic {
  return {
    ...toListPublic(item),
    knowledge_review_pending: knowledgeReviewPending,
  }
}

function persist(): void {
  if (typeof sessionStorage === 'undefined') {
    return
  }
  const payload: Record<string, ApplicationRecord[]> = {}
  store.forEach((items, candidateId) => {
    payload[candidateId] = items
  })
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
}

function hydrateRecord(raw: Partial<ApplicationRecord> & Pick<ApplicationRecord, 'id'>): ApplicationRecord {
  const interviewAt = raw.interview_at !== undefined ? raw.interview_at : (raw.deadline ?? null)
  const statusText = raw.status_text ?? ''
  return {
    id: raw.id,
    company_name: raw.company_name ?? '',
    role_title: raw.role_title ?? '',
    jd_url: raw.jd_url ?? '',
    exam_points: raw.exam_points ?? '',
    progress_text: raw.progress_text ?? '',
    status_text: statusText,
    normalized_status: raw.normalized_status ?? normalizeStatus(statusText),
    applied_at: raw.applied_at ?? todayBeijingDate(),
    interview_at: interviewAt,
    interview_summary: raw.interview_summary ?? '',
    deadline: raw.deadline !== undefined ? raw.deadline : deadlineFromInterview(interviewAt),
    updated_at: raw.updated_at ?? nowIso(),
    has_related_knowledge: raw.has_related_knowledge ?? false,
  }
}

function seedKnown(): ApplicationRecord[] {
  return [
    {
      id: 'a_mock_star',
      company_name: '[Mock] 星云科技',
      role_title: '[Mock] 产品经理',
      jd_url: 'https://jobs.example.com/pm-32',
      exam_points: '[Mock] 对照简历中的转化与激活项目，考查归因、从 0 到 1 的证据，而不是复述 JD。',
      progress_text: '[Mock] 已约下周一面',
      status_text: '等待面试',
      normalized_status: 'waiting_interview',
      applied_at: '2026-09-12',
      interview_at: '2026-09-20',
      interview_summary: '',
      deadline: '2026-09-20',
      updated_at: '2026-09-13T07:00:00Z',
      has_related_knowledge: true,
    },
    {
      id: 'a_mock_qinghe',
      company_name: '[Mock] 青禾',
      role_title: '[Mock] 渠道增长',
      jd_url: 'https://jobs.example.com/qinghe',
      exam_points: '[Mock] 对照简历里的投放复盘，考查预算减半时怎么取舍渠道。',
      progress_text: '[Mock] 已投递',
      status_text: '等待面试',
      normalized_status: 'waiting_interview',
      applied_at: '2026-09-10',
      interview_at: null,
      interview_summary: '',
      deadline: null,
      updated_at: '2026-09-13T06:00:00Z',
      has_related_knowledge: true,
    },
    {
      id: 'a_mock_data',
      company_name: '[Mock] 数据岗',
      role_title: '[Mock] 数据分析',
      jd_url: 'https://jobs.example.com/data-7',
      exam_points: '[Mock] 已挂岗位，考查点仅作进度留存。',
      progress_text: '[Mock] 流程结束',
      status_text: '已挂',
      normalized_status: 'rejected',
      applied_at: '2026-09-01',
      interview_at: null,
      interview_summary: '[Mock] 已结束',
      deadline: null,
      updated_at: '2026-09-13T04:00:00Z',
      has_related_knowledge: false,
    },
  ]
}

function restoreStore(): void {
  if (hydrated) {
    return
  }
  hydrated = true
  if (typeof sessionStorage !== 'undefined') {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY)
      if (raw) {
        const parsed = JSON.parse(raw) as Record<string, Array<Partial<ApplicationRecord> & Pick<ApplicationRecord, 'id'>>>
        Object.entries(parsed).forEach(([candidateId, items]) => {
          store.set(candidateId, items.map(hydrateRecord))
        })
      }
    } catch {
      store.clear()
    }
  }
  if (!store.has(KNOWN_ID)) {
    store.set(KNOWN_ID, seedKnown())
    persist()
  }
  let maxSeq = 0
  store.forEach((items) => {
    items.forEach((item) => {
      const matched = /^a_mock_(\d+)$/.exec(item.id)
      if (matched) {
        maxSeq = Math.max(maxSeq, Number(matched[1]))
      }
    })
  })
  appSeq = maxSeq
}

function findDuplicateUrl(items: ApplicationRecord[], jdUrl: string, exceptId?: string): ApplicationRecord | undefined {
  const url = jdUrl.trim()
  if (!url) {
    return undefined
  }
  return items.find((item) => item.id !== exceptId && item.jd_url.trim() === url)
}

export function mockEnsureApplications(candidateId: string): ApplicationPublic[] {
  restoreStore()
  const existing = store.get(candidateId)
  if (existing) {
    return existing
  }
  const created: ApplicationRecord[] = []
  store.set(candidateId, created)
  persist()
  return created
}

export function mockListApplicationsDto(candidateId: string): ApplicationsListPublic {
  return {
    applications: mockEnsureApplications(candidateId).map((item) => toListPublic(item as ApplicationRecord)),
  }
}

export function mockApplicationCounts(candidateId: string): {
  application_count: number
  waiting_interview_count: number
} {
  const items = mockEnsureApplications(candidateId)
  return {
    application_count: items.length,
    waiting_interview_count: items.filter((item) => item.normalized_status === 'waiting_interview').length,
  }
}

export function mockWaitingApplications(candidateId: string): ApplicationPublic[] {
  return mockEnsureApplications(candidateId).filter((item) => item.normalized_status === 'waiting_interview')
}

export function mockFindApplication(
  candidateId: string,
  predicate: (item: ApplicationPublic) => boolean,
): ApplicationPublic | undefined {
  return mockEnsureApplications(candidateId).find(predicate)
}

export function mockUpsertApplication(
  candidateId: string,
  patch: Partial<ApplicationPublic> & Pick<ApplicationPublic, 'company_name' | 'role_title'>,
): ApplicationPublic {
  restoreStore()
  const items = mockEnsureApplications(candidateId) as ApplicationRecord[]
  const matched =
    (patch.id ? items.find((item) => item.id === patch.id) : undefined) ??
    items.find((item) => item.company_name.includes(patch.company_name.replace('[Mock] ', ''))) ??
    items.find((item) => patch.company_name.includes(item.company_name.replace('[Mock] ', '')))

  if (matched) {
    const interviewAt =
      patch.interview_at !== undefined ? patch.interview_at : (patch.deadline !== undefined ? patch.deadline : matched.interview_at)
    Object.assign(matched, patch, {
      interview_at: interviewAt,
      deadline: patch.deadline !== undefined ? patch.deadline : deadlineFromInterview(interviewAt),
      applied_at: patch.applied_at ?? matched.applied_at,
      interview_summary: patch.interview_summary ?? matched.interview_summary,
      updated_at: nowIso(),
    })
    persist()
    return toListPublic(matched)
  }

  appSeq += 1
  const interviewAt = patch.interview_at !== undefined ? patch.interview_at : (patch.deadline ?? null)
  const created: ApplicationRecord = {
    id: `a_mock_${appSeq}`,
    company_name: patch.company_name.startsWith('[Mock]') ? patch.company_name : `[Mock] ${patch.company_name}`,
    role_title: patch.role_title.startsWith('[Mock]') ? patch.role_title : `[Mock] ${patch.role_title}`,
    jd_url: patch.jd_url ?? '',
    exam_points: patch.exam_points ?? '',
    progress_text: patch.progress_text ?? '[Mock] 已投递',
    status_text: patch.status_text ?? '等待面试',
    normalized_status: patch.normalized_status ?? 'waiting_interview',
    applied_at: patch.applied_at ?? todayBeijingDate(),
    interview_at: interviewAt,
    interview_summary: patch.interview_summary ?? '',
    deadline: patch.deadline !== undefined ? patch.deadline : deadlineFromInterview(interviewAt),
    updated_at: nowIso(),
    has_related_knowledge: false,
  }
  items.push(created)
  persist()
  return toListPublic(created)
}

export function mockGetApplications(tokenOk: boolean, candidateId: string | null): MockHttp<ApplicationsListPublic> {
  if (!tokenOk || !candidateId) {
    return fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED')
  }
  return {
    status: 200,
    data: envelope(true, mockListApplicationsDto(candidateId), { message: 'ok' }),
  }
}

export function mockCreateApplication(
  tokenOk: boolean,
  candidateId: string | null,
  body: ApplicationCreate,
): MockHttp<ApplicationPublic> {
  if (!tokenOk || !candidateId) {
    return fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED')
  }
  restoreStore()
  const items = mockEnsureApplications(candidateId) as ApplicationRecord[]
  const jdUrl = body.jd_url ?? ''
  if (findDuplicateUrl(items, jdUrl)) {
    return fail(409, '这个投递链接已经在表里，请改原来的那一行。', 'CONFLICT')
  }
  appSeq += 1
  const statusText = body.status_text ?? ''
  const interviewAt = body.interview_at ?? null
  const created: ApplicationRecord = {
    id: `a_mock_${appSeq}`,
    company_name: withMockLabel(body.company_name ?? ''),
    role_title: withMockLabel(body.role_title ?? ''),
    jd_url: jdUrl,
    exam_points: '',
    progress_text: body.progress_text ?? '',
    status_text: statusText,
    normalized_status: normalizeStatus(statusText),
    applied_at: body.applied_at?.trim() ? body.applied_at : todayBeijingDate(),
    interview_at: interviewAt,
    interview_summary: body.interview_summary ?? '',
    deadline: deadlineFromInterview(interviewAt),
    updated_at: nowIso(),
    has_related_knowledge: false,
  }
  items.push(created)
  persist()
  return {
    status: 201,
    data: envelope(true, toListPublic(created), { message: 'ok' }),
  }
}

export function mockPatchApplication(
  tokenOk: boolean,
  candidateId: string | null,
  applicationId: string,
  body: ApplicationUpdate,
): MockHttp<ApplicationPatchPublic> {
  if (!tokenOk || !candidateId) {
    return fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED')
  }
  restoreStore()
  const items = mockEnsureApplications(candidateId) as ApplicationRecord[]
  const current = items.find((item) => item.id === applicationId)
  if (!current) {
    return fail(404, '这一行不在了。', 'NOT_FOUND')
  }
  if (body.company_name?.trim() === '保存失败') {
    return fail(400, '这一格没存上，已恢复刚才的内容。', 'VALIDATION_ERROR')
  }
  if (body.jd_url !== undefined && findDuplicateUrl(items, body.jd_url, current.id)) {
    return fail(409, '这个投递链接已经在表里，请改原来的那一行。', 'CONFLICT')
  }

  const previousStatus = current.normalized_status
  if (body.company_name !== undefined) {
    current.company_name = withMockLabel(body.company_name)
  }
  if (body.role_title !== undefined) {
    current.role_title = withMockLabel(body.role_title)
  }
  if (body.jd_url !== undefined) {
    current.jd_url = body.jd_url
  }
  if (body.applied_at !== undefined) {
    current.applied_at = body.applied_at.trim() ? body.applied_at : todayBeijingDate()
  }
  if (body.interview_at !== undefined) {
    current.interview_at = body.interview_at
    current.deadline = deadlineFromInterview(body.interview_at)
  }
  if (body.status_text !== undefined) {
    current.status_text = body.status_text
    current.normalized_status = normalizeStatus(body.status_text)
  }
  if (body.interview_summary !== undefined) {
    current.interview_summary = body.interview_summary
  }
  if (body.progress_text !== undefined) {
    current.progress_text = body.progress_text
  }
  current.updated_at = nowIso()
  persist()

  const becameRejected = current.normalized_status === 'rejected' && previousStatus !== 'rejected'
  const knowledgeReviewPending = becameRejected && current.has_related_knowledge
  return {
    status: 200,
    data: envelope(true, toPatchPublic(current, knowledgeReviewPending), { message: 'ok' }),
  }
}

export function mockDeleteApplication(
  tokenOk: boolean,
  candidateId: string | null,
  applicationId: string,
): MockHttp<ApplicationDeletePublic> {
  if (!tokenOk || !candidateId) {
    return fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED')
  }
  restoreStore()
  const items = mockEnsureApplications(candidateId) as ApplicationRecord[]
  const index = items.findIndex((item) => item.id === applicationId)
  if (index < 0) {
    return fail(404, '这一行不在了。', 'NOT_FOUND')
  }
  items.splice(index, 1)
  persist()
  const dto: ApplicationDeletePublic = { id: applicationId, deleted: true }
  return {
    status: 200,
    data: envelope(true, dto, { message: 'ok' }),
  }
}
