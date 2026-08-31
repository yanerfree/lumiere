import { useState, useEffect, useCallback, useRef } from 'react'
import { Button, Input, Tabs, Modal, Form, message, Popconfirm, Tag, Tooltip, Spin } from 'antd'
import {
  PlusOutlined, DeleteOutlined, CopyOutlined, EditOutlined,
  GlobalOutlined, CloudServerOutlined,
  UnorderedListOutlined, CheckOutlined, CloseOutlined, HolderOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'
import { PERM } from '../../utils/permissions'
import { usePermissions } from '../../utils/PermissionContext'

export default function EnvConfig() {
  const [activeTab, setActiveTab] = useState('environments')
  // 环境/变量写操作后端要 env.write（tester 及以上）。只读角色（viewer）看得到配置、不能改。
  const { has } = usePermissions()
  const canWrite = has(PERM.ENV_WRITE)

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: '#1d2129' }}>环境配置</h2>
        <span style={{ fontSize: 13, color: '#86909c' }}>
          环境和全局变量都是<b>本项目</b>的，别的项目看不到。
          「全局变量」指的是<b>本项目所有环境共用</b>的兜底层。
          执行时优先级：环境变量 &gt; 全局变量 &gt; 脚本配置
        </span>
      </div>
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          { key: 'environments', label: <span><CloudServerOutlined /> 环境管理</span>, children: <EnvironmentPanel canWrite={canWrite} /> },
          { key: 'global', label: <span><GlobalOutlined /> 全局变量</span>, children: <GlobalVariablePanel canWrite={canWrite} /> },
        ]}
      />
    </div>
  )
}

// ============ 环境管理面板 ============
function EnvironmentPanel({ canWrite }) {
  // 环境 2026-08-21 起是项目级的，这个页面也跟着从 /settings/env
  // 搬到 /projects/:projectId/settings/env —— 没有项目就没有环境可管。
  const { projectId } = useParams()
  const [envs, setEnvs] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [envVars, setEnvVars] = useState([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [dragIdx, setDragIdx] = useState(null)
  const [form] = Form.useForm()

  // 环境名称/描述编辑
  const [editingName, setEditingName] = useState(false)
  const [editingDesc, setEditingDesc] = useState(false)
  const [editNameVal, setEditNameVal] = useState('')
  const [editDescVal, setEditDescVal] = useState('')

  const selectedEnv = envs.find(e => e.id === selectedId)

  const fetchEnvs = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get(`/projects/${projectId}/environments`)
      const list = res.data || []
      setEnvs(list)
      // 函数式更新才拿得到最新的 selectedId：这个 useCallback 依赖是 []，
      // 直接读闭包里的 selectedId 永远是初始的 null，于是每次刷新列表
      // （改名、改描述、拖拽排序之后都会刷）都把选中项重置回第一个。
      setSelectedId(prev => (prev && list.some(e => e.id === prev)) ? prev : (list[0]?.id ?? null))
    } catch { /* */ } finally { setLoading(false) }
  }, [])

  const fetchEnvVars = useCallback(async () => {
    if (!selectedId) return
    try {
      const res = await api.get(`/projects/${projectId}/environments/${selectedId}/variables`)
      setEnvVars(res.data || [])
    } catch { /* */ }
  }, [selectedId])

  useEffect(() => { fetchEnvs() }, [fetchEnvs])
  useEffect(() => { fetchEnvVars() }, [fetchEnvVars])

  const handleCreate = async () => {
    let values
    try { values = await form.validateFields() } catch { return }
    try {
      const res = await api.post(`/projects/${projectId}/environments`, { name: values.name, description: values.description || null })
      message.success('环境创建成功')
      setCreateOpen(false)
      form.resetFields()
      fetchEnvs()
      setSelectedId(res.data.id)
    } catch { /* */ }
  }

  // 拖动调整环境顺序：本地乐观更新 + 持久化 sort_order
  const handleDropEnv = async (targetIdx) => {
    const from = dragIdx
    setDragIdx(null)
    if (from === null || from === targetIdx) return
    const next = [...envs]
    const [moved] = next.splice(from, 1)
    next.splice(targetIdx, 0, moved)
    setEnvs(next)
    try {
      await api.put(`/projects/${projectId}/environments/reorder`, {
        items: next.map((e, i) => ({ id: e.id, sortOrder: i })),
      })
      fetchEnvs()
    } catch { fetchEnvs() }
  }

  const handleClone = async () => {
    if (!selectedEnv) return
    try {
      const res = await api.post(`/projects/${projectId}/environments/${selectedId}/clone`, { name: `${selectedEnv.name}-copy` })
      message.success('环境复制成功')
      fetchEnvs()
      setSelectedId(res.data.id)
    } catch { /* */ }
  }

  const handleDeleteEnv = async () => {
    try {
      await api.del(`/projects/${projectId}/environments/${selectedId}`)
      message.success('环境已删除')
      setSelectedId(null)
      fetchEnvs()
    } catch { /* */ }
  }

  const handleUpdateEnv = async (field, value) => {
    if (!selectedId) return
    try {
      await api.put(`/projects/${projectId}/environments/${selectedId}`, { [field]: value })
      message.success('已更新')
      fetchEnvs()
    } catch { /* */ }
  }

  const handleSaveVars = async (vars) => {
    try {
      await api.put(`/projects/${projectId}/environments/${selectedId}/variables`, vars.map(v => ({
        key: v.key, value: v.value, description: v.description || null,
      })))
      message.success('变量已保存')
      fetchEnvVars()
    } catch { /* */ }
  }

  const startEditName = () => {
    setEditNameVal(selectedEnv?.name || '')
    setEditingName(true)
  }
  const confirmEditName = () => {
    const v = editNameVal.trim()
    if (!v) { message.warning('名称不能为空'); return }
    if (v !== selectedEnv?.name) handleUpdateEnv('name', v)
    setEditingName(false)
  }

  const startEditDesc = () => {
    setEditDescVal(selectedEnv?.description || '')
    setEditingDesc(true)
  }
  const confirmEditDesc = () => {
    handleUpdateEnv('description', editDescVal.trim() || null)
    setEditingDesc(false)
  }

  return (
    <div style={{ display: 'flex', gap: 16, minHeight: 500 }}>
      {/* 左侧环境列表 */}
      {/* alignSelf 必须是 flex-start：父级是 flex 行，默认 align-items:stretch 会把
          左栏拉到和右侧详情一样高，多出来的高度全落在列表和「新增环境」之间，
          变成一块空白 —— 而且右侧变量越多空白越大（实测 4 个变量时 15px，
          16 个变量时 325px），看着像切换环境就多出个空条目 */}
      <div style={{ width: 200, background: 'var(--panel-bg)', borderRadius: 14, border: 'none', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)', display: 'flex', flexDirection: 'column', flexShrink: 0, alignSelf: 'flex-start' }}>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {loading ? <div style={{ textAlign: 'center', padding: 20 }}><Spin size="small" /></div> :
            envs.map((env, i) => (
              <div key={env.id}
                draggable
                onClick={() => setSelectedId(env.id)}
                onDragStart={e => { e.dataTransfer.effectAllowed = 'move'; setDragIdx(i) }}
                onDragOver={e => { e.preventDefault(); e.currentTarget.style.borderTop = '2px solid #0ea5a0' }}
                onDragLeave={e => { e.currentTarget.style.borderTop = '2px solid transparent' }}
                onDrop={e => { e.preventDefault(); e.currentTarget.style.borderTop = '2px solid transparent'; handleDropEnv(i) }}
                onDragEnd={() => setDragIdx(null)}
                style={{
                  padding: '8px 14px', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 8,
                  background: selectedId === env.id ? '#e0f7f6' : 'transparent',
                  borderLeft: selectedId === env.id ? '3px solid #0ea5a0' : '3px solid transparent',
                  // 最后一项的下边框留透明：紧接着就是按钮区的上边框，
                  // 画实线会变成挨着的两条线；用 none 则少 1px 盒高，
                  // 最后一项会比其它项矮一点点
                  borderBottom: `1px solid ${i < envs.length - 1 ? 'rgba(0,0,0,0.04)' : 'transparent'}`,
                  borderTop: '2px solid transparent',
                  opacity: dragIdx === i ? 0.4 : 1,
                  transition: 'opacity .15s',
                }}>
                <Tooltip title="拖动调整顺序">
                  <HolderOutlined style={{ fontSize: 11, color: '#c9cdd4', cursor: 'grab', flexShrink: 0 }} />
                </Tooltip>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#1d2129', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{env.name}</div>
                  {/* 描述行始终占位、且限单行 —— 否则「无描述」和「描述换行」两种环境
                      会撑出三种不同高度（实测 41 / 60 / 78px），列表看着参差不齐 */}
                  <div title={env.description || ''}
                    style={{ fontSize: 11, color: '#86909c', marginTop: 2, height: 16, lineHeight: '16px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {env.description || ''}
                  </div>
                </div>
              </div>
            ))
          }
        </div>
        {canWrite && (
          <div style={{ padding: 10, borderTop: '1px solid rgba(0,0,0,0.06)' }}>
            <Button type="dashed" icon={<PlusOutlined />} block size="small" onClick={() => setCreateOpen(true)}>新增环境</Button>
          </div>
        )}
      </div>

      {/* 右侧环境详情 */}
      <div style={{ flex: 1, background: 'var(--panel-bg)', borderRadius: 14, border: 'none', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)', padding: '20px 24px' }}>
        {selectedEnv ? (<>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              {/* 环境名称 — 可编辑 */}
              {editingName ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                  <Input value={editNameVal} onChange={e => setEditNameVal(e.target.value)}
                    onPressEnter={confirmEditName} autoFocus size="small"
                    style={{ fontSize: 15, fontWeight: 600, width: 200 }} />
                  <Button type="text" size="small" icon={<CheckOutlined />} style={{ color: '#0ea5a0' }} onClick={confirmEditName} />
                  <Button type="text" size="small" icon={<CloseOutlined />} style={{ color: '#c9cdd4' }} onClick={() => setEditingName(false)} />
                </div>
              ) : (
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0, color: '#1d2129', cursor: canWrite ? 'pointer' : 'default', display: 'inline-flex', alignItems: 'center', gap: 6 }}
                  onClick={canWrite ? startEditName : undefined}>
                  {selectedEnv.name}
                  {canWrite && <EditOutlined style={{ fontSize: 12, color: '#c9cdd4' }} />}
                </h3>
              )}
              {/* 描述 — 可编辑 */}
              {editingDesc ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
                  <Input value={editDescVal} onChange={e => setEditDescVal(e.target.value)}
                    onPressEnter={confirmEditDesc} autoFocus size="small"
                    placeholder="环境用途说明" style={{ fontSize: 12, width: 280 }} />
                  <Button type="text" size="small" icon={<CheckOutlined />} style={{ color: '#0ea5a0' }} onClick={confirmEditDesc} />
                  <Button type="text" size="small" icon={<CloseOutlined />} style={{ color: '#c9cdd4' }} onClick={() => setEditingDesc(false)} />
                </div>
              ) : (
                <div style={{ fontSize: 12, color: '#86909c', marginTop: 4, cursor: canWrite ? 'pointer' : 'default', display: 'inline-flex', alignItems: 'center', gap: 4 }}
                  onClick={canWrite ? startEditDesc : undefined}>
                  {selectedEnv.description || (canWrite ? '点击添加描述' : '无描述')}
                  {canWrite && <EditOutlined style={{ fontSize: 11, color: '#c9cdd4' }} />}
                </div>
              )}
            </div>
            {canWrite && (
              <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
                <Tooltip title="复制环境"><Button size="small" icon={<CopyOutlined />} onClick={handleClone} /></Tooltip>
                <Popconfirm title="确定删除该环境？" onConfirm={handleDeleteEnv}>
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              </div>
            )}
          </div>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#86909c', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 0.5 }}>环境变量</div>
          <VariableTable variables={envVars} onSave={handleSaveVars} canWrite={canWrite} />
          <CommonVarHint />
        </>) : (
          <div style={{ textAlign: 'center', padding: 80, color: '#c9cdd4' }}>请从左侧选择环境</div>
        )}
      </div>

      <Modal title="新增环境" open={createOpen} onOk={handleCreate} onCancel={() => { setCreateOpen(false); form.resetFields() }} okText="创建" cancelText="取消">
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="环境名称" rules={[{ required: true, message: '请输入环境名称' }]}>
            <Input placeholder="如 staging、production" />
          </Form.Item>
          <Form.Item name="description" label="描述"><Input placeholder="环境用途说明" /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

// ============ 全局变量面板 ============
function GlobalVariablePanel({ canWrite }) {
  // 「全局」= 本项目所有环境共用，不是跨项目共用（迁移 zzp0gvarproj）
  const { projectId } = useParams()
  const [variables, setVariables] = useState([])
  const [loading, setLoading] = useState(false)

  const fetchVars = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get(`/projects/${projectId}/global-variables`)
      setVariables(res.data || [])
    } catch { /* */ } finally { setLoading(false) }
  }, [projectId])

  useEffect(() => { fetchVars() }, [fetchVars])

  const handleSave = async (vars) => {
    try {
      await api.put(`/projects/${projectId}/global-variables`, vars.filter(v => v.key && v.value).map(v => ({
        key: v.key, value: v.value, description: v.description || null,
      })))
      message.success('全局变量已保存')
      fetchVars()
    } catch { /* request.js 已展示错误 */ }
  }

  return (
    <div style={{ background: 'var(--panel-bg)', borderRadius: 14, border: 'none', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)', padding: '20px 24px', maxWidth: 900 }}>
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 15, fontWeight: 600, color: '#1d2129' }}>全局变量</div>
        <div style={{ fontSize: 12, color: '#86909c', marginTop: 4 }}>
          在<b>本项目所有环境</b>中共享（不跨项目）。当某个环境变量存在同名 key 时，以环境变量为准
        </div>
      </div>
      {loading ? <Spin /> : <VariableTable variables={variables} onSave={handleSave} canWrite={canWrite} />}
    </div>
  )
}

// ============ 变量表格（复用组件） ============
// 环境变量和全局变量共用这一张表。改动集中在四件事上（原来那版是一排 3px 行距、
// 分隔线 rgba(0,0,0,0.03) 几乎看不见的无边框输入框，一眼看过去是一片小字）：
//   1. 行距和分隔线加出来、悬浮有底色 —— 才看得清自己在改哪一行；
//   2. 密码/密钥类的值默认打码（Input.Password，点眼睛看），
//      不然 ADMIN_PASSWORD、DATABASE_URL 就明文摊在设置页上给旁边的人看；
//   3. 变量多了给个搜索框 —— 只过滤**显示**，保存永远提交全量（见 handleSave）；
//   4. 只填了一半的行不再静默丢弃。
const SECRET_HINTS = ['PASSWORD', 'PASSWD', 'PWD', 'SECRET', 'TOKEN', 'APIKEY', 'API_KEY',
  'PRIVATE_KEY', 'CREDENTIAL', 'DATABASE_URL', 'DSN']
// 按变量名猜的，猜不准也不影响用：打码只是默认折起来，眼睛点一下就展开。
const isSecret = (key) => {
  const k = String(key || '').toUpperCase()
  return SECRET_HINTS.some(h => k.includes(h))
}

function VariableTable({ variables, onSave, canWrite = true }) {
  const [editVars, setEditVars] = useState([])
  const [dirty, setDirty] = useState(false)
  const [bulkOpen, setBulkOpen] = useState(false)
  const [bulkText, setBulkText] = useState('')
  const [filter, setFilter] = useState('')
  const [hoverUid, setHoverUid] = useState(null)
  const idCounter = useRef(0)

  useEffect(() => {
    setEditVars(variables.map(v => ({ _uid: ++idCounter.current, key: v.key, value: v.value, description: v.description || '' })))
    setDirty(false)
    setFilter('')
  }, [variables])

  const updateVar = (uid, field, value) => {
    setEditVars(prev => prev.map(v => v._uid === uid ? { ...v, [field]: value } : v))
    setDirty(true)
  }

  const addVar = () => {
    // 先清筛选：带着筛选词加空行，那行不匹配任何词，加完就是「点了没反应」
    setFilter('')
    setEditVars(prev => [...prev, { _uid: ++idCounter.current, key: '', value: '', description: '' }])
    setDirty(true)
  }

  const removeVar = (uid) => {
    setEditVars(prev => prev.filter(v => v._uid !== uid))
    setDirty(true)
  }

  const handleSave = () => {
    // 之前这里直接 editVars.filter(v => v.key && v.value) 就交出去了 ——
    // 填了变量名忘了填值的那一行，点完保存**一声不响就消失**，像被系统吃掉。
    // 半填的行先拦下来说清是哪几行；整行全空的（点了「添加变量」还没写）静默丢掉没有歧义。
    const half = editVars.filter(v => {
      const k = (v.key || '').trim(); const val = (v.value || '').trim()
      return (k && !val) || (!k && val)
    })
    if (half.length > 0) {
      message.warning(`有 ${half.length} 行只填了一半：${half.map(v => (v.key || '').trim() || '(变量名为空)').join('、')} —— 补齐或删掉再保存`)
      return
    }
    onSave?.(editVars.filter(v => (v.key || '').trim() && (v.value || '').trim()))
  }

  const openBulkEdit = () => {
    // 批量编辑始终是全量文本，跟筛选无关 —— 否则筛选着点进去，
    // 确定一下就把没显示的变量全删了
    setBulkText(editVars.map(v => {
      const parts = [v.key, v.value]
      if (v.description) parts.push(v.description)
      return parts.join(',')
    }).join('\n'))
    setBulkOpen(true)
  }

  const handleBulkConfirm = () => {
    const lines = bulkText.split('\n').filter(l => l.trim())
    const parsed = []
    for (const line of lines) {
      const parts = line.split(',')
      if (parts.length < 2) continue
      const key = parts[0].trim()
      const value = parts[1].trim()
      const description = parts.slice(2).join(',').trim()
      if (key) parsed.push({ _uid: ++idCounter.current, key, value, description })
    }
    setEditVars(parsed)
    setDirty(true)
    setFilter('')
    setBulkOpen(false)
  }

  const kw = filter.trim().toLowerCase()
  // 只影响渲染。editVars 一直是全量，保存/批量编辑都读它 —— 筛选状态下保存
  // 绝不会把没显示的变量删掉。
  const shown = kw
    ? editVars.filter(v => `${v.key} ${v.value} ${v.description}`.toLowerCase().includes(kw))
    : editVars

  const cell = { fontSize: 12, fontFamily: 'var(--font-mono)', padding: '2px 4px' }

  return (<>
    {/* 计数 + 未保存提示 + 搜索。原来这张表上方什么都没有：变量二十来个时
        只能靠滚动数，也没法在里面找一个 key。 */}
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6, minHeight: 24 }}>
      <span style={{ fontSize: 12, color: '#86909c' }}>
        {editVars.length} 个变量
        {kw && <span style={{ color: '#4e5969' }}> · 匹配 {shown.length} 个</span>}
        {dirty && <span style={{ color: '#d48806', fontWeight: 600 }}> · 有未保存的修改</span>}
      </span>
      <span style={{ flex: 1 }} />
      {editVars.length > 5 && (
        <Input size="small" allowClear value={filter} onChange={e => setFilter(e.target.value)}
          prefix={<SearchOutlined style={{ color: '#c9cdd4' }} />}
          placeholder="搜变量名 / 值 / 备注" style={{ width: 210, fontSize: 12 }} />
      )}
    </div>

    {/* 表头 */}
    <div style={{ display: 'flex', gap: 8, padding: '6px 8px', marginBottom: 2, borderBottom: '1px solid rgba(0,0,0,0.09)' }}>
      <div style={{ width: '25%', fontSize: 12, fontWeight: 600, color: '#4e5969' }}>变量名</div>
      <div style={{ width: '35%', fontSize: 12, fontWeight: 600, color: '#4e5969' }}>值</div>
      <div style={{ flex: 1, fontSize: 12, fontWeight: 600, color: '#4e5969' }}>备注</div>
      {/* 两个按钮（复制 / 删除），比原来的 36 宽一格 */}
      <div style={{ width: 60 }} />
    </div>

    {/* 行 */}
    {editVars.length === 0 && (
      <div style={{ padding: '24px 0', textAlign: 'center', color: '#c9cdd4', fontSize: 12 }}>暂无变量，点击下方添加</div>
    )}
    {editVars.length > 0 && shown.length === 0 && (
      <div style={{ padding: '24px 0', textAlign: 'center', color: '#c9cdd4', fontSize: 12 }}>
        没有变量匹配「{filter.trim()}」—— 这 {editVars.length} 个变量都还在，清掉搜索词就能看到
      </div>
    )}
    {shown.map(v => {
      const secret = isSecret(v.key)
      const hovered = hoverUid === v._uid
      return (
        <div key={v._uid}
          onMouseEnter={() => setHoverUid(v._uid)}
          onMouseLeave={() => setHoverUid(prev => (prev === v._uid ? null : prev))}
          style={{
            display: 'flex', gap: 8, alignItems: 'center',
            // 3px → 5px：原来一行才 26px 高，二十行堆在一起分不出行
            padding: '5px 8px',
            borderBottom: '1px solid rgba(0,0,0,0.05)',
            background: hovered ? 'rgba(14,165,160,0.05)' : 'transparent',
            borderRadius: 6,
            transition: 'background .12s',
          }}>
          <Input spellCheck={false} value={v.key} onChange={e => updateVar(v._uid, 'key', e.target.value)}
            placeholder="KEY" variant="borderless" size="small" readOnly={!canWrite}
            style={{ ...cell, width: '25%' }} />
          {secret ? (
            // 打码但可看：Input.Password 自带眼睛，不用自己管展开状态。
            // 判断只看变量名，所以这永远只是「默认折起来」，不是权限控制 ——
            // 值本身接口就是明文返回的，真要保密得在后端做。
            <Input.Password spellCheck={false} value={v.value} onChange={e => updateVar(v._uid, 'value', e.target.value)}
              placeholder="VALUE" variant="borderless" size="small" readOnly={!canWrite}
              style={{ ...cell, width: '35%' }} />
          ) : (
            <Input spellCheck={false} value={v.value} onChange={e => updateVar(v._uid, 'value', e.target.value)}
              placeholder="VALUE" variant="borderless" size="small" readOnly={!canWrite}
              style={{ ...cell, width: '35%' }} />
          )}
          <Input value={v.description} onChange={e => updateVar(v._uid, 'description', e.target.value)}
            placeholder="变量用途说明" variant="borderless" size="small" readOnly={!canWrite}
            style={{ flex: 1, fontSize: 12, color: '#86909c', padding: '2px 4px' }} />
          {/* 复制值：打码的那些光靠看抄不出来，得有个按钮。
              悬浮才显形，用 visibility 而不是条件渲染 —— 否则鼠标进出会让整行宽度跳一下 */}
          <Tooltip title="复制值">
            <Button type="text" size="small" icon={<CopyOutlined />}
              style={{ color: '#c9cdd4', width: 28, visibility: hovered && v.value ? 'visible' : 'hidden' }}
              onClick={() => copyToClipboard(v.value).then(
                () => message.success(`已复制 ${v.key || '变量'} 的值`),
                () => message.error('复制失败'),
              )} />
          </Tooltip>
          {canWrite ? (
            <Popconfirm title="删除此变量？" onConfirm={() => removeVar(v._uid)}>
              <Button type="text" size="small" icon={<DeleteOutlined />} style={{ color: '#c9cdd4', width: 28 }} />
            </Popconfirm>
          ) : <div style={{ width: 28 }} />}
        </div>
      )
    })}

    {canWrite && (
      <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
        <Button type="dashed" icon={<PlusOutlined />} onClick={addVar} size="small">添加变量</Button>
        <Button icon={<UnorderedListOutlined />} onClick={openBulkEdit} size="small">批量编辑</Button>
        {/* 保存按钮只在有改动时出现（原来就是这样）。旁边那句话是给它配的：
            按钮凭空冒出来，就得说清为什么冒出来、不点会怎样。 */}
        {dirty && <Button type="primary" size="small" onClick={handleSave}>保存</Button>}
        {dirty && <span style={{ fontSize: 12, color: '#d48806' }}>改动还没提交，离开这一页会丢</span>}
      </div>
    )}

    <Modal title="批量编辑" open={bulkOpen} onOk={handleBulkConfirm} onCancel={() => setBulkOpen(false)} okText="确定" cancelText="取消" width={560}>
      <div style={{ fontSize: 13, color: '#86909c', marginBottom: 10 }}>
        格式: <span style={{ color: '#4e5969', fontFamily: 'var(--font-mono)' }}>变量名,值,备注</span>
        <span style={{ color: '#d48806', marginLeft: 8 }}>确定后按这里的内容整份替换 —— 没列出来的变量会被删掉</span>
      </div>
      <Input.TextArea value={bulkText} onChange={e => setBulkText(e.target.value)} rows={10}
        placeholder={'BASE_URL,https://staging.example.com,测试目标地址\nDB_HOST,10.0.1.100,数据库主机'}
        style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }} />
    </Modal>
  </>)
}

// ============ 常用变量提示 ============
const COMMON_VARS = [
  { key: 'BASE_URL', desc: '测试目标地址', example: 'http://localhost:8000', required: true },
  { key: 'ADMIN_USERNAME', desc: '管理员用户名', example: 'admin' },
  { key: 'ADMIN_PASSWORD', desc: '管理员密码', example: 'admin123' },
  { key: 'TEST_PASSWORD', desc: '测试用户默认密码', example: 'Test@123456' },
  { key: 'DATABASE_URL', desc: '测试数据库连接', example: 'postgresql+asyncpg://...' },
]

function CommonVarHint() {
  return (
    <div style={{ marginTop: 16, padding: '12px 16px', background: 'rgba(255,255,255,0.2)', borderRadius: 12, border: 'none' }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: '#86909c', marginBottom: 8 }}>常用变量参考</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {COMMON_VARS.map(v => (
          <div key={v.key} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <code style={{ background: 'rgba(14,165,160,0.1)', padding: '2px 8px', borderRadius: 8, color: '#0ea5a0', fontWeight: 500, fontSize: 11 }}>{v.key}</code>
            <span style={{ color: '#86909c' }}>{v.desc}</span>
            <span style={{ color: '#c9cdd4' }}>如 {v.example}</span>
            {v.required && <Tag color="orange" style={{ fontSize: 11, lineHeight: '16px', padding: '0 4px', border: 'none' }}>必填</Tag>}
          </div>
        ))}
      </div>
      <div style={{ fontSize: 11, color: '#c9cdd4', marginTop: 8 }}>
        设置 BASE_URL 后，脚本通过 HTTP 请求测试目标服务；未设置则走进程内测试模式
      </div>
    </div>
  )
}
