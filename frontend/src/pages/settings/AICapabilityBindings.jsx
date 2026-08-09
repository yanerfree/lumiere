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

  const { bindings = [], registry: rawRegistry = [], categoryMeta = {}, fallbackEnabled } = data
  // 已下线/已封存的能力不展示 —— 留着一张永远不会被点亮的卡片，
  // 只会让每个新用户重新问一遍"这个是坏了还是我没配好"。
  // 后端保留了 key（删了会让调用静默降档到 text 档），这里只是不渲染。
  const registry = rawRegistry.filter(m => !m.deprecated)
  // 一个模块都不剩的内置档位不渲染 —— 留一张永远点不亮的卡片，
  // 每个新用户都要重新问一遍"这个是坏了还是我没配好"。
  // 自定义档位即使空着也留，那是用户自己建的。
  const hiddenBuiltin = bindings.filter(
    b => b.isBuiltin && !registry.some(m => m.category === b.category)
  )
  const visibleBindings = bindings.filter(b => !hiddenBuiltin.includes(b))
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

      {/* 「现在在用」——用户一天里 99% 的时候只想知道这一件事：我点生成，是谁在算？
          原来这里是「兜底连接：公司网关-Sonnet · claude-sonnet-4-6」紧挨着
          「实际生效：文本生成 → claude-sonnet-5」，两个模型号并排放着谁也不解释谁。
          工程师看到这个第一反应不是"我要升级"，是"这页面在骗我" —— 用户抱怨
          "平台只能选 4.6" 就是看的那一行，而下拉里其实一直有 opus-5。
          解决"打架"的办法不是把两个数字解释清楚，是让首屏只剩一个。 */}
      {overview?.fallback && (() => {
        const resolved = overview.fallback.resolved || []
        const models = [...new Set(resolved.map(r => r.model).filter(Boolean))]
        const conn = overview.fallback.connection
        const single = models.length === 1 ? models[0] : null
        return (
          <div style={{
            marginBottom: 16, padding: '16px 18px', borderRadius: 12,
            background: fallbackEnabled ? 'linear-gradient(135deg, rgba(14,165,160,0.06), rgba(78,138,240,0.05))' : 'rgba(0,0,0,0.03)',
            border: '1px solid rgba(14,165,160,0.18)',
            opacity: fallbackEnabled ? 1 : 0.55,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
              <div style={{ minWidth: 280 }}>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>平台当前在用</div>
                {single ? (
                  <div style={{ fontSize: 26, fontWeight: 600, letterSpacing: 0.3, color: '#1d2129', fontFamily: 'var(--font-mono)' }}>
                    {single}
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                    {resolved.map(r => (
                      <div key={r.category} style={{ fontSize: 15 }}>
                        <span style={{ color: '#86909c', fontSize: 12, marginRight: 6 }}>{r.label}</span>
                        <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{r.model || '—'}</span>
                      </div>
                    ))}
                  </div>
                )}
                <div style={{ fontSize: 12, color: '#86909c', marginTop: 6 }}>
                  经 {conn?.name || (overview.fallback.usingEnv ? '.env 配置' : '未配置')}
                  {conn?.baseUrlMasked && (
                    <span style={{ marginLeft: 6, fontFamily: 'var(--font-mono)', color: '#4e5969' }}>
                      {conn.baseUrlMasked}
                    </span>
                  )}
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                {conn?.status === 'ok'
                  ? <Tag color="success" style={{ margin: 0 }}>连接正常</Tag>
                  : conn
                    ? <Tooltip title={conn.statusMessage}><Tag color="error" style={{ margin: 0 }}>连接异常</Tag></Tooltip>
                    : <Tag color="warning" style={{ margin: 0 }}>未配置兜底</Tag>}
                <div style={{ fontSize: 12, color: '#86909c', marginTop: 8 }}>
                  {single
                    ? `这一个模型负责平台上全部 ${registry.length} 项 AI 能力`
                    : `${resolved.length} 个档位各用各的模型`}
                </div>
                {/* 429 降级通道 —— 顶栏「服务 N/17」里有它，但在这一页改模型的人
                    不会去看顶栏。它挂了的话，换完模型跑生成会撞上莫名其妙的 429 失败。 */}
                {overview.fallback.cliChannel && (
                  <div style={{ fontSize: 12, marginTop: 6 }}>
                    <span style={{ color: '#86909c' }}>限流降级通道　</span>
                    {overview.fallback.cliChannel.alive ? (
                      <Tag color="success" style={{ margin: 0 }}>正常</Tag>
                    ) : (
                      <Tooltip title={overview.fallback.cliChannel.hint}>
                        <Tag color="error" style={{ margin: 0 }}>不可用</Tag>
                      </Tooltip>
                    )}
                  </div>
                )}
                <div style={{ marginTop: 6 }}>
                  <Select
                    size="small" style={{ minWidth: 230 }}
                    value={overview.fallback.connection?.id}
                    onChange={switchFallbackConn}
                    placeholder="选择兜底连接"
                    options={(overview.candidates || []).map(c => ({ value: c.id, label: c.name }))}
                  />
                </div>
              </div>
            </div>
          </div>
        )
      })()}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 14 }}>
        {visibleBindings.map(b => {
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

      {hiddenBuiltin.length > 0 && (
        <div style={{ marginTop: 12, fontSize: 12, color: '#86909c', lineHeight: 1.7 }}>
          {hiddenBuiltin.map(b => b.label).join('、')} 已下线，不再由平台调 AI。
          UI 脚本改为在外部 Claude Code 本地写好、真跑通后回推，平台负责存、跑、留痕。
          <br />
          具体怎么用：<Text code style={{ fontSize: 12 }}>MCP 工具中心 → 用例：步骤 + 接口场景</Text>
        </div>
      )}

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
