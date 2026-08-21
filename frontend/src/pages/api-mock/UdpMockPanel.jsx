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
const ACCENT = '#4e8af0'

export default function UdpMockPanel() {
  const [handlers, setHandlers] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [form, setForm] = useState(null)
  const [originalForm, setOriginalForm] = useState(null)
  const [logs, setLogs] = useState([])
  const [logsTotal, setLogsTotal] = useState(0)
  const [logPage, setLogPage] = useState(1)
  const [logPageSize] = useState(50)
  const [expandedLogId, setExpandedLogId] = useState(null)
  const [serviceStatus, setServiceStatus] = useState({ running: false, port: 28600, handlersCount: 0, handlersEnabled: 0, totalLogs: 0 })
  const [saving, setSaving] = useState(false)
  const [activeTab, setActiveTab] = useState('config')
  const [presets, setPresets] = useState([])
  const [presetOpen, setPresetOpen] = useState(false)
  const [testMessage, setTestMessage] = useState('Hello UDP')
  const [testHex, setTestHex] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [testing, setTesting] = useState(false)
  const [dragIdx, setDragIdx] = useState(null)
  const pollRef = useRef(null)

  useEffect(() => {
    fetchHandlers()
    fetchStatus()
    fetchLogs()
    fetchPresets()
    pollRef.current = setInterval(() => { fetchStatus() }, 5000)
    return () => clearInterval(pollRef.current)
  }, [])

  const fetchHandlers = async () => { try { const r = await api.get('/protocol-mock/udp/handlers'); setHandlers(r.data || r || []) } catch {} }
  const fetchStatus = async () => { try { const r = await api.get('/protocol-mock/udp/status'); setServiceStatus(r.data || r) } catch {} }
  const fetchLogs = async (page) => {
    try {
      const p = page || logPage
      const params = new URLSearchParams({ limit: String(logPageSize), offset: String((p - 1) * logPageSize) })
      const r = await api.get(`/protocol-mock/udp/logs?${params}`)
      const d = r.data || r
      setLogs(d.data || d || [])
      setLogsTotal(d.total ?? (d.data || d || []).length)
    } catch {}
  }

  const fetchPresets = async () => { try { const r = await api.get('/protocol-mock/udp/presets'); setPresets(r.data?.data || r.data || []) } catch {} }

  useEffect(() => {
    if (handlers.length > 0 && !selectedId) selectHandler(handlers[0])
  }, [handlers])

  const selectHandler = useCallback((h) => {
    const formData = { ...h }
    setSelectedId(h.id)
    setForm(formData)
    setOriginalForm(formData)
    setActiveTab('config')
    setTestMessage(h.matchPattern || 'Hello UDP')
    setTestHex(h.matchMode === 'hex')
    setTestResult(null)
  }, [])

  const isDirty = useMemo(() => {
    if (!form || !originalForm) return false
    const keys = ['name', 'matchMode', 'matchPattern', 'responseMode', 'responseData',
      'responseHex', 'delayMs', 'enabled']
    for (const k of keys) {
      if (form[k] !== originalForm[k]) return true
    }
    return false
  }, [form, originalForm])

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
      const n = handlers.length + 1
      const body = { name: `UDP Handler ${n}`, matchMode: 'exact', matchPattern: '', responseMode: 'echo', responseData: '', delayMs: 0 }
      const r = await api.post('/protocol-mock/udp/handlers', body)
      const d = r.data || r
      message.success('Handler 已创建')
      await fetchHandlers()
      selectHandler(d)
    } catch {}
  }

  const handleCreateFromPreset = async (preset) => {
    try {
      const body = {
        name: preset.name,
        matchMode: preset.matchMode,
        matchPattern: preset.matchPattern,
        responseMode: preset.responseMode,
        responseData: preset.responseData,
        responseHex: preset.responseHex || false,
        enabled: true,
      }
      const r = await api.post('/protocol-mock/udp/handlers', body)
      message.success(`已从预设创建: ${preset.label || preset.name}`)
      setPresetOpen(false)
      await fetchHandlers()
      selectHandler(r.data || r)
    } catch {}
  }

  const handleSave = async () => {
    if (!form) return
    setSaving(true)
    try {
      await api.put(`/protocol-mock/udp/handlers/${form.id}`, form)
      message.success('已保存')
      await fetchHandlers()
      setOriginalForm({ ...form })
    } catch {} finally { setSaving(false) }
  }

  const handleDelete = async (id) => {
    try {
      await api.delete(`/protocol-mock/udp/handlers/${id}`)
      message.success('已删除')
      if (selectedId === id) { setSelectedId(null); setForm(null); setOriginalForm(null) }
      await fetchHandlers()
    } catch {}
  }

  const handleToggle = async (id, checked) => {
    try {
      await api.patch(`/protocol-mock/udp/handlers/${id}/toggle`)
      await fetchHandlers()
      if (form && form.id === id) {
        setForm(f => ({ ...f, enabled: checked }))
        setOriginalForm(f => ({ ...f, enabled: checked }))
      }
    } catch {}
  }

  const handleToggleLock = async () => {
    if (!form) return
    try {
      const r = await api.patch(`/protocol-mock/udp/handlers/${form.id}/lock`)
      const d = r.data || r
      message.success(d.locked ? '处理器已锁定，配置只读' : '处理器已解锁')
      setForm(f => ({ ...f, locked: d.locked }))
      setOriginalForm(f => ({ ...f, locked: d.locked }))
      await fetchHandlers()
    } catch {}
  }

  const handleDropHandler = async (targetIdx) => {
    const from = dragIdx
    setDragIdx(null)
    if (from === null || from === targetIdx) return
    const next = [...handlers]
    const [moved] = next.splice(from, 1)
    next.splice(targetIdx, 0, moved)
    setHandlers(next)
    try {
      await api.put('/protocol-mock/udp/handlers/reorder', { items: next.map((r, i) => ({ id: r.id, sortOrder: i })) })
      await fetchHandlers()
    } catch { await fetchHandlers() }
  }

  const locked = !!form?.locked

  const handleToggleService = async () => {
    try {
      if (serviceStatus.running) {
        await api.post('/protocol-mock/udp/stop')
        message.success('UDP Mock 服务已停止')
      } else {
        await api.post('/protocol-mock/udp/start')
        message.success('UDP Mock 服务已启动')
      }
      setTimeout(fetchStatus, 500)
    } catch (e) {
      message.error(`操作失败: ${e?.response?.data?.error || e?.response?.data?.detail || e.message || '未知错误'}`)
    }
  }

  const handleClearLogs = async () => {
    try {
      await api.delete('/protocol-mock/udp/logs')
      message.success('日志已清空')
      setExpandedLogId(null)
      setLogs([])
      setLogsTotal(0)
      setLogPage(1)
      fetchLogs(1)
    } catch {}
  }

  // ─── Config Tab ───
  const renderConfigTab = () => {
    if (!form) {
      return (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
          <Empty description={<span style={{ color: '#c9cdd4' }}>选择左侧 Handler 查看配置</span>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
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
              style={{ fontSize: 15, fontWeight: 600, width: 200, padding: '0 4px' }}
              placeholder="Handler 名称"
              disabled={locked}
            />
            {locked && <Tag color="orange" icon={<LockFilled />} style={{ borderRadius: 8 }}>已锁定</Tag>}
          </div>
          <Space size={8}>
            <Button type="primary" icon={<SaveOutlined />} size="small" onClick={handleSave} loading={saving} disabled={!isDirty || locked}>保存</Button>
            <Switch
              checked={form.enabled}
              onChange={v => handleToggle(form.id, v)}
              checkedChildren="启用" unCheckedChildren="禁用" size="small"
              disabled={locked}
            />
            <Button
              icon={locked ? <UnlockOutlined /> : <LockOutlined />}
              size="small"
              type={locked ? 'primary' : 'default'}
              ghost={locked}
              onClick={handleToggleLock}
            >{locked ? '解锁' : '锁定'}</Button>
            {locked ? (
              <Tooltip title="锁定状态不可删除">
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
          {locked && (
            <Alert
              type="warning" showIcon icon={<LockFilled />}
              message="此处理器已锁定，配置为只读。点击右上角「解锁」后才能编辑。"
              style={{ marginBottom: 14, borderRadius: 12 }}
            />
          )}
          {/* Match mode */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>匹配模式</div>
              <Radio.Group
                value={form.matchMode || 'exact'}
                onChange={e => setForm(f => ({ ...f, matchMode: e.target.value }))}
                size="small"
                disabled={locked}
              >
                <Radio value="exact">精确</Radio>
                <Radio value="hex">Hex</Radio>
                <Radio value="regex">正则</Radio>
              </Radio.Group>
            </div>
            <div style={{ minWidth: 80 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>延迟 (ms)</div>
              <InputNumber value={form.delayMs ?? 0} onChange={v => setForm(f => ({ ...f, delayMs: v }))}
                min={0} step={100} size="small" style={{ width: 80 }} placeholder="0" disabled={locked} />
            </div>
          </div>

          {/* Match pattern */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>匹配规则</div>
            <TextArea spellCheck={false}
              value={form.matchPattern}
              onChange={e => setForm(f => ({ ...f, matchPattern: e.target.value }))}
              rows={3}
              style={{ fontFamily: MONO, fontSize: 12 }}
              placeholder={form.matchMode === 'hex' ? '十六进制匹配，如 48454C4C4F' : form.matchMode === 'regex' ? '正则表达式，如 ^PING' : '精确匹配内容'}
              disabled={locked}
            />
          </div>

          {/* Response mode */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>响应模式</div>
            <Radio.Group
              value={form.responseMode || 'echo'}
              onChange={e => setForm(f => ({ ...f, responseMode: e.target.value }))}
              buttonStyle="solid" size="small"
              disabled={locked}
            >
              <Radio.Button value="echo">Echo</Radio.Button>
              <Radio.Button value="fixed">固定响应</Radio.Button>
              <Radio.Button value="custom">自定义</Radio.Button>
            </Radio.Group>
          </div>

          {/* Response data */}
          {form.responseMode !== 'echo' && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 12, color: '#86909c' }}>响应数据</span>
                <Switch
                  checked={form.responseHex || false}
                  onChange={v => setForm(f => ({ ...f, responseHex: v }))}
                  checkedChildren="Hex" unCheckedChildren="Text" size="small"
                  disabled={locked}
                />
              </div>
              <TextArea spellCheck={false}
                value={form.responseData}
                onChange={e => setForm(f => ({ ...f, responseData: e.target.value }))}
                rows={5}
                style={{ fontFamily: MONO, fontSize: 12 }}
                placeholder={form.responseHex ? '十六进制响应数据，如 504F4E47' : '文本响应数据'}
                disabled={locked}
              />
            </div>
          )}
        </div>
      </div>
    )
  }

  /* ─── Test Tab ─── */
  const handleTest = async () => {
    if (!serviceStatus.running) return
    setTesting(true)
    setTestResult(null)
    try {
      const r = await api.post('/protocol-mock/udp/test', {
        message: testMessage || 'Hello',
        hex_mode: testHex,
      })
      setTestResult(r.data || r)
      fetchLogs()
    } catch (e) {
      setTestResult({ error: e?.response?.data?.error || e.message })
    } finally { setTesting(false) }
  }

  const renderTestTab = () => {
    const target = `${window.location.hostname}:${serviceStatus.port}`
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div style={{ flex: 1, overflow: 'auto', padding: '14px 16px' }}>
          {/* Target info */}
          <div style={{ marginBottom: 16, padding: '10px 14px', borderRadius: 12, background: 'rgba(24,144,255,0.04)', border: '1px solid rgba(24,144,255,0.12)' }}>
            <div style={{ fontSize: 11, color: '#86909c', marginBottom: 6 }}>UDP 端点</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <code style={{ fontFamily: MONO, fontSize: 13, color: ACCENT, fontWeight: 500 }}>{target}</code>
              <Button size="small" type="text" icon={<CopyOutlined />} onClick={() => { copyToClipboard(target); message.success('已复制') }} />
            </div>
          </div>

          {/* Message input */}
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <span style={{ fontSize: 12, color: '#86909c' }}>发送数据</span>
              <Radio.Group value={testHex} onChange={e => setTestHex(e.target.value)} size="small">
                <Radio.Button value={false}>文本</Radio.Button>
                <Radio.Button value={true}>Hex</Radio.Button>
              </Radio.Group>
            </div>
            <TextArea spellCheck={false}
              value={testMessage}
              onChange={e => setTestMessage(e.target.value)}
              rows={4}
              style={{ fontFamily: MONO, fontSize: 12 }}
              placeholder={testHex ? '48 65 6c 6c 6f' : 'Hello UDP'}
            />
          </div>

          {/* Send button */}
          <div style={{ marginBottom: 16 }}>
            <Button
              type="primary" icon={<SendOutlined />}
              loading={testing} onClick={handleTest}
              disabled={!serviceStatus.running}
            >
              {serviceStatus.running ? '发送数据' : '服务未启动'}
            </Button>
            {!serviceStatus.running && (
              <span style={{ marginLeft: 8, fontSize: 12, color: '#ff7d00' }}>请先启动 UDP Mock 服务</span>
            )}
          </div>

          {/* nc hint */}
          <div style={{ marginBottom: 16, padding: '8px 12px', borderRadius: 10, background: 'rgba(0,0,0,0.02)', border: '1px solid rgba(0,0,0,0.04)' }}>
            <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>命令行测试 (nc/netcat)</div>
            <code style={{ fontSize: 11, fontFamily: MONO, color: '#4e5969', wordBreak: 'break-all' }}>
              echo -n "{testMessage}" | nc -u -w1 {window.location.hostname} {serviceStatus.port}
            </code>
            <Button size="small" type="text" icon={<CopyOutlined />} style={{ marginLeft: 4 }}
              onClick={() => { copyToClipboard(`echo -n "${testMessage}" | nc -u -w1 ${window.location.hostname} ${serviceStatus.port}`); message.success('已复制') }}
            />
          </div>

          {/* Result */}
          {testResult && (
            <div>
              {testResult.error ? (
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
                      {testResult.received_bytes != null && (
                        <Tag style={{ margin: 0, fontSize: 11, borderRadius: 8 }}>{testResult.received_bytes} bytes</Tag>
                      )}
                      {testResult.duration_ms != null && (
                        <span style={{ fontSize: 11, color: '#86909c' }}>{testResult.duration_ms}ms</span>
                      )}
                    </Space>
                    <Button size="small" icon={<CopyOutlined />} onClick={() => {
                      const text = [
                        '--- REQUEST ---',
                        `Target: ${testResult.target || ''}`,
                        `Data (Text): ${testResult.sent || ''}`,
                        `Data (Hex): ${testResult.sent_hex || ''}`,
                        '',
                        '--- RESPONSE ---',
                        `Data (Text): ${testResult.received ?? '(无响应 - UDP 无匹配时不回复)'}`,
                        `Data (Hex): ${testResult.received_hex || ''}`,
                        `Bytes: ${testResult.received_bytes ?? 0}`,
                      ].join('\n')
                      copyToClipboard(text)
                      message.success('已复制完整请求/响应')
                    }}>复制全部</Button>
                  </div>

                  {/* Request section */}
                  <div style={{ padding: '10px 14px', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: ACCENT, marginBottom: 6, textTransform: 'uppercase', letterSpacing: 1 }}>Request</div>
                    <div style={{ fontSize: 12, fontFamily: MONO, color: '#1d2129', marginBottom: 6 }}>
                      <span style={{ color: '#86909c' }}>Target: </span>{testResult.target}
                    </div>
                    <div style={{ display: 'flex', gap: 12 }}>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Text</div>
                        <pre style={{
                          background: 'rgba(0,0,0,0.02)', color: '#4e5969', padding: 8, borderRadius: 8,
                          fontSize: 11, fontFamily: MONO, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                          border: '1px solid rgba(0,0,0,0.04)', maxHeight: 80, overflow: 'auto', margin: 0,
                        }}>{testResult.sent || ''}</pre>
                      </div>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Hex</div>
                        <pre style={{
                          background: 'rgba(0,0,0,0.02)', color: '#4e5969', padding: 8, borderRadius: 8,
                          fontSize: 11, fontFamily: MONO, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                          border: '1px solid rgba(0,0,0,0.04)', maxHeight: 80, overflow: 'auto', margin: 0,
                        }}>{testResult.sent_hex || ''}</pre>
                      </div>
                    </div>
                  </div>

                  {/* Response section */}
                  <div style={{ padding: '10px 14px' }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: '#0ea5a0', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 1 }}>Response</div>
                    <pre style={{
                      ...CODE_BLOCK_STYLE, padding: 12, borderRadius: 10,
                      overflow: 'auto', fontSize: 11, lineHeight: 1.5, maxHeight: 150,
                      whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: '0 0 6px 0',
                    }}>{testResult.received ?? '(无响应 - UDP 无匹配 handler 时不回复)'}</pre>
                    {testResult.received_hex && (
                      <div>
                        <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Hex</div>
                        <pre style={{
                          background: 'rgba(0,0,0,0.02)', color: '#4e5969', padding: 8, borderRadius: 8,
                          fontSize: 11, fontFamily: MONO, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                          border: '1px solid rgba(0,0,0,0.04)', maxHeight: 80, overflow: 'auto', margin: 0,
                        }}>{testResult.received_hex}</pre>
                      </div>
                    )}
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
              {['时间', '方向', '客户端 IP:Port', '数据大小'].map((h, i) => (
                <th key={h} style={{
                  padding: '6px 10px', textAlign: i >= 3 ? 'right' : 'left',
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
                  background: expandedLogId === l.id ? `rgba(24,144,255,0.06)` : 'transparent',
                }}>
                  <td style={{ padding: '5px 10px', whiteSpace: 'nowrap', fontSize: 11, color: '#86909c' }}>
                    {new Date(l.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}
                  </td>
                  <td style={{ padding: '5px 10px' }}>
                    <Tag color={l.direction === 'in' ? 'blue' : 'green'} style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 6px', borderRadius: 8 }}>
                      {l.direction === 'in' ? 'IN' : 'OUT'}
                    </Tag>
                  </td>
                  <td style={{ padding: '5px 10px', fontFamily: MONO, fontSize: 11, color: '#4e5969' }}>
                    {l.clientIp || '-'}:{l.clientPort || '-'}
                  </td>
                  <td style={{ padding: '5px 10px', textAlign: 'right', fontSize: 11, color: '#86909c', whiteSpace: 'nowrap' }}>
                    {l.dataSize || 0} B
                  </td>
                </tr>
                {expandedLogId === l.id && (
                  <tr>
                    <td colSpan={4} style={{ padding: '10px 16px', background: 'transparent', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                      <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4, fontWeight: 500 }}>数据内容</div>
                      <pre style={{
                        maxHeight: 120, overflow: 'auto', margin: 0, padding: 8, borderRadius: 12,
                        background: 'transparent', border: '1px solid rgba(0,0,0,0.04)', fontSize: 11, fontFamily: MONO,
                        whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                      }}>{l.data || l.raw_data || '-'}</pre>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {logs.length === 0 && (
              <tr><td colSpan={4} style={{ textAlign: 'center', padding: 40, color: '#c9cdd4', fontSize: 12 }}>暂无通信日志</td></tr>
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
            <span style={{ fontWeight: 600, fontSize: 16, letterSpacing: 0.5 }}>UDP Mock</span>
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
            {serviceStatus.handlersEnabled ?? 0}/{serviceStatus.handlersCount ?? 0} handlers · {serviceStatus.totalLogs ?? 0} 日志
          </span>
        </div>
        <Space size={8}>
          {serviceStatus.running && (
            <Button size="small" icon={<CopyOutlined />} onClick={() => {
              copyToClipboard(`${window.location.hostname}:${serviceStatus.port} (UDP Datagram)`)
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

      {/* ━━━ Body: Left list + Right tabs ━━━ */}
      <div style={{ flex: 1, display: 'flex', gap: 0, minHeight: 0 }}>

        {/* Left panel: handler list */}
        <div style={{
          width: 260, flexShrink: 0, background: 'transparent', borderRight: '1px solid rgba(0,0,0,0.05)',
          display: 'flex', flexDirection: 'column',
        }}>
          <div style={{
            padding: '10px 14px', borderBottom: '1px solid rgba(0,0,0,0.04)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span style={{ fontWeight: 600, fontSize: 13, color: '#1d2129' }}>Handlers</span>
            <Space size={4}>
              <Tooltip title="从预设创建">
                <Button icon={<AppstoreOutlined />} size="small" onClick={() => setPresetOpen(true)} disabled={presets.length === 0} />
              </Tooltip>
              <Tooltip title="新建 Handler">
                <Button type="primary" ghost icon={<PlusOutlined />} size="small" onClick={handleCreate} />
              </Tooltip>
            </Space>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: '6px 8px' }}>
            {handlers.map((h, i) => {
              const sel = selectedId === h.id
              const isDragging = dragIdx === i
              return (
                <div key={h.id} onClick={() => selectHandler(h)}
                  draggable
                  onDragStart={e => { e.dataTransfer.effectAllowed = 'move'; setDragIdx(i) }}
                  onDragOver={e => { e.preventDefault(); e.currentTarget.style.borderTop = `2px solid ${ACCENT}` }}
                  onDragLeave={e => { e.currentTarget.style.borderTop = '2px solid transparent' }}
                  onDrop={e => { e.preventDefault(); e.currentTarget.style.borderTop = '2px solid transparent'; handleDropHandler(i) }}
                  onDragEnd={() => setDragIdx(null)}
                  style={{
                    padding: '10px 12px', marginBottom: 4, borderRadius: 12, cursor: 'pointer',
                    background: sel ? `rgba(24,144,255,0.06)` : 'transparent',
                    borderLeft: `3px solid ${sel ? ACCENT : h.enabled ? '#0ea5a0' : 'rgba(0,0,0,0.1)'}`,
                    borderTop: '2px solid transparent',
                    transition: 'background .15s', position: 'relative', opacity: isDragging ? 0.4 : 1,
                  }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Tooltip title="拖动调整顺序">
                      <HolderOutlined onClick={e => e.stopPropagation()} style={{ fontSize: 12, color: '#c9cdd4', cursor: 'grab' }} />
                    </Tooltip>
                    <Tag color="blue" style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 6px', borderRadius: 8 }}>
                      {(h.matchMode || 'exact').toUpperCase()}
                    </Tag>
                    <span style={{
                      flex: 1, fontSize: 12, color: sel ? '#1d2129' : '#86909c',
                      fontWeight: sel ? 500 : 400,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{h.name}</span>
                    {h.locked && <Tooltip title="已锁定，不可编辑"><LockFilled style={{ fontSize: 11, color: '#ff7d00' }} /></Tooltip>}
                    {h.locked ? (
                      <Tooltip title="锁定状态不可删除"><DeleteOutlined style={{ fontSize: 11, color: '#e8e8e8', cursor: 'not-allowed' }} onClick={e => e.stopPropagation()} /></Tooltip>
                    ) : (
                      <Popconfirm title="确认删除？" onConfirm={(e) => { e?.stopPropagation?.(); handleDelete(h.id) }}>
                        <DeleteOutlined onClick={e => e.stopPropagation()} style={{ fontSize: 11, color: '#c9cdd4', cursor: 'pointer' }} />
                      </Popconfirm>
                    )}
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 6 }}>
                    <span style={{
                      fontSize: 11, fontFamily: MONO, color: '#4e5969',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 160,
                    }}>{h.matchPattern || '(empty)'}</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      {!h.enabled && <Tag style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 4px', borderRadius: 6, color: '#c9cdd4', borderColor: '#c9cdd4', background: 'transparent' }}>禁用</Tag>}
                      <Tag color={h.responseMode === 'echo' ? 'cyan' : h.responseMode === 'fixed' ? 'blue' : 'purple'} style={{
                        margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 5px', borderRadius: 8,
                      }}>{h.responseMode || 'echo'}</Tag>
                    </div>
                  </div>
                </div>
              )
            })}
            {handlers.length === 0 && (
              <Empty description="暂无 Handler" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ marginTop: 60 }} />
            )}
          </div>
        </div>

        {/* Right panel: tabs */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: 'transparent' }}>
          <div style={{ borderBottom: '1px solid rgba(0,0,0,0.04)', paddingLeft: 16, flexShrink: 0 }}>
            <div style={{ display: 'flex', gap: 0 }}>
              {[
                { key: 'config', label: 'Handler 配置' },
                { key: 'test', label: <>测试 <ThunderboltOutlined style={{ fontSize: 11 }} /></> },
                { key: 'logs', label: <>通信日志 <Tag style={{ margin: '0 0 0 4px', fontSize: 11, borderRadius: 12, lineHeight: '18px', padding: '0 6px' }}>{serviceStatus.totalLogs ?? 0}</Tag></> },
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
