import { useState, useEffect, useCallback } from 'react'
import {
  Card, Switch, Select, AutoComplete, Tag, Button, Modal, Form, Input,
  message, Space, Typography, Popconfirm, Table, Spin, Tooltip, Collapse,
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, WarningOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'

const { Text } = Typography

// 弱模型不适合 agentic UI 脚本生成 → 给红色警告
const isWeakForAgentic = (model) => /haiku/i.test(model || '')
const fmtDay = (iso) => (iso ? String(iso).slice(5, 10) : '')

// 这个组件原来是"大横幅（多段解释文字）+ 每个档位一张卡片 + 卡片里再嵌一行行
// 能力下拉"，跟项目内「AI 能力 → 能力总览」页（AICapabilities.jsx）用的是
// 完全不同的两套排版看同一份数据 —— 用户的原话是"看一眼看不出是什么东西"。
// 现在改成跟那个页面同一种形状：一张表，一行一个能力，模型是这一行唯一
// 决定"实际调哪个模型"的地方。全局默认/连接状态收进表格上方一行状态条，
// 不再用大号数字复述表格已经说清楚的事。
export default function AICapabilityBindings({ overview, onOverviewReload }) {
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)          // {fallbackEnabled, bindings, registry, categoryMeta, builtinCategories}
  const [usage, setUsage] = useState(null)         // {items, orphans}
  const [models, setModels] = useState([])         // [{id, displayName}]
  const [modelSource, setModelSource] = useState('')
  const [savingRowKey, setSavingRowKey] = useState(null)   // 正在保存哪一行（能力 key 或档位 id）
  const [switchingConn, setSwitchingConn] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [form] = Form.useForm()

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/ai-capabilities')
      setData(res.data)
    } catch { /* */ } finally { setLoading(false) }
  }, [])

  const fetchUsage = useCallback(async () => {
    try { setUsage((await api.get('/ai-capabilities/usage')).data) } catch { /* */ }
  }, [])

  const fetchModels = useCallback(async () => {
    try {
      const res = await api.get('/ai-capabilities/models')
      const list = (res.data?.models || []).map(m =>
        typeof m === 'string' ? { id: m, displayName: m } : m
      )
      setModels(list)
      setModelSource(res.data?.source || '')
      if (res.data?.message) message.info(res.data.message)
    } catch { /* */ }
  }, [])

  useEffect(() => { fetchAll(); fetchUsage(); fetchModels() }, [fetchAll, fetchUsage, fetchModels])

  const reloadAll = () => { fetchAll(); fetchUsage(); onOverviewReload?.() }

  const modelOptions = models.map(m => ({
    value: m.id,
    label: m.displayName && m.displayName !== m.id ? `${m.displayName} · ${m.id}` : m.id,
  }))

  const toggleFallback = async (checked) => {
    try {
      await api.put('/ai-capabilities/settings', { fallback_enabled: checked })
      setData(d => ({ ...d, fallbackEnabled: checked }))
      message.success(checked ? '已开启全局默认兜底' : '已关闭：未单独配置的项目将无法使用 AI')
      onOverviewReload?.()
    } catch { /* */ }
  }

  // 切换兜底连接 = 把某个配置设为「系统默认」(后端会自动清掉其它配置的默认标记)
  const switchFallbackConn = async (configId) => {
    setSwitchingConn(true)
    try {
      await api.put(`/ai-providers/${configId}`, { is_system_default: true })
      message.success('兜底连接已切换')
      onOverviewReload?.()
    } catch { /* */ } finally { setSwitchingConn(false) }
  }

  // 改一个档位的默认模型（表格上方那一行紧凑输入）
  const saveCategoryModel = async (binding, model) => {
    if (!model || model === binding.model) return
    setSavingRowKey(`tier-${binding.id}`)
    try {
      await api.put(`/ai-capabilities/bindings/${binding.id}`, { model })
      message.success(`「${binding.label}」默认模型已更新为 ${model}`)
      reloadAll()
    } catch { /* */ } finally { setSavingRowKey(null) }
  }

  // 单个能力换模型（表格里每一行的下拉）。空 = 取消单独指定，回到跟着档位走
  const setCapabilityModel = async (key, model) => {
    setSavingRowKey(key)
    try {
      await api.put('/ai-capabilities/capability-model', { key, model: model || null })
      message.success(model ? `已改成 ${model}` : '已改回跟着档位')
      reloadAll()
    } catch { /* */ } finally { setSavingRowKey(null) }
  }

  const deleteBinding = async (binding) => {
    try {
      await api.del(`/ai-capabilities/bindings/${binding.id}`)
      message.success('已删除自定义档位')
      reloadAll()
    } catch { /* */ }
  }

  const handleAdd = async () => {
    try {
      const v = await form.validateFields()
      await api.post('/ai-capabilities/bindings', {
        label: v.label,
        category: v.category,
        model: v.model,
        module_keys: v.moduleKeys || [],
      })
      message.success('自定义档位已创建')
      setAddOpen(false)
      reloadAll()
    } catch { /* */ }
  }

  if (loading && !data) return <Spin style={{ display: 'block', margin: '32px auto' }} />
  if (!data) return null

  const { bindings = [], registry: rawRegistry = [], fallbackEnabled } = data
  const registry = rawRegistry.filter(m => !m.deprecated)
  const deprecated = rawRegistry.filter(m => m.deprecated)

  // 一个类别一个内置档位（文本生成/UI脚本生成）。类别下活着的能力清空了，
  // 这张卡就不该再露出来 —— 留着一张永远点不亮的卡片，只会让人重新问一遍
  // "这个是坏了还是我没配好"。
  const builtinTiers = bindings.filter(
    b => b.isBuiltin && registry.some(m => m.category === b.category)
  )

  // 「单入口覆盖」（PUT /capability-model 建的，key 前缀 cap-）和
  // 「用户自己建的自定义档位」（真正圈了一组能力、起了自己的名字）分开看：
  // 前者是"这一行换个模型"，后者才是"新建一类"。
  const capOverrides = bindings.filter(b => !b.isBuiltin && String(b.key || '').startsWith('cap-'))
  const customTiers = bindings.filter(b => !b.isBuiltin && !String(b.key || '').startsWith('cap-'))

  const ownModelOf = (key) => capOverrides.find(x => (x.moduleKeys || [])[0] === key)?.model || null
  const usageOf = (key) => (usage?.items || []).find(i => i.key === key)

  // 每个能力"挂在哪个档位下"——builtin 类别，或者被某个自定义档位圈中。
  const tierOf = (cap) => customTiers.find(b => (b.moduleKeys || []).includes(cap.key))
    || builtinTiers.find(b => b.category === cap.category)

  const columns = [
    {
      title: '能力',
      dataIndex: 'label',
      render: (v, r) => (
        <div>
          <div style={{ fontWeight: 500 }}>{v}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>{r.where}</Text>
        </div>
      ),
    },
    {
      title: '档位',
      width: 130,
      render: (_, r) => {
        const tier = tierOf(r)
        if (!tier) return <Text type="secondary">—</Text>
        return (
          <Tag color={tier.isBuiltin ? (tier.category === 'ui_script' ? 'purple' : 'blue') : 'geekblue'}>
            {tier.label}
          </Tag>
        )
      },
    },
    {
      title: '模型',
      width: 260,
      render: (_, r) => {
        const tier = tierOf(r)
        const own = ownModelOf(r.key)
        const warn = tier?.category === 'ui_script' && isWeakForAgentic(own || tier?.model)
        return (
          <Space direction="vertical" size={2} style={{ width: '100%' }}>
            <Select
              size="small"
              style={{ width: 240 }}
              value={own || ''}
              loading={savingRowKey === r.key}
              onChange={(v) => setCapabilityModel(r.key, v)}
              options={[
                { value: '', label: `跟随${tier?.label || '档位'}（${tier?.model || '—'}）` },
                ...modelOptions.map(o => ({ value: o.value, label: o.value })),
              ]}
            />
            {warn && (
              <Tooltip title="agentic UI 脚本生成用 Haiku 极可能失败，建议 Sonnet/Opus">
                <Tag color="error" icon={<WarningOutlined />} style={{ fontSize: 11 }}>弱模型</Tag>
              </Tooltip>
            )}
          </Space>
        )
      },
    },
    {
      title: (
        <Tooltip title="按调用记录数的真实用量。「暂无记录」和「从未调用」是两件事，见下方说明。">
          <span style={{ borderBottom: '1px dotted #c9cdd4' }}>真实用量</span>
        </Tooltip>
      ),
      width: 170,
      render: (_, r) => {
        const u = usageOf(r.key)
        if (!u) return <Text type="secondary" style={{ fontSize: 12 }}>—</Text>
        if (u.calls > 0) return (
          <span style={{ fontSize: 12 }}>
            <b style={{ color: '#0ea5a0' }}>{u.calls}</b> 次
            <span style={{ color: '#86909c', marginLeft: 6 }}>最近 {fmtDay(u.lastUsedAt)}</span>
          </span>
        )
        return u.meteredSince ? (
          <Tooltip title={`这条链路从 ${u.meteredSince} 才开始记调用，"0 次"只代表这之后没人用过`}>
            <span style={{ fontSize: 12, color: '#d48806', borderBottom: '1px dotted currentColor' }}>
              暂无记录
            </span>
          </Tooltip>
        ) : <span style={{ fontSize: 12, color: '#c9cdd4' }}>从未调用</span>
      },
    },
  ]

  return (
    <Card
      size="small"
      style={{ marginTop: 20 }}
      title={<span style={{ fontWeight: 600 }}>🧩 能力 → 模型</span>}
      extra={
        <Button size="small" icon={<ReloadOutlined />} onClick={fetchModels}>
          刷新模型清单{modelSource === 'gateway' ? '（网关）' : modelSource === 'preset' ? '（预置）' : ''}
        </Button>
      }
    >
      {/* 状态条：一行说清"用哪个连接、通不通、限流通道好不好"，
          不再用大号数字复述下面表格已经说清楚的模型信息。 */}
      {overview?.fallback && (() => {
        const conn = overview.fallback.connection
        const cli = overview.fallback.cliChannel
        return (
          <div style={{
            display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 16,
            padding: '10px 14px', borderRadius: 10, marginBottom: 14,
            background: 'rgba(0,0,0,0.02)', border: '1px solid rgba(0,0,0,0.06)',
          }}>
            <Tooltip title="按功能类别为「未单独配置 AI 的项目」提供默认连接。项目若已自选/自建配置，则以项目配置为准">
              <span style={{ fontSize: 12, color: '#86909c', borderBottom: '1px dotted #c9cdd4' }}>全局默认兜底</span>
            </Tooltip>
            <Switch size="small" checked={fallbackEnabled} onChange={toggleFallback}
              checkedChildren="开" unCheckedChildren="关" />
            <Select
              size="small" style={{ minWidth: 200 }}
              value={conn?.id} loading={switchingConn}
              onChange={switchFallbackConn}
              placeholder="选择兜底连接"
              options={(overview.candidates || []).map(c => ({ value: c.id, label: c.name }))}
            />
            {conn?.status === 'ok'
              ? <Tag color="success" style={{ margin: 0 }}>连接正常</Tag>
              : conn
                ? <Tooltip title={conn.statusMessage}><Tag color="error" style={{ margin: 0 }}>连接异常</Tag></Tooltip>
                : <Tag color="warning" style={{ margin: 0 }}>未配置兜底</Tag>}
            {cli && (
              <Tooltip title={cli.alive ? '429 限流降级通道正常' : cli.hint}>
                <Tag color={cli.alive ? 'success' : 'error'} style={{ margin: 0 }}>
                  限流降级通道 {cli.alive ? '正常' : '不可用'}
                </Tag>
              </Tooltip>
            )}
          </div>
        )
      })()}

      {/* 档位默认模型：一行一个类别，紧凑输入。表格里"跟随档位"的行读的就是这里。 */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, marginBottom: 14 }}>
        {builtinTiers.map(b => (
          <div key={b.id} style={{ minWidth: 260 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>{b.icon} {b.label}默认模型</Text>
            <AutoComplete
              style={{ width: '100%', marginTop: 4 }}
              options={modelOptions}
              defaultValue={b.model}
              disabled={savingRowKey === `tier-${b.id}`}
              onSelect={(val) => saveCategoryModel(b, val)}
              onBlur={(e) => { const val = (e.target?.value || '').trim(); if (val) saveCategoryModel(b, val) }}
              filterOption={(input, option) =>
                (option?.value || '').toLowerCase().includes((input || '').toLowerCase())}
            />
          </div>
        ))}
      </div>

      <Table
        rowKey="key"
        size="small"
        columns={columns}
        dataSource={registry}
        pagination={false}
      />

      {deprecated.length > 0 && (
        <Collapse
          ghost
          size="small"
          style={{ marginTop: 12 }}
          items={[{
            key: '1',
            label: <Text type="secondary" style={{ fontSize: 12 }}>已下线的入口（{deprecated.length}）</Text>,
            children: (
              <div style={{ fontSize: 12, color: '#86909c', lineHeight: 1.9 }}>
                {deprecated.map(m => (
                  <div key={m.key}><s>{m.label}</s> —— {m.deprecatedNote || '已下线'}</div>
                ))}
              </div>
            ),
          }]}
        />
      )}

      {customTiers.length > 0 && (
        <div style={{ marginTop: 14, fontSize: 12, color: '#86909c' }}>
          自定义档位：
          <Space size={8} wrap style={{ marginLeft: 6 }}>
            {customTiers.map(b => (
              <Tag key={b.id} style={{ margin: 0 }}>
                {b.label} · {b.model} · 覆盖 {(b.moduleKeys || []).length} 项
                <Popconfirm title="删除此自定义档位？" onConfirm={() => deleteBinding(b)}>
                  <DeleteOutlined style={{ marginLeft: 6, cursor: 'pointer' }} />
                </Popconfirm>
              </Tag>
            ))}
          </Space>
        </div>
      )}

      <div style={{ marginTop: 10 }}>
        <Button
          type="link"
          size="small"
          style={{ padding: 0, fontSize: 12 }}
          icon={<PlusOutlined />}
          onClick={() => {
            form.resetFields()
            form.setFieldsValue({ category: 'text' })
            setAddOpen(true)
          }}
        >
          批量给一组能力指定同一个模型
        </Button>
      </div>

      <Modal
        title="新增自定义档位"
        open={addOpen}
        onCancel={() => setAddOpen(false)}
        onOk={handleAdd}
        okText="创建"
        width={520}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="label" label="档位名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="例如：文档生成专用" />
          </Form.Item>
          <Form.Item name="category" label="能力类别" rules={[{ required: true }]}
            extra="决定推荐模型与解析路径：text=普通文本；ui_script=agentic 浏览器生成">
            <Select options={[
              { value: 'text', label: '📝 文本生成' },
              { value: 'ui_script', label: '🎭 UI 脚本生成（agentic）' },
            ]} />
          </Form.Item>
          <Form.Item name="model" label="模型" rules={[{ required: true, message: '请选择模型' }]}>
            <AutoComplete
              options={modelOptions}
              filterOption={(input, option) =>
                (option?.value || '').toLowerCase().includes(input.toLowerCase())}
              placeholder="选择或输入模型名"
            />
          </Form.Item>
          <Form.Item name="moduleKeys" label="覆盖哪些能力"
            extra="这些能力将改用本档位的模型（从原来所在的档位划过来）">
            <Select
              mode="multiple"
              allowClear
              placeholder="选择能力..."
              optionFilterProp="label"
              options={registry.map(m => ({
                value: m.key,
                label: `${m.label}（${m.category === 'ui_script' ? 'UI脚本' : '文本'}）`,
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
