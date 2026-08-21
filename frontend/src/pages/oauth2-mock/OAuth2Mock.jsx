import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Button, Space, Input, Tag, Popconfirm, Tooltip, Badge, Pagination,
  Empty, Switch, message, Modal, InputNumber, Alert,
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, SaveOutlined, PlayCircleOutlined, PauseCircleOutlined,
  ReloadOutlined, ClearOutlined, CopyOutlined, SafetyCertificateOutlined, EditOutlined,
  CheckCircleFilled, CloseCircleFilled, LockOutlined, LockFilled, UnlockOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'

const MONO = 'var(--font-mono)'

export default function OAuth2Mock() {
  const [clients, setClients] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [clientForm, setClientForm] = useState(null)
  const [originalForm, setOriginalForm] = useState(null)
  const [saving, setSaving] = useState(false)
  const [activeTab, setActiveTab] = useState('config')
  const [logs, setLogs] = useState([])
  const [logsTotal, setLogsTotal] = useState(0)
  const [logPage, setLogPage] = useState(1)
  const [expandedLogId, setExpandedLogId] = useState(null)
  const [serviceStatus, setServiceStatus] = useState({ running: false, port: 28800, clientsCount: 0, clientsEnabled: 0, totalLogs: 0 })
  const [createOpen, setCreateOpen] = useState(false)
  const [newClient, setNewClient] = useState({ client_id: '', client_secret: '', name: '', scope: '', token_ttl: 3600 })
  const [tokenResult, setTokenResult] = useState(null)
  const [fetchingToken, setFetchingToken] = useState(false)
  const pollRef = useRef(null)

  useEffect(() => {
    fetchClients()
    fetchStatus()
    fetchLogs()
    pollRef.current = setInterval(fetchStatus, 5000)
    return () => clearInterval(pollRef.current)
  }, [])

  const fetchClients = async () => { try { const r = await api.get('/oauth2-mock/clients'); setClients(r.data || []) } catch {} }
  const fetchStatus = async () => { try { const r = await api.get('/oauth2-mock/status'); setServiceStatus(r.data || r) } catch {} }
  const fetchLogs = async (page) => {
    try {
      const p = page || logPage
      const r = await api.get(`/oauth2-mock/logs?limit=50&offset=${(p - 1) * 50}`)
      const d = r.data || r
      setLogs(d.data || [])
      setLogsTotal(d.total ?? 0)
    } catch {}
  }

  useEffect(() => { if (clients.length > 0 && !selectedId) selectClient(clients[0]) }, [clients])

  const selectClient = useCallback((c) => {
    setSelectedId(c.clientId)
    setClientForm({ ...c })
    setOriginalForm({ ...c })
  }, [])

  const isDirty = clientForm && originalForm && JSON.stringify(clientForm) !== JSON.stringify(originalForm)
  const locked = !!clientForm?.locked

  const handleSave = async () => {
    if (!clientForm || !selectedId) return
    setSaving(true)
    try {
      await api.put(`/oauth2-mock/clients/${selectedId}`, {
        name: clientForm.name, scope: clientForm.scope, audience: clientForm.audience,
        token_ttl: clientForm.tokenTtl, client_secret: clientForm.clientSecret,
      })
      message.success('已保存')
      fetchClients()
      setOriginalForm({ ...clientForm })
    } catch { message.error('保存失败') }
    finally { setSaving(false) }
  }

  const handleCreate = async () => {
    if (!newClient.client_id?.trim()) { message.warning('请填写 Client ID'); return }
    if (!newClient.client_secret?.trim()) { message.warning('请填写 Client Secret'); return }
    try {
      await api.post('/oauth2-mock/clients', newClient)
      message.success('已注册')
      setCreateOpen(false)
      setNewClient({ client_id: '', client_secret: '', name: '', scope: '', token_ttl: 3600 })
      fetchClients()
      fetchStatus()
    } catch { message.error('注册失败') }
  }

  const handleDelete = async (clientId) => {
    try {
      await api.delete(`/oauth2-mock/clients/${clientId}`)
      message.success('已删除')
      if (selectedId === clientId) { setSelectedId(null); setClientForm(null) }
      fetchClients()
      fetchStatus()
    } catch {}
  }

  const handleToggle = async (clientId) => {
    try { await api.patch(`/oauth2-mock/clients/${clientId}/toggle`); fetchClients() } catch {}
  }

  const handleToggleLock = async () => {
    if (!clientForm || !selectedId) return
    try {
      const r = await api.patch(`/oauth2-mock/clients/${selectedId}/lock`)
      const d = r.data || r
      message.success(d.locked ? 'Client 已锁定，需解锁后才能编辑' : 'Client 已解锁')
      setClientForm(f => ({ ...f, locked: d.locked }))
      setOriginalForm(f => ({ ...f, locked: d.locked }))
      fetchClients()
    } catch {}
  }

  const handleFetchToken = async () => {
    if (!clientForm || !serviceStatus.running) { message.warning('请先启动服务'); return }
    setFetchingToken(true); setTokenResult(null)
    try {
      const tokenUrl = `http://${window.location.hostname}:${serviceStatus.port}/oauth2/token`
      const res = await api.post('/toolbox/http-request', {
        method: 'POST', url: tokenUrl,
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `grant_type=client_credentials&client_id=${encodeURIComponent(clientForm.clientId)}&client_secret=${encodeURIComponent(clientForm.clientSecret)}`,
      })
      const d = res.data?.data || res.data || res
      if (d.error) { setTokenResult({ error: d.error }); return }
      try {
        const parsed = JSON.parse(d.body)
        setTokenResult({ parsed })
      } catch { setTokenResult({ error: '响应解析失败' }) }
    } catch (e) { setTokenResult({ error: e.message }) }
    finally { setFetchingToken(false) }
  }

  const handleStartStop = async () => {
    try {
      if (serviceStatus.running) {
        await api.post('/oauth2-mock/stop')
        message.success('OAuth2 Mock 服务已停止')
      } else {
        await api.post('/oauth2-mock/start')
        message.success('OAuth2 Mock 服务已启动')
      }
      fetchStatus()
    } catch (e) {
      message.error(`操作失败: ${e?.response?.data?.error || e?.response?.data?.detail || e.message || '未知错误'}`)
    }
  }

  const baseUrl = `http://${window.location.hostname}:${serviceStatus.port}`

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 70px)' }}>
      {/* 顶栏 */}
      <div style={{ padding: '8px 20px', background: 'rgba(255,255,255,0.7)', backdropFilter: 'blur(16px)', borderBottom: '1px solid rgba(255,255,255,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <SafetyCertificateOutlined style={{ fontSize: 18, color: '#0ea5a0' }} />
          <span style={{ fontWeight: 600, fontSize: 16 }}>OAuth2 Mock</span>
          <Badge status={serviceStatus.running ? 'success' : 'default'} text={
            <span style={{ fontSize: 12, color: serviceStatus.running ? '#0ea5a0' : '#86909c' }}>
              {serviceStatus.running ? `LIVE :${serviceStatus.port}` : 'STOPPED'}
            </span>
          } />
          <Tag style={{ fontSize: 11 }}>{serviceStatus.clientsCount} 个 Client</Tag>
          <Tag style={{ fontSize: 11 }}>{serviceStatus.totalLogs} 条日志</Tag>
        </div>
        <Space size={8}>
          {serviceStatus.running && (
            <Tooltip title="复制 Token 端点">
              <Button size="small" icon={<CopyOutlined />} onClick={() => { copyToClipboard(`${baseUrl}/oauth2/token`); message.success('已复制') }}>Token 端点</Button>
            </Tooltip>
          )}
          {serviceStatus.running && (
            <Tooltip title="复制 Introspect 端点">
              <Button size="small" icon={<CopyOutlined />} onClick={() => { copyToClipboard(`${baseUrl}/oauth2/introspect`); message.success('已复制') }}>Introspect 端点</Button>
            </Tooltip>
          )}
          <Button size="small" type={serviceStatus.running ? 'default' : 'primary'}
            icon={serviceStatus.running ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
            danger={serviceStatus.running} onClick={handleStartStop}>
            {serviceStatus.running ? '停止' : '启动'}
          </Button>
        </Space>
      </div>

      <div style={{ flex: 1, display: 'flex', minHeight: 0, padding: 10, gap: 10 }}>
        {/* 左栏 */}
        <div style={{ width: 260, flexShrink: 0, background: 'rgba(255,255,255,0.65)', backdropFilter: 'blur(16px)', borderRadius: 16, display: 'flex', flexDirection: 'column', boxShadow: '0 2px 12px rgba(0,0,0,0.04)' }}>
          <div style={{ padding: '8px 10px', borderBottom: '1px solid rgba(0,0,0,0.04)', display: 'flex', gap: 6 }}>
            <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)} style={{ flex: 1 }}>注册 Client</Button>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0' }}>
            {clients.map(c => (
              <div key={c.clientId} onClick={() => selectClient(c)}
                style={{
                  padding: '8px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
                  background: selectedId === c.clientId ? 'rgba(14,165,160,0.06)' : 'transparent',
                  borderLeft: selectedId === c.clientId ? '3px solid #0ea5a0' : '3px solid transparent',
                }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: c.enabled ? '#0ea5a0' : '#c9cdd4', flexShrink: 0 }} />
                <div style={{ flex: 1, overflow: 'hidden' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, minWidth: 0 }}>
                    {c.locked && (
                      <Tooltip title="已锁定，不可编辑">
                        <LockFilled style={{ fontSize: 11, color: '#ff7d00', flexShrink: 0 }} />
                      </Tooltip>
                    )}
                    <span style={{ fontWeight: 500, color: '#1d2129', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name || c.clientId}</span>
                  </div>
                  <div style={{ fontSize: 11, color: '#86909c', fontFamily: MONO }}>{c.clientId}</div>
                </div>
              </div>
            ))}
            {clients.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 Client" style={{ marginTop: 40 }} />}
          </div>
        </div>

        {/* 右栏 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: 'rgba(255,255,255,0.55)', backdropFilter: 'blur(16px)', borderRadius: 16, boxShadow: '0 2px 12px rgba(0,0,0,0.04)', minWidth: 0 }}>
          <div style={{ display: 'flex', borderBottom: '1px solid rgba(0,0,0,0.04)', padding: '0 16px', gap: 4 }}>
            {['config', 'endpoints', 'logs'].map(tab => (
              <div key={tab} onClick={() => { setActiveTab(tab); if (tab === 'logs') fetchLogs() }}
                style={{
                  padding: '8px 14px', fontSize: 13, cursor: 'pointer', fontWeight: activeTab === tab ? 600 : 400,
                  color: activeTab === tab ? '#0ea5a0' : '#86909c',
                  borderBottom: activeTab === tab ? '2px solid #0ea5a0' : '2px solid transparent',
                }}>
                {{ config: 'Client 配置', endpoints: '端点信息', logs: '请求日志' }[tab]}
              </div>
            ))}
          </div>

          <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
            {activeTab === 'config' && clientForm && (
              <div style={{ maxWidth: 600 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                  <span style={{ fontWeight: 600, fontSize: 15 }}>{clientForm.name || clientForm.clientId}</span>
                  {locked && <Tag color="orange" icon={<LockFilled />} style={{ margin: 0, fontSize: 11 }}>已锁定</Tag>}
                  <Switch checked={clientForm.enabled} onChange={() => handleToggle(clientForm.clientId)}
                    disabled={locked} checkedChildren="启用" unCheckedChildren="禁用" />
                </div>

                {locked && (
                  <Alert
                    type="warning" showIcon style={{ marginBottom: 14, fontSize: 12 }}
                    message="此 Client 已锁定，配置为只读。点击下方「解锁」后才能编辑。"
                  />
                )}

                <Field label="Client ID" value={clientForm.clientId} disabled mono />
                <Field label="Client Secret" value={clientForm.clientSecret}
                  onChange={v => setClientForm(f => ({ ...f, clientSecret: v }))}
                  disabled={locked} mono copyable />
                <Field label="名称" value={clientForm.name} disabled={locked}
                  onChange={v => setClientForm(f => ({ ...f, name: v }))} />
                <Field label="Scope" value={clientForm.scope} disabled={locked}
                  onChange={v => setClientForm(f => ({ ...f, scope: v }))} mono />
                <Field label="Audience" value={clientForm.audience} disabled={locked}
                  onChange={v => setClientForm(f => ({ ...f, audience: v }))} mono />
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 12, color: '#4e5969', fontWeight: 500, marginBottom: 4 }}>Token 有效期（秒）</div>
                  <InputNumber value={clientForm.tokenTtl} onChange={v => setClientForm(f => ({ ...f, tokenTtl: v }))}
                    min={5} max={86400} style={{ width: '100%' }} disabled={locked} />
                </div>

                <Space style={{ marginTop: 8 }}>
                  <Button type="primary" icon={<SaveOutlined />} onClick={handleSave} loading={saving} disabled={!isDirty || locked}>保存</Button>
                  <Tooltip title={locked ? '解锁后可编辑' : '锁定后不可编辑，需先解锁'}>
                    <Button
                      icon={locked ? <UnlockOutlined /> : <LockOutlined />}
                      onClick={handleToggleLock}
                      type={locked ? 'primary' : 'default'}
                      ghost={locked}
                    >
                      {locked ? '解锁' : '锁定'}
                    </Button>
                  </Tooltip>
                  {locked ? (
                    <Tooltip title="已锁定，请先解锁"><Button danger icon={<DeleteOutlined />} disabled>删除</Button></Tooltip>
                  ) : (
                    <Popconfirm title="确认删除？" onConfirm={() => handleDelete(clientForm.clientId)}>
                      <Button danger icon={<DeleteOutlined />}>删除</Button>
                    </Popconfirm>
                  )}
                </Space>

                {/* 获取 Token */}
                <div style={{ marginTop: 20, padding: 16, background: 'rgba(14,165,160,0.03)', borderRadius: 12, border: '1px solid rgba(14,165,160,0.12)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: tokenResult ? 12 : 0 }}>
                    <span style={{ fontSize: 13, fontWeight: 600, color: '#0ea5a0' }}>获取 Token</span>
                    <Button type="primary" loading={fetchingToken} onClick={handleFetchToken}
                      disabled={!serviceStatus.running}>
                      {serviceStatus.running ? '获取 Token' : '服务未启动'}
                    </Button>
                  </div>
                  {tokenResult && (tokenResult.error ? (
                    <div style={{ color: '#e53935', padding: 10, background: '#fff5f5', borderRadius: 8, fontSize: 12 }}>{tokenResult.error}</div>
                  ) : tokenResult.parsed?.access_token ? (
                    <div>
                      <div style={{ fontSize: 12, color: '#4e5969', fontWeight: 500, marginBottom: 4 }}>Access Token</div>
                      <div style={{ position: 'relative', padding: 10, background: 'rgba(255,255,255,0.8)', borderRadius: 8, fontFamily: MONO, fontSize: 11, wordBreak: 'break-all', color: '#434343', border: '1px solid rgba(0,0,0,0.06)' }}>
                        {tokenResult.parsed.access_token}
                        <Button type="text" size="small" icon={<CopyOutlined />}
                          style={{ position: 'absolute', top: 4, right: 4, color: '#0ea5a0' }}
                          onClick={() => { copyToClipboard(tokenResult.parsed.access_token); message.success('已复制 Token') }} />
                      </div>
                      <div style={{ marginTop: 8, fontSize: 12, color: '#4e5969', fontWeight: 500, marginBottom: 4 }}>Bearer Header</div>
                      <div style={{ position: 'relative', padding: 10, background: 'rgba(255,255,255,0.8)', borderRadius: 8, fontFamily: MONO, fontSize: 11, wordBreak: 'break-all', color: '#434343', border: '1px solid rgba(0,0,0,0.06)' }}>
                        Bearer {tokenResult.parsed.access_token}
                        <Button type="text" size="small" icon={<CopyOutlined />}
                          style={{ position: 'absolute', top: 4, right: 4, color: '#0ea5a0' }}
                          onClick={() => { copyToClipboard(`Bearer ${tokenResult.parsed.access_token}`); message.success('已复制') }} />
                      </div>
                      <div style={{ display: 'flex', gap: 16, marginTop: 8, fontSize: 11, color: '#86909c' }}>
                        <span>有效期: {tokenResult.parsed.expires_in}s</span>
                        <span>类型: {tokenResult.parsed.token_type}</span>
                        {tokenResult.parsed.scope && <span>Scope: {tokenResult.parsed.scope}</span>}
                      </div>
                    </div>
                  ) : null)}
                </div>
              </div>
            )}
            {activeTab === 'config' && !clientForm && <Empty description="选择一个 Client" style={{ marginTop: 60 }} />}

            {activeTab === 'endpoints' && (
              <div style={{ maxWidth: 700 }}>
                <h4 style={{ color: '#0ea5a0', marginBottom: 16 }}>OAuth2 端点</h4>
                {!serviceStatus.running && (
                  <div style={{ padding: 12, background: 'var(--orange-bg)', border: '1px solid #ffe7ba', borderRadius: 8, marginBottom: 16, fontSize: 12, color: '#ad6800' }}>
                    服务未启动，请先点击顶部的「启动」按钮
                  </div>
                )}
                <EndpointCard title="Token 端点" method="POST" url={`${baseUrl}/oauth2/token`}
                  desc="用 client_credentials 换取 access_token" />
                <EndpointCard title="Introspection 端点" method="POST" url={`${baseUrl}/oauth2/introspect`}
                  desc="验证 Token 是否有效（RFC 7662）— Stoa 网关调用此端点验证 Token" />
                <div style={{ padding: '8px 12px', background: '#f0f9f8', border: '1px solid #d6ece9', borderRadius: 8, marginBottom: 10, fontSize: 12, color: '#4e5969', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span>需要在 Stoa 的 OAuth2 认证配置中，将 introspection_endpoint 指向此地址</span>
                  <Button type="link" size="small" icon={<CopyOutlined />} style={{ padding: 0 }}
                    onClick={() => { copyToClipboard(`${baseUrl}/oauth2/introspect`); message.success('已复制') }}>复制</Button>
                </div>
                <EndpointCard title="JWKS 端点" method="GET" url={`${baseUrl}/oauth2/jwks`}
                  desc="RSA 公钥（JWK 格式）— 也可用于本地 JWT 验签" />
                <EndpointCard title="OpenID Discovery" method="GET" url={`${baseUrl}/.well-known/openid-configuration`}
                  desc="服务发现文档" />

                <h4 style={{ color: '#0ea5a0', margin: '24px 0 12px' }}>使用流程</h4>
                <div style={{ fontSize: 12, color: '#4e5969', marginBottom: 12, lineHeight: 1.8 }}>
                  1. 在 Stoa 创建应用，获得 client_id 和 client_secret<br/>
                  2. 点击左栏「注册 Client」，填入 Stoa 的凭据<br/>
                  3. 在 Stoa 路由的 OAuth2 配置中，将 introspection_endpoint 指向本服务<br/>
                  4. 用 client_id + client_secret 到 Token 端点换取 access_token<br/>
                  5. 带 Token 访问 Stoa API，Stoa 会到 Introspect 端点验证
                </div>
                <CodeBlock title="获取 Token" code={`curl -X POST ${baseUrl}/oauth2/token \\
  -H "Content-Type: application/x-www-form-urlencoded" \\
  -d "grant_type=client_credentials&client_id=<your-client-id>&client_secret=<your-client-secret>"`} />
              </div>
            )}

            {activeTab === 'logs' && (
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>请求日志</span>
                  <Space size={8}>
                    <Button size="small" icon={<ReloadOutlined />} onClick={() => fetchLogs()}>刷新</Button>
                    <Popconfirm title="清空所有日志？" onConfirm={async () => { await api.delete('/oauth2-mock/logs'); fetchLogs(); fetchStatus() }}>
                      <Button size="small" icon={<ClearOutlined />} danger>清空</Button>
                    </Popconfirm>
                  </Space>
                </div>
                {logs.length === 0 ? <Empty description="暂无日志" style={{ marginTop: 40 }} /> : (
                  <>
                    {logs.map(log => (
                      <div key={log.id} onClick={() => setExpandedLogId(expandedLogId === log.id ? null : log.id)}
                        style={{ padding: '8px 12px', marginBottom: 4, background: 'rgba(255,255,255,0.8)', borderRadius: 8, cursor: 'pointer', border: '1px solid rgba(0,0,0,0.04)', fontSize: 12 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          {log.status === 'success' || log.status === 'active' ? (
                            <CheckCircleFilled style={{ color: '#0ea5a0', fontSize: 13 }} />
                          ) : (
                            <CloseCircleFilled style={{ color: log.status === 'inactive' ? '#faad14' : '#e8453c', fontSize: 13 }} />
                          )}
                          <Tag style={{ fontSize: 11, margin: 0 }}>{log.endpoint}</Tag>
                          <span style={{ color: '#86909c', fontFamily: MONO }}>{log.clientId || '-'}</span>
                          <span style={{ flex: 1 }} />
                          <span style={{ color: '#c9cdd4', fontSize: 11 }}>{new Date(log.timestamp).toLocaleTimeString()}</span>
                        </div>
                        {expandedLogId === log.id && (
                          <div style={{ marginTop: 8, padding: 8, background: 'rgba(0,0,0,0.02)', borderRadius: 6, fontFamily: MONO, fontSize: 11, color: '#4e5969' }}>
                            {log.detail}
                          </div>
                        )}
                      </div>
                    ))}
                    {logsTotal > 50 && (
                      <div style={{ textAlign: 'center', marginTop: 12 }}>
                        <Pagination current={logPage} total={logsTotal} pageSize={50} size="small"
                          onChange={p => { setLogPage(p); fetchLogs(p) }} />
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      <Modal title="注册 Client（从 Stoa 获取的凭据）" open={createOpen} onOk={handleCreate} onCancel={() => setCreateOpen(false)} okText="注册" cancelText="取消" width={480}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '8px 0' }}>
          <div style={{ padding: 10, background: '#f0f9f8', border: '1px solid #d6ece9', borderRadius: 8, fontSize: 12, color: '#4e5969' }}>
            将 Stoa 创建应用时生成的 client_id 和 client_secret 填入此处，即可用这对凭据换取 Token。
          </div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4 }}>Client ID *</div>
            <Input spellCheck={false} value={newClient.client_id} onChange={e => setNewClient(n => ({ ...n, client_id: e.target.value }))}
              placeholder="从 Stoa 复制的 client_id" style={{ fontFamily: MONO }} />
          </div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4 }}>Client Secret *</div>
            <Input.Password value={newClient.client_secret} onChange={e => setNewClient(n => ({ ...n, client_secret: e.target.value }))}
              placeholder="从 Stoa 复制的 client_secret" style={{ fontFamily: MONO }} />
          </div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4 }}>名称（备注）</div>
            <Input value={newClient.name} onChange={e => setNewClient(n => ({ ...n, name: e.target.value }))}
              placeholder="如：oauth2 应用" />
          </div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4 }}>Token 有效期（秒）</div>
            <InputNumber value={newClient.token_ttl} onChange={v => setNewClient(n => ({ ...n, token_ttl: v }))}
              min={5} max={86400} style={{ width: '100%' }} />
          </div>
        </div>
      </Modal>
    </div>
  )
}

function Field({ label, value, onChange, disabled, mono, copyable }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 12, color: '#4e5969', fontWeight: 500, marginBottom: 4 }}>{label}</div>
      <div style={{ display: 'flex', gap: 4 }}>
        <Input value={value} onChange={onChange ? e => onChange(e.target.value) : undefined}
          disabled={disabled} spellCheck={!mono}
          style={{ fontFamily: mono ? MONO : undefined, fontSize: 12 }} />
        {copyable && <Tooltip title="复制"><Button icon={<CopyOutlined />} size="small"
          onClick={() => { copyToClipboard(value); message.success('已复制') }} /></Tooltip>}
      </div>
    </div>
  )
}

function EndpointCard({ title, method, url, desc }) {
  return (
    <div style={{ padding: 12, background: 'rgba(255,255,255,0.8)', borderRadius: 10, border: '1px solid rgba(0,0,0,0.06)', marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <Tag color={method === 'POST' ? 'orange' : 'green'} style={{ fontSize: 11, margin: 0 }}>{method}</Tag>
        <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 500, color: '#1d2129' }}>{url}</span>
        <Tooltip title="复制">
          <Button type="text" size="small" icon={<CopyOutlined />} style={{ color: '#c9cdd4' }}
            onClick={() => { copyToClipboard(url); message.success('已复制') }} />
        </Tooltip>
      </div>
      <div style={{ fontSize: 12, color: '#86909c' }}>{desc}</div>
    </div>
  )
}

function CodeBlock({ title, code }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 500, color: '#4e5969', marginBottom: 4 }}>{title}</div>
      <div style={{ position: 'relative' }}>
        <pre style={{ padding: 12, background: 'rgba(0,0,0,0.03)', borderRadius: 8, fontFamily: MONO, fontSize: 11, color: '#434343', whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
          {code}
        </pre>
        <Button type="text" size="small" icon={<CopyOutlined />}
          style={{ position: 'absolute', top: 4, right: 4, color: '#c9cdd4' }}
          onClick={() => { copyToClipboard(code); message.success('已复制') }} />
      </div>
    </div>
  )
}
