import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import {
  createApplication,
  deleteApplication,
  listApplications,
  patchApplication,
  type ApplicationPublic,
  type ApplicationUpdate,
} from '../services/applications'
import { ensureRejectedKnowledgeAsk } from '../services/conversations'
import { APPLICATION_STATUS_LABELS } from '../constants/applicationStatus'
import { isApiError } from '../utils/apiError'

type EditableField = keyof Pick<
  ApplicationPublic,
  'company_name' | 'role_title' | 'jd_url' | 'applied_at' | 'interview_at' | 'status_text' | 'interview_summary'
>

const STATUS_OPTIONS = APPLICATION_STATUS_LABELS

function toDateInput(value: string | null): string {
  if (!value) {
    return ''
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return value
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value.slice(0, 10)
  }
  return parsed.toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
}

function withoutPending(row: ApplicationPublic): ApplicationPublic {
  const { knowledge_review_pending: _pending, ...rest } = row as ApplicationPublic & {
    knowledge_review_pending?: boolean
  }
  return rest
}

export default function ApplicationsPage() {
  const navigate = useNavigate()
  const [rows, setRows] = useState<ApplicationPublic[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const snapshots = useRef<Record<string, ApplicationPublic>>({})

  const remember = useCallback((row: ApplicationPublic) => {
    snapshots.current[row.id] = { ...row }
  }, [])

  const loadRows = useCallback((showLoading = false) => {
    if (showLoading) {
      setLoading(true)
    }
    setLoadError(null)
    return listApplications()
      .then((items) => {
        setRows(items)
        items.forEach((item) => {
          snapshots.current[item.id] = { ...item }
        })
      })
      .catch((error: unknown) => {
        setLoadError(isApiError(error) ? error.errorText || error.message : '投递表现在读不到，请稍后再试。')
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  useEffect(() => {
    void loadRows(true)
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        void loadRows()
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [loadRows])

  async function saveField(id: string, field: EditableField, raw: string): Promise<void> {
    const snap = snapshots.current[id]
    if (!snap) {
      return
    }
    const nextValue = field === 'interview_at' && raw.trim() === '' ? null : raw
    const previous = snap[field]
    if ((nextValue ?? '') === (previous ?? '')) {
      return
    }

    const patch: ApplicationUpdate = { [field]: nextValue }
    try {
      const updated = await patchApplication(id, patch)
      const next = withoutPending(updated)
      setRows((current) => current.map((row) => (row.id === id ? next : row)))
      snapshots.current[id] = { ...next }
      setSaveError(null)
      if (updated.knowledge_review_pending) {
        ensureRejectedKnowledgeAsk(id)
        navigate('/chat?kb_ask=1', { state: { knowledgeAsk: true, applicationId: id } })
      }
    } catch (error: unknown) {
      setRows((current) => current.map((row) => (row.id === id ? { ...row, [field]: previous } : row)))
      setSaveError(isApiError(error) ? error.errorText || error.message : '这一格没存上，已恢复刚才的内容。')
    }
  }

  async function onAddRow(): Promise<void> {
    setAdding(true)
    setSaveError(null)
    try {
      const created = await createApplication({})
      setRows((current) => [...current, created])
      snapshots.current[created.id] = { ...created }
    } catch (error: unknown) {
      setSaveError(isApiError(error) ? error.errorText || error.message : '新行没加上，请稍后再试。')
    } finally {
      setAdding(false)
    }
  }

  async function onDelete(id: string): Promise<void> {
    setDeletingId(id)
    setSaveError(null)
    try {
      await deleteApplication(id)
      setRows((current) => current.filter((row) => row.id !== id))
      delete snapshots.current[id]
    } catch (error: unknown) {
      setSaveError(isApiError(error) ? error.errorText || error.message : '这一行没删掉，请稍后再试。')
    } finally {
      setDeletingId(null)
    }
  }

  function extraStatus(row: ApplicationPublic): string | null {
    return row.status_text && !STATUS_OPTIONS.includes(row.status_text as (typeof STATUS_OPTIONS)[number])
      ? row.status_text
      : null
  }

  return (
    <div className="stage wide-stage">
      <div className="page-head">
        <h2>我的投递</h2>
        <p>改格子会马上记住。面试时间可以空着。投递状态含筛选、测评、一面到三面、等待面试和已挂。改成已挂或简历挂且还有面经时，会回到对话里问你删不删。</p>
      </div>
      {loadError ? (
        <p className="form-error" role="alert">
          {loadError}
        </p>
      ) : null}
      {saveError ? (
        <p className="form-error" role="alert">
          {saveError}
        </p>
      ) : null}
      <div className="sheet">
        <div className="sheet-body">
        {loading ? (
          <p className="sheet-status" role="status">
            正在读取投递表…
          </p>
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>公司</th>
                <th>岗位</th>
                <th>投递链接</th>
                <th>投递时间</th>
                <th>面试时间</th>
                <th>投递状态</th>
                <th>面试总结</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <input
                      value={row.company_name}
                      aria-label="公司"
                      onFocus={() => remember(row)}
                      onChange={(event) => {
                        const value = event.target.value
                        setRows((current) =>
                          current.map((item) => (item.id === row.id ? { ...item, company_name: value } : item)),
                        )
                      }}
                      onBlur={(event) => {
                        void saveField(row.id, 'company_name', event.target.value)
                      }}
                    />
                  </td>
                  <td>
                    <input
                      value={row.role_title}
                      aria-label="岗位"
                      onFocus={() => remember(row)}
                      onChange={(event) => {
                        const value = event.target.value
                        setRows((current) =>
                          current.map((item) => (item.id === row.id ? { ...item, role_title: value } : item)),
                        )
                      }}
                      onBlur={(event) => {
                        void saveField(row.id, 'role_title', event.target.value)
                      }}
                    />
                  </td>
                  <td>
                    <input
                      className="apps-url"
                      value={row.jd_url}
                      aria-label="投递链接"
                      onFocus={() => remember(row)}
                      onChange={(event) => {
                        const value = event.target.value
                        setRows((current) =>
                          current.map((item) => (item.id === row.id ? { ...item, jd_url: value } : item)),
                        )
                      }}
                      onBlur={(event) => {
                        void saveField(row.id, 'jd_url', event.target.value)
                      }}
                    />
                  </td>
                  <td>
                    <input
                      type="date"
                      value={toDateInput(row.applied_at)}
                      aria-label="投递时间"
                      onFocus={() => remember(row)}
                      onChange={(event) => {
                        const value = event.target.value
                        setRows((current) =>
                          current.map((item) => (item.id === row.id ? { ...item, applied_at: value } : item)),
                        )
                      }}
                      onBlur={(event) => {
                        void saveField(row.id, 'applied_at', event.target.value)
                      }}
                    />
                  </td>
                  <td>
                    <input
                      value={toDateInput(row.interview_at)}
                      aria-label="面试时间"
                      placeholder="可空"
                      onFocus={() => remember(row)}
                      onChange={(event) => {
                        const value = event.target.value
                        setRows((current) =>
                          current.map((item) => (item.id === row.id ? { ...item, interview_at: value || null } : item)),
                        )
                      }}
                      onBlur={(event) => {
                        void saveField(row.id, 'interview_at', event.target.value)
                      }}
                    />
                  </td>
                  <td>
                    <select
                      value={row.status_text}
                      aria-label="投递状态"
                      onFocus={() => remember(row)}
                      onChange={(event) => {
                        const value = event.target.value
                        setRows((current) =>
                          current.map((item) => (item.id === row.id ? { ...item, status_text: value } : item)),
                        )
                      }}
                      onBlur={(event) => {
                        void saveField(row.id, 'status_text', event.target.value)
                      }}
                    >
                      <option value=""></option>
                      {extraStatus(row) ? <option value={extraStatus(row) ?? ''}>{extraStatus(row)}</option> : null}
                      {STATUS_OPTIONS.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <textarea
                      className="apps-summary"
                      value={row.interview_summary}
                      aria-label="面试总结"
                      rows={1}
                      onFocus={() => remember(row)}
                      onChange={(event) => {
                        const value = event.target.value
                        setRows((current) =>
                          current.map((item) => (item.id === row.id ? { ...item, interview_summary: value } : item)),
                        )
                      }}
                      onBlur={(event) => {
                        void saveField(row.id, 'interview_summary', event.target.value)
                      }}
                    />
                  </td>
                  <td>
                    <button
                      className="linkish"
                      type="button"
                      disabled={deletingId === row.id}
                      onClick={() => {
                        void onDelete(row.id)
                      }}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="sheet-actions">
          <button className="ghost" type="button" disabled={adding || loading} onClick={() => void onAddRow()}>
            加一行
          </button>
        </div>
        </div>
      </div>
    </div>
  )
}
