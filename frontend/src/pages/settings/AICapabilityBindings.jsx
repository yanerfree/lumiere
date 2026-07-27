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

export default function AICapabilityBindings() {
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)          // {fallbackEnabled, bindings, registry, categoryMeta, builtinCategories}
  const [models, setModels] = useState([])         // [{id, displayName}]
  const [modelSource, setModelSource] = useState('')
  const [savingId, setSavingId] = useState(null)
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
    } catch { /* */ }
  }

  const saveModel = async (binding, model) => {
    if (!model || model === binding.model) return
    setSavingId(binding.id)
    try {
      await api.put(`/ai-capabilities/bindings/${binding.id}`, { model })
      message.success(`「${binding.label}」模型已更新为 ${model}`)
      fetchAll()
    } catch { /* */ } finally { setSavingId(null) }
  }

  const deleteBinding = async (binding) => {
    try {
      await api.del(`/ai-capabilities/bindings/${binding.id}`)
      message.success('已删除档位')
      fetchAll()
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
                  value={b.model}
                  disabled={savingId === b.id}
                  onSelect={(val) => saveModel(b, val)}
                  onBlur={(e) => saveModel(b, e.target?.value)}
                  filterOption={(input, option) =>
                    (option?.value || '').toLowerCase().includes(input.toLowerCase())}
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
