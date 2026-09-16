export type NormalizedStatus = 'waiting_interview' | 'rejected' | 'other'

export type ApplicationPublic = {
  id: string
  company_name: string
  role_title: string
  jd_url: string
  exam_points: string
  progress_text: string
  status_text: string
  normalized_status: NormalizedStatus
  applied_at: string
  interview_at: string | null
  interview_summary: string
  deadline: string | null
  updated_at: string
}

export type ApplicationPatchPublic = ApplicationPublic & {
  knowledge_review_pending: boolean
}

export type ApplicationsListPublic = {
  applications: ApplicationPublic[]
}

export type ApplicationCreate = {
  company_name?: string
  role_title?: string
  jd_url?: string
  applied_at?: string
  interview_at?: string | null
  status_text?: string
  interview_summary?: string
  progress_text?: string
}

export type ApplicationUpdate = {
  company_name?: string
  role_title?: string
  jd_url?: string
  applied_at?: string
  interview_at?: string | null
  status_text?: string
  interview_summary?: string
  progress_text?: string
}

export type ApplicationDeletePublic = {
  id: string
  deleted: true
}
