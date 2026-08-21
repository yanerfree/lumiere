import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import {
  Card, Radio, Button, Tag, Space, message, Empty, Spin, Typography, Divider,
  Modal, Form, Input, Select, InputNumber, Popconfirm, Tooltip,
} from 'antd'
import {
  CheckCircleOutlined, CloseCircleOutlined, StarFilled, ThunderboltOutlined,
  SwapOutlined, RobotOutlined, PlusOutlined, DeleteOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'

const { Text } = Typography

const PROVIDERS = [
  { value: 'openai_compatible', label: '公司 AI 网关 / OpenAI 兼容' },
  { value: 'anthropic', label: 'Anthropic (直连)' },
  { value: 'ollama', label: 'Ollama (本地)' },
]

export default function ProjectAIConfig() {
  const { projectId } = useParams()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [customModalOpen, setCustomModalOpen] = useState(false)
  const [form] = Form.useForm()

  // 用量统计
  const [usage, setUsage] = useState(null)
  useEffect(() => {
    if (!projectId) return
    api.get(`/projects/${projectId}/ai-usage`).then(res => setUsage(res.data)).catch(() => {})
  }, [projectId])

  const fetchConfig = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get(`/projects/${projectId}/ai-config`)
      setData(res.data)
    } catch { /* */ } finally { setLoading(false) }
  }, [projectId])

  useEffect(() => { fetchConfig() }, [fetchConfig])

  const handleSelectSystem = async (providerConfigId) => {
    setSelecting(true)
    try {
      await api.post(`/projects/${projectId}/ai-config/select/${providerConfigId}`)
      message.success('已切换到此 AI 服务')
      fetchConfig()
    } catch { /* */ } finally { setSelecting(false) }
  }

  const handleCreateCustom = async () => {
    try {
      const values = await form.validateFields()
      await api.post(`/projects/${projectId}/ai-config/custom`, {
        name: values.name,
        provider: values.provider,
        base_url: values.baseUrl,
        api_key: values.apiKey || undefined,
        auth_token: values.authToken || undefined,
        model: values.model,
        temperature: values.temperature,
        max_tokens: values.maxTokens,
        timeout_seconds: values.timeoutSeconds,
      })
      message.success('项目专属配置已创建并激活')
      setCustomModalOpen(false)
      form.resetFields()
      fetchConfig()
    } catch { /* */ }
  }

  const handleTestCustom = async () => {
    try {
      const values = await form.validateFields(['provider', 'baseUrl', 'model', 'apiKey', 'authToken'])
      message.loading({ content: '正在测试连接...', key: 'test-custom' })
      const res = await api.post('/ai-providers/test-connection', {
        provider: values.provider,
        base_url: values.baseUrl,
        model: values.model,
        api_key: values.apiKey || undefined,
        auth_token: values.authToken || undefined,
      })
      const d = res.data
      if (d.success) {
        message.success({ content: `${d.message}  ·  ${d.latencyMs}ms`, key: 'test-custom' })
      } else {
        message.error({ content: d.message, key: 'test-custom' })
      }
    } catch { /* */ }
  }

  const handleDeleteCustom = async (configId) => {
    try {
      await api.del(`/projects/${projectId}/ai-config/custom/${configId}`)
      message.success('已删除项目配置')
      fetchConfig()
    } catch { /* */ }
  }

  const openCustomModal = () => {
    form.resetFields()
    form.setFieldsValue({
      provider: 'openai_compatible',
      temperature: 0.3,
      maxTokens: 4096,
      timeoutSeconds: 120,
    })
    setCustomModalOpen(true)
  }

  if (loading && !data) return <Spin style={{ display: 'block', margin: '80px auto' }} />

  const systemConfigs = data?.systemConfigs || []
  const projectConfigs = (data?.projectConfigs || []).filter(c => !c.providerConfigId)
  const activeProviderConfigId = data?.activeProviderConfigId
  const activeConfigId = data?.activeConfigId

  // 找当前激活的配置名
  const activeName = (() => {
    if (activeProviderConfigId) {
      return systemConfigs.find(c => c.id === activeProviderConfigId)?.name
    }
    if (activeConfigId) {
      const pc = projectConfigs.find(c => c.id === activeConfigId)
      return pc?.name
    }
    return null
  })()
  const activeModel = (() => {
    if (activeProviderConfigId) {
      return systemConfigs.find(c => c.id === activeProviderConfigId)?.model
    }
    if (activeConfigId) {
      return projectConfigs.find(c => c.id === activeConfigId)?.model
    }
    return null
  })()

  const statusIcon = (config) => {
    if (!config.status) return <Tag>未测试</Tag>
    if (config.status === 'ok') return <Tag color="cyan" icon={<CheckCircleOutlined />}>正常</Tag>
    return <Tag color="error" icon={<CloseCircleOutlined />}>异常</Tag>
  }

  const hasNoConfigs = systemConfigs.length === 0 && projectConfigs.length === 0
  const hasActiveConfig = !!(activeProviderConfigId || activeConfigId)

  // 「实际生效」由后端 describe_effective 给出(与管理端总览、真实调用同一套逻辑)。
  // 项目没单独选时会吃管理员的全局兜底 —— 此时 AI 功能是可用的,不能显示成"未配置/不可用"。
  const eff = data?.effective
  const effKind = eff?.configKind || 'none'
  const isFallback = effKind === 'system_default' || effKind === 'env'
  const aiUsable = effKind !== 'none'
  const effModel = (cat) => (eff?.models || []).find(m => m.category === cat)?.model
  const KIND_LABEL = {
    project_selected: '本项目已选择',
    project_custom: '本项目专属配置',
    system_default: '全局兜底（管理员统一提供）',
    env: '.env 兜底',
    none: '无可用配置',
  }

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: '#1d2129' }}>
          <RobotOutlined style={{ marginRight: 8 }} />
          AI 配置
        </h2>
        <span style={{ fontSize: 13, color: '#86909c' }}>
          配置 AI 服务后，以下功能将在本项目中可用。不配置不影响手动测试管理。
        </span>
      </div>

      {/* 功能说明 */}
      <Card size="small" style={{ marginBottom: 16, background: 'rgba(0,0,0,0.02)' }}>
        <div style={{ fontSize: 13, lineHeight: 2 }}>
          <b>配置 AI 后可用的功能：</b>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 24px', marginTop: 4 }}>
            <span>{aiUsable ? '✅' : '❌'} <b>AI 用例生成</b> — 从接口定义自动生成多维度测试用例</span>
            <span>{aiUsable ? '✅' : '❌'} <b>AI 脚本生成</b> — 根据用例自动生成 pytest 测试脚本</span>
            <span style={{ color: '#86909c' }}>{'⏳'} <b>质量评审</b> — AI 评审用例质量并打分（即将上线）</span>
            <span style={{ color: '#86909c' }}>{'⏳'} <b>失败诊断</b> — AI 分析测试失败原因（即将上线）</span>
          </div>
          {!aiUsable && (
            <div style={{ marginTop: 8, color: '#ff7d00' }}>
              {'⚠️'} 当前无可用 AI 配置（本项目未选择，且管理员的全局兜底已关闭），以上功能不可用。
              手动管理用例、执行测试、API Mock 等功能不受影响。
            </div>
          )}
        </div>
      </Card>

      {/* 当前生效横幅 —— 一定要显示「实际在用哪个」。项目没单独选时是吃全局兜底,
          以前这里直接报"尚未选择 / 功能不可用",与实际调用矛盾,用户看不出到底在用什么。 */}
      <Card
        size="small"
        style={{
          borderColor: aiUsable ? (isFallback ? '#faad14' : '#0ea5a0') : '#e8453c',
          background: aiUsable
            ? (isFallback ? 'rgba(250,173,20,0.08)' : 'rgba(14,165,160,0.08)')
            : 'rgba(255,77,79,0.08)',
          marginBottom: 16,
        }}
      >
        <Space wrap size="middle">
          {aiUsable
            ? <CheckCircleOutlined style={{ color: isFallback ? '#faad14' : '#0ea5a0', fontSize: 16 }} />
            : <CloseCircleOutlined style={{ color: '#e8453c', fontSize: 16 }} />}
          <Text strong>当前生效：</Text>
          <Tag color={isFallback ? 'orange' : (aiUsable ? 'cyan' : 'red')}>
            {KIND_LABEL[effKind] || effKind}
          </Tag>
          {eff?.connectionName && (
            <Tooltip title={`${eff.provider || '-'} · ${eff.baseUrlMasked || '-'}`}>
              <Text>{eff.connectionName}</Text>
            </Tooltip>
          )}
          {aiUsable && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              文本生成 <Tag style={{ marginInlineEnd: 4 }}>{effModel('text') || '—'}</Tag>
              UI 脚本生成 <Tag style={{ marginInlineEnd: 0 }}>{effModel('ui_script') || '—'}</Tag>
            </Text>
          )}
        </Space>
        {isFallback && (
          <div style={{ marginTop: 6, fontSize: 12, color: '#86909c' }}>
            本项目未单独选择 AI，正在使用管理员配置的全局兜底，AI 功能可正常使用。
            如需本项目专用（不同服务商/模型），在下方选择一个系统配置或创建项目专属配置即可覆盖。
          </div>
        )}
        {!aiUsable && (
          <div style={{ marginTop: 6, fontSize: 12, color: '#86909c' }}>
            本项目未选择 AI，且管理员已关闭全局兜底 → AI 功能不可用。请从下方选择或自建配置。
          </div>
        )}
      </Card>

      {/* 系统配置区域 */}
      {systemConfigs.length > 0 && (
        <>
          <Divider orientation="left" style={{ margin: '4px 0 12px' }}>系统配置（管理员提供）</Divider>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
            {systemConfigs.map((config) => {
              const isActive = config.id === activeProviderConfigId
              return (
                <Card
                  key={config.id}
                  size="small"
                  style={{ borderColor: isActive ? '#0ea5a0' : undefined }}
                  hoverable={!isActive}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Space size="middle">
                      <Radio checked={isActive} />
                      <div>
                        <Space size={4}>
                          <Text strong style={{ fontSize: 15 }}>{config.name}</Text>
                          {config.isSystemDefault && (
                            <Tag color="gold" icon={<StarFilled />} style={{ fontSize: 11 }}>推荐</Tag>
                          )}
                          {isActive && <Tag color="#0ea5a0">当前使用</Tag>}
                        </Space>
                        <div style={{ marginTop: 4 }}>
                          <Space size={12}>
                            <Text type="secondary">模型: <Tag style={{ marginLeft: 2 }}>{config.model}</Tag></Text>
                            {statusIcon(config)}
                          </Space>
                        </div>
                      </div>
                    </Space>
                    {!isActive && (
                      <Button
                        type="primary"
                        ghost
                        icon={<SwapOutlined />}
                        loading={selecting}
                        onClick={() => handleSelectSystem(config.id)}
                      >
                        使用此配置
                      </Button>
                    )}
                  </div>
                </Card>
              )
            })}
          </div>
        </>
      )}

      {/* 项目自建配置区域 */}
      <Divider orientation="left" style={{ margin: '4px 0 12px' }}>项目专属配置</Divider>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {projectConfigs.map((config) => {
          const isActive = config.id === activeConfigId && !activeProviderConfigId
          return (
            <Card key={config.id} size="small" style={{ borderColor: isActive ? '#0ea5a0' : undefined }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Space size="middle">
                  <Radio checked={isActive} />
                  <div>
                    <Space size={4}>
                      <Text strong>{config.name}</Text>
                      <Tag color="blue">项目自建</Tag>
                      {isActive && <Tag color="#0ea5a0">当前使用</Tag>}
                    </Space>
                    <div style={{ marginTop: 4 }}>
                      <Text type="secondary">
                        模型: <Tag style={{ marginLeft: 2 }}>{config.model || '未设置'}</Tag>
                      </Text>
                    </div>
                  </div>
                </Space>
                <Space>
                  <Popconfirm title="确认删除此配置？" onConfirm={() => handleDeleteCustom(config.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              </div>
            </Card>
          )
        })}

        <Button
          type="dashed"
          icon={<PlusOutlined />}
          onClick={openCustomModal}
          style={{ height: 48 }}
        >
          添加项目专属 AI 配置
        </Button>
      </div>

      {hasNoConfigs && (
        <Card style={{ marginTop: 16 }}>
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <span>
                暂无可用的 AI 配置<br />
                <Text type="secondary" style={{ fontSize: 13 }}>
                  请联系管理员添加系统配置，或点击上方按钮自建项目配置
                </Text>
              </span>
            }
          />
        </Card>
      )}

      {/* 用量统计 */}
      {usage && usage.totalCalls > 0 && (
        <>
          <Divider orientation="left" style={{ margin: '16px 0 12px' }}>AI 用量统计</Divider>
          <Card size="small">
            <div style={{ display: 'flex', gap: 24, marginBottom: 8 }}>
              <div><Text type="secondary">总调用：</Text><Text strong>{usage.totalCalls} 次</Text></div>
              <div><Text type="secondary">总 Token：</Text><Text strong>{usage.totalTokens.toLocaleString()}</Text></div>
            </div>
            {usage.bySkill.length > 0 && (
              <div>
                {usage.bySkill.map(s => (
                  <div key={s.skillName} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                    <Tag>{s.skillName}</Tag>
                    <Text type="secondary">{s.calls} 次 · {s.tokens.toLocaleString()} tokens</Text>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      )}

      {/* 自建配置弹窗 */}
      <Modal
        title="添加项目专属 AI 配置"
        open={customModalOpen}
        onCancel={() => setCustomModalOpen(false)}
        width={520}
        footer={[
          <Button key="test" icon={<ThunderboltOutlined />} onClick={handleTestCustom}>
            测试连接
          </Button>,
          <Button key="cancel" onClick={() => setCustomModalOpen(false)}>
            取消
          </Button>,
          <Button key="save" type="primary" onClick={handleCreateCustom}>
            创建并激活
          </Button>,
        ]}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="配置名称" rules={[{ required: true, message: '请输入配置名称' }]}>
            <Input placeholder="例如: 项目专用-DeepSeek" />
          </Form.Item>
          <Form.Item name="provider" label="服务商类型" rules={[{ required: true }]}>
            <Select options={PROVIDERS} />
          </Form.Item>
          <Form.Item name="baseUrl" label="API 地址" rules={[{ required: true, message: '请输入 API 地址' }]}>
            <Input placeholder="http://..." />
          </Form.Item>
          <Form.Item name="apiKey" label="API Key">
            <Input.Password placeholder="sk-..." />
          </Form.Item>
          <Form.Item name="authToken" label="网关 Token (Bearer)">
            <Input.Password placeholder="gw-..." />
          </Form.Item>
          <Form.Item name="model" label="模型名称" rules={[{ required: true, message: '请输入模型' }]}>
            <Input placeholder="deepseek-chat" />
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
        </Form>
      </Modal>
    </div>
  )
}
