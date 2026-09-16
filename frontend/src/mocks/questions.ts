import type { ApiEnvelope } from '../types/session'
import { mockWaitingApplications, type ApplicationPublic } from './applications'

export type QuestionPublic = {
  id: string
  kind: 'common' | 'role'
  prompt: string
  answer: string | null
  target_application_id: string | null
}

export type QuestionSetPublic = {
  id: string
  beijing_date: string
  status: 'none' | 'pending' | 'in_progress' | 'completed' | 'voided' | 'rest_day'
  questions: QuestionPublic[]
  review: string | null
  voided_at: string | null
}

type MockHttp<T> = {
  status: number
  data: ApiEnvelope<T>
}

const KNOWN_ID = 'c_mock_known'
const BEIJING_DATE = '2026-09-13'
const store = new Map<string, QuestionSetPublic>()
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
    request_id: `req_mock_qs_${String(requestSeq).padStart(3, '0')}`,
    metadata: {},
  }
}

function fail<T>(status: number, error: string, errorCode: string): MockHttp<T> {
  return {
    status,
    data: envelope<T>(false, null, { error, error_code: errorCode, message: null }),
  }
}

function toQuestion(item: QuestionPublic): QuestionPublic {
  return {
    id: item.id,
    kind: item.kind,
    prompt: item.prompt,
    answer: item.answer,
    target_application_id: item.target_application_id,
  }
}

function toPublic(item: QuestionSetPublic): QuestionSetPublic {
  return {
    id: item.id,
    beijing_date: item.beijing_date,
    status: item.status,
    questions: item.questions.map(toQuestion),
    review: item.review,
    voided_at: item.voided_at,
  }
}

function emptyShell(candidateId: string): QuestionSetPublic {
  return {
    id: `qs_none_${candidateId}`,
    beijing_date: BEIJING_DATE,
    status: 'none',
    questions: [],
    review: null,
    voided_at: null,
  }
}

function seedKnown(): void {
  store.set(KNOWN_ID, {
    id: 'qs_mock_known',
    beijing_date: BEIJING_DATE,
    status: 'in_progress',
    questions: [
      {
        id: 'q_mock_1',
        kind: 'common',
        prompt: '[Mock] 你如何证明，转化率提升 18% 是你的改动带来的？',
        answer: null,
        target_application_id: null,
      },
      {
        id: 'q_mock_2',
        kind: 'common',
        prompt: '[Mock] 再选一段不同的简历经历，说明其中的冲突和你如何权衡。',
        answer: null,
        target_application_id: null,
      },
      {
        id: 'q_mock_3',
        kind: 'common',
        prompt: '[Mock] 用一次协作受阻的经历，讲清你怎么沟通、怎么收口。',
        answer: null,
        target_application_id: null,
      },
      {
        id: 'q_mock_4',
        kind: 'role',
        prompt: '[Mock] 用户注册后没有使用核心功能，你会先排查什么？',
        answer: null,
        target_application_id: 'a_mock_star',
      },
      {
        id: 'q_mock_5',
        kind: 'role',
        prompt: '[Mock] 预算减半，你会如何重新分配拉新渠道？',
        answer: null,
        target_application_id: 'a_mock_star',
      },
    ],
    review: null,
    voided_at: null,
  })
}

seedKnown()

export function mockGetQuestionSetRecord(candidateId: string): QuestionSetPublic {
  return store.get(candidateId) ?? emptyShell(candidateId)
}

export function mockEnsureQuestionDay(candidateId: string, waiting: ApplicationPublic[]): QuestionSetPublic {
  const current = store.get(candidateId)
  if (current && current.status !== 'none' && current.questions.length === 5) {
    return current
  }
  const target = waiting[0] ?? null
  const company = target?.company_name.replace('[Mock] ', '') ?? '已记录岗位'
  const created: QuestionSetPublic = {
    id: `qs_mock_${candidateId}`,
    beijing_date: BEIJING_DATE,
    status: 'in_progress',
    questions: [
      {
        id: `q_${candidateId}_1`,
        kind: 'common',
        prompt: '[Mock] 请把简历里某段项目经历讲深一层：你做了什么、难点是什么、结果怎么证明？',
        answer: null,
        target_application_id: null,
      },
      {
        id: `q_${candidateId}_2`,
        kind: 'common',
        prompt: '[Mock] 再选一段不同的简历经历，说明其中的冲突、你如何权衡，以及学到了什么。',
        answer: null,
        target_application_id: null,
      },
      {
        id: `q_${candidateId}_3`,
        kind: 'common',
        prompt: '[Mock] 用简历里一次协作或推进受阻的经历，讲清你怎么沟通、怎么收口。',
        answer: null,
        target_application_id: null,
      },
      {
        id: `q_${candidateId}_4`,
        kind: 'role',
        prompt: `[Mock] 结合${company}的业务，用户没有走到核心功能时，你会先排查什么？`,
        answer: null,
        target_application_id: target?.id ?? null,
      },
      {
        id: `q_${candidateId}_5`,
        kind: 'role',
        prompt: `[Mock] 若${company}把预算减半，你会如何取舍渠道并仍拿到面试要的证据？`,
        answer: null,
        target_application_id: target?.id ?? null,
      },
    ],
    review: null,
    voided_at: null,
  }
  store.set(candidateId, created)
  return created
}

export function mockAnswerNextQuestion(candidateId: string, text: string): {
  question: QuestionPublic | null
  set: QuestionSetPublic
  justCompleted: boolean
} {
  const set = mockGetQuestionSetRecord(candidateId)
  const unanswered = set.questions.find((item) => !item.answer)
  if (!unanswered || set.status === 'completed' || set.status === 'none') {
    return { question: null, set, justCompleted: false }
  }
  unanswered.answer = text
  const remaining = set.questions.some((item) => !item.answer)
  let justCompleted = false
  if (!remaining) {
    set.status = 'completed'
    set.review =
      '[Mock] 五题都写完了。面试时容易把结果讲成感觉，而不是证据链。建议下次先说指标、再讲你改了什么、最后补对照。今日不再催促。'
    justCompleted = true
  } else {
    set.status = 'in_progress'
  }
  return { question: toQuestion(unanswered), set: toPublic(set), justCompleted }
}

export function mockQuestionProgress(candidateId: string): { answered: number; total: number; status: string } {
  const set = mockGetQuestionSetRecord(candidateId)
  const total = set.questions.length
  const answered = set.questions.filter((item) => Boolean(item.answer)).length
  return { answered, total, status: set.status }
}

export function mockGetQuestionSet(tokenOk: boolean, candidateId: string | null): MockHttp<QuestionSetPublic> {
  if (!tokenOk || !candidateId) {
    return fail(401, '接续已失效，请用邮箱重新进入。', 'UNAUTHORIZED')
  }
  const waiting = mockWaitingApplications(candidateId)
  const record = mockGetQuestionSetRecord(candidateId)
  const dto =
    record.status === 'none' && waiting.length === 0
      ? toPublic(emptyShell(candidateId))
      : toPublic(record)
  return {
    status: 200,
    data: envelope(true, dto, { message: 'ok' }),
  }
}
