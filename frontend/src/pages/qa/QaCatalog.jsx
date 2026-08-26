import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Card, Table, Tag, Space, Button, Input, Select, Alert, message, Tooltip,
  Progress, Modal, Form, Collapse, Popconfirm, Popover, Checkbox,
} from 'antd'
import {
  ReloadOutlined, SearchOutlined, BugOutlined, FileTextOutlined, SettingOutlined,
  InfoCircleOutlined, CheckCircleFilled, WarningFilled, CloseCircleOutlined,
} from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import { api } from '../../utils/request'

const C = {
  red: '#e8453c', orange: '#ff7d00', teal: '#0ea5a0',
  gray: '#86909c', faint: '#c9cdd4', ink: '#1d2129', line: '#e5e6eb',
}

const STATE_TAG = {
  covered: { text: '✅ 已覆盖', color: C.teal, bg: 'rgba(14,165,160,0.1)' },
  gap: { text: '⬜ 待补', color: C.orange, bg: 'rgba(255,125,0,0.1)' },
  deprecated: { text: '❌ 已废弃', color: C.gray, bg: 'rgba(0,0,0,0.03)' },
}

const PRIORITY_COLOR = { P0: C.red, P1: C.orange, P2: C.teal, P3: C.gray }

// 口径全部抄自 QA 清单自己的「列的含义」一节 —— 平台不另立一套说法，
// 否则同一个词在两边意思不一样，比不解释更坏。
const TIER = {
  smoke: { text: '冒烟', desc: '闸门 1。这一层红了，后面所有闸门的红都是噪音' },
  api: { text: '单点契约', desc: '单个接口的请求/响应契约' },
  scenario: { text: '跨面全链', desc: '跨多个接口的完整业务链路' },
  ui: { text: '浏览器旅程', desc: '真浏览器里的用户旅程' },
}
const tierText = t => TIER[t]?.text || t || '—'

const HIGH_RISK = 6      // 与后端 qa_catalog.HIGH_RISK 同口径
const URGENT_RISK = 9

const riskColor = r => (r >= URGENT_RISK ? C.red : r >= HIGH_RISK ? C.orange : C.gray)

// 清单里一半的场景描述带 `反引号` 和 **加粗**，原样打出来是满屏符号
const RICH_RE = /(`[^`]+`|\*\*[^*]+\*\*)/g
function Rich({ text }) {
  if (!text) return null
  return String(text).split(RICH_RE).filter(Boolean).map((p, i) => {
    if (p.length > 2 && p.startsWith('`') && p.endsWith('`')) {
      return (
        <code key={i} style={{
          fontFamily: 'ui-monospace, monospace', fontSize: 12, padding: '0 4px',
          background: 'rgba(0,0,0,0.04)', borderRadius: 3, color: '#476582',
        }}>{p.slice(1, -1)}</code>
      )
    }
    if (p.length > 4 && p.startsWith('**') && p.endsWith('**')) {
      return <strong key={i}>{p.slice(2, -2)}</strong>
    }
    return <span key={i}>{p}</span>
  })
}

function Panel({ title, extra, children, tone }) {
  const border = tone === 'bad' ? 'rgba(232,69,60,0.35)' : tone === 'warn' ? 'rgba(255,125,0,0.35)' : undefined
  return (
    <Card
      size="small" style={{ flex: 1, minWidth: 300, borderColor: border }}
      styles={{ body: { padding: '12px 16px' } }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: C.ink }}>{title}</span>
        {extra}
      </div>
      {children}
    </Card>
  )
}

// 看板上的每一行都能点 —— 看到一个数字，下一步动作永远是"给我看这些条"
function Hit({ onClick, active, children, style }) {
  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 8, cursor: onClick ? 'pointer' : 'default',
        padding: '2px 6px', margin: '0 -6px', borderRadius: 4, fontSize: 12,
        background: active ? 'rgba(14,165,160,0.1)' : 'transparent', ...style,
      }}
      onMouseEnter={e => { if (onClick && !active) e.currentTarget.style.background = 'rgba(0,0,0,0.03)' }}
      onMouseLeave={e => { if (onClick && !active) e.currentTarget.style.background = 'transparent' }}
    >
      {children}
    </div>
  )
}

const LEGEND = (
  <div style={{ maxWidth: 460, fontSize: 12, lineHeight: 1.9 }}>
    <div><b>优先级 P</b> — 先做哪个。P0 最高，按业务影响 → 核心旅程 → 使用频率判定。</div>
    <div><b>风险 R</b> — 要不要缓解。<b>概率(1–3) × 影响(1–3)，取值 1–9</b>。</div>
    <div style={{ color: C.gray, paddingLeft: 12 }}>
      P 和 R 是两条独立的轴，不许互相推导。P2 的场景评出 R≥6，
      是「回去重新审优先级」的信号，不是自动升 P0。
    </div>
    <div><b>执行层</b> — {Object.entries(TIER).map(([k, v]) => `${k} ${v.text}`).join(' · ')}</div>
    <div><b>状态</b> — ✅ 清单标了已有用例 · ⬜ 待补 · ❌ 已废弃（ID 保留不复用）</div>
    <div style={{ marginTop: 6, color: C.gray }}>
      「已覆盖」只代表<b>有脚本声明了这个场景 ID</b>，不代表这条跑过、更不代表跑绿了；
      挂着 @known-bug 的就是明知道红的。口径来自 QA 清单的「列的含义」一节。
    </div>
  </div>
)

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
  const [quick, setQuick] = useState()          // 看板点出来的那一类：urgent/bugs/lying/mismatch
  const [showDeprecated, setShowDeprecated] = useState(false)
  const [sorter, setSorter] = useState({})
  const [page, setPage] = useState(1)
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
  const repo = data?.repo

  const tiers = useMemo(
    () => [...new Set(scenarios.map(s => s.tier).filter(Boolean))].sort(),
    [scenarios],
  )

  const QUICK = useMemo(() => ({
    urgent: { label: 'P0 待补 · 风险 9', test: s => s.state === 'gap' && s.priority === 'P0' && (s.risk || 0) >= URGENT_RISK },
    bugs: { label: '挂着已知缺陷', test: s => (s.knownBugs || []).length > 0 },
    lying: { label: '标了 ✅ 却没有脚本', test: s => s.claimedButUncovered },
    mismatch: { label: `风险 ≥${HIGH_RISK} 但优先级 P2/P3`, test: s => s.state !== 'deprecated' && (s.risk || 0) >= HIGH_RISK && ['P2', 'P3'].includes(s.priority) },
  }), [])

  const hasFilter = keyword || domain || priority || tier || state || quick
  const clearFilters = () => {
    setKeyword(''); setDomain(); setPriority(); setTier(); setState(); setQuick()
  }
  // 从看板跳过来时，别让上一次的筛选残留在里面把结果减成空的
  const jump = (patch) => {
    clearFilters()
    setDomain(patch.domain); setPriority(patch.priority); setState(patch.state); setQuick(patch.quick)
    if (patch.sortRisk) setSorter({ columnKey: 'risk', order: 'descend' })
    if (patch.showDeprecated) setShowDeprecated(true)
  }

  useEffect(() => { setPage(1) }, [keyword, domain, priority, tier, state, quick, showDeprecated])

  const filtered = useMemo(() => scenarios.filter(s => {
    if (!showDeprecated && s.state === 'deprecated' && state !== 'deprecated') return false
    if (domain && s.domain !== domain) return false
    if (priority && s.priority !== priority) return false
    if (tier && s.tier !== tier) return false
    if (state && s.state !== state) return false
    if (quick && !QUICK[quick].test(s)) return false
    if (keyword) {
      const k = keyword.toLowerCase()
      const hit = s.id.toLowerCase().includes(k)
        || (s.title || '').toLowerCase().includes(k)
        || (s.scripts || []).some(x => x.path.toLowerCase().includes(k))
      if (!hit) return false
    }
    return true
  }), [scenarios, domain, priority, tier, state, quick, keyword, showDeprecated, QUICK])

  const urgentCount = useMemo(
    () => scenarios.filter(QUICK.urgent.test).length, [scenarios, QUICK])

  // 缺口最多的域排前面 —— 「黑洞域」自己浮上来，不用人去 24 个域里翻
  const domainRows = useMemo(
    () => [...(data?.domains || [])].sort((a, b) => (b.gap - a.gap) || (b.p0Gap - a.p0Gap) || a.code.localeCompare(b.code)),
    [data],
  )

  const sortOrderOf = key => (sorter.columnKey === key ? sorter.order : null)

  const columns = [
    {
      title: 'ID', dataIndex: 'id', width: 92, fixed: 'left',
      render: (v, r) => (
        <Tooltip title={r.domainName ? `${r.domain} — ${r.domainName}` : r.domain}>
          <span style={{ fontFamily: 'ui-monospace, monospace', fontWeight: 600 }}>{v}</span>
        </Tooltip>
      ),
    },
    {
      title: '场景（这条要证明什么）', dataIndex: 'title',
      render: (v, r) => {
        // 「已废弃」「@known-bug GL#530」这类备注在别的列已经写着了，别重复占地方
        const note = r.stateNote && !/^@known-bug/.test(r.stateNote) && r.stateNote !== '已废弃'
          ? r.stateNote : null
        return (
          <div>
            <div style={{ lineHeight: 1.6 }}>
              <Rich text={v} />
              {r.claimedButUncovered && (
                <Tooltip title="清单标了 ✅ 但仓库里没有任何脚本声明这个 ID —— QA 自己的 check-coverage.sh 管这叫「抓清单说谎」，会 BLOCK">
                  <Tag style={{ marginLeft: 6 }} color="warning">清单未对上</Tag>
                </Tooltip>
              )}
            </div>
            {note && <div style={{ fontSize: 11, color: C.gray, marginTop: 2 }}>{note}</div>}
          </div>
        )
      },
    },
    {
      title: <Tooltip title="先做哪个。P0 最高">优先级</Tooltip>,
      dataIndex: 'priority', width: 88, align: 'center',
      sorter: (a, b) => (a.priority || 'P9').localeCompare(b.priority || 'P9'),
      sortOrder: sortOrderOf('priority'), key: 'priority',
      render: v => v
        ? <Tag style={{ margin: 0, color: PRIORITY_COLOR[v] || C.gray, background: 'transparent', borderColor: PRIORITY_COLOR[v] || C.line }}>{v}</Tag>
        : '—',
    },
    {
      title: <Tooltip title="风险分 = 概率(1–3) × 影响(1–3)，取值 1–9。决定要不要缓解，和优先级是两条独立的轴">
        <span>风险 <InfoCircleOutlined style={{ fontSize: 11, color: C.faint }} /></span>
      </Tooltip>,
      dataIndex: 'risk', width: 86, align: 'center',
      sorter: (a, b) => (a.risk || 0) - (b.risk || 0),
      sortOrder: sortOrderOf('risk'), key: 'risk',
      render: v => v == null ? '—' : (
        <span style={{
          display: 'inline-block', minWidth: 22, padding: '1px 6px', borderRadius: 4, fontSize: 12,
          color: riskColor(v), background: v >= HIGH_RISK ? `${riskColor(v)}14` : 'transparent',
          fontWeight: v >= HIGH_RISK ? 600 : 400,
        }}>{v}</span>
      ),
    },
    {
      title: <Tooltip title={Object.entries(TIER).map(([k, t]) => `${k}=${t.text}`).join(' · ')}>执行层</Tooltip>,
      dataIndex: 'tier', width: 100,
      render: v => v ? <Tooltip title={`${v} — ${TIER[v]?.desc || ''}`}><Tag style={{ margin: 0 }}>{tierText(v)}</Tag></Tooltip> : '—',
    },
    {
      title: '状态', dataIndex: 'state', width: 150,
      render: (v, r) => {
        const t = STATE_TAG[v] || STATE_TAG.gap
        return (
          <Space size={4} wrap={false}>
            <span style={{ color: t.color, background: t.bg, padding: '2px 8px', borderRadius: 10, fontSize: 12, whiteSpace: 'nowrap' }}>{t.text}</span>
            {r.knownBugs?.length > 0 && (
              <Tooltip title="有脚本，但脚本头上挂着 @known-bug —— 跑得通，结论是红的">
                <Tag color="error" style={{ margin: 0 }}>带缺陷</Tag>
              </Tooltip>
            )}
          </Space>
        )
      },
    },
    {
      title: '覆盖脚本', dataIndex: 'scripts', width: 240,
      render: (list) => !list?.length ? <span style={{ color: C.faint }}>—</span> : (
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          {list.map(s => (
            <Tooltip key={s.path} title={s.path}>
              <span style={{ fontSize: 12, fontFamily: 'ui-monospace, monospace', color: s.primary ? C.ink : C.gray }}>
                <FileTextOutlined style={{ marginRight: 4, color: C.faint }} />
                {s.path.split('/').pop()}
              </span>
            </Tooltip>
          ))}
        </Space>
      ),
    },
    {
      title: '已知缺陷', dataIndex: 'knownBugs', width: 210,
      render: (list) => !list?.length ? <span style={{ color: C.faint }}>—</span> : (
        <Space direction="vertical" size={2}>
          {list.map((b, i) => (
            <Tooltip key={i} title={b}>
              <Tag icon={<BugOutlined />} color="error" style={{ maxWidth: 190, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {b.split(/\s+/)[0]}
              </Tag>
            </Tooltip>
          ))}
        </Space>
      ),
    },
  ]

  const coverRate = summary?.total ? Math.round((summary.covered / summary.total) * 100) : 0
  const healthy = summary && !summary.claimedButUncovered && !summary.orphanScripts

  const sourceDetail = repo && (
    <div style={{ maxWidth: 480, fontSize: 12, lineHeight: 2 }}>
      <div>仓库 <code>{repo.url}</code></div>
      <div>分支 <code>{repo.branch}</code>{repo.branchAuto && <Tag color="blue" style={{ marginLeft: 4 }}>跟默认分支</Tag>}</div>
      <div>清单 <code>{repo.catalogPath}</code>
        <Tag color={repo.catalogAuto ? 'blue' : 'default'} style={{ marginLeft: 4 }}>{repo.catalogAuto ? '自动识别' : '配置指定'}</Tag>
      </div>
      <div>脚本 {summary?.scripts ?? 0} 个
        <Tag color={repo.caseDiscovery === 'grep' ? 'blue' : 'default'} style={{ marginLeft: 4 }}>
          {repo.caseDiscovery === 'grep' ? '按 @scenario 自动捞' : '按配置的 glob'}
        </Tag>
      </div>
      <div>commit <code>{repo.commitShort}</code> {repo.commitSubject}</div>
      <div style={{ color: C.gray, marginTop: 6 }}>
        平台对这个仓库只读：clone --bare / fetch / git show，不写一个字。
      </div>
    </div>
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 20, fontWeight: 600, color: C.ink, margin: 0 }}>QA 场景清单</h2>
          <div style={{ fontSize: 12, color: C.gray, marginTop: 2 }}>
            QA 维护的验收场景分母 + 仓库里真实存在的脚本分子，两边对照着看。平台只读，不回写。
          </div>
        </div>
        <Space>
          <Popover content={LEGEND} title="这一页的列都是什么意思" placement="bottomRight">
            <Button icon={<InfoCircleOutlined />} type="text">怎么读这一页</Button>
          </Popover>
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
        <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexWrap: 'wrap', alignItems: 'stretch' }}>

          {/* 1. 覆盖到哪了 —— 并且说清「已覆盖」不等于「跑绿了」 */}
          <Panel
            title="覆盖到哪了"
            extra={<span style={{ fontSize: 11, color: C.gray }}>不含 {summary.deprecated} 条已废弃</span>}
          >
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 30, fontWeight: 600, color: C.teal, lineHeight: 1 }}>{coverRate}%</span>
              <span style={{ fontSize: 12, color: C.gray }}>
                {summary.covered} / {summary.total} 条清单里有脚本认领
              </span>
            </div>
            {['P0', 'P1', 'P2', 'P3'].filter(p => summary.byPriority?.[p]).map(p => {
              const s = summary.byPriority[p]
              return (
                <Hit key={p} active={priority === p && !state && !quick} onClick={() => jump({ priority: p })}>
                  <span style={{ width: 22, color: PRIORITY_COLOR[p] }}>{p}</span>
                  <Progress
                    percent={s.total ? Math.round((s.covered / s.total) * 100) : 0}
                    size="small" strokeColor={PRIORITY_COLOR[p]} style={{ flex: 1, margin: 0 }} showInfo={false}
                  />
                  <span style={{ color: C.gray, width: 60, textAlign: 'right' }}>{s.covered}/{s.total}</span>
                </Hit>
              )
            })}
            {summary.coveredWithBugs > 0 && (
              <Hit
                active={quick === 'bugs'} onClick={() => jump({ quick: 'bugs' })}
                style={{ marginTop: 8, color: C.red }}
              >
                <WarningFilled />
                <span>
                  其中 <b>{summary.coveredWithBugs}</b> 条挂着已知缺陷 ——
                  有脚本，但结论已知是红的
                </span>
              </Hit>
            )}
          </Panel>

          {/* 2. 还欠多少 —— 一个数字要能直接变成明天的活儿 */}
          <Panel title="还欠多少" extra={<span style={{ fontSize: 11, color: C.gray }}>清单标 ⬜ 待补</span>}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 30, fontWeight: 600, color: C.orange, lineHeight: 1 }}>{summary.gap}</span>
              <span style={{ fontSize: 12, color: C.gray }}>条场景还没有任何脚本</span>
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
              {['P0', 'P1', 'P2', 'P3'].filter(p => summary.byPriority?.[p]?.gap).map(p => (
                <Tag
                  key={p} onClick={() => jump({ priority: p, state: 'gap', sortRisk: true })}
                  color={priority === p && state === 'gap' ? PRIORITY_COLOR[p] : undefined}
                  style={{ cursor: 'pointer', margin: 0, borderColor: PRIORITY_COLOR[p] }}
                >
                  {p} 缺 {summary.byPriority[p].gap}
                </Tag>
              ))}
            </div>
            <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 8 }}>
              <div style={{ fontSize: 11, color: C.gray, marginBottom: 4 }}>
                要挑一批今天就动手的，就挑这批：
              </div>
              <Button
                size="small" danger={urgentCount > 0} type={quick === 'urgent' ? 'primary' : 'default'}
                onClick={() => jump({ quick: 'urgent', sortRisk: true })} disabled={!urgentCount}
              >
                P0 待补 · 风险 9 —— {urgentCount} 条
              </Button>
            </div>
          </Panel>

          {/* 3. 清单可信吗 —— 前两项是 QA 自己门禁会 BLOCK 的，不该埋在页面底部 */}
          <Panel
            title="清单可信吗"
            tone={healthy ? undefined : 'bad'}
            extra={healthy
              ? <span style={{ fontSize: 11, color: C.teal }}><CheckCircleFilled /> 清单和脚本对得上</span>
              : <span style={{ fontSize: 11, color: C.red }}><WarningFilled /> 有对不上的</span>}
          >
            <Hit active={quick === 'lying'} onClick={() => summary.claimedButUncovered && jump({ quick: 'lying' })}>
              {summary.claimedButUncovered
                ? <WarningFilled style={{ color: C.red }} />
                : <CheckCircleFilled style={{ color: C.teal }} />}
              <span style={{ flex: 1 }}>标了 ✅ 却没有任何脚本</span>
              <b style={{ color: summary.claimedButUncovered ? C.red : C.gray }}>{summary.claimedButUncovered}</b>
            </Hit>
            <Hit>
              {summary.orphanScripts
                ? <WarningFilled style={{ color: C.red }} />
                : <CheckCircleFilled style={{ color: C.teal }} />}
              <span style={{ flex: 1 }}>脚本声明了清单外的 ID</span>
              <b style={{ color: summary.orphanScripts ? C.red : C.gray }}>{summary.orphanScripts}</b>
            </Hit>
            <Hit active={quick === 'mismatch'} onClick={() => summary.riskMismatch && jump({ quick: 'mismatch' })}>
              {summary.riskMismatch
                ? <WarningFilled style={{ color: C.orange }} />
                : <CheckCircleFilled style={{ color: C.teal }} />}
              <span style={{ flex: 1 }}>风险 ≥{HIGH_RISK} 却排在 P2/P3</span>
              <b style={{ color: summary.riskMismatch ? C.orange : C.gray }}>{summary.riskMismatch}</b>
            </Hit>
            <div style={{ fontSize: 11, color: C.gray, marginTop: 8, lineHeight: 1.6 }}>
              前两项是 QA 自己门禁（<code>check-coverage.sh</code>）会直接 BLOCK 的；
              第三项是「回去重新审优先级」的信号，不阻断。
            </div>
          </Panel>
        </div>
      )}

      {/* 按域看：24 个域一屏看完，缺口多的自己浮到前面 */}
      {configured && domainRows.length > 0 && (
        <Collapse
          size="small" defaultActiveKey={['d']} style={{ marginBottom: 12 }}
          items={[{
            key: 'd',
            label: <span style={{ fontSize: 13 }}>
              按域看缺口（{domainRows.length} 个域 · 缺得多的排前面 · 点一行筛这个域）
            </span>,
            children: (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(400px, 1fr))', gap: '2px 24px' }}>
                {domainRows.map(d => (
                  <Hit key={d.code} active={domain === d.code} onClick={() => jump({ domain: domain === d.code ? undefined : d.code })}>
                    <span style={{ fontFamily: 'ui-monospace, monospace', fontWeight: 600, width: 40 }}>{d.code}</span>
                    <span style={{ width: 110, color: C.gray, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
                    <Progress
                      percent={d.total ? Math.round((d.covered / d.total) * 100) : 0}
                      size="small" showInfo={false} strokeColor={C.teal} style={{ flex: 1, margin: 0, minWidth: 60 }}
                    />
                    <span style={{ width: 52, textAlign: 'right', color: C.gray }}>{d.covered}/{d.total}</span>
                    <span style={{ width: 96, textAlign: 'right', color: d.gap ? C.orange : C.faint }}>
                      缺 {d.gap}{d.p0Gap ? <b style={{ color: C.red }}> · P0 {d.p0Gap}</b> : null}
                    </span>
                  </Hit>
                ))}
              </div>
            ),
          }]}
        />
      )}

      <Card styles={{ body: { padding: 16 } }}>
        <Space wrap style={{ marginBottom: 12 }}>
          <Input
            placeholder="搜索 ID / 场景 / 脚本路径" prefix={<SearchOutlined />} allowClear
            value={keyword} onChange={e => setKeyword(e.target.value)} style={{ width: 240 }}
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
          <Select placeholder="执行层" allowClear value={tier} onChange={setTier} style={{ width: 140 }}
            options={tiers.map(t => ({ value: t, label: `${tierText(t)}（${t}）` }))} />
          <Select placeholder="状态" allowClear value={state} onChange={setState} style={{ width: 130 }}
            options={[
              { value: 'covered', label: '✅ 已覆盖' },
              { value: 'gap', label: '⬜ 待补' },
              { value: 'deprecated', label: '❌ 已废弃' },
            ]} />
          {quick && (
            <Tag color="processing" closable onClose={() => setQuick()} style={{ margin: 0 }}>
              {QUICK[quick].label}
            </Tag>
          )}
          {hasFilter && (
            <Button size="small" type="text" icon={<CloseCircleOutlined />} onClick={clearFilters}>清除筛选</Button>
          )}
          {summary?.deprecated > 0 && (
            <Checkbox checked={showDeprecated} onChange={e => setShowDeprecated(e.target.checked)}>
              <span style={{ fontSize: 12, color: C.gray }}>显示已废弃（{summary.deprecated}）</span>
            </Checkbox>
          )}
          <span style={{ fontSize: 12, color: C.gray }}>共 {filtered.length} 条</span>
        </Space>

        <Table
          rowKey="id"
          columns={columns}
          dataSource={filtered}
          loading={loading}
          size="small"
          scroll={{ x: 1180 }}
          onChange={(_p, _f, s) => setSorter({ columnKey: s?.columnKey, order: s?.order })}
          pagination={{
            current: page, onChange: setPage,
            pageSize: 50, showSizeChanger: true, showTotal: t => `共 ${t} 条`,
          }}
        />
      </Card>

      {configured && data?.orphanScriptList?.length > 0 && (
        <Card
          title="声明了清单外 ID 的脚本"
          size="small"
          style={{ marginTop: 16 }}
          extra={<span style={{ fontSize: 12, color: C.gray }}>
            脚本声明的场景 ID 在清单里查无此条 —— 要么清单漏登记，要么脚本抄错了 ID
          </span>}
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

      {/* 数据来源是运维信息，一天看一次都嫌多 —— 压到页脚一行，细节收进 Popover */}
      {configured && repo?.commitShort && (
        <div style={{ fontSize: 12, color: C.gray, marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
          <span>数据来源</span>
          <Popover content={sourceDetail} title="QA 仓（只读）" placement="topLeft">
            <span style={{ cursor: 'pointer', textDecoration: 'underline dotted' }}>
              {repo.url} · {repo.branch} · <code>{repo.commitShort}</code>
            </span>
          </Popover>
          {repo.commitDate && <span>拉取于 {new Date(repo.commitDate).toLocaleString('zh-CN')}</span>}
        </div>
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
