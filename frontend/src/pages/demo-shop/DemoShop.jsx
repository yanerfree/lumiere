import { useState, useEffect, useMemo, useRef } from 'react'
import {
  Button, Space, Input, Tag, Badge, Table, message, Tooltip, Drawer, Radio,
  Popconfirm, Empty, Alert, Typography,
} from 'antd'
import {
  PlayCircleOutlined, PauseCircleOutlined, ReloadOutlined, CopyOutlined,
  ShoppingCartOutlined, QuestionCircleOutlined, ClearOutlined, ExportOutlined,
  SendOutlined, LockFilled, EditOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'

const MONO = 'var(--font-mono)'

const METHOD_COLOR = (m) => ({
  GET: '#0ea5a0', POST: '#ff7d00', PUT: '#4e8af0',
  PATCH: '#7c5cbf', DELETE: '#e8453c',
}[m] || '#4e5969')

const CODE_COLOR = (c) => (c >= 500 ? '#e8453c' : c >= 400 ? '#ff7d00' : c >= 300 ? '#7c5cbf' : '#0ea5a0')

// 谁能调这条接口。标错了页面上「用哪个账号」会给错默认值，而请求照发不误，
// 于是看起来像「接口权限判错了」，其实是这一栏标错了。
const AUTH_META = {
  none: { label: '不用登录', color: 'default' },
  user: { label: '要登录', color: 'blue' },
  admin: { label: '仅管理员', color: 'orange' },
}

// 发请求时用谁的身份。前两个会真去 /api/login 换 token，后两个是故意用来看 401 的。
const IDENTITIES = [
  { key: 'admin', label: '管理员' },
  { key: 'clerk', label: '店员' },
  { key: 'none', label: '不带 token' },
  { key: 'bad', label: '乱填 token' },
]

const pretty = (text) => {
  try { return JSON.stringify(JSON.parse(text), null, 2) } catch { return text }
}

// 日志里的真实路径能不能算作这条接口。/api/orders/{order_no} 要吃得下
// /api/orders/SO202609240001，否则「只看这条接口」永远是空的 —— 而空列表看着像没调过。
const pathMatches = (pattern, actual) => {
  const re = new RegExp('^' + pattern.replace(/[.*+?^${}()|[\]\\]/g, m => (m === '{' || m === '}' ? m : '\\' + m))
    .replace(/\{\w+\}/g, '[^/]+') + '$')
  return re.test((actual || '').split('?')[0])
}

export default function DemoShop() {
  const [status, setStatus] = useState({ running: false, port: 29000, stats: null, accounts: [] })
  const [endpoints, setEndpoints] = useState([])
  const [groupOrder, setGroupOrder] = useState([])
  const [rules, setRules] = useState(null)
  const [selKey, setSelKey] = useState(null)
  const [tab, setTab] = useState('spec')
  const [logs, setLogs] = useState([])
  const [onlyThis, setOnlyThis] = useState(true)
  const [helpOpen, setHelpOpen] = useState(false)

  // 测试面板
  const [identity, setIdentity] = useState('admin')
  const [pathVals, setPathVals] = useState({})
  const [queryVals, setQueryVals] = useState({})
  const [bodyText, setBodyText] = useState('')
  const [sending, setSending] = useState(false)
  const [resp, setResp] = useState(null)
  const tokenCache = useRef({})
  const pollRef = useRef(null)

  const ep = useMemo(() => endpoints.find(e => e.key === selKey) || null, [endpoints, selKey])
  const baseUrl = `http://${window.location.hostname}:${status.port}`

  useEffect(() => {
    fetchEndpoints()
    fetchStatus()
    fetchLogs()
    pollRef.current = setInterval(fetchStatus, 5000)
    return () => clearInterval(pollRef.current)
  }, [])

  // 换一条接口 = 整个测试面板按这条的样例重填，别把上一条的参数留在框里
  useEffect(() => {
    if (!ep) return
    setPathVals(Object.fromEntries((ep.pathParams || []).map(p => [p.name, p.sample])))
    setQueryVals(Object.fromEntries((ep.query || []).map(q => [q.name, q.sample])))
    setBodyText(ep.body || '')
    setIdentity(ep.auth === 'none' ? 'none' : 'admin')
    setResp(null)
  }, [selKey])

  const fetchEndpoints = async () => {
    try {
      const r = await api.get('/demo-shop/endpoints')
      const list = r.data || []
      setEndpoints(list)
      setGroupOrder(r.groupOrder || [])
      setRules(r.rules || null)
      if (list.length && !selKey) setSelKey(list[0].key)
    } catch { message.error('接口清单读取失败') }
  }
  const fetchStatus = async () => {
    try { const r = await api.get('/demo-shop/status'); setStatus(r.data || r) } catch { /* 服务没起来是常态，顶栏已经写着 STOPPED，不用再弹一次 */ }
  }
  const fetchLogs = async () => {
    try { const r = await api.get('/demo-shop/logs?limit=200'); setLogs(r.data || []) } catch { /* 同上 */ }
  }

  const handleStartStop = async () => {
    try {
      if (status.running) { await api.post('/demo-shop/stop'); message.success('订单服务已停止') }
      else {
        const r = await api.post('/demo-shop/start')
        if (r.ok === false) { message.error(`启动失败：${r.error || '端口可能被占用'}`); return }
        message.success(`订单服务已启动，端口 ${r.port}`)
      }
      tokenCache.current = {}
      setTimeout(fetchStatus, 400)
    } catch { message.error('操作失败') }
  }

  const doReset = async () => {
    try {
      const r = await api.post('/demo-shop/reset')
      const d = r.data || r
      message.success(`已重置：清掉 ${d.deletedOrders} 条订单，商品回到出厂值`)
      fetchStatus()
    } catch { message.error('重置失败') }
  }

  // ── 发请求 ──

  const realPath = ep ? ep.path.replace(/\{(\w+)\}/g, (_, n) => encodeURIComponent(pathVals[n] ?? `{${n}}`)) : ''
  const queryString = new URLSearchParams(
    Object.entries(queryVals).filter(([, v]) => String(v ?? '').trim() !== '')
  ).toString()
  const fullUrl = ep ? `${baseUrl}${realPath}${queryString ? '?' + queryString : ''}` : ''
  const hasBody = ep && !['GET', 'DELETE'].includes(ep.method)

  const getToken = async (who) => {
    if (tokenCache.current[who]) return tokenCache.current[who]
    const acc = (status.accounts || []).find(a => a.username === who)
    const r = await fetch(`${baseUrl}/api/login`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: who, password: acc?.password || '' }),
    })
    const j = await r.json()
    if (!j.token) throw new Error(`用 ${who} 登录没拿到 token`)
    tokenCache.current[who] = j.token
    return j.token
  }

  const handleSend = async () => {
    if (!ep) return
    setSending(true); setResp(null)
    const t0 = performance.now()
    try {
      const headers = {}
      if (identity === 'admin' || identity === 'clerk') headers.Authorization = `Bearer ${await getToken(identity)}`
      if (identity === 'bad') headers.Authorization = 'Bearer 这是一个乱填的token'
      let body
      if (hasBody && bodyText.trim()) { headers['Content-Type'] = 'application/json'; body = bodyText }
      const r = await fetch(fullUrl, { method: ep.method, headers, body })
      const text = await r.text()
      setResp({ status: r.status, ms: Math.round(performance.now() - t0), text })
    } catch (e) {
      setResp({ error: `${e.message}${status.running ? '' : '（服务没启动，先点右上角「启动服务」）'}` })
    } finally {
      setSending(false)
      setTimeout(() => { fetchLogs(); fetchStatus() }, 200)
    }
  }

  const curl = () => {
    const parts = [`curl -i -X ${ep.method} '${fullUrl}'`]
    if (identity !== 'none') parts.push(`-H 'Authorization: Bearer <token>'`)
    if (hasBody && bodyText.trim()) {
      parts.push(`-H 'Content-Type: application/json'`)
      parts.push(`-d '${bodyText.replace(/\n\s*/g, '')}'`)
    }
    return parts.join(' \\\n  ')
  }

  // 返回码撞上清单里写好的报错时，直接告诉人这是设计好的，不是坏了
  const matchedError = useMemo(() => {
    if (!ep || !resp || resp.error || resp.status < 400) return null
    let code = null
    try { code = JSON.parse(resp.text)?.code } catch { /* 返回不是 JSON（比如 500 的纯文本），那就没有 code 可认 */ }
    return (ep.errors || []).find(e => e.status === resp.status && (!code || e.code === code)) || null
  }, [ep, resp])

  const unlogged = !!ep && (rules?.unloggedPaths || []).includes(ep.path)

  const shownLogs = useMemo(() => (
    onlyThis && ep ? logs.filter(l => l.method === ep.method && pathMatches(ep.path, l.path)) : logs
  ), [logs, onlyThis, ep])

  const grouped = useMemo(() => {
    const order = groupOrder.length ? groupOrder : [...new Set(endpoints.map(e => e.group))]
    return order.map(g => [g, endpoints.filter(e => e.group === g)]).filter(([, v]) => v.length)
  }, [endpoints, groupOrder])

  // ─── 左栏：一条一条的接口 ───

  const renderRail = () => (
    <div style={{
      width: 268, flexShrink: 0, borderRight: '1px solid rgba(0,0,0,0.05)',
      display: 'flex', flexDirection: 'column', minHeight: 0,
    }}>
      <div style={{
        padding: '10px 14px', borderBottom: '1px solid rgba(0,0,0,0.04)',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <span style={{ fontWeight: 600, fontSize: 13, color: '#1d2129' }}>接口</span>
        <Tooltip title="这些是真服务上实际开着的接口，页面上不能加、不能删、不能改路径">
          <Tag style={{ margin: 0, fontSize: 11 }} icon={<LockFilled style={{ fontSize: 10 }} />}>固定 {endpoints.length} 条</Tag>
        </Tooltip>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: '6px 8px' }}>
        {grouped.map(([g, list]) => (
          <div key={g} style={{ marginBottom: 6 }}>
            <div style={{ fontSize: 11, color: '#c9cdd4', padding: '6px 6px 2px', letterSpacing: 1 }}>{g}</div>
            {list.map(e => {
              const sel = e.key === selKey
              return (
                <div key={e.key} onClick={() => setSelKey(e.key)} style={{
                  padding: '9px 10px', marginBottom: 4, borderRadius: 12, cursor: 'pointer',
                  background: sel ? 'rgba(14,165,160,0.07)' : 'transparent',
                  borderLeft: `3px solid ${sel ? '#0ea5a0' : 'rgba(0,0,0,0.08)'}`,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Tag style={{
                      margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 4px', borderRadius: 8,
                      fontWeight: 600, color: METHOD_COLOR(e.method), borderColor: METHOD_COLOR(e.method),
                      background: 'transparent',
                    }}>{e.method}</Tag>
                    <span style={{
                      flex: 1, fontSize: 11, fontFamily: MONO, color: '#4e5969',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{e.path}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 5 }}>
                    <span style={{ fontSize: 12, color: sel ? '#1d2129' : '#86909c', fontWeight: sel ? 500 : 400 }}>{e.name}</span>
                    <Tag color={AUTH_META[e.auth].color} style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 5px', borderRadius: 8 }}>
                      {AUTH_META[e.auth].label}
                    </Tag>
                  </div>
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )

  // ─── 右栏 · 接口说明 ───

  const kv = (label, node) => (
    <div style={{ display: 'flex', gap: 10, marginBottom: 6, fontSize: 13 }}>
      <span style={{ width: 76, color: '#86909c', flexShrink: 0 }}>{label}</span>
      <span style={{ flex: 1, minWidth: 0 }}>{node}</span>
    </div>
  )

  const renderSpec = () => {
    if (!ep) return <Empty style={{ marginTop: 80 }} image={Empty.PRESENTED_IMAGE_SIMPLE} description="左边选一条接口" />
    return (
      <div style={{ height: '100%', overflow: 'auto', padding: '14px 16px' }}>
        <div style={{ marginBottom: 14, padding: '10px 14px', borderRadius: 12, background: 'rgba(14,165,160,0.05)', border: '1px solid rgba(14,165,160,0.15)' }}>
          <div style={{ fontSize: 11, color: '#86909c', marginBottom: 6 }}>接口地址</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Tag style={{ margin: 0, fontWeight: 600, color: METHOD_COLOR(ep.method), borderColor: METHOD_COLOR(ep.method), background: 'transparent' }}>{ep.method}</Tag>
            <code style={{ fontFamily: MONO, fontSize: 13, color: '#0ea5a0', fontWeight: 500 }}>{baseUrl}{ep.path}</code>
            <Button size="small" type="text" icon={<CopyOutlined />}
              onClick={() => { copyToClipboard(`${baseUrl}${ep.path}`); message.success('已复制') }} />
          </div>
        </div>

        {kv('名称', <b>{ep.name}</b>)}
        {kv('说明', ep.summary)}
        {kv('谁能调', <>
          <Tag color={AUTH_META[ep.auth].color} style={{ margin: 0 }}>{AUTH_META[ep.auth].label}</Tag>
          {ep.auth !== 'none' && <span style={{ fontSize: 12, color: '#86909c', marginLeft: 8 }}>
            请求头带 <code style={{ fontFamily: MONO }}>Authorization: Bearer &lt;token&gt;</code>
          </span>}
        </>)}

        <Alert type="info" showIcon style={{ margin: '12px 0', fontSize: 12 }}
          message={<>
            <b>这是真接口，不是 Mock：</b>方法、路径、状态码、返回内容都是真实代码算出来的，
            <b>页面上改不了</b>（Mock 那边才是「你配什么它答什么」）。
            你能改的只有<b>「发什么请求」</b> —— 见下面「测试」页。
          </>} />

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
          {[
            ['方法', false], ['路径', false], ['状态码', false], ['返回内容', false], ['延迟', false],
            ['路径参数', true], ['查询参数', true], ['请求体', true], ['用哪个账号', true],
          ].map(([t, editable]) => (
            <Tag key={t} color={editable ? 'cyan' : 'default'}
              icon={editable ? <EditOutlined style={{ fontSize: 10 }} /> : <LockFilled style={{ fontSize: 10 }} />}
              style={{ margin: 0, fontSize: 11 }}>{t}</Tag>
          ))}
        </div>

        {unlogged && (
          <Alert type="info" showIcon style={{ marginBottom: 12, fontSize: 12 }}
            message="这条接口故意不进「请求日志」—— 健康检查会被反复轮询，记下来会把真正的业务请求冲没。" />
        )}

        {!!(ep.pathParams || []).length && (
          <>
            <div style={sectionTitle}>路径参数</div>
            <Table size="small" pagination={false} rowKey="name" dataSource={ep.pathParams} columns={[
              { title: '名字', dataIndex: 'name', width: 130, render: v => <code style={{ fontFamily: MONO }}>{'{'}{v}{'}'}</code> },
              { title: '样例', dataIndex: 'sample', width: 180, render: v => <code style={{ fontFamily: MONO }}>{v}</code> },
              { title: '说明', dataIndex: 'desc' },
            ]} />
          </>
        )}

        {!!(ep.query || []).length && (
          <>
            <div style={sectionTitle}>查询参数（都可以不填）</div>
            <Table size="small" pagination={false} rowKey="name" dataSource={ep.query} columns={[
              { title: '名字', dataIndex: 'name', width: 130, render: v => <code style={{ fontFamily: MONO }}>{v}</code> },
              { title: '默认', dataIndex: 'sample', width: 180, render: v => <code style={{ fontFamily: MONO }}>{v || '—'}</code> },
              { title: '说明', dataIndex: 'desc' },
            ]} />
          </>
        )}

        {ep.body && (<>
          <div style={sectionTitle}>请求体样例</div>
          <pre style={codeBox}>{ep.body}</pre>
        </>)}

        <div style={sectionTitle}>返回样例（成功时）</div>
        <pre style={codeBox}>{ep.sampleResponse}</pre>

        {!!(ep.errors || []).length && (<>
          <div style={sectionTitle}>它会怎么报错 —— 这些都是故意设计的，正好拿来写用例</div>
          <Table size="small" pagination={false} rowKey={r => r.status + r.code} dataSource={ep.errors} columns={[
            { title: '状态码', dataIndex: 'status', width: 80, render: v => <b style={{ color: CODE_COLOR(v), fontFamily: MONO }}>{v}</b> },
            { title: '错误码', dataIndex: 'code', width: 180, render: v => <code style={{ fontFamily: MONO }}>{v}</code> },
            { title: '什么时候会出现', dataIndex: 'when' },
          ]} />
          <Alert type="warning" showIcon style={{ marginTop: 8, fontSize: 12 }}
            message="写断言认错误码（上面那一列），别认中文句子 —— 文案会改，错误码不会。" />
        </>)}

        {ep.key === 'order_action' && rules && (<>
          <div style={sectionTitle}>状态只能这么走（跳步一律 409）</div>
          <Table size="small" pagination={false} rowKey={r => r.action + r.from} dataSource={rules.transitions} columns={[
            { title: '动作', dataIndex: 'action', width: 110, render: (v, r) => <><code style={{ fontFamily: MONO }}>{v}</code> <span style={{ color: '#86909c' }}>{r.actionLabel}</span></> },
            { title: '从', dataIndex: 'fromLabel', width: 110 },
            { title: '到', dataIndex: 'toLabel', width: 110 },
          ]} />
        </>)}
      </div>
    )
  }

  // ─── 右栏 · 测试 ───

  const renderTest = () => {
    if (!ep) return <Empty style={{ marginTop: 80 }} image={Empty.PRESENTED_IMAGE_SIMPLE} description="左边选一条接口" />
    return (
      <div style={{ height: '100%', overflow: 'auto', padding: '14px 16px' }}>
        {!status.running && (
          <Alert type="warning" showIcon style={{ marginBottom: 12, fontSize: 12 }}
            message="服务没启动，发出去会失败。点右上角「启动服务」。" />
        )}

        <div style={{ marginBottom: 14, padding: '10px 14px', borderRadius: 12, background: 'rgba(14,165,160,0.05)', border: '1px solid rgba(14,165,160,0.15)' }}>
          <div style={{ fontSize: 11, color: '#86909c', marginBottom: 6 }}>这次要发的请求</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Tag style={{ margin: 0, fontWeight: 600, color: METHOD_COLOR(ep.method), borderColor: METHOD_COLOR(ep.method), background: 'transparent' }}>{ep.method}</Tag>
            <code style={{ flex: 1, fontFamily: MONO, fontSize: 13, color: '#0ea5a0', wordBreak: 'break-all' }}>{fullUrl}</code>
            <Button size="small" type="text" icon={<CopyOutlined />} onClick={() => { copyToClipboard(fullUrl); message.success('已复制') }} />
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <div style={fieldLabel}>用哪个账号发</div>
          <Radio.Group size="small" value={identity} onChange={e => setIdentity(e.target.value)}
            options={IDENTITIES.map(i => ({ value: i.key, label: i.label }))} optionType="button" />
          <span style={{ fontSize: 12, color: '#86909c', marginLeft: 10 }}>
            {ep.auth === 'none' ? '这条不用登录，带不带都行'
              : identity === 'none' ? '预期会返回 401'
              : identity === 'bad' ? '预期会返回 401'
              : ep.auth === 'admin' && identity === 'clerk' ? '店员没这个权限，预期 403'
              : '会先自动登录换 token，再带上去'}
          </span>
        </div>

        {(ep.pathParams || []).map(p => (
          <div key={p.name} style={{ marginBottom: 10 }}>
            <div style={fieldLabel}>路径参数 {'{'}{p.name}{'}'} <span style={{ color: '#c9cdd4' }}>· {p.desc}</span></div>
            <Input size="small" style={{ fontFamily: MONO }} value={pathVals[p.name] ?? ''}
              onChange={e => setPathVals(v => ({ ...v, [p.name]: e.target.value }))} />
          </div>
        ))}

        {!!(ep.query || []).length && (
          <div style={{ marginBottom: 10 }}>
            <div style={fieldLabel}>查询参数（留空就不带）</div>
            {ep.query.map(q => (
              <div key={q.name} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                <code style={{ width: 110, fontFamily: MONO, fontSize: 12, color: '#4e5969' }}>{q.name}</code>
                <Input size="small" style={{ flex: 1, fontFamily: MONO }} placeholder={q.desc}
                  value={queryVals[q.name] ?? ''} onChange={e => setQueryVals(v => ({ ...v, [q.name]: e.target.value }))} />
              </div>
            ))}
          </div>
        )}

        {hasBody && (
          <div style={{ marginBottom: 12 }}>
            <div style={fieldLabel}>请求体{ep.body ? '' : '（这条接口不需要，留空即可）'}</div>
            <Input.TextArea spellCheck={false} rows={ep.body ? 8 : 2} value={bodyText}
              onChange={e => setBodyText(e.target.value)} style={{ fontFamily: MONO, fontSize: 12 }} />
          </div>
        )}

        <Space style={{ marginBottom: 14 }}>
          <Button type="primary" icon={<SendOutlined />} loading={sending} onClick={handleSend}>发送请求</Button>
          <Button size="small" icon={<CopyOutlined />} onClick={() => { copyToClipboard(curl()); message.success('已复制 curl 命令') }}>复制成 curl</Button>
        </Space>

        {resp && (
          <div>
            {resp.error ? (
              <Alert type="error" showIcon message="请求发不出去" description={resp.error} />
            ) : (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                  <Tag style={{ margin: 0, fontFamily: MONO, fontWeight: 600, fontSize: 13, color: CODE_COLOR(resp.status), borderColor: CODE_COLOR(resp.status), background: 'transparent' }}>{resp.status}</Tag>
                  <span style={{ fontSize: 12, color: '#86909c' }}>{resp.ms}ms</span>
                  {matchedError && (
                    <Tag color="orange" style={{ margin: 0 }}>这是设计好的报错：{matchedError.code}</Tag>
                  )}
                </div>
                {matchedError && (
                  <Alert type="info" showIcon style={{ marginBottom: 8, fontSize: 12 }}
                    message={`不是坏了 —— ${matchedError.when}`} />
                )}
                <pre style={{ ...codeBox, maxHeight: 340 }}>{pretty(resp.text) || '（没有返回内容）'}</pre>
              </>
            )}
          </div>
        )}
      </div>
    )
  }

  // ─── 右栏 · 请求日志 ───

  const renderLogs = () => (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {unlogged && (
        <Alert type="info" showIcon banner style={{ fontSize: 12 }}
          message={`${ep.path} 故意不记日志 —— 健康检查会被反复轮询，记下来会把真正的业务请求冲出屏幕。这里是空的不是没调过。`} />
      )}
      <div style={{ padding: '8px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
        <Space size={8}>
          <Radio.Group size="small" value={onlyThis} onChange={e => setOnlyThis(e.target.value)} optionType="button"
            options={[{ value: true, label: '只看这条接口' }, { value: false, label: '全部' }]} />
          <span style={{ fontSize: 12, color: '#86909c' }}>共 {shownLogs.length} 条</span>
        </Space>
        <Space size={4}>
          <Button size="small" type="text" icon={<ReloadOutlined />} onClick={fetchLogs} />
          <Popconfirm title="清空所有请求日志？" onConfirm={async () => { await api.delete('/demo-shop/logs'); fetchLogs() }}>
            <Button size="small" type="text" danger icon={<ClearOutlined />} />
          </Popconfirm>
        </Space>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
        <Table size="small" rowKey="id" dataSource={shownLogs} pagination={{ pageSize: 20, showTotal: t => `共 ${t} 条` }}
          columns={[
            { title: '时间', dataIndex: 'ts', width: 160, render: v => <span style={{ fontSize: 12, color: '#86909c' }}>{new Date(v).toLocaleString('zh-CN')}</span> },
            { title: '方法', dataIndex: 'method', width: 80, render: v => <b style={{ fontFamily: MONO, fontSize: 12, color: METHOD_COLOR(v) }}>{v}</b> },
            { title: '路径', dataIndex: 'path', ellipsis: true, render: v => <span style={{ fontFamily: MONO, fontSize: 12 }}>{v}</span> },
            { title: '状态码', dataIndex: 'status', width: 80, render: v => <b style={{ fontFamily: MONO, color: CODE_COLOR(v) }}>{v}</b> },
            { title: '耗时', dataIndex: 'durationMs', width: 80, align: 'right', render: v => <span style={{ fontFamily: MONO, fontSize: 12 }}>{v}ms</span> },
            { title: '调用人', dataIndex: 'actor', width: 90 },
          ]}
          expandable={{
            expandedRowRender: r => (
              <div style={{ fontFamily: MONO, fontSize: 12 }}>
                <div style={{ color: '#86909c', marginBottom: 4 }}>请求内容</div>
                <pre style={{ margin: 0, whiteSpace: 'pre-wrap', maxHeight: 160, overflow: 'auto' }}>{pretty(r.requestBody) || '（无请求体）'}</pre>
                <div style={{ color: '#86909c', margin: '8px 0 4px' }}>返回内容</div>
                <pre style={{ margin: 0, whiteSpace: 'pre-wrap', maxHeight: 220, overflow: 'auto' }}>{pretty(r.responseSnippet) || '（无）'}</pre>
              </div>
            ),
          }}
          locale={{
            emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={
              unlogged ? '这条接口故意不记日志，见上面那条说明'
                : onlyThis ? '这条接口还没被调过。去「测试」页发一个。'
                : '还没有人调过这个服务。'
            } />,
          }} />
      </div>
    </div>
  )

  // ─── 页面 ───

  const logCount = shownLogs.length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 70px)' }}>
      <div className="lum-page-strip" style={{ padding: '8px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <ShoppingCartOutlined style={{ fontSize: 18, color: '#0ea5a0' }} />
          <span style={{ fontWeight: 600, fontSize: 16 }}>订单服务</span>
          <Tag color="cyan" style={{ fontSize: 11 }}>真接口·会落库</Tag>
          <Badge status={status.running ? 'success' : 'default'} text={
            <span style={{ fontSize: 12, color: status.running ? '#0ea5a0' : '#86909c' }}>
              {status.running ? `LIVE :${status.port}` : 'STOPPED'}
            </span>
          } />
        </div>
        <Space size={8}>
          <Button size="small" icon={<QuestionCircleOutlined />} onClick={() => setHelpOpen(true)}>怎么用</Button>
          <Tooltip title="复制服务地址，填到测试环境的 BASE_URL">
            <Button size="small" icon={<CopyOutlined />} onClick={() => { copyToClipboard(baseUrl); message.success('已复制') }}>{baseUrl}</Button>
          </Tooltip>
          {status.running && (
            <Button size="small" icon={<ExportOutlined />} onClick={() => window.open(`${baseUrl}/docs`, '_blank')}>接口文档</Button>
          )}
          <Popconfirm title="重置样例数据" description="会清空所有订单，商品恢复出厂库存。确定？" onConfirm={doReset} okButtonProps={{ danger: true }}>
            <Button size="small" danger icon={<ClearOutlined />}>重置数据</Button>
          </Popconfirm>
          <Button size="small" type={status.running ? 'default' : 'primary'} danger={status.running}
            icon={status.running ? <PauseCircleOutlined /> : <PlayCircleOutlined />} onClick={handleStartStop}>
            {status.running ? '停止服务' : '启动服务'}
          </Button>
        </Space>
      </div>

      <div style={{ flex: 1, display: 'flex', minHeight: 0, margin: 10, background: 'var(--panel-bg)', backdropFilter: 'blur(16px)', borderRadius: 16, boxShadow: '0 2px 12px rgba(0,0,0,0.04)', overflow: 'hidden' }}>
        {renderRail()}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <div style={{ borderBottom: '1px solid rgba(0,0,0,0.04)', paddingLeft: 16, flexShrink: 0, display: 'flex' }}>
            {[
              { key: 'spec', label: '接口说明' },
              { key: 'test', label: <>测试 <SendOutlined style={{ fontSize: 11 }} /></> },
              { key: 'logs', label: <>请求日志 <Tag style={{ margin: '0 0 0 4px', fontSize: 11, borderRadius: 12, lineHeight: '18px', padding: '0 6px' }}>{logCount}</Tag></> },
            ].map(t => (
              <div key={t.key} onClick={() => { setTab(t.key); if (t.key === 'logs') fetchLogs() }} style={{
                padding: '10px 16px', cursor: 'pointer', fontSize: 14,
                color: tab === t.key ? '#0ea5a0' : '#4e5969',
                fontWeight: tab === t.key ? 500 : 400,
                borderBottom: tab === t.key ? '2px solid #0ea5a0' : '2px solid transparent',
                marginBottom: -1,
              }}>{t.label}</div>
            ))}
          </div>
          <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
            {tab === 'spec' ? renderSpec() : tab === 'test' ? renderTest() : renderLogs()}
          </div>
        </div>
      </div>

      <Drawer open={helpOpen} onClose={() => setHelpOpen(false)} width={560} title="订单服务怎么用">
        <Typography.Paragraph>
          这是平台自带的一个<b>真的小后端</b>，接口一条条列在左边。和隔壁 Mock 的区别：
          Mock 是你配什么它答什么；这个自己有规矩 —— 下单真扣库存、发过货的取消不了、店员删不了单。
        </Typography.Paragraph>

        <Typography.Title level={5}>一、先启动</Typography.Title>
        <Typography.Paragraph>
          点右上角「启动服务」，变绿 LIVE 就行。服务地址：
          <Typography.Text code copyable style={{ fontFamily: MONO }}>{baseUrl}</Typography.Text>
          <br />把它填到「项目设置 → 环境变量」的 BASE_URL，用例就能打它了。
        </Typography.Paragraph>

        <Typography.Title level={5}>二、账号</Typography.Title>
        <Typography.Paragraph>
          先调 <Typography.Text code>POST /api/login</Typography.Text> 拿 token，之后每个请求带
          <Typography.Text code>Authorization: Bearer &lt;token&gt;</Typography.Text>。
          在「测试」页选「用哪个账号发」，页面会自动帮你换好 token。
        </Typography.Paragraph>
        <Table size="small" pagination={false} rowKey="username" dataSource={status.accounts || []}
          columns={[
            { title: '账号', dataIndex: 'username', render: v => <span style={{ fontFamily: MONO }}>{v}</span> },
            { title: '密码', dataIndex: 'password', render: v => <span style={{ fontFamily: MONO }}>{v}</span> },
            { title: '能干什么', dataIndex: 'role', render: v => v === 'admin' ? '全部，含删订单 / 改商品 / 重置数据' : '下单、发货，不能删单不能改商品' },
          ]} />

        <Typography.Title level={5} style={{ marginTop: 16 }}>三、哪些能改、哪些不能</Typography.Title>
        <Typography.Paragraph>
          <b>改不了</b>：方法、路径、状态码、返回内容、延迟 —— 这些是真代码算出来的，也不能加接口、删接口。
          <br /><b>能改</b>：路径参数、查询参数、请求体、用哪个账号发。
          <br /><b>想改服务里的数据</b>：调 <Typography.Text code>PATCH /api/products/&#123;sku&#125;</Typography.Text> 改价格 / 库存 / 上下架，
          或右上角「重置数据」。
        </Typography.Paragraph>

        <Typography.Title level={5}>四、每条接口的报错</Typography.Title>
        <Typography.Paragraph>
          点左边任一接口 → 「接口说明」最下面那张表，列的都是<b>故意设计出来的</b>报错，正好拿来写用例。
          返回长这样：<Typography.Text code>{'{ code: "OUT_OF_STOCK", message: "库存不足：…" }'}</Typography.Text>
          —— 断言认 code，别认中文。
        </Typography.Paragraph>
      </Drawer>
    </div>
  )
}

const sectionTitle = { fontSize: 13, fontWeight: 600, color: '#1d2129', margin: '16px 0 8px' }
const fieldLabel = { fontSize: 12, color: '#86909c', marginBottom: 4 }
const codeBox = {
  margin: 0, padding: 12, borderRadius: 12, background: 'rgba(0,0,0,0.03)',
  border: '1px solid rgba(0,0,0,0.05)', fontFamily: MONO, fontSize: 12, lineHeight: 1.6,
  whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: 260, overflow: 'auto',
}
