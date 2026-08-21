import { useState, useEffect, useCallback } from 'react'
import { timeColumn } from '../../utils/timeCol'
import {
  Button, Table, Modal, Form, Input, Select, InputNumber, Switch,
  message, Tag, Space, Card, Popconfirm, Tooltip, Spin, Badge, Typography, AutoComplete,
} from 'antd'
import {
  PlusOutlined, EditOutlined, DeleteOutlined, CheckCircleOutlined,
  CloseCircleOutlined, StarOutlined, StarFilled, ThunderboltOutlined,
  ApiOutlined, EyeOutlined, EyeInvisibleOutlined, LoadingOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'
import AIProjectOverview from './AIProjectOverview'
import AICapabilityBindings from './AICapabilityBindings'

const { Text, Paragraph } = Typography

const PROVIDERS = [
  { value: 'openai_compatible', label: '公司 AI 网关 / OpenAI 兼容' },
  { value: 'anthropic', label: 'Anthropic (直连)' },
  { value: 'ollama', label: 'Ollama (本地)' },
]

export default function AIProviderConfig() {
  const [configs, setConfigs] = useState([])
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [testingId, setTestingId] = useState(null)
  const [showSecret, setShowSecret] = useState({})
  const [modelOptions, setModelOptions] = useState([])
  const [overview, setOverview] = useState(null)
  const [overviewLoading, setOverviewLoading] = useState(false)
  const [form] = Form.useForm()

  const fetchConfigs = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/ai-providers')
      setConfigs(res.data || [])
    } catch { /* */ } finally { setLoading(false) }
  }, [])

  const fetchProjects = useCallback(async () => {
    try {
      const res = await api.get('/projects')
      setProjects(res.data || [])
    } catch { /* */ }
  }, [])

  // 兜底链 + 项目总览共用这一份数据（子组件不再各自请求）
  const fetchOverview = useCallback(async () => {
    setOverviewLoading(true)
    try {
      const res = await api.get('/ai-capabilities/overview')
      setOverview(res.data)
    } catch { /* */ } finally { setOverviewLoading(false) }
  }, [])

  const fetchModels = useCallback(async () => {
    try {
      const res = await api.get('/ai-capabilities/models')
      const list = (res.data?.models || []).map(m =>
        typeof m === 'string' ? { id: m, displayName: m } : m
      )
      setModelOptions(list.map(m => ({
        value: m.id,
        label: m.displayName && m.displayName !== m.id ? `${m.displayName} · ${m.id}` : m.id,
      })))
    } catch { /* */ }
  }, [])

  useEffect(() => { fetchConfigs(); fetchProjects(); fetchModels(); fetchOverview() }, [fetchConfigs, fetchProjects, fetchModels, fetchOverview])

  const openCreate = () => {
    setEditingId(null)
    form.resetFields()
    form.setFieldsValue({
      provider: 'openai_compatible',
      temperature: 0.3,
      maxTokens: 4096,
      timeoutSeconds: 120,
      isSystemDefault: false,
    })
    setModalOpen(true)
  }

  const openEdit = async (record) => {
    setEditingId(record.id)
    try {
      const res = await api.get(`/ai-providers/${record.id}`)
      const d = res.data
      form.setFieldsValue({
        name: d.name,
        provider: d.provider,
        baseUrl: d.baseUrl,
        model: d.model,
        temperature: d.temperature,
        maxTokens: d.maxTokens,
        timeoutSeconds: d.timeoutSeconds,
        isSystemDefault: d.isSystemDefault,
        assignedProjectIds: d.assignedProjectIds || [],
      })
      setModalOpen(true)
    } catch { /* */ }
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      const body = {
        name: values.name,
        provider: values.provider,
        base_url: values.baseUrl,
        model: values.model,
        temperature: values.temperature,
        max_tokens: values.maxTokens,
        timeout_seconds: values.timeoutSeconds,
        is_system_default: values.isSystemDefault || false,
        assigned_project_ids: values.assignedProjectIds || [],
      }
      if (values.apiKey) body.api_key = values.apiKey
      if (values.authToken) body.auth_token = values.authToken

      if (editingId) {
        await api.put(`/ai-providers/${editingId}`, body)
        message.success('更新成功')
      } else {
        await api.post('/ai-providers', body)
        message.success('创建成功')
      }
      setModalOpen(false)
      fetchConfigs()
    } catch { /* */ }
  }

  const handleDelete = async (id) => {
    try {
      await api.del(`/ai-providers/${id}`)
      message.success('已删除')
      fetchConfigs()
    } catch { /* */ }
  }

  const handleTest = async (id) => {
    setTestingId(id)
    try {
      const res = await api.post(`/ai-providers/${id}/test`)
      const d = res.data
      if (d.success) {
        message.success(`${d.message}  ·  ${d.latencyMs}ms`)
      } else {
        message.error(d.message)
      }
      fetchConfigs()
    } catch { /* */ } finally { setTestingId(null) }
  }

  const handleTestInModal = async () => {
    if (editingId) {
      // 编辑已有配置：用服务端已保存的凭据直接测试
      message.loading({ content: '正在测试连接...', key: 'test-modal' })
      try {
        const res = await api.post(`/ai-providers/${editingId}/test`)
        const d = res.data
        if (d.success) {
          message.success({ content: `${d.message}  ·  ${d.latencyMs}ms`, key: 'test-modal' })
        } else {
          message.error({ content: d.message, key: 'test-modal' })
        }
        fetchConfigs()
      } catch { /* */ }
      return
    }

    // 新建：用表单值直接测试
    try {
      const values = await form.validateFields(['provider', 'baseUrl', 'model', 'apiKey', 'authToken'])
      const body = {
        provider: values.provider,
        base_url: values.baseUrl,
        model: values.model,
      }
      if (values.apiKey) body.api_key = values.apiKey
      if (values.authToken) body.auth_token = values.authToken

      message.loading({ content: '正在测试连接...', key: 'test-modal' })
      const res = await api.post('/ai-providers/test-connection', body)
      const d = res.data
      if (d.success) {
        message.success({ content: `${d.message}  ·  ${d.latencyMs}ms`, key: 'test-modal' })
      } else {
        message.error({ content: d.message, key: 'test-modal' })
      }
    } catch { /* */ }
  }

  const statusTag = (record) => {
    if (!record.status) return <Tag>未测试</Tag>
    if (record.status === 'ok') return <Tag color="cyan" icon={<CheckCircleOutlined />}>正常</Tag>
    const msg = record.statusMessage || '异常'
    return (
      <Tooltip title={msg}>
        <Tag color="error" icon={<CloseCircleOutlined />}>异常</Tag>
      </Tooltip>
    )
  }

  const columns = [
    {
      // 不写宽度：让它吸收富余宽度，其余列才能保持声明值
      // （全都写死时 antd 按比例摊，「更新时间」会被撑到 122px 装 84px 的内容）
      title: '配置名称',
      dataIndex: 'name',
      render: (name, r) => (
        <Space>
          {r.isSystemDefault ? <StarFilled style={{ color: '#faad14' }} /> : null}
          <Text strong>{name}</Text>
          {r.isSystemDefault && <Tag color="gold" style={{ fontSize: 11 }}>系统默认</Tag>}
        </Space>
      ),
    },
    {
      title: '服务商',
      dataIndex: 'provider',
      // 160px 装不下「公司 AI 网关 / OpenAI 兼容」，实测折成两行、行高参差
      width: 200,
      render: (p) => PROVIDERS.find(x => x.value === p)?.label || p,
    },
    {
      // 这一列是**连接自带的默认值**，不是实际调用用的模型 —— 实际用哪个由下面
      // 「AI 能力 → 模型」决定。原来标题就叫「模型」，和下面的「实际生效」并排
      // 摆着两个不同的数字谁也不解释谁，用户看表格以为平台还在用 4.6。
      title: (
        <Tooltip title="连接自带的默认模型，一般用于测试连通性。实际调用用哪个模型，由下面的「AI 能力 → 模型」决定。">
          <span style={{ borderBottom: '1px dashed #c9cdd4', cursor: 'help' }}>默认模型</span>
        </Tooltip>
      ),
      dataIndex: 'model',
      width: 220,
      render: (m) => <Tag style={{ color: '#86909c' }}>{m}</Tag>,
    },
    {
      title: '状态',
      width: 80,
      render: (_, r) => statusTag(r),
    },
    {
      title: '已分配项目',
      dataIndex: 'assignedProjectIds',
      width: 180,
      render: (ids) => {
        if (!ids || ids.length === 0) return <Text type="secondary">未分配</Text>
        const names = ids.map(id => projects.find(p => p.id === id)?.name || id.slice(0, 8)).join('、')
        return <Tooltip title={names}><Tag color="blue">{ids.length} 个项目</Tag></Tooltip>
      },
    },
    {
      title: '启用',
      dataIndex: 'isEnabled',
      width: 60,
      render: (v) => v ? <Badge status="success" text="是" /> : <Badge status="default" text="否" />,
    },
    timeColumn({ key: 'updatedAt', title: '更新时间' }),
    {
      title: '操作',
      width: 200,
      render: (_, record) => (
        <Space size={4}>
          <Tooltip title="测试连接">
            <Button
              size="small"
              icon={testingId === record.id ? <LoadingOutlined /> : <ThunderboltOutlined />}
              loading={testingId === record.id}
              onClick={() => handleTest(record.id)}
            />
          </Tooltip>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>
            编辑
          </Button>
          <Popconfirm title="确认删除此配置？" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: '#1d2129' }}>
          AI 服务配置
        </h2>
        <span style={{ fontSize: 13, color: '#86909c' }}>
          管理 AI 服务连接。创建配置后需要<b>分配给项目</b>，项目内才能使用 AI 功能。
        </span>
      </div>

      <Card size="small" style={{ marginBottom: 12, background: 'rgba(0,0,0,0.02)' }}>
        <div style={{ fontSize: 13, lineHeight: 2 }}>
          <b>AI 服务为以下功能提供支持：</b>
          <span style={{ marginLeft: 12 }}>AI 用例生成 · 功能场景生成 · 质量评审 · 失败诊断 · 文档生成</span>
          <br/>
          <b>配置步骤：</b>
          <span style={{ marginLeft: 12 }}>① 新增 AI 服务 → ② 测试连接 → ③ 分配给项目 → ④ 项目内选择使用</span>
        </div>
      </Card>

      <div style={{ marginBottom: 12 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新增 AI 服务配置
        </Button>
      </div>

      <Table
        rowKey="id"
        columns={columns}
        dataSource={configs}
        loading={loading}
        pagination={false}
        size="small"
      />

      <AICapabilityBindings overview={overview} onOverviewReload={() => { fetchOverview(); fetchConfigs() }} />

      <AIProjectOverview overview={overview} loading={overviewLoading} onReload={fetchOverview} />

      <Modal
        title={editingId ? '编辑 AI 服务配置' : '新增 AI 服务配置'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        width={560}
        footer={[
          <Button key="test" icon={<ThunderboltOutlined />} onClick={handleTestInModal}>
            测试连接
          </Button>,
          <Button key="cancel" onClick={() => setModalOpen(false)}>
            取消
          </Button>,
          <Button key="save" type="primary" onClick={handleSave}>
            保存
          </Button>,
        ]}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="配置名称" rules={[{ required: true, message: '请输入配置名称' }]}>
            <Input placeholder="例如: 公司网关-Haiku" />
          </Form.Item>

          <Form.Item name="provider" label="服务商类型" rules={[{ required: true }]}>
            <Select options={PROVIDERS} />
          </Form.Item>

          <Form.Item name="baseUrl" label="API 地址" rules={[{ required: true, message: '请输入 API 地址' }]}>
            <Input placeholder="http://192.168.51.10:8080/v1" />
          </Form.Item>

          <Form.Item name="apiKey" label="API Key" extra="留空表示不修改">
            <Input.Password placeholder={editingId ? '留空则不修改' : 'sk-...'} />
          </Form.Item>

          <Form.Item name="authToken" label="网关 Token (Bearer)" extra="留空表示不修改">
            <Input.Password placeholder={editingId ? '留空则不修改' : 'gw-...'} />
          </Form.Item>

          <Form.Item name="model" label="模型名称" rules={[{ required: true, message: '请选择模型' }]}>
            <AutoComplete
              options={modelOptions}
              filterOption={(input, option) =>
                (option?.value || '').toLowerCase().includes(input.toLowerCase())}
              placeholder="选择或输入模型名（如 claude-haiku-4-5-20251001）"
            />
          </Form.Item>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
            <Form.Item name="temperature" label="温度">
              <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="maxTokens" label="最大 Token">
              <InputNumber min={100} max={128000} step={100} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="timeoutSeconds" label="超时 (秒)">
              <InputNumber min={10} max={600} style={{ width: '100%' }} />
            </Form.Item>
          </div>

          <Form.Item name="isSystemDefault" valuePropName="checked" label="系统默认">
            <Switch checkedChildren="默认" unCheckedChildren="否" />
          </Form.Item>

          <Form.Item
            name="assignedProjectIds"
            label="分配给项目"
            extra="选择哪些项目可以使用此配置，不选则所有项目不可见"
          >
            <Select
              mode="multiple"
              placeholder="选择项目..."
              allowClear
              options={projects.map(p => ({ value: p.id, label: p.name }))}
              optionFilterProp="label"
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
