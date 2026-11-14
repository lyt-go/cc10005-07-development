import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { fetchJson, request } from '@/api/client'

const ENDPOINT = '/api/boundary-preview'
const statusOptions = ['', '待启用', '已启用', '历史']
const eligibilityOptions = ['', '可启用', '不可启用']
const sortOptions = ['排序序号', '创建时间', '实际可达深度', '最大深度', '允许路径数', '排除路径数', '超深页数', '站点编号', '配置编号']

type Option = {
  id: number
  配置编号?: string
  站点编号?: string
  站点域名?: string
  种子URL?: string
  最大深度?: string
  同域约束?: string
  路径包含规则?: string
  路径排除规则?: string
  robots规则?: string
  sitemap地址?: string
  页面模板路径?: string
  status?: string
  规则版本?: number
  有一个有效预演?: boolean
}

type Options = {
  configs: Option[]
  sites: Option[]
  occupancy: Record<string, unknown>[]
  rule_priority: string[]
  statuses: string[]
  sort_fields: string[]
}

type RuleHit = { 规则来源: string; 结果: string; 说明: string; 命中规则?: string | null }
type PathItem = {
  url: string
  路径: string
  深度: number
  深度路径: string[]
  来源页: string | null
  来源类型: string
  站点内跳转: boolean
  判定: string
  排除来源: string | null
  排除规则: string | null
  排除说明: string | null
  规则命中: RuleHit[]
  robots命中: Record<string, unknown>
  超深: boolean
  超深说明?: string
}
type Boundary = {
  判定方式: string
  规则优先级: string[]
  规则优先级说明: string
  最大深度: number
  实际可达深度: number | null
  允许进入的路径: PathItem[]
  被排除路径: PathItem[]
  超深路径: PathItem[]
  路径排除来源: Record<string, number>
  robots规则命中: PathItem[]
  深度边界判定: Record<string, unknown>
  遍历统计: Record<string, number>
}
type Preview = {
  id: number
  预演编号: string
  status: string
  配置编号: string
  站点编号: string
  站点域名: string
  最大深度: number
  实际可达深度: number | null
  排序序号: number
  路径排除规则: string | null
  规则版本: Record<string, number>
  判定指纹: string
  站点识别结果: Record<string, string>
  爬取资格: string
  资格判定: { 阻断项: { 原因: string; 来源: string }[]; 提示项: { 原因: string; 来源: string }[]; 说明: string }
  边界判定: Boundary
  路径排除来源: Record<string, number>
  站点占用: { 占用编号?: string; 站点域名?: string; 预演次数?: number }
  检查点编号: string | null
  检查点id: number | null
  历史原因: string | null
  创建时间: string
  检查点追溯?: Record<string, string> | null
  检查点?: Record<string, unknown> | null
  站点占用记录?: Record<string, unknown> | null
}

type Page = { items: Preview[]; total: number; sort?: Record<string, string> }

function value(row: Record<string, unknown>, key: string): string {
  const item = row?.[key]
  return item === null || item === undefined || item === '' ? '—' : String(item)
}

export default function BoundaryPreviewView() {
  const [searchParams] = useSearchParams()
  const [options, setOptions] = useState<Options | null>(null)
  const [configId, setConfigId] = useState(searchParams.get('config') ?? '')
  const [siteId, setSiteId] = useState(searchParams.get('site') ?? '')
  const [active, setActive] = useState<Preview | null>(null)
  const [rows, setRows] = useState<Preview[]>([])
  const [total, setTotal] = useState(0)
  const [sortMeta, setSortMeta] = useState<Record<string, string>>({})
  const [message, setMessage] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [checkpoints, setCheckpoints] = useState<Record<string, unknown>[]>([])
  const [filters, setFilters] = useState({
    config_keyword: '',
    site_keyword: '',
    exclude_rule: '',
    status: '',
    eligibility: '',
    fingerprint: '',
  })
  const [sortBy, setSortBy] = useState('排序序号')
  const [order, setOrder] = useState('asc')

  const loadOptions = useCallback(async () => {
    try {
      setOptions(await fetchJson<Options>(`${ENDPOINT}/options`))
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '预演选项读取失败')
    }
  }, [])

  const loadCheckpoints = useCallback(async () => {
    try {
      const payload = await fetchJson<{ items: Record<string, unknown>[] }>(`${ENDPOINT}/checkpoints`)
      setCheckpoints(payload.items ?? [])
    } catch {
      setCheckpoints([])
    }
  }, [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    Object.entries(filters).forEach(([key, val]) => {
      if (val) query.set(key, val)
    })
    query.set('sort_by', sortBy)
    query.set('order', order)
    try {
      const payload = await fetchJson<Page>(`${ENDPOINT}/previews?${query.toString()}`)
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      setSortMeta(payload.sort ?? {})
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '预演记录读取失败')
    }
  }, [filters, sortBy, order])

  useEffect(() => {
    void loadOptions()
    void loadCheckpoints()
  }, [loadOptions, loadCheckpoints])

  useEffect(() => {
    void reload()
  }, [reload])

  const selectedConfig = useMemo(
    () => options?.configs.find((item) => String(item.id) === configId),
    [options, configId],
  )
  const selectedSite = useMemo(
    () => options?.sites.find((item) => String(item.id) === siteId),
    [options, siteId],
  )

  async function runPreview() {
    setMessage('')
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/previews`, {
        method: 'POST',
        body: JSON.stringify({ values: { config_id: Number(configId), site_id: Number(siteId) } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '边界预演未完成')
      }
      setActive(payload.entry)
      setMessage(payload.message)
      await reload()
      await loadOptions()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '边界预演失败')
    }
  }

  async function confirmPreview(target: Preview) {
    setMessage('')
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/previews/${target.id}/confirm`, {
        method: 'POST',
        body: JSON.stringify({ values: { operator: '值班管理员' } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '预演确认失败')
      }
      setMessage(payload.message)
      setActive(payload.entry)
      await reload()
      await loadCheckpoints()
      await loadOptions()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '预演确认失败')
    }
  }

  async function openDetail(row: Preview) {
    setErrorMessage('')
    try {
      setActive(await fetchJson<Preview>(`${ENDPOINT}/previews/${row.id}`))
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '预演明细读取失败')
    }
  }

  return (
    <section className="page" data-module="boundarypreview">
      <header className="page-head">
        <div>
          <h2>边界预演</h2>
          <p className="page-desc">
            选择爬虫配置与站点描述，先看清允许路径、排除路径、可达深度与爬取资格，确认后再启用。
            预演只基于站点描述的离线链接图，不产生任何抓取任务。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出预演清单
          </button>
        </div>
      </header>

      <article className="panel">
        <h3 className="panel-title">选择预演对象</h3>
        <div className="filter-bar">
          <label className="filter-item">
            <span>爬虫配置</span>
            <select value={configId} onChange={(event) => setConfigId(event.target.value)}>
              <option value="">请选择爬虫配置</option>
              {options?.configs.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.配置编号}（{item.种子URL}，最大深度 {item.最大深度}）
                </option>
              ))}
            </select>
          </label>
          <label className="filter-item">
            <span>站点描述</span>
            <select value={siteId} onChange={(event) => setSiteId(event.target.value)}>
              <option value="">请选择站点描述</option>
              {options?.sites.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.站点编号}（{item.站点域名}，{item.status}
                  {item.有一个有效预演 ? '，已有有效预演' : ''}）
                </option>
              ))}
            </select>
          </label>
          <button className="btn primary" type="button" disabled={!configId || !siteId} onClick={() => void runPreview()}>
            执行边界预演
          </button>
          <button
            className="btn"
            type="button"
            disabled={!active || active.status !== '待启用' || active.爬取资格 !== '可启用'}
            onClick={() => active && void confirmPreview(active)}
          >
            确认并启用
          </button>
        </div>
        {(selectedConfig || selectedSite) && (
          <dl className="kv-grid">
            {selectedConfig && (
              <>
                <dt>种子URL</dt><dd>{selectedConfig.种子URL}</dd>
                <dt>最大深度</dt><dd>{selectedConfig.最大深度}</dd>
                <dt>同域约束</dt><dd>{selectedConfig.同域约束}</dd>
                <dt>路径包含规则</dt><dd>{selectedConfig.路径包含规则 || '—'}</dd>
                <dt>路径排除规则</dt><dd>{selectedConfig.路径排除规则 || '—'}</dd>
              </>
            )}
            {selectedSite && (
              <>
                <dt>站点域名</dt><dd>{selectedSite.站点域名}</dd>
                <dt>robots规则</dt><dd className="pre">{selectedSite.robots规则}</dd>
                <dt>sitemap地址</dt><dd>{selectedSite.sitemap地址}</dd>
                <dt>页面模板路径</dt><dd>{selectedSite.页面模板路径}</dd>
              </>
            )}
          </dl>
        )}
        {message && <p className="ok-text">{message}</p>}
        {errorMessage && <p className="error-text">{errorMessage}</p>}
      </article>

      {active && <PreviewDetail preview={active} onConfirm={confirmPreview} />}

      <article className="panel">
        <h3 className="panel-title">预演台账</h3>
        <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
          <label className="filter-item">
            <span>爬虫配置</span>
            <input
              value={filters.config_keyword}
              placeholder="按配置编号筛选"
              onChange={(event) => setFilters((prev) => ({ ...prev, config_keyword: event.target.value }))}
            />
          </label>
          <label className="filter-item">
            <span>站点描述</span>
            <input
              value={filters.site_keyword}
              placeholder="按站点编号筛选"
              onChange={(event) => setFilters((prev) => ({ ...prev, site_keyword: event.target.value }))}
            />
          </label>
          <label className="filter-item">
            <span>路径排除规则</span>
            <input
              value={filters.exclude_rule}
              placeholder="如 /news/tag/"
              onChange={(event) => setFilters((prev) => ({ ...prev, exclude_rule: event.target.value }))}
            />
          </label>
          <label className="filter-item">
            <span>预演状态</span>
            <select value={filters.status} onChange={(event) => setFilters((prev) => ({ ...prev, status: event.target.value }))}>
              {statusOptions.map((item) => <option key={item || '全部'} value={item}>{item || '全部'}</option>)}
            </select>
          </label>
          <label className="filter-item">
            <span>爬取资格</span>
            <select value={filters.eligibility} onChange={(event) => setFilters((prev) => ({ ...prev, eligibility: event.target.value }))}>
              {eligibilityOptions.map((item) => <option key={item || '全部'} value={item}>{item || '全部'}</option>)}
            </select>
          </label>
          <label className="filter-item">
            <span>判定指纹</span>
            <input
              value={filters.fingerprint}
              placeholder="BND-XXXXXXXXXXXX"
              onChange={(event) => setFilters((prev) => ({ ...prev, fingerprint: event.target.value }))}
            />
          </label>
          <label className="filter-item">
            <span>排序字段</span>
            <select value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
              {sortOptions.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label className="filter-item">
            <span>排列顺序</span>
            <select value={order} onChange={(event) => setOrder(event.target.value)}>
              <option value="asc">升序</option>
              <option value="desc">降序</option>
            </select>
          </label>
          <button className="btn" type="submit">查询</button>
          <button
            className="btn ghost"
            type="button"
            onClick={() => setFilters({ config_keyword: '', site_keyword: '', exclude_rule: '', status: '', eligibility: '', fingerprint: '' })}
          >
            重置条件
          </button>
        </form>

        <table className="data-table">
          <thead>
            <tr>
              <th>排序序号</th>
              <th>预演编号</th>
              <th>爬虫配置</th>
              <th>站点描述</th>
              <th>最大深度</th>
              <th>实际可达深度</th>
              <th>路径排除规则</th>
              <th>爬取资格</th>
              <th>预演状态</th>
              <th>判定指纹</th>
              <th>检查点</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.排序序号}</td>
                <td>{row.预演编号}</td>
                <td>{row.配置编号}</td>
                <td>{row.站点编号}（{row.站点域名}）</td>
                <td>{row.最大深度}</td>
                <td>{row.实际可达深度 ?? '—'}</td>
                <td>{row.路径排除规则 || '—'}</td>
                <td><span className={`badge ${row.爬取资格 === '可启用' ? 'ok' : 'warn'}`}>{row.爬取资格}</span></td>
                <td><span className={`badge ${row.status === '历史' ? 'muted' : row.status === '已启用' ? 'ok' : ''}`}>{row.status}</span></td>
                <td className="mono">{row.判定指纹}</td>
                <td>{row.检查点编号 ?? '—'}</td>
                <td className="row-actions">
                  <button className="link" type="button" onClick={() => void openDetail(row)}>查看明细</button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={12}>暂无边界预演记录，请先选择爬虫配置与站点描述执行预演</td>
              </tr>
            )}
          </tbody>
        </table>

        <footer className="page-foot">
          <span>
            共 {total} 条预演记录；排序字段 {sortMeta.排序字段 ?? sortBy}，方向 {sortMeta.排序方向 ?? order}
            {sortMeta.稳定兜底 ? `，兜底顺序 ${sortMeta.稳定兜底}` : ''}
          </span>
        </footer>
      </article>

      <article className="panel">
        <h3 className="panel-title">检查点台账</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>检查点编号</th>
              <th>预演编号</th>
              <th>配置编号</th>
              <th>站点编号</th>
              <th>判定指纹</th>
              <th>实际可达深度</th>
              <th>入队路径数</th>
              <th>规则已更新</th>
              <th>创建时间</th>
            </tr>
          </thead>
          <tbody>
            {checkpoints.map((row) => (
              <tr key={String(row.id)}>
                <td>{value(row, '检查点编号')}</td>
                <td>{value(row, '预演编号')}</td>
                <td>{value(row, '配置编号')}</td>
                <td>{value(row, '站点编号')}</td>
                <td className="mono">{value(row, '判定指纹')}</td>
                <td>{value(row, '实际可达深度')}</td>
                <td>{Array.isArray(row.入队路径) ? row.入队路径.length : '—'}</td>
                <td>{row.规则已更新 ? '是（仍按原指纹可追溯）' : '否'}</td>
                <td>{value(row, '创建时间')}</td>
              </tr>
            ))}
            {checkpoints.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={9}>尚无检查点，确认预演后才会建立</td>
              </tr>
            )}
          </tbody>
        </table>
      </article>
    </section>
  )
}

function PreviewDetail({ preview, onConfirm }: { preview: Preview; onConfirm: (row: Preview) => void }) {
  const boundary = preview.边界判定
  const depth = boundary.深度边界判定
  return (
    <article className="panel">
      <div className="panel-head">
        <h3 className="panel-title">预演结果 {preview.预演编号}</h3>
        <div className="row-actions">
          <span className={`badge ${preview.爬取资格 === '可启用' ? 'ok' : 'warn'}`}>爬取资格：{preview.爬取资格}</span>
          <span className={`badge ${preview.status === '历史' ? 'muted' : preview.status === '已启用' ? 'ok' : ''}`}>{preview.status}</span>
          {preview.检查点编号 && <span className="badge">检查点 {preview.检查点编号}</span>}
          {preview.status === '待启用' && preview.爬取资格 === '可启用' && (
            <button className="btn primary" type="button" onClick={() => onConfirm(preview)}>确认并启用</button>
          )}
        </div>
      </div>

      <dl className="kv-grid">
        <dt>站点识别结果</dt>
        <dd>
          {preview.站点识别结果?.结果}：{preview.站点识别结果?.说明}
        </dd>
        <dt>最大深度 / 实际可达深度</dt>
        <dd>{preview.最大深度} / {preview.实际可达深度 ?? '—'}</dd>
        <dt>深度边界结论</dt>
        <dd>{String(depth.结论 ?? '—')}：{String(depth.说明 ?? '')}</dd>
        <dt>判定指纹</dt>
        <dd className="mono">{preview.判定指纹}</dd>
        <dt>判定方式</dt>
        <dd>{boundary.判定方式}</dd>
        <dt>规则优先级</dt>
        <dd>{boundary.规则优先级.join(' > ')}（{boundary.规则优先级说明}）</dd>
        <dt>规则版本</dt>
        <dd>站点 {preview.规则版本?.站点}，爬虫配置 {preview.规则版本?.爬虫配置}</dd>
        <dt>站点占用</dt>
        <dd>
          {preview.站点占用?.占用编号 ?? '—'}（{preview.站点占用?.站点域名 ?? '—'}，第 {preview.站点占用?.预演次数 ?? 0} 次预演）
        </dd>
        {preview.历史原因 && (
          <>
            <dt>历史原因</dt>
            <dd>{preview.历史原因}</dd>
          </>
        )}
        {preview.检查点追溯 && (
          <>
            <dt>检查点追溯</dt>
            <dd>{preview.检查点追溯.说明}（{preview.检查点追溯.判定指纹}）</dd>
          </>
        )}
      </dl>

      {preview.资格判定?.阻断项?.length > 0 && (
        <ul className="notice-list warn">
          {preview.资格判定.阻断项.map((item) => (
            <li key={`${item.来源}-${item.原因}`}>阻断 · {item.来源}：{item.原因}</li>
          ))}
        </ul>
      )}
      {preview.资格判定?.提示项?.length > 0 && (
        <ul className="notice-list">
          {preview.资格判定.提示项.map((item) => (
            <li key={`${item.来源}-${item.原因}`}>提示 · {item.来源}：{item.原因}</li>
          ))}
        </ul>
      )}

      <h4 className="sub-title">允许进入的路径（{boundary.允许进入的路径.length}）</h4>
      <table className="data-table">
        <thead>
          <tr><th>路径</th><th>深度</th><th>来源页</th><th>robots</th><th>深度路径</th></tr>
        </thead>
        <tbody>
          {boundary.允许进入的路径.map((item) => (
            <tr key={item.url}>
              <td>{item.url}</td>
              <td>{item.深度}</td>
              <td>{item.来源页 ?? '种子URL'}</td>
              <td>{item.robots命中?.规则 ? `${item.robots命中.结果}：${item.robots命中.规则}` : '默认允许'}</td>
              <td className="mono small">{item.深度路径.join(' → ')}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4 className="sub-title">被排除路径（{boundary.被排除路径.length}）</h4>
      <table className="data-table">
        <thead>
          <tr><th>路径</th><th>深度</th><th>来源页</th><th>排除来源</th><th>命中规则</th><th>说明</th></tr>
        </thead>
        <tbody>
          {boundary.被排除路径.map((item) => (
            <tr key={item.url}>
              <td>{item.url}</td>
              <td>{item.深度}</td>
              <td>{item.来源页 ?? '种子URL'}{item.站点内跳转 ? '（站点内跳转）' : ''}</td>
              <td><span className="badge warn">{item.排除来源}</span></td>
              <td>{item.排除规则 ?? '—'}</td>
              <td>{item.排除说明}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4 className="sub-title">超深路径（{boundary.超深路径.length}）</h4>
      <table className="data-table">
        <thead>
          <tr><th>页面</th><th>深度</th><th>来源页</th><th>深度路径</th><th>说明</th></tr>
        </thead>
        <tbody>
          {boundary.超深路径.map((item) => (
            <tr key={item.url}>
              <td>{item.url}</td>
              <td>{item.深度}</td>
              <td>{item.来源页 ?? '—'}</td>
              <td className="mono small">{item.深度路径.join(' → ')}</td>
              <td>{item.超深说明}</td>
            </tr>
          ))}
          {boundary.超深路径.length === 0 && (
            <tr><td className="empty-state" colSpan={5}>没有发现越过深度边界的页面</td></tr>
          )}
        </tbody>
      </table>

      <h4 className="sub-title">robots 规则命中（{boundary.robots规则命中.length}）</h4>
      <table className="data-table">
        <thead>
          <tr><th>URL</th><th>深度</th><th>结果</th><th>命中规则</th><th>分组</th></tr>
        </thead>
        <tbody>
          {boundary.robots规则命中.map((item) => (
            <tr key={item.url}>
              <td>{item.url}</td>
              <td>{item.深度}</td>
              <td>{String(item.robots命中?.结果)}</td>
              <td>{String(item.robots命中?.规则 ?? '—')}</td>
              <td>{String(item.robots命中?.分组 ?? '—')}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>
          遍历统计：判定节点 {boundary.遍历统计?.判定节点数} 个，判定边 {boundary.遍历统计?.判定边数} 条；
          路径排除来源 {Object.entries(boundary.路径排除来源).map(([key, count]) => `${key} ${count}`).join('，') || '无'}
        </span>
      </footer>
    </article>
  )
}
