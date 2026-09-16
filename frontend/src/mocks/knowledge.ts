import type {
  KnowledgeItemDeletePublic,
  KnowledgeItemListPublic,
  KnowledgeItemPublic,
  KnowledgeItemsListPublic,
  KnowledgeSegmentPublic,
  KnowledgeSourceType,
} from '../types/knowledge'
import type { ApiEnvelope } from '../types/session'

export type {
  KnowledgeItemDeletePublic,
  KnowledgeItemListPublic,
  KnowledgeItemPublic,
  KnowledgeItemsListPublic,
}

type KnowledgeRecord = KnowledgeItemListPublic & {
  body: string
  segments: KnowledgeSegmentPublic[]
}

type MockHttp<T> = {
  status: number
  data: ApiEnvelope<T>
}

const KNOWN_ID = 'c_mock_known'
const STORAGE_KEY = 'offer_coming.mock.knowledge'
const store = new Map<string, KnowledgeRecord[]>()
let requestSeq = 0
let itemSeq = 0
let hydrated = false

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
    request_id: `req_mock_kb_${String(requestSeq).padStart(3, '0')}`,
    metadata: {},
  }
}

function fail<T>(status: number, error: string, errorCode: string): MockHttp<T> {
  return {
    status,
    data: envelope<T>(false, null, { error, error_code: errorCode, message: null }),
  }
}

function excerptFromBody(body: string): string {
  return body.slice(0, 200)
}

function toListPublic(item: KnowledgeRecord): KnowledgeItemListPublic {
  return {
    id: item.id,
    title: item.title,
    source_type: item.source_type,
    source_url: item.source_url,
    application_id: item.application_id,
    company_name: item.company_name,
    role_title: item.role_title,
    created_at: item.created_at,
    excerpt: item.excerpt,
  }
}

function toDetailPublic(item: KnowledgeRecord): KnowledgeItemPublic {
  return {
    ...toListPublic(item),
    body: item.body,
    segments: item.segments.map((segment) => ({
      id: segment.id,
      ordinal: segment.ordinal,
      text: segment.text,
      status: segment.status,
    })),
  }
}

function persist(): void {
  if (typeof sessionStorage === 'undefined') {
    return
  }
  const payload: Record<string, KnowledgeRecord[]> = {}
  store.forEach((items, candidateId) => {
    payload[candidateId] = items
  })
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
}

function hydrateRecord(raw: Partial<KnowledgeRecord> & Pick<KnowledgeRecord, 'id'>): KnowledgeRecord {
  const body = raw.body ?? ''
  const sourceType: KnowledgeSourceType =
    raw.source_type === 'upload' || raw.source_type === 'paste' || raw.source_type === 'search'
      ? raw.source_type
      : 'search'
  return {
    id: raw.id,
    title: raw.title ?? '',
    source_type: sourceType,
    source_url: raw.source_url ?? null,
    application_id: raw.application_id ?? null,
    company_name: raw.company_name ?? '',
    role_title: raw.role_title ?? '',
    created_at: raw.created_at ?? nowIso(),
    excerpt: raw.excerpt ?? excerptFromBody(body),
    body,
    segments: Array.isArray(raw.segments) ? raw.segments : [],
  }
}

function seedKnown(): KnowledgeRecord[] {
  const xingheBody = '[Mock] 常见追问：激活怎么定义、有没有对照组、你怎么排除季节因素。'
  const uploadBody = '[Mock] 预算减半时先砍不能讲清贡献的渠道，留下能复盘的投放。'
  return [
    {
      id: 'k_mock_xinghe',
      title: '[Mock] 星河一面：激活指标怎么问',
      source_type: 'search',
      source_url: null,
      application_id: 'a_mock_star',
      company_name: '[Mock] 星河',
      role_title: '[Mock] 一面',
      created_at: '2026-09-13T07:10:00Z',
      excerpt: excerptFromBody(xingheBody),
      body: xingheBody,
      segments: [
        {
          id: 'ks_mock_xinghe_1',
          ordinal: 1,
          text: xingheBody,
          status: 'active',
        },
      ],
    },
    {
      id: 'k_mock_upload',
      title: '[Mock] 你上传的渠道面经.pdf',
      source_type: 'upload',
      source_url: null,
      application_id: 'a_mock_qinghe',
      company_name: '[Mock] 青禾',
      role_title: '[Mock] 渠道增长',
      created_at: '2026-09-13T06:20:00Z',
      excerpt: excerptFromBody(uploadBody),
      body: uploadBody,
      segments: [
        {
          id: 'ks_mock_upload_1',
          ordinal: 1,
          text: uploadBody,
          status: 'active',
        },
      ],
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
        const parsed = JSON.parse(raw) as Record<string, Array<Partial<KnowledgeRecord> & Pick<KnowledgeRecord, 'id'>>>
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
      const matched = /^k_mock_added_(\d+)$/.exec(item.id)
      if (matched) {
        maxSeq = Math.max(maxSeq, Number(matched[1]))
      }
    })
  })
  itemSeq = maxSeq
}

function itemsFor(candidateId: string): KnowledgeRecord[] {
  restoreStore()
  const existing = store.get(candidateId)
  if (existing) {
    return existing
  }
  const created: KnowledgeRecord[] = []
  store.set(candidateId, created)
  persist()
  return created
}

export function mockGetKnowledgeItems(
  tokenOk: boolean,
  candidateId: string | null,
  applicationId?: string,
): MockHttp<KnowledgeItemsListPublic> {
  if (!tokenOk || !candidateId) {
    return fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED')
  }
  const items = itemsFor(candidateId)
    .filter((item) => !applicationId || item.application_id === applicationId)
    .map((item) => toListPublic(item))
  return {
    status: 200,
    data: envelope(true, { items }, { message: 'ok' }),
  }
}

export function mockGetKnowledgeItem(
  tokenOk: boolean,
  candidateId: string | null,
  knowledgeItemId: string,
): MockHttp<KnowledgeItemPublic> {
  if (!tokenOk || !candidateId) {
    return fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED')
  }
  const current = itemsFor(candidateId).find((item) => item.id === knowledgeItemId)
  if (!current) {
    return fail(404, '这条面经不在了。', 'NOT_FOUND')
  }
  return {
    status: 200,
    data: envelope(true, toDetailPublic(current), { message: 'ok' }),
  }
}

export function mockUploadKnowledgeItem(
  tokenOk: boolean,
  candidateId: string | null,
  input: { title: string; body: string },
): MockHttp<KnowledgeItemPublic> {
  if (!tokenOk || !candidateId) {
    return fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED')
  }
  const body = input.body.trim()
  if (!body) {
    return fail(400, '没能读出这份面经的正文，请改发文本或换一份文件。', 'VALIDATION_FAILED')
  }
  const listed = mockIngestKnowledge(candidateId, {
    title: input.title || body.slice(0, 40),
    body,
    source_type: 'upload',
  })
  const created = itemsFor(candidateId).find((item) => item.id === listed.id)
  if (!created) {
    return fail(400, '没能读出这份面经的正文，请改发文本或换一份文件。', 'VALIDATION_FAILED')
  }
  return {
    status: 201,
    data: envelope(true, toDetailPublic(created), { message: 'ok' }),
  }
}

export function mockListKnowledgeForApplication(
  candidateId: string,
  applicationId: string,
): KnowledgeItemListPublic[] {
  return itemsFor(candidateId)
    .filter((item) => item.application_id === applicationId)
    .map((item) => toListPublic(item))
}

export function mockHasKnowledgeForApplication(candidateId: string, applicationId: string): boolean {
  return mockListKnowledgeForApplication(candidateId, applicationId).length > 0
}

export function mockEnsureSearchKnowledge(
  candidateId: string,
  application: {
    id: string
    company_name: string
    role_title: string
    jd_url: string
  },
): KnowledgeItemListPublic {
  const existing = itemsFor(candidateId).find(
    (item) => item.application_id === application.id && item.source_type === 'search',
  )
  if (existing) {
    return toListPublic(existing)
  }
  return mockAddKnowledgeItem(candidateId, {
    title: `${application.company_name.replace('[Mock] ', '')} ${application.role_title.replace('[Mock] ', '')} 相关面经`.trim(),
    source_type: 'search',
    source_url: application.jd_url || null,
    application_id: application.id,
    company_name: application.company_name.replace('[Mock] ', ''),
    role_title: application.role_title.replace('[Mock] ', ''),
    body: '[Mock] 对照该岗位能对上的准备点：激活怎么定义、渠道怎么取舍，以及从 0 到 1 的证据。不是编造的帖子。',
  })
}

export function mockIngestKnowledge(
  candidateId: string,
  input: {
    title: string
    body: string
    source_type: 'upload' | 'paste'
    application_id?: string | null
    company_name?: string
    role_title?: string
  },
): KnowledgeItemListPublic {
  return mockAddKnowledgeItem(candidateId, {
    title: input.title,
    source_type: input.source_type,
    source_url: null,
    application_id: input.application_id ?? null,
    company_name: input.company_name ?? '',
    role_title: input.role_title ?? '',
    body: input.body,
  })
}

export function mockAddKnowledgeItem(
  candidateId: string,
  input: {
    title: string
    source_type: KnowledgeSourceType
    source_url?: string | null
    application_id?: string | null
    company_name: string
    role_title: string
    body: string
  },
): KnowledgeItemListPublic {
  const items = itemsFor(candidateId)
  itemSeq += 1
  const body = input.body.startsWith('[Mock]') ? input.body : `[Mock] ${input.body}`
  const title = input.title.startsWith('[Mock]') ? input.title : `[Mock] ${input.title}`
  const created: KnowledgeRecord = {
    id: `k_mock_added_${itemSeq}`,
    title,
    source_type: input.source_type,
    source_url: input.source_url ?? null,
    application_id: input.application_id ?? null,
    company_name: input.company_name
      ? input.company_name.startsWith('[Mock]')
        ? input.company_name
        : `[Mock] ${input.company_name}`
      : '',
    role_title: input.role_title
      ? input.role_title.startsWith('[Mock]')
        ? input.role_title
        : `[Mock] ${input.role_title}`
      : '',
    created_at: nowIso(),
    excerpt: excerptFromBody(body),
    body,
    segments: [
      {
        id: `ks_mock_added_${itemSeq}_1`,
        ordinal: 1,
        text: body,
        status: 'active',
      },
    ],
  }
  items.unshift(created)
  persist()
  return toListPublic(created)
}

export function mockDeleteKnowledgeItem(
  tokenOk: boolean,
  candidateId: string | null,
  knowledgeItemId: string,
): MockHttp<KnowledgeItemDeletePublic> {
  if (!tokenOk || !candidateId) {
    return fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED')
  }
  const items = itemsFor(candidateId)
  const index = items.findIndex((item) => item.id === knowledgeItemId)
  if (index < 0) {
    return fail(404, '这条面经不在了。', 'NOT_FOUND')
  }
  items.splice(index, 1)
  persist()
  const dto: KnowledgeItemDeletePublic = { id: knowledgeItemId, deleted: true }
  return {
    status: 200,
    data: envelope(true, dto, { message: 'ok' }),
  }
}
