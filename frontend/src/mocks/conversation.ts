import type { ApiEnvelope, CandidateCurrentPublic } from '../types/session'
import {
  mockApplicationCounts,
  mockEnsureApplications,
  mockFindApplication,
  mockUpsertApplication,
  mockWaitingApplications,
} from './applications'
import {
  mockAnswerNextQuestion,
  mockEnsureQuestionDay,
  mockGetQuestionSetRecord,
  mockQuestionProgress,
} from './questions'
import {
  mockEnsureSearchKnowledge,
  mockHasKnowledgeForApplication,
  mockIngestKnowledge,
  mockListKnowledgeForApplication,
} from './knowledge'
import { mockCandidateById } from './session'
import type { ApplicationPublic } from '../types/applications'
import {
  canonicalStatusFromText,
  normalizeApplicationStatus,
  STATUS_PROGRESS_ASK,
} from '../constants/applicationStatus'

export type MessagePublic = {
  id: string
  role: 'user' | 'assistant'
  message_type:
    | 'chat'
    | 'nudge'
    | 'questions'
    | 'review'
    | 'counseling'
    | 'jd_summary'
    | 'error_notice'
    | 'kb_notice'
    | 'kb_ask'
  content: string
  created_at: string
}

export type ConversationPublic = {
  id: string
  candidate_id: string
  created_at: string
}

export type MessagesListPublic = {
  messages: MessagePublic[]
}

export type AgentStatusEvent = {
  stage:
    | 'thinking'
    | 'fetch_jd'
    | 'update_application'
    | 'search_experiences'
    | 'ingest_document'
    | 'counseling'
    | 'questions'
    | 'review'
    | 'reply'
  text: string
}

export type AgentDeltaEvent = {
  text: string
}

export type AgentDoneEvent = {
  message: MessagePublic
  snapshot: CandidateCurrentPublic
}

export type AgentErrorEvent = {
  error: string
  error_code: string
}

export type MockSseEvent =
  | { event: 'status'; data: AgentStatusEvent }
  | { event: 'delta'; data: AgentDeltaEvent }
  | { event: 'done'; data: AgentDoneEvent }
  | { event: 'error'; data: AgentErrorEvent }

type MockHttp<T> = {
  status: number
  data: ApiEnvelope<T>
}

type ConversationState = {
  conversation: ConversationPublic
  messages: MessagePublic[]
  counselingActive: boolean
  mood: string
}

const expiredTokens = new Set<string>()
const busyFlags = new Set<string>()
const states = new Map<string, ConversationState>()
let messageSeq = 20
let requestSeq = 0

const URL_RE = /https?:\/\/[^\s]+/i
const FAIL_RE = /打不开|读不到|失败|unreachable|invalid|\/fail\b/i
const KB_FAIL_RE = /检索失败|找不到面经|kb-fail|no-jingyan|\/noke\b/i
const ANXIETY_RE = /不开心|焦虑|难受|崩溃|郁闷|慌/
const BETTER_RE = /心情变好|好多了|不焦虑|没事了|心情好了/
const REJECTED_RE = /已挂|挂了|挂掉|简历挂/
const PROGRESS_RE = /进度|状态|deadline|截止|一面|二面|三面|约了|周五|下周|筛选|测评/
const JOB_RE = /岗位|JD|职位|投了|投递/
const PASTE_RE = /粘贴面经|这是面经|面经如下|面经正文/
const FORBIDDEN_STATUS = /主体|子\s*Agent|工具/
const KEY_INVALID_RE = /key无效|key 无效|key失效|演示key失效|llm-key-invalid/i
const KEY_INVALID_NOTICE = '[Mock] 请到「我的key」更新后再聊。这次没有当成成功的模型回复。'

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
    request_id: `req_mock_cv_${String(requestSeq).padStart(3, '0')}`,
    metadata: {},
  }
}

function fail<T>(status: number, error: string, errorCode: string): MockHttp<T> {
  return {
    status,
    data: envelope<T>(false, null, { error, error_code: errorCode, message: null }),
  }
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms)
  })
}

function reduceMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function mockCandidateIdFromToken(token: string | null): string | null {
  if (!token || expiredTokens.has(token) || !token.startsWith('mock-session-')) {
    return null
  }
  const rest = token.slice('mock-session-'.length)
  const splitAt = rest.lastIndexOf('-')
  if (splitAt <= 0) {
    return null
  }
  const candidateId = rest.slice(0, splitAt)
  return candidateId.startsWith('c_mock_') ? candidateId : null
}

export function mockExpireCurrentToken(token: string | null): void {
  if (token) {
    expiredTokens.add(token)
  }
}

function conversationIdFor(candidateId: string): string {
  return `cv_${candidateId}`
}

function nextMessage(partial: Omit<MessagePublic, 'id' | 'created_at'> & { created_at?: string }): MessagePublic {
  messageSeq += 1
  return {
    id: `m_mock_${messageSeq}`,
    created_at: partial.created_at ?? nowIso(),
    role: partial.role,
    message_type: partial.message_type,
    content: partial.content,
  }
}

function toMessage(item: MessagePublic): MessagePublic {
  return {
    id: item.id,
    role: item.role,
    message_type: item.message_type,
    content: item.content,
    created_at: item.created_at,
  }
}

function toConversation(item: ConversationPublic): ConversationPublic {
  return {
    id: item.id,
    candidate_id: item.candidate_id,
    created_at: item.created_at,
  }
}

function profileOf(candidateId: string): {
  email: string
  resume_filename: string
  has_llm_api_key: boolean
  llm_key_status: CandidateCurrentPublic['llm_key_status']
} {
  const live = mockCandidateById(candidateId)
  if (live) {
    return {
      email: live.email,
      resume_filename: live.resume_filename,
      has_llm_api_key: live.has_llm_api_key,
      llm_key_status: live.llm_key_status,
    }
  }
  if (candidateId === 'c_mock_known') {
    return {
      email: 'lin@example.com',
      resume_filename: '[Mock] 林晓_产品简历.pdf',
      has_llm_api_key: true,
      llm_key_status: 'saved',
    }
  }
  return {
    email: `[Mock] ${candidateId}@example.com`,
    resume_filename: '[Mock] 简历',
    has_llm_api_key: true,
    llm_key_status: 'saved',
  }
}

export function mockBuildSnapshot(candidateId: string, counselingActive: boolean): CandidateCurrentPublic {
  const counts = mockApplicationCounts(candidateId)
  const questions = mockQuestionProgress(candidateId)
  const questionStatus = questions.total === 0 ? 'none' : questions.status
  const profile = profileOf(candidateId)
  return {
    id: candidateId,
    email: profile.email,
    resume_filename: profile.resume_filename,
    resume_parse_ok: true,
    has_llm_api_key: profile.has_llm_api_key,
    llm_key_status: profile.llm_key_status,
    application_count: counts.application_count,
    waiting_interview_count: counts.waiting_interview_count,
    today: {
      beijing_date: '2026-09-13',
      is_question_day: questionStatus !== 'none' && questionStatus !== 'rest_day',
      is_rest_day: false,
      question_set_status: questionStatus,
      counseling_active: counselingActive,
    },
  }
}

function seedKnown(state: ConversationState): void {
  mockEnsureApplications('c_mock_known')
  state.messages = [
    nextMessage({
      role: 'assistant',
      message_type: 'chat',
      content: '[Mock] 早呀，星云科技的一面还在进度里。今天给你准备了五道题，先从你最熟悉的项目开始。',
      created_at: '2026-09-13T02:05:00Z',
    }),
    nextMessage({
      role: 'assistant',
      message_type: 'questions',
      content: '[Mock] 今日五题，按 1 / 2 / 3 / 4 / 5 作答。',
      created_at: '2026-09-13T02:05:01Z',
    }),
  ]
}

function ensureState(candidateId: string): ConversationState {
  const existing = states.get(candidateId)
  if (existing) {
    return existing
  }
  const created: ConversationState = {
    conversation: {
      id: conversationIdFor(candidateId),
      candidate_id: candidateId,
      created_at: candidateId === 'c_mock_known' ? '2026-09-13T06:40:00Z' : nowIso(),
    },
    messages: [],
    counselingActive: false,
    mood: '准备面试',
  }
  if (candidateId === 'c_mock_known') {
    seedKnown(created)
  } else {
    created.messages = [
      nextMessage({
        role: 'assistant',
        message_type: 'chat',
        content: '[Mock] 简历我收下了。把你投过的岗位和链接发给我，我对照简历总结考查点。',
      }),
    ]
  }
  states.set(candidateId, created)
  return created
}

function requireCandidate(token: string | null): { ok: true; candidateId: string } | { ok: false; response: MockHttp<never> } {
  const candidateId = mockCandidateIdFromToken(token)
  if (!candidateId) {
    return {
      ok: false,
      response: fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED'),
    }
  }
  return { ok: true, candidateId }
}

export function mockGetCurrentConversation(token: string | null): MockHttp<ConversationPublic> {
  const auth = requireCandidate(token)
  if (!auth.ok) {
    return auth.response
  }
  const state = ensureState(auth.candidateId)
  return {
    status: 200,
    data: envelope(true, toConversation(state.conversation), { message: 'ok' }),
  }
}

export function mockListMessages(
  token: string | null,
  conversationId: string,
  afterId?: string,
  limit = 200,
): MockHttp<MessagesListPublic> {
  const auth = requireCandidate(token)
  if (!auth.ok) {
    return auth.response
  }
  const state = ensureState(auth.candidateId)
  if (conversationId !== state.conversation.id) {
    return fail(403, '这段对话不属于当前求职者。', 'FORBIDDEN')
  }
  const capped = Math.min(Math.max(limit, 1), 200)
  let items = state.messages.map(toMessage)
  if (afterId) {
    const index = items.findIndex((item) => item.id === afterId)
    items = index >= 0 ? items.slice(index + 1) : items
    items = items.slice(0, capped)
  } else {
    items = items.slice(-capped)
  }
  return {
    status: 200,
    data: envelope(true, { messages: items }, { message: 'ok' }),
  }
}

function extractUrl(text: string): string | null {
  const match = text.match(URL_RE)
  return match ? match[0] : null
}

function companyFromText(text: string): string {
  if (/星云|星河/.test(text)) {
    return '星云科技'
  }
  if (/青禾/.test(text)) {
    return '青禾'
  }
  if (/数据岗/.test(text)) {
    return '数据岗'
  }
  return '示例公司'
}

function companyFromUrl(url: string): string | null {
  if (/pm-32|xingyun|星云/.test(url)) {
    return '星云科技'
  }
  if (/qinghe|青禾/.test(url)) {
    return '青禾'
  }
  if (/data-7|数据/.test(url)) {
    return '数据岗'
  }
  return null
}

function isExperiencePaste(text: string): boolean {
  if (URL_RE.test(text)) {
    return false
  }
  if (PASTE_RE.test(text)) {
    return true
  }
  return text.includes('面经') && text.length >= 40
}

function kbAskContent(company: string, titles: string[]): string {
  const listed = titles.length > 0 ? titles.map((title) => title.replace('[Mock] ', '')).join('、') : '相关条目'
  return `[Mock] ${company.replace('[Mock] ', '')}已挂。相关面经「${listed}」里，整篇对别的岗位准备还有用，建议先留着；只绑这一岗的局部可以删。现在先不删。你回我留还是按建议删？`
}

function assertBusinessStatus(text: string): string {
  if (FORBIDDEN_STATUS.test(text)) {
    return '正在想怎么回你'
  }
  return text
}

function deadlineFromText(text: string): string | null {
  const iso = text.match(/\d{4}-\d{2}-\d{2}/)
  if (iso) {
    return iso[0]
  }
  if (/周五/.test(text)) {
    return '2026-09-18'
  }
  if (/下周/.test(text)) {
    return '2026-09-20'
  }
  return null
}

function maybeAttachQuestions(state: ConversationState, candidateId: string): void {
  const waiting = mockWaitingApplications(candidateId)
  if (waiting.length === 0) {
    return
  }
  const set = mockEnsureQuestionDay(candidateId, waiting)
  const already = state.messages.some((item) => item.message_type === 'questions' && item.content.includes('今日五题'))
  if (!already && set.questions.length === 5) {
    state.messages.push(
      nextMessage({
        role: 'assistant',
        message_type: 'questions',
        content: '[Mock] 今日五题，按 1 / 2 / 3 / 4 / 5 作答。',
      }),
    )
  }
}

async function* streamText(text: string): AsyncGenerator<MockSseEvent> {
  if (reduceMotion()) {
    yield { event: 'delta', data: { text } }
    return
  }
  for (const char of text) {
    yield { event: 'delta', data: { text: char } }
    await wait(10)
  }
}

type PlannedReply = {
  stage: AgentStatusEvent['stage']
  statusText: string
  messageType: MessagePublic['message_type']
  content: string
  errorCode?: string
  afterExam?: 'search_ok' | 'search_fail'
  application?: ApplicationPublic
}

function planReply(state: ConversationState, candidateId: string, text: string): PlannedReply {
  const profile = profileOf(candidateId)
  if (KEY_INVALID_RE.test(text) || profile.llm_key_status === 'invalid') {
    return {
      stage: 'reply',
      statusText: '正在想怎么回你',
      messageType: 'error_notice',
      content: KEY_INVALID_NOTICE,
      errorCode: 'LLM_KEY_INVALID',
    }
  }

  if (ANXIETY_RE.test(text)) {
    state.counselingActive = true
    state.mood = '正在听你说'
    return {
      stage: 'counseling',
      statusText: '先听你说',
      messageType: 'counseling',
      content:
        '[Mock] 我在。先不催题，也不骂醒。你现在是投完没回音，还是这五题压着你？身体和呼吸是什么感觉？',
    }
  }

  if (BETTER_RE.test(text) && state.counselingActive) {
    state.counselingActive = false
    state.mood = '心情慢慢回来了'
    const progress = mockQuestionProgress(candidateId)
    const next = mockGetQuestionSetRecord(candidateId).questions.find((item) => !item.answer)
    if (next) {
      return {
        stage: 'questions',
        statusText: '正在接回今天的题',
        messageType: 'chat',
        content: `[Mock] 好，辅导停在这里。同一轮接上题目：${next.prompt.replace('[Mock] ', '')}`,
      }
    }
    if (progress.status === 'completed') {
      return {
        stage: 'reply',
        statusText: '正在想怎么回你',
        messageType: 'chat',
        content: '[Mock] 好，辅导停在这里。今日五题已经写完，不再催促。进度我还看着。',
      }
    }
    return {
      stage: 'reply',
      statusText: '正在接回今天的提醒',
      messageType: 'nudge',
      content: '[Mock] 好，辅导停在这里。还没答完的话，我们按当前时间把待办接上：先看进度和 deadline，再写题。',
    }
  }

  if (isExperiencePaste(text)) {
    const related =
      mockWaitingApplications(candidateId)[0] ?? mockFindApplication(candidateId, () => true) ?? undefined
    mockIngestKnowledge(candidateId, {
      title: related ? `${related.company_name.replace('[Mock] ', '')} 粘贴面经` : '你粘贴的面经',
      body: text.slice(0, 800),
      source_type: 'paste',
      application_id: related?.id ?? null,
      company_name: related?.company_name.replace('[Mock] ', '') ?? '',
      role_title: related?.role_title.replace('[Mock] ', '') ?? '',
    })
    return {
      stage: 'ingest_document',
      statusText: '正在收录面经',
      messageType: 'kb_notice',
      content: '[Mock] 已收入你粘贴的面经，可在「我的面经」查看。',
    }
  }

  const url = extractUrl(text)
  if (url && FAIL_RE.test(text + url) && !KB_FAIL_RE.test(text + url)) {
    return {
      stage: 'fetch_jd',
      statusText: '正在读取岗位链接',
      messageType: 'error_notice',
      content: '[Mock] 这段岗位链接读不到。请换一个公开页，或把 JD 正文贴过来，我才能对照简历总结考查点。',
      errorCode: 'JD_FETCH_FAILED',
    }
  }

  if (url || (JOB_RE.test(text) && URL_RE.test(text))) {
    const resolvedUrl = url ?? extractUrl(text) ?? ''
    const existingByUrl = resolvedUrl
      ? mockFindApplication(candidateId, (item) => item.jd_url.trim() === resolvedUrl)
      : undefined
    const company =
      existingByUrl?.company_name.replace('[Mock] ', '') ??
      companyFromUrl(resolvedUrl) ??
      (companyFromText(text) !== '示例公司' ? companyFromText(text) : '示例公司')
    const application = mockUpsertApplication(candidateId, {
      id: existingByUrl?.id,
      company_name: company,
      role_title: existingByUrl?.role_title.replace('[Mock] ', '') ?? '产品经理',
      jd_url: resolvedUrl,
      exam_points: `[Mock] 对照你的简历项目，${company}更看重激活定义、渠道取舍，以及你有没有从 0 到 1 的证据。不是复述职位描述。`,
      progress_text: existingByUrl?.progress_text ?? '[Mock] 已投递',
      status_text: existingByUrl?.status_text || canonicalStatusFromText(text) || '',
      normalized_status:
        existingByUrl?.normalized_status ??
        normalizeApplicationStatus(canonicalStatusFromText(text) || ''),
      applied_at: existingByUrl?.applied_at,
      interview_at: existingByUrl?.interview_at ?? null,
      interview_summary: existingByUrl?.interview_summary,
    })
    const searchFail = KB_FAIL_RE.test(text + resolvedUrl)
    const statusAsk = application.status_text ? '' : `\n\n${STATUS_PROGRESS_ASK}`
    return {
      stage: 'fetch_jd',
      statusText: '正在读取岗位链接',
      messageType: 'jd_summary',
      content: `[Mock] 我读了这份 JD，对照你简历后，${application.company_name.replace('[Mock] ', '')}的考查点更集中在：激活怎么定义、渠道取舍，以及你有没有从 0 到 1 的证据。不是复述职位描述。${statusAsk}`,
      afterExam: searchFail ? 'search_fail' : 'search_ok',
      application,
    }
  }

  if (REJECTED_RE.test(text) || PROGRESS_RE.test(text)) {
    const mentioned = companyFromText(text)
    const existing =
      mockFindApplication(candidateId, (item) => text.includes(item.company_name.replace('[Mock] ', ''))) ??
      mockFindApplication(candidateId, (item) => mentioned !== '示例公司' && item.company_name.includes(mentioned)) ??
      mockWaitingApplications(candidateId)[0] ??
      mockEnsureApplications(candidateId)[0]
    const deadline = deadlineFromText(text) ?? existing?.deadline ?? null
    const nextStatus = canonicalStatusFromText(text) || (REJECTED_RE.test(text) ? '已挂' : existing?.status_text || '')
    const companyName = mentioned !== '示例公司' ? mentioned : (existing?.company_name.replace('[Mock] ', '') ?? mentioned)
    const updated = mockUpsertApplication(candidateId, {
      id: existing?.id,
      company_name: companyName,
      role_title: existing?.role_title.replace('[Mock] ', '') ?? '产品经理',
      jd_url: existing?.jd_url ?? '',
      exam_points: existing?.exam_points,
      progress_text: nextStatus === '已挂' || nextStatus === '简历挂' ? '[Mock] 流程结束' : `[Mock] ${text.slice(0, 40)}`,
      status_text: nextStatus,
      normalized_status: normalizeApplicationStatus(nextStatus),
      deadline,
    })
    const rejected = updated.normalized_status === 'rejected'
    if (rejected) {
      const related = mockListKnowledgeForApplication(candidateId, updated.id)
      if (related.length > 0 || mockHasKnowledgeForApplication(candidateId, updated.id)) {
        return {
          stage: 'update_application',
          statusText: '正在记下进度',
          messageType: 'kb_ask',
          content: kbAskContent(updated.company_name, related.map((item) => item.title)),
          application: updated,
        }
      }
      return {
        stage: 'update_application',
        statusText: '正在记下进度',
        messageType: 'chat',
        content: `[Mock] 记下了，${updated.company_name.replace('[Mock] ', '')}已挂，之后不再出题，进度里还会看到它。`,
      }
    }
    return {
      stage: 'update_application',
      statusText: '正在记下进度',
      messageType: 'chat',
      content: `[Mock] 记下了。${updated.company_name.replace('[Mock] ', '')}现在是「${updated.status_text}」，deadline ${updated.deadline ?? '未定'}。之后的提醒和出题按这次更新。`,
    }
  }

  const progress = mockQuestionProgress(candidateId)
  if (progress.total === 5 && progress.status !== 'completed' && !state.counselingActive) {
    const answered = mockAnswerNextQuestion(candidateId, text)
    if (answered.justCompleted && answered.set.review) {
      return {
        stage: 'review',
        statusText: '正在整理点评',
        messageType: 'review',
        content: answered.set.review,
      }
    }
    if (answered.question) {
      const remain = answered.set.questions.filter((item) => !item.answer).length
      return {
        stage: 'questions',
        statusText: '正在记下你的回答',
        messageType: 'chat',
        content: `[Mock] 这题我记下了。还剩 ${remain} 题，按序号继续写就行。`,
      }
    }
  }

  if (state.counselingActive) {
    return {
      stage: 'counseling',
      statusText: '先听你说',
      messageType: 'counseling',
      content: '[Mock] 我还在听。你愿意的话，继续说现在的感觉。先不催题。',
    }
  }

  return {
    stage: 'reply',
    statusText: '正在想怎么回你',
    messageType: 'chat',
    content: '[Mock] 我记下了。答题就按题号写；报进度就把公司和状态放在一句里；岗位请带上链接。',
  }
}

function allowedExperienceFile(file: File): boolean {
  const name = file.name.toLowerCase()
  return /\.(pdf|docx?|txt|md)$/.test(name)
}

export async function* mockSendMessageStream(
  token: string | null,
  conversationId: string,
  content: string,
  file?: File | null,
): AsyncGenerator<MockSseEvent | { event: 'http_error'; status: number; body: ApiEnvelope<null> }> {
  const auth = requireCandidate(token)
  if (!auth.ok) {
    yield { event: 'http_error', status: auth.response.status, body: auth.response.data }
    return
  }
  const trimmed = content.trim()
  if ((!trimmed && !file) || trimmed.length > 8000) {
    yield {
      event: 'http_error',
      status: 400,
      body: envelope(false, null, {
        error: file ? '请附上面经文件，或写 1 到 8000 字。' : '请用 1 到 8000 字跟小凹说一句。',
        error_code: 'VALIDATION_ERROR',
        message: null,
      }),
    }
    return
  }
  if (file && !allowedExperienceFile(file)) {
    yield {
      event: 'http_error',
      status: 400,
      body: envelope(false, null, {
        error: '面经文件请用 PDF、DOC、DOCX、TXT 或 MD。',
        error_code: 'VALIDATION_ERROR',
        message: null,
      }),
    }
    return
  }
  const state = ensureState(auth.candidateId)
  if (conversationId !== state.conversation.id) {
    yield {
      event: 'http_error',
      status: 403,
      body: envelope(false, null, { error: '这段对话不属于当前求职者。', error_code: 'FORBIDDEN', message: null }),
    }
    return
  }
  if (busyFlags.has(auth.candidateId)) {
    yield {
      event: 'http_error',
      status: 409,
      body: envelope(false, null, {
        error: '小凹还在回复这一句，请等它说完再发。',
        error_code: 'CONFLICT',
        message: null,
      }),
    }
    return
  }

  busyFlags.add(auth.candidateId)
  const userContent = file
    ? trimmed
      ? `${trimmed}\n[附件] ${file.name}`
      : `[附件] ${file.name}`
    : trimmed
  state.messages.push(
    nextMessage({
      role: 'user',
      message_type: 'chat',
      content: userContent,
    }),
  )

  try {
    yield {
      event: 'status',
      data: { stage: 'thinking', text: assertBusinessStatus('小凹正在思考...') },
    }
    await wait(reduceMotion() ? 0 : 160)
    if (file) {
      const related =
        mockWaitingApplications(auth.candidateId)[0] ??
        mockFindApplication(auth.candidateId, () => true) ??
        undefined
      yield {
        event: 'status',
        data: { stage: 'ingest_document', text: assertBusinessStatus('正在收录面经') },
      }
      await wait(reduceMotion() ? 0 : 280)
      mockIngestKnowledge(auth.candidateId, {
        title: file.name,
        body: `[Mock] 来自对话文件 ${file.name} 的面经摘录：渠道取舍和激活定义。`,
        source_type: 'upload',
        application_id: related?.id ?? null,
        company_name: related?.company_name.replace('[Mock] ', '') ?? '',
        role_title: related?.role_title.replace('[Mock] ', '') ?? '',
      })
      const noticeText = `[Mock] 已收入你发来的面经「${file.name}」，可在「我的面经」查看。`
      for await (const delta of streamText(noticeText)) {
        yield delta
      }
      const notice = nextMessage({
        role: 'assistant',
        message_type: 'kb_notice',
        content: noticeText,
      })
      state.messages.push(notice)
      yield {
        event: 'done',
        data: {
          message: toMessage(notice),
          snapshot: mockBuildSnapshot(auth.candidateId, state.counselingActive),
        },
      }
      return
    }

    const planned = planReply(state, auth.candidateId, trimmed)
    yield { event: 'status', data: { stage: planned.stage, text: assertBusinessStatus(planned.statusText) } }
    await wait(reduceMotion() ? 0 : 280)

    if (planned.errorCode) {
      const notice = nextMessage({
        role: 'assistant',
        message_type: 'error_notice',
        content: planned.content,
      })
      state.messages.push(notice)
      await wait(reduceMotion() ? 0 : 80)
      yield { event: 'error', data: { error: planned.content, error_code: planned.errorCode } }
      return
    }

    for await (const delta of streamText(planned.content)) {
      yield delta
    }

    const assistant = nextMessage({
      role: 'assistant',
      message_type: planned.messageType,
      content: planned.content,
    })
    state.messages.push(assistant)
    if (planned.messageType === 'jd_summary') {
      maybeAttachQuestions(state, auth.candidateId)
    }

    yield {
      event: 'done',
      data: {
        message: toMessage(assistant),
        snapshot: mockBuildSnapshot(auth.candidateId, state.counselingActive),
      },
    }

    if (planned.afterExam && planned.application) {
      yield {
        event: 'status',
        data: { stage: 'search_experiences', text: assertBusinessStatus('正在查找面经') },
      }
      await wait(reduceMotion() ? 0 : 280)
      if (planned.afterExam === 'search_fail') {
        const failText =
          '[Mock] 这次公开面经没读到。考查点已经记下了。你可以在对话里发文件或粘贴面经。'
        const failNotice = nextMessage({
          role: 'assistant',
          message_type: 'kb_notice',
          content: failText,
        })
        state.messages.push(failNotice)
        yield {
          event: 'done',
          data: {
            message: toMessage(failNotice),
            snapshot: mockBuildSnapshot(auth.candidateId, state.counselingActive),
          },
        }
      } else {
        mockEnsureSearchKnowledge(auth.candidateId, {
          id: planned.application.id,
          company_name: planned.application.company_name,
          role_title: planned.application.role_title,
          jd_url: planned.application.jd_url,
        })
        const okText = '[Mock] 已收入该岗位面经，可在「我的面经」查看。'
        const okNotice = nextMessage({
          role: 'assistant',
          message_type: 'kb_notice',
          content: okText,
        })
        state.messages.push(okNotice)
        yield {
          event: 'done',
          data: {
            message: toMessage(okNotice),
            snapshot: mockBuildSnapshot(auth.candidateId, state.counselingActive),
          },
        }
      }
    }
  } finally {
    busyFlags.delete(auth.candidateId)
  }
}

export function mockEnsureKbAsk(token: string | null, applicationId: string): MockHttp<MessagesListPublic> {
  const auth = requireCandidate(token)
  if (!auth.ok) {
    return auth.response
  }
  const state = ensureState(auth.candidateId)
  const application = mockFindApplication(auth.candidateId, (item) => item.id === applicationId)
  if (!application) {
    return {
      status: 200,
      data: envelope(true, { messages: [] }, { message: 'ok' }),
    }
  }
  const related = mockListKnowledgeForApplication(auth.candidateId, applicationId)
  if (related.length === 0) {
    return {
      status: 200,
      data: envelope(true, { messages: [] }, { message: 'ok' }),
    }
  }
  const company = application.company_name.replace('[Mock] ', '')
  const existing = state.messages.find(
    (item) => item.message_type === 'kb_ask' && item.content.includes(company),
  )
  if (existing) {
    return {
      status: 200,
      data: envelope(true, { messages: [toMessage(existing)] }, { message: 'ok' }),
    }
  }
  const ask = nextMessage({
    role: 'assistant',
    message_type: 'kb_ask',
    content: kbAskContent(application.company_name, related.map((item) => item.title)),
  })
  state.messages.push(ask)
  return {
    status: 200,
    data: envelope(true, { messages: [toMessage(ask)] }, { message: 'ok' }),
  }
}

export function mockInjectNudge(
  token: string | null,
  kind: 'morning' | 'evening' | 'email_fail',
): MockHttp<MessagesListPublic> {
  const auth = requireCandidate(token)
  if (!auth.ok) {
    return auth.response
  }
  const state = ensureState(auth.candidateId)
  const progress = mockQuestionProgress(auth.candidateId)
  if (progress.status === 'completed') {
    return {
      status: 200,
      data: envelope(true, { messages: [] }, { message: 'ok' }),
    }
  }
  if (state.counselingActive) {
    const paused = nextMessage({
      role: 'assistant',
      message_type: 'chat',
      content: '[Mock] 你还没说心情变好。催促先停着，我们还在刚才那件事上。',
    })
    state.messages.push(paused)
    return {
      status: 200,
      data: envelope(true, { messages: [toMessage(paused)] }, { message: 'ok' }),
    }
  }

  const content =
    kind === 'evening'
      ? '[Mock] 还没答完。十二点以前，把五题写完。别再跟我耗。'
      : '[Mock] 现在 10:00，今天这五题我们慢慢写，我在。'
  const nudge = nextMessage({
    role: 'assistant',
    message_type: 'nudge',
    content,
  })
  state.messages.push(nudge)
  const inserted = [toMessage(nudge)]
  if (kind === 'email_fail') {
    const notice = nextMessage({
      role: 'assistant',
      message_type: 'error_notice',
      content: '[Mock] 催促邮件没发出去：收件邮箱被拒信。对话里的提醒还在，请核对邮箱。',
    })
    state.messages.push(notice)
    inserted.push(toMessage(notice))
  }
  return {
    status: 200,
    data: envelope(true, { messages: inserted }, { message: 'ok' }),
  }
}

export function mockConversationMood(token: string | null): string {
  const candidateId = mockCandidateIdFromToken(token)
  if (!candidateId) {
    return ''
  }
  return ensureState(candidateId).mood
}

export function mockIsCounseling(token: string | null): boolean {
  const candidateId = mockCandidateIdFromToken(token)
  if (!candidateId) {
    return false
  }
  return ensureState(candidateId).counselingActive
}
