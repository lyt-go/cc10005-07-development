import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/sitemap'
const columns = [
  "站点编号",
  "站点域名",
  "robots规则",
  "sitemap地址",
  "页面模板路径",
  "翻页参数名",
  "站点字符编码",
  "最近探测时间"
]
const actions = [
  "发起探测",
  "确认识别",
  "标记屏蔽"
]
const statuses = [
  "草稿",
  "探测中",
  "已识别",
  "已屏蔽"
]
const stats = [
  {
    "label": "站点描述总数",
    "value": 0
  },
  {
    "label": "已识别站点",
    "value": 0
  },
  {
    "label": "探测中站点",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

export default function SitemapView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [editing, setEditing] = useState<Row | null>(null)
  const [editValues, setEditValues] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('站点描述列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '站点地图列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  function startEdit(row: Row) {
    setEditing(row)
    setEditValues({
      robots规则: String(row['robots规则'] ?? ''),
      sitemap地址: String(row['sitemap地址'] ?? ''),
      页面模板路径: String(row['页面模板路径'] ?? ''),
      翻页参数名: String(row['翻页参数名'] ?? ''),
    })
  }

  async function saveEdit() {
    if (!editing) return
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${editing.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ values: editValues }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '站点描述更新失败')
      }
      setErrorMessage('')
      setNotice(payload.message)
      setEditing(null)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '站点描述更新失败')
    }
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('站点地图动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '站点地图操作失败')
    }
  }

  return (
    <section className="page" data-module="sitemap">
      <header className="page-head">
        <div>
          <h2>站点地图管理</h2>
          <p className="page-desc">维护目标站点的结构描述信息，记录robots规则、站点地图和页面模板路径。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('站点描述登记入口尚未接入审批流')}>
            登记站点描述
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出站点地图清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                <button className="link" type="button" onClick={() => startEdit(row)}>更新规则</button>
                <Link className="link" to={`/boundarypreview?site=${row.id}`}>边界预演</Link>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无站点地图数据，可先登记站点描述</td>
            </tr>
          )}
        </tbody>
      </table>

      {editing && (
        <article className="panel">
          <h3 className="panel-title">更新站点规则：{String(editing['站点编号'])}</h3>
          <p className="page-desc">robots、sitemap、模板路径或翻页参数一旦变化，该站点的旧边界预演会转为历史，使用中的检查点仍按原判定指纹可追溯。</p>
          <div className="filter-bar">
            {['robots规则', 'sitemap地址', '页面模板路径', '翻页参数名'].map((field) => (
              <label key={field} className="filter-item">
                <span>{field}</span>
                <input
                  value={editValues[field] ?? ''}
                  onChange={(event) => setEditValues((prev) => ({ ...prev, [field]: event.target.value }))}
                />
              </label>
            ))}
            <button className="btn primary" type="button" onClick={() => void saveEdit()}>保存规则</button>
            <button className="btn ghost" type="button" onClick={() => setEditing(null)}>取消</button>
          </div>
        </article>
      )}

      <footer className="page-foot">
        <span>共 {total} 条站点地图记录</span>
        {notice && <span className="ok-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
