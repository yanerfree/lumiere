import { useState, useEffect, useRef, useMemo, useCallback, Fragment } from 'react'
import {
  Button, Space, Input, Select, Tag, Radio, Popconfirm, Tooltip, Badge, Pagination,
  Empty, Typography, InputNumber, Switch, message, Drawer, Alert, Modal
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, SaveOutlined, PlayCircleOutlined, PauseCircleOutlined,
  ReloadOutlined, ExportOutlined, ClearOutlined, CopyOutlined, ThunderboltOutlined,
  LockOutlined, SettingOutlined, CheckOutlined, SendOutlined, LinkOutlined, StarOutlined
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'

const { Text } = Typography
const { TextArea } = Input

const MONO = "'SF Mono', Monaco, Menlo, Consolas, monospace"

const STATUS_COLOR = (sc) => {
  if (sc >= 500) return '#e8453c'
  if (sc >= 400) return '#fa8c16'
  return '#0ea5a0'
}

const MODE_LABELS = { default: '默认响应', random: '随机响应', custom: '自定义' }
const MODE_COLORS = { default: 'blue', random: 'purple', custom: 'cyan' }

const NEW_ROUTE_PRESETS = [
  { name: '429 限频', path: '/mock-429/v1/chat/completions', presetMode: 'error_429_rpm', statusCode: 429, responseType: 'text', finishReason: 'stop', responseBody: 'Rate limit reached for gpt-4o on requests per min (RPM): Limit 500, Used 500, Requested 1.' },
  { name: '500 服务错误', path: '/mock-500/v1/chat/completions', presetMode: 'error_500', statusCode: 500, responseType: 'text', finishReason: 'stop', responseBody: 'The server had an error while processing your request. Sorry about that!' },
  { name: 'Tool Calls', path: '/mock-tools/v1/chat/completions', presetMode: 'normal_tool_calls', statusCode: 200, responseType: 'tool_calls', finishReason: 'tool_calls', responseBody: '', toolCalls: [{ name: 'get_weather', arguments: '{"location":"Beijing","unit":"celsius"}' }] },
  { name: '模型拒绝', path: '/mock-refusal/v1/chat/completions', presetMode: 'normal_refusal', statusCode: 200, responseType: 'refusal', finishReason: 'stop', responseBody: "I'm sorry, I can't assist with that request." },
  { name: '截断响应', path: '/mock-truncated/v1/chat/completions', presetMode: 'normal_length', statusCode: 200, responseType: 'text', finishReason: 'length', responseBody: 'This response was truncated because it reached the maximum token limit. The content is incomplete and ends mid-sentence, which is typical when the model hits max_tokens. The application should handle this by' },
  { name: '401 无效Key', path: '/mock-401/v1/chat/completions', presetMode: 'error_401_invalid_key', statusCode: 401, responseType: 'text', finishReason: 'stop', responseBody: 'Incorrect API key provided: sk-proj-****xxxx.' },
  { name: '向量 Embeddings', path: '/v1/embeddings', presetMode: 'normal_embedding', statusCode: 200, responseType: 'embedding', finishReason: 'stop', responseBody: '' },
  // Azure OpenAI：api-version=v1 走 /openai/v1/...，日期版本把部署名放在路径里
  { name: 'Azure Chat (api-version=v1)', path: '/openai/v1/chat/completions', presetMode: 'normal_text', statusCode: 200, responseType: 'text', finishReason: 'stop', responseBody: 'This is a mock response from the LLM Mock service.' },
  { name: 'Azure Chat (部署名通配)', path: '/openai/deployments/*/chat/completions', presetMode: 'normal_text', statusCode: 200, responseType: 'text', finishReason: 'stop', responseBody: 'This is a mock response from the LLM Mock service.' },
]

export default function LlmMock() {
  const [routes, setRoutes] = useState([])
  const [selectedRouteId, setSelectedRouteId] = useState(null)
  const [routeForm, setRouteForm] = useState(null)
  const [originalForm, setOriginalForm] = useState(null)
  const [presets, setPresets] = useState([])
  const [customPresets, setCustomPresets] = useState([])
  const [savePresetOpen, setSavePresetOpen] = useState(false)
  const [savePresetName, setSavePresetName] = useState('')
  const [logs, setLogs] = useState([])
  const [logsTotal, setLogsTotal] = useState(0)
  const [logPage, setLogPage] = useState(1)
  const [logPageSize] = useState(50)
  const [expandedLogId, setExpandedLogId] = useState(null)
  const [expandedLogDetail, setExpandedLogDetail] = useState(null)
  const [logFilter, setLogFilter] = useState('all')
  const [serviceStatus, setServiceStatus] = useState({ running: false, port: 28100, captureEnabled: true, routesCount: 0, routesEnabled: 0, totalRequests: 0 })
  const [saving, setSaving] = useState(false)
  const [activeTab, setActiveTab] = useState('config')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [copyText, setCopyText] = useState('复制')
  const pollRef = useRef(null)

  useEffect(() => {
    fetchRoutes()
    fetchPresets()
    fetchCustomPresets()
    fetchStatus()
    fetchLogs()
    pollRef.current = setInterval(() => { fetchStatus() }, 5000)
    return () => clearInterval(pollRef.current)
  }, [])

  const fetchRoutes = async () => { try { const r = await api.get('/llm-mock/routes'); setRoutes(r.data || r || []) } catch {} }
  const fetchPresets = async () => { try { const r = await api.get('/llm-mock/presets'); setPresets(r.data?.data || r.data || []) } catch {} }
  const fetchCustomPresets = async () => { try { const r = await api.get('/llm-mock/custom-presets'); setCustomPresets(r.data?.data || r.data || []) } catch {} }
  const fetchStatus = async () => { try { const r = await api.get('/llm-mock/status'); setServiceStatus(r.data || r) } catch {} }
  const fetchLogs = async (page) => {
    try {
      const p = page || logPage
      const params = new URLSearchParams({ limit: String(logPageSize), offset: String((p - 1) * logPageSize) })
      if (logFilter !== 'all') params.set('status', logFilter)
      const r = await api.get(`/llm-mock/logs?${params}`)
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
    setSelectedRouteId(route.id)
    setRouteForm({ ...route })
    setOriginalForm({ ...route })
    setActiveTab('config')
  }, [])

  const isDirty = useMemo(() => {
    if (!routeForm || !originalForm) return false
    const keys = ['name', 'method', 'path', 'enabled', 'statusCode', 'responseType', 'finishReason',
      'responseBody', 'responseMode', 'presetMode', 'delayMs', 'sseChunkDelayMs', 'tokenMode',
      'customPromptTokens', 'customCompletionTokens', 'modelMode', 'customModel', 'responseFormat']
    for (const k of keys) {
      if (routeForm[k] !== originalForm[k]) return true
    }
    if (JSON.stringify(routeForm.toolCalls) !== JSON.stringify(originalForm.toolCalls)) return true
    if (JSON.stringify(routeForm.responseHeaders) !== JSON.stringify(originalForm.responseHeaders)) return true
    return false
  }, [routeForm, originalForm])

  const defaultRouteId = useMemo(() => {
    if (!routes.length) return null
    return routes.reduce((a, b) => new Date(a.createdAt) < new Date(b.createdAt) ? a : b).id
  }, [routes])
  const isDefault = routeForm && routeForm.id === defaultRouteId

  // 路径里的通配/查询串：* 只吃一段、** 跨层级、? 后面整段在匹配时忽略
  const pathPattern = useMemo(() => {
    const [body = '', query] = (routeForm?.path || '').split('?')
    return {
      hasWildcard: body.includes('*'),
      hasQuery: query !== undefined,
      // 通配路径本身不能直接请求，给一个能跑的示例地址
      sample: body.replace(/\*+/g, 'gpt-4o-mini'),
    }
  }, [routeForm?.path])

  const fullUrl = useMemo(() => {
    if (!routeForm || !serviceStatus.running) return null
    return `http://${window.location.hostname}:${serviceStatus.port}${pathPattern.sample}`
  }, [routeForm, serviceStatus, pathPattern])

  const handleCreateRoute = async () => {
    try {
      const idx = routes.length % NEW_ROUTE_PRESETS.length
      const tpl = NEW_ROUTE_PRESETS[idx]
      const body = { method: 'POST', ...tpl }
      const r = await api.post('/llm-mock/routes', body)
      const d = r.data || r
      message.success('路由已创建')
      await fetchRoutes()
      selectRoute(d)
    } catch {}
  }

  const handleSaveRoute = async () => {
    if (!routeForm) return
    setSaving(true)
    try {
      await api.put(`/llm-mock/routes/${routeForm.id}`, routeForm)
      message.success('已保存')
      await fetchRoutes()
      setOriginalForm({ ...routeForm })
    } catch {} finally { setSaving(false) }
  }

  const handleDeleteRoute = async (id) => {
    try {
      await api.delete(`/llm-mock/routes/${id}`)
      message.success('已删除')
      if (selectedRouteId === id) { setSelectedRouteId(null); setRouteForm(null); setOriginalForm(null) }
      await fetchRoutes()
    } catch {}
  }

  const handleToggle = async (id, checked) => {
    try {
      await api.patch(`/llm-mock/routes/${id}/toggle`)
      await fetchRoutes()
      if (routeForm && routeForm.id === id) {
        setRouteForm(f => ({ ...f, enabled: checked }))
        setOriginalForm(f => ({ ...f, enabled: checked }))
      }
    } catch {}
  }

  const handlePresetChange = async (key) => {
    if (!routeForm) return
    if (key.startsWith('custom:')) {
      const cp = customPresets.find(p => `custom:${p.id}` === key)
      if (cp && cp.config) {
        setRouteForm(f => ({
          ...f, presetMode: key,
          statusCode: cp.config.statusCode ?? cp.config.status_code ?? f.statusCode,
          finishReason: cp.config.finishReason ?? cp.config.finish_reason ?? f.finishReason,
          responseType: cp.config.responseType ?? cp.config.response_type ?? f.responseType,
          responseBody: cp.config.responseBody ?? cp.config.response_body ?? f.responseBody,
          toolCalls: cp.config.toolCalls ?? cp.config.tool_calls ?? f.toolCalls,
          responseHeaders: cp.config.responseHeaders ?? cp.config.response_headers ?? f.responseHeaders,
        }))
      }
      return
    }
    try {
      const r = await api.get(`/llm-mock/presets/${key}`)
      const p = r.data || r
      setRouteForm(f => ({
        ...f, presetMode: key,
        statusCode: p.statusCode ?? p.status_code ?? f.statusCode,
        finishReason: p.finishReason ?? p.finish_reason ?? f.finishReason,
        responseType: p.responseType ?? p.response_type ?? f.responseType,
        responseBody: p.responseBody ?? p.response_body ?? f.responseBody,
        toolCalls: p.toolCalls ?? p.tool_calls ?? f.toolCalls,
        responseHeaders: p.responseHeaders ?? p.response_headers ?? f.responseHeaders,
      }))
    } catch { setRouteForm(f => ({ ...f, presetMode: key })) }
  }

  const handleSaveCustomPreset = async () => {
    if (!routeForm || !savePresetName.trim()) return
    try {
      await api.post('/llm-mock/custom-presets', {
        name: savePresetName.trim(),
        config: {
          statusCode: routeForm.statusCode,
          finishReason: routeForm.finishReason,
          responseType: routeForm.responseType,
          responseBody: routeForm.responseBody,
          toolCalls: routeForm.toolCalls,
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
      await api.delete(`/llm-mock/custom-presets/${id}`)
      message.success('预设已删除')
      fetchCustomPresets()
    } catch {}
  }

  const handleToggleService = async () => {
    try {
      if (serviceStatus.running) {
        await api.post('/llm-mock/stop'); message.success('Mock 服务已停止')
      } else {
        await api.post('/llm-mock/start'); message.success('Mock 服务已启动')
      }
      setTimeout(fetchStatus, 500)
    } catch (e) {
      message.error(`操作失败: ${e?.response?.data?.error || e?.response?.data?.detail || e.message || '未知错误'}`)
    }
  }

  const handleClearLogs = async () => {
    try { await api.delete('/llm-mock/logs'); message.success('日志已清空'); setExpandedLogId(null); setExpandedLogDetail(null); setLogs([]); setLogsTotal(0); setLogPage(1); fetchLogs(1) } catch {}
  }

  const handleReplay = async (logId) => {
    try { const r = await api.post(`/llm-mock/logs/${logId}/replay`); message.success(`回放完成: ${(r.data || r).status_code}`); fetchLogs() } catch {}
  }

  const handleExportLogs = () => window.open('/api/llm-mock/logs/export', '_blank')

  const handleToggleLogDetail = async (logId) => {
    if (expandedLogId === logId) { setExpandedLogId(null); setExpandedLogDetail(null); return }
    try {
      const r = await api.get(`/llm-mock/logs/${logId}`)
      setExpandedLogDetail(r.data || r)
      setExpandedLogId(logId)
    } catch {}
  }

  const handleCopyPreview = () => {
    copyToClipboard(previewJson)
    setCopyText('已复制 ✓'); setTimeout(() => setCopyText('复制'), 1500)
  }

  const responseModeValue = routeForm?.responseMode || 'default'
  const isEmbedding = routeForm?.responseType === 'embedding'

  // 响应内容里写了个纯数字数组 → 固定向量；否则按输入文本确定性生成
  const fixedVector = useMemo(() => {
    if (!isEmbedding || !routeForm?.responseBody?.trim().startsWith('[')) return null
    try {
      const p = JSON.parse(routeForm.responseBody)
      return Array.isArray(p) && p.length > 0 && p.every(x => typeof x === 'number') ? p : null
    } catch { return null }
  }, [isEmbedding, routeForm?.responseBody])

  const bodyHint = (() => {
    if (!routeForm) return ''
    const sc = routeForm.statusCode ?? 200
    if (sc >= 400) return '只需填写错误消息，系统自动包装为 OpenAI 错误格式'
    if (routeForm.responseType === 'refusal') return '填写拒绝理由，放入 message.refusal'
    if (routeForm.responseType === 'tool_calls') return 'Tool Calls 在「高级设置」中配置'
    if (routeForm.responseType === 'embedding') return '留空即按输入文本生成向量；填数字数组则固定返回该向量'
    return '填写 AI 回复文本，系统自动包装为 Chat Completion 格式'
  })()

  const previewJson = useMemo(() => {
    if (!routeForm) return ''
    if (responseModeValue === 'random') {
      return isEmbedding
        ? '// 随机模式：每次请求返回一个随机向量（同一段文本两次调用也不一样，可用来测语义缓存「未命中」）'
        : '// 随机模式：每次请求从内置模板池随机选取一条回复'
    }
    const sc = routeForm.statusCode ?? 200
    if (sc >= 400) {
      const msg = routeForm.responseBody || 'Error message'
      const typeMap = { 400: ['invalid_request_error', 'invalid_request'], 401: ['invalid_request_error', 'invalid_api_key'], 403: ['insufficient_quota', 'insufficient_quota'], 404: ['invalid_request_error', 'model_not_found'], 408: ['timeout', 'request_timeout'], 429: ['requests', 'rate_limit_exceeded'] }
      const [t, c] = typeMap[sc] || (sc >= 500 ? ['server_error', 'server_error'] : ['error', null])
      return JSON.stringify({ error: { message: msg, type: t, param: null, code: c } }, null, 2)
    }
    const type = routeForm.responseType || 'text'
    const model = routeForm.modelMode === 'custom' && routeForm.customModel ? routeForm.customModel : '${request.model}'
    if (type === 'embedding') {
      const pt = routeForm.tokenMode === 'custom' ? (routeForm.customPromptTokens || 0) : '~auto'
      return JSON.stringify({
        object: 'list',
        data: [{
          object: 'embedding',
          index: 0,
          embedding: fixedVector || ['<按输入文本确定性生成，维度取请求 dimensions，否则按模型名推断（text-embedding-3-small → 1536）>'],
        }],
        model,
        usage: { prompt_tokens: pt, total_tokens: pt },
      }, null, 2)
    }
    const msg = { role: 'assistant', content: null, refusal: null, annotations: [] }
    if (type === 'refusal') msg.refusal = routeForm.responseBody || "I'm sorry, I can't assist with that."
    else if (type === 'tool_calls') {
      msg.tool_calls = (routeForm.toolCalls || []).map((tc, i) => ({
        id: `call_mock${String(i).padStart(22, '0')}`, type: 'function',
        function: { name: tc.name || 'unknown', arguments: tc.arguments || '{}' }
      }))
    } else msg.content = routeForm.responseBody || 'Mock response'
    const pt = routeForm.tokenMode === 'custom' ? (routeForm.customPromptTokens || 0) : '~auto'
    const ct = routeForm.tokenMode === 'custom' ? (routeForm.customCompletionTokens || 0) : '~auto'
    return JSON.stringify({
      id: 'chatcmpl-xxxxxxxxxxxxxxxxxxxxx', object: 'chat.completion', created: '${timestamp}', model,
      choices: [{ index: 0, message: msg, logprobs: null, finish_reason: routeForm.finishReason || 'stop' }],
      usage: { prompt_tokens: pt, completion_tokens: ct, total_tokens: typeof pt === 'number' && typeof ct === 'number' ? pt + ct : '~auto' },
    }, null, 2)
  }, [routeForm])

  // ─── 路由配置 Tab ───
  const renderConfigTab = () => {
    if (!routeForm) {
      return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
        <Empty description={<span style={{ color: '#bfbfbf' }}>选择左侧路由查看配置</span>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </div>
    }
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* 路由头部 */}
        <div style={{
          padding: '10px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0,
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {isDefault && <Tag color="default" style={{ margin: 0, fontSize: 11 }}>默认</Tag>}
            <Input value={routeForm.name} onChange={e => setRouteForm(f => ({ ...f, name: e.target.value }))}
              variant="borderless" style={{ fontSize: 15, fontWeight: 600, width: 200, padding: '0 4px' }} placeholder="路由名称" />
          </div>
          <Space size={8}>
            <Button type="primary" icon={<SaveOutlined />} size="small" onClick={handleSaveRoute} loading={saving} disabled={!isDirty}>保存</Button>
            <Switch checked={routeForm.enabled} onChange={v => handleToggle(routeForm.id, v)}
              checkedChildren="启用" unCheckedChildren="禁用" size="small" />
            <Button size="small" onClick={() => setAdvancedOpen(true)}>高级</Button>
            {isDefault ? (
              <Tooltip title="默认路由不可删除"><Button icon={<DeleteOutlined />} size="small" disabled /></Tooltip>
            ) : (
              <Popconfirm title="确认删除？" onConfirm={() => handleDeleteRoute(routeForm.id)}>
                <Button icon={<DeleteOutlined />} size="small" danger />
              </Popconfirm>
            )}
          </Space>
        </div>

        {/* 可滚动配置区 */}
        <div style={{ flex: 1, overflow: 'auto', padding: '14px 16px' }}>
          {/* URL 栏 */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 0, marginBottom: 8,
            border: '1px solid rgba(0,0,0,0.08)', borderRadius: 12, overflow: 'hidden', background: 'transparent',
          }}>
            <Select value={routeForm.method} onChange={v => setRouteForm(f => ({ ...f, method: v }))}
              variant="borderless" style={{ width: 100, flexShrink: 0 }} popupMatchSelectWidth={100}>
              {['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map(m => (
                <Select.Option key={m} value={m}>
                  <span style={{ fontWeight: 600, color: m === 'GET' ? '#0ea5a0' : m === 'POST' ? '#fa8c16' : m === 'DELETE' ? '#e8453c' : '#4e8af0' }}>{m}</span>
                </Select.Option>
              ))}
            </Select>
            <div style={{ width: 1, height: 24, background: 'rgba(0,0,0,0.08)', flexShrink: 0 }} />
            <Input value={routeForm.path} onChange={e => setRouteForm(f => ({ ...f, path: e.target.value }))}
              variant="borderless" style={{ fontFamily: MONO, fontSize: 13, background: 'transparent' }} placeholder="/v1/chat/completions" />
          </div>

          {/* 完整访问地址 */}
          {serviceStatus.running && fullUrl && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16,
              padding: '6px 12px', background: '#e0f7f6', border: '1px solid rgba(14,165,160,0.3)', borderRadius: 12,
            }}>
              <LinkOutlined style={{ color: '#0ea5a0', fontSize: 12 }} />
              {pathPattern.hasWildcard && (
                <Tag color="cyan" style={{ margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>示例</Tag>
              )}
              <span style={{ fontSize: 12, fontFamily: MONO, color: '#0ea5a0', flex: 1, userSelect: 'all' }}>{fullUrl}</span>
              <Button size="small" type="text" icon={<CopyOutlined />} style={{ color: '#0ea5a0' }}
                onClick={() => { copyToClipboard(fullUrl); message.success('已复制访问地址') }} />
            </div>
          )}
          {/* 通配 / 带查询串的路径，说明清楚匹配规则，不然只能靠猜 */}
          {(pathPattern.hasWildcard || pathPattern.hasQuery) && (
            <div style={{ fontSize: 11, color: '#8c8c8c', marginTop: -8, marginBottom: 16, lineHeight: 1.6 }}>
              {pathPattern.hasWildcard && <>通配匹配：<code>*</code> 匹配一段（不跨 <code>/</code>），<code>**</code> 跨层级。</>}
              {pathPattern.hasQuery && <>路径里 <code>?</code> 之后的查询串在匹配时忽略，带任意 <code>api-version</code> 都能命中。</>}
            </div>
          )}
          {!serviceStatus.running && (
            <div style={{ fontSize: 12, color: '#bfbfbf', marginBottom: 16 }}>
              服务未启动，启动后显示完整访问地址
            </div>
          )}

          {/* 响应模式 + 预设 + 状态码 + 响应类型 + 结束原因 */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>响应模式</div>
              <Radio.Group value={responseModeValue} onChange={e => setRouteForm(f => ({ ...f, responseMode: e.target.value }))}
                buttonStyle="solid" size="small">
                <Radio.Button value="default">默认</Radio.Button>
                <Radio.Button value="random">随机</Radio.Button>
                <Radio.Button value="custom">自定义</Radio.Button>
              </Radio.Group>
            </div>
            <div style={{ minWidth: 80 }}>
              <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>状态码</div>
              <InputNumber value={routeForm.statusCode ?? 200} onChange={v => setRouteForm(f => ({ ...f, statusCode: v }))}
                min={100} max={599} size="small" style={{ width: 80 }} />
            </div>
            <div style={{ minWidth: 160 }}>
              <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>响应类型</div>
              <Select value={routeForm.responseType || 'text'} onChange={v => setRouteForm(f => ({ ...f, responseType: v }))}
                size="small" style={{ width: 160 }}>
                <Select.Option value="text">文本回复</Select.Option>
                <Select.Option value="tool_calls">Tool Calls</Select.Option>
                <Select.Option value="refusal">模型拒绝</Select.Option>
                <Select.Option value="embedding">向量 Embeddings</Select.Option>
              </Select>
            </div>
            {!isEmbedding && (
              <div style={{ minWidth: 110 }}>
                <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>结束原因</div>
                <Select value={routeForm.finishReason || 'stop'} onChange={v => setRouteForm(f => ({ ...f, finishReason: v }))}
                  size="small" style={{ width: 110 }}>
                  <Select.Option value="stop">stop</Select.Option>
                  <Select.Option value="length">length</Select.Option>
                  <Select.Option value="tool_calls">tool_calls</Select.Option>
                  <Select.Option value="content_filter">content_filter</Select.Option>
                </Select>
              </div>
            )}
            <div style={{ flex: 1, minWidth: 170, maxWidth: 250 }}>
              <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>预设模式</div>
              <Select value={routeForm.presetMode} onChange={handlePresetChange}
                placeholder="选择预设填充..." size="small" style={{ width: '100%' }}
                allowClear onClear={() => setRouteForm(f => ({ ...f, presetMode: null }))}>
                <Select.OptGroup label="正常响应 (200)">
                  {presets.filter(p => p.group === 'normal').map(p =>
                    <Select.Option key={p.key} value={p.key}>{p.label}</Select.Option>)}
                </Select.OptGroup>
                <Select.OptGroup label="客户端错误 (4xx)">
                  {presets.filter(p => p.group === 'clientError' || p.group === 'client_error').map(p =>
                    <Select.Option key={p.key} value={p.key}>{p.label}</Select.Option>)}
                </Select.OptGroup>
                <Select.OptGroup label="服务端错误 (5xx)">
                  {presets.filter(p => p.group === 'serverError' || p.group === 'server_error').map(p =>
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

          {/* 延迟 + SSE间隔 + Token模式 + 模型模式 */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>延迟 (ms)</div>
              <InputNumber value={routeForm.delayMs ?? 0} onChange={v => setRouteForm(f => ({ ...f, delayMs: v }))}
                min={0} step={100} size="small" style={{ width: 80 }} placeholder="0" />
            </div>
            {!isEmbedding && (
              <div>
                <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>SSE 间隔 (ms)</div>
                <InputNumber value={routeForm.sseChunkDelayMs ?? 50} onChange={v => setRouteForm(f => ({ ...f, sseChunkDelayMs: v }))}
                  min={0} size="small" style={{ width: 80 }} placeholder="50" />
              </div>
            )}
            <div>
              <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>Token 模式</div>
              <Radio.Group value={routeForm.tokenMode || 'auto'} onChange={e => setRouteForm(f => ({ ...f, tokenMode: e.target.value }))} size="small">
                <Radio value="auto">自动</Radio>
                <Radio value="custom">自定义</Radio>
              </Radio.Group>
            </div>
            {routeForm.tokenMode === 'custom' && (<>
              <div>
                <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>Prompt Tokens</div>
                <InputNumber value={routeForm.customPromptTokens} onChange={v => setRouteForm(f => ({ ...f, customPromptTokens: v }))} min={0} size="small" style={{ width: 80 }} />
              </div>
              {!isEmbedding && (
                <div>
                  <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>Completion Tokens</div>
                  <InputNumber value={routeForm.customCompletionTokens} onChange={v => setRouteForm(f => ({ ...f, customCompletionTokens: v }))} min={0} size="small" style={{ width: 80 }} />
                </div>
              )}
            </>)}
            <div>
              <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>模型模式</div>
              <Radio.Group value={routeForm.modelMode || 'follow_request'} onChange={e => setRouteForm(f => ({ ...f, modelMode: e.target.value }))} size="small">
                <Radio value="follow_request">跟随请求</Radio>
                <Radio value="custom">自定义</Radio>
              </Radio.Group>
            </div>
            {routeForm.modelMode === 'custom' && (
              <div>
                <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>自定义模型</div>
                <Input value={routeForm.customModel} onChange={e => setRouteForm(f => ({ ...f, customModel: e.target.value }))}
                  placeholder="gpt-4o-mini" size="small" style={{ width: 130 }} />
              </div>
            )}
          </div>

          {/* 随机模式提示 */}
          {responseModeValue === 'random' && (
            <Alert type="info" showIcon style={{ fontSize: 12, marginBottom: 16 }}
              message={isEmbedding
                ? '随机模式：每次请求返回随机向量，同一段文本前后两次不一致——用来测语义缓存「未命中」'
                : '随机模式：每次请求从内置模板池随机选取一条 AI 回复'} />
          )}

          {/* 向量模式下响应内容填了非数字数组 —— 提醒它会被忽略，别以为原样返回 */}
          {isEmbedding && responseModeValue !== 'random' && routeForm.responseBody?.trim() && !fixedVector && (
            <Alert type="warning" showIcon style={{ fontSize: 12, marginBottom: 16 }}
              message="响应内容不是数字数组，会被忽略：当前按输入文本确定性生成向量。想固定返回请填 [0.1, 0.2, 0.3] 这样的数组，或清空。" />
          )}

          {/* 响应内容 + 预览 — 左右分栏 */}
          {responseModeValue !== 'random' && (
            <div style={{ display: 'flex', gap: 12, minHeight: 0 }}>
              {/* 左：响应体编辑 */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                  <span style={{ fontSize: 12, color: '#8c8c8c', fontWeight: 500 }}>响应内容</span>
                  <span style={{ fontSize: 11, color: '#bfbfbf' }}>{bodyHint}</span>
                  <span style={{ flex: 1 }} />
                  <Button size="small" icon={<StarOutlined />}
                    onClick={() => { setSavePresetName(''); setSavePresetOpen(true) }}>保存为预设</Button>
                </div>
                {routeForm.responseType !== 'tool_calls' ? (
                  <TextArea
                    value={routeForm.responseBody}
                    onChange={e => setRouteForm(f => ({ ...f, responseBody: e.target.value }))}
                    style={{ fontFamily: MONO, fontSize: 12, flex: 1, minHeight: 200, resize: 'vertical' }}
                    placeholder={(routeForm.statusCode ?? 200) >= 400
                      ? '输入错误消息...\n如: Rate limit reached for gpt-4o...'
                      : isEmbedding
                        ? '留空 → 按输入文本确定性生成向量（相同文本 → 相同向量，语义缓存能测出命中/未命中）\n填数字数组 → 固定返回该向量，如: [0.1, 0.2, 0.3]'
                        : '输入 AI 回复文本...\n支持: ${request.model}  ${request.messages[-1].content}  ${timestamp}'}
                  />
                ) : (
                  <div style={{ padding: '14px', background: 'transparent', borderRadius: 12, border: '1px solid rgba(0,0,0,0.04)', fontSize: 12, color: '#8c8c8c' }}>
                    Tool Calls 函数在右侧「高级设置」中配置
                  </div>
                )}
              </div>

              {/* 右：响应预览 */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 12, color: '#8c8c8c', fontWeight: 500 }}>响应预览</span>
                    <Tag color={(routeForm.statusCode ?? 200) < 400 ? 'cyan' : 'red'} style={{ margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
                      {routeForm.statusCode ?? 200}
                    </Tag>
                    <Tag color={MODE_COLORS[responseModeValue]} style={{ margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
                      {MODE_LABELS[responseModeValue]}
                    </Tag>
                  </div>
                  <Button size="small" type="text"
                    icon={copyText === '复制' ? <CopyOutlined /> : <CheckOutlined />}
                    onClick={handleCopyPreview}
                    style={{ color: copyText === '复制' ? '#8c8c8c' : '#0ea5a0', fontSize: 12 }}>
                    {copyText}
                  </Button>
                </div>
                <pre style={{
                  margin: 0, padding: 14, flex: 1, minHeight: 200, overflow: 'auto',
                  fontSize: 12, lineHeight: 1.6, fontFamily: MONO,
                  background: '#1e1e2e', color: '#cdd6f4',
                  whiteSpace: 'pre-wrap', wordBreak: 'break-all', borderRadius: 12,
                }}>
                  {previewJson}
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>
    )
  }

  // ─── 请求日志 Tab ───
  const renderLogsTab = () => (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{
        padding: '8px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0,
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <span style={{ fontSize: 13, fontWeight: 500, color: '#262626' }}>共 {logsTotal} 条</span>
        <Space size={4}>
          <Radio.Group value={logFilter} onChange={e => setLogFilter(e.target.value)} size="small">
            <Radio.Button value="all">全部</Radio.Button>
            <Radio.Button value="ok">OK</Radio.Button>
            <Radio.Button value="error">Error</Radio.Button>
          </Radio.Group>
          <Button icon={<ReloadOutlined />} size="small" type="text" onClick={() => fetchLogs()} />
          <Button icon={<ExportOutlined />} size="small" type="text" onClick={handleExportLogs} />
          <Popconfirm title="确认清空？" onConfirm={handleClearLogs}>
            <Button icon={<ClearOutlined />} size="small" type="text" danger />
          </Popconfirm>
        </Space>
      </div>

      <div style={{ flex: 1, overflow: 'auto' }}>
        <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: 'rgba(255,255,255,0.45)', position: 'sticky', top: 0, zIndex: 1 }}>
              {['时间', '状态', '方法', '路径', '请求模型', '响应模型', 'Tokens', '耗时', ''].map((h, i) => (
                <th key={h || 'op'} style={{
                  padding: '6px 10px', textAlign: i >= 6 ? 'right' : 'left',
                  fontWeight: 500, fontSize: 11, color: '#8c8c8c', borderBottom: '1px solid rgba(0,0,0,0.04)',
                  whiteSpace: 'nowrap',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {logs.map(l => (
              <Fragment key={l.id}>
              <tr key={l.id} onClick={() => handleToggleLogDetail(l.id)} style={{
                cursor: 'pointer', borderBottom: '1px solid rgba(0,0,0,0.03)', background: 'rgba(255,255,255,0.25)',
                background: expandedLogId === l.id ? 'rgba(14,165,160,0.08)' : 'transparent',
              }}>
                <td style={{ padding: '5px 10px', whiteSpace: 'nowrap', fontSize: 11, color: '#8c8c8c' }}>
                  {new Date(l.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}
                </td>
                <td style={{ padding: '5px 10px' }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: STATUS_COLOR(l.statusCode) }}>{l.statusCode}</span>
                </td>
                <td style={{ padding: '5px 10px', fontSize: 11 }}>{l.method}</td>
                <td style={{ padding: '5px 10px', fontFamily: MONO, fontSize: 11, color: '#595959', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{l.path}</td>
                <td style={{ padding: '5px 10px', fontSize: 11, color: '#8c8c8c' }}>{l.requestModel || '-'}</td>
                <td style={{ padding: '5px 10px', fontSize: 11, color: '#8c8c8c' }}>{l.responseModel || '-'}</td>
                <td style={{ padding: '5px 10px', textAlign: 'right', fontSize: 11, color: '#8c8c8c', whiteSpace: 'nowrap' }}>
                  {(l.promptTokens || 0) + (l.completionTokens || 0) > 0
                    ? `${l.promptTokens || 0}+${l.completionTokens || 0}=${l.totalTokens || 0}`
                    : '-'}
                </td>
                <td style={{ padding: '5px 10px', textAlign: 'right', fontSize: 11, color: '#8c8c8c', whiteSpace: 'nowrap' }}>{Math.round(l.totalMs ?? 0)}ms</td>
                <td style={{ padding: '5px 10px', textAlign: 'right' }}>
                  <Button size="small" type="text" icon={<SendOutlined />} onClick={e => { e.stopPropagation(); handleReplay(l.id) }} />
                </td>
              </tr>
              {expandedLogId === l.id && expandedLogDetail && (
                <tr key={`${l.id}-detail`}>
                  <td colSpan={9} style={{ padding: '10px 16px', background: 'transparent', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                    <div style={{ display: 'flex', gap: 24, fontSize: 12 }}>
                      {expandedLogDetail.requestBody?.messages && (
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 11, color: '#8c8c8c', marginBottom: 4, fontWeight: 500 }}>请求消息</div>
                          <div style={{ maxHeight: 120, overflow: 'auto' }}>
                            {expandedLogDetail.requestBody.messages.map((m, i) => (
                              <div key={i} style={{
                                marginBottom: 2, padding: '3px 8px', borderRadius: 12, fontSize: 11,
                                background: m.role === 'user' ? '#fff7e6' : m.role === 'system' ? 'rgba(0,0,0,0.03)' : '#e0f7f6',
                                overflow: 'hidden', textOverflow: 'ellipsis',
                              }}>
                                <span style={{ color: '#8c8c8c', fontSize: 10 }}>{m.role}</span>{' '}
                                {typeof m.content === 'string' ? m.content : JSON.stringify(m.content)}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 11, color: '#8c8c8c', marginBottom: 4, fontWeight: 500 }}>响应内容</div>
                        <pre style={{
                          maxHeight: 120, overflow: 'auto', margin: 0, padding: 8, borderRadius: 12,
                          background: 'transparent', border: '1px solid rgba(0,0,0,0.04)', fontSize: 11, fontFamily: MONO,
                          whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                        }}>
                          {(() => { try { return JSON.stringify(JSON.parse(expandedLogDetail.responseBody), null, 2) } catch { return expandedLogDetail.responseBody || '-' } })()}
                        </pre>
                      </div>
                    </div>
                  </td>
                </tr>
              )}
            </Fragment>))}
            {logs.length === 0 && (
              <tr><td colSpan={9} style={{ textAlign: 'center', padding: 40, color: '#bfbfbf', fontSize: 12 }}>暂无请求日志</td></tr>
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
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 70px)', background: 'transparent' }}>

      {/* ━━━ 顶栏 ━━━ */}
      <div style={{
        padding: '10px 20px', background: 'transparent', borderBottom: '1px solid rgba(0,0,0,0.03)', background: 'rgba(255,255,255,0.25)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <ThunderboltOutlined style={{ fontSize: 18, color: '#4e8af0' }} />
            <span style={{ fontWeight: 600, fontSize: 16, letterSpacing: 0.5 }}>LLM Mock</span>
          </div>
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '2px 10px', borderRadius: 12,
            background: serviceStatus.running ? '#e0f7f6' : 'rgba(0,0,0,0.04)',
            border: `1px solid ${serviceStatus.running ? 'rgba(14,165,160,0.3)' : 'rgba(0,0,0,0.1)'}`,
          }}>
            <Badge status={serviceStatus.running ? 'success' : 'default'} />
            <span style={{ fontSize: 12, fontWeight: 600, fontFamily: 'monospace', color: serviceStatus.running ? '#0ea5a0' : '#999' }}>
              {serviceStatus.running ? `LIVE :${serviceStatus.port}` : 'STOPPED'}
            </span>
          </div>
          <span style={{ fontSize: 12, color: '#8c8c8c' }}>
            {serviceStatus.routesEnabled}/{serviceStatus.routesCount} 路由 · {serviceStatus.totalRequests} 请求
          </span>
        </div>
        <Space size={8}>
          {serviceStatus.running && (
            <Button size="small" icon={<CopyOutlined />} onClick={() => {
              const url = `http://${window.location.hostname}:${serviceStatus.port}`
              copyToClipboard(url)
              message.success('已复制端点地址')
            }}>复制端点</Button>
          )}
          <Button type={serviceStatus.running ? 'default' : 'primary'} danger={serviceStatus.running}
            icon={serviceStatus.running ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
            onClick={handleToggleService} size="small">
            {serviceStatus.running ? '停止服务' : '启动服务'}
          </Button>
        </Space>
      </div>

      {/* ━━━ 主体 ━━━ */}
      <div style={{ flex: 1, display: 'flex', gap: 0, minHeight: 0 }}>

        {/* 左栏：路由列表 */}
        <div style={{
          width: 260, flexShrink: 0, background: 'transparent', borderRight: '1px solid rgba(0,0,0,0.05)',
          display: 'flex', flexDirection: 'column',
        }}>
          <div style={{
            padding: '10px 14px', borderBottom: '1px solid rgba(0,0,0,0.04)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span style={{ fontWeight: 600, fontSize: 13, color: '#262626' }}>路由</span>
            <Tooltip title="新建路由">
              <Button type="primary" ghost icon={<PlusOutlined />} size="small" onClick={handleCreateRoute} />
            </Tooltip>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: '6px 8px' }}>
            {routes.map(r => {
              const sel = selectedRouteId === r.id
              const isDef = r.id === defaultRouteId
              const mode = r.responseMode || 'default'
              return (
                <div key={r.id} onClick={() => selectRoute(r)} style={{
                  padding: '10px 12px', marginBottom: 4, borderRadius: 12, cursor: 'pointer',
                  background: sel ? 'rgba(14,165,160,0.08)' : 'transparent',
                  borderLeft: `3px solid ${sel ? '#4e8af0' : r.enabled ? '#0ea5a0' : 'rgba(0,0,0,0.1)'}`,
                  transition: 'all .15s',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    {isDef && <LockOutlined style={{ fontSize: 10, color: '#bfbfbf' }} />}
                    <Tag color={r.statusCode >= 400 ? 'red' : 'blue'} style={{
                      margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 4px', borderRadius: 8,
                    }}>{r.method}</Tag>
                    <span style={{
                      flex: 1, fontSize: 11, fontFamily: MONO,
                      color: '#595959', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{r.path}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 6 }}>
                    <span style={{ fontSize: 12, color: sel ? '#262626' : '#8c8c8c', fontWeight: sel ? 500 : 400 }}>{r.name}</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <Tag style={{
                        margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 5px',
                        color: STATUS_COLOR(r.statusCode), borderColor: STATUS_COLOR(r.statusCode),
                        background: 'transparent', borderRadius: 8,
                      }}>{r.statusCode}</Tag>
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

        {/* 右栏：Tab(配置/日志) */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: 'transparent' }}>
          <div style={{ borderBottom: '1px solid rgba(0,0,0,0.04)', paddingLeft: 16, flexShrink: 0 }}>
            <div style={{ display: 'flex', gap: 0 }}>
              {[
                { key: 'config', label: '路由配置' },
                { key: 'logs', label: <>请求日志 <Tag style={{ margin: '0 0 0 4px', fontSize: 11, borderRadius: 12, lineHeight: '18px', padding: '0 6px' }}>{serviceStatus.totalRequests}</Tag></> },
              ].map(t => (
                <div key={t.key} onClick={() => setActiveTab(t.key)} style={{
                  padding: '10px 16px', cursor: 'pointer', fontSize: 14, position: 'relative',
                  color: activeTab === t.key ? '#4e8af0' : '#595959',
                  fontWeight: activeTab === t.key ? 500 : 400,
                  borderBottom: activeTab === t.key ? '2px solid #4e8af0' : '2px solid transparent',
                  marginBottom: -1,
                }}>
                  {t.label}
                </div>
              ))}
            </div>
          </div>
          <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
            {activeTab === 'config' ? renderConfigTab() : renderLogsTab()}
          </div>
        </div>
      </div>

      {/* ━━━ 高级设置抽屉 ━━━ */}
      <Drawer open={advancedOpen} onClose={() => setAdvancedOpen(false)} width={420} title="高级设置">
        {routeForm && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            {routeForm.responseType === 'tool_calls' && (
              <div>
                <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 6 }}>Tool Calls (JSON)</div>
                <TextArea value={JSON.stringify(routeForm.toolCalls || [], null, 2)}
                  onChange={e => { try { setRouteForm(f => ({ ...f, toolCalls: JSON.parse(e.target.value) })) } catch {} }}
                  rows={6} style={{ fontFamily: MONO, fontSize: 12 }}
                  placeholder='[{"name":"get_weather","arguments":"{\"location\":\"Beijing\"}"}]' />
              </div>
            )}
            <div>
              <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 6 }}>自定义响应头 (JSON)</div>
              <TextArea
                value={routeForm.responseHeaders ? JSON.stringify(routeForm.responseHeaders, null, 2) : ''}
                onChange={e => { try { setRouteForm(f => ({ ...f, responseHeaders: e.target.value ? JSON.parse(e.target.value) : null })) } catch {} }}
                rows={3} style={{ fontFamily: MONO, fontSize: 12 }}
                placeholder='{"X-Custom-Header": "value"}' />
            </div>
          </div>
        )}
      </Drawer>

      <Modal title="保存为自定义预设" open={savePresetOpen}
        onOk={handleSaveCustomPreset} onCancel={() => setSavePresetOpen(false)}
        okText="保存" cancelText="取消" okButtonProps={{ disabled: !savePresetName.trim() }}>
        <div style={{ marginBottom: 8, fontSize: 13, color: '#8c8c8c' }}>
          将当前响应配置（状态码、响应类型、响应内容等）保存为预设，方便下次快速选用。
        </div>
        <Input placeholder="输入预设名称" value={savePresetName}
          onChange={e => setSavePresetName(e.target.value)}
          onPressEnter={handleSaveCustomPreset} autoFocus />
      </Modal>
    </div>
  )
}
