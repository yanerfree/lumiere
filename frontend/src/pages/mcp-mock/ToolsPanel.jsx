import { useState, useEffect, useMemo, useCallback } from 'react'
import {
  Button, Input, InputNumber, Radio, Switch, Space, Tag, Tooltip, Popconfirm,
  Empty, Alert, Modal, message,
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, SaveOutlined, PlayCircleOutlined,
  LockOutlined, LockFilled, UnlockOutlined, EyeOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { CODE_BLOCK_STYLE } from '../../components/MockCodeBlock'
import ParamEditor from './ParamEditor'

const MONO = 'var(--font-mono)'
const { TextArea } = Input

const MODE_LABEL = { success: '成功', error: '失败', custom: '自定义' }
const MODE_COLOR = { success: '#0ea5a0', error: '#e8453c', custom: '#4e8af0' }

const label = { fontSize: 12, color: '#86909c', marginBottom: 4 }
const hint = { fontSize: 11, color: '#c9cdd4', marginTop: 4, lineHeight: 1.6 }

/** 文本框里存的永远是字符串，回存时再解析 —— 否则每敲一个字都要过一次 JSON.parse，括号还没配平就飘红。 */
const toText = (v) => (v === null || v === undefined ? '' : typeof v === 'string' ? v : JSON.stringify(v, null, 2))

export default function ToolsPanel({ server, serverLocked, onChanged }) {
  const [tools, setTools] = useState([])
  const [selId, setSelId] = useState(null)
  const [form, setForm] = useState(null)
  const [origin, setOrigin] = useState(null)
  const [saving, setSaving] = useState(false)
  const [calling, setCalling] = useState(false)
  const [callArgs, setCallArgs] = useState('{}')
  const [callResult, setCallResult] = useState(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')

  const sid = server?.id

  const fetchTools = useCallback(async (keepId) => {
    if (!sid) return
    try {
      const r = await api.get(`/mcp-mock/servers/${sid}/tools`)
      const list = r.data || []
      setTools(list)
      const want = keepId || selId
      const hit = list.find(t => t.id === want) || list[0]
      if (hit) pick(hit)
      else { setSelId(null); setForm(null); setOrigin(null) }
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid])

  useEffect(() => { setSelId(null); setForm(null); setOrigin(null); fetchTools() }, [sid, fetchTools])

  const pick = (t) => {
    setSelId(t.id)
    const f = { ...t, successText: toText(t.successData), customText: toText(t.customData) }
    setForm(f)
    setOrigin(f)
    setCallResult(null)
    // 有必填参数就把它们预填进调用测试的输入框，省得人自己照着 schema 敲
    const pre = {}
    ;(t.params || []).forEach(p => { if (p.required) pre[p.name] = p.default !== undefined ? p.default : sample(p) })
    setCallArgs(JSON.stringify(pre, null, 2))
  }

  const dirty = useMemo(() => {
    if (!form || !origin) return false
    return JSON.stringify({ ...form, id: 0 }) !== JSON.stringify({ ...origin, id: 0 })
  }, [form, origin])

  const locked = !!form?.locked || serverLocked

  const save = async () => {
    if (!form) return
    let successData, customData
    try {
      successData = form.successText.trim() ? JSON.parse(form.successText) : null
    } catch { message.error('「成功时返回」不是合法的 JSON'); return }
    try {
      customData = form.customText.trim() ? JSON.parse(form.customText) : null
    } catch { message.error('「自定义返回」不是合法的 JSON'); return }
    setSaving(true)
    try {
      const r = await api.put(`/mcp-mock/servers/${sid}/tools/${form.id}`, {
        name: form.name,
        description: form.description,
        params: form.params || [],
        mode: form.mode,
        successData,
        customData,
        customIsError: !!form.customIsError,
        errorMessage: form.errorMessage,
        delayMs: form.delayMs || 0,
      })
      if (r.error) { message.error(r.error); return }
      if (r.data?.reloadError) message.warning(r.data.reloadError)
      message.success('已保存')
      await fetchTools(form.id)
      onChanged?.()
    } catch {} finally { setSaving(false) }
  }

  const create = async () => {
    if (!newName.trim()) { message.warning('工具名不能为空'); return }
    try {
      const r = await api.post(`/mcp-mock/servers/${sid}/tools`, {
        name: newName.trim(), description: newDesc.trim(), params: [], successData: { ok: true },
      })
      if (r.error) { message.error(r.error); return }
      if (r.data?.reloadError) message.warning(r.data.reloadError)
      message.success('工具已建好')
      setCreateOpen(false); setNewName(''); setNewDesc('')
      await fetchTools(r.data?.id)
      onChanged?.()
    } catch {}
  }

  const remove = async (t) => {
    try {
      const r = await api.delete(`/mcp-mock/servers/${sid}/tools/${t.id}`)
      if (r.error) { message.error(r.error); return }
      if (r.reloadError || r.data?.reloadError) message.warning(r.reloadError || r.data.reloadError)
      message.success('已删除')
      setSelId(null)
      await fetchTools()
      onChanged?.()
    } catch {}
  }

  const toggle = async (t) => {
    try { await api.patch(`/mcp-mock/servers/${sid}/tools/${t.id}/toggle`); await fetchTools(t.id); onChanged?.() } catch {}
  }
  const toggleLock = async (t) => {
    try { await api.patch(`/mcp-mock/servers/${sid}/tools/${t.id}/lock`); await fetchTools(t.id) } catch {}
  }

  const call = async () => {
    if (!form) return
    let args
    try { args = JSON.parse(callArgs || '{}') } catch { message.error('参数不是合法的 JSON'); return }
    setCalling(true)
    try {
      const r = await api.post(`/mcp-mock/servers/${sid}/call`, { tool: form.name, arguments: args })
      setCallResult(r.data !== undefined ? r : { ...r, data: null })
      onChanged?.()
    } catch (e) { setCallResult({ error: e.message }) } finally { setCalling(false) }
  }

  const preview = async () => {
    if (!form) return
    try {
      const r = await api.get(`/mcp-mock/servers/${sid}/tools/${form.id}/preview`)
      setCallResult({ ...r, preview: true })
    } catch {}
  }

  if (!sid) return null

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 0 }}>
      {/* 工具列表 */}
      <div style={{ width: 190, flexShrink: 0, borderRight: '1px solid rgba(0,0,0,0.04)', display: 'flex', flexDirection: 'column' }}>
        <div style={{
          padding: '8px 10px', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0,
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: '#1d2129' }}>工具 {tools.length}</span>
          <Button size="small" type="primary" ghost icon={<PlusOutlined />} disabled={serverLocked}
            onClick={() => setCreateOpen(true)}>新增</Button>
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: '6px 6px' }}>
          {tools.map(t => {
            const sel = t.id === selId
            return (
              <div key={t.id} onClick={() => pick(t)} style={{
                padding: '6px 8px', cursor: 'pointer', marginBottom: 4, borderRadius: 10,
                borderLeft: `3px solid ${sel ? '#7c5cbf' : 'transparent'}`,
                background: sel ? 'rgba(124,92,191,0.06)' : 'transparent',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  {t.locked && <LockFilled style={{ fontSize: 10, color: '#ff7d00', flexShrink: 0 }} />}
                  <span style={{
                    fontFamily: MONO, fontSize: 11, color: t.enabled ? '#1d2129' : '#c9cdd4',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>{t.name}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2 }}>
                  <span style={{ fontSize: 10, color: MODE_COLOR[t.mode] }}>{MODE_LABEL[t.mode]}</span>
                  {(t.params || []).length > 0 && <span style={{ fontSize: 10, color: '#c9cdd4' }}>· {t.params.length} 个参数</span>}
                  {!t.enabled && <Tag style={{ margin: 0, fontSize: 10, lineHeight: '14px', padding: '0 3px' }}>停用</Tag>}
                </div>
              </div>
            )
          })}
          {tools.length === 0 && (
            <div style={{ padding: '30px 0' }}>
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={<span style={{ color: '#c9cdd4', fontSize: 12 }}>还没有工具</span>} />
            </div>
          )}
        </div>
      </div>

      {/* 工具编辑 */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {!form ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={<span style={{ color: '#c9cdd4' }}>左边选一个工具</span>} />
          </div>
        ) : (
          <>
            <div style={{
              padding: '8px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0,
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            }}>
              <span style={{ fontFamily: MONO, fontSize: 13, color: '#1d2129' }}>{form.name}</span>
              <Space size={8}>
                <Button size="small" type="primary" icon={<SaveOutlined />} loading={saving}
                  disabled={!dirty || locked} onClick={save}>保存</Button>
                <Switch size="small" checked={!!form.enabled} disabled={locked}
                  onChange={() => toggle(form)} checkedChildren="启用" unCheckedChildren="停用" />
                <Tooltip title={form.locked ? '解锁后可编辑' : '锁上之后不能改也不能删'}>
                  <Button size="small" icon={form.locked ? <UnlockOutlined /> : <LockOutlined />}
                    type={form.locked ? 'primary' : 'default'} ghost={!!form.locked}
                    disabled={serverLocked} onClick={() => toggleLock(form)} />
                </Tooltip>
                {locked ? (
                  <Tooltip title="已锁定，请先解锁"><Button size="small" danger icon={<DeleteOutlined />} disabled /></Tooltip>
                ) : (
                  <Popconfirm title="确认删除这个工具？" okText="删除" cancelText="再想想" onConfirm={() => remove(form)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                )}
              </Space>
            </div>

            <div style={{ flex: 1, overflow: 'auto', padding: '14px 16px' }}>
              {locked && (
                <Alert type="warning" showIcon style={{ marginBottom: 12, fontSize: 12 }}
                  message={serverLocked ? '这个服务锁着，下面都是只读的。' : '这个工具已锁定，下面都是只读的。'} />
              )}

              <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
                <div style={{ width: 220 }}>
                  <div style={label}>工具名</div>
                  <Input size="small" spellCheck={false} style={{ fontFamily: MONO }} disabled={locked}
                    value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={label}>描述（对面客户端会读到）</div>
                  <Input size="small" disabled={locked} value={form.description || ''}
                    onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
                </div>
                <div style={{ width: 130 }}>
                  <div style={label}>响应延迟</div>
                  <InputNumber size="small" min={0} max={60000} step={100} style={{ width: '100%' }} disabled={locked}
                    addonAfter="ms" value={form.delayMs || 0}
                    onChange={v => setForm(f => ({ ...f, delayMs: v || 0 }))} />
                </div>
              </div>

              {/* 入参 */}
              <div style={{ marginBottom: 16 }}>
                <div style={label}>入参</div>
                <ParamEditor value={form.params} disabled={locked}
                  onChange={v => setForm(f => ({ ...f, params: v }))} />
                <div style={hint}>这里填的会原样变成对面客户端看到的参数表，松紧由「服务设置 → 参数校验松紧」决定。</div>
              </div>

              {/* 响应模式 */}
              <div style={{ marginBottom: 16 }}>
                <div style={label}>响应模式</div>
                <Radio.Group size="small" buttonStyle="solid" value={form.mode} disabled={locked}
                  onChange={e => setForm(f => ({ ...f, mode: e.target.value }))}>
                  <Radio.Button value="success">成功</Radio.Button>
                  <Radio.Button value="error">失败</Radio.Button>
                  <Radio.Button value="custom">自定义</Radio.Button>
                </Radio.Group>
              </div>

              {form.mode === 'success' && (
                <div style={{ marginBottom: 16 }}>
                  <div style={label}>成功时返回（JSON）</div>
                  <TextArea spellCheck={false} rows={6} disabled={locked}
                    style={{ fontFamily: MONO, fontSize: 12, borderRadius: 12 }}
                    value={form.successText} placeholder='{"ok": true}'
                    onChange={e => setForm(f => ({ ...f, successText: e.target.value }))} />
                </div>
              )}

              {form.mode === 'error' && (
                <div style={{ marginBottom: 16 }}>
                  <div style={label}>失败时的报错文字</div>
                  <Input size="small" disabled={locked} value={form.errorMessage || ''}
                    placeholder="这个工具被配置成必然失败"
                    onChange={e => setForm(f => ({ ...f, errorMessage: e.target.value }))} />
                  <div style={hint}>对面收到的是一次工具调用错误，用来测客户端的报错处理。</div>
                </div>
              )}

              {form.mode === 'custom' && (
                <div style={{ marginBottom: 16 }}>
                  <div style={label}>自定义返回（JSON）</div>
                  <TextArea spellCheck={false} rows={6} disabled={locked}
                    style={{ fontFamily: MONO, fontSize: 12, borderRadius: 12 }}
                    value={form.customText} placeholder='{"result": "..."}'
                    onChange={e => setForm(f => ({ ...f, customText: e.target.value }))} />
                  <div style={{ marginTop: 8 }}>
                    <Radio.Group size="small" disabled={locked} value={!!form.customIsError}
                      onChange={e => setForm(f => ({ ...f, customIsError: e.target.value }))}>
                      <Radio value={false}>当成正常结果</Radio>
                      <Radio value={true}>当成报错</Radio>
                    </Radio.Group>
                  </div>
                </div>
              )}

              {/* 调用测试 */}
              <div style={{ marginBottom: 12 }}>
                <div style={label}>调用测试</div>
                <TextArea spellCheck={false} rows={3} value={callArgs}
                  style={{ fontFamily: MONO, fontSize: 12, borderRadius: 12, marginBottom: 8 }}
                  onChange={e => setCallArgs(e.target.value)} placeholder="{}" />
                <Space size={8}>
                  <Button size="small" type="primary" icon={<PlayCircleOutlined />} loading={calling}
                    onClick={call}>发送调用</Button>
                  <Tooltip title="不带参数、不记日志，只看返回长什么样">
                    <Button size="small" icon={<EyeOutlined />} onClick={preview}>看返回样子</Button>
                  </Tooltip>
                </Space>
                <div style={hint}>这里走的是和对面客户端**同一套**参数校验，所以这儿过得了，那边也过得了。认证那一层不走。</div>
              </div>

              {callResult && (
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <span style={{ fontSize: 12, color: '#86909c', fontWeight: 500 }}>
                      {callResult.preview ? '返回样子' : '调用结果'}
                    </span>
                    {callResult.rejectKind === 'validate' && <Tag color="red" style={{ margin: 0, fontSize: 11 }}>参数被打回</Tag>}
                    {callResult.isError && <Tag color="red" style={{ margin: 0, fontSize: 11 }}>报错</Tag>}
                    {callResult.mode && !callResult.rejectKind && (
                      <Tag style={{ margin: 0, fontSize: 11 }}>{MODE_LABEL[callResult.mode] || callResult.mode}</Tag>
                    )}
                  </div>
                  {callResult.error && (
                    <Alert type="error" showIcon style={{ marginBottom: 8, fontSize: 12 }} message={callResult.error} />
                  )}
                  <pre style={{
                    ...CODE_BLOCK_STYLE, padding: 12, borderRadius: 12, overflow: 'auto',
                    fontSize: 11, lineHeight: 1.5, maxHeight: 220, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  }}>{JSON.stringify(callResult.data ?? callResult, null, 2)}</pre>
                </div>
              )}
            </div>
          </>
        )}
      </div>

      <Modal title="新建工具" open={createOpen} width={460} okText="创建" cancelText="取消"
        onCancel={() => setCreateOpen(false)} onOk={create}>
        <div style={{ marginBottom: 12 }}>
          <div style={label}>工具名 *</div>
          <Input spellCheck={false} style={{ fontFamily: MONO }} value={newName}
            placeholder="search_order" onChange={e => setNewName(e.target.value)} />
        </div>
        <div>
          <div style={label}>描述</div>
          <Input value={newDesc} placeholder="按订单号查订单" onChange={e => setNewDesc(e.target.value)} />
        </div>
      </Modal>
    </div>
  )
}

/** 调用测试的预填值：给必填参数塞一个类型对的样子货，人改一下就能发。 */
function sample(p) {
  if (Array.isArray(p.enum) && p.enum.length) return p.enum[0]
  switch (p.type) {
    case 'integer': return p.minimum ?? 1
    case 'number': return p.minimum ?? 1
    case 'boolean': return true
    case 'array': return []
    case 'object': return {}
    default: return ''
  }
}
