import { useState, useEffect, useCallback, useMemo } from 'react'
import { Card, Table, Tag, Space, Button, Input, Select, Alert, message, Tooltip, Progress, Modal, Form, Collapse, Popconfirm } from 'antd'
import { ReloadOutlined, SearchOutlined, BugOutlined, FileTextOutlined, SettingOutlined } from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import { api } from '../../utils/request'

const STATE_TAG = {
  covered: { text: '✅ 已覆盖', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  gap: { text: '⬜ 待补', color: '#ff7d00', bg: 'rgba(255,125,0,0.1)' },
  deprecated: { text: '❌ 已废弃', color: '#86909c', bg: 'rgba(0,0,0,0.03)' },
}

const PRIORITY_COLOR = { P0: '#e8453c', P1: '#ff7d00', P2: '#0ea5a0', P3: '#86909c' }

function StatCard({ label, value, sub, color }) {
  return (
    <Card size="small" style={{ flex: 1, minWidth: 120 }} styles={{ body: { padding: '12px 16px' } }}>
      <div style={{ fontSize: 12, color: '#86909c' }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 600, color: color || '#1d2129', lineHeight: 1.3 }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: '#c9cdd4' }}>{sub}</div>}
    </Card>
  )
}

export default function QaCatalog() {
  const { projectId } = useParams()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [domain, setDomain] = useState()
  const [priority, setPriority] = useState()
  const [tier, setTier] = useState()
  const [state, setState] = useState()
  const [cfgOpen, setCfgOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get(`/projects/${projectId}/qa-catalog`)
      setData(res.data)
    } catch { /* request.js 已展示错误 */ } finally { setLoading(false) }
  }, [projectId])

  useEffect(() => { fetchData() }, [fetchData])

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      const res = await api.post(`/projects/${projectId}/qa-catalog/refresh`)
      setData(res.data)
      if (res.data?.error) message.warning(res.data.error)
      else message.success('已从 QA 仓拉取最新清单')
    } catch { /* request.js 已展示错误 */ } finally { setRefreshing(false) }
  }

  const openConfig = () => {
    const c = data?.config || {}
    form.setFieldsValue({
      url: c.url || '',
      branch: c.branch || '',
      catalogPath: c.catalogPath || '',
      caseGlobs: (c.caseGlobs || []).join(', '),
    })
    setCfgOpen(true)
  }

  // 保存后端会顺手按新配置读一遍（自动识别认没认出来，当场就能看见）
  const saveConfig = async (payload) => {
    setSaving(true)
    try {
      const res = await api.put(`/projects/${projectId}/qa-catalog/config`, payload)
      setData(res.data)
      setCfgOpen(false)
      if (!payload.url) message.success('已取消 QA 仓配置')
      else if (res.data?.error) message.warning(res.data.error)
      else message.success('已保存并读取 QA 仓')
    } catch { /* request.js 已展示错误 */ } finally { setSaving(false) }
  }

  const handleSaveConfig = async () => {
    let v
    try { v = await form.validateFields() } catch { return }
    await saveConfig({
      url: (v.url || '').trim(),
      branch: (v.branch || '').trim(),
      catalogPath: (v.catalogPath || '').trim(),
      caseGlobs: (v.caseGlobs || '').split(',').map(x => x.trim()).filter(Boolean),
    })
  }

  const configured = data?.configured
  const scenarios = useMemo(() => data?.scenarios || [], [data])
  const summary = data?.summary

  const tiers = useMemo(
    () => [...new Set(scenarios.map(s => s.tier).filter(Boolean))].sort(),
    [scenarios],
  )

  const filtered = useMemo(() => scenarios.filter(s => {
    if (domain && s.domain !== domain) return false
    if (priority && s.priority !== priority) return false
    if (tier && s.tier !== tier) return false
    if (state && s.state !== state) return false
    if (keyword) {
      const k = keyword.toLowerCase()
      const hit = s.id.toLowerCase().includes(k)
        || (s.title || '').toLowerCase().includes(k)
        || (s.scripts || []).some(x => x.path.toLowerCase().includes(k))
      if (!hit) return false
    }
    return true
  }), [scenarios, domain, priority, tier, state, keyword])

  const columns = [
    {
      title: 'ID', dataIndex: 'id', width: 96, fixed: 'left',
      render: (v, r) => (
        <Tooltip title={r.domainName ? `${r.domain} — ${r.domainName}` : r.domain}>
          <span style={{ fontFamily: 'ui-monospace, monospace', fontWeight: 600 }}>{v}</span>
        </Tooltip>
      ),
    },
    {
      title: '场景', dataIndex: 'title', ellipsis: true,
      render: (v, r) => (
        <span>
          {v}
          {r.claimedButUncovered && (
            <Tooltip title="清单标了 ✅ 但没有任何脚本声明这个 ID">
              <Tag style={{ marginLeft: 6 }} color="warning">清单未对上</Tag>
            </Tooltip>
          )}
        </span>
      ),
    },
    {
      title: 'P', dataIndex: 'priority', width: 60, align: 'center',
      render: v => v ? <Tag style={{ margin: 0, color: PRIORITY_COLOR[v] || '#86909c', background: 'transparent', borderColor: PRIORITY_COLOR[v] || '#e5e6eb' }}>{v}</Tag> : '—',
    },
    { title: 'R', dataIndex: 'risk', width: 50, align: 'center', render: v => v ?? '—' },
    { title: '层', dataIndex: 'tier', width: 84, render: v => v ? <Tag style={{ margin: 0 }}>{v}</Tag> : '—' },
    {
      title: '状', dataIndex: 'state', width: 96,
      render: (v, r) => {
        const t = STATE_TAG[v] || STATE_TAG.gap
        return (
          <Tooltip title={r.stateNote || undefined}>
            <span style={{ color: t.color, background: t.bg, padding: '2px 8px', borderRadius: 10, fontSize: 12, whiteSpace: 'nowrap' }}>{t.text}</span>
          </Tooltip>
        )
      },
    },
    {
      title: '覆盖脚本', dataIndex: 'scripts', width: 320,
      render: (list) => !list?.length ? <span style={{ color: '#c9cdd4' }}>—</span> : (
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          {list.map(s => (
            <span key={s.path} style={{ fontSize: 12, fontFamily: 'ui-monospace, monospace', color: s.primary ? '#1d2129' : '#86909c' }}>
              <FileTextOutlined style={{ marginRight: 4, color: '#c9cdd4' }} />
              {s.path}
            </span>
          ))}
        </Space>
      ),
    },
    {
      title: '已知缺陷', dataIndex: 'knownBugs', width: 200,
      render: (list) => !list?.length ? <span style={{ color: '#c9cdd4' }}>—</span> : (
        <Space direction="vertical" size={2}>
          {list.map((b, i) => (
            <Tooltip key={i} title={b}>
              <Tag icon={<BugOutlined />} color="error" style={{ maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {b.split(/\s+/)[0]}
              </Tag>
            </Tooltip>
          ))}
        </Space>
      ),
    },
  ]

  const coverRate = summary?.total ? Math.round((summary.covered / summary.total) * 100) : 0

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 20, fontWeight: 600, color: '#1d2129', margin: 0 }}>QA 场景清单</h2>
          <div style={{ fontSize: 12, color: '#86909c', marginTop: 2 }}>
            只读展示 QA 仓的场景清单与已规划脚本，平台不会写入该仓库
          </div>
        </div>
        <Space>
          <Button icon={<SettingOutlined />} onClick={openConfig}>{configured ? '仓库配置' : '配置 QA 仓'}</Button>
          <Button icon={<ReloadOutlined />} onClick={handleRefresh} loading={refreshing} disabled={!configured}>
            拉取最新
          </Button>
        </Space>
      </div>

      {!loading && configured === false && (
        <Alert
          type="info" showIcon style={{ marginBottom: 16 }}
          message="尚未配置 QA 仓"
          description={
            <span>
              这个项目还没有配置 QA 仓，下面只显示表头。点右上角
              <Button type="link" size="small" style={{ padding: '0 4px' }} onClick={openConfig}>配置 QA 仓</Button>
              填上仓库地址就行 —— 分支、清单路径、脚本范围都能自己认出来。
              平台对该仓库只读：只做 clone / fetch，不会写入任何内容。
            </span>
          }
        />
      )}

      {data?.error && (
        <Alert
          type="error" showIcon style={{ marginBottom: 16 }}
          message="读取 QA 仓失败" description={data.error}
          action={<Button size="small" onClick={openConfig}>改配置</Button>}
        />
      )}

      {configured && summary && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
          <StatCard label="场景总数" value={summary.total} sub={summary.deprecated ? `另有 ${summary.deprecated} 条已废弃` : null} />
          <StatCard label="已覆盖" value={summary.covered} color="#0ea5a0" sub={`覆盖率 ${coverRate}%`} />
          <StatCard label="待补缺口" value={summary.gap} color="#ff7d00" sub={summary.byPriority?.P0 ? `P0 缺口 ${summary.byPriority.P0.gap}` : null} />
          <StatCard label="用例脚本" value={summary.scripts} sub="声明了场景 ID 的文件" />
          <StatCard label="带已知缺陷" value={summary.knownBugScenarios} color={summary.knownBugScenarios ? '#e8453c' : undefined} sub="@known-bug 标记" />
          <Card size="small" style={{ flex: 2, minWidth: 240 }} styles={{ body: { padding: '12px 16px' } }}>
            <div style={{ fontSize: 12, color: '#86909c', marginBottom: 6 }}>
              分优先级覆盖
            </div>
            {['P0', 'P1', 'P2', 'P3'].filter(p => summary.byPriority?.[p]).map(p => {
              const s = summary.byPriority[p]
              return (
                <div key={p} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
                  <span style={{ width: 24, fontSize: 12, color: PRIORITY_COLOR[p] }}>{p}</span>
                  <Progress
                    percent={s.total ? Math.round((s.covered / s.total) * 100) : 0}
                    size="small" strokeColor={PRIORITY_COLOR[p]} style={{ flex: 1, margin: 0 }}
                  />
                  <span style={{ fontSize: 11, color: '#86909c', width: 56, textAlign: 'right' }}>{s.covered}/{s.total}</span>
                </div>
              )
            })}
          </Card>
        </div>
      )}

      {configured && data?.repo?.commitShort && (
        <div style={{ fontSize: 12, color: '#86909c', marginBottom: 12, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 4 }}>
          <Tag>{data.repo.url}</Tag>
          <Tag>{data.repo.branch}{data.repo.branchAuto ? ' · 默认分支' : ''}</Tag>
          <Tag style={{ fontFamily: 'ui-monospace, monospace' }}>{data.repo.commitShort}</Tag>
          <span>{data.repo.commitSubject}</span>
          {data.repo.commitDate && <span style={{ marginLeft: 8 }}>{new Date(data.repo.commitDate).toLocaleString('zh-CN')}</span>}
          {/* 自动识别的结果要看得见：认错了才知道要去「高级」里手填 */}
          <span style={{ width: '100%', marginTop: 4 }}>
            清单 <code>{data.repo.catalogPath}</code>
            <Tag style={{ marginLeft: 4 }} color={data.repo.catalogAuto ? 'blue' : 'default'}>
              {data.repo.catalogAuto ? '自动识别' : '配置指定'}
            </Tag>
            · 脚本 {summary?.scripts ?? 0} 个
            <Tag style={{ marginLeft: 4 }} color={data.repo.caseDiscovery === 'grep' ? 'blue' : 'default'}>
              {data.repo.caseDiscovery === 'grep' ? '按 @scenario 自动捞' : '按配置的 glob'}
            </Tag>
          </span>
        </div>
      )}

      <Card styles={{ body: { padding: 16 } }}>
        <Space wrap style={{ marginBottom: 12 }}>
          <Input
            placeholder="搜索 ID / 场景 / 脚本路径" prefix={<SearchOutlined />} allowClear
            value={keyword} onChange={e => setKeyword(e.target.value)} style={{ width: 260 }}
          />
          <Select
            placeholder="域" allowClear value={domain} onChange={setDomain} style={{ width: 200 }}
            options={(data?.domains || []).map(d => ({
              value: d.code,
              label: `${d.code}${d.name ? ' · ' + d.name : ''}（${d.covered}/${d.total}）`,
            }))}
          />
          <Select placeholder="优先级" allowClear value={priority} onChange={setPriority} style={{ width: 110 }}
            options={['P0', 'P1', 'P2', 'P3'].map(p => ({ value: p, label: p }))} />
          <Select placeholder="层" allowClear value={tier} onChange={setTier} style={{ width: 120 }}
            options={tiers.map(t => ({ value: t, label: t }))} />
          <Select placeholder="状态" allowClear value={state} onChange={setState} style={{ width: 130 }}
            options={[
              { value: 'covered', label: '✅ 已覆盖' },
              { value: 'gap', label: '⬜ 待补' },
              { value: 'deprecated', label: '❌ 已废弃' },
            ]} />
          <span style={{ fontSize: 12, color: '#86909c' }}>共 {filtered.length} 条</span>
        </Space>

        <Table
          rowKey="id"
          columns={columns}
          dataSource={filtered}
          loading={loading}
          size="small"
          scroll={{ x: 1180 }}
          pagination={{ pageSize: 50, showSizeChanger: true, showTotal: t => `共 ${t} 条` }}
        />
      </Card>

      {configured && data?.orphanScriptList?.length > 0 && (
        <Card
          title="声明了清单外 ID 的脚本"
          size="small"
          style={{ marginTop: 16 }}
        >
          <Table
            rowKey="path" size="small" pagination={false}
            dataSource={data.orphanScriptList}
            columns={[
              { title: '脚本', dataIndex: 'path' },
              { title: '未知 ID', dataIndex: 'ids', render: v => v.join(' ') },
            ]}
          />
        </Card>
      )}

      {/* QA 仓配置：只在这一页维护 —— 它只影响这一页，认错了也只在这一页报错 */}
      <Modal
        title="QA 仓（只读）"
        open={cfgOpen}
        onCancel={() => setCfgOpen(false)}
        width={560}
        footer={[
          data?.config?.url ? (
            <Popconfirm
              key="clear" title="取消配置后这一页只剩表头，确定？"
              onConfirm={() => saveConfig({ url: '', branch: '', catalogPath: '', caseGlobs: [] })}
            >
              <Button danger type="text" style={{ float: 'left' }}>取消配置</Button>
            </Popconfirm>
          ) : null,
          <Button key="cancel" onClick={() => setCfgOpen(false)}>取消</Button>,
          <Button key="ok" type="primary" loading={saving} onClick={handleSaveConfig}>保存</Button>,
        ]}
      >
        <Alert
          type="info" showIcon style={{ marginBottom: 16 }}
          message="平台对这个仓库永远只读"
          description="只做 clone --bare / fetch / git show，不写入、不建分支、也不要求对方仓库为我们加任何文件。"
        />
        <Form form={form} layout="vertical">
          <Form.Item
            name="url" label="仓库地址"
            rules={[{ required: true, message: '请输入 QA 仓地址' }]}
            extra="服务器要能免密访问它（SSH key / 只读 token）"
          >
            <Input placeholder="git@gitlab.example.com:qa/uag-qa.git" />
          </Form.Item>
          <Collapse
            ghost size="small"
            items={[{
              key: 'adv',
              label: <span style={{ fontSize: 13 }}>高级 · 三项都留空 = 自动识别</span>,
              children: (
                <>
                  <Form.Item name="branch" label="分支" extra="留空 = 跟仓库自己的默认分支走">
                    <Input placeholder="留空即可" />
                  </Form.Item>
                  <Form.Item name="catalogPath" label="场景清单文件" extra="留空 = 找场景行最多的那份 .md">
                    <Input placeholder="如 docs/test-scenario-catalog.md（留空即可）" />
                  </Form.Item>
                  <Form.Item
                    name="caseGlobs" label="用例脚本范围"
                    extra="留空 = 用 git grep 捞所有声明了 @scenario 的文件；填了就只认这些 glob（逗号分隔）"
                  >
                    <Input placeholder="如 api/**/*.sh, ui/tests/**/*.spec.ts（留空即可）" />
                  </Form.Item>
                </>
              ),
            }]}
          />
        </Form>
      </Modal>
    </div>
  )
}
