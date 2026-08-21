import { useState, useEffect, useRef, useMemo, useCallback, Fragment } from 'react'
import {
  Button, Space, Input, Select, Tag, Radio, Popconfirm, Tooltip, Badge, Pagination,
  Empty, Typography, InputNumber, Switch, Modal, Alert, message
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, SaveOutlined, PlayCircleOutlined, PauseCircleOutlined,
  ReloadOutlined, ClearOutlined, CopyOutlined, CloudServerOutlined, AppstoreOutlined,
  SendOutlined, ThunderboltOutlined,
  LockOutlined, LockFilled, UnlockOutlined, HolderOutlined
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'
import { CODE_BLOCK_STYLE } from '../../components/MockCodeBlock'

const { TextArea } = Input
const MONO = 'var(--font-mono)'
const ACCENT = '#7c5cbf'

const GRPC_CODES = [
  { value: 0, label: '0 - OK' },
  { value: 1, label: '1 - CANCELLED' },
  { value: 2, label: '2 - UNKNOWN' },
  { value: 3, label: '3 - INVALID_ARGUMENT' },
  { value: 5, label: '5 - NOT_FOUND' },
  { value: 13, label: '13 - INTERNAL' },
  { value: 14, label: '14 - UNAVAILABLE' },
]

const GRPC_CODE_LABELS = { 0: 'OK', 1: 'CANCELLED', 2: 'UNKNOWN', 3: 'INVALID_ARGUMENT', 5: 'NOT_FOUND', 13: 'INTERNAL', 14: 'UNAVAILABLE' }

const METHOD_TYPE_COLOR = { unary: 'green', server_stream: 'blue' }
const METHOD_TYPE_LABEL = { unary: 'Unary', server_stream: 'Server Stream' }

export default function GrpcMockPanel() {
  const [services, setServices] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [form, setForm] = useState(null)
  const [originalForm, setOriginalForm] = useState(null)
  const [logs, setLogs] = useState([])
  const [logsTotal, setLogsTotal] = useState(0)
  const [logPage, setLogPage] = useState(1)
  const [logPageSize] = useState(50)
  const [expandedLogId, setExpandedLogId] = useState(null)
  const [logServiceFilter, setLogServiceFilter] = useState(undefined)
  const [serviceStatus, setServiceStatus] = useState({ running: false, port: 28700, servicesCount: 0, servicesEnabled: 0, totalLogs: 0, reflectionVersion: 'both' })
  const [saving, setSaving] = useState(false)
  const [activeTab, setActiveTab] = useState('config')
  const [presets, setPresets] = useState([])
  const [presetOpen, setPresetOpen] = useState(false)
  const pollRef = useRef(null)
  const [testBody, setTestBody] = useState('')
  const [testResult, setTestResult] = useState(null)
  const [testing, setTesting] = useState(false)
  const [dragIdx, setDragIdx] = useState(null)

  useEffect(() => {
    fetchServices()
    fetchStatus()
    fetchLogs()
    fetchPresets()
    pollRef.current = setInterval(() => { fetchStatus() }, 5000)
    return () => clearInterval(pollRef.current)
  }, [])

  const fetchServices = async () => { try { const r = await api.get('/protocol-mock/grpc/services'); setServices(r.data || r || []) } catch {} }
  const fetchStatus = async () => { try { const r = await api.get('/protocol-mock/grpc/status'); setServiceStatus(r.data || r) } catch {} }
  const fetchLogs = async (page, svcFilter) => {
    try {
      const p = page || logPage
      const filter = svcFilter !== undefined ? svcFilter : logServiceFilter
      const params = new URLSearchParams({ limit: String(logPageSize), offset: String((p - 1) * logPageSize) })
      if (filter) params.set('serviceName', filter)
      const r = await api.get(`/protocol-mock/grpc/logs?${params}`)
      const d = r.data || r
      setLogs(d.data || d || [])
      setLogsTotal(d.total ?? (d.data || d || []).length)
    } catch {}
  }

  const fetchPresets = async () => { try { const r = await api.get('/protocol-mock/grpc/presets'); setPresets(r.data?.data || r.data || []) } catch {} }

  useEffect(() => { setLogPage(1); fetchLogs(1, logServiceFilter) }, [logServiceFilter])

  useEffect(() => {
    if (services.length > 0 && !selectedId) selectService(services[0])
  }, [services])

  const selectService = useCallback((svc) => {
    const formData = { ...svc }
    setSelectedId(svc.id)
    setForm(formData)
    setOriginalForm(formData)
    setActiveTab('config')
    setTestBody(svc.requestSample || '{"name": "world"}')
    setTestResult(null)
  }, [])

  const isDirty = useMemo(() => {
    if (!form || !originalForm) return false
    const keys = ['name', 'serviceName', 'methodName', 'methodType', 'requestSample',
      'responseBody', 'streamItems', 'delayMs', 'statusCode', 'statusMessage', 'enabled']
    for (const k of keys) {
      if (form[k] !== originalForm[k]) return true
    }
    return false
  }, [form, originalForm])

  const serviceNames = useMemo(() => {
    const names = [...new Set(services.map(s => s.serviceName).filter(Boolean))]
    return names
  }, [services])

  const groupedPresets = useMemo(() => {
    const groups = {}
    for (const p of presets) {
      const g = p.group || '其他'
      if (!groups[g]) groups[g] = []
      groups[g].push(p)
    }
    return groups
  }, [presets])

  const handleCreate = async () => {
    try {
      const n = services.length + 1
      const body = {
        name: `gRPC Service ${n}`,
        serviceName: 'example.Service',
        methodName: `Method${n}`,
        methodType: 'unary',
        responseBody: '{"message": "hello"}',
        statusCode: 0,
        delayMs: 0,
      }
      const r = await api.post('/protocol-mock/grpc/services', body)
      const d = r.data || r
      message.success('Service 已创建')
      await fetchServices()
      selectService(d)
    } catch {}
  }

  const handleCreateFromPreset = async (preset) => {
    try {
      const body = {
        name: preset.name,
        serviceName: preset.serviceName,
        methodName: preset.methodName,
        methodType: preset.methodType,
        requestSample: preset.requestSample,
        responseBody: preset.responseBody,
        streamItems: preset.streamItems,
        statusCode: preset.statusCode || 0,
        statusMessage: preset.statusMessage,
        enabled: true,
      }
      const r = await api.post('/protocol-mock/grpc/services', body)
      message.success(`已从预设创建: ${preset.label || preset.name}`)
      setPresetOpen(false)
      await fetchServices()
      selectService(r.data || r)
    } catch {}
  }

  const handleSave = async () => {
    if (!form) return
    setSaving(true)
    try {
      await api.put(`/protocol-mock/grpc/services/${form.id}`, form)
      message.success('已保存')
      await fetchServices()
      setOriginalForm({ ...form })
    } catch {} finally { setSaving(false) }
  }

  const handleDelete = async (id) => {
    try {
      await api.delete(`/protocol-mock/grpc/services/${id}`)
      message.success('已删除')
      if (selectedId === id) { setSelectedId(null); setForm(null); setOriginalForm(null) }
      await fetchServices()
    } catch {}
  }

  const handleToggle = async (id, checked) => {
    try {
      await api.patch(`/protocol-mock/grpc/services/${id}/toggle`)
      await fetchServices()
      if (form && form.id === id) {
        setForm(f => ({ ...f, enabled: checked }))
        setOriginalForm(f => ({ ...f, enabled: checked }))
      }
    } catch {}
  }

  const handleToggleLock = async () => {
    if (!form) return
    try {
      const r = await api.patch(`/protocol-mock/grpc/services/${form.id}/lock`)
      const d = r.data || r
      message.success(d.locked ? '服务已锁定，配置只读' : '服务已解锁')
      setForm(f => ({ ...f, locked: d.locked }))
      setOriginalForm(f => ({ ...f, locked: d.locked }))
      await fetchServices()
    } catch {}
  }

  const handleDropService = async (targetIdx) => {
    const from = dragIdx
    setDragIdx(null)
    if (from === null || from === targetIdx) return
    const next = [...services]
    const [moved] = next.splice(from, 1)
    next.splice(targetIdx, 0, moved)
    setServices(next)
    try {
      await api.put('/protocol-mock/grpc/services/reorder', { items: next.map((r, i) => ({ id: r.id, sortOrder: i })) })
      await fetchServices()
    } catch { await fetchServices() }
  }

  const locked = !!form?.locked

  const handleToggleService = async () => {
    try {
      if (serviceStatus.running) {
        await api.post('/protocol-mock/grpc/stop')
        message.success('gRPC Mock 服务已停止')
      } else {
        await api.post('/protocol-mock/grpc/start')
        message.success('gRPC Mock 服务已启动')
      }
      setTimeout(fetchStatus, 500)
    } catch (e) {
      message.error(`操作失败: ${e?.response?.data?.error || e?.response?.data?.detail || e.message || '未知错误'}`)
    }
  }

  const handleClearLogs = async () => {
    try {
      await api.delete('/protocol-mock/grpc/logs')
      message.success('日志已清空')
      setExpandedLogId(null)
      setLogs([])
      setLogsTotal(0)
      setLogPage(1)
      fetchLogs(1)
    } catch {}
  }

  const formatJson = (str) => {
    if (!str) return ''
    try { return JSON.stringify(JSON.parse(str), null, 2) } catch { return str }
  }

  // ─── Config Tab ───
  const renderConfigTab = () => {
    if (!form) {
      return (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
          <Empty description={<span style={{ color: '#c9cdd4' }}>选择左侧 Service 查看配置</span>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
        </div>
      )
    }
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Header */}
        <div style={{
          padding: '10px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
            <Input
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              variant="borderless"
              disabled={locked}
              style={{ fontSize: 15, fontWeight: 600, width: 220, padding: '0 4px' }}
              placeholder="Service 名称"
            />
            {locked && <Tag color="orange" icon={<LockFilled />} style={{ borderRadius: 8 }}>已锁定</Tag>}
          </div>
          <Space size={8}>
            <Button type="primary" icon={<SaveOutlined />} size="small" onClick={handleSave} loading={saving} disabled={!isDirty || locked}>保存</Button>
            <Switch
              checked={form.enabled}
              onChange={v => handleToggle(form.id, v)}
              disabled={locked}
              checkedChildren="启用" unCheckedChildren="禁用" size="small"
            />
            <Button
              icon={locked ? <UnlockOutlined /> : <LockOutlined />}
              size="small"
              type={locked ? 'primary' : 'default'}
              ghost={locked}
              onClick={handleToggleLock}
            >{locked ? '解锁' : '锁定'}</Button>
            {locked ? (
              <Tooltip title="已锁定，无法删除">
                <Button icon={<DeleteOutlined />} size="small" danger disabled />
              </Tooltip>
            ) : (
              <Popconfirm title="确认删除？" onConfirm={() => handleDelete(form.id)}>
                <Button icon={<DeleteOutlined />} size="small" danger />
              </Popconfirm>
            )}
          </Space>
        </div>

        {/* Scrollable config */}
        <div style={{ flex: 1, overflow: 'auto', padding: '14px 16px' }}>
          {locked && <Alert type="warning" showIcon icon={<LockFilled />} message="此服务已锁定，配置为只读。点击右上角「解锁」后才能编辑。" style={{ marginBottom: 14, borderRadius: 12 }} />}
          {/* Service name + Method name */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Service 名称</div>
              <Input spellCheck={false} value={form.serviceName} onChange={e => setForm(f => ({ ...f, serviceName: e.target.value }))}
                placeholder="helloworld.Greeter" disabled={locked} style={{ fontFamily: MONO, fontSize: 12 }} />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Method 名称</div>
              <Input spellCheck={false} value={form.methodName} onChange={e => setForm(f => ({ ...f, methodName: e.target.value }))}
                placeholder="SayHello" disabled={locked} style={{ fontFamily: MONO, fontSize: 12 }} />
            </div>
          </div>

          {/* Method type + Delay + Status code */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Method 类型</div>
              <Radio.Group
                value={form.methodType || 'unary'}
                onChange={e => setForm(f => ({ ...f, methodType: e.target.value }))}
                disabled={locked}
                size="small"
              >
                <Radio value="unary">Unary</Radio>
                <Radio value="server_stream">Server Stream</Radio>
              </Radio.Group>
            </div>
            <div style={{ minWidth: 80 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>延迟 (ms)</div>
              <InputNumber value={form.delayMs ?? 0} onChange={v => setForm(f => ({ ...f, delayMs: v }))}
                min={0} step={100} size="small" disabled={locked} style={{ width: 80 }} placeholder="0" />
            </div>
            <div style={{ minWidth: 180 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Status Code</div>
              <Select value={form.statusCode ?? 0} onChange={v => setForm(f => ({ ...f, statusCode: v }))}
                size="small" disabled={locked} style={{ width: 180 }} options={GRPC_CODES} />
            </div>
          </div>

          {/* Status message (when statusCode != 0) */}
          {(form.statusCode !== 0 && form.statusCode != null) && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Status Message</div>
              <Input value={form.statusMessage} onChange={e => setForm(f => ({ ...f, statusMessage: e.target.value }))}
                placeholder="错误描述信息" disabled={locked} />
            </div>
          )}

          {/* Request sample */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Request Sample (JSON, 仅文档参考)</div>
            <TextArea spellCheck={false}
              value={form.requestSample}
              onChange={e => setForm(f => ({ ...f, requestSample: e.target.value }))}
              rows={3}
              disabled={locked}
              style={{ fontFamily: MONO, fontSize: 12 }}
              placeholder='{"name": "world"}'
            />
          </div>

          {/* Response body */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Response Body (JSON)</div>
            <TextArea spellCheck={false}
              value={form.responseBody}
              onChange={e => setForm(f => ({ ...f, responseBody: e.target.value }))}
              rows={5}
              disabled={locked}
              style={{ fontFamily: MONO, fontSize: 12 }}
              placeholder='{"message": "Hello, world!"}'
            />
          </div>

          {/* Stream items (only for server_stream) */}
          {form.methodType === 'server_stream' && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Stream Items (JSON Array)</div>
              <TextArea spellCheck={false}
                value={form.streamItems}
                onChange={e => setForm(f => ({ ...f, streamItems: e.target.value }))}
                rows={5}
                disabled={locked}
                style={{ fontFamily: MONO, fontSize: 12 }}
                placeholder={'[\n  {"message": "chunk 1"},\n  {"message": "chunk 2"},\n  {"message": "chunk 3"}\n]'}
              />
            </div>
          )}
        </div>
      </div>
    )
  }

  // ─── Test Tab ───
  const handleTest = async () => {
    if (!form || !serviceStatus.running) return
    setTesting(true)
    setTestResult(null)
    try {
      const r = await api.post('/protocol-mock/grpc/test', {
        service_name: form.serviceName,
        method_name: form.methodName,
        body: testBody || '{}',
      })
      setTestResult(r.data || r)
      fetchLogs()
    } catch (e) {
      setTestResult({ error: e?.response?.data?.error || e.message })
    } finally { setTesting(false) }
  }

  const renderTestTab = () => {
    if (!form) {
      return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
        <Empty description={<span style={{ color: '#c9cdd4' }}>选择左侧 Service 进行测试</span>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </div>
    }
    const target = `${window.location.hostname}:${serviceStatus.port}`
    const method = `/${form.serviceName}/${form.methodName}`
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div style={{ flex: 1, overflow: 'auto', padding: '14px 16px' }}>
          {/* Target info */}
          <div style={{ marginBottom: 16, padding: '10px 14px', borderRadius: 12, background: 'rgba(124,92,191,0.04)', border: '1px solid rgba(124,92,191,0.12)' }}>
            <div style={{ fontSize: 11, color: '#86909c', marginBottom: 6 }}>gRPC 端点</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <code style={{ fontFamily: MONO, fontSize: 13, color: ACCENT, fontWeight: 500 }}>{target}</code>
              <Button size="small" type="text" icon={<CopyOutlined />} onClick={() => { copyToClipboard(target); message.success('已复制') }} />
            </div>
            <div style={{ fontSize: 11, color: '#86909c', marginTop: 6 }}>Method</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <code style={{ fontFamily: MONO, fontSize: 12, color: '#4e5969' }}>{method}</code>
              <Button size="small" type="text" icon={<CopyOutlined />} onClick={() => { copyToClipboard(method); message.success('已复制') }} />
            </div>
          </div>

          {/* Request body */}
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Request Body (JSON)</div>
            <TextArea spellCheck={false}
              value={testBody}
              onChange={e => setTestBody(e.target.value)}
              rows={5}
              style={{ fontFamily: MONO, fontSize: 12 }}
              placeholder='{"name": "world"}'
            />
          </div>

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
              <span style={{ marginLeft: 8, fontSize: 12, color: '#ff7d00' }}>请先启动 gRPC Mock 服务</span>
            )}
          </div>

          {/* grpcurl hint */}
          <div style={{ marginBottom: 16, padding: '8px 12px', borderRadius: 10, background: 'rgba(0,0,0,0.02)', border: '1px solid rgba(0,0,0,0.04)' }}>
            <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>命令行测试</div>
            <div style={{ marginBottom: 6 }}>
              <span style={{ fontSize: 11, color: '#aaa', marginRight: 6 }}>查看服务列表</span>
              <code style={{ fontSize: 11, fontFamily: MONO, color: '#4e5969', wordBreak: 'break-all' }}>
                grpcurl -plaintext {target} list
              </code>
              <Button size="small" type="text" icon={<CopyOutlined />} style={{ marginLeft: 4 }}
                onClick={() => { copyToClipboard(`grpcurl -plaintext ${target} list`); message.success('已复制') }}
              />
            </div>
            <div>
              <span style={{ fontSize: 11, color: '#aaa', marginRight: 6 }}>调用方法</span>
              <code style={{ fontSize: 11, fontFamily: MONO, color: '#4e5969', wordBreak: 'break-all' }}>
                grpcurl -plaintext -d '{testBody || '{}'}' {target} {form.serviceName}/{form.methodName}
              </code>
              <Button size="small" type="text" icon={<CopyOutlined />} style={{ marginLeft: 4 }}
                onClick={() => {
                  copyToClipboard(`grpcurl -plaintext -d '${testBody || '{}'}' ${target} ${form.serviceName}/${form.methodName}`)
                  message.success('已复制')
                }}
              />
            </div>
            <div style={{ fontSize: 11, color: '#bbb', marginTop: 4 }}>
              JSON Mode: 请求/响应为 JSON raw bytes，grpcurl 的 -d 参数中的字段不会做 protobuf 编码
            </div>
          </div>

          {/* Result */}
          {testResult && (
            <div>
              {testResult.error && !testResult.target ? (
                <pre style={{
                  background: 'var(--red-bg)', color: '#e8453c', padding: 12, borderRadius: 12,
                  overflow: 'auto', fontSize: 11, lineHeight: 1.5, maxHeight: 200,
                  fontFamily: MONO, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  border: '1px solid #ffccc7',
                }}>{testResult.error}</pre>
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
                      {testResult.status_code != null && (
                        <Tag color={testResult.status_code === 0 ? 'green' : 'red'} style={{ margin: 0, fontSize: 11, borderRadius: 8 }}>
                          {testResult.status_message || (testResult.status_code === 0 ? 'OK' : `Code ${testResult.status_code}`)}
                        </Tag>
                      )}
                      {testResult.duration_ms != null && (
                        <span style={{ fontSize: 11, color: '#86909c' }}>{testResult.duration_ms}ms</span>
                      )}
                    </Space>
                    <Button size="small" icon={<CopyOutlined />} onClick={() => {
                      const text = [
                        `--- REQUEST ---`,
                        `Target: ${testResult.target || ''}`,
                        `Method: ${testResult.method || ''}`,
                        `Body:`,
                        formatJson(testResult.sent) || '',
                        '',
                        `--- RESPONSE ---`,
                        `Status: ${testResult.status_code ?? ''} ${testResult.status_message || ''}`,
                        `Body:`,
                        formatJson(testResult.received) || '',
                      ].join('\n')
                      copyToClipboard(text)
                      message.success('已复制完整请求/响应')
                    }}>复制全部</Button>
                  </div>

                  {/* Request section */}
                  <div style={{ padding: '10px 14px', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: ACCENT, marginBottom: 6, textTransform: 'uppercase', letterSpacing: 1 }}>Request</div>
                    <div style={{ display: 'flex', gap: 16, marginBottom: 6, fontSize: 12, fontFamily: MONO }}>
                      <div><span style={{ color: '#86909c' }}>Target: </span><span style={{ color: '#1d2129' }}>{testResult.target}</span></div>
                      <div><span style={{ color: '#86909c' }}>Method: </span><span style={{ color: '#1d2129' }}>{testResult.method}</span></div>
                    </div>
                    {testResult.sent && (
                      <pre style={{
                        background: 'rgba(0,0,0,0.02)', color: '#4e5969', padding: 8, borderRadius: 8,
                        fontSize: 11, fontFamily: MONO, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                        border: '1px solid rgba(0,0,0,0.04)', maxHeight: 120, overflow: 'auto', margin: 0,
                      }}>{formatJson(testResult.sent)}</pre>
                    )}
                  </div>

                  {/* Response section */}
                  <div style={{ padding: '10px 14px' }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: '#0ea5a0', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 1 }}>Response</div>
                    <pre style={{
                      ...CODE_BLOCK_STYLE, padding: 12, borderRadius: 10,
                      overflow: 'auto', fontSize: 11, lineHeight: 1.5, maxHeight: 250,
                      whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0,
                    }}>{formatJson(testResult.received) || '(empty)'}</pre>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    )
  }

  // ─── Logs Tab ───
  const renderLogsTab = () => (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{
        padding: '8px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0,
      }}>
        <span style={{ fontSize: 13, fontWeight: 500, color: '#1d2129' }}>共 {logsTotal} 条</span>
        <Space size={4}>
          <Select
            value={logServiceFilter}
            onChange={v => setLogServiceFilter(v)}
            allowClear
            placeholder="按 Service 筛选"
            size="small"
            style={{ width: 180 }}
          >
            {serviceNames.map(n => <Select.Option key={n} value={n}>{n}</Select.Option>)}
          </Select>
          <Button icon={<ReloadOutlined />} size="small" type="text" onClick={() => fetchLogs()} />
          <Popconfirm title="确认清空所有日志？" onConfirm={handleClearLogs}>
            <Button icon={<ClearOutlined />} size="small" type="text" danger />
          </Popconfirm>
        </Space>
      </div>

      <div style={{ flex: 1, overflow: 'auto' }}>
        <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: 'rgba(255,255,255,0.45)', position: 'sticky', top: 0, zIndex: 1 }}>
              {['时间', 'Service/Method', '类型', '客户端 IP', 'Status Code', '耗时'].map((h, i) => (
                <th key={h} style={{
                  padding: '6px 10px', textAlign: i >= 4 ? 'right' : 'left',
                  fontWeight: 500, fontSize: 11, color: '#86909c', borderBottom: '1px solid rgba(0,0,0,0.04)',
                  whiteSpace: 'nowrap',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {logs.map(l => (
              <Fragment key={l.id}>
                <tr onClick={() => setExpandedLogId(expandedLogId === l.id ? null : l.id)} style={{
                  cursor: 'pointer', borderBottom: '1px solid rgba(0,0,0,0.03)',
                  background: expandedLogId === l.id ? 'rgba(124,92,191,0.06)' : 'transparent',
                }}>
                  <td style={{ padding: '5px 10px', whiteSpace: 'nowrap', fontSize: 11, color: '#86909c' }}>
                    {new Date(l.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}
                  </td>
                  <td style={{ padding: '5px 10px', fontFamily: MONO, fontSize: 11, color: '#4e5969', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {l.serviceName || '-'}/{l.methodName || '-'}
                  </td>
                  <td style={{ padding: '5px 10px' }}>
                    <Tag color={METHOD_TYPE_COLOR[l.methodType] || 'default'} style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 6px', borderRadius: 8 }}>
                      {METHOD_TYPE_LABEL[l.methodType] || l.methodType || '-'}
                    </Tag>
                  </td>
                  <td style={{ padding: '5px 10px', fontSize: 11, color: '#86909c' }}>{l.clientIp || '-'}</td>
                  <td style={{ padding: '5px 10px', textAlign: 'right' }}>
                    <Tag color={(l.statusCode ?? 0) === 0 ? 'green' : 'red'} style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 6px', borderRadius: 8 }}>
                      {GRPC_CODE_LABELS[l.statusCode ?? 0] || (l.statusCode ?? 0)}
                    </Tag>
                  </td>
                  <td style={{ padding: '5px 10px', textAlign: 'right', fontSize: 11, color: '#86909c', whiteSpace: 'nowrap' }}>
                    {Math.round(l.durationMs ?? 0)}ms
                  </td>
                </tr>
                {expandedLogId === l.id && (
                  <tr>
                    <td colSpan={6} style={{ padding: '10px 16px', background: 'transparent', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                      <div style={{ display: 'flex', gap: 24, fontSize: 12 }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4, fontWeight: 500 }}>Request Body</div>
                          <pre style={{
                            maxHeight: 120, overflow: 'auto', margin: 0, padding: 8, borderRadius: 12,
                            background: 'transparent', border: '1px solid rgba(0,0,0,0.04)', fontSize: 11, fontFamily: MONO,
                            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                          }}>{formatJson(l.requestBody) || '-'}</pre>
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4, fontWeight: 500 }}>Response Body</div>
                          <pre style={{
                            maxHeight: 120, overflow: 'auto', margin: 0, padding: 8, borderRadius: 12,
                            background: 'transparent', border: '1px solid rgba(0,0,0,0.04)', fontSize: 11, fontFamily: MONO,
                            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                          }}>{formatJson(l.responseBody) || '-'}</pre>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {logs.length === 0 && (
              <tr><td colSpan={6} style={{ textAlign: 'center', padding: 40, color: '#c9cdd4', fontSize: 12 }}>暂无调用日志</td></tr>
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

      {/* ━━━ Top bar ━━━ */}
      <div style={{
        padding: '10px 20px', borderBottom: '1px solid rgba(0,0,0,0.03)', background: 'rgba(255,255,255,0.25)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <CloudServerOutlined style={{ fontSize: 18, color: ACCENT }} />
            <span style={{ fontWeight: 600, fontSize: 16, letterSpacing: 0.5 }}>gRPC Mock</span>
            <span style={{ fontSize: 11, color: '#86909c', marginLeft: 4 }}>(JSON mode)</span>
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
            {serviceStatus.servicesEnabled ?? 0}/{serviceStatus.servicesCount ?? 0} services · {serviceStatus.totalLogs ?? 0} 调用
          </span>
          <Tooltip title="客户端需使用 JSON 编码（如 grpcurl -plaintext）">
            <Tag color="purple" style={{ margin: 0, fontSize: 11, cursor: 'help' }}>JSON 编码</Tag>
          </Tooltip>
          <Tooltip title="gRPC 服务器反射协议版本。v1alpha 为旧版（Python grpcio 默认），v1 为新版。选「两者」可同时被两种客户端发现，兼容性最好。">
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 12, color: '#86909c', cursor: 'help' }}>反射</span>
              <Radio.Group
                value={serviceStatus.reflectionVersion || 'both'}
                onChange={async e => {
                  try {
                    await api.put('/protocol-mock/grpc/config', { reflectionVersion: e.target.value })
                    const label = { both: '两者 (v1 + v1alpha)', v1: 'v1', v1alpha: 'v1alpha' }[e.target.value]
                    message.success(`反射协议已设为 ${label}${serviceStatus.running ? '（服务已重启）' : ''}`)
                    setTimeout(fetchStatus, 600)
                  } catch {}
                }}
                size="small"
                buttonStyle="solid"
              >
                <Radio.Button value="both" style={{ fontSize: 11, padding: '0 8px' }}>两者</Radio.Button>
                <Radio.Button value="v1" style={{ fontSize: 11, padding: '0 8px' }}>v1</Radio.Button>
                <Radio.Button value="v1alpha" style={{ fontSize: 11, padding: '0 8px' }}>v1alpha</Radio.Button>
              </Radio.Group>
            </div>
          </Tooltip>
        </div>
        <Space size={8}>
          {serviceStatus.running && (
            <Button size="small" icon={<CopyOutlined />} onClick={() => {
              copyToClipboard(`${window.location.hostname}:${serviceStatus.port}`)
              message.success('已复制 gRPC 端点地址')
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

      {/* ━━━ Body: Left list + Right tabs ━━━ */}
      <div style={{ flex: 1, display: 'flex', gap: 0, minHeight: 0 }}>

        {/* Left panel: service list */}
        <div style={{
          width: 260, flexShrink: 0, background: 'transparent', borderRight: '1px solid rgba(0,0,0,0.05)',
          display: 'flex', flexDirection: 'column',
        }}>
          <div style={{
            padding: '10px 14px', borderBottom: '1px solid rgba(0,0,0,0.04)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span style={{ fontWeight: 600, fontSize: 13, color: '#1d2129' }}>Services</span>
            <Space size={4}>
              <Tooltip title="从预设创建">
                <Button icon={<AppstoreOutlined />} size="small" onClick={() => setPresetOpen(true)} disabled={presets.length === 0} />
              </Tooltip>
              <Tooltip title="新建 Service">
                <Button type="primary" ghost icon={<PlusOutlined />} size="small" onClick={handleCreate} />
              </Tooltip>
            </Space>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: '6px 8px' }}>
            {services.map((s, i) => {
              const sel = selectedId === s.id
              const isDragging = dragIdx === i
              return (
                <div
                  key={s.id}
                  onClick={() => selectService(s)}
                  draggable
                  onDragStart={() => setDragIdx(i)}
                  onDragOver={e => { e.preventDefault(); e.currentTarget.style.borderTop = `2px solid ${ACCENT}` }}
                  onDragLeave={e => { e.currentTarget.style.borderTop = '2px solid transparent' }}
                  onDrop={e => { e.preventDefault(); e.currentTarget.style.borderTop = '2px solid transparent'; handleDropService(i) }}
                  onDragEnd={() => setDragIdx(null)}
                  style={{
                    padding: '10px 12px', marginBottom: 4, borderRadius: 12, cursor: 'pointer',
                    background: sel ? 'rgba(124,92,191,0.06)' : 'transparent',
                    borderLeft: `3px solid ${sel ? ACCENT : s.enabled ? '#0ea5a0' : 'rgba(0,0,0,0.1)'}`,
                    borderTop: '2px solid transparent',
                    opacity: isDragging ? 0.4 : 1,
                    transition: 'background .15s, opacity .15s',
                  }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Tooltip title="拖动调整顺序">
                      <HolderOutlined
                        onClick={e => e.stopPropagation()}
                        style={{ color: '#c9cdd4', cursor: 'grab', fontSize: 12 }}
                      />
                    </Tooltip>
                    <span style={{
                      flex: 1, fontSize: 12, color: sel ? '#1d2129' : '#86909c',
                      fontWeight: sel ? 500 : 400,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{s.name}</span>
                    {s.locked && <LockFilled style={{ color: '#faad14', fontSize: 12 }} />}
                    <Tag color={METHOD_TYPE_COLOR[s.methodType] || 'default'} style={{
                      margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 5px', borderRadius: 8,
                    }}>{METHOD_TYPE_LABEL[s.methodType] || s.methodType || 'unary'}</Tag>
                  </div>
                  <div style={{ marginTop: 4, paddingLeft: 18 }}>
                    <span style={{
                      fontSize: 11, fontFamily: MONO, color: '#4e5969',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      display: 'block',
                    }}>{s.serviceName || '-'}/{s.methodName || '-'}</span>
                  </div>
                </div>
              )
            })}
            {services.length === 0 && (
              <Empty description="暂无 Service" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ marginTop: 60 }} />
            )}
          </div>
        </div>

        {/* Right panel: tabs */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: 'transparent' }}>
          <div style={{ borderBottom: '1px solid rgba(0,0,0,0.04)', paddingLeft: 16, flexShrink: 0 }}>
            <div style={{ display: 'flex', gap: 0 }}>
              {[
                { key: 'config', label: 'Service 配置' },
                { key: 'test', label: <>测试 <ThunderboltOutlined style={{ fontSize: 11 }} /></> },
                { key: 'logs', label: <>调用日志 <Tag style={{ margin: '0 0 0 4px', fontSize: 11, borderRadius: 12, lineHeight: '18px', padding: '0 6px' }}>{serviceStatus.totalLogs ?? 0}</Tag></> },
              ].map(t => (
                <div key={t.key} onClick={() => setActiveTab(t.key)} style={{
                  padding: '10px 16px', cursor: 'pointer', fontSize: 14, position: 'relative',
                  color: activeTab === t.key ? ACCENT : '#4e5969',
                  fontWeight: activeTab === t.key ? 500 : 400,
                  borderBottom: activeTab === t.key ? `2px solid ${ACCENT}` : '2px solid transparent',
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

      {/* ━━━ Preset Modal ━━━ */}
      <Modal title="从预设创建" open={presetOpen} onCancel={() => setPresetOpen(false)} footer={null} width={560}>
        {Object.entries(groupedPresets).map(([group, items]) => (
          <div key={group} style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 13, color: '#86909c', marginBottom: 8 }}>{group}</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {items.map(p => (
                <div
                  key={p.key || p.name}
                  onClick={() => handleCreateFromPreset(p)}
                  style={{
                    padding: '8px 14px', borderRadius: 8, cursor: 'pointer',
                    border: '1px solid rgba(0,0,0,0.08)', background: 'rgba(255,255,255,0.6)',
                    fontSize: 13, transition: 'all 0.2s',
                  }}
                  onMouseEnter={e => e.currentTarget.style.borderColor = ACCENT}
                  onMouseLeave={e => e.currentTarget.style.borderColor = 'rgba(0,0,0,0.08)'}
                >
                  <div style={{ fontWeight: 500 }}>{p.label || p.name}</div>
                  {p.description && <div style={{ fontSize: 11, color: '#86909c', marginTop: 2 }}>{p.description}</div>}
                </div>
              ))}
            </div>
          </div>
        ))}
        {presets.length === 0 && <Empty description="暂无预设" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
      </Modal>
    </div>
  )
}
