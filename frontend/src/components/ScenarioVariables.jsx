import { useEffect, useRef, useState } from 'react'
import { Table, Button, Modal, Form, Input, Select, message, Popconfirm, Tag, Tooltip, Popover, Space } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, ThunderboltOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../utils/request'

const KIND_META = {
  literal: { label: '固定值', color: 'default', hint: '直接用该值' },
  random: { label: '随机唯一', color: 'purple', hint: '执行时补 -${runId}-${rand} 保唯一' },
  global_ref: { label: '引用全局', color: 'geekblue', hint: '引用项目全局数据(值为全局键名)' },
  template: { label: '部分固定+随机', color: 'gold', hint: '模板里字面原样保留，{{$fn}} 执行期随机展开' },
}

/**
 * 场景变量编辑区 —— 挂在用例上，UI 与接口测试共用同一份。
 * random 执行时自动加随机后缀保唯一；global_ref 引用项目全局数据；
 * template 支持"部分固定+部分随机"（apifox 式数据生成器，⚡快速插入）。
 * 场景内"上一步提取→下一步用"的中间值不在这里维护（走脚本内 extract）。
 */
export default function ScenarioVariables({ projectId, branchId, caseId }) {
  const base = `/projects/${projectId}/branches/${branchId}/cases/${caseId}/scenario-variables`
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form] = Form.useForm()
  const kind = Form.useWatch('kind', form)

  // 数据生成器目录 + 快速插入 + 预览
  const [generators, setGenerators] = useState([])
  const [genOpen, setGenOpen] = useState(false)
  const [sample, setSample] = useState('')
  const valueRef = useRef(null)

  const load = async () => {
    if (!projectId || !branchId || !caseId) return
    setLoading(true)
    try {
      const res = await api.get(base)
      setRows(res.data || [])
    } catch (e) {
      message.error(e?.message || '加载场景变量失败')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { load() }, [projectId, branchId, caseId])

  // 生成器目录（静态，进入即拉一次）
  useEffect(() => {
    if (!projectId || !branchId || !caseId) return
    api.get(`${base}/generators`).then((res) => setGenerators(res.data || [])).catch(() => {})
  }, [projectId, branchId, caseId])

  const runPreview = async (tpl) => {
    const t = tpl ?? form.getFieldValue('valueTemplate')
    if (!t || !String(t).includes('{{')) { setSample(''); return }
    try {
      const res = await api.post(`${base}/preview`, { template: t })
      setSample(res.data?.sample ?? '')
    } catch { /* 预览失败静默，不打断编辑 */ }
  }

  // 把生成器 token 插入到"值/模板"输入框光标处
  const insertToken = (token) => {
    const el = valueRef.current?.input
    const cur = form.getFieldValue('valueTemplate') || ''
    let next
    if (el && typeof el.selectionStart === 'number') {
      const s = el.selectionStart, e = el.selectionEnd
      next = cur.slice(0, s) + token + cur.slice(e)
    } else {
      next = cur + token
    }
    form.setFieldValue('valueTemplate', next)
    if (next.includes('{{') && form.getFieldValue('kind') !== 'template') {
      form.setFieldValue('kind', 'template')   // 用了生成器就自动切到 template
    }
    setGenOpen(false)
    setTimeout(() => { runPreview(next); el?.focus?.() }, 0)
  }

  const openAdd = () => { setEditing(null); form.resetFields(); form.setFieldsValue({ kind: 'literal', varType: 'string' }); setSample(''); setModalOpen(true) }
  const openEdit = (r) => { setEditing(r); form.setFieldsValue(r); setModalOpen(true); setTimeout(() => runPreview(r.valueTemplate), 0) }

  const submit = async () => {
    const v = await form.validateFields()
    try {
      if (editing) await api.put(`${base}/${editing.id}`, v)
      else await api.post(base, v)
      message.success(editing ? '已更新' : '已新增')
      setModalOpen(false); load()
    } catch (e) {
      message.error(e?.message || '保存失败')
    }
  }
  const del = async (r) => {
    try { await api.delete(`${base}/${r.id}`); message.success('已删除'); load() }
    catch (e) { message.error(e?.message || '删除失败') }
  }

  const genPanel = (
    <div style={{ maxHeight: 340, overflowY: 'auto', width: 320 }}>
      <div style={{ fontSize: 12, color: '#86909c', marginBottom: 6 }}>
        点一下插入到光标处。<code>{'{{$fn}}'}</code> 执行期随机展开，字面字符原样保留。
      </div>
      {generators.map((cat) => (
        <div key={cat.category} style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 12, color: '#4e5969', fontWeight: 600, margin: '4px 0' }}>{cat.category}</div>
          <Space wrap size={[6, 6]}>
            {cat.items.map((it) => (
              <Tooltip key={it.token} title={<span><code>{it.token}</code>{it.desc ? ` · ${it.desc}` : ''}</span>}>
                <Button size="small" onClick={() => insertToken(it.token)}>{it.label}</Button>
              </Tooltip>
            ))}
          </Space>
        </div>
      ))}
    </div>
  )

  const columns = [
    { title: '变量名', dataIndex: 'name', width: 160, render: (t) => <code>{`\${${t}}`}</code> },
    { title: '类型', dataIndex: 'kind', width: 130, render: (k) => {
      const m = KIND_META[k] || KIND_META.literal
      return <Tooltip title={m.hint}><Tag color={m.color}>{m.label}</Tag></Tooltip>
    } },
    { title: '值 / 模板', dataIndex: 'valueTemplate', ellipsis: true, render: (t, r) => {
      if (!t) return <span style={{ color: '#c9cdd4' }}>—</span>
      const hasRandom = r.kind === 'template' || r.kind === 'random'
      return <span><code style={{ fontSize: 12 }}>{t}</code>{hasRandom && <Tag color="orange" style={{ marginLeft: 6 }}>含随机</Tag>}</span>
    } },
    { title: '说明', dataIndex: 'description', ellipsis: true, render: (t) => t || <span style={{ color: '#c9cdd4' }}>—</span> },
    { title: '操作', width: 100, render: (_, r) => (
      <>
        <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} />
        <Popconfirm title="删除该场景变量？" onConfirm={() => del(r)}>
          <Button type="text" size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </>
    ) },
  ]

  return (
    <div style={{ padding: '8px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <div style={{ fontSize: 13, color: '#86909c' }}>
          场景变量 · UI 与接口测试共用 · <code>{'${变量名}'}</code> 引用；random/template 执行时自动随机化
        </div>
        <Button size="small" type="primary" ghost icon={<PlusOutlined />} onClick={openAdd}>新增变量</Button>
      </div>
      <Table
        rowKey="id" size="small" loading={loading} columns={columns} dataSource={rows} pagination={false}
        locale={{ emptyText: '暂无场景变量，点「新增变量」把该场景用到的数据抽成变量' }}
      />
      <Modal title={editing ? '编辑场景变量' : '新增场景变量'} open={modalOpen} onOk={submit} onCancel={() => setModalOpen(false)} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="变量名" rules={[{ required: true, message: '请输入变量名' }, { pattern: /^[A-Za-z_][A-Za-z0-9_]*$/, message: '字母/数字/下划线，且不以数字开头' }]}>
            <Input placeholder="如 serviceName" />
          </Form.Item>
          <Form.Item name="kind" label="类型" rules={[{ required: true }]}>
            <Select options={Object.entries(KIND_META).map(([v, m]) => ({ value: v, label: `${m.label} — ${m.hint}` }))} />
          </Form.Item>
          <Form.Item label="值 / 模板" required
            tooltip="固定值:最终值；随机唯一:前缀(执行时补随机后缀)；引用全局:全局数据键名；部分固定+随机:用 ⚡ 插入 {{$fn}} 生成器">
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item name="valueTemplate" noStyle>
                <Input
                  ref={valueRef}
                  placeholder={kind === 'template' ? '如 svc-{{$string:6}}-{{$city}}' : (kind === 'global_ref' ? '全局键名，如 BASE_URL' : '如 svc （random 会生成 svc-xxx）')}
                  onChange={(e) => runPreview(e.target.value)}
                />
              </Form.Item>
              <Popover open={genOpen} onOpenChange={setGenOpen} trigger="click" placement="bottomRight" content={genPanel} title="⚡ 快速插入随机数据">
                <Button icon={<ThunderboltOutlined />}>快速插入</Button>
              </Popover>
            </Space.Compact>
          </Form.Item>
          {kind === 'template' && (
            <div style={{ margin: '-8px 0 12px', fontSize: 12, color: '#86909c', display: 'flex', alignItems: 'center', gap: 8 }}>
              <span>预览：</span>
              <code style={{ color: '#4e8af0' }}>{sample || '(输入含 {{$fn}} 的模板后显示样例)'}</code>
              <Button type="text" size="small" icon={<ReloadOutlined />} onClick={() => runPreview()} />
            </div>
          )}
          <Form.Item name="varType" label="数据类型" initialValue="string">
            <Select options={[{ value: 'string' }, { value: 'number' }, { value: 'json' }]} />
          </Form.Item>
          <Form.Item name="description" label="说明（调试可读）">
            <Input.TextArea rows={2} placeholder="这个变量是什么、给哪些步骤用" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
