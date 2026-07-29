import { useState, useEffect, useMemo } from 'react'
import { Card, Tag, Space, Typography, Table, Button, message, Input, Modal, Popconfirm, Tabs, Badge, Radio, Checkbox, Tooltip, Alert } from 'antd'
import {
  ApiOutlined, CopyOutlined, ThunderboltOutlined,
  KeyOutlined, PlusOutlined, DeleteOutlined, CheckCircleOutlined,
  RobotOutlined, LinkOutlined, SettingOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'

const { Text } = Typography

const CAT_COLORS = {
  '用例': 'blue', 'API 接口': 'cyan', '环境变量': 'orange', '测试报告': 'purple',
  '接口测试': 'geekblue', '功能场景生成': 'magenta', '项目与分支': 'green',
  'UI 脚本': 'volcano', '文档生成': 'gold', '回推同步': 'red',
}

/**
 * 预设档位 —— 只是勾选的快捷方式，落库存的仍是展开后的显式工具列表，
 * 这样语义可审计，也不会因为日后改了档位定义导致已有 Key 的范围悄悄变化。
 */
const PROFILES = {
  live: {
    label: '活体验证回推',
    hint: '在被测系统里真跑一遍再回写成果。刻意排除 tb_generate_api_test（凭文档造，与活体验证冲突）',
    tools: [
      'tb_list_projects', 'tb_list_branches', 'tb_list_cases', 'tb_get_case', 'tb_get_folder_tree',
      'tb_create_case', 'tb_list_api_tree', 'tb_get_api_node', 'tb_list_environments',
      'tb_get_merged_variables', 'tb_get_sync_spec', 'tb_list_global_data',
      'tb_upsert_scenario_variables', 'tb_list_scenario_variables',
      'tb_sync_orchestrated_scenario', 'tb_list_api_tests', 'tb_get_api_test', 'tb_run_api_test',
    ],
  },
  docgen: {
    label: '文档批量生成',
    hint: '从需求文档批量产出用例的 AI 流水线',
    tools: [
      'tb_list_projects', 'tb_list_branches', 'tb_list_cases',
      'tb_create_scenario_task', 'tb_confirm_and_generate', 'tb_get_scenario_task',
      'tb_query_coverage_matrix', 'tb_get_generation_stats',
    ],
  },
  all: { label: '全量（不限制）', hint: '开放所有工具，适合调试', tools: null },
}

const cardStyle = { borderRadius: 12, border: '1px solid rgba(0,0,0,0.04)', boxShadow: 'none' }
const sectionTitle = { fontSize: 14, fontWeight: 600, color: '#2e3138', marginBottom: 4 }

/** 按分类勾选工具，分类头可整组全选/取消。 */
function ToolPicker({ tools, byCategory, value, onChange }) {
  const toggle = (name, on) =>
    onChange(on ? [...value, name] : value.filter(n => n !== name))
  const toggleCat = (items, on) => {
    const names = items.map(t => t.name)
    onChange(on ? [...new Set([...value, ...names])] : value.filter(n => !names.includes(n)))
  }
  return (
    <div style={{ marginTop: 12, maxHeight: 300, overflowY: 'auto', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 10, padding: 12 }}>
      <div style={{ fontSize: 12, color: '#8c919e', marginBottom: 8 }}>
        已选 <b style={{ color: '#0ea5a0' }}>{value.length}</b> / {tools.length}
      </div>
      {byCategory.map(([cat, items]) => {
        const names = items.map(t => t.name)
        const checkedCount = names.filter(n => value.includes(n)).length
        return (
          <div key={cat} style={{ marginBottom: 10 }}>
            <Checkbox
              checked={checkedCount === names.length}
              indeterminate={checkedCount > 0 && checkedCount < names.length}
              onChange={e => toggleCat(items, e.target.checked)}
            >
              <Tag color={CAT_COLORS[cat]} style={{ fontSize: 11 }}>{cat}</Tag>
            </Checkbox>
            <div style={{ marginLeft: 24, marginTop: 4, display: 'flex', flexDirection: 'column', gap: 2 }}>
              {items.map(t => (
                <Checkbox key={t.name} checked={value.includes(t.name)}
                  onChange={e => toggle(t.name, e.target.checked)}>
                  <Text code style={{ fontSize: 11 }}>{t.name}</Text>
                </Checkbox>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function MCPTools() {
  const mcpUrl = `http://${window.location.hostname}:18800/mcp/`
  const [apiKeys, setApiKeys] = useState([])
  const [tools, setTools] = useState([])
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [newKeyName, setNewKeyName] = useState('')
  const [newKeyResult, setNewKeyResult] = useState(null)
  const [creating, setCreating] = useState(false)
  const [profile, setProfile] = useState('live')
  const [picked, setPicked] = useState(PROFILES.live.tools)
  const [scopeEditing, setScopeEditing] = useState(null)   // 正在编辑范围的 Key

  useEffect(() => { fetchKeys(); fetchTools() }, [])
  const fetchKeys = async () => { try { setApiKeys((await api.get('/mcp-keys')).data || []) } catch {} }
  // 工具目录来自后端注册表，不再前端硬编码（曾经写死 20 条、后端实际 32 条）
  const fetchTools = async () => { try { setTools((await api.get('/mcp-keys/tools')).data || []) } catch {} }

  const byCategory = useMemo(() => {
    const m = new Map()
    tools.forEach(t => { if (!m.has(t.category)) m.set(t.category, []); m.get(t.category).push(t) })
    return [...m.entries()]
  }, [tools])

  const applyProfile = (key) => {
    setProfile(key)
    // custom 不在 PROFILES 里：沿用当前勾选（从档位切过去时正好作为起点）
    if (key !== 'custom') setPicked(PROFILES[key].tools)
    else if (picked === null) setPicked(tools.map(t => t.name))
  }

  const createKey = async () => {
    setCreating(true)
    try {
      const body = { name: newKeyName || 'default' }
      if (picked) body.allowedTools = picked
      setNewKeyResult((await api.post('/mcp-keys', body)).data)
      setNewKeyName(''); fetchKeys()
    }
    catch (e) { message.error(e.message || '创建失败') } finally { setCreating(false) }
  }
  const revokeKey = async (id) => { try { await api.delete(`/mcp-keys/${id}`); message.success('已吊销'); fetchKeys() } catch { message.error('吊销失败') } }

  const saveScope = async () => {
    const { id, tools: sel } = scopeEditing
    try {
      await api.patch(`/mcp-keys/${id}`, sel === null ? { resetTools: true } : { allowedTools: sel })
      message.success('工具范围已更新')
      setScopeEditing(null); fetchKeys()
    } catch (e) { message.error(e.message || '更新失败') }
  }

  const copy = (text) => copyToClipboard(text).then(() => message.success('已复制'))

  const onlineCount = apiKeys.filter(k => k.lastUsedAt && Date.now() - new Date(k.lastUsedAt).getTime() < 30 * 60 * 1000).length
  const mcpConfig = JSON.stringify({ mcpServers: { testbench: { type: "streamable-http", url: mcpUrl, headers: { Authorization: "Bearer <你的API Key>" } } } }, null, 2)

  return (
    <div style={{ maxWidth: 960 }}>
      {/* ── 页头 ── */}
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: '0 0 6px', color: '#1d2129' }}>
          <LinkOutlined style={{ marginRight: 8, color: '#0ea5a0' }} />MCP 工具中心
        </h2>
        <Text type="secondary" style={{ fontSize: 13 }}>管理 Claude Code 与平台的连接，查看可用的 AI 工具</Text>
      </div>

      {/* ── 服务地址（独立突出） ── */}
      <Card size="small" style={{ ...cardStyle, marginBottom: 16, borderLeft: '3px solid #0ea5a0' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 12, color: '#8c919e', marginBottom: 2 }}>MCP 服务地址</div>
            <span style={{ fontSize: 16, fontFamily: "'SF Mono', Monaco, Consolas, monospace", fontWeight: 500, color: '#2e3138', letterSpacing: 0.3 }}>
              {mcpUrl}
            </span>
          </div>
          <Space size={16}>
            <Button size="small" icon={<CopyOutlined />} onClick={() => copy(mcpUrl)}>复制地址</Button>
            <Space split={<span style={{ color: '#e0e0e3' }}>|</span>}>
              <Text type="secondary" style={{ fontSize: 12 }}>{onlineCount}/{apiKeys.length} 在线</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>{tools.length} 个工具</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>StreamableHTTP</Text>
            </Space>
          </Space>
        </div>
      </Card>

      {/* ── 主体 Tab ── */}
      <Tabs defaultActiveKey="connections" items={[
        {
          key: 'connections',
          label: <span><KeyOutlined /> 连接管理 {onlineCount > 0 && <Badge count={onlineCount} size="small" style={{ marginLeft: 4 }} />}</span>,
          children: (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
                <Text type="secondary" style={{ fontSize: 13 }}>每个 Claude Code 用独立 API Key 连接。创建后按「配置指南」完成配置。</Text>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => { setCreateModalOpen(true); setNewKeyResult(null); setNewKeyName('') }}>创建 Key</Button>
              </div>

              {apiKeys.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {apiKeys.map(k => {
                    const lastUsed = k.lastUsedAt ? new Date(k.lastUsedAt) : null
                    const isOnline = lastUsed && (Date.now() - lastUsed.getTime() < 30 * 60 * 1000)
                    const isRecent = lastUsed && (Date.now() - lastUsed.getTime() < 24 * 60 * 60 * 1000)
                    return (
                      <Card key={k.id} size="small" style={{
                        ...cardStyle,
                        borderLeft: `3px solid ${isOnline ? '#0ea5a0' : isRecent ? '#faad14' : '#e8e8e8'}`,
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                            <div style={{
                              width: 36, height: 36, borderRadius: 12,
                              background: isOnline ? 'rgba(14,165,160,0.08)' : isRecent ? 'rgba(250,173,20,0.08)' : 'rgba(0,0,0,0.03)',
                              display: 'flex', alignItems: 'center', justifyContent: 'center',
                            }}>
                              <RobotOutlined style={{ fontSize: 18, color: isOnline ? '#0ea5a0' : isRecent ? '#faad14' : '#bfc4cd' }} />
                            </div>
                            <div>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span style={{ fontSize: 14, fontWeight: 600, color: '#2e3138' }}>{k.name}</span>
                                <Text code style={{ fontSize: 11, color: '#8c919e' }}>{k.prefix}...</Text>
                                {isOnline && <Tag color="cyan" style={{ fontSize: 10, lineHeight: '16px', padding: '0 6px', margin: 0 }}>在线</Tag>}
                                {!isOnline && isRecent && <Tag color="warning" style={{ fontSize: 10, lineHeight: '16px', padding: '0 6px', margin: 0 }}>最近活跃</Tag>}
                                <Tooltip title={k.allowedTools
                                  ? `已限定 ${k.allowedTools.length} 个工具，范围外的工具该连接看不到也调不了`
                                  : '未限制，可使用全部工具'}>
                                  <Tag color={k.allowedTools ? 'processing' : 'default'} style={{ fontSize: 10, lineHeight: '16px', padding: '0 6px', margin: 0 }}>
                                    {k.allowedTools ? `${k.allowedTools.length}/${tools.length} 工具` : '全部工具'}
                                  </Tag>
                                </Tooltip>
                              </div>
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                {lastUsed ? `最近调用 ${lastUsed.toLocaleString('zh-CN')}` : '尚未使用'}
                              </Text>
                            </div>
                          </div>
                          <Space size={4}>
                            <Button size="small" type="text" icon={<SettingOutlined />}
                              onClick={() => setScopeEditing({ id: k.id, name: k.name, tools: k.allowedTools ?? null })}>
                              工具范围
                            </Button>
                            <Popconfirm title="吊销后该连接立即失效" onConfirm={() => revokeKey(k.id)} okText="吊销" cancelText="取消" okButtonProps={{ danger: true }}>
                              <Button size="small" danger type="text" icon={<DeleteOutlined />}>吊销</Button>
                            </Popconfirm>
                          </Space>
                        </div>
                      </Card>
                    )
                  })}
                </div>
              ) : (
                <div style={{ textAlign: 'center', padding: '60px 0', color: '#bfc4cd' }}>
                  <RobotOutlined style={{ fontSize: 36, marginBottom: 12 }} />
                  <div style={{ fontSize: 14 }}>还没有连接</div>
                  <div style={{ fontSize: 12, marginTop: 4 }}>点击「创建 Key」添加 Claude Code 连接</div>
                </div>
              )}
            </div>
          ),
        },
        {
          key: 'tools',
          label: <span><ThunderboltOutlined /> 工具列表 ({tools.length})</span>,
          children: (
            <Table rowKey="name" dataSource={tools} pagination={false} size="small"
              columns={[
                { title: '工具', dataIndex: 'name', width: 220, render: n => <Text code style={{ fontSize: 11 }}>{n}</Text> },
                { title: '分类', dataIndex: 'category', width: 80, render: c => <Tag color={CAT_COLORS[c]} style={{ fontSize: 11 }}>{c}</Tag> },
                { title: '说明', dataIndex: 'description', render: d => <span style={{ fontSize: 13 }}>{d}</span> },
                { title: '参数', dataIndex: 'params', width: 240, render: p => <Text type="secondary" style={{ fontSize: 11 }}>{p}</Text> },
              ]}
            />
          ),
        },
        {
          key: 'guide',
          label: <span><ApiOutlined /> 配置指南</span>,
          children: (
            <div style={{ maxWidth: 680 }}>
              {[
                { num: '1', title: '创建 API Key', desc: '在「连接管理」Tab 点击「创建 Key」，复制保存密钥。' },
                { num: '2', title: '添加 .mcp.json 配置', desc: '将以下内容合并到项目根目录的 .mcp.json 文件：', code: mcpConfig },
                { num: '3', title: '在 Claude Code 中使用', desc: '重启 Claude Code，然后直接用自然语言：', examples: [
                  { hint: '从需求文档生成手工测试用例', cmd: '帮我为这份需求生成测试用例：用户可以登录系统...' },
                  { hint: '查看生成进度', cmd: '查看最近的测试用例生成任务' },
                ] },
              ].map((step) => (
                <div key={step.num} style={{ marginBottom: 28 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                    <div style={{
                      width: 28, height: 28, borderRadius: '50%', flexShrink: 0,
                      background: 'linear-gradient(135deg, #0ea5a0, #7cacf8)', color: '#fff',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 13, fontWeight: 600,
                    }}>{step.num}</div>
                    <span style={sectionTitle}>{step.title}</span>
                  </div>
                  <div style={{ marginLeft: 38 }}>
                    <Text type="secondary" style={{ fontSize: 13 }}>{step.desc}</Text>
                    {step.code && (
                      <div style={{ position: 'relative', marginTop: 8 }}>
                        <pre style={{
                          background: 'rgba(0,0,0,0.02)', border: '1px solid rgba(0,0,0,0.06)',
                          borderRadius: 12, padding: '14px 18px', fontSize: 12,
                          fontFamily: "'SF Mono', Monaco, monospace", overflow: 'auto', lineHeight: 1.6,
                        }}>{step.code}</pre>
                        <Button size="small" icon={<CopyOutlined />} style={{ position: 'absolute', top: 10, right: 10 }}
                          onClick={() => copy(step.code)}>复制</Button>
                      </div>
                    )}
                    {step.examples && (
                      <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {step.examples.map((ex, i) => (
                          <Card key={i} size="small" style={{ ...cardStyle, borderLeft: '3px solid #7cacf8' }}>
                            <div style={{ fontSize: 11, color: '#8c919e', marginBottom: 2 }}>{ex.hint}</div>
                            <div style={{ fontSize: 13, fontFamily: 'monospace' }}>{ex.cmd}</div>
                          </Card>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ),
        },
      ]} />

      {/* 创建 Key 弹窗 */}
      <Modal title="创建连接" open={createModalOpen} onCancel={() => setCreateModalOpen(false)} width={560}
        footer={newKeyResult ? [
          <Button key="close" type="primary" onClick={() => setCreateModalOpen(false)}>我已保存，关闭</Button>
        ] : [
          <Button key="cancel" onClick={() => setCreateModalOpen(false)}>取消</Button>,
          <Button key="create" type="primary" icon={<PlusOutlined />} onClick={createKey} loading={creating}>创建</Button>,
        ]}>
        {!newKeyResult ? (
          <div>
            <Text type="secondary" style={{ fontSize: 13, display: 'block', marginBottom: 16 }}>
              给这个连接取个名字，方便识别是谁的 Claude Code。
            </Text>
            <Input placeholder="如：小李的开发机、CI 流水线" value={newKeyName} onChange={e => setNewKeyName(e.target.value)} size="large" />

            <div style={{ ...sectionTitle, marginTop: 20 }}>工具范围</div>
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 10 }}>
              限定这个连接能用哪些工具。范围外的工具 Claude Code <b>看不到也调不了</b>，
              避免它在几十个工具里挑错。
            </Text>
            <Radio.Group value={profile} onChange={e => applyProfile(e.target.value)}
              style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {Object.entries(PROFILES).map(([k, p]) => (
                <Radio key={k} value={k}>
                  <span style={{ fontSize: 13 }}>{p.label}</span>
                  <Text type="secondary" style={{ fontSize: 11, marginLeft: 6 }}>
                    {p.tools ? `${p.tools.length} 个` : `${tools.length} 个`} · {p.hint}
                  </Text>
                </Radio>
              ))}
              <Radio value="custom"><span style={{ fontSize: 13 }}>自定义</span></Radio>
            </Radio.Group>

            {profile === 'custom' && (
              <ToolPicker tools={tools} byCategory={byCategory}
                value={picked || []} onChange={setPicked} />
            )}
          </div>
        ) : (
          <div>
            <div style={{ textAlign: 'center', padding: '20px 0 16px', marginBottom: 16, background: 'rgba(14,165,160,0.04)', borderRadius: 12 }}>
              <CheckCircleOutlined style={{ fontSize: 28, color: '#0ea5a0', marginBottom: 8 }} />
              <div style={{ fontWeight: 600, fontSize: 15 }}>创建成功</div>
              <Text type="secondary" style={{ fontSize: 12 }}>请立即复制密钥，关闭后不再显示</Text>
            </div>
            <Card size="small" style={cardStyle}>
              <Text code copyable style={{ fontSize: 13, wordBreak: 'break-all' }}>{newKeyResult.key}</Text>
            </Card>
            {newKeyResult.allowedTools && (
              <Alert style={{ marginTop: 12 }} type="info" showIcon
                message={`该连接已限定 ${newKeyResult.allowedTools.length} 个工具`}
                description="范围外的工具不会出现在它的工具列表里，直接调用也会被拒绝。" />
            )}
          </div>
        )}
      </Modal>

      {/* 编辑已有 Key 的工具范围 */}
      <Modal title={`工具范围 · ${scopeEditing?.name || ''}`} open={!!scopeEditing} width={560}
        onCancel={() => setScopeEditing(null)} onOk={saveScope} okText="保存">
        {scopeEditing && (
          <div>
            <Radio.Group
              value={scopeEditing.tools === null ? 'all' : 'custom'}
              onChange={e => setScopeEditing(s => ({
                ...s, tools: e.target.value === 'all' ? null : (PROFILES.live.tools),
              }))}
              style={{ display: 'flex', gap: 16, marginBottom: 12 }}
            >
              <Radio value="all">不限制（全部工具）</Radio>
              <Radio value="custom">限定范围</Radio>
            </Radio.Group>

            {scopeEditing.tools !== null && (
              <>
                <Space wrap size={6} style={{ marginBottom: 4 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>快速套用：</Text>
                  {Object.entries(PROFILES).filter(([, p]) => p.tools).map(([k, p]) => (
                    <Button key={k} size="small"
                      onClick={() => setScopeEditing(s => ({ ...s, tools: p.tools }))}>{p.label}</Button>
                  ))}
                </Space>
                <ToolPicker tools={tools} byCategory={byCategory}
                  value={scopeEditing.tools}
                  onChange={v => setScopeEditing(s => ({ ...s, tools: v }))} />
              </>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
