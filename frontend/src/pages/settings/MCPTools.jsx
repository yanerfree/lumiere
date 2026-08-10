import { useState, useEffect, useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { Card, Tag, Space, Typography, Button, message, Input, Modal, Popconfirm, Tabs, Badge, Checkbox, Tooltip, Alert, Dropdown, Collapse, Switch } from 'antd'
import {
  ApiOutlined, CopyOutlined, ThunderboltOutlined,
  KeyOutlined, PlusOutlined, DeleteOutlined, CheckCircleOutlined,
  RobotOutlined, LinkOutlined, DownOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'

const { Text } = Typography

// 分类名跟后端 _section() 一一对应，改名要同步改。
// ⚠ 漏一个不会报错，只会静默变成灰色 —— 「失败归因」就这么灰了一整轮没人发现。
// 后端加了新分类时这里要跟着加，test_mcp_category_colors 会红。
const CAT_COLORS = {
  '定位项目/分支': 'green', '用例·手工步骤': 'blue', '接口库·只记怎么调': 'cyan',
  '接口场景·可执行': 'geekblue', '环境与变量': 'orange', '回推入库': 'red',
  '需求→用例流水线': 'magenta', 'UI 脚本': 'volcano', '执行报告': 'purple',
  '文档规范': 'gold', 'Skill 共享': 'lime', '失败归因': 'error', '其它': 'default',
}

/*
 * 档位从后端取（app/mcp/profiles.py）——和工具注册表同一个进程，
 * 才不会重演"前端写死 20 条、后端实际 32 条"那种漂移。
 * 档位只是勾选的快捷方式，落库存的仍是展开后的显式工具名列表：
 * 语义可审计，日后改档位定义也不会让已有项目的范围悄悄变。
 */

const cardStyle = { borderRadius: 12, border: '1px solid rgba(0,0,0,0.04)', boxShadow: 'none' }
const sectionTitle = { fontSize: 14, fontWeight: 600, color: '#2e3138', marginBottom: 4 }

// 分类色板对应的实色（Tag 的语义色拿不到色值，分组条要用真颜色）
const CAT_HEX = {
  green: '#52c41a', blue: '#1677ff', cyan: '#13c2c2', geekblue: '#2f54eb',
  orange: '#fa8c16', red: '#f5222d', magenta: '#eb2f96', volcano: '#fa541c',
  purple: '#722ed1', gold: '#faad14', lime: '#a0d911', error: '#e8453c',
  default: '#8c919e',
}
const catHex = (cat) => CAT_HEX[CAT_COLORS[cat]] || CAT_HEX.default

/** 一行工具：勾选框 + 名字 + 说明（夹两行，悬停看全文）+ 参数。 */
function ToolRow({ t, checked, disabled, onToggle }) {
  const clamp2 = {
    display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
  }
  return (
    <label style={{
      display: 'flex', alignItems: 'flex-start', gap: 10, padding: '9px 14px 9px 34px',
      borderTop: '1px solid rgba(0,0,0,0.04)', cursor: disabled ? 'default' : 'pointer',
      background: checked && !disabled ? 'rgba(14,165,160,0.035)' : 'transparent',
    }}>
      <Checkbox disabled={disabled} checked={checked}
        onChange={e => onToggle(t.name, e.target.checked)} style={{ marginTop: 1 }} />
      <Text code style={{ fontSize: 11, flex: '0 0 190px', lineHeight: '20px' }}>{t.name}</Text>
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* 说明最长的有 400 多字，全铺开就没法挑工具了。夹两行，悬停看全文。 */}
        <Tooltip title={<span style={{ fontSize: 12 }}>{t.description}</span>}
          styles={{ root: { maxWidth: 620 } }}>
          <div style={{ fontSize: 12.5, color: '#4e5969', lineHeight: 1.65, ...clamp2 }}>
            {t.description}
          </div>
        </Tooltip>
        {t.params && (
          <div style={{
            fontSize: 11, color: '#a8adb7', marginTop: 3, fontFamily: 'var(--font-mono)',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>{t.params}</div>
        )}
      </div>
    </label>
  )
}

/**
 * 本项目的 MCP 工具范围 —— 一张默认收起的可勾选清单。
 *
 * 走过三段弯路，都记下来免得再犯：
 *
 * 1. 做成 9 张档位卡二选一 → 只能"全部"或"恰好某一档"，想在某档基础上
 *    多开一个工具做不到。范围是个集合，就该按集合编辑。
 * 2. 改成勾选之后，把勾选框藏在「只开放勾选的」模式后面 →
 *    **默认打开一个勾选框都看不到**，跟改之前长得一样。模式切换是给程序看的。
 * 3. 勾选框露出来了，但 42 条整段说明全铺开 → 一堵墙，滚好几屏才看得完。
 *
 * 定稿：分类默认收起（整页 12 行看得完）、勾选框常驻可点、说明夹两行悬停看全文。
 */
function ScopePanel({ tools, byCategory, profiles, scope, keyCount, saving, onSave }) {
  const savedUnlimited = !scope?.allowedTools
  const savedList = scope?.allowedTools || []
  const allNames = tools.map(t => t.name)
  const [unlimited, setUnlimited] = useState(savedUnlimited)
  // 不限制时下面照样显示全勾，人看到的和"全开"一致
  const [sel, setSel] = useState(savedUnlimited ? allNames : savedList)
  const [openKeys, setOpenKeys] = useState([])
  // ⚠ 保存成功后要跟上服务端那份，但**不要用 useEffect 去 setState 同步** ——
  // 那是 react-hooks/set-state-in-effect。调用方给了 key（见 <ScopePanel key=...>），
  // saved 变了整个组件重挂，初值自然就是新的。

  const selSet = new Set(sel)
  const same = (a, b) => a.length === b.length && a.every(x => b.includes(x))
  const dirty = unlimited !== savedUnlimited || (!unlimited && !same(sel, savedList))
  const picked = unlimited ? tools.length : sel.length

  const toggle = (name, on) => setSel(v => on ? [...v, name] : v.filter(n => n !== name))
  const toggleCat = (items, on) => {
    const names = items.map(t => t.name)
    setSel(v => on ? [...new Set([...v, ...names])] : v.filter(n => !names.includes(n)))
  }
  const applyProfile = (p) => {
    setUnlimited(false)
    setSel(p.tools ? [...p.tools] : allNames)
    // 套完把命中的分类展开，让人看见它到底勾了些什么，而不是只看到个数字
    setOpenKeys(byCategory
      .filter(([, items]) => items.some(t => (p.tools || allNames).includes(t.name)))
      .map(([c]) => c))
  }

  // 预设走下拉，不排成一行按钮：那行 chips 长短不一会换行，Tooltip 弹出来
  // 还正好盖住旁边的按钮。菜单是纵向的，说明直接当第二行小字排下来。
  const presetMenu = {
    items: profiles.filter(p => p.tools).map(p => ({
      key: p.key,
      label: (
        <div style={{ padding: '3px 0', maxWidth: 400 }}>
          <div style={{ fontSize: 13, fontWeight: 500 }}>
            {p.label}
            <Text type="secondary" style={{ fontSize: 11, marginLeft: 6 }}>{p.tools.length} 个</Text>
          </div>
          <div style={{ fontSize: 11.5, color: '#8c919e', whiteSpace: 'normal', lineHeight: 1.5 }}>{p.task}</div>
        </div>
      ),
    })),
    onClick: ({ key }) => applyProfile(profiles.find(p => p.key === key)),
  }

  const collapseItems = byCategory.map(([cat, items]) => {
    const names = items.map(t => t.name)
    const n = names.filter(x => selSet.has(x)).length
    const full = n === names.length
    return {
      key: cat,
      label: (
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <span style={{ width: 3, height: 15, borderRadius: 2, background: catHex(cat), flex: '0 0 auto' }} />
          {/* 勾选框自己吃掉点击，别让"想勾一整类"变成"把它收起来" */}
          <span onClick={e => e.stopPropagation()} style={{ display: 'inline-flex' }}>
            <Checkbox disabled={unlimited} checked={full}
              indeterminate={n > 0 && !full}
              onChange={e => toggleCat(items, e.target.checked)} />
          </span>
          <span style={{ fontWeight: 600, fontSize: 13, color: '#2e3138' }}>{cat}</span>
        </div>
      ),
      extra: (
        <span style={{
          fontSize: 11.5, fontWeight: 600, padding: '1px 9px', borderRadius: 10,
          color: full ? '#0ea5a0' : n ? '#fa8c16' : '#9aa0aa',
          background: full ? 'rgba(14,165,160,0.1)' : n ? 'rgba(250,140,22,0.1)' : 'rgba(0,0,0,0.04)',
        }}>{n}/{items.length}</span>
      ),
      children: (
        <div>
          {items.map(t => (
            <ToolRow key={t.name} t={t} disabled={unlimited}
              checked={selSet.has(t.name)} onToggle={toggle} />
          ))}
        </div>
      ),
      styles: { body: { padding: 0 } },
    }
  })

  return (
    <div>
      <Card size="small" style={{ ...cardStyle, marginBottom: 14, background: 'rgba(14,165,160,0.035)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
          <div style={{ minWidth: 210 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
              <span style={{ fontSize: 26, fontWeight: 700, color: picked ? '#0ea5a0' : '#e8453c', lineHeight: 1 }}>
                {picked}
              </span>
              <span style={{ fontSize: 13, color: '#8c919e' }}>/ {tools.length} 个工具已开放</span>
            </div>
            <div style={{ height: 4, borderRadius: 2, background: 'rgba(0,0,0,0.06)', marginTop: 8, overflow: 'hidden' }}>
              <div style={{
                height: '100%', width: `${(picked / Math.max(tools.length, 1)) * 100}%`,
                background: '#0ea5a0', borderRadius: 2, transition: 'width .2s',
              }} />
            </div>
            <Text type="secondary" style={{ fontSize: 11.5, display: 'block', marginTop: 6 }}>
              勾上的 CC 才看得到、调得动 · 本项目 <b style={{ color: '#4e5969' }}>{keyCount}</b> 把 Key 都按它生效
            </Text>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 10 }}>
            <Space size={8} wrap>
              <Dropdown menu={presetMenu} trigger={['click']} placement="bottomRight">
                <Button size="small">套用预设 <DownOutlined style={{ fontSize: 10 }} /></Button>
              </Dropdown>
              <Button size="small" disabled={unlimited} onClick={() => setSel(allNames)}>全选</Button>
              <Button size="small" disabled={unlimited} onClick={() => setSel([])}>清空</Button>
              <Button size="small" type="text" onClick={() =>
                setOpenKeys(openKeys.length ? [] : byCategory.map(([c]) => c))}>
                {openKeys.length ? '全部收起' : '全部展开'}
              </Button>
            </Space>
            <Space size={12} wrap>
              {/* 「不限制」不等于"把 42 个全勾上"：前者以后新增的工具自动包含，
                  后者不会（落库 NULL vs 显式清单）。这个区别得写出来，别让人猜。 */}
              <Tooltip title="勾上之后，以后平台新增的工具也自动包含；取消勾选就按下面的清单来">
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <Switch size="small" checked={unlimited}
                    onChange={v => { setUnlimited(v); if (v) setSel(allNames) }} />
                  <span style={{ fontSize: 12.5, color: unlimited ? '#0ea5a0' : '#8c919e' }}>不限制</span>
                </span>
              </Tooltip>
              {dirty && (
                <>
                  <Button size="small" onClick={() => {
                    setUnlimited(savedUnlimited); setSel(savedUnlimited ? allNames : savedList)
                  }}>放弃修改</Button>
                  <Button size="small" type="primary" loading={saving}
                    disabled={!unlimited && sel.length === 0}
                    onClick={() => onSave(unlimited ? null : sel)}>保存</Button>
                </>
              )}
            </Space>
          </div>
        </div>
        {unlimited && (
          <div style={{ marginTop: 8, fontSize: 11.5, color: '#8c919e' }}>
            当前不限制，所以下面全勾且点不动 —— 关掉右上角「不限制」就能逐个勾选。
          </div>
        )}
        {!unlimited && sel.length === 0 && (
          <div style={{ marginTop: 8, fontSize: 12, color: '#e8453c' }}>
            一个都没勾 —— 那样 CC 连不上任何工具，等于把这个项目的 MCP 关了。至少勾一个再保存。
          </div>
        )}
      </Card>

      {/* 默认全部收起：42 条整段说明铺开是一堵墙，收起来整页 12 行，要改哪类点哪类。 */}
      <Collapse items={collapseItems} activeKey={openKeys} onChange={setOpenKeys}
        expandIconPosition="start" size="small"
        style={{ background: 'transparent', borderRadius: 10 }} />
    </div>
  )
}

export default function MCPTools() {
  const { projectId } = useParams()
  const mcpUrl = `http://${window.location.hostname}:18800/mcp/`
  const [apiKeys, setApiKeys] = useState([])
  const [tools, setTools] = useState([])
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [newKeyName, setNewKeyName] = useState('')
  const [newKeyResult, setNewKeyResult] = useState(null)
  const [creating, setCreating] = useState(false)
  const [profiles, setProfiles] = useState([])
  const [scope, setScope] = useState(null)          // 本项目的工具范围
  const [savingScope, setSavingScope] = useState(false)

  useEffect(() => {
    if (!projectId) return
    fetchKeys(); fetchTools(); fetchProfiles(); fetchScope()
  }, [projectId])
  const fetchKeys = async () => { try { setApiKeys((await api.get('/mcp-keys')).data || []) } catch { /* 拦截器已弹错，这里不重复报 */ } }
  // 工具目录来自后端注册表，不再前端硬编码（曾经写死 20 条、后端实际 32 条）
  const fetchTools = async () => { try { setTools((await api.get('/mcp-keys/tools')).data || []) } catch { /* 同上 */ } }
  const fetchProfiles = async () => {
    try {
      const d = (await api.get('/mcp-keys/profiles')).data || {}
      setProfiles(d.profiles || [])
    } catch { /* 拉不到就只剩工具列表，不至于开天窗 */ }
  }
  const fetchScope = async () => {
    try { setScope((await api.get(`/projects/${projectId}/mcp-scope`)).data) } catch { /* 同上 */ }
  }

  const byCategory = useMemo(() => {
    const m = new Map()
    tools.forEach(t => { if (!m.has(t.category)) m.set(t.category, []); m.get(t.category).push(t) })
    return [...m.entries()]
  }, [tools])

  // 本项目的 Key 和「还没归属项目」的旧 Key 分开列 —— 后者不受项目范围管，
  // 混在一起会让人以为改了范围它也跟着变了。
  const projectKeys = apiKeys.filter(k => k.projectId === projectId)
  const orphanKeys = apiKeys.filter(k => !k.projectId)

  const saveScope = async (toolNames) => {
    setSavingScope(true)
    try {
      // 档位只是快捷方式，落库存展开后的显式工具名（不存档位名，
      // 否则日后改了档位定义，已有项目的范围会悄悄变）。全量档存 null。
      await api.put(`/projects/${projectId}/mcp-scope`,
        toolNames ? { allowedTools: toolNames } : { resetTools: true })
      message.success(`已保存，本项目 ${scope?.keyCount ?? 0} 把 Key 立即生效`)
      await fetchScope()
    } catch (e) { message.error(e.message || '保存失败') } finally { setSavingScope(false) }
  }

  const createKey = async () => {
    setCreating(true)
    try {
      // 不再在这里选范围 —— 范围跟项目走，Key 只是一把钥匙
      const body = { name: newKeyName || 'default', projectId }
      setNewKeyResult((await api.post('/mcp-keys', body)).data)
      setNewKeyName(''); fetchKeys(); fetchScope()
    }
    catch (e) { message.error(e.message || '创建失败') } finally { setCreating(false) }
  }
  const revokeKey = async (id) => { try { await api.delete(`/mcp-keys/${id}`); message.success('已吊销'); fetchKeys(); fetchScope() } catch { message.error('吊销失败') } }

  const adoptKey = async (id) => {
    try {
      await api.patch(`/mcp-keys/${id}`, { projectId })
      message.success('已归到本项目，范围改由项目决定')
      fetchKeys(); fetchScope()
    } catch (e) { message.error(e.message || '操作失败') }
  }

  const copy = (text) => copyToClipboard(text).then(() => message.success('已复制'))

  const onlineCount = projectKeys.filter(k => k.lastUsedAt && Date.now() - new Date(k.lastUsedAt).getTime() < 30 * 60 * 1000).length
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
              <Text type="secondary" style={{ fontSize: 12 }}>{onlineCount}/{projectKeys.length} 在线</Text>
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
                <Text type="secondary" style={{ fontSize: 13 }}>
                  每个 Claude Code 用独立 API Key 连接。<b>工具范围不在这里选</b> ——
                  它是项目级的，去「工具范围」页签改一次，本项目所有 Key 都生效。
                </Text>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => { setCreateModalOpen(true); setNewKeyResult(null); setNewKeyName('') }}>创建 Key</Button>
              </div>

              {projectKeys.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {projectKeys.map(k => {
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
                                <Tooltip title={scope?.allowedTools
                                  ? `跟随本项目的工具范围：${scope.allowedTools.length} 个工具，范围外的看不到也调不了。改范围去「工具范围」页签。`
                                  : '本项目未限制范围，可使用全部工具'}>
                                  <Tag color={scope?.allowedTools ? 'processing' : 'default'} style={{ fontSize: 10, lineHeight: '16px', padding: '0 6px', margin: 0 }}>
                                    {scope?.allowedTools ? `${scope.allowedTools.length}/${tools.length} 工具` : '全部工具'}
                                  </Tag>
                                </Tooltip>
                              </div>
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                {lastUsed ? `最近调用 ${lastUsed.toLocaleString('zh-CN')}` : '尚未使用'}
                              </Text>
                            </div>
                          </div>
                          <Space size={4}>
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
                  <div style={{ fontSize: 14 }}>本项目还没有连接</div>
                  <div style={{ fontSize: 12, marginTop: 4 }}>点击「创建 Key」添加 Claude Code 连接</div>
                </div>
              )}

              {/* 范围挪到项目级之前建的 Key 没有归属项目。它们**不受本项目范围管**，
                  单独列出来说清楚 —— 混进上面那一堆里，人会以为改了范围它们也跟着变。
                  不自动认领：猜错项目等于静默改权限。 */}
              {orphanKeys.length > 0 && (
                <div style={{ marginTop: 26 }}>
                  <div style={{ ...sectionTitle }}>未归属项目的 Key（{orphanKeys.length}）</div>
                  <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 10 }}>
                    这些是范围改成项目级之前建的，<b>不受本项目的工具范围管</b>，仍按它自己那份旧范围跑。
                    归到本项目后就跟着项目范围走。
                  </Text>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {orphanKeys.map(k => (
                      <Card key={k.id} size="small" style={{ ...cardStyle, borderLeft: '3px solid #e8e8e8' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                          <Space size={10}>
                            <span style={{ fontSize: 13, fontWeight: 600, color: '#2e3138' }}>{k.name}</span>
                            <Text code style={{ fontSize: 11, color: '#8c919e' }}>{k.prefix}...</Text>
                            <Tag color={k.allowedTools ? 'processing' : 'default'} style={{ fontSize: 10, lineHeight: '16px', padding: '0 6px', margin: 0 }}>
                              {k.allowedTools ? `旧范围 ${k.allowedTools.length}/${tools.length}` : '全部工具'}
                            </Tag>
                          </Space>
                          <Space size={4}>
                            <Popconfirm title="归到本项目后，它的范围立刻改由本项目决定" onConfirm={() => adoptKey(k.id)} okText="归属" cancelText="取消">
                              <Button size="small" type="text">归到本项目</Button>
                            </Popconfirm>
                            <Popconfirm title="吊销后该连接立即失效" onConfirm={() => revokeKey(k.id)} okText="吊销" cancelText="取消" okButtonProps={{ danger: true }}>
                              <Button size="small" danger type="text" icon={<DeleteOutlined />}>吊销</Button>
                            </Popconfirm>
                          </Space>
                        </div>
                      </Card>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ),
        },
        {
          key: 'tools',
          label: <span><ThunderboltOutlined /> 工具范围 ({scope?.allowedTools ? `${scope.allowedTools.length}/${tools.length}` : tools.length})</span>,
          children: (
            <ScopePanel
              key={scope ? (scope.allowedTools ? scope.allowedTools.join(',') : 'unlimited') : 'loading'}
              tools={tools} byCategory={byCategory} profiles={profiles}
              scope={scope} keyCount={scope?.keyCount ?? projectKeys.length}
              saving={savingScope} onSave={saveScope} />
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

            {/* 这里不再选范围。范围是项目级的，一把 Key 只是一把钥匙 ——
                原来把"设权限"和"发钥匙"绑在一起，于是每换一次范围就多出一把 Key。 */}
            <div style={{ marginTop: 16, fontSize: 12.5, color: '#4e5969', background: 'rgba(14,165,160,0.06)',
              border: '1px solid rgba(14,165,160,0.18)', borderRadius: 10, padding: '8px 12px', lineHeight: 1.8 }}>
              它的工具范围<b>跟随本项目</b>
              {scope?.allowedTools ? `（当前 ${scope.allowedTools.length}/${tools.length} 个工具）` : '（当前不限制）'}
              ，不用在这里选。要改去「工具范围」页签，改一次本项目所有 Key 都生效。
            </div>
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
            {scope?.allowedTools && (
              <Alert style={{ marginTop: 12 }} type="info" showIcon
                message={`该连接跟随本项目的工具范围：${scope.allowedTools.length} 个工具`}
                description="范围外的工具不会出现在它的工具列表里，直接调用也会被拒绝。改范围去「工具范围」页签。" />
            )}
          </div>
        )}
      </Modal>

    </div>
  )
}
