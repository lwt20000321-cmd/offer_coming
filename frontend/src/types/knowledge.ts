export type KnowledgeSourceType = 'search' | 'upload' | 'paste'

export type KnowledgeSegmentStatus = 'active' | 'pending_delete' | 'deleted'

export type KnowledgeItemListPublic = {
  id: string
  title: string
  source_type: KnowledgeSourceType
  source_url: string | null
  application_id: string | null
  company_name: string
  role_title: string
  created_at: string
  excerpt: string
}

export type KnowledgeSegmentPublic = {
  id: string
  ordinal: number
  text: string
  status: KnowledgeSegmentStatus
}

export type KnowledgeItemPublic = KnowledgeItemListPublic & {
  body: string
  segments: KnowledgeSegmentPublic[]
}

export type KnowledgeItemsListPublic = {
  items: KnowledgeItemListPublic[]
}

export type KnowledgeItemDeletePublic = {
  id: string
  deleted: true
}
