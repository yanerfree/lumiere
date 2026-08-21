import { useState, useEffect, useRef, useMemo, useCallback, Fragment, lazy, Suspense } from 'react'
import {
  Button, Space, Input, Select, Tag, Radio, Popconfirm, Tooltip, Badge, Pagination,
  Empty, Typography, InputNumber, Switch, message, Drawer, Alert, Modal, Spin
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, SaveOutlined, PlayCircleOutlined, PauseCircleOutlined,
  ReloadOutlined, ExportOutlined, ClearOutlined, CopyOutlined, CloudServerOutlined,
  LockOutlined, LockFilled, UnlockOutlined, HolderOutlined, SettingOutlined, CheckOutlined,
  SendOutlined, StarOutlined, ApiOutlined, WifiOutlined, GlobalOutlined, CloudOutlined
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'
import { LogBlock, CODE_BLOCK_STYLE } from '../../components/MockCodeBlock'

const { Text } = Typography
const { TextArea } = Input

const MONO = 'var(--font-mono)'

const fmtHeaders = (h) => {
  if (!h || typeof h !== 'object' || !Object.keys(h).length) return '-'
  try { return JSON.stringify(h, null, 2) } catch { return String(h) }
}

const STATUS_COLOR = (sc) => {
  if (sc >= 500) return '#e8453c'
  if (sc >= 400) return '#ff7d00'
  if (sc >= 300) return '#7c5cbf'
  return '#0ea5a0'
}

const METHOD_COLOR = (m) => {
  if (m === 'ANY') return '#d4380d'
  if (m === 'GET') return '#0ea5a0'
  if (m === 'POST') return '#ff7d00'
  if (m === 'DELETE') return '#e8453c'
  if (m === 'PUT') return '#4e8af0'
  if (m === 'PATCH') return '#7c5cbf'
  return '#4e5969'
}

const MODE_LABELS = { default: '默认响应', random: '随机响应', custom: '自定义', echo: '回显请求', echo_body: '回显请求体' }
const MODE_COLORS = { default: 'blue', random: 'purple', custom: 'cyan' }

const CONTENT_TYPES = [
  { value: 'application/json', label: 'JSON' },
  { value: 'text/xml', label: 'XML' },
  { value: 'text/html', label: 'HTML' },
  { value: 'text/plain', label: 'Text' },
  { value: 'text/csv', label: 'CSV' },
  { value: 'application/xml', label: 'XML (application)' },
  { value: 'application/octet-stream', label: 'Binary' },
]

const CT_SHORT = (ct) => {
  if (!ct) return 'JSON'
  if (ct.includes('json')) return 'JSON'
  if (ct.includes('xml')) return 'XML'
  if (ct.includes('html')) return 'HTML'
  if (ct.includes('csv')) return 'CSV'
  if (ct.includes('plain')) return 'Text'
  return ct.split('/').pop()
}

const CT_COLOR = (ct) => {
  if (!ct) return 'blue'
  if (ct.includes('json')) return 'blue'
  if (ct.includes('xml')) return 'orange'
  if (ct.includes('html')) return 'cyan'
  if (ct.includes('csv')) return 'cyan'
  if (ct.includes('plain')) return 'default'
  return 'default'
}

const WsMockPanel = lazy(() => import('./WsMockPanel'))
const TcpMockPanel = lazy(() => import('./TcpMockPanel'))
const UdpMockPanel = lazy(() => import('./UdpMockPanel'))
const GrpcMockPanel = lazy(() => import('./GrpcMockPanel'))

const PROTOCOLS = [
  { key: 'http', label: 'HTTP', color: '#0ea5a0', icon: <ApiOutlined /> },
  { key: 'ws', label: 'WebSocket', color: '#0ea5a0', icon: <WifiOutlined /> },
  { key: 'tcp', label: 'TCP', color: '#ff7d00', icon: <CloudServerOutlined /> },
  { key: 'udp', label: 'UDP', color: '#4e8af0', icon: <GlobalOutlined /> },
  { key: 'grpc', label: 'gRPC', color: '#7c5cbf', icon: <CloudOutlined /> },
]

export default function ApiMock() {
  const [protocol, setProtocol] = useState('http')
  const activeProto = PROTOCOLS.find(p => p.key === protocol) || PROTOCOLS[0]

  return (
    <div style={{ height: 'calc(100vh - 70px)', display: 'flex', flexDirection: 'column' }}>
      {/* 协议标签栏 */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 0, padding: '0 20px',
        background: 'rgba(255,255,255,0.5)', borderBottom: '1px solid rgba(0,0,0,0.06)',
        flexShrink: 0, height: 42,
      }}>
        {PROTOCOLS.map(p => (
          <div
            key={p.key}
            onClick={() => setProtocol(p.key)}
            style={{
              padding: '8px 18px', cursor: 'pointer', fontSize: 13, fontWeight: 500,
              display: 'flex', alignItems: 'center', gap: 6,
              color: protocol === p.key ? p.color : '#86909c',
              borderBottom: protocol === p.key ? `2px solid ${p.color}` : '2px solid transparent',
              transition: 'all 0.2s',
            }}
          >
            {p.icon} {p.label}
          </div>
        ))}
      </div>

      {/* 面板内容 */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        {protocol === 'http' && <HttpMockPanel />}
        <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#86909c' }}>加载中...</div>}>
          {protocol === 'ws' && <WsMockPanel />}
          {protocol === 'tcp' && <TcpMockPanel />}
          {protocol === 'udp' && <UdpMockPanel />}
          {protocol === 'grpc' && <GrpcMockPanel />}
        </Suspense>
      </div>
    </div>
  )
}

// HTTP Mock 面板（原 ApiMock 组件）
function HttpMockPanel() {
  const [routes, setRoutes] = useState([])
  const [selectedRouteId, setSelectedRouteId] = useState(null)
  const [dragIdx, setDragIdx] = useState(null)
  const [routeForm, setRouteForm] = useState(null)
  const [originalForm, setOriginalForm] = useState(null)
  const [presets, setPresets] = useState([])
  const [selectedPreset, setSelectedPreset] = useState(undefined)
  const [customPresets, setCustomPresets] = useState([])
  const [savePresetOpen, setSavePresetOpen] = useState(false)
  const [savePresetName, setSavePresetName] = useState('')
  const [logs, setLogs] = useState([])
  const [logsTotal, setLogsTotal] = useState(0)
  const [logPage, setLogPage] = useState(1)
  const [logPageSize] = useState(50)
  const [expandedLogId, setExpandedLogId] = useState(null)
  const [expandedLogDetail, setExpandedLogDetail] = useState(null)
  const [logDrawerOpen, setLogDrawerOpen] = useState(false)
  const [logDetailLoading, setLogDetailLoading] = useState(false)
  const [logFilter, setLogFilter] = useState('all')
  const [serviceStatus, setServiceStatus] = useState({ running: false, port: 28200, captureEnabled: true, routesCount: 0, routesEnabled: 0, totalRequests: 0 })
  const [saving, setSaving] = useState(false)
  const [activeTab, setActiveTab] = useState('config')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  // 打开高级设置时的快照，用于「取消」回滚抽屉内的改动
  const [advancedSnapshot, setAdvancedSnapshot] = useState(null)
  const [copyText, setCopyText] = useState('复制')
  const pollRef = useRef(null)
  const [testMethod, setTestMethod] = useState('GET')
  const [testPath, setTestPath] = useState('/')
  const [testBody, setTestBody] = useState('')
  const [testHeaders, setTestHeaders] = useState('')
  const [testResult, setTestResult] = useState(null)
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    fetchRoutes()
    fetchPresets()
    fetchCustomPresets()
    fetchStatus()
    fetchLogs()
    pollRef.current = setInterval(() => { fetchStatus() }, 5000)
    return () => clearInterval(pollRef.current)
  }, [])

  const fetchRoutes = async () => { try { const r = await api.get('/api-mock/routes'); setRoutes(r.data || r || []) } catch {} }
  const fetchPresets = async () => { try { const r = await api.get('/api-mock/presets'); setPresets(r.data?.data || r.data || []) } catch {} }
  const fetchCustomPresets = async () => { try { const r = await api.get('/api-mock/custom-presets'); setCustomPresets(r.data?.data || r.data || []) } catch {} }
  const fetchStatus = async () => { try { const r = await api.get('/api-mock/status'); setServiceStatus(r.data || r) } catch {} }
  const fetchLogs = async (page) => {
    try {
      const p = page || logPage
      const params = new URLSearchParams({ limit: String(logPageSize), offset: String((p - 1) * logPageSize) })
      if (logFilter !== 'all') params.set('status', logFilter)
      const r = await api.get(`/api-mock/logs?${params}`)
      const d = r.data || r
      setLogs(d.data || d || [])
      setLogsTotal(d.total ?? (d.data || d || []).length)
    } catch {}
  }

  useEffect(() => { setLogPage(1); fetchLogs(1) }, [logFilter])

  useEffect(() => {
    if (routes.length > 0 && !selectedRouteId) selectRoute(routes[0])
  }, [routes])

  const selectRoute = useCallback((route) => {
    const formData = { ...route }
    setSelectedRouteId(route.id)
    setRouteForm(formData)
    setOriginalForm(formData)
    setSelectedPreset(undefined)
    setActiveTab('config')
    setTestMethod(route.method || 'GET')
    setTestPath(route.path || '/')
    setTestBody(route.responseBody ? '' : '')
    setTestHeaders('')
    setTestResult(null)
  }, [])

  const isDirty = useMemo(() => {
    if (!routeForm || !originalForm) return false
    const keys = ['name', 'method', 'path', 'enabled', 'statusCode', 'contentType',
      'responseBody', 'responseMode', 'matchMode', 'delayMs', 'proxyUrl', 'proxyModifyResponse', 'authType']
    for (const k of keys) {
      if (routeForm[k] !== originalForm[k]) return true
    }
    if (JSON.stringify(routeForm.responseHeaders) !== JSON.stringify(originalForm.responseHeaders)) return true
    if (JSON.stringify(routeForm.authConfig) !== JSON.stringify(originalForm.authConfig)) return true
    return false
  }, [routeForm, originalForm])

  const defaultRouteId = useMemo(() => {
    if (!routes.length) return null
    return routes.reduce((a, b) => new Date(a.createdAt) < new Date(b.createdAt) ? a : b).id
  }, [routes])

  const isDefault = routeForm && routeForm.id === defaultRouteId

  const handleCreateRoute = async () => {
    try {
      const n = routes.length + 1
      const body = {
        name: `路由 ${n}`,
        method: 'GET',
        path: `/mock/api-${n}`,
        statusCode: 200,
        contentType: 'application/json',
        responseBody: '{"code":0,"message":"success","data":null}',
      }
      const r = await api.post('/api-mock/routes', body)
      const d = r.data || r
      message.success('路由已创建，请修改路径和响应')
      await fetchRoutes()
      selectRoute(d)
    } catch {}
  }

  // 返回是否保存成功 —— 高级设置抽屉据此决定关不关（失败就别关，改动留着）
  const handleSaveRoute = async () => {
    if (!routeForm) return false
    setSaving(true)
    try {
      await api.put(`/api-mock/routes/${routeForm.id}`, routeForm)
      message.success('已保存')
      await fetchRoutes()
      setOriginalForm({ ...routeForm })
      return true
    } catch { return false } finally { setSaving(false) }
  }

  const handleDeleteRoute = async (id) => {
    try {
      await api.delete(`/api-mock/routes/${id}`)
      message.success('已删除')
      if (selectedRouteId === id) { setSelectedRouteId(null); setRouteForm(null); setOriginalForm(null) }
      await fetchRoutes()
    } catch {}
  }

  const handleToggle = async (id, checked) => {
    try {
      await api.patch(`/api-mock/routes/${id}/toggle`)
      await fetchRoutes()
      if (routeForm && routeForm.id === id) {
        setRouteForm(f => ({ ...f, enabled: checked }))
        setOriginalForm(f => ({ ...f, enabled: checked }))
      }
    } catch {}
  }

  const handleToggleLock = async () => {
    if (!routeForm) return
    try {
      const r = await api.patch(`/api-mock/routes/${routeForm.id}/lock`)
      const d = r.data || r
      message.success(d.locked ? '路由已锁定，需解锁后才能编辑' : '路由已解锁')
      setRouteForm(f => ({ ...f, locked: d.locked }))
      setOriginalForm(f => ({ ...f, locked: d.locked }))
      await fetchRoutes()
    } catch {}
  }

  // 拖动调整路由顺序：本地乐观更新 + 持久化 sort_order
  const handleDropRoute = async (targetIdx) => {
    const from = dragIdx
    setDragIdx(null)
    if (from === null || from === targetIdx) return
    const next = [...routes]
    const [moved] = next.splice(from, 1)
    next.splice(targetIdx, 0, moved)
    setRoutes(next)
    try {
      await api.put('/api-mock/routes/reorder', {
        items: next.map((r, i) => ({ id: r.id, sortOrder: i })),
      })
      await fetchRoutes()
    } catch { await fetchRoutes() }
  }

  const handlePresetChange = async (key) => {
    if (!routeForm) return
    setSelectedPreset(key)
    if (key.startsWith('custom:')) {
      const cp = customPresets.find(p => `custom:${p.id}` === key)
      if (cp && cp.config) {
        setRouteForm(f => ({
          ...f,
          statusCode: cp.config.statusCode ?? cp.config.status_code ?? f.statusCode,
          contentType: cp.config.contentType ?? cp.config.content_type ?? f.contentType,
          responseBody: cp.config.responseBody ?? cp.config.response_body ?? f.responseBody,
          responseHeaders: cp.config.responseHeaders ?? cp.config.response_headers ?? f.responseHeaders,
        }))
      }
      return
    }
    try {
      const r = await api.get(`/api-mock/presets/${key}`)
      const p = r.data?.data || r.data || r
      setRouteForm(f => ({
        ...f,
        statusCode: p.status_code ?? p.statusCode ?? f.statusCode,
        contentType: p.content_type ?? p.contentType ?? f.contentType,
        responseBody: p.response_body ?? p.responseBody ?? f.responseBody,
        responseHeaders: p.response_headers ?? p.responseHeaders ?? f.responseHeaders,
      }))
    } catch {}
  }

  const handleSaveCustomPreset = async () => {
    if (!routeForm || !savePresetName.trim()) return
    try {
      await api.post('/api-mock/custom-presets', {
        name: savePresetName.trim(),
        config: {
          statusCode: routeForm.statusCode,
          contentType: routeForm.contentType,
          responseBody: routeForm.responseBody,
          responseHeaders: routeForm.responseHeaders,
        }
      })
      message.success('预设已保存')
      setSavePresetOpen(false)
      setSavePresetName('')
      fetchCustomPresets()
    } catch {}
  }

  const handleDeleteCustomPreset = async (e, id) => {
    e.stopPropagation()
    try {
      await api.delete(`/api-mock/custom-presets/${id}`)
      message.success('预设已删除')
      fetchCustomPresets()
    } catch {}
  }

  const handleToggleService = async () => {
    try {
      if (serviceStatus.running) {
        await api.post('/api-mock/stop')
        message.success('API Mock 服务已停止')
      } else {
        await api.post('/api-mock/start')
        message.success('API Mock 服务已启动')
      }
      setTimeout(fetchStatus, 500)
    } catch (e) {
      message.error(`操作失败: ${e?.response?.data?.error || e?.response?.data?.detail || e.message || '未知错误'}`)
    }
  }

  const handleClearLogs = async () => {
    try {
      await api.delete('/api-mock/logs')
      message.success('日志已清空')
      setExpandedLogId(null)
      setExpandedLogDetail(null)
      setLogs([])
      setLogsTotal(0)
      setLogPage(1)
      fetchLogs(1)
    } catch {}
  }

  const handleReplay = async (logId) => {
    try {
      const r = await api.post(`/api-mock/logs/${logId}/replay`)
      message.success(`回放完成: ${(r.data || r).status_code}`)
      fetchLogs()
    } catch {}
  }

  const handleExportLogs = () => window.open('/api/api-mock/logs/export', '_blank')

  const handleOpenLogDetail = async (logId) => {
    setExpandedLogId(logId)
    setExpandedLogDetail(null)
    setLogDrawerOpen(true)
    setLogDetailLoading(true)
    try {
      const r = await api.get(`/api-mock/logs/${logId}`)
      setExpandedLogDetail(r.data || r)
    } catch {} finally { setLogDetailLoading(false) }
  }

  const handleCloseLogDrawer = () => {
    setLogDrawerOpen(false)
    setExpandedLogId(null)
    setExpandedLogDetail(null)
  }

  const handleOpenAdvanced = () => {
    setAdvancedSnapshot(routeForm ? { ...routeForm } : null)
    setAdvancedOpen(true)
  }

  // 保存整条路由后关闭抽屉；失败则留在抽屉里，改动不丢
  const handleSaveAdvanced = async () => {
    if (await handleSaveRoute()) setAdvancedOpen(false)
  }

  // 「取消」= 丢弃抽屉里的改动。点 X / 遮罩关闭则保留，主界面的保存按钮会亮着提示
  const handleCancelAdvanced = () => {
    if (advancedSnapshot) setRouteForm(advancedSnapshot)
    setAdvancedOpen(false)
  }

  const handleCopyPreview = () => {
    const body = routeForm?.responseBody || ''
    copyToClipboard(body).then(() => {
      setCopyText('已复制 ✓')
      setTimeout(() => setCopyText('复制'), 1500)
    })
  }

  const responseModeValue = routeForm?.responseMode || 'default'
  const isEchoMode = responseModeValue === 'echo' || responseModeValue === 'echo_body'
  const topResponseMode = isEchoMode ? 'echo' : responseModeValue
  const locked = !!routeForm?.locked

  const formatBody = (body, ct) => {
    if (!body) return ''
    if (ct?.includes('json')) {
      try { return JSON.stringify(JSON.parse(body), null, 2) } catch { return body }
    }
    return body
  }

  // ─── 渲染：路由配置 Tab ───
  const renderConfigTab = () => {
    if (!routeForm) {
      return (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
          <Empty description={<span style={{ color: '#c9cdd4' }}>选择左侧路由查看配置</span>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
        </div>
      )
    }
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* 路由头部 */}
        <div style={{
          padding: '10px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {isDefault && <Tag color="default" style={{ margin: 0, fontSize: 11 }}>默认</Tag>}
            {locked && <Tag color="orange" icon={<LockFilled />} style={{ margin: 0, fontSize: 11 }}>已锁定</Tag>}
            <Input
              value={routeForm.name}
              onChange={e => setRouteForm(f => ({ ...f, name: e.target.value }))}
              variant="borderless"
              disabled={locked}
              style={{ fontSize: 15, fontWeight: 600, width: 200, padding: '0 4px' }}
              placeholder="路由名称"
            />
          </div>
          <Space size={8}>
            <Button type="primary" icon={<SaveOutlined />} size="small" onClick={handleSaveRoute} loading={saving} disabled={!isDirty || locked}>保存</Button>
            <Switch
              checked={routeForm.enabled}
              onChange={(v) => handleToggle(routeForm.id, v)}
              disabled={locked}
              checkedChildren="启用" unCheckedChildren="禁用" size="small"
            />
            <Tooltip title={locked ? '解锁后可编辑' : '锁定后不可编辑，需先解锁'}>
              <Button
                size="small"
                icon={locked ? <UnlockOutlined /> : <LockOutlined />}
                onClick={handleToggleLock}
                type={locked ? 'primary' : 'default'}
                ghost={locked}
              >
                {locked ? '解锁' : '锁定'}
              </Button>
            </Tooltip>
            <Tooltip title={locked ? '已锁定，请先解锁' : ''}>
              <Button size="small" onClick={handleOpenAdvanced} disabled={locked}>高级</Button>
            </Tooltip>
            {isDefault ? (
              <Tooltip title="默认路由不可删除"><Button icon={<DeleteOutlined />} size="small" disabled /></Tooltip>
            ) : locked ? (
              <Tooltip title="已锁定，请先解锁"><Button icon={<DeleteOutlined />} size="small" danger disabled /></Tooltip>
            ) : (
              <Popconfirm title="确认删除？" onConfirm={() => handleDeleteRoute(routeForm.id)}>
                <Button icon={<DeleteOutlined />} size="small" danger />
              </Popconfirm>
            )}
          </Space>
        </div>

        {/* 可滚动配置区 */}
        <div style={{ flex: 1, overflow: 'auto', padding: '14px 16px' }}>
          {locked && (
            <Alert
              type="warning" showIcon icon={<LockFilled />}
              message="此路由已锁定，配置为只读。点击右上角「解锁」后才能编辑。"
              style={{ fontSize: 12, marginBottom: 14 }}
            />
          )}
          {/* URL 栏 */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 0, marginBottom: 16,
            border: '1px solid rgba(0,0,0,0.08)', borderRadius: 12, overflow: 'hidden', background: 'transparent',
          }}>
            <Select
              value={routeForm.method}
              onChange={v => setRouteForm(f => ({ ...f, method: v }))}
              variant="borderless"
              disabled={locked}
              style={{ width: 100, flexShrink: 0 }}
              popupMatchSelectWidth={100}
            >
              {['ANY', 'GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'].map(m => (
                <Select.Option key={m} value={m}>
                  <span style={{ fontWeight: 600, color: METHOD_COLOR(m) }}>{m}</span>
                </Select.Option>
              ))}
            </Select>
            <div style={{ width: 1, height: 24, background: 'rgba(0,0,0,0.08)', flexShrink: 0 }} />
            <Input spellCheck={false}
              value={routeForm.path}
              onChange={e => setRouteForm(f => ({ ...f, path: e.target.value }))}
              variant="borderless"
              disabled={locked}
              style={{ fontFamily: MONO, fontSize: 13, background: 'transparent' }}
              placeholder="/mock/api-example"
            />
          </div>

          {/* 响应模式 + 快速预设 + 状态码 + Content-Type — 紧凑两行 */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>响应模式</div>
              <Radio.Group
                value={topResponseMode}
                onChange={e => {
                  const v = e.target.value
                  setRouteForm(f => ({ ...f, responseMode: v === 'echo' ? 'echo' : v }))
                }}
                disabled={locked}
                buttonStyle="solid" size="small"
              >
                <Radio.Button value="default">默认</Radio.Button>
                <Radio.Button value="random">随机</Radio.Button>
                <Radio.Button value="custom">自定义</Radio.Button>
                <Radio.Button value="echo">回显</Radio.Button>
              </Radio.Group>
            </div>
            <div style={{ minWidth: 80 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>状态码</div>
              <InputNumber
                value={routeForm.statusCode ?? 200}
                onChange={v => setRouteForm(f => ({ ...f, statusCode: v }))}
                min={100} max={599} size="small" style={{ width: 80 }}
                disabled={locked}
              />
            </div>
            <div style={{ minWidth: 130 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Content-Type</div>
              <Select
                value={routeForm.contentType || 'application/json'}
                onChange={v => setRouteForm(f => ({ ...f, contentType: v }))}
                size="small" style={{ width: 130 }}
                disabled={locked}
                showSearch options={CONTENT_TYPES}
              />
            </div>
            <div style={{ flex: 1, minWidth: 180 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>快速预设</div>
              <Select
                onChange={handlePresetChange}
                placeholder="选择预设填充..."
                size="small" style={{ width: '100%' }}
                value={selectedPreset}
                disabled={locked}
              >
                <Select.OptGroup label="JSON">
                  {presets.filter(p => p.group === 'json').map(p =>
                    <Select.Option key={p.key} value={p.key}>{p.label}</Select.Option>)}
                </Select.OptGroup>
                <Select.OptGroup label="XML">
                  {presets.filter(p => p.group === 'xml').map(p =>
                    <Select.Option key={p.key} value={p.key}>{p.label}</Select.Option>)}
                </Select.OptGroup>
                <Select.OptGroup label="HTML">
                  {presets.filter(p => p.group === 'html').map(p =>
                    <Select.Option key={p.key} value={p.key}>{p.label}</Select.Option>)}
                </Select.OptGroup>
                <Select.OptGroup label="Text">
                  {presets.filter(p => p.group === 'text').map(p =>
                    <Select.Option key={p.key} value={p.key}>{p.label}</Select.Option>)}
                </Select.OptGroup>
                <Select.OptGroup label="HTTP 状态码">
                  {presets.filter(p => p.group === 'status').map(p =>
                    <Select.Option key={p.key} value={p.key}>{p.label}</Select.Option>)}
                </Select.OptGroup>
                {customPresets.length > 0 && (
                  <Select.OptGroup label="自定义预设">
                    {customPresets.map(p =>
                      <Select.Option key={`custom:${p.id}`} value={`custom:${p.id}`}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span><StarOutlined style={{ color: '#faad14', marginRight: 4, fontSize: 11 }} />{p.name}</span>
                          <DeleteOutlined style={{ color: '#e8453c', fontSize: 11 }} onClick={(e) => handleDeleteCustomPreset(e, p.id)} />
                        </div>
                      </Select.Option>)}
                  </Select.OptGroup>
                )}
              </Select>
            </div>
          </div>

          {/* 延迟 + 匹配模式 */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>延迟 (ms)</div>
              <InputNumber value={routeForm.delayMs ?? 0} onChange={v => setRouteForm(f => ({ ...f, delayMs: v }))}
                min={0} step={100} size="small" style={{ width: 80 }} placeholder="0" disabled={locked} />
            </div>
            <div>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>匹配模式</div>
              <Radio.Group value={routeForm.matchMode || 'exact'} onChange={e => setRouteForm(f => ({ ...f, matchMode: e.target.value }))} size="small" disabled={locked}>
                <Radio value="exact">精确</Radio>
                <Radio value="prefix">前缀</Radio>
                <Radio value="regex">正则</Radio>
              </Radio.Group>
            </div>
            <div style={{ fontSize: 11, color: '#c9cdd4', alignSelf: 'center', paddingBottom: 2 }}>
              {(routeForm.matchMode || 'exact') === 'exact' && '请求路径完全一致才匹配'}
              {routeForm.matchMode === 'prefix' && '路径前缀匹配（如 /api 匹配 /api/xxx）'}
              {routeForm.matchMode === 'regex' && '路径正则匹配（如 /api/users/\\d+）'}
            </div>
          </div>

          {/* 随机模式提示 */}
          {responseModeValue === 'random' && (
            <Alert
              type="info" showIcon
              message="随机模式：每次请求从内置模板池随机选取一条响应返回"
              style={{ fontSize: 12, marginBottom: 16 }}
            />
          )}

          {/* 回显模式：子选项 + 提示 */}
          {isEchoMode && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>回显内容</div>
                <Radio.Group
                  value={responseModeValue}
                  onChange={e => setRouteForm(f => ({ ...f, responseMode: e.target.value }))}
                  disabled={locked}
                  buttonStyle="solid" size="small"
                >
                  <Radio.Button value="echo">完整请求</Radio.Button>
                  <Radio.Button value="echo_body">仅请求体</Radio.Button>
                </Radio.Group>
              </div>
              <Alert
                type="info" showIcon
                message={responseModeValue === 'echo_body'
                  ? '回显请求体：原样返回收到的请求体，Content-Type 跟随请求（状态码仍按上方设置）'
                  : '回显完整请求：以 JSON 返回本次请求的 method / path / query / headers / body / ip'}
                style={{ fontSize: 12 }}
              />
            </div>
          )}

          {/* 响应内容 + 预览 — 左右分栏 */}
          {responseModeValue !== 'random' && !isEchoMode && (
            <div style={{ display: 'flex', gap: 12, minHeight: 0 }}>
              {/* 左：响应体编辑 */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                  <span style={{ fontSize: 12, color: '#86909c', fontWeight: 500 }}>响应内容</span>
                  <span style={{ fontSize: 11, color: '#c9cdd4' }}>
                    {'模板: ${method} ${path} ${timestamp} ${uuid}'}
                  </span>
                  <span style={{ flex: 1 }} />
                  <Button size="small" icon={<StarOutlined />}
                    onClick={() => { setSavePresetName(''); setSavePresetOpen(true) }}>保存为预设</Button>
                </div>
                <TextArea spellCheck={false}
                  value={routeForm.responseBody}
                  onChange={e => setRouteForm(f => ({ ...f, responseBody: e.target.value }))}
                  disabled={locked}
                  style={{ fontFamily: MONO, fontSize: 12, flex: 1, minHeight: 200, resize: 'vertical' }}
                  placeholder={'输入响应内容...\n\n例如:\n{"code":0,"message":"success","data":null}'}
                />
              </div>

              {/* 右：响应预览 */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 12, color: '#86909c', fontWeight: 500 }}>响应预览</span>
                    <Tag color={(routeForm.statusCode ?? 200) < 400 ? 'cyan' : 'red'} style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 4px' }}>
                      {routeForm.statusCode ?? 200}
                    </Tag>
                    <Tag color={CT_COLOR(routeForm.contentType)} style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 4px' }}>
                      {CT_SHORT(routeForm.contentType)}
                    </Tag>
                  </div>
                  <Button
                    size="small" type="text"
                    icon={copyText === '复制' ? <CopyOutlined /> : <CheckOutlined />}
                    onClick={handleCopyPreview}
                    style={{ color: copyText === '复制' ? '#86909c' : '#0ea5a0', fontSize: 12 }}
                  >
                    {copyText}
                  </Button>
                </div>
                <pre style={{
                  ...CODE_BLOCK_STYLE,
                  margin: 0, padding: 14, flex: 1, minHeight: 200, overflow: 'auto',
                  fontSize: 12, lineHeight: 1.6,
                  whiteSpace: 'pre-wrap', wordBreak: 'break-all', borderRadius: 12,
                }}>
                  {formatBody(routeForm.responseBody, routeForm.contentType)}
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>
    )
  }

  // ─── 渲染：测试 Tab ───
  const handleTest = async () => {
    if (!serviceStatus.running) return
    setTesting(true)
    setTestResult(null)
    try {
      let headers = {}
      if (testHeaders.trim()) {
        try { headers = JSON.parse(testHeaders) } catch { message.error('Headers JSON 格式错误'); setTesting(false); return }
      }
      const r = await api.post('/api-mock/test', {
        method: testMethod,
        path: testPath,
        body: testBody || '',
        headers,
      })
      setTestResult(r.data || r)
      fetchLogs()
    } catch (e) {
      setTestResult({ error: e?.response?.data?.error || e.message })
    } finally { setTesting(false) }
  }

  const renderTestTab = () => {
    if (!routeForm) {
      return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
        <Empty description={<span style={{ color: '#c9cdd4' }}>选择左侧路由进行测试</span>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </div>
    }
    const baseUrl = `http://${window.location.hostname}:${serviceStatus.port}`
    const fullUrl = `${baseUrl}${testPath}`
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div style={{ flex: 1, overflow: 'auto', padding: '14px 16px' }}>
          {/* URL display */}
          <div style={{ marginBottom: 16, padding: '10px 14px', borderRadius: 12, background: 'rgba(124,92,191,0.04)', border: '1px solid rgba(124,92,191,0.12)' }}>
            <div style={{ fontSize: 11, color: '#86909c', marginBottom: 6 }}>请求 URL</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <code style={{ fontFamily: MONO, fontSize: 13, color: '#7c5cbf', fontWeight: 500 }}>{fullUrl}</code>
              <Button size="small" type="text" icon={<CopyOutlined />} onClick={() => { copyToClipboard(fullUrl); message.success('已复制') }} />
            </div>
          </div>

          {/* Method + Path */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
            <div style={{ width: 100 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Method</div>
              <Select value={testMethod} onChange={v => setTestMethod(v)} size="small" style={{ width: '100%' }}>
                {['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'].map(m => (
                  <Select.Option key={m} value={m}><span style={{ color: METHOD_COLOR(m), fontWeight: 600 }}>{m}</span></Select.Option>
                ))}
              </Select>
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Path</div>
              <Input spellCheck={false} value={testPath} onChange={e => setTestPath(e.target.value)}
                style={{ fontFamily: MONO, fontSize: 12 }} placeholder="/api/users" size="small" />
            </div>
          </div>

          {/* Headers */}
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Headers (JSON, 可选)</div>
            <TextArea spellCheck={false}
              value={testHeaders}
              onChange={e => setTestHeaders(e.target.value)}
              rows={2}
              style={{ fontFamily: MONO, fontSize: 12 }}
              placeholder='{"Authorization": "Bearer xxx"}'
            />
          </div>

          {/* Body */}
          {!['GET', 'HEAD', 'OPTIONS'].includes(testMethod) && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Request Body</div>
              <TextArea spellCheck={false}
                value={testBody}
                onChange={e => setTestBody(e.target.value)}
                rows={4}
                style={{ fontFamily: MONO, fontSize: 12 }}
                placeholder='{"key": "value"}'
              />
            </div>
          )}

          {/* Send button */}
          <div style={{ marginBottom: 16 }}>
            <Button
              type="primary" icon={<SendOutlined />}
              loading={testing} onClick={handleTest}
              disabled={!serviceStatus.running}
            >
              {serviceStatus.running ? '发送请求' : '服务未启动'}
            </Button>
            {!serviceStatus.running && (
              <span style={{ marginLeft: 8, fontSize: 12, color: '#ff7d00' }}>请先启动 HTTP Mock 服务</span>
            )}
          </div>

          {/* curl hint */}
          <div style={{ marginBottom: 16, padding: '8px 12px', borderRadius: 10, background: 'rgba(0,0,0,0.02)', border: '1px solid rgba(0,0,0,0.04)' }}>
            <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>命令行测试 (curl)</div>
            <code style={{ fontSize: 11, fontFamily: MONO, color: '#4e5969', wordBreak: 'break-all' }}>
              curl {testMethod !== 'GET' ? `-X ${testMethod} ` : ''}{fullUrl}{testBody ? ` -H "Content-Type: application/json" -d '${testBody}'` : ''}
            </code>
            <Button size="small" type="text" icon={<CopyOutlined />} style={{ marginLeft: 4 }}
              onClick={() => {
                const cmd = `curl ${testMethod !== 'GET' ? `-X ${testMethod} ` : ''}${fullUrl}${testBody ? ` -H "Content-Type: application/json" -d '${testBody}'` : ''}`
                copyToClipboard(cmd)
                message.success('已复制')
              }}
            />
          </div>

          {/* Result */}
          {testResult && (
            <div>
              {testResult.error ? (
                <div>
                  <div style={{ fontSize: 12, color: '#86909c', fontWeight: 500, marginBottom: 6 }}>错误</div>
                  <pre style={{
                    background: 'var(--red-bg)', color: '#e8453c', padding: 12, borderRadius: 12,
                    overflow: 'auto', fontSize: 11, lineHeight: 1.5, maxHeight: 200,
                    fontFamily: MONO, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                    border: '1px solid #ffccc7',
                  }}>{testResult.error}</pre>
                </div>
              ) : (
                <div style={{ border: '1px solid rgba(0,0,0,0.06)', borderRadius: 12, overflow: 'hidden' }}>
                  {/* Header bar */}
                  <div style={{
                    padding: '8px 14px', background: 'rgba(0,0,0,0.02)',
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    borderBottom: '1px solid rgba(0,0,0,0.04)',
                  }}>
                    <Space size={8}>
                      <span style={{ fontSize: 12, fontWeight: 600, color: '#1d2129' }}>请求 / 响应详情</span>
                      {testResult.response?.status_code != null && (
                        <Tag color={testResult.response.status_code < 400 ? 'green' : 'red'} style={{ margin: 0, fontSize: 11, borderRadius: 8 }}>
                          {testResult.response.status_code}
                        </Tag>
                      )}
                      {testResult.duration_ms != null && (
                        <span style={{ fontSize: 11, color: '#86909c' }}>{testResult.duration_ms}ms</span>
                      )}
                    </Space>
                    <Button size="small" icon={<CopyOutlined />} onClick={() => {
                      const req = testResult.request || {}
                      const res = testResult.response || {}
                      const text = [
                        `--- REQUEST ---`,
                        `${req.method || ''} ${req.url || ''}`,
                        ...(req.headers ? Object.entries(req.headers).map(([k, v]) => `${k}: ${v}`) : []),
                        '',
                        req.body || '',
                        '',
                        `--- RESPONSE ---`,
                        `Status: ${res.status_code || ''}`,
                        ...(res.headers ? Object.entries(res.headers).map(([k, v]) => `${k}: ${v}`) : []),
                        '',
                        typeof res.body === 'object' ? JSON.stringify(res.body, null, 2) : (res.body || ''),
                      ].join('\n')
                      copyToClipboard(text)
                      message.success('已复制完整请求/响应')
                    }}>复制全部</Button>
                  </div>

                  {/* Request section */}
                  <div style={{ padding: '10px 14px', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: '#7c5cbf', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 1 }}>Request</div>
                    <div style={{ fontFamily: MONO, fontSize: 12, color: '#1d2129', marginBottom: 6 }}>
                      <span style={{ color: METHOD_COLOR(testResult.request?.method), fontWeight: 600 }}>{testResult.request?.method}</span>
                      {' '}<span>{testResult.request?.url}</span>
                    </div>
                    {testResult.request?.headers && (
                      <pre style={{
                        background: 'rgba(0,0,0,0.02)', color: '#4e5969', padding: 8, borderRadius: 8,
                        fontSize: 11, fontFamily: MONO, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                        border: '1px solid rgba(0,0,0,0.04)', maxHeight: 100, overflow: 'auto', margin: '0 0 6px 0',
                      }}>{Object.entries(testResult.request.headers).map(([k, v]) => `${k}: ${v}`).join('\n')}</pre>
                    )}
                    {testResult.request?.body && (
                      <pre style={{
                        background: 'rgba(0,0,0,0.02)', color: '#4e5969', padding: 8, borderRadius: 8,
                        fontSize: 11, fontFamily: MONO, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                        border: '1px solid rgba(0,0,0,0.04)', maxHeight: 120, overflow: 'auto', margin: 0,
                      }}>{testResult.request.body}</pre>
                    )}
                  </div>

                  {/* Response section */}
                  <div style={{ padding: '10px 14px' }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: '#0ea5a0', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 1 }}>Response</div>
                    {testResult.response?.headers && (
                      <pre style={{
                        background: 'rgba(0,0,0,0.02)', color: '#4e5969', padding: 8, borderRadius: 8,
                        fontSize: 11, fontFamily: MONO, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                        border: '1px solid rgba(0,0,0,0.04)', maxHeight: 100, overflow: 'auto', margin: '0 0 6px 0',
                      }}>{Object.entries(testResult.response.headers).map(([k, v]) => `${k}: ${v}`).join('\n')}</pre>
                    )}
                    <pre style={{
                      ...CODE_BLOCK_STYLE, padding: 12, borderRadius: 10,
                      overflow: 'auto', fontSize: 11, lineHeight: 1.5, maxHeight: 250,
                      whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0,
                    }}>{typeof testResult.response?.body === 'object' ? JSON.stringify(testResult.response.body, null, 2) : (testResult.response?.body || '(empty)')}</pre>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    )
  }

  // ─── 渲染：请求日志 Tab ───
  const renderLogsTab = () => (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{
        padding: '8px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0,
      }}>
        <span style={{ fontSize: 13, fontWeight: 500, color: '#1d2129' }}>共 {logsTotal} 条</span>
        <Space size={4}>
          <Radio.Group value={logFilter} onChange={e => setLogFilter(e.target.value)} size="small">
            <Radio.Button value="all">全部</Radio.Button>
            <Radio.Button value="ok">OK</Radio.Button>
            <Radio.Button value="error">Error</Radio.Button>
          </Radio.Group>
          <Button icon={<ReloadOutlined />} size="small" type="text" onClick={() => fetchLogs()} />
          <Button icon={<ExportOutlined />} size="small" type="text" onClick={handleExportLogs} />
          <Popconfirm title="确认清空所有日志？" onConfirm={handleClearLogs}>
            <Button icon={<ClearOutlined />} size="small" type="text" danger />
          </Popconfirm>
        </Space>
      </div>

      <div style={{ flex: 1, overflow: 'auto' }}>
        <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: 'rgba(255,255,255,0.45)', position: 'sticky', top: 0, zIndex: 1 }}>
              {['时间', '状态', '方法', '路径', '类型', '耗时', ''].map((h, i) => (
                <th key={h || 'op'} style={{
                  padding: '6px 10px', textAlign: i >= 5 ? 'right' : 'left',
                  fontWeight: 500, fontSize: 11, color: '#86909c', borderBottom: '1px solid rgba(0,0,0,0.04)',
                  whiteSpace: 'nowrap',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {logs.map(l => (
              <Fragment key={l.id}>
                <tr onClick={() => handleOpenLogDetail(l.id)} style={{
                  cursor: 'pointer', borderBottom: '1px solid rgba(0,0,0,0.03)', background: 'rgba(255,255,255,0.25)',
                  background: expandedLogId === l.id ? 'rgba(124,92,191,0.06)' : 'transparent',
                }}>
                  <td style={{ padding: '5px 10px', whiteSpace: 'nowrap', fontSize: 11, color: '#86909c' }}>
                    {new Date(l.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}
                  </td>
                  <td style={{ padding: '5px 10px' }}>
                    <span style={{ fontSize: 11, fontWeight: 600, color: STATUS_COLOR(l.statusCode) }}>{l.statusCode}</span>
                  </td>
                  <td style={{ padding: '5px 10px' }}>
                    <span style={{ fontSize: 11, fontWeight: 600, color: METHOD_COLOR(l.method) }}>{l.method}</span>
                  </td>
                  <td style={{ padding: '5px 10px', fontFamily: MONO, fontSize: 11, color: '#4e5969', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{l.path}</td>
                  <td style={{ padding: '5px 10px', fontSize: 11, color: '#86909c' }}>{CT_SHORT(l.contentType)}</td>
                  <td style={{ padding: '5px 10px', textAlign: 'right', fontSize: 11, color: '#86909c', whiteSpace: 'nowrap' }}>{Math.round(l.totalMs ?? 0)}ms</td>
                  <td style={{ padding: '5px 10px', textAlign: 'right' }}>
                    <Button size="small" type="text" icon={<SendOutlined />} onClick={e => { e.stopPropagation(); handleReplay(l.id) }} />
                  </td>
                </tr>
              </Fragment>
            ))}
            {logs.length === 0 && (
              <tr><td colSpan={7} style={{ textAlign: 'center', padding: 40, color: '#c9cdd4', fontSize: 12 }}>暂无请求日志</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {logsTotal > logPageSize && (
        <div style={{ padding: '8px 16px', borderTop: '1px solid rgba(0,0,0,0.04)', flexShrink: 0, textAlign: 'right' }}>
          <Pagination size="small" current={logPage} pageSize={logPageSize} total={logsTotal}
            showTotal={t => `共 ${t} 条`} showSizeChanger={false}
            onChange={p => { setLogPage(p); setExpandedLogId(null); fetchLogs(p) }} />
        </div>
      )}
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: 'transparent' }}>

      {/* ━━━ 顶栏 ━━━ */}
      <div style={{
        padding: '10px 20px', background: 'transparent', borderBottom: '1px solid rgba(0,0,0,0.03)', background: 'rgba(255,255,255,0.25)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <CloudServerOutlined style={{ fontSize: 18, color: '#7c5cbf' }} />
            <span style={{ fontWeight: 600, fontSize: 16, letterSpacing: 0.5 }}>API Mock</span>
          </div>
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '2px 10px', borderRadius: 12,
            background: serviceStatus.running ? '#e0f7f6' : 'rgba(0,0,0,0.04)',
            border: `1px solid ${serviceStatus.running ? 'rgba(14,165,160,0.3)' : 'rgba(0,0,0,0.1)'}`,
          }}>
            <Badge status={serviceStatus.running ? 'success' : 'default'} />
            <span style={{
              fontSize: 12, fontWeight: 600, fontFamily: 'var(--font-mono)',
              color: serviceStatus.running ? '#0ea5a0' : '#999',
            }}>
              {serviceStatus.running ? `LIVE :${serviceStatus.port}` : 'STOPPED'}
            </span>
          </div>
          <span style={{ fontSize: 12, color: '#86909c' }}>
            {serviceStatus.routesEnabled}/{serviceStatus.routesCount} 路由 · {serviceStatus.totalRequests} 请求
          </span>
        </div>
        <Space size={8}>
          {serviceStatus.running && (
            <Button size="small" icon={<CopyOutlined />} onClick={() => {
              copyToClipboard(`http://${window.location.hostname}:${serviceStatus.port}`)
              message.success('已复制端点地址')
            }}>复制端点</Button>
          )}
          <Button
            type={serviceStatus.running ? 'default' : 'primary'}
            danger={serviceStatus.running}
            icon={serviceStatus.running ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
            onClick={handleToggleService} size="small"
          >
            {serviceStatus.running ? '停止服务' : '启动服务'}
          </Button>
        </Space>
      </div>

      {/* ━━━ 主体：左侧路由列表 + 右侧 Tab(配置/日志) ━━━ */}
      <div style={{ flex: 1, display: 'flex', gap: 0, minHeight: 0 }}>

        {/* ── 左栏：路由列表 ── */}
        <div style={{
          width: 260, flexShrink: 0, background: 'transparent', borderRight: '1px solid rgba(0,0,0,0.05)',
          display: 'flex', flexDirection: 'column',
        }}>
          <div style={{
            padding: '10px 14px', borderBottom: '1px solid rgba(0,0,0,0.04)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span style={{ fontWeight: 600, fontSize: 13, color: '#1d2129' }}>路由</span>
            <Tooltip title="新建路由">
              <Button type="primary" ghost icon={<PlusOutlined />} size="small" onClick={handleCreateRoute} />
            </Tooltip>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: '6px 8px' }}>
            {routes.map((r, i) => {
              const sel = selectedRouteId === r.id
              const isDef = r.id === defaultRouteId
              const isDragging = dragIdx === i
              return (
                <div
                  key={r.id}
                  draggable
                  onClick={() => selectRoute(r)}
                  onDragStart={e => { e.dataTransfer.effectAllowed = 'move'; setDragIdx(i) }}
                  onDragOver={e => { e.preventDefault(); e.currentTarget.style.borderTop = '2px solid #7c5cbf' }}
                  onDragLeave={e => { e.currentTarget.style.borderTop = '2px solid transparent' }}
                  onDrop={e => { e.preventDefault(); e.currentTarget.style.borderTop = '2px solid transparent'; handleDropRoute(i) }}
                  onDragEnd={() => setDragIdx(null)}
                  style={{
                    padding: '10px 12px', marginBottom: 4, borderRadius: 12, cursor: 'pointer',
                    background: sel ? 'rgba(124,92,191,0.06)' : 'transparent',
                    borderLeft: `3px solid ${sel ? '#7c5cbf' : r.enabled ? '#0ea5a0' : 'rgba(0,0,0,0.1)'}`,
                    borderTop: '2px solid transparent',
                    opacity: isDragging ? 0.4 : 1,
                    transition: 'opacity .15s',
                  }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Tooltip title="拖动调整顺序">
                      <HolderOutlined style={{ fontSize: 11, color: '#c8c8c8', cursor: 'grab', flexShrink: 0 }} />
                    </Tooltip>
                    {isDef && <LockOutlined style={{ fontSize: 11, color: '#c9cdd4' }} />}
                    {r.locked && (
                      <Tooltip title="已锁定，不可编辑">
                        <LockFilled style={{ fontSize: 11, color: '#ff7d00', flexShrink: 0 }} />
                      </Tooltip>
                    )}
                    <Tag style={{
                      margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 4px', borderRadius: 8,
                      fontWeight: 600, color: METHOD_COLOR(r.method), borderColor: METHOD_COLOR(r.method),
                      background: 'transparent',
                    }}>{r.method}</Tag>
                    <span style={{
                      flex: 1, fontSize: 11, fontFamily: MONO,
                      color: '#4e5969', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{r.path}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 6 }}>
                    <span style={{ fontSize: 12, color: sel ? '#1d2129' : '#86909c', fontWeight: sel ? 500 : 400 }}>{r.name}</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <Tag style={{
                        margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 5px',
                        color: STATUS_COLOR(r.statusCode), borderColor: STATUS_COLOR(r.statusCode),
                        background: 'transparent', borderRadius: 8,
                      }}>{r.statusCode}</Tag>
                      <Tag color={CT_COLOR(r.contentType)} style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 4px', borderRadius: 6 }}>
                        {CT_SHORT(r.contentType)}
                      </Tag>
                      {r.authType && r.authType !== 'none' && (
                        <Tooltip title={`认证: ${r.authType}`}>
                          <LockOutlined style={{ fontSize: 11, color: '#ff7d00' }} />
                        </Tooltip>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
            {routes.length === 0 && (
              <Empty description="暂无路由" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ marginTop: 60 }} />
            )}
          </div>
        </div>

        {/* ── 右栏：Tab 切换（路由配置 / 请求日志） ── */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: 'transparent' }}>
          <div style={{ borderBottom: '1px solid rgba(0,0,0,0.04)', paddingLeft: 16, flexShrink: 0 }}>
            <div style={{ display: 'flex', gap: 0 }}>
              {[
                { key: 'config', label: '路由配置' },
                { key: 'test', label: <>测试 <SendOutlined style={{ fontSize: 11 }} /></> },
                { key: 'logs', label: <>请求日志 <Tag style={{ margin: '0 0 0 4px', fontSize: 11, borderRadius: 12, lineHeight: '18px', padding: '0 6px' }}>{serviceStatus.totalRequests}</Tag></> },
              ].map(t => (
                <div key={t.key} onClick={() => setActiveTab(t.key)} style={{
                  padding: '10px 16px', cursor: 'pointer', fontSize: 14, position: 'relative',
                  color: activeTab === t.key ? 'var(--primary)' : '#4e5969',
                  fontWeight: activeTab === t.key ? 500 : 400,
                  borderBottom: activeTab === t.key ? '2px solid var(--primary)' : '2px solid transparent',
                  marginBottom: -1,
                }}>
                  {t.label}
                </div>
              ))}
            </div>
          </div>
          <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
            {activeTab === 'config' ? renderConfigTab() : activeTab === 'test' ? renderTestTab() : renderLogsTab()}
          </div>
        </div>
      </div>

      {/* ━━━ 高级设置抽屉 ━━━ */}
      <Drawer
        open={advancedOpen}
        onClose={() => setAdvancedOpen(false)}
        width={420}
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span>高级设置</span>
            {isDirty && <Tag color="orange" style={{ margin: 0, fontSize: 11 }}>未保存</Tag>}
          </div>
        }
        footer={
          <div>
            <div style={{ fontSize: 11, color: '#86909c', marginBottom: 8, lineHeight: 1.6 }}>
              保存会提交这条路由的全部改动（含主界面上未保存的部分）。直接关闭窗口不会丢改动，回主界面还能保存。
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <Button size="small" onClick={handleCancelAdvanced}>取消</Button>
              <Button type="primary" size="small" icon={<SaveOutlined />}
                onClick={handleSaveAdvanced} loading={saving} disabled={!isDirty}>保存</Button>
            </div>
          </div>
        }
      >
        {routeForm && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            {/* 认证配置 */}
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: '#1d2129', marginBottom: 8 }}>请求认证</div>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 6 }}>认证方式</div>
              <Select value={routeForm.authType || 'none'} onChange={v => setRouteForm(f => ({ ...f, authType: v, authConfig: v === 'none' ? null : (f.authConfig || {}) }))}
                size="small" style={{ width: '100%' }}
                options={[
                  { value: 'none', label: '无认证' },
                  { value: 'bearer', label: 'Bearer Token' },
                  { value: 'basic', label: 'Basic Auth' },
                  { value: 'apikey', label: 'API Key' },
                  { value: 'jwt', label: 'JWT 验证' },
                  { value: 'custom_header', label: '自定义 Header' },
                ]} />
              {routeForm.authType === 'bearer' && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Token</div>
                  <Input spellCheck={false} value={routeForm.authConfig?.token || ''} onChange={e => setRouteForm(f => ({ ...f, authConfig: { ...f.authConfig, token: e.target.value } }))}
                    placeholder="输入 Bearer Token" style={{ fontFamily: MONO, fontSize: 12 }} />
                </div>
              )}
              {routeForm.authType === 'basic' && (
                <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>用户名</div>
                    <Input value={routeForm.authConfig?.username || ''} onChange={e => setRouteForm(f => ({ ...f, authConfig: { ...f.authConfig, username: e.target.value } }))}
                      placeholder="username" />
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>密码</div>
                    <Input.Password value={routeForm.authConfig?.password || ''} onChange={e => setRouteForm(f => ({ ...f, authConfig: { ...f.authConfig, password: e.target.value } }))}
                      placeholder="password" />
                  </div>
                </div>
              )}
              {routeForm.authType === 'apikey' && (
                <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Header 名称</div>
                    <Input value={routeForm.authConfig?.headerName || ''} onChange={e => setRouteForm(f => ({ ...f, authConfig: { ...f.authConfig, headerName: e.target.value } }))}
                      placeholder="X-API-Key" />
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Key 值</div>
                    <Input spellCheck={false} value={routeForm.authConfig?.key || ''} onChange={e => setRouteForm(f => ({ ...f, authConfig: { ...f.authConfig, key: e.target.value } }))}
                      placeholder="your-api-key" style={{ fontFamily: MONO, fontSize: 12 }} />
                  </div>
                </div>
              )}
              {routeForm.authType === 'jwt' && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Secret（HS256 签名验证，留空则只检查格式和过期时间）</div>
                  <Input spellCheck={false} value={routeForm.authConfig?.secret || ''} onChange={e => setRouteForm(f => ({ ...f, authConfig: { ...f.authConfig, secret: e.target.value } }))}
                    placeholder="your-jwt-secret（可选）" style={{ fontFamily: MONO, fontSize: 12 }} />
                  <div style={{ fontSize: 11, color: '#86909c', marginTop: 6 }}>验证逻辑：JWT 格式 → exp 过期检查 → 签名校验（如填了 secret）</div>
                </div>
              )}
              {routeForm.authType === 'custom_header' && (
                <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Header 名称</div>
                    <Input value={routeForm.authConfig?.headerName || ''} onChange={e => setRouteForm(f => ({ ...f, authConfig: { ...f.authConfig, headerName: e.target.value } }))}
                      placeholder="X-Custom-Auth" />
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Header 值</div>
                    <Input spellCheck={false} value={routeForm.authConfig?.headerValue || ''} onChange={e => setRouteForm(f => ({ ...f, authConfig: { ...f.authConfig, headerValue: e.target.value } }))}
                      placeholder="expected-value" style={{ fontFamily: MONO, fontSize: 12 }} />
                  </div>
                </div>
              )}
              {routeForm.authType && routeForm.authType !== 'none' && (
                <div style={{ fontSize: 11, color: '#0ea5a0', marginTop: 6 }}>请求必须携带正确的认证信息，否则返回 401</div>
              )}
            </div>

            {/* 代理转发 */}
            <div>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 6 }}>代理转发 URL</div>
              <Input spellCheck={false} value={routeForm.proxyUrl || ''} onChange={e => setRouteForm(f => ({ ...f, proxyUrl: e.target.value || null }))}
                placeholder="https://api.example.com/real-endpoint"
                prefix={<SendOutlined style={{ color: '#c9cdd4' }} />}
                style={{ fontFamily: MONO, fontSize: 12 }} />
              <div style={{ fontSize: 11, color: '#c9cdd4', marginTop: 4 }}>填写后请求将被转发到该地址，可用于接口代理调试</div>
            </div>
            {routeForm.proxyUrl && (
              <div>
                <Switch
                  checked={routeForm.proxyModifyResponse || false}
                  onChange={v => setRouteForm(f => ({ ...f, proxyModifyResponse: v }))}
                />
                <span style={{ marginLeft: 8, fontSize: 12, color: '#4e5969' }}>转发后用本地配置覆盖响应</span>
              </div>
            )}
            <div>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 6 }}>自定义响应头 (JSON)</div>
              <TextArea spellCheck={false}
                value={routeForm.responseHeaders ? JSON.stringify(routeForm.responseHeaders, null, 2) : ''}
                onChange={e => { try { setRouteForm(f => ({ ...f, responseHeaders: e.target.value ? JSON.parse(e.target.value) : null })) } catch {} }}
                rows={4}
                style={{ fontFamily: MONO, fontSize: 12 }}
                placeholder='{"X-Custom-Header": "value", "Cache-Control": "no-cache"}' />
            </div>
          </div>
        )}
      </Drawer>

      {/* ━━━ 请求日志详情抽屉 ━━━ */}
      <Drawer
        open={logDrawerOpen}
        onClose={handleCloseLogDrawer}
        width={680}
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 15, fontWeight: 600 }}>请求详情</span>
            {expandedLogDetail && (
              <>
                <span style={{ fontSize: 12, fontWeight: 700, color: METHOD_COLOR(expandedLogDetail.method) }}>{expandedLogDetail.method}</span>
                <span style={{ fontSize: 12, fontWeight: 700, color: STATUS_COLOR(expandedLogDetail.statusCode) }}>{expandedLogDetail.statusCode}</span>
                <span style={{ fontFamily: MONO, fontSize: 12, color: '#4e5969', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{expandedLogDetail.path}</span>
              </>
            )}
          </div>
        }
        extra={expandedLogDetail && (
          <Button size="small" icon={<SendOutlined />}
            onClick={() => handleReplay(expandedLogDetail.id)}>重放</Button>
        )}
      >
        {logDetailLoading && !expandedLogDetail ? (
          <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
        ) : expandedLogDetail ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 24px', fontSize: 12 }}>
              {[
                ['时间', new Date(expandedLogDetail.timestamp).toLocaleString('zh-CN', { hour12: false })],
                ['Content-Type', expandedLogDetail.contentType || '-'],
                ['来源 IP', expandedLogDetail.ip || '-'],
                ['来源', expandedLogDetail.caller || '-'],
                ['总耗时', `${Math.round(expandedLogDetail.totalMs ?? 0)} ms`],
                ['匹配耗时', `${(expandedLogDetail.matchMs ?? 0).toFixed(1)} ms`],
              ].map(([k, v]) => (
                <div key={k} style={{ display: 'flex', gap: 8, minWidth: 0 }}>
                  <span style={{ color: '#86909c', flexShrink: 0 }}>{k}</span>
                  <span style={{ color: '#1d2129', fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v}</span>
                </div>
              ))}
            </div>
            <LogBlock title="请求头" content={fmtHeaders(expandedLogDetail.requestHeaders)}
              onCopy={() => copyToClipboard(fmtHeaders(expandedLogDetail.requestHeaders)).then(() => message.success('已复制'))} />
            <LogBlock title="请求体" content={expandedLogDetail.requestBody || '-'}
              onCopy={() => copyToClipboard(expandedLogDetail.requestBody || '').then(() => message.success('已复制'))} />
            <LogBlock title="响应头" content={fmtHeaders(expandedLogDetail.responseHeadersOut)}
              onCopy={() => copyToClipboard(fmtHeaders(expandedLogDetail.responseHeadersOut)).then(() => message.success('已复制'))} />
            <LogBlock title="响应体" content={formatBody(expandedLogDetail.responseBody, expandedLogDetail.contentType)}
              onCopy={() => copyToClipboard(expandedLogDetail.responseBody || '').then(() => message.success('已复制'))} />
          </div>
        ) : (
          <Empty description="加载失败" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Drawer>

      <Modal title="保存为自定义预设" open={savePresetOpen}
        onOk={handleSaveCustomPreset} onCancel={() => setSavePresetOpen(false)}
        okText="保存" cancelText="取消" okButtonProps={{ disabled: !savePresetName.trim() }}>
        <div style={{ marginBottom: 8, fontSize: 13, color: '#86909c' }}>
          将当前响应配置（状态码、Content-Type、响应内容等）保存为预设，方便下次快速选用。
        </div>
        <Input placeholder="输入预设名称" value={savePresetName}
          onChange={e => setSavePresetName(e.target.value)}
          onPressEnter={handleSaveCustomPreset} autoFocus />
      </Modal>
    </div>
  )
}
