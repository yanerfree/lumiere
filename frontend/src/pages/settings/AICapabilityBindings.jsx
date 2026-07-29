import { useState, useEffect, useCallback } from 'react'
import {
  Card, Switch, Select, AutoComplete, Tag, Button, Modal, Form, Input,
  message, Space, Typography, Popconfirm, Alert, Divider, Spin,
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, WarningOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'

const { Text, Paragraph } = Typography

// 弱模型不适合 agentic UI 脚本生成 → 给红色警告
const isWeakForAgentic = (model) => /haiku/i.test(model || '')

export default function AICapabilityBindings({ overview, onOverviewReload }) {
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)          // {fallbackEnabled, bindings, registry, categoryMeta, builtinCategories}
  const [models, setModels] = useState([])         // [{id, displayName}]
  const [modelSource, setModelSource] = useState('')
  const [savingId, setSavingId] = useState(null)
  const [switchingConn, setSwitchingConn] = useState(false)
  const [draft, setDraft] = useState({})           // {bindingId: 正在输入的模型文本}
  const clearDraft = (id) => setDraft(d => { const n = { ...d }; delete n[id]; return n })
  const [addOpen, setAddOpen] = useState(false)
  const [form] = Form.useForm()

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/ai-capabilities')
      setData(res.data)
    } catch { /* */ } finally { setLoading(false) }
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

  useEffect(() => { fetchAll(); fetchModels() }, [fetchAll, fetchModels])

  const modelOptions = models.map(m => ({
    value: m.id,
    label: m.displayName && m.displayName !== m.id ? `${m.displayName} · ${m.id}` : m.id,
  }))

  const toggleFallback = async (checked) => {
    try {
      await api.put('/ai-capabilities/settings', { fallback_enabled: checked })
      setData(d => ({ ...d, fallbackEnabled: checked }))
      message.success(checked ? '已开启全局默认兜底' : '已关闭:未单独配置的项目将无法使用 AI')
      onOverviewReload?.()   // 兜底开关影响所有吃兜底的项目 → 刷新总览
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

  const saveModel = async (binding, model) => {
    if (!model || model === binding.model) return
    setSavingId(binding.id)
    try {
      await api.put(`/ai-capabilities/bindings/${binding.id}`, { model })
      message.success(`「${binding.label}」模型已更新为 ${model}`)
      fetchAll()
      onOverviewReload?.()   // 档位模型变了 → 总览里的生效模型跟着变
    } catch { /* */ } finally { setSavingId(null) }
  }

  const deleteBinding = async (binding) => {
    try {
      await api.del(`/ai-capabilities/bindings/${binding.id}`)
      message.success('已删除档位')
      fetchAll()
      onOverviewReload?.()
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
      fetchAll()
      onOverviewReload?.()
    } catch { /* */ }
  }

  if (loading && !data) return <Spin style={{ display: 'block', margin: '32px auto' }} />
  if (!data) return null

  const { bindings = [], registry = [], categoryMeta = {}, fallbackEnabled } = data
  // 被自定义档位圈走的模块 key,内置卡片里就不再重复展示
  const claimed = new Set(bindings.filter(b => !b.isBuiltin).flatMap(b => b.moduleKeys || []))

  const modulesForCard = (b) => {
    if (b.isBuiltin) {
      return registry.filter(m => m.category === b.category && !claimed.has(m.key))
    }
    return registry.filter(m => (b.moduleKeys || []).includes(m.key))
  }

  return (
    <Card
      size="small"
      style={{ marginTop: 20 }}
      title={<span style={{ fontWeight: 600 }}>🧩 AI 能力 → 模型</span>}
      extra={
        <Space size="middle">
          <Button size="small" icon={<ReloadOutlined />} onClick={fetchModels}>
            刷新模型清单{modelSource === 'gateway' ? '（网关）' : modelSource === 'preset' ? '（预置）' : ''}
          </Button>
          <span style={{ fontSize: 13 }}>
            全局默认兜底：
            <Switch
              size="small"
              checked={fallbackEnabled}
              onChange={toggleFallback}
              checkedChildren="开"
              unCheckedChildren="关"
              style={{ marginLeft: 6 }}
            />
          </span>
        </Space>
      }
    >
      <Alert
        type={fallbackEnabled ? 'info' : 'warning'}
        showIcon
        style={{ marginBottom: 14 }}
        message={
          fallbackEnabled
            ? '按功能类别为「未单独配置 AI 的项目」指定模型。项目若已自选/自建配置，则以项目配置为准。'
            : '兜底已关闭：没有单独配置 AI 的项目调用 AI 会直接报「未配置」。'
        }
      />

      {/* 兜底链:开关开着到底会用哪个连接、哪个模型,不让用户猜 */}
      {overview?.fallback && (
        <div
          style={{
            marginBottom: 14, padding: '10px 14px', borderRadius: 8,
            background: fallbackEnabled ? 'rgba(22,119,255,0.04)' : 'rgba(0,0,0,0.03)',
            border: '1px solid rgba(22,119,255,0.12)',
            opacity: fallbackEnabled ? 1 : 0.55,
          }}
        >
          <Space wrap size="middle" style={{ marginBottom: 6 }}>
            <Text strong style={{ fontSize: 13 }}>兜底连接：</Text>
            <Select
              size="small"
              style={{ minWidth: 240 }}
              value={overview.fallback.connection?.id}
              placeholder={overview.fallback.usingEnv ? '.env 兜底（未设系统默认配置）' : '未设置'}
              disabled={!fallbackEnabled || switchingConn}
              loading={switchingConn}
              onChange={switchFallbackConn}
              options={(overview.candidates || []).map(c => ({
                value: c.id,
                label: `${c.name} · ${c.model}`,
              }))}
            />
            {overview.fallback.connection && !overview.fallback.connection.status && <Tag>未测试</Tag>}
            {overview.fallback.connection?.status === 'ok' && <Tag color="cyan">连接正常</Tag>}
            {overview.fallback.connection?.status && overview.fallback.connection.status !== 'ok' && (
              <Tag color="error" icon={<WarningOutlined />}>
                连接异常{overview.fallback.connection.statusMessage ? `：${overview.fallback.connection.statusMessage}` : ''}
              </Tag>
            )}
            {!overview.fallback.connection && overview.fallback.usingEnv && (
              <Tag color="orange">
                正在用 .env 兜底（{overview.fallback.envModel || '未设模型'}），建议指定一个系统默认配置
              </Tag>
            )}
            {!overview.fallback.connection && !overview.fallback.usingEnv && (
              <Tag color="error" icon={<WarningOutlined />}>无可用兜底：未设系统默认且 .env 未启用</Tag>
            )}
          </Space>
          <div style={{ fontSize: 12.5 }}>
            <Text type="secondary">实际生效：</Text>
            {(overview.fallback.resolved || []).map((r, i) => (
              <span key={r.category}>
                {i > 0 && <Text type="secondary"> · </Text>}
                {r.label} → {r.model ? <Tag style={{ marginInlineEnd: 0 }}>{r.model}</Tag> : <Text type="secondary">—</Text>}
              </span>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 14 }}>
        {bindings.map(b => {
          const mods = modulesForCard(b)
          const warn = b.category === 'ui_script' && isWeakForAgentic(b.model)
          return (
            <Card
              key={b.id}
              size="small"
              type="inner"
              title={
                <Space>
                  <span>{b.icon}</span>
                  <Text strong>{b.label}</Text>
                  <Tag color={b.category === 'ui_script' ? 'purple' : 'blue'} style={{ fontSize: 11 }}>
                    {b.category === 'ui_script' ? 'UI脚本/Agentic' : '文本'}
                  </Tag>
                  {b.isBuiltin
                    ? <Tag style={{ fontSize: 11 }}>内置</Tag>
                    : <Tag color="geekblue" style={{ fontSize: 11 }}>自定义</Tag>}
                </Space>
              }
              extra={
                b.isBuiltin ? null : (
                  <Popconfirm title="删除此自定义档位？" onConfirm={() => deleteBinding(b)}>
                    <Button size="small" danger type="text" icon={<DeleteOutlined />} />
                  </Popconfirm>
                )
              }
            >
              <div style={{ marginBottom: 8 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>模型</Text>
                <AutoComplete
                  style={{ width: '100%', marginTop: 4 }}
                  options={modelOptions}
                  // draft = 正在编辑的文本。之前只给受控 value 没给 onChange,键盘输入会被
                  // 受控值立刻覆盖回去 → 框子根本改不动,且下拉过滤词恒等于当前模型全名、
                  // 只能筛出它自己。必须有 onChange 才能清空重输。
                  value={draft[b.id] ?? b.model}
                  disabled={savingId === b.id}
                  onChange={(val) => setDraft(d => ({ ...d, [b.id]: val }))}
                  onSelect={(val) => { clearDraft(b.id); saveModel(b, val) }}
                  onBlur={(e) => {
                    const val = (e.target?.value || '').trim()
                    clearDraft(b.id)          // 失焦即回到受控值,避免留下没保存的半截文本
                    if (val) saveModel(b, val)
                  }}
                  filterOption={(input, option) =>
                    (option?.value || '').toLowerCase().includes((input || '').toLowerCase())}
                  placeholder="选择或输入模型名"
                />
              </div>

              {warn && (
                <Alert
                  type="error"
                  showIcon
                  icon={<WarningOutlined />}
                  style={{ marginBottom: 8, padding: '4px 8px' }}
                  message={<span style={{ fontSize: 12 }}>此档位是 agentic 生成，用 Haiku 极可能失败，建议 Sonnet/Opus</span>}
                />
              )}

              <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
                💡 {b.recommend}
              </Paragraph>

              <Text type="secondary" style={{ fontSize: 12 }}>覆盖模块（{mods.length}）</Text>
              <div style={{ marginTop: 4 }}>
                {mods.length === 0
                  ? <Text type="secondary" style={{ fontSize: 12 }}>—</Text>
                  : mods.map(m => (
                      <Tag key={m.key} style={{ marginBottom: 4, fontSize: 11 }} title={m.where}>
                        {m.label}
                      </Tag>
                    ))}
              </div>
            </Card>
          )
        })}
      </div>

      <Button
        type="dashed"
        icon={<PlusOutlined />}
        style={{ marginTop: 14 }}
        onClick={() => {
          form.resetFields()
          form.setFieldsValue({ category: 'text' })
          setAddOpen(true)
        }}
      >
        新增自定义档位（把某些模块单独绑一个模型）
      </Button>

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
          <Form.Item name="moduleKeys" label="覆盖哪些模块"
            extra="这些模块将改用本档位的模型（从内置档位划过来）">
            <Select
              mode="multiple"
              allowClear
              placeholder="选择模块..."
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
