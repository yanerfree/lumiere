import { useState, useEffect, useRef, useMemo, useCallback, Fragment } from 'react'
import {
  Button, Space, Input, Tag, Radio, Popconfirm, Tooltip, Badge, Pagination,
  Empty, Typography, Switch, message, Drawer, Modal, Alert
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, SaveOutlined, PlayCircleOutlined, PauseCircleOutlined,
  ReloadOutlined, ClearOutlined, CopyOutlined, ApiOutlined, EditOutlined,
  LockOutlined, LockFilled, UnlockOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'
import { CODE_BLOCK_STYLE } from '../../components/MockCodeBlock'

const { Text } = Typography
const { TextArea } = Input

const MONO = 'var(--font-mono)'

const MODE_LABEL = { success: '成功', error: '失败', custom: '自定义' }
const MODE_COLOR = { success: '#0ea5a0', error: '#e8453c', custom: '#4e8af0' }

export default function McpMock() {
  const [tools, setTools] = useState([])
  const [selectedName, setSelectedName] = useState(null)
  const [toolForm, setToolForm] = useState(null)
  const [originalForm, setOriginalForm] = useState(null)
  const [saving, setSaving] = useState(false)
  const [activeTab, setActiveTab] = useState('config')
  const [callResult, setCallResult] = useState(null)
  const [callArgs, setCallArgs] = useState('{}')
  const [calling, setCalling] = useState(false)
  const [logs, setLogs] = useState([])
  const [logsTotal, setLogsTotal] = useState(0)
  const [logPage, setLogPage] = useState(1)
  const [expandedLogId, setExpandedLogId] = useState(null)
  const [serviceStatus, setServiceStatus] = useState({ running: false, port: 28300, transport: 'streamable-http', toolsCount: 0, toolsEnabled: 0, totalLogs: 0 })
  const [createOpen, setCreateOpen] = useState(false)
  const [newToolName, setNewToolName] = useState('')
  const [newToolDesc, setNewToolDesc] = useState('')
  const pollRef = useRef(null)

  useEffect(() => {
    fetchTools()
    fetchStatus()
    fetchLogs()
    pollRef.current = setInterval(fetchStatus, 5000)
    return () => clearInterval(pollRef.current)
  }, [])

  const fetchTools = async () => {
    try {
      const r = await api.get('/mcp-mock/tools')
      setTools(r.data || [])
    } catch {}
  }
  const fetchStatus = async () => {
    try {
      const r = await api.get('/mcp-mock/status')
      setServiceStatus(r.data || r)
    } catch {}
  }
  const fetchLogs = async (page) => {
    try {
      const p = page || logPage
      const r = await api.get(`/mcp-mock/logs?limit=50&offset=${(p - 1) * 50}`)
      const d = r.data || r
      setLogs(d.data || [])
      setLogsTotal(d.total ?? 0)
    } catch {}
  }

  useEffect(() => {
    if (tools.length > 0 && !selectedName) selectTool(tools[0])
  }, [tools])

  const selectTool = useCallback((t) => {
    setSelectedName(t.name)
    setToolForm({ ...t })
    setOriginalForm({ ...t })
    setCallResult(null)
    setCallArgs('{}')
    setActiveTab('config')
  }, [])

  const isDirty = useMemo(() => {
    if (!toolForm || !originalForm) return false
    return toolForm.mode !== originalForm.mode || toolForm.enabled !== originalForm.enabled ||
      toolForm.description !== originalForm.description
  }, [toolForm, originalForm])

  const handleSaveTool = async () => {
    if (!toolForm) return
    setSaving(true)
    try {
      await api.put(`/mcp-mock/tools/${toolForm.name}`, {
        mode: toolForm.mode,
        enabled: toolForm.enabled,
        description: toolForm.description,
      })
      message.success('已保存')
      await fetchTools()
      setOriginalForm({ ...toolForm })
    } catch {} finally { setSaving(false) }
  }

  const handleDeleteTool = async (name) => {
    try {
      const r = await api.delete(`/mcp-mock/tools/${name}`)
      if (r.error) { message.error(r.error); return }
      message.success('已删除')
      if (r.reloadError) message.warning(r.reloadError)
      if (selectedName === name) { setSelectedName(null); setToolForm(null); setOriginalForm(null) }
      await fetchTools()
    } catch {}
  }

  const handleToggle = async (name, checked) => {
    try {
      await api.patch(`/mcp-mock/tools/${name}/toggle`)
      await fetchTools()
      if (toolForm && toolForm.name === name) {
        setToolForm(f => ({ ...f, enabled: checked }))
        setOriginalForm(f => ({ ...f, enabled: checked }))
      }
    } catch {}
  }

  const handleToggleLock = async () => {
    if (!toolForm) return
    try {
      const r = await api.patch(`/mcp-mock/tools/${toolForm.name}/lock`)
      const d = r.data || r
      message.success(d.locked ? '工具已锁定，需解锁后才能编辑' : '工具已解锁')
      setToolForm(f => ({ ...f, locked: d.locked }))
      setOriginalForm(f => ({ ...f, locked: d.locked }))
      await fetchTools()
    } catch {}
  }

  const handleModeSwitch = (mode) => {
    setToolForm(f => ({ ...f, mode }))
  }

  const handleToggleService = async () => {
    try {
      if (serviceStatus.running) {
        await api.post('/mcp-mock/stop'); message.success('MCP Mock 服务已停止')
      } else {
        await api.post('/mcp-mock/start'); message.success('MCP Mock 服务已启动')
      }
      setTimeout(fetchStatus, 500)
    } catch (e) {
      message.error(`操作失败: ${e.message || '未知错误'}`)
    }
  }

  const handleCreateTool = async () => {
    if (!newToolName.trim()) { message.warning('请输入工具名称'); return }
    try {
      const r = await api.post('/mcp-mock/tools', {
        name: newToolName.trim(),
        description: newToolDesc.trim(),
        successData: { result: 'ok' },
      })
      if (r.error) { message.error(r.error); return }
      message.success('工具已创建')
      if (r.data?.reloadError) message.warning(r.data.reloadError)
      setCreateOpen(false)
      setNewToolName('')
      setNewToolDesc('')
      await fetchTools()
      selectTool(r.data || { name: newToolName.trim(), description: newToolDesc.trim(), mode: 'success', enabled: true })
    } catch {}
  }

  const handleCall = async () => {
    if (!toolForm) return
    setCalling(true)
    try {
      let args = {}
      try { args = JSON.parse(callArgs) } catch { message.error('参数 JSON 格式错误'); setCalling(false); return }
      const r = await api.post('/mcp-mock/call', { tool: toolForm.name, arguments: args })
      setCallResult(r)
      fetchLogs()
    } catch (e) { setCallResult({ error: e.message }) } finally { setCalling(false) }
  }

  const handleClearLogs = async () => {
    try {
      await api.delete('/mcp-mock/logs')
      message.success('日志已清空')
      setLogs([]); setLogsTotal(0); setLogPage(1)
    } catch {}
  }

  const mcpUrl = serviceStatus.running
    ? `http://${window.location.hostname}:${serviceStatus.port}/`
    : null

  const locked = !!toolForm?.locked

  // ─── 工具配置 Tab ───
  const renderConfigTab = () => {
    if (!toolForm) {
      return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
        <Empty description={<span style={{ color: '#c9cdd4' }}>选择左侧工具查看配置</span>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </div>
    }
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* 工具头部 */}
        <div style={{
          padding: '10px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0,
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {locked && <Tag color="orange" icon={<LockFilled />} style={{ margin: 0, fontSize: 11 }}>已锁定</Tag>}
            <Text code style={{ fontSize: 13 }}>{toolForm.name}</Text>
          </div>
          <Space size={8}>
            <Button type="primary" icon={<SaveOutlined />} size="small" onClick={handleSaveTool} loading={saving} disabled={!isDirty || locked}>保存</Button>
            <Switch checked={toolForm.enabled} onChange={v => handleToggle(toolForm.name, v)}
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
            {locked ? (
              <Tooltip title="已锁定，请先解锁"><Button icon={<DeleteOutlined />} size="small" danger disabled /></Tooltip>
            ) : tools.length > 1 ? (
              <Popconfirm title="确认删除该工具？" onConfirm={() => handleDeleteTool(toolForm.name)}>
                <Button icon={<DeleteOutlined />} size="small" danger />
              </Popconfirm>
            ) : (
              <Tooltip title="至少保留一个工具"><Button icon={<DeleteOutlined />} size="small" disabled /></Tooltip>
            )}
          </Space>
        </div>

        {/* 可滚动配置区 */}
        <div style={{ flex: 1, overflow: 'auto', padding: '14px 16px' }}>
          {locked && (
            <Alert
              type="warning" showIcon style={{ marginBottom: 12, fontSize: 12 }}
              message="此工具已锁定，配置为只读。点击右上角「解锁」后才能编辑。"
            />
          )}
          {/* 描述 */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>工具描述</div>
            <Input value={toolForm.description} onChange={e => setToolForm(f => ({ ...f, description: e.target.value }))}
              placeholder="工具描述..." size="small" disabled={locked} />
          </div>

          {/* MCP 地址 */}
          {mcpUrl && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16,
              padding: '6px 12px', background: 'var(--green-bg)', border: '1px solid rgba(14,165,160,0.3)', borderRadius: 12,
            }}>
              <ApiOutlined style={{ color: '#0ea5a0', fontSize: 12 }} />
              <span style={{ fontSize: 12, fontFamily: MONO, color: '#0ea5a0', flex: 1, userSelect: 'all' }}>{mcpUrl}</span>
              <Button size="small" type="text" icon={<CopyOutlined />} style={{ color: '#0ea5a0' }}
                onClick={() => { copyToClipboard(mcpUrl); message.success('已复制 MCP 地址') }} />
            </div>
          )}
          {!serviceStatus.running && (
            <div style={{ fontSize: 12, color: '#c9cdd4', marginBottom: 16 }}>
              服务未启动，启动后显示 MCP 访问地址
            </div>
          )}

          {/* 响应模式 */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>响应模式</div>
            <Radio.Group value={toolForm.mode} onChange={e => handleModeSwitch(e.target.value)}
              buttonStyle="solid" size="small" disabled={locked}>
              <Radio.Button value="success"><span style={{ color: toolForm.mode === 'success' ? '#fff' : '#0ea5a0' }}>成功</span></Radio.Button>
              <Radio.Button value="error"><span style={{ color: toolForm.mode === 'error' ? '#fff' : '#e8453c' }}>失败</span></Radio.Button>
              <Radio.Button value="custom"><span style={{ color: toolForm.mode === 'custom' ? '#fff' : '#4e8af0' }}>自定义</span></Radio.Button>
            </Radio.Group>
          </div>

          {/* 自定义响应编辑 */}
          {toolForm.mode === 'custom' && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>自定义响应 (JSON)</div>
              <TextArea spellCheck={false}
                rows={8}
                value={typeof toolForm.customData === 'string' ? toolForm.customData : JSON.stringify(toolForm.customData, null, 2) || ''}
                onChange={e => setToolForm(f => ({ ...f, customData: e.target.value }))}
                disabled={locked}
                style={{ fontFamily: MONO, fontSize: 12, borderRadius: 12 }}
                placeholder='{"result": "custom data"}'
              />
              <div style={{ marginTop: 8 }}>
                <Radio.Group value={toolForm.customIsError || false} disabled={locked}
                  onChange={e => setToolForm(f => ({ ...f, customIsError: e.target.value }))} size="small">
                  <Radio value={false}><Tag color="cyan" style={{ margin: 0 }}>isError: false</Tag></Radio>
                  <Radio value={true}><Tag color="error" style={{ margin: 0 }}>isError: true</Tag></Radio>
                </Radio.Group>
              </div>
              <Button type="primary" size="small" style={{ marginTop: 8 }} disabled={locked} onClick={async () => {
                let customData = null
                const raw = toolForm.customData
                if (raw && typeof raw === 'string' && raw.trim()) {
                  try { customData = JSON.parse(raw) } catch { message.error('JSON 格式错误'); return }
                } else if (raw && typeof raw === 'object') {
                  customData = raw
                }
                try {
                  await api.put(`/mcp-mock/tools/${toolForm.name}`, {
                    mode: 'custom',
                    custom_data: customData,
                    custom_is_error: toolForm.customIsError || false,
                  })
                  message.success('自定义配置已保存')
                  fetchTools()
                } catch {}
              }}>保存自定义</Button>
            </div>
          )}

          {/* 参数信息 */}
          {toolForm.params && Object.keys(toolForm.params).length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>参数定义</div>
              <div style={{
                padding: '8px 12px', background: 'rgba(0,0,0,0.02)', borderRadius: 12,
                border: '1px solid rgba(0,0,0,0.04)', fontSize: 12, fontFamily: MONO,
              }}>
                {Object.entries(toolForm.params).map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', gap: 8, padding: '2px 0' }}>
                    <span style={{ color: '#4e8af0' }}>{k}</span>
                    <span style={{ color: '#86909c' }}>{v}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 调用测试 */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>调用测试</div>
            <TextArea spellCheck={false} rows={3} value={callArgs} onChange={e => setCallArgs(e.target.value)}
              style={{ fontFamily: MONO, fontSize: 12, borderRadius: 12, marginBottom: 8 }}
              placeholder='{"branch_id": "xxx"}' />
            <Button type="primary" icon={calling ? null : <PlayCircleOutlined />}
              loading={calling} onClick={handleCall} size="small">发送调用</Button>
          </div>

          {/* 调用结果 */}
          {callResult && (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 12, color: '#86909c', fontWeight: 500 }}>调用结果</span>
                {callResult.source && <Tag color={callResult.source === 'mock' ? 'orange' : 'green'} style={{ margin: 0, fontSize: 11 }}>
                  {callResult.source === 'mock' ? 'Mock' : '真实'}</Tag>}
              </div>
              <pre style={{
                ...CODE_BLOCK_STYLE, padding: 12, borderRadius: 12,
                overflow: 'auto', fontSize: 11, lineHeight: 1.5, maxHeight: 200,
                whiteSpace: 'pre-wrap', wordBreak: 'break-all',
              }}>{JSON.stringify(callResult.data || callResult.error || callResult, null, 2)}</pre>
            </div>
          )}
        </div>
      </div>
    )
  }

  // ─── 调用日志 Tab ───
  const renderLogsTab = () => (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{
        padding: '8px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0,
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <span style={{ fontSize: 13, fontWeight: 500, color: '#1d2129' }}>共 {logsTotal} 条</span>
        <Space size={4}>
          <Button icon={<ReloadOutlined />} size="small" type="text" onClick={() => fetchLogs()} />
          <Popconfirm title="确认清空？" onConfirm={handleClearLogs}>
            <Button icon={<ClearOutlined />} size="small" type="text" danger />
          </Popconfirm>
        </Space>
      </div>

      <div style={{ flex: 1, overflow: 'auto' }}>
        <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: 'rgba(255,255,255,0.45)', position: 'sticky', top: 0, zIndex: 1 }}>
              {['时间', '工具', '来源', '模式', '状态', '耗时'].map((h, i) => (
                <th key={h} style={{
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
                <tr onClick={() => setExpandedLogId(expandedLogId === l.id ? null : l.id)} style={{
                  cursor: 'pointer', borderBottom: '1px solid rgba(0,0,0,0.03)',
                  background: expandedLogId === l.id ? 'rgba(124,92,191,0.08)' : 'transparent',
                }}>
                  <td style={{ padding: '5px 10px', whiteSpace: 'nowrap', fontSize: 11, color: '#86909c' }}>
                    {new Date(l.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}
                  </td>
                  <td style={{ padding: '5px 10px' }}>
                    <Text code style={{ fontSize: 11 }}>{l.tool}</Text>
                  </td>
                  <td style={{ padding: '5px 10px' }}>
                    <Tag color={l.source === 'mock' || l.source === 'mock-server' ? 'orange' : 'cyan'} style={{ margin: 0, fontSize: 11 }}>{l.source}</Tag>
                  </td>
                  <td style={{ padding: '5px 10px', fontSize: 11, color: '#86909c' }}>{MODE_LABEL[l.mode] || l.mode}</td>
                  <td style={{ padding: '5px 10px' }}>
                    <Tag color={l.isError ? 'red' : 'cyan'} style={{ margin: 0, fontSize: 11 }}>{l.isError ? '失败' : '成功'}</Tag>
                  </td>
                  <td style={{ padding: '5px 10px', textAlign: 'right', fontSize: 11, color: '#86909c', whiteSpace: 'nowrap' }}>{l.elapsedMs}ms</td>
                </tr>
                {expandedLogId === l.id && (
                  <tr>
                    <td colSpan={6} style={{ padding: '10px 16px', background: 'transparent', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                      <div style={{ display: 'flex', gap: 24, fontSize: 12 }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4, fontWeight: 500 }}>请求参数</div>
                          <pre style={{
                            maxHeight: 120, overflow: 'auto', margin: 0, padding: 8, borderRadius: 12,
                            background: 'transparent', border: '1px solid rgba(0,0,0,0.04)', fontSize: 11, fontFamily: MONO,
                            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                          }}>{JSON.stringify(l.arguments, null, 2)}</pre>
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4, fontWeight: 500 }}>响应结果</div>
                          <pre style={{
                            maxHeight: 120, overflow: 'auto', margin: 0, padding: 8, borderRadius: 12,
                            background: 'transparent', border: '1px solid rgba(0,0,0,0.04)', fontSize: 11, fontFamily: MONO,
                            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                          }}>{(() => { try { return JSON.stringify(JSON.parse(l.response), null, 2) } catch { return l.response } })()}</pre>
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

      {logsTotal > 50 && (
        <div style={{ padding: '8px 16px', borderTop: '1px solid rgba(0,0,0,0.04)', flexShrink: 0, textAlign: 'right' }}>
          <Pagination size="small" current={logPage} pageSize={50} total={logsTotal}
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
        padding: '10px 20px', background: 'transparent', borderBottom: '1px solid rgba(0,0,0,0.03)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <ApiOutlined style={{ fontSize: 18, color: '#7c5cbf' }} />
            <span style={{ fontWeight: 600, fontSize: 16, letterSpacing: 0.5 }}>MCP Mock</span>
          </div>
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '2px 10px', borderRadius: 12,
            background: serviceStatus.running ? '#e0f7f6' : 'rgba(0,0,0,0.04)',
            border: `1px solid ${serviceStatus.running ? 'rgba(14,165,160,0.3)' : 'rgba(0,0,0,0.1)'}`,
          }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: serviceStatus.running ? '#0ea5a0' : '#c9cdd4' }} />
            <span style={{ fontSize: 12, fontWeight: 600, fontFamily: 'var(--font-mono)', color: serviceStatus.running ? '#0ea5a0' : '#999' }}>
              {serviceStatus.running ? 'LIVE' : 'STOPPED'}
            </span>
          </div>
          {serviceStatus.running && (
            <Tag style={{ margin: 0, fontSize: 11, borderRadius: 12, fontFamily: MONO }}>
              :{serviceStatus.port}
            </Tag>
          )}
          <Radio.Group
            value={serviceStatus.transport || 'streamable-http'}
            onChange={async e => {
              try {
                await api.put('/mcp-mock/config', { transport: e.target.value })
                message.success(`传输协议已切换为 ${e.target.value === 'sse' ? 'SSE' : 'Streamable HTTP'}`)
                setTimeout(fetchStatus, 500)
              } catch {}
            }}
            size="small"
            buttonStyle="solid"
          >
            <Radio.Button value="streamable-http" style={{ fontSize: 11, padding: '0 8px' }}>Streamable HTTP</Radio.Button>
            <Radio.Button value="sse" style={{ fontSize: 11, padding: '0 8px' }}>SSE</Radio.Button>
          </Radio.Group>
          <span style={{ fontSize: 12, color: '#86909c' }}>
            {serviceStatus.toolsEnabled}/{serviceStatus.toolsCount} 个工具
          </span>
        </div>
        <Space size={12}>
          {mcpUrl && (
            <Button size="small" icon={<CopyOutlined />} onClick={() => {
              copyToClipboard(mcpUrl).then(() => message.success('已复制 MCP 地址'))
            }}>复制地址</Button>
          )}
          <Button type={serviceStatus.running ? 'default' : 'primary'} size="small"
            icon={serviceStatus.running ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
            onClick={handleToggleService}>
            {serviceStatus.running ? '停止' : '启动'}
          </Button>
        </Space>
      </div>

      {/* ━━━ 主体 ━━━ */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>

        {/* 左面板：工具列表 */}
        <div style={{ width: 260, flexShrink: 0, borderRight: '1px solid rgba(0,0,0,0.04)', display: 'flex', flexDirection: 'column', background: 'transparent' }}>
          <div style={{
            padding: '10px 14px', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0,
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span style={{ fontWeight: 600, fontSize: 13, color: '#1d2129' }}>工具</span>
            <Button type="primary" ghost size="small" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新增</Button>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: '6px 8px' }}>
            {tools.map(t => {
              const sel = t.name === selectedName
              return (
                <div key={t.name} onClick={() => selectTool(t)} style={{
                  padding: '8px 10px', cursor: 'pointer', marginBottom: 4, borderRadius: 12,
                  borderLeft: `3px solid ${sel ? '#7c5cbf' : 'transparent'}`,
                  background: sel ? 'rgba(124,92,191,0.06)' : 'transparent',
                  transition: 'all 0.15s',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 2 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, minWidth: 0 }}>
                      {t.locked && (
                        <Tooltip title="已锁定，不可编辑">
                          <LockFilled style={{ fontSize: 11, color: '#ff7d00', flexShrink: 0 }} />
                        </Tooltip>
                      )}
                      <Text code style={{ fontSize: 11, maxWidth: 150 }} ellipsis>{t.name}</Text>
                    </div>
                    <Tag color={MODE_COLOR[t.mode]} style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 5px', borderRadius: 8 }}>
                      {MODE_LABEL[t.mode]}
                    </Tag>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontSize: 11, color: '#86909c', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 160 }}>
                      {t.description || '无描述'}
                    </span>
                    {!t.enabled && <Tag color="default" style={{ margin: 0, fontSize: 11, lineHeight: '14px', padding: '0 4px' }}>禁用</Tag>}
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* 右面板 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          {/* Tab 栏 */}
          <div style={{ borderBottom: '1px solid rgba(0,0,0,0.04)', paddingLeft: 16, flexShrink: 0 }}>
            <div style={{ display: 'flex', gap: 0 }}>
              {[
                { key: 'config', label: '工具配置' },
                { key: 'logs', label: <>调用日志 <Tag style={{ margin: '0 0 0 4px', fontSize: 11, borderRadius: 12, lineHeight: '18px', padding: '0 6px' }}>{logsTotal}</Tag></> },
              ].map(t => (
                <div key={t.key} onClick={() => setActiveTab(t.key)} style={{
                  padding: '10px 16px', cursor: 'pointer', fontSize: 14, position: 'relative',
                  color: activeTab === t.key ? '#7c5cbf' : '#4e5969',
                  fontWeight: activeTab === t.key ? 600 : 400,
                }}>
                  {t.label}
                  {activeTab === t.key && <div style={{
                    position: 'absolute', bottom: 0, left: 16, right: 16, height: 2,
                    background: 'rgba(124,92,191,0.12)', borderRadius: 8,
                  }} />}
                </div>
              ))}
            </div>
          </div>

          {/* Tab 内容 */}
          <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
            {activeTab === 'config' ? renderConfigTab() : renderLogsTab()}
          </div>
        </div>
      </div>

      {/* 新建工具弹窗 */}
      <Modal title="新建 MCP 工具" open={createOpen} onCancel={() => setCreateOpen(false)}
        onOk={handleCreateTool} okText="创建" cancelText="取消" width={480}>
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>工具名称 *</div>
          <Input spellCheck={false} value={newToolName} onChange={e => setNewToolName(e.target.value)}
            placeholder="如 tb_search_users" style={{ fontFamily: MONO }} />
        </div>
        <div>
          <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>描述</div>
          <Input value={newToolDesc} onChange={e => setNewToolDesc(e.target.value)}
            placeholder="搜索用户列表" />
        </div>
      </Modal>
    </div>
  )
}
