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

// 分类名跟后端 _section() 一一对应，改名要同步改
const CAT_COLORS = {
  '定位项目/分支': 'green', '用例·手工步骤': 'blue', '接口库·只记怎么调': 'cyan',
  '接口场景·可执行': 'geekblue', '环境与变量': 'orange', '回推入库': 'red',
  '需求→用例流水线': 'magenta', 'UI 脚本': 'volcano', '执行报告': 'purple',
  '文档规范': 'gold', 'Skill 共享': 'default',
}

/**
 * 预设档位从后端取（app/mcp/profiles.py）——和工具注册表同一个进程，
 * 才不会重演"前端写死 20 条、后端实际 32 条"那种漂移。
 * 档位只是勾选的快捷方式，落库存的仍是展开后的显式工具名列表：
 * 语义可审计，日后改档位定义也不会让已有 Key 的范围悄悄变。
 */
const EMPTY_PROFILE = { label: '', task: '', hint: '', tools: null }

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

/**
 * 工具目录：按「我要干的活」看，而不是几十条平铺。
 * 选一个场景 → 该场景要调哪些工具直接高亮，其余灰掉，一眼能数清。
 */
function ToolCatalog({ tools, byCategory, profiles, onUseProfile }) {
  const [scene, setScene] = useState('live')
  const [onlyScene, setOnlyScene] = useState(true)
  const prof = profiles.find(p => p.key === scene) || profiles[0] || EMPTY_PROFILE
  const inScene = (n) => !prof.tools || prof.tools.includes(n)
  const usedCount = prof.tools ? prof.tools.length : tools.length

  return (
    <div>
      <Card size="small" style={{ ...cardStyle, marginBottom: 16, background: 'rgba(14,165,160,0.03)' }}>
        <div style={{ fontSize: 12, color: '#8c919e', marginBottom: 8 }}>我要干的活</div>
        <Radio.Group value={scene} onChange={e => setScene(e.target.value)} optionType="button" buttonStyle="solid" size="small"
          style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {profiles.map(p => <Radio.Button key={p.key} value={p.key}>{p.label}</Radio.Button>)}
        </Radio.Group>
        <div style={{ marginTop: 10, fontSize: 13, color: '#4e5969' }}>{prof.task}</div>
        <div style={{ marginTop: 4, fontSize: 12, color: '#8c919e' }}>{prof.hint}</div>
        <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 16 }}>
          <span style={{ fontSize: 13 }}>
            这件事会用到 <b style={{ color: '#0ea5a0', fontSize: 16 }}>{usedCount}</b> / {tools.length} 个工具
          </span>
          <Checkbox checked={onlyScene} onChange={e => setOnlyScene(e.target.checked)} style={{ fontSize: 12 }}>
            只看用到的
          </Checkbox>
          <Button size="small" type="primary" ghost onClick={() => onUseProfile(scene)}>
            按这个范围建 Key
          </Button>
        </div>
      </Card>

      {byCategory.map(([cat, items]) => {
        const shown = onlyScene ? items.filter(t => inScene(t.name)) : items
        if (!shown.length) return null
        const hit = items.filter(t => inScene(t.name)).length
        return (
          <div key={cat} style={{ marginBottom: 18 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <Tag color={CAT_COLORS[cat]} style={{ fontSize: 12, margin: 0 }}>{cat}</Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>{hit}/{items.length} 个用到</Text>
            </div>
            <Table rowKey="name" dataSource={shown} pagination={false} size="small" showHeader={false}
              rowClassName={r => inScene(r.name) ? '' : 'tool-dimmed'}
              columns={[
                {
                  dataIndex: 'name', width: 240,
                  render: (n) => (
                    <span>
                      {inScene(n)
                        ? <CheckCircleOutlined style={{ color: '#0ea5a0', fontSize: 12, marginRight: 6 }} />
                        : <span style={{ display: 'inline-block', width: 18 }} />}
                      <Text code style={{ fontSize: 11 }}>{n}</Text>
                    </span>
                  ),
                },
                { dataIndex: 'description', render: d => <span style={{ fontSize: 13 }}>{d}</span> },
                { dataIndex: 'params', width: 220, render: p => <Text type="secondary" style={{ fontSize: 11 }}>{p}</Text> },
              ]}
            />
          </div>
        )
      })}
      <style>{`.tool-dimmed { opacity: .38 }`}</style>
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
  const [picked, setPicked] = useState(null)
  const [profiles, setProfiles] = useState([])
  const [scopeEditing, setScopeEditing] = useState(null)   // 正在编辑范围的 Key

  useEffect(() => { fetchKeys(); fetchTools(); fetchProfiles() }, [])
  const fetchKeys = async () => { try { setApiKeys((await api.get('/mcp-keys')).data || []) } catch { /* 拦截器已弹错，这里不重复报 */ } }
  // 工具目录来自后端注册表，不再前端硬编码（曾经写死 20 条、后端实际 32 条）
  const fetchTools = async () => { try { setTools((await api.get('/mcp-keys/tools')).data || []) } catch { /* 同上 */ } }
  const fetchProfiles = async () => {
    try {
      const d = (await api.get('/mcp-keys/profiles')).data || {}
      const list = d.profiles || []
      setProfiles(list)
      // 默认档位的工具作为初始勾选 —— 建 Key 弹窗直接就是收敛过的范围
      const first = list.find(p => p.key === 'live') || list[0]
      if (first) setPicked(first.tools)
    } catch { /* 拉不到就只剩自定义勾选，不至于开天窗 */ }
  }
  const profileOf = (key) => profiles.find(p => p.key === key)

  const byCategory = useMemo(() => {
    const m = new Map()
    tools.forEach(t => { if (!m.has(t.category)) m.set(t.category, []); m.get(t.category).push(t) })
    return [...m.entries()]
  }, [tools])

  const applyProfile = (key) => {
    setProfile(key)
    // custom 不是后端档位：沿用当前勾选（从档位切过去时正好作为起点）
    if (key !== 'custom') setPicked(profileOf(key)?.tools ?? null)
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
            <span style={{ fontSize: 16, fontFamily: 'var(--font-mono)', fontWeight: 500, color: '#2e3138', letterSpacing: 0.3 }}>
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
            <ToolCatalog tools={tools} byCategory={byCategory} profiles={profiles}
              onUseProfile={(key) => { applyProfile(key); setCreateModalOpen(true); setNewKeyResult(null); setNewKeyName('') }} />
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
                          fontFamily: 'var(--font-mono)', overflow: 'auto', lineHeight: 1.6,
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
                            <div style={{ fontSize: 13, fontFamily: 'var(--font-mono)' }}>{ex.cmd}</div>
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
              {profiles.map(p => (
                <Radio key={p.key} value={p.key}>
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
            {/* 用户抱怨过"改了工具就要重新生成 Key，太麻烦"。改范围其实一直是就地生效的，
                但弹窗里从来没说过这句话 —— 人打开只看到两个单选和保存，疑虑一点没被打消。 */}
            <div style={{ fontSize: 12.5, color: '#4e5969', background: 'rgba(14,165,160,0.06)',
              border: '1px solid rgba(14,165,160,0.18)', borderRadius: 10, padding: '8px 12px', marginBottom: 12, lineHeight: 1.8 }}>
              保存后<b>立即对这个 Key 生效</b>。Key 本身不变 —— 对面的 Claude Code
              不用改配置、不用重连，下一次 <Text code style={{ fontSize: 11 }}>tools/list</Text> 就是新范围。
            </div>
            <Radio.Group
              value={scopeEditing.tools === null ? 'all' : 'custom'}
              onChange={e => setScopeEditing(s => ({
                ...s, tools: e.target.value === 'all' ? null : (profileOf('live')?.tools || []),
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
                  {profiles.filter(p => p.tools).map(p => (
                    <Button key={p.key} size="small"
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
