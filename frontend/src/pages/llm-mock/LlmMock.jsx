import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import {
  Button, Space, Input, Select, Tag, Radio, Popconfirm, Tooltip, Badge, Pagination,
  Empty, Typography, InputNumber, Switch, message, Drawer, Alert, Modal, Spin
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, SaveOutlined, PlayCircleOutlined, PauseCircleOutlined,
  ReloadOutlined, ExportOutlined, ClearOutlined, CopyOutlined, ThunderboltOutlined,
  LockOutlined, LockFilled, UnlockOutlined, HolderOutlined,
  SettingOutlined, CheckOutlined, SendOutlined, LinkOutlined, StarOutlined
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

// 日志里请求体是已解析的对象、响应体是原始字符串，两种都要能格式化
const fmtJson = (v) => {
  if (v === null || v === undefined || v === '') return '-'
  if (typeof v === 'string') {
    try { return JSON.stringify(JSON.parse(v), null, 2) } catch { return v }
  }
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

const STATUS_COLOR = (sc) => {
  if (sc >= 500) return '#e8453c'
  if (sc >= 400) return '#ff7d00'
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
  // 智能应答两个角色各一条 —— 一键建出来就能用，不用自己想路径该怎么写
  { name: '智能应答 · 上游模型', path: '/mock-smart/v1/chat/completions', smartEnabled: true, smartRole: 'upstream', statusCode: 200, responseType: 'text', finishReason: 'stop', sseChunkSize: 6, sseChunkDelayMs: 20, responseBody: '' },
  { name: '智能应答 · 护栏检查模型', path: '/mock-smart/checker/v1/chat/completions', smartEnabled: true, smartRole: 'checker', statusCode: 200, responseType: 'text', finishReason: 'stop', responseBody: '' },
]

// 智能应答开着时被接管的配置：由请求里的指令决定，页面上改了也不生效，所以置灰。
// 「置灰但看得见」是刻意的 —— 这块以前是个黑盒 bool，看不见改不了，所以被拆过一次。
const SMART_ROLE_LABEL = { auto: '自动判断', upstream: '上游模型', checker: '护栏检查模型' }

export default function LlmMock() {
  const [routes, setRoutes] = useState([])
  const [selectedRouteId, setSelectedRouteId] = useState(null)
  const [routeForm, setRouteForm] = useState(null)
  const [originalForm, setOriginalForm] = useState(null)
  const [presets, setPresets] = useState([])
  const [customPresets, setCustomPresets] = useState([])
  // 智能应答契约从后端取（单一真源），不在这里再抄一份指令表
  const [smartContract, setSmartContract] = useState(null)
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
  const [serviceStatus, setServiceStatus] = useState({ running: false, port: 28100, captureEnabled: true, routesCount: 0, routesEnabled: 0, totalRequests: 0 })
  const [saving, setSaving] = useState(false)
  const [activeTab, setActiveTab] = useState('config')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  // 打开高级设置时的快照，用于「取消」回滚抽屉内的改动
  const [advancedSnapshot, setAdvancedSnapshot] = useState(null)
  const [copyText, setCopyText] = useState('复制')
  const [dragIdx, setDragIdx] = useState(null)
  const pollRef = useRef(null)

  useEffect(() => {
    fetchRoutes()
    fetchPresets()
    fetchCustomPresets()
    fetchSmartContract()
    fetchStatus()
    fetchLogs()
    pollRef.current = setInterval(() => { fetchStatus() }, 5000)
    return () => clearInterval(pollRef.current)
  }, [])

  const fetchRoutes = async () => { try { const r = await api.get('/llm-mock/routes'); setRoutes(r.data || r || []) } catch {} }
  const fetchPresets = async () => { try { const r = await api.get('/llm-mock/presets'); setPresets(r.data?.data || r.data || []) } catch {} }
  const fetchCustomPresets = async () => { try { const r = await api.get('/llm-mock/custom-presets'); setCustomPresets(r.data?.data || r.data || []) } catch {} }
  const fetchSmartContract = async () => { try { const r = await api.get('/llm-mock/smart-contract'); setSmartContract(r.data || r) } catch {} }
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
      'customPromptTokens', 'customCompletionTokens', 'modelMode', 'customModel', 'responseFormat',
      // 这三个不加进来，开了智能应答保存按钮不会亮，改了等于没改
      'streamMode', 'sseChunkSize', 'smartEnabled', 'smartRole', 'smartBodyMarker']
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

  // 返回是否保存成功 —— 高级设置抽屉据此决定关不关（失败就别关，改动留着）
  const handleSaveRoute = async () => {
    if (!routeForm) return false
    setSaving(true)
    try {
      await api.put(`/llm-mock/routes/${routeForm.id}`, routeForm)
      message.success('已保存')
      await fetchRoutes()
      setOriginalForm({ ...routeForm })
      return true
    } catch { return false } finally { setSaving(false) }
  }

  const handleToggleLock = async () => {
    if (!routeForm) return
    try {
      const r = await api.patch(`/llm-mock/routes/${routeForm.id}/lock`)
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
      await api.put('/llm-mock/routes/reorder', {
        items: next.map((r, i) => ({ id: r.id, sortOrder: i })),
      })
      await fetchRoutes()
    } catch { await fetchRoutes() }
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
          streamMode: cp.config.streamMode ?? cp.config.stream_mode ?? f.streamMode,
          sseChunkSize: cp.config.sseChunkSize ?? cp.config.sse_chunk_size ?? f.sseChunkSize,
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
        // 这两个回落到默认值而不是保留当前值 —— 否则选过一次「fail-closed」再换普通预设，
        // 耍赖开关会悄悄粘着不放，后面所有请求都变成事件流，很难查。
        streamMode: p.streamMode ?? p.stream_mode ?? 'auto',
        sseChunkSize: p.sseChunkSize ?? p.sse_chunk_size ?? f.sseChunkSize,
        sseChunkDelayMs: p.sseChunkDelayMs ?? p.sse_chunk_delay_ms ?? f.sseChunkDelayMs,
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
          streamMode: routeForm.streamMode,
          sseChunkSize: routeForm.sseChunkSize,
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

  const handleOpenLogDetail = async (logId) => {
    setExpandedLogId(logId)
    setExpandedLogDetail(null)
    setLogDrawerOpen(true)
    setLogDetailLoading(true)
    try {
      const r = await api.get(`/llm-mock/logs/${logId}`)
      setExpandedLogDetail(r.data || r)
    } catch {} finally { setLogDetailLoading(false) }
  }

  const handleCloseLogDrawer = () => {
    setLogDrawerOpen(false)
    setExpandedLogId(null)
    setExpandedLogDetail(null)
  }

  const handleCopyPreview = () => {
    copyToClipboard(previewJson)
    setCopyText('已复制 ✓'); setTimeout(() => setCopyText('复制'), 1500)
  }

  const responseModeValue = routeForm?.responseMode || 'default'
  const isEmbedding = routeForm?.responseType === 'embedding'
  const locked = !!routeForm?.locked

  // ── 智能应答 ──
  const smartOn = !!routeForm?.smartEnabled
  // 被智能应答接管的配置一律**隐藏**（不是置灰）—— 摆一堆改了不生效的框只会误导人。
  // 路径、模型映射这些指令管不着，即使开着智能应答也照常可改。

  // 当前路径判出来的协议形状 / 角色 —— 与后端 detect_shape / resolve_role 同一套判据。
  // 页面上必须显示出来：不显示的话「这条路由到底会按哪种形状回」只能靠猜，
  // 而形状不对时客户端报的错跟网关自己的 bug 长得一样。
  const smartShape = useMemo(() => {
    const p = (routeForm?.path || '').split('?')[0].replace(/\/+$/, '')
    if (p.endsWith('/messages')) return 'anthropic'
    if (p.endsWith('/chat/completions')) return 'chat'
    if (p.endsWith('/completions')) return 'text'
    return 'chat'
  }, [routeForm?.path])

  const smartRole = useMemo(() => {
    const explicit = routeForm?.smartRole || 'auto'
    if (explicit !== 'auto') return explicit
    const p = (routeForm?.path || '').toLowerCase()
    return ['/checker', '/check', '/guard', '/guardrail', '/moderation'].some(h => p.includes(h))
      ? 'checker' : 'upstream'
  }, [routeForm?.smartRole, routeForm?.path])

  const SHAPE_LABEL = {
    chat: 'chat.completion（OpenAI Chat）',
    text: 'text_completion（legacy completions，choice 用 text）',
    anthropic: 'message（Anthropic，content 是 block 数组）',
  }


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
    if (routeForm.smartEnabled) {
      return smartRole === 'checker'
        ? '智能应答接管 · 只读：实际返回的是判决 JSON，长度和开头按每次请求现算'
        : '智能应答接管 · 只读：这是不带指令时返回的正文，带了指令按右边那张表走'
    }
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
        <Empty description={<span style={{ color: '#c9cdd4' }}>选择左侧路由查看配置</span>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
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
            {locked && <Tag color="orange" icon={<LockFilled />} style={{ margin: 0, fontSize: 11 }}>已锁定</Tag>}
            <Input value={routeForm.name} onChange={e => setRouteForm(f => ({ ...f, name: e.target.value }))}
              variant="borderless" disabled={locked}
              style={{ fontSize: 15, fontWeight: 600, width: 200, padding: '0 4px' }} placeholder="路由名称" />
          </div>
          <Space size={8}>
            <Button type="primary" icon={<SaveOutlined />} size="small" onClick={handleSaveRoute} loading={saving} disabled={!isDirty || locked}>保存</Button>
            <Switch checked={routeForm.enabled} onChange={v => handleToggle(routeForm.id, v)}
              disabled={locked}
              checkedChildren="启用" unCheckedChildren="禁用" size="small" />
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
              type="warning" showIcon style={{ marginBottom: 12, fontSize: 12 }}
              message="此路由已锁定，配置为只读。点击右上角「解锁」后才能编辑。"
            />
          )}
          {/* URL 栏 */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 0, marginBottom: 8,
            border: '1px solid rgba(0,0,0,0.08)', borderRadius: 12, overflow: 'hidden', background: 'transparent',
          }}>
            <Select value={routeForm.method} onChange={v => setRouteForm(f => ({ ...f, method: v }))}
              variant="borderless" disabled={locked} style={{ width: 100, flexShrink: 0 }} popupMatchSelectWidth={100}>
              {['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map(m => (
                <Select.Option key={m} value={m}>
                  <span style={{ fontWeight: 600, color: m === 'GET' ? '#0ea5a0' : m === 'POST' ? '#ff7d00' : m === 'DELETE' ? '#e8453c' : '#4e8af0' }}>{m}</span>
                </Select.Option>
              ))}
            </Select>
            <div style={{ width: 1, height: 24, background: 'rgba(0,0,0,0.08)', flexShrink: 0 }} />
            <Input spellCheck={false} value={routeForm.path} onChange={e => setRouteForm(f => ({ ...f, path: e.target.value }))}
              variant="borderless" disabled={locked} style={{ fontFamily: MONO, fontSize: 13, background: 'transparent' }} placeholder="/v1/chat/completions" />
          </div>

          {/* 完整访问地址 */}
          {serviceStatus.running && fullUrl && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16,
              padding: '6px 12px', background: 'var(--green-bg)', border: '1px solid rgba(14,165,160,0.3)', borderRadius: 12,
            }}>
              <LinkOutlined style={{ color: '#0ea5a0', fontSize: 12 }} />
              {pathPattern.hasWildcard && (
                <Tag color="cyan" style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 4px' }}>示例</Tag>
              )}
              <span style={{ fontSize: 12, fontFamily: MONO, color: '#0ea5a0', flex: 1, userSelect: 'all' }}>{fullUrl}</span>
              <Button size="small" type="text" icon={<CopyOutlined />} style={{ color: '#0ea5a0' }}
                onClick={() => { copyToClipboard(fullUrl); message.success('已复制访问地址') }} />
            </div>
          )}
          {/* 通配 / 带查询串的路径，说明清楚匹配规则，不然只能靠猜 */}
          {(pathPattern.hasWildcard || pathPattern.hasQuery) && (
            <div style={{ fontSize: 11, color: '#86909c', marginTop: -8, marginBottom: 16, lineHeight: 1.6 }}>
              {pathPattern.hasWildcard && <>通配匹配：<code>*</code> 匹配一段（不跨 <code>/</code>），<code>**</code> 跨层级。</>}
              {pathPattern.hasQuery && <>路径里 <code>?</code> 之后的查询串在匹配时忽略，带任意 <code>api-version</code> 都能命中。</>}
            </div>
          )}
          {!serviceStatus.running && (
            <div style={{ fontSize: 12, color: '#c9cdd4', marginBottom: 16 }}>
              服务未启动，启动后显示完整访问地址
            </div>
          )}

          {/* ━━ 智能应答：独立一区 ━━
              它一开就接管下面整片响应配置，所以不能跟那些配置混排 ——
              混在一起的话，一个开关关掉半屏输入框，看着像页面坏了 */}
          {!isEmbedding && (
            <div style={{
              marginBottom: 18, borderRadius: 12, overflow: 'hidden',
              border: `1px solid ${smartOn ? 'rgba(78,138,240,0.35)' : 'rgba(0,0,0,0.08)'}`,
              borderLeft: `3px solid ${smartOn ? '#4e8af0' : 'rgba(0,0,0,0.12)'}`,
              background: smartOn ? 'rgba(78,138,240,0.05)' : 'transparent',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap', padding: '10px 14px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <ThunderboltOutlined style={{ fontSize: 14, color: smartOn ? '#4e8af0' : '#c9cdd4' }} />
                  <span style={{ fontSize: 13, fontWeight: 600, color: smartOn ? '#4e8af0' : '#4e5969' }}>智能应答</span>
                  <Tooltip title="让这条路由的行为由「请求里写的指令」决定，而不是这个页面上的配置：请求正文里写 MODE:PII 就返回带敏感信息的输出，写 SAY:你好 就回「你好」。为什么要这样：配置在服务端的话，每换一个场景都要改配置、等下发、重来一遍，对照实验做不起来。开启后被它接管的配置会隐藏 —— 显示一堆改了不生效的框只会误导人。">
                    <span style={{ cursor: 'help', color: '#c9cdd4', fontSize: 12 }}>?</span>
                  </Tooltip>
                  <Switch size="small" checked={smartOn} disabled={locked}
                    checkedChildren="开" unCheckedChildren="关"
                    onChange={v => setRouteForm(f => ({ ...f, smartEnabled: v }))} />
                </div>
                {!smartOn && (
                  <span style={{ fontSize: 11, color: '#c9cdd4' }}>
                    关着：所有请求都回下面配的那段「响应内容」。开了就按请求里的 MODE: / SAY: 指令分场景回。
                  </span>
                )}
              {smartOn && (<>
                <div>
                  <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>
                    这个假模型演谁
                    <Tooltip title="上游模型 = 被测智能体调的那个大模型，演各种 MODE 场景。护栏检查模型 = 网关的「护栏 AI 检查」调的那个模型，它只回判决，并把「本次收到的待检正文有多长、开头是什么」回显出来 —— 网关到底把什么喂给了护栏，这是唯一的观测点。自动判断 = 路径里带 /checker、/guard 就算护栏。">
                      <span style={{ marginLeft: 4, cursor: 'help', color: '#c9cdd4' }}>?</span>
                    </Tooltip>
                  </div>
                  <Select size="small" style={{ width: 150 }} disabled={locked}
                    value={routeForm.smartRole || 'auto'}
                    onChange={v => setRouteForm(f => ({ ...f, smartRole: v }))}>
                    <Select.Option value="auto">自动判断（按路径）</Select.Option>
                    <Select.Option value="upstream">上游模型</Select.Option>
                    <Select.Option value="checker">护栏检查模型</Select.Option>
                  </Select>
                </div>
                <div style={{ fontSize: 11, color: '#86909c', paddingBottom: 4 }}>
                  当前判定：<b style={{ color: '#4e8af0' }}>{SMART_ROLE_LABEL[smartRole]}</b>
                  <span style={{ margin: '0 6px', color: '#c9cdd4' }}>|</span>
                  协议形状 <b style={{ color: '#4e8af0' }}>{smartShape}</b>
                </div>
                {smartRole === 'checker' && (
                  <div>
                    <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>
                      待检正文定位标记
                      <Tooltip title={`护栏把正文包在提示模板里发过来，要从信封里把正文抠出来才能回显它的长度。默认认这几种写法：${(smartContract?.bodyMarkers || []).join(' / ')}。模板不一样就填你自己的。抠不到时不会静默返回 0，而是把整个信封当正文并在日志里标 bodyFrom=fallback —— 否则「护栏到底拿没拿到正文」这个证据就被淹了。`}>
                        <span style={{ marginLeft: 4, cursor: 'help', color: '#c9cdd4' }}>?</span>
                      </Tooltip>
                    </div>
                    <Input size="small" style={{ width: 180, fontFamily: MONO }} disabled={locked}
                      placeholder={(smartContract?.bodyMarkers || ['Text to check:'])[0]}
                      value={routeForm.smartBodyMarker || ''}
                      onChange={e => setRouteForm(f => ({ ...f, smartBodyMarker: e.target.value || null }))} />
                  </div>
                )}
              </>)}
              </div>
              {/* 开着的时候在这一区里就把话说清楚，不用再单起一个 Alert 占地方 */}
              {smartOn && (
                <div style={{
                  padding: '8px 14px', fontSize: 11, color: '#4e5969', lineHeight: 1.8,
                  borderTop: '1px solid rgba(78,138,240,0.2)', background: 'rgba(78,138,240,0.04)',
                }}>
                  响应内容 / 状态码 / 响应类型 / 结束原因 / 响应模式 / 响应流式 / 延迟 已由<b>请求里的指令</b>决定，
                  改了不生效，所以收起来了 —— 右边列着它认哪些指令。
                  下面留着的<b>几字一片 / SSE 间隔 / Token 模式 / 模型模式</b>指令管不着、<b>照常生效</b> ——
                  分片数和 token 用量本身就是拿来断言网关的（分片边界、计费统计），藏了就没法配。
                  关掉开关回到普通的静态 mock，你原来配的东西一个都没丢。
                </div>
              )}
            </div>
          )}

          {/* 响应模式 + 预设 + 状态码 + 响应类型 + 结束原因 —— 智能应答开着时整行不显示 */}
          {!smartOn && (
          <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>响应模式</div>
              <Radio.Group value={responseModeValue} onChange={e => setRouteForm(f => ({ ...f, responseMode: e.target.value }))}
                buttonStyle="solid" size="small" disabled={locked}>
                <Radio.Button value="default">默认</Radio.Button>
                <Radio.Button value="random">随机</Radio.Button>
                <Radio.Button value="custom">自定义</Radio.Button>
              </Radio.Group>
            </div>
            <div style={{ minWidth: 80 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>状态码</div>
              <InputNumber value={routeForm.statusCode ?? 200} onChange={v => setRouteForm(f => ({ ...f, statusCode: v }))}
                min={100} max={599} size="small" style={{ width: 80 }} disabled={locked} />
            </div>
            <div style={{ minWidth: 160 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>响应类型</div>
              <Select value={routeForm.responseType || 'text'} onChange={v => setRouteForm(f => ({ ...f, responseType: v }))}
                size="small" style={{ width: 160 }} disabled={locked}>
                <Select.Option value="text">文本回复</Select.Option>
                <Select.Option value="tool_calls">Tool Calls</Select.Option>
                <Select.Option value="refusal">模型拒绝</Select.Option>
                <Select.Option value="embedding">向量 Embeddings</Select.Option>
              </Select>
            </div>
            {!isEmbedding && (
              <div style={{ minWidth: 110 }}>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>结束原因</div>
                <Select value={routeForm.finishReason || 'stop'} onChange={v => setRouteForm(f => ({ ...f, finishReason: v }))}
                  size="small" style={{ width: 110 }} disabled={locked}>
                  <Select.Option value="stop">stop</Select.Option>
                  <Select.Option value="length">length</Select.Option>
                  <Select.Option value="tool_calls">tool_calls</Select.Option>
                  <Select.Option value="content_filter">content_filter</Select.Option>
                </Select>
              </div>
            )}
            <div style={{ flex: 1, minWidth: 170, maxWidth: 250 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>预设模式</div>
              <Select value={routeForm.presetMode} onChange={handlePresetChange}
                placeholder="选择预设填充..." size="small" style={{ width: '100%' }} disabled={locked}
                allowClear onClear={() => setRouteForm(f => ({ ...f, presetMode: null }))}>
                <Select.OptGroup label="正常响应 (200)">
                  {presets.filter(p => p.group === 'normal').map(p =>
                    <Select.Option key={p.key} value={p.key}>{p.label}</Select.Option>)}
                </Select.OptGroup>
                <Select.OptGroup label="网关联调">
                  {presets.filter(p => p.group === 'gateway').map(p =>
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
          )}

          {/* 延迟 + SSE间隔 + Token模式 + 模型模式。
              智能应答开着时只剩「模型模式」—— 模型映射由路由决定，指令管不着它，所以还得能改 */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            {!smartOn && (
            <div>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>延迟 (ms)</div>
              <InputNumber value={routeForm.delayMs ?? 0} onChange={v => setRouteForm(f => ({ ...f, delayMs: v }))}
                min={0} step={100} size="small" style={{ width: 80 }} placeholder="0" disabled={locked} />
            </div>
            )}
            {!isEmbedding && (
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>
                  SSE 间隔 (ms)
                  {smartOn && (
                    <Tooltip title="智能应答下这一格照常生效 —— 指令管不着分片计时。例外是 MODE:SLOW，它会把间隔顶成 250ms，好和对接方那份脚本对齐。">
                      <span style={{ marginLeft: 4, cursor: 'help', color: '#c9cdd4' }}>?</span>
                    </Tooltip>
                  )}
                </div>
                <InputNumber value={routeForm.sseChunkDelayMs ?? 50} onChange={v => setRouteForm(f => ({ ...f, sseChunkDelayMs: v }))}
                  min={0} size="small" style={{ width: 80 }} placeholder="50" disabled={locked} />
              </div>
            )}
            {!isEmbedding && (
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>
                  几字一片
                  <Tooltip title="流式时正文每多少个字发一片。对接网关时「分片数」本身常常是被验证的指标 —— 比如一段 34 字的正文，填 6 就切成 6 片，加上开头帧/结束帧/[DONE] 正好 9 个 data 分片。默认 1 是逐字发。">
                    <span style={{ marginLeft: 4, cursor: 'help', color: '#c9cdd4' }}>?</span>
                  </Tooltip>
                </div>
                <InputNumber value={routeForm.sseChunkSize ?? 1} onChange={v => setRouteForm(f => ({ ...f, sseChunkSize: v }))}
                  min={1} size="small" style={{ width: 80 }} placeholder="1" disabled={locked} />
              </div>
            )}
            {!isEmbedding && !smartOn && (
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>
                  响应流式
                  <Tooltip title="默认「跟随请求」，看请求体里的 stream 字段。选「强制流式」后即使请求写 stream:false 也返回 SSE 事件流，用来验网关的 fail-closed；「强制整包」则相反，请求要流式也只回完整 JSON。">
                    <span style={{ marginLeft: 4, cursor: 'help', color: '#c9cdd4' }}>?</span>
                  </Tooltip>
                </div>
                <Select value={routeForm.streamMode || 'auto'} onChange={v => setRouteForm(f => ({ ...f, streamMode: v }))}
                  size="small" style={{ width: 130 }} disabled={locked}>
                  <Select.Option value="auto">跟随请求</Select.Option>
                  <Select.Option value="force_stream">强制流式</Select.Option>
                  <Select.Option value="force_json">强制整包</Select.Option>
                </Select>
              </div>
            )}
            <div>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Token 模式</div>
              <Radio.Group value={routeForm.tokenMode || 'auto'} onChange={e => setRouteForm(f => ({ ...f, tokenMode: e.target.value }))} size="small" disabled={locked}>
                <Radio value="auto">自动</Radio>
                <Radio value="custom">自定义</Radio>
              </Radio.Group>
            </div>
            {routeForm.tokenMode === 'custom' && (<>
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Prompt Tokens</div>
                <InputNumber value={routeForm.customPromptTokens} onChange={v => setRouteForm(f => ({ ...f, customPromptTokens: v }))} min={0} size="small" style={{ width: 80 }} disabled={locked} />
              </div>
              {!isEmbedding && (
                <div>
                  <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Completion Tokens</div>
                  <InputNumber value={routeForm.customCompletionTokens} onChange={v => setRouteForm(f => ({ ...f, customCompletionTokens: v }))} min={0} size="small" style={{ width: 80 }} disabled={locked} />
                </div>
              )}
            </>)}
            <div>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>模型模式</div>
              <Radio.Group value={routeForm.modelMode || 'follow_request'} onChange={e => setRouteForm(f => ({ ...f, modelMode: e.target.value }))} size="small" disabled={locked}>
                <Radio value="follow_request">跟随请求</Radio>
                <Radio value="custom">自定义</Radio>
              </Radio.Group>
            </div>
            {routeForm.modelMode === 'custom' && (
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>自定义模型</div>
                <Input value={routeForm.customModel} onChange={e => setRouteForm(f => ({ ...f, customModel: e.target.value }))}
                  placeholder="gpt-4o-mini" size="small" style={{ width: 130 }} disabled={locked} />
              </div>
            )}
          </div>

          {/* 耍赖模式提示 —— 这两个会让 mock 故意不守 OpenAI 约定，不提示的话排查半天查不出 */}
          {!isEmbedding && !smartOn && routeForm.streamMode === 'force_stream' && (
            <Alert type="warning" showIcon style={{ fontSize: 12, marginBottom: 16 }}
              message="强制流式：请求写 stream:false 也会返回 text/event-stream。这是故意不守约定，用来验网关 fail-closed —— 别的调用方走这条路由会拿到解析不了的响应。" />
          )}
          {!isEmbedding && !smartOn && routeForm.streamMode === 'force_json' && (
            <Alert type="warning" showIcon style={{ fontSize: 12, marginBottom: 16 }}
              message="强制整包：请求写 stream:true 也只返回完整 JSON，不发事件流。等着读流的客户端可能会一直挂到超时。" />
          )}

          {/* 随机模式提示 */}
          {!smartOn && responseModeValue === 'random' && (
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
                  <span style={{ fontSize: 12, color: '#86909c', fontWeight: 500 }}>响应内容</span>
                  <span style={{ fontSize: 11, color: '#c9cdd4' }}>{bodyHint}</span>
                  <span style={{ flex: 1 }} />
                  {!smartOn && (
                    <Button size="small" icon={<StarOutlined />} disabled={locked}
                      onClick={() => { setSavePresetName(''); setSavePresetOpen(true) }}>保存为预设</Button>
                  )}
                </div>
                {routeForm.responseType !== 'tool_calls' ? (
                  <TextArea spellCheck={false}
                    // 智能应答下显示它实际会返回什么（看得见但改不了）—— 不写回 routeForm，
                    // 免得关掉智能应答之后你自己原来填的东西没了。
                    // 护栏角色返回的是判决 JSON 而不是那段默认正文，显示错了等于页面在骗人
                    value={smartOn
                      ? (smartRole === 'checker'
                        ? (smartContract?.checkerSample || '')
                        : (smartContract?.defaultBody || ''))
                      : routeForm.responseBody}
                    onChange={e => setRouteForm(f => ({ ...f, responseBody: e.target.value }))}
                    disabled={locked}
                    // 智能应答下用 readOnly 而不是 disabled：disabled 会把正文灰到看不清，
                    // 而这段正文正是要给人看的（它就是不带指令时的返回内容）
                    readOnly={smartOn}
                    style={{ fontFamily: MONO, fontSize: 12, flex: 1, minHeight: 200, resize: 'vertical' }}
                    placeholder={(routeForm.statusCode ?? 200) >= 400
                      ? '输入错误消息...\n如: Rate limit reached for gpt-4o...'
                      : isEmbedding
                        ? '留空 → 按输入文本确定性生成向量（相同文本 → 相同向量，语义缓存能测出命中/未命中）\n填数字数组 → 固定返回该向量，如: [0.1, 0.2, 0.3]'
                        : '输入 AI 回复文本...\n支持: ${request.model}  ${request.messages[-1].content}  ${timestamp}'}
                  />
                ) : (
                  <div style={{ padding: '14px', background: 'transparent', borderRadius: 12, border: '1px solid rgba(0,0,0,0.04)', fontSize: 12, color: '#86909c' }}>
                    Tool Calls 函数在右侧「高级设置」中配置
                  </div>
                )}
              </div>

              {/* 右：智能应答开着时是指令契约面板，否则是响应预览 */}
              {smartOn ? (
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span style={{ fontSize: 12, color: '#86909c', fontWeight: 500 }}>它认这些指令</span>
                    <span style={{ fontSize: 11, color: '#c9cdd4' }}>写在请求正文里（最后一条 user 消息）</span>
                  </div>
                  <div style={{
                    flex: 1, minHeight: 200, maxHeight: 420, overflow: 'auto',
                    border: '1px solid rgba(0,0,0,0.06)', borderRadius: 12, background: 'rgba(255,255,255,0.4)',
                  }}>
                    {smartRole === 'checker' ? (
                      <div style={{ padding: 14, fontSize: 12, lineHeight: 1.8 }}>
                        <div style={{ color: '#1d2129', fontWeight: 500, marginBottom: 6 }}>
                          护栏检查模型不演场景，只回判决
                        </div>
                        <div style={{ color: '#86909c', marginBottom: 10 }}>
                          它把「本次收到的待检正文有多长、开头是什么」回显进 reason 里。
                          <b style={{ color: '#ff7d00' }}>BODY_LEN 和 ENVELOPE_LEN 要分开看</b>：
                          提示模板本身几百字，只看信封长度的话，正文为空时它仍是个大数字，
                          「护栏到底拿没拿到正文」这个证据就被淹了。
                        </div>
                        <div style={{ color: '#86909c', marginBottom: 4 }}>判决规则：</div>
                        <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse', marginBottom: 10 }}>
                          <tbody>
                            {[
                              ['待检文本含 VIOLATION', 'verdict=false · mock_violation'],
                              ['含身份证号 + 脱敏模式', 'verdict=true · id_card · 给 redacted_content'],
                              ['含身份证号 + 仅检测', 'verdict=false · id_card'],
                              ['其余', 'verdict=true'],
                            ].map(([k, v]) => (
                              <tr key={k}>
                                <td style={{ padding: '3px 8px 3px 0', color: '#4e5969', whiteSpace: 'nowrap' }}>{k}</td>
                                <td style={{ padding: '3px 0', color: '#86909c', fontFamily: MONO }}>{v}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        <div style={{ color: '#86909c', marginBottom: 4 }}>返回样例：</div>
                        <pre style={{
                          ...CODE_BLOCK_STYLE, margin: 0, padding: 10, fontSize: 11, lineHeight: 1.6,
                          whiteSpace: 'pre-wrap', wordBreak: 'break-all', borderRadius: 8,
                        }}>{fmtJson(smartContract?.checkerSample)}</pre>
                        <div style={{ marginTop: 10, color: '#ff7d00', fontSize: 11 }}>
                          ⚠ 判「是不是脱敏模式」用的是<b>精确行匹配</b> <code>Redact mode: detect_and_redact</code>，
                          不是子串包含 —— 系统提示本身就在解释这条规则，用包含会把每个「仅检测」请求都误判成脱敏，结论全反。
                        </div>
                      </div>
                    ) : (
                      <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                        <tbody>
                          {(smartContract?.directives || []).map(d => (
                            <tr key={d.key || 'none'} style={{ borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                              <td style={{ padding: '7px 10px', whiteSpace: 'nowrap', verticalAlign: 'top', width: 120 }}>
                                <code style={{ fontFamily: MONO, fontSize: 11, color: '#4e8af0' }}>
                                  {d.key || '（不带指令）'}
                                </code>
                              </td>
                              <td style={{ padding: '7px 10px 7px 0', color: '#4e5969', lineHeight: 1.6 }}>
                                {d.effect}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                  <div style={{ marginTop: 6, fontSize: 11, color: '#86909c', lineHeight: 1.7 }}>
                    协议形状按<b>路径</b>判：<code style={{ fontFamily: MONO }}>{SHAPE_LABEL[smartShape]}</code>。
                    入参三种写法都能读到指令：OpenAI 的字符串 content、Anthropic 的 block 数组、legacy 的 <code>prompt</code>。
                  </div>
                </div>
              ) : (
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 12, color: '#86909c', fontWeight: 500 }}>响应预览</span>
                    <Tag color={(routeForm.statusCode ?? 200) < 400 ? 'cyan' : 'red'} style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 4px' }}>
                      {routeForm.statusCode ?? 200}
                    </Tag>
                    <Tag color={MODE_COLORS[responseModeValue]} style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 4px' }}>
                      {MODE_LABELS[responseModeValue]}
                    </Tag>
                  </div>
                  <Button size="small" type="text"
                    icon={copyText === '复制' ? <CopyOutlined /> : <CheckOutlined />}
                    onClick={handleCopyPreview}
                    style={{ color: copyText === '复制' ? '#86909c' : '#0ea5a0', fontSize: 12 }}>
                    {copyText}
                  </Button>
                </div>
                <pre style={{
                  ...CODE_BLOCK_STYLE,
                  margin: 0, padding: 14, flex: 1, minHeight: 200, overflow: 'auto',
                  fontSize: 12, lineHeight: 1.6,
                  whiteSpace: 'pre-wrap', wordBreak: 'break-all', borderRadius: 12,
                }}>
                  {previewJson}
                </pre>
              </div>
              )}
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
        <span style={{ fontSize: 13, fontWeight: 500, color: '#1d2129' }}>共 {logsTotal} 条</span>
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
                  fontWeight: 500, fontSize: 11, color: '#86909c', borderBottom: '1px solid rgba(0,0,0,0.04)',
                  whiteSpace: 'nowrap',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {logs.map(l => (
              <tr key={l.id} onClick={() => handleOpenLogDetail(l.id)} style={{
                cursor: 'pointer', borderBottom: '1px solid rgba(0,0,0,0.03)', background: 'rgba(255,255,255,0.25)',
                background: expandedLogId === l.id ? 'rgba(14,165,160,0.08)' : 'transparent',
              }}>
                <td style={{ padding: '5px 10px', whiteSpace: 'nowrap', fontSize: 11, color: '#86909c' }}>
                  {new Date(l.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}
                </td>
                <td style={{ padding: '5px 10px' }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: STATUS_COLOR(l.statusCode) }}>{l.statusCode}</span>
                </td>
                <td style={{ padding: '5px 10px', fontSize: 11 }}>{l.method}</td>
                <td style={{ padding: '5px 10px', fontFamily: MONO, fontSize: 11, color: '#4e5969', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{l.path}</td>
                <td style={{ padding: '5px 10px', fontSize: 11, color: '#86909c' }}>{l.requestModel || '-'}</td>
                <td style={{ padding: '5px 10px', fontSize: 11, color: '#86909c' }}>{l.responseModel || '-'}</td>
                <td style={{ padding: '5px 10px', textAlign: 'right', fontSize: 11, color: '#86909c', whiteSpace: 'nowrap' }}>
                  {(l.promptTokens || 0) + (l.completionTokens || 0) > 0
                    ? `${l.promptTokens || 0}+${l.completionTokens || 0}=${l.totalTokens || 0}`
                    : '-'}
                </td>
                <td style={{ padding: '5px 10px', textAlign: 'right', fontSize: 11, color: '#86909c', whiteSpace: 'nowrap' }}>{Math.round(l.totalMs ?? 0)}ms</td>
                <td style={{ padding: '5px 10px', textAlign: 'right' }}>
                  <Button size="small" type="text" icon={<SendOutlined />} onClick={e => { e.stopPropagation(); handleReplay(l.id) }} />
                </td>
              </tr>
            ))}
            {logs.length === 0 && (
              <tr><td colSpan={9} style={{ textAlign: 'center', padding: 40, color: '#c9cdd4', fontSize: 12 }}>暂无请求日志</td></tr>
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
            <span style={{ fontSize: 12, fontWeight: 600, fontFamily: 'var(--font-mono)', color: serviceStatus.running ? '#0ea5a0' : '#999' }}>
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
            <span style={{ fontWeight: 600, fontSize: 13, color: '#1d2129' }}>路由</span>
            <Tooltip title="新建路由">
              <Button type="primary" ghost icon={<PlusOutlined />} size="small" onClick={handleCreateRoute} />
            </Tooltip>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: '6px 8px' }}>
            {routes.map((r, i) => {
              const sel = selectedRouteId === r.id
              const isDef = r.id === defaultRouteId
              const mode = r.responseMode || 'default'
              const isDragging = dragIdx === i
              return (
                <div
                  key={r.id}
                  draggable
                  onClick={() => selectRoute(r)}
                  onDragStart={e => { e.dataTransfer.effectAllowed = 'move'; setDragIdx(i) }}
                  onDragOver={e => { e.preventDefault(); e.currentTarget.style.borderTop = '2px solid #4e8af0' }}
                  onDragLeave={e => { e.currentTarget.style.borderTop = '2px solid transparent' }}
                  onDrop={e => { e.preventDefault(); e.currentTarget.style.borderTop = '2px solid transparent'; handleDropRoute(i) }}
                  onDragEnd={() => setDragIdx(null)}
                  style={{
                    padding: '10px 12px', marginBottom: 4, borderRadius: 12, cursor: 'pointer',
                    background: sel ? 'rgba(14,165,160,0.08)' : 'transparent',
                    borderLeft: `3px solid ${sel ? '#4e8af0' : r.enabled ? '#0ea5a0' : 'rgba(0,0,0,0.1)'}`,
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
                    <Tag color={r.statusCode >= 400 ? 'red' : 'blue'} style={{
                      margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 4px', borderRadius: 8,
                    }}>{r.method}</Tag>
                    <span style={{
                      flex: 1, fontSize: 11, fontFamily: MONO,
                      color: '#4e5969', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{r.path}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 6 }}>
                    <span style={{ fontSize: 12, color: sel ? '#1d2129' : '#86909c', fontWeight: sel ? 500 : 400 }}>{r.name}</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      {r.smartEnabled && (
                        <Tooltip title="智能应答：行为由请求里的指令决定">
                          <Tag color="blue" style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 4px', borderRadius: 8 }}>智能</Tag>
                        </Tooltip>
                      )}
                      <Tag style={{
                        margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 5px',
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
            {activeTab === 'config' ? renderConfigTab() : renderLogsTab()}
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
            {routeForm.responseType === 'tool_calls' && (
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 6 }}>Tool Calls (JSON)</div>
                <TextArea spellCheck={false} value={JSON.stringify(routeForm.toolCalls || [], null, 2)}
                  onChange={e => { try { setRouteForm(f => ({ ...f, toolCalls: JSON.parse(e.target.value) })) } catch {} }}
                  rows={6} style={{ fontFamily: MONO, fontSize: 12 }}
                  placeholder='[{"name":"get_weather","arguments":"{\"location\":\"Beijing\"}"}]' />
              </div>
            )}
            <div>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 6 }}>自定义响应头 (JSON)</div>
              <TextArea spellCheck={false}
                value={routeForm.responseHeaders ? JSON.stringify(routeForm.responseHeaders, null, 2) : ''}
                onChange={e => { try { setRouteForm(f => ({ ...f, responseHeaders: e.target.value ? JSON.parse(e.target.value) : null })) } catch {} }}
                rows={3} style={{ fontFamily: MONO, fontSize: 12 }}
                placeholder='{"X-Custom-Header": "value"}' />
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
                <span style={{ fontSize: 12, fontWeight: 700, color: '#4e8af0' }}>{expandedLogDetail.method}</span>
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
                ['来源 IP', expandedLogDetail.ip || '-'],
                ['请求模型', expandedLogDetail.requestModel || '-'],
                ['响应模型', expandedLogDetail.responseModel || '-'],
                ['Tokens', (expandedLogDetail.promptTokens || 0) + (expandedLogDetail.completionTokens || 0) > 0
                  ? `${expandedLogDetail.promptTokens || 0} + ${expandedLogDetail.completionTokens || 0} = ${expandedLogDetail.totalTokens || 0}`
                  : '-'],
                ['finish_reason', expandedLogDetail.finishReason || '-'],
                ['首字节耗时', `${Math.round(expandedLogDetail.firstByteMs ?? 0)} ms`],
                ['总耗时', `${Math.round(expandedLogDetail.totalMs ?? 0)} ms`],
              ].map(([k, v]) => (
                <div key={k} style={{ display: 'flex', gap: 8, minWidth: 0 }}>
                  <span style={{ color: '#86909c', flexShrink: 0 }}>{k}</span>
                  <span style={{ color: '#1d2129', fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v}</span>
                </div>
              ))}
            </div>

            {/* 智能应答判定 —— 这次请求被解析成了什么。`stream` 记的是**网关实际发出的值**，
                流式降级到底有没有真发生，只能看这一格 */}
            {expandedLogDetail.smartMeta && (
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#4e5969', marginBottom: 6 }}>智能应答判定</div>
                <div style={{
                  display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 24px', fontSize: 12,
                  padding: '10px 12px', borderRadius: 12,
                  background: 'rgba(78,138,240,0.06)', border: '1px solid rgba(78,138,240,0.2)',
                }}>
                  {(() => {
                    const m = expandedLogDetail.smartMeta
                    const rows = [
                      ['角色', SMART_ROLE_LABEL[m.role] || m.role],
                      ['协议形状', m.shape],
                      ['命中指令', m.directive || '（无，走默认正文）'],
                      ['请求 stream', String(m.stream)],
                      ['stream_options', m.hasStreamOptions ? `有${m.includeUsage ? '（include_usage）' : ''}` : '无'],
                    ]
                    if (m.loopStage) rows.push(['Agent Loop 轮次', `第 ${m.loopStage} 轮`])
                    if (m.aborted) rows.push(['流是否发完', '⚠ 客户端中途断连（护栏拦截通常就是这个形态）'])
                    if (m.role === 'checker') {
                      rows.push(
                        ['待检正文长度', `${m.checkedLen}${m.bodyFrom === 'fallback' ? '（⚠ 没按标记抠到，整个信封当正文了）' : ''}`],
                        ['信封长度', String(m.envelopeLen)],
                        ['脱敏模式', m.redactMode ? 'detect_and_redact' : '仅检测'],
                        ['判决', m.verdict ? 'true（放行）' : 'false（拦截）'],
                        ['命中类别', (m.categories || []).join(', ') || '（无）'],
                        ['正文开头', m.bodyHead || '（空）'],
                      )
                    }
                    return rows.map(([k, v]) => (
                      <div key={k} style={{ display: 'flex', gap: 8, minWidth: 0 }}>
                        <span style={{ color: '#86909c', flexShrink: 0 }}>{k}</span>
                        <span style={{
                          color: k === '待检正文长度' && m.bodyFrom === 'fallback' ? '#ff7d00' : '#1d2129',
                          fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>{v}</span>
                      </div>
                    ))
                  })()}
                </div>
              </div>
            )}

            {/* LLM 专属：按 role 上色的对话气泡，比裸 JSON 好读，所以单独留一块 */}
            {expandedLogDetail.requestBody?.messages && (
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#4e5969', marginBottom: 6 }}>请求消息</div>
                <div style={{ maxHeight: 220, overflow: 'auto' }}>
                  {expandedLogDetail.requestBody.messages.map((m, i) => (
                    <div key={i} style={{
                      marginBottom: 4, padding: '6px 10px', borderRadius: 12, fontSize: 12, lineHeight: 1.6,
                      background: m.role === 'user' ? '#fff7e6' : m.role === 'system' ? 'rgba(0,0,0,0.03)' : '#e0f7f6',
                      whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                    }}>
                      <span style={{ color: '#86909c', fontSize: 11, marginRight: 6 }}>{m.role}</span>
                      {typeof m.content === 'string' ? m.content : JSON.stringify(m.content)}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <LogBlock title="请求头" content={fmtHeaders(expandedLogDetail.requestHeaders)}
              onCopy={() => copyToClipboard(fmtHeaders(expandedLogDetail.requestHeaders)).then(() => message.success('已复制'))} />
            <LogBlock title="请求体" content={fmtJson(expandedLogDetail.requestBody)}
              onCopy={() => copyToClipboard(fmtJson(expandedLogDetail.requestBody)).then(() => message.success('已复制'))} />
            <LogBlock title="响应头" content={fmtHeaders(expandedLogDetail.responseHeadersOut)}
              onCopy={() => copyToClipboard(fmtHeaders(expandedLogDetail.responseHeadersOut)).then(() => message.success('已复制'))} />
            <LogBlock title="响应体" content={fmtJson(expandedLogDetail.responseBody)}
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
          将当前响应配置（状态码、响应类型、响应内容等）保存为预设，方便下次快速选用。
        </div>
        <Input placeholder="输入预设名称" value={savePresetName}
          onChange={e => setSavePresetName(e.target.value)}
          onPressEnter={handleSaveCustomPreset} autoFocus />
      </Modal>

    </div>
  )
}
