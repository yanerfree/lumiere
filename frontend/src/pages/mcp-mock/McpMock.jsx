import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import {
  Button, Space, Input, Tag, Radio, Tooltip, Empty, Modal, Alert, Select, message,
} from 'antd'
import {
  PlusOutlined, PlayCircleOutlined, PauseCircleOutlined, ReloadOutlined,
  ApiOutlined, LockFilled,
} from '@ant-design/icons'
import { api } from '../../utils/request'
import ServerSettings from './ServerSettings'
import ToolsPanel from './ToolsPanel'
import LogsPanel from './LogsPanel'

const MONO = 'var(--font-mono)'

const TRANSPORT_TAG = { 'streamable-http': 'HTTP', sse: 'SSE', stdio: 'stdio' }
const AUTH_TAG = { none: null, bearer: 'Bearer', apikey: 'API Key' }
const VALIDATE_TAG = { strict: '严格', loose: '宽松', off: '不校验' }

/**
 * MCP Mock —— 多服务版。
 *
 * 结构和「协议 Mock」一样是两层：左边一列是**服务**，右边是这个服务的
 * 设置 / 工具 / 调用日志。所有服务共用一个端口，靠地址里的 `/<服务代号>/mcp` 分开。
 */
export default function McpMock() {
  const [meta, setMeta] = useState({})
  const [servers, setServers] = useState([])
  const [selId, setSelId] = useState(null)
  const [form, setForm] = useState(null)       // 当前服务的可编辑副本
  const [origin, setOrigin] = useState(null)
  const [status, setStatus] = useState({})
  const [tab, setTab] = useState('settings')
  const [saving, setSaving] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [draft, setDraft] = useState(null)
  const pollRef = useRef(null)

  const fetchStatus = useCallback(async () => {
    try { const r = await api.get('/mcp-mock/status'); setStatus(r.data || r) } catch {}
  }, [])

  const fetchServers = useCallback(async (keepId) => {
    try {
      const r = await api.get('/mcp-mock/servers')
      const list = r.data || []
      setServers(list)
      setSelId(prev => {
        const want = keepId || prev
        const hit = list.find(s => s.id === want) || list[0]
        return hit ? hit.id : null
      })
    } catch {}
  }, [])

  useEffect(() => {
    fetchServers(); fetchStatus()
    pollRef.current = setInterval(fetchStatus, 5000)
    return () => clearInterval(pollRef.current)
  }, [fetchServers, fetchStatus])

  useEffect(() => {
    api.get('/mcp-mock/meta').then(r => setMeta(r.data || r)).catch(() => {})
  }, [])

  // 选中服务变了就拉一次详情（列表里没有 tools，但这里只要服务本体）
  useEffect(() => {
    if (!selId) { setForm(null); setOrigin(null); return }
    let dead = false
    api.get(`/mcp-mock/servers/${selId}`).then(r => {
      if (dead) return
      const d = r.data || r
      const f = { ...d, authConfig: d.authConfig || {} }
      setForm(f); setOrigin(f)
    }).catch(() => {})
    return () => { dead = true }
  }, [selId])

  const dirty = useMemo(() => {
    if (!form || !origin) return false
    const keys = ['name', 'description', 'instructions', 'transport', 'authType', 'validateMode']
    if (keys.some(k => (form[k] || '') !== (origin[k] || ''))) return true
    return JSON.stringify(form.authConfig || {}) !== JSON.stringify(origin.authConfig || {})
  }, [form, origin])

  const saveServer = async () => {
    if (!form) return
    setSaving(true)
    try {
      const r = await api.put(`/mcp-mock/servers/${form.id}`, {
        name: form.name,
        description: form.description,
        instructions: form.instructions,
        transport: form.transport,
        authType: form.authType,
        authConfig: form.authConfig || {},
        validateMode: form.validateMode,
      })
      if (r.data?.reloadError) message.warning(`已保存，但重载失败：${r.data.reloadError}`)
      else message.success('已保存')
      await fetchServers(form.id)
      setOrigin({ ...form })
      fetchStatus()
    } catch {} finally { setSaving(false) }
  }

  /**
   * 锁 / 停用这两个开关。
   *
   * ⚠ 这两条接口回的是 `{id, locked}` / `{id, enabled}` 这种**一小片**，
   *   不是整个服务 —— 直接拿它当 form 会把名字、认证、地址全冲没。所以改完
   *   重新拉一次详情。
   */
  const patchServer = async (path, okMsg) => {
    if (!form) return
    const id = form.id
    try {
      const r = await api.patch(`/mcp-mock/servers/${id}/${path}`)
      if (r.data?.reloadError) message.warning(`已保存，但重载失败：${r.data.reloadError}`)
      else if (okMsg) message.success(okMsg)
      const d = await api.get(`/mcp-mock/servers/${id}`)
      const full = d.data || d
      const f = { ...full, authConfig: full.authConfig || {} }
      setForm(f); setOrigin(f)
      await fetchServers(id)
      fetchStatus()
    } catch {}
  }

  const deleteServer = async () => {
    if (!form) return
    try {
      const r = await api.delete(`/mcp-mock/servers/${form.id}`)
      message.success('服务已删除')
      setSelId(null)
      await fetchServers()
      fetchStatus()
    } catch {}
  }

  const createServer = async () => {
    const d = draft || {}
    if (!d.name?.trim()) { message.warning('服务名称不能为空'); return }
    if (!d.slug?.trim()) { message.warning('服务代号不能为空'); return }
    try {
      const r = await api.post('/mcp-mock/servers', {
        name: d.name.trim(),
        slug: d.slug.trim(),
        description: d.description || '',
        transport: d.transport || 'streamable-http',
        authType: d.authType || 'none',
        authConfig: d.authType === 'bearer' ? { token: d.token || '' }
          : d.authType === 'apikey' ? { headerName: d.headerName || 'X-API-Key', apiKey: d.apiKey || '' }
            : {},
        validateMode: d.validateMode || 'strict',
      })
      const created = r.data || r
      if (created.reloadError) message.warning(`已创建，但重载失败：${created.reloadError}`)
      else message.success('服务已建好')
      setCreateOpen(false); setDraft(null)
      await fetchServers(created.id)
      setTab('tools')
      fetchStatus()
    } catch {}
  }

  const toggleService = async () => {
    try {
      if (status.running) { await api.post('/mcp-mock/stop'); message.success('已停止') }
      else { await api.post('/mcp-mock/start'); message.success('已启动') }
      setTimeout(fetchStatus, 600)
    } catch {}  // request() 自己弹过 toast 了，这里再弹一次是重的
  }

  const reloadService = async () => {
    try {
      const r = await api.post('/mcp-mock/reload')
      if (r.reloadError) message.error(`重载失败：${r.reloadError}`)
      else message.success('已重载，改动生效了')
      setTimeout(fetchStatus, 600)
    } catch {}
  }

  // 库里已启用的服务 vs 上次真挂上去的 —— 对不上就是「改了还没生效」
  const stale = useMemo(() => {
    if (!status.running) return false
    const mountedIds = new Set((status.mounted || []).map(m => m.id))
    const enabled = servers.filter(s => s.enabled)
    if (enabled.length !== mountedIds.size) return true
    return enabled.some(s => !mountedIds.has(s.id))
  }, [status, servers])

  const TABS = [
    { key: 'settings', label: '服务设置' },
    { key: 'tools', label: '工具' },
    { key: 'logs', label: '调用日志' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 70px)', background: 'transparent' }}>

      {/* ━━━ 顶栏 ━━━ */}
      <div className="lum-page-strip" style={{
        padding: '10px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <ApiOutlined style={{ fontSize: 18, color: '#7c5cbf' }} />
            <span style={{ fontWeight: 600, fontSize: 16, letterSpacing: 0.5 }}>MCP Mock</span>
          </div>
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 6, padding: '2px 10px', borderRadius: 12,
            background: status.running ? '#e0f7f6' : 'rgba(0,0,0,0.04)',
            border: `1px solid ${status.running ? 'rgba(14,165,160,0.3)' : 'rgba(0,0,0,0.1)'}`,
          }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: status.running ? '#0ea5a0' : '#c9cdd4' }} />
            <span style={{ fontSize: 12, fontWeight: 600, fontFamily: MONO, color: status.running ? '#0ea5a0' : '#999' }}>
              {status.running ? 'LIVE' : 'STOPPED'}
            </span>
          </div>
          {status.running && (
            <Tag style={{ margin: 0, fontSize: 11, borderRadius: 12, fontFamily: MONO }}>:{status.port}</Tag>
          )}
          <span style={{ fontSize: 12, color: '#86909c' }}>
            {status.serversEnabled ?? 0}/{status.serversCount ?? 0} 个服务在跑
          </span>
          {stale && (
            <Tooltip title="数据库里的服务和正在跑的那几个对不上 —— 点「重载」让改动生效">
              <Tag color="orange" style={{ margin: 0, fontSize: 11 }}>改动还没生效</Tag>
            </Tooltip>
          )}
        </div>
        <Space size={8}>
          <Button size="small" icon={<ReloadOutlined />} onClick={reloadService} disabled={!status.running}>重载</Button>
          <Button size="small" type={status.running ? 'default' : 'primary'}
            icon={status.running ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
            onClick={toggleService}>{status.running ? '停止' : '启动'}</Button>
        </Space>
      </div>

      {/* ━━━ 主体 ━━━ */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>

        {/* 左：服务列表 */}
        <div style={{
          width: 240, flexShrink: 0, borderRight: '1px solid rgba(0,0,0,0.04)',
          display: 'flex', flexDirection: 'column',
        }}>
          <div style={{
            padding: '10px 14px', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0,
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span style={{ fontWeight: 600, fontSize: 13, color: '#1d2129' }}>MCP 服务</span>
            <Button size="small" type="primary" ghost icon={<PlusOutlined />}
              onClick={() => { setDraft({ transport: 'streamable-http', authType: 'none', validateMode: 'strict' }); setCreateOpen(true) }}>
              新建
            </Button>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: '6px 8px' }}>
            {servers.map(s => {
              const sel = s.id === selId
              return (
                <div key={s.id} onClick={() => { setSelId(s.id); setTab('settings') }} style={{
                  padding: '8px 10px', cursor: 'pointer', marginBottom: 4, borderRadius: 12,
                  borderLeft: `3px solid ${sel ? '#7c5cbf' : 'transparent'}`,
                  background: sel ? 'rgba(124,92,191,0.06)' : 'transparent',
                  transition: 'all 0.15s',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 3 }}>
                    {s.locked && (
                      <Tooltip title="已锁定"><LockFilled style={{ fontSize: 11, color: '#ff7d00', flexShrink: 0 }} /></Tooltip>
                    )}
                    <span style={{
                      fontSize: 13, fontWeight: 500, minWidth: 0,
                      color: s.enabled ? '#1d2129' : '#c9cdd4',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{s.name}</span>
                    {s.builtin && <Tag color="purple" style={{ margin: 0, fontSize: 10, lineHeight: '15px', padding: '0 4px' }}>预置</Tag>}
                  </div>
                  <div style={{ fontSize: 10, fontFamily: MONO, color: '#86909c', marginBottom: 3 }}>/{s.slug}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
                    <Tag style={{ margin: 0, fontSize: 10, lineHeight: '15px', padding: '0 4px' }}>
                      {TRANSPORT_TAG[s.transport] || s.transport}
                    </Tag>
                    {AUTH_TAG[s.authType] && (
                      <Tag color="gold" style={{ margin: 0, fontSize: 10, lineHeight: '15px', padding: '0 4px' }}>
                        {AUTH_TAG[s.authType]}
                      </Tag>
                    )}
                    <Tag color={s.validateMode === 'off' ? 'default' : s.validateMode === 'loose' ? 'blue' : 'cyan'}
                      style={{ margin: 0, fontSize: 10, lineHeight: '15px', padding: '0 4px' }}>
                      {VALIDATE_TAG[s.validateMode] || s.validateMode}
                    </Tag>
                    <span style={{ fontSize: 10, color: '#c9cdd4' }}>{s.toolCount ?? 0} 个工具</span>
                    {!s.enabled && <Tag style={{ margin: 0, fontSize: 10, lineHeight: '15px', padding: '0 4px' }}>停用</Tag>}
                  </div>
                </div>
              )
            })}
            {servers.length === 0 && (
              <div style={{ padding: '40px 0' }}>
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={<span style={{ color: '#c9cdd4', fontSize: 12 }}>还没有 MCP 服务</span>} />
              </div>
            )}
          </div>
        </div>

        {/* 右：详情 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          {!form ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={<span style={{ color: '#c9cdd4' }}>左边选一个服务</span>} />
            </div>
          ) : (
            <>
              <div style={{ borderBottom: '1px solid rgba(0,0,0,0.04)', paddingLeft: 16, flexShrink: 0 }}>
                <div style={{ display: 'flex' }}>
                  {TABS.map(t => (
                    <div key={t.key} onClick={() => setTab(t.key)} style={{
                      padding: '10px 16px', cursor: 'pointer', fontSize: 14, position: 'relative',
                      color: tab === t.key ? '#7c5cbf' : '#4e5969',
                      fontWeight: tab === t.key ? 600 : 400,
                    }}>
                      {t.label}
                      {tab === t.key && <div style={{
                        position: 'absolute', bottom: 0, left: 16, right: 16, height: 2,
                        background: 'rgba(124,92,191,0.12)', borderRadius: 8,
                      }} />}
                    </div>
                  ))}
                </div>
              </div>

              <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                {tab === 'settings' && (
                  <ServerSettings
                    meta={meta} form={form} setForm={setForm} dirty={dirty} saving={saving}
                    running={!!status.running}
                    onSave={saveServer}
                    onToggleLock={() => patchServer('lock')}
                    onToggleEnabled={() => patchServer('toggle')}
                    onDelete={deleteServer}
                  />
                )}
                {tab === 'tools' && (
                  <ToolsPanel server={form} serverLocked={!!form.locked}
                    onChanged={() => { fetchServers(form.id); fetchStatus() }} />
                )}
                {tab === 'logs' && <LogsPanel serverId={form.id} onCleared={fetchStatus} />}
              </div>
            </>
          )}
        </div>
      </div>

      {/* 新建服务 */}
      <Modal title="新建 MCP 服务" open={createOpen} width={520} okText="创建" cancelText="取消"
        onCancel={() => setCreateOpen(false)} onOk={createServer}>
        {draft && <CreateForm meta={meta} draft={draft} setDraft={setDraft} />}
      </Modal>
    </div>
  )
}

const flabel = { fontSize: 12, color: '#86909c', marginBottom: 4 }

function CreateForm({ meta, draft, setDraft }) {
  const set = (kv) => setDraft(d => ({ ...d, ...kv }))
  return (
    <div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={flabel}>服务名称 *</div>
          <Input value={draft.name || ''} placeholder="订单服务 Mock" onChange={e => set({ name: e.target.value })} />
        </div>
        <div style={{ width: 190 }}>
          <div style={flabel}>服务代号 *</div>
          <Input spellCheck={false} style={{ fontFamily: MONO }} value={draft.slug || ''}
            placeholder="order-mock" onChange={e => set({ slug: e.target.value })} />
        </div>
      </div>
      <Alert type="info" showIcon style={{ marginBottom: 12, fontSize: 12 }}
        message={`接入地址会长这样：http://<平台地址>:28300/${draft.slug || '<服务代号>'}/mcp`}
        description="服务代号建好就改不了了 —— 它在地址里。只能用小写字母、数字和中划线。" />

      <div style={{ marginBottom: 12 }}>
        <div style={flabel}>说明</div>
        <Input value={draft.description || ''} placeholder="这个服务用来测什么"
          onChange={e => set({ description: e.target.value })} />
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={flabel}>传输方式</div>
        <Radio.Group size="small" buttonStyle="solid" value={draft.transport}
          onChange={e => set({ transport: e.target.value })}>
          {(meta.transports || []).map(t => (
            <Tooltip key={t.value} title={t.hint}>
              <Radio.Button value={t.value} disabled={!t.supported} style={{ fontSize: 11 }}>{t.label}</Radio.Button>
            </Tooltip>
          ))}
        </Radio.Group>
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={flabel}>认证方式</div>
        <Radio.Group size="small" buttonStyle="solid" value={draft.authType}
          onChange={e => set({ authType: e.target.value })}>
          {(meta.authTypes || []).map(a => (
            <Radio.Button key={a.value} value={a.value} style={{ fontSize: 11 }}>{a.label}</Radio.Button>
          ))}
        </Radio.Group>
        {draft.authType === 'bearer' && (
          <Input size="small" style={{ marginTop: 8, fontFamily: MONO }} spellCheck={false}
            placeholder="Token，比如 my-secret-token" value={draft.token || ''}
            onChange={e => set({ token: e.target.value })} />
        )}
        {draft.authType === 'apikey' && (
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <Input size="small" style={{ width: 170, fontFamily: MONO }} spellCheck={false}
              placeholder="X-API-Key" value={draft.headerName || ''}
              onChange={e => set({ headerName: e.target.value })} />
            <Input size="small" style={{ flex: 1, fontFamily: MONO }} spellCheck={false}
              placeholder="Key 的值" value={draft.apiKey || ''}
              onChange={e => set({ apiKey: e.target.value })} />
          </div>
        )}
      </div>

      <div>
        <div style={flabel}>参数校验松紧</div>
        <Select size="small" style={{ width: '100%' }} value={draft.validateMode}
          onChange={v => set({ validateMode: v })}
          options={(meta.validateModes || []).map(v => ({
            value: v.value,
            label: <span>{v.label} <span style={{ color: '#c9cdd4', fontSize: 11 }}>— {v.hint}</span></span>,
          }))} />
      </div>
    </div>
  )
}
