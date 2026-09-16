import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import {
  deleteKnowledgeItem,
  getKnowledgeItem,
  listKnowledgeItems,
  uploadKnowledgeItem,
  type KnowledgeItemListPublic,
  type KnowledgeItemPublic,
} from '../services/knowledge'
import { isApiError } from '../utils/apiError'

const SOURCE_LABEL: Record<KnowledgeItemListPublic['source_type'], string> = {
  search: '检索',
  upload: '上传',
  paste: '粘贴',
}

function sourceLine(item: KnowledgeItemListPublic): string {
  const source = SOURCE_LABEL[item.source_type]
  const company = item.company_name.trim()
  return company ? `${source} · ${company}` : source
}

function visibleBody(detail: KnowledgeItemPublic): string {
  const parts = detail.segments
    .filter((segment) => segment.status !== 'deleted')
    .sort((left, right) => left.ordinal - right.ordinal)
    .map((segment) => segment.text)
  return parts.length > 0 ? parts.join('\n\n') : detail.body
}

function toListItem(detail: KnowledgeItemPublic): KnowledgeItemListPublic {
  return {
    id: detail.id,
    title: detail.title,
    source_type: detail.source_type,
    source_url: detail.source_url,
    application_id: detail.application_id,
    company_name: detail.company_name,
    role_title: detail.role_title,
    created_at: detail.created_at,
    excerpt: detail.excerpt,
  }
}

export default function KnowledgePage() {
  const fileRef = useRef<HTMLInputElement>(null)
  const [items, setItems] = useState<KnowledgeItemListPublic[]>([])
  const [details, setDetails] = useState<Record<string, KnowledgeItemPublic>>({})
  const [openId, setOpenId] = useState<string | null>(null)
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [openingId, setOpeningId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    void listKnowledgeItems()
      .then((next) => {
        if (!cancelled) {
          setItems(next)
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setLoadError(isApiError(error) ? error.errorText || error.message : '面经现在读不到，请稍后再试。')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function onToggleOpen(id: string): Promise<void> {
    if (openId === id) {
      setOpenId(null)
      return
    }
    if (details[id]) {
      setOpenId(id)
      setActionError(null)
      return
    }
    setOpeningId(id)
    setActionError(null)
    try {
      const detail = await getKnowledgeItem(id)
      setDetails((current) => ({ ...current, [id]: detail }))
      setOpenId(id)
    } catch (error: unknown) {
      setActionError(isApiError(error) ? error.errorText || error.message : '正文现在读不到，请稍后再试。')
    } finally {
      setOpeningId(null)
    }
  }

  async function onConfirmDelete(id: string): Promise<void> {
    setDeletingId(id)
    setActionError(null)
    try {
      await deleteKnowledgeItem(id)
      setItems((current) => current.filter((item) => item.id !== id))
      setDetails((current) => {
        const next = { ...current }
        delete next[id]
        return next
      })
      if (openId === id) {
        setOpenId(null)
      }
      setConfirmId(null)
    } catch (error: unknown) {
      setActionError(isApiError(error) ? error.errorText || error.message : '这条没删掉，请稍后再试。')
    } finally {
      setDeletingId(null)
    }
  }

  async function onPickFile(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const file = event.target.files?.[0] ?? null
    event.target.value = ''
    if (!file || uploading) {
      return
    }
    setUploading(true)
    setActionError(null)
    try {
      const detail = await uploadKnowledgeItem(file)
      setDetails((current) => ({ ...current, [detail.id]: detail }))
      setItems((current) => {
        const listed = toListItem(detail)
        const without = current.filter((item) => item.id !== listed.id)
        return [listed, ...without]
      })
    } catch (error: unknown) {
      setActionError(isApiError(error) ? error.errorText || error.message : '这份面经没收录上，请换一份再试。')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="stage wide-stage">
      <div className="page-head">
        <div className="page-head-row">
          <h2>我的面经</h2>
          <button
            className="jy-upload"
            type="button"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
          >
            {uploading ? '正在收录…' : '上传面经'}
          </button>
          <input
            ref={fileRef}
            id="jy-file"
            type="file"
            accept=".pdf,.doc,.docx,.txt,.md"
            hidden
            onChange={(event) => {
              void onPickFile(event)
            }}
          />
        </div>
      </div>
      {loadError ? (
        <p className="form-error" role="alert">
          {loadError}
        </p>
      ) : null}
      {actionError ? (
        <p className="form-error" role="alert">
          {actionError}
        </p>
      ) : null}
      <div className="sheet">
        <div className="sheet-body">
        {loading ? (
          <p className="sheet-status" role="status">
            正在读取面经…
          </p>
        ) : items.length === 0 ? (
          <p className="jy-empty">
            还没有收录的面经。投递岗位后我会去搜公开经验；你也可以点上方「上传面经」发文件。
          </p>
        ) : (
          items.map((item) => {
            const open = openId === item.id
            const detail = details[item.id]
            return (
              <article key={item.id} className={`jy-item${open ? ' is-open' : ''}`}>
                <div className="jy-item-row">
                  <div>
                    <h3>{item.title}</h3>
                    <div className="jy-src">{sourceLine(item)}</div>
                  </div>
                  <button
                    className="linkish"
                    type="button"
                    disabled={openingId === item.id || deletingId === item.id}
                    onClick={() => {
                      void onToggleOpen(item.id)
                    }}
                  >
                    {open ? '收起' : '看正文'}
                  </button>
                  {confirmId === item.id ? (
                    <>
                      <button
                        className="linkish"
                        type="button"
                        disabled={deletingId === item.id}
                        onClick={() => {
                          void onConfirmDelete(item.id)
                        }}
                      >
                        确认删除
                      </button>
                      <button
                        className="linkish"
                        type="button"
                        disabled={deletingId === item.id}
                        onClick={() => {
                          setConfirmId(null)
                        }}
                      >
                        取消
                      </button>
                    </>
                  ) : (
                    <button
                      className="linkish"
                      type="button"
                      disabled={deletingId === item.id}
                      onClick={() => {
                        setConfirmId(item.id)
                      }}
                    >
                      删除
                    </button>
                  )}
                </div>
                {open && detail ? <p className="jy-body">{visibleBody(detail)}</p> : null}
              </article>
            )
          })
        )}
        </div>
      </div>
    </div>
  )
}
