import type { NormalizedStatus } from '../types/applications'

export const APPLICATION_STATUS_LABELS = [
  '简历筛选中',
  '简历挂',
  '待测评',
  '已测评',
  '一面',
  '二面',
  '三面',
  '等待面试',
  '已挂',
] as const

export type ApplicationStatusLabel = (typeof APPLICATION_STATUS_LABELS)[number]

export const STATUS_PROGRESS_ASK =
  '现在这条是什么进度？请选：简历筛选中、简历挂、待测评、已测评、一面、二面、三面、等待面试、已挂。'

const REJECTED_RE = /已挂|挂了|被拒|淘汰|offer 黄了|简历挂/
const WAITING_RE = /等待面试|等面试|约面|一面|二面|三面/

export function normalizeApplicationStatus(statusText: string): NormalizedStatus {
  const text = statusText.trim()
  if (REJECTED_RE.test(text)) {
    return 'rejected'
  }
  if (WAITING_RE.test(text)) {
    return 'waiting_interview'
  }
  return 'other'
}

export function canonicalStatusFromText(text: string): string | null {
  const blob = text || ''
  const rules: Array<[string, string[]]> = [
    ['简历筛选中', ['简历筛选中', '筛选中', '初筛中', '简历初筛']],
    ['简历挂', ['简历挂']],
    ['待测评', ['待测评', '等测评']],
    ['已测评', ['已测评', '测完了', '测完']],
    ['三面', ['三面']],
    ['二面', ['二面']],
    ['一面', ['一面']],
    ['等待面试', ['等待面试', '等面试', '约面']],
    ['已挂', ['已挂', '挂了', '被拒', '淘汰', 'offer 黄了']],
  ]
  for (const [label, aliases] of rules) {
    if (aliases.some((alias) => blob.includes(alias))) {
      return label
    }
  }
  return null
}
