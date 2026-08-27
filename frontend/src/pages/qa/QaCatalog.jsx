import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Card, Table, Tag, Space, Button, Input, Select, Alert, message, Tooltip,
  Progress, Modal, Form, Collapse, Popconfirm, Popover, Checkbox, Drawer, Empty, Spin, Tabs,
} from 'antd'
import {
  ReloadOutlined, SearchOutlined, BugOutlined, FileTextOutlined, SettingOutlined,
  InfoCircleOutlined, CheckCircleFilled, WarningFilled, CloseCircleOutlined,
  RobotOutlined, LoadingOutlined, CopyOutlined, DownloadOutlined,
} from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import { api } from '../../utils/request'
import { PERM } from '../../utils/permissions'
import { usePermissions } from '../../utils/PermissionContext'

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
          fontFamily: 'var(--font-mono)', fontSize: 12, padding: '0 4px',
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

// AI 评审的结论。措辞对着「这个域的脚本撑不撑得起这个域的清单」说，
// 不用「通过/不通过」—— 这里没有门禁，说"不通过"会被当成拦了谁的活
// 词换过两轮，都是同一个毛病：**用一个词概括，读的人就得猜。**
//   第一版「靠得住 / 有水分 / 撑不住」—— "水"在哪？"撑"的是什么？
//   第二版「能信 / 信一半 / 不能信」—— 信什么？信一半是哪一半？
// 问题不在词好不好听，在于**这一栏根本不是一个形容词能装下的东西**。
// 它答的是一个很具体的问题：*脚本头写了 `@scenario X`，它到底验没验 X？*
// 所以不概括了，直接把那句话写出来。这三个短语里没有一个字要人再解释一遍。
const VERDICT = {
  ok: { text: '都验到了', color: 'success',
        why: '脚本声明覆盖的场景，读下来都真在验那件事' },
  risky: { text: '部分没验到', color: 'warning',
           why: '一部分场景脚本认领了、但没真验：断言太松，或在这个环境里压根没跑' },
  bad: { text: '多数没验到', color: 'error',
         why: '认领的那几条主要场景，多数没真验 ——「已覆盖」这一栏当不了数' },
}

// 「谁动手」。人第一个要知道的不是严重度，是"这条要不要我处理"。
// 上一版三类混在一张表里：MCP 那 6 条里有 2 条根子是我们自己的环境记录没铺 apikey，
// 跟人家脚本一点关系没有 —— 看的人先当成"脚本写得不行"，理解半天才反应过来。
// **别让人来分，分好了给他。**
const BLAME = {
  script: { title: 'QA 的脚本要改', color: C.red,
            why: '断言写得站不住：跑绿了也证明不了它认领的那件事' },
  env: { title: '不是脚本的问题：环境没铺东西', color: C.orange,
         why: '脚本可能写得很对，只是在这个环境里自己跳过了。我们只看得到自己这侧的环境记录，QA 跑的时候有没有，这儿判不了' },
  catalog: { title: '清单口径要商量', color: C.teal,
             why: '脚本和环境都没错，是清单认领的口径对不上，或者这件事清单里压根没列' },
}
const BLAME_ORDER = ['script', 'env', 'catalog']
const blameOf = g => (BLAME[g.blame] ? g.blame : 'script')
const REVIEW_RUNNING = s => s === 'queued' || s === 'running'

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
  const { has } = usePermissions()
  const canConfig = has(PERM.PROJECT_SETTINGS)
  const canGenerate = has(PERM.CASE_GENERATE)
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
  const [pageSize, setPageSize] = useState(20)
  const [cfgOpen, setCfgOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  // 看脚本内容：点开是 git show 出来的原文，只读
  const [file, setFile] = useState(null)
  const [fileLoading, setFileLoading] = useState(false)

  // 域级 AI 评审
  const [envs, setEnvs] = useState([])
  const [reviews, setReviews] = useState({})      // 域码 → 最近一次评审
  const [reviewFor, setReviewFor] = useState(null)   // 正在弹「选环境」框的那个域
  const [envId, setEnvId] = useState()
  const [starting, setStarting] = useState(false)
  const [openReview, setOpenReview] = useState(null)  // 抽屉里展示的那一条

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get(`/projects/${projectId}/qa-catalog`)
      setData(res.data)
    } catch { /* request.js 已展示错误 */ } finally { setLoading(false) }
  }, [projectId])

  useEffect(() => { fetchData() }, [fetchData])

  const fetchReviews = useCallback(async () => {
    try {
      const res = await api.get(`/projects/${projectId}/qa-catalog/reviews`)
      const map = {}
      for (const r of res.data?.reviews || []) map[r.domain] = r
      setReviews(map)
      return map
    } catch { return {} }
  }, [projectId])

  useEffect(() => {
    fetchReviews()
    api.get(`/projects/${projectId}/environments`).then(r => setEnvs(r.data || [])).catch(() => {})
  }, [projectId, fetchReviews])

  // 有域在评就接着轮询。**不轮询的话页面永远停在「排队中」** —— 后台跑完了没人告诉它
  const pending = useMemo(
    () => Object.values(reviews).filter(r => r.status === 'queued' || r.status === 'running'),
    [reviews])
  useEffect(() => {
    if (!pending.length) return undefined
    const t = setInterval(async () => {
      const map = await fetchReviews()
      // 抽屉开着的那条也要跟着变，否则人盯着一个「评审中」看到天荒地老
      setOpenReview(prev => (prev && map[prev.domain]?.id === prev.id ? map[prev.domain] : prev))
    }, 3000)
    return () => clearInterval(t)
  }, [pending.length, fetchReviews])

  const openFile = async (path) => {
    setFile({ path, content: '' })
    setFileLoading(true)
    try {
      const res = await api.get(
        `/projects/${projectId}/qa-catalog/file?path=${encodeURIComponent(path)}`)
      setFile(res.data)
    } catch { setFile(null) } finally { setFileLoading(false) }
  }

  const startReview = async () => {
    setStarting(true)
    try {
      const res = await api.post(`/projects/${projectId}/qa-catalog/reviews`,
        { domain: reviewFor.code, envId })
      setReviews(prev => ({ ...prev, [reviewFor.code]: res.data }))
      setReviewFor(null)
      setOpenReview(res.data)
      message.success(`已开始评审 ${reviewFor.code}，几十秒后出结论`)
    } catch { /* request.js 已展示错误 */ } finally { setStarting(false) }
  }

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
  const bugRefs = useMemo(() => data?.knownBugRefList || [], [data])
  const catalogIssues = data?.catalogIssues

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
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 12, color: C.gray, whiteSpace: 'nowrap',
          }}>{v}</span>
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
            <Tooltip key={s.path} title={`${s.path}（点开看内容）`}>
              <span
                onClick={() => openFile(s.path)}
                style={{
                  fontSize: 12, fontFamily: 'var(--font-mono)', cursor: 'pointer',
                  color: s.primary ? C.teal : C.gray, textDecoration: 'underline dotted',
                }}
              >
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
  // 「落后 N 个提交」算不出来（要每次开页面打一次网络），但「多久没拉过」是本地就有的，
  // 而且过期的页恰恰是没人点过「拉取最新」的那种 —— 超过 6 小时标黄，别让人拿着旧数字做判断。
  const fetchAge = useMemo(() => {
    if (!repo?.fetchedAt) return null
    const mins = Math.round((Date.now() - new Date(repo.fetchedAt).getTime()) / 60000)
    if (mins < 1) return { stale: false, text: '刚刚' }
    if (mins < 60) return { stale: false, text: `${mins} 分钟前` }
    const hours = Math.round(mins / 60)
    if (hours < 24) return { stale: hours >= 6, text: `${hours} 小时前` }
    return { stale: true, text: `${Math.round(hours / 24)} 天前` }
  }, [repo?.fetchedAt])
  // 「读不进来的行」也算不可信：那不是"对不上"，是我们根本没读到，比对不上更该先看
  const healthy = summary && !summary.claimedButUncovered && !summary.orphanScripts
    && !summary.unparsedRows && !summary.duplicateIds
  const parseLoss = (summary?.unparsedRows || 0) + (summary?.duplicateIds || 0)

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
      <div>提交于 {repo.commitDate ? new Date(repo.commitDate).toLocaleString('zh-CN') : '—'}</div>
      <div>拉取于 {repo.fetchedAt ? new Date(repo.fetchedAt).toLocaleString('zh-CN') : '—'}
        {fetchAge && <Tag color={fetchAge.stale ? 'orange' : 'green'} style={{ marginLeft: 4 }}>{fetchAge.text}</Tag>}
      </div>
      <div style={{ color: C.gray, marginTop: 6 }}>
        平台对这个仓库只读：clone --bare / fetch / git show，不写一个字。
      </div>
    </div>
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 20, fontWeight: 600, color: C.ink, margin: 0 }}>QA 对账</h2>
          <div style={{ fontSize: 12, color: C.gray, marginTop: 2 }}>
            QA 维护的验收场景分母 + 仓库里真实存在的脚本分子，两边对照着看。平台只读，不回写。
          </div>
        </div>
        <Space>
          <Popover content={LEGEND} title="这一页的列都是什么意思" placement="bottomRight">
            <Button icon={<InfoCircleOutlined />} type="text">怎么读这一页</Button>
          </Popover>
          {canConfig && (
            <Button icon={<SettingOutlined />} onClick={openConfig}>{configured ? '仓库配置' : '配置 QA 仓'}</Button>
          )}
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
              {canConfig && (
                <Button type="link" size="small" style={{ padding: '0 4px' }} onClick={openConfig}>配置 QA 仓</Button>
              )}
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
          action={canConfig && <Button size="small" onClick={openConfig}>改配置</Button>}
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
            {/* 「N 条挂着缺陷」和「几个缺陷单」是两个数：一个单子常压住好几条场景
                （实测 F-5 一个号压住 8 条）。只给前一个数，会被读成"有 12 个缺陷要修" */}
            {summary.coveredWithBugs > 0 && (
              <div style={{ marginTop: 8 }}>
                <Hit active={quick === 'bugs'} onClick={() => jump({ quick: 'bugs' })} style={{ color: C.red }}>
                  <WarningFilled />
                  <span style={{ flex: 1 }}>
                    <b>{summary.coveredWithBugs}</b> 条挂着已知缺陷，归到
                    {bugRefs.length > 0 ? (
                      <Popover
                        placement="bottomLeft"
                        title={<span style={{ fontSize: 12 }}>这些红在等 {bugRefs.length} 个缺陷单</span>}
                        content={
                          <div style={{ maxWidth: 360, fontSize: 12, lineHeight: 1.9 }}>
                            {bugRefs.map(b => (
                              <div key={b.ref}>
                                <code style={{ color: C.red }}>{b.ref}</code>
                                <span style={{ color: C.gray }}> 压住 {b.scenarios.length} 条 · </span>
                                {b.scenarios.join(' ')}
                              </div>
                            ))}
                            <div style={{ color: C.gray, marginTop: 8, lineHeight: 1.6 }}>
                              缺陷号取自脚本头的 <code>@known-bug</code>。修掉一个单子，
                              上面对应的那几条一起转绿 —— 所以要排期的是这 {bugRefs.length} 个，
                              不是 {summary.coveredWithBugs} 个。
                            </div>
                          </div>
                        }
                      >
                        <span
                          onClick={e => e.stopPropagation()}
                          style={{ cursor: 'help', borderBottom: `1px dotted ${C.red}`, margin: '0 2px' }}
                        >
                          <b>{bugRefs.length}</b> 个缺陷单
                        </span>
                      </Popover>
                    ) : <b> —</b>}
                  </span>
                </Hit>
                <div style={{ fontSize: 11, color: C.gray, paddingLeft: 20, lineHeight: 1.5 }}>
                  有脚本，但结论已知是红的
                </div>
              </div>
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
            {/* 这一行 0 也要显示：只在出问题时才冒出来的指标，跟"没算过"长得一模一样，
                而这里少读一行的后果是那条场景在页面上根本不存在 —— 覆盖率不掉、缺口不涨 */}
            <Popover
              placement="bottomLeft"
              title={<span style={{ fontSize: 12 }}>解析这份清单时丢掉的行</span>}
              content={
                <div style={{ maxWidth: 460, fontSize: 12, lineHeight: 1.8 }}>
                  {parseLoss === 0 && (
                    <div style={{ color: C.teal }}>
                      <CheckCircleFilled /> 清单里每一行都读进来了，
                      上面的 {summary.total} 条就是清单的全部。
                    </div>
                  )}
                  {catalogIssues?.unparsedRows?.map(r => (
                    <div key={r.line} style={{ marginBottom: 4 }}>
                      <span style={{ color: C.gray }}>第 {r.line} 行 </span>
                      <code style={{ fontSize: 11, wordBreak: 'break-all' }}>{r.raw}</code>
                    </div>
                  ))}
                  {catalogIssues?.duplicateIds?.length > 0 && (
                    <div style={{ marginTop: 6 }}>
                      同一个 ID 出现了两次（只留了第一条）：
                      <b style={{ color: C.orange }}> {catalogIssues.duplicateIds.join(' ')}</b>
                    </div>
                  )}
                  <div style={{ color: C.gray, marginTop: 8, lineHeight: 1.6 }}>
                    首列像场景 ID、整行却没解析成 —— 常见是行尾少一根 <code>|</code>、
                    短横打成了中文破折号、域码写成小写。丢掉一行不会让覆盖率变低，
                    只会让那条场景「不存在」，所以这里必须自己报出来。
                  </div>
                </div>
              }
            >
              {/* Popover 靠 cloneElement 往 child 上挂 onMouseEnter/ref，而 Hit 自己就用了
                  这两个名字、也不透传 ref —— 直接把 Hit 当 child 会一辈子弹不出来 */}
              <div>
                <Hit style={{ cursor: 'help' }}>
                  {parseLoss
                    ? <WarningFilled style={{ color: C.red }} />
                    : <CheckCircleFilled style={{ color: C.teal }} />}
                  <span style={{ flex: 1 }}>清单里读不进来的行</span>
                  <b style={{ color: parseLoss ? C.red : C.gray }}>{parseLoss}</b>
                </Hit>
              </div>
            </Popover>
            <div style={{ fontSize: 11, color: C.gray, marginTop: 8, lineHeight: 1.6 }}>
              前两项是 QA 自己门禁（<code>check-coverage.sh</code>）会直接 BLOCK 的；
              第三项是「回去重新审优先级」的信号，不阻断；最后一项是我们自己的解析漏没漏。
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
              按域看缺口（{domainRows.length} 个域 · 缺得多的排前面 · 点一行筛这个域 ·
              点「AI 评审」看这个域的脚本撑不撑得起清单）
            </span>,
            children: (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(490px, 1fr))', gap: '2px 24px' }}>
                {domainRows.map(d => {
                  const rv = reviews[d.code]
                  return (
                    <Hit key={d.code} active={domain === d.code} onClick={() => jump({ domain: domain === d.code ? undefined : d.code })}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, width: 40 }}>{d.code}</span>
                      <span style={{ width: 110, color: C.gray, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
                      <Progress
                        percent={d.total ? Math.round((d.covered / d.total) * 100) : 0}
                        size="small" showInfo={false} strokeColor={C.teal} style={{ flex: 1, margin: 0, minWidth: 60 }}
                      />
                      <span style={{ width: 52, textAlign: 'right', color: C.gray }}>{d.covered}/{d.total}</span>
                      <span style={{ width: 96, textAlign: 'right', color: d.gap ? C.orange : C.faint }}>
                        缺 {d.gap}{d.p0Gap ? <b style={{ color: C.red }}> · P0 {d.p0Gap}</b> : null}
                      </span>
                      <span style={{ width: 88, textAlign: 'right' }} onClick={e => e.stopPropagation()}>
                        {REVIEW_RUNNING(rv?.status) ? (
                          <Tag icon={<LoadingOutlined />} color="processing" style={{ margin: 0, cursor: 'pointer' }}
                               onClick={() => setOpenReview(rv)}>评审中</Tag>
                        ) : rv?.status === 'done' ? (
                          <Tooltip title={`${rv.environmentName || '—'} · ${rv.commitSha} · 点开看结论`}>
                            <Tag color={VERDICT[rv.result?.verdict]?.color || 'default'}
                                 style={{ margin: 0, cursor: 'pointer' }}
                                 onClick={() => setOpenReview(rv)}>
                              {VERDICT[rv.result?.verdict]?.text || '已评'}
                            </Tag>
                          </Tooltip>
                        ) : rv?.status === 'failed' ? (
                          <Tag color="error" style={{ margin: 0, cursor: 'pointer' }}
                               onClick={() => setOpenReview(rv)}>没评上</Tag>
                        ) : canGenerate ? (
                          <Button
                            type="link" size="small" icon={<RobotOutlined />}
                            style={{ padding: 0, height: 'auto', fontSize: 12 }}
                            onClick={() => { setReviewFor(d); setEnvId(envs[0]?.id) }}
                          >AI 评审</Button>
                        ) : null}
                      </span>
                    </Hit>
                  )
                })}
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
            // 24 个域，翻着找太慢。label 里域码和中文名都在，打 MCP 或「能力」都能命中
            showSearch optionFilterProp="label"
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
            current: page, pageSize, showSizeChanger: true, showTotal: t => `共 ${t} 条`,
            // 两个都得收：只接 page 的话，换每页条数会被受控的 pageSize 按回原值
            onChange: (p, s) => { setPage(p); setPageSize(s) },
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
              {
                title: '脚本', dataIndex: 'path',
                render: p => (
                  <a onClick={() => openFile(p)} style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                    <FileTextOutlined style={{ marginRight: 4 }} />{p}
                  </a>
                ),
              },
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
          {repo.fetchedAt && (
            <span style={fetchAge?.stale ? { color: C.orange } : undefined}>
              拉取于 {new Date(repo.fetchedAt).toLocaleString('zh-CN')}{fetchAge ? `（${fetchAge.text}）` : ''}
            </span>
          )}
        </div>
      )}

      {/* QA 仓配置：只在这一页维护 —— 它只影响这一页，认错了也只在这一页报错 */}
      <Modal
        title="QA 仓（只读）"
        open={cfgOpen}
        onCancel={() => setCfgOpen(false)}
        width={560}
        footer={[
          canConfig && data?.config?.url ? (
            <Popconfirm
              key="clear" title="取消配置后这一页只剩表头，确定？"
              onConfirm={() => saveConfig({ url: '', branch: '', catalogPath: '', caseGlobs: [] })}
            >
              <Button danger type="text" style={{ float: 'left' }}>取消配置</Button>
            </Popconfirm>
          ) : null,
          <Button key="cancel" onClick={() => setCfgOpen(false)}>取消</Button>,
          canConfig ? (
            <Button key="ok" type="primary" loading={saving} onClick={handleSaveConfig}>保存</Button>
          ) : null,
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

      {/* 脚本原文：git show 出来的那份，只读 */}
      <Drawer
        title={<span style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }}>{file?.path}</span>}
        open={!!file} onClose={() => setFile(null)} width={860}
        extra={file?.commitSha && <span style={{ fontSize: 12, color: C.gray }}>
          {file.lines} 行 · {(file.bytes / 1024).toFixed(1)} KB · <code>{file.commitSha.slice(0, 10)}</code>
        </span>}
      >
        {fileLoading ? <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div> : (
          <>
            {/* 点开脚本第一件想知道的事：它自己声明覆盖了哪几条、跟清单对不对得上 */}
            {(file?.header?.ids?.length > 0 || file?.header?.tier || file?.header?.knownBugs?.length > 0) && (
              <Space wrap size={4} style={{ marginBottom: 10 }}>
                <span style={{ fontSize: 12, color: C.gray }}>脚本头声明：</span>
                {(file.header.ids || []).map(id => <Tag key={id} color="blue" style={{ margin: 0 }}>{id}</Tag>)}
                {file.header.tier && <Tag style={{ margin: 0 }}>{tierText(file.header.tier)}</Tag>}
                {(file.header.knownBugs || []).map((b, i) => (
                  <Tag key={i} icon={<BugOutlined />} color="error" style={{ margin: 0 }}>{b}</Tag>
                ))}
              </Space>
            )}
            {file?.truncated && (
              <Alert type="warning" showIcon style={{ marginBottom: 10 }}
                     message="文件太大，只显示了前面一段" />
            )}
            <pre style={{
              margin: 0, padding: 12, background: '#0f1720', color: '#d8e0ea', borderRadius: 6,
              fontSize: 12, lineHeight: 1.7, overflow: 'auto', maxHeight: 'calc(100vh - 220px)',
              fontFamily: 'var(--font-mono)',
            }}>{file?.content}</pre>
          </>
        )}
      </Drawer>

      {/* 选环境 —— 环境是结论的一部分：脚本要的变量这个环境有没有，直接决定它跑不跑得起来 */}
      <Modal
        title={`AI 评审 · ${reviewFor?.code || ''} ${reviewFor?.name || ''}`}
        open={!!reviewFor} onCancel={() => setReviewFor(null)}
        okText="开始评审" confirmLoading={starting} onOk={startReview}
        okButtonProps={{ disabled: !envs.length }} width={520}
      >
        <Alert
          type="info" showIcon style={{ marginBottom: 16 }}
          message="只读这个域的清单和脚本，不跑任何东西"
          description="平台不会在这个环境上执行 QA 的脚本，也不会往 QA 仓写任何内容。结论只存在本平台。"
        />
        <div style={{ fontSize: 13, lineHeight: 2, marginBottom: 12 }}>
          <div>这个域共 <b>{reviewFor?.total || 0}</b> 条场景（已覆盖 {reviewFor?.covered || 0} · 待补 {reviewFor?.gap || 0}）</div>
          <div style={{ color: C.gray }}>
            重点看「声明覆盖了、其实没验到」—— 这正是 QA 自己的 <code>check-coverage.sh</code> 查不了的那一层。
          </div>
        </div>
        <div style={{ fontSize: 13, marginBottom: 6 }}>在哪个环境上评</div>
        {envs.length ? (
          <Select
            value={envId} onChange={setEnvId} style={{ width: '100%' }}
            options={envs.map(e => ({ value: e.id, label: e.name }))}
          />
        ) : (
          <Alert type="warning" showIcon message="这个项目还没配环境"
                 description="去「项目设置 → 环境」加一个再来，评审要拿环境的变量名跟脚本引用对账。" />
        )}
        <div style={{ fontSize: 12, color: C.gray, marginTop: 8 }}>
          只把环境的<b>变量名</b>交给模型对账（脚本要 <code>ADMIN_TOKEN</code>、这个环境有没有），
          <b>变量值一个字节都不会外传</b>。
        </div>
      </Modal>

      {/* 评审结论 */}
      <Drawer
        title={<Space>
          <span>AI 评审 · {openReview?.domain} {openReview?.domainName}</span>
          {openReview?.status === 'done' && (
            <Tag color={VERDICT[openReview.result?.verdict]?.color || 'default'} style={{ margin: 0 }}>
              {VERDICT[openReview.result?.verdict]?.text || '已评'}
            </Tag>
          )}
        </Space>}
        open={!!openReview} onClose={() => setOpenReview(null)} width={780}
        extra={openReview?.status === 'done' && canGenerate && (
          <Button size="small" icon={<RobotOutlined />} onClick={() => {
            const d = domainRows.find(x => x.code === openReview.domain)
            setOpenReview(null); setReviewFor(d || { code: openReview.domain }); setEnvId(openReview.environmentId || envs[0]?.id)
          }}>重评</Button>
        )}
      >
        {!openReview ? null : REVIEW_RUNNING(openReview.status) ? (
          <div style={{ textAlign: 'center', padding: '48px 0', color: C.gray }}>
            <Spin /><div style={{ marginTop: 12 }}>正在读这个域的 {openReview.scriptCount} 份脚本…几十秒，可以关掉页面</div>
          </div>
        ) : openReview.status === 'failed' ? (
          <Alert type="error" showIcon message="这次没评上" description={openReview.error} />
        ) : (
          <ReviewTabs r={openReview} onOpenFile={openFile} projectId={projectId} />
        )}
        {openReview && (
          <div style={{ marginTop: 20, paddingTop: 10, borderTop: '1px solid rgba(0,0,0,0.06)', fontSize: 12, color: C.gray, lineHeight: 1.9 }}>
            环境 <b>{openReview.environmentName || '—'}</b> · QA 仓 {openReview.branch} <code>{openReview.commitSha}</code>
            {' · '}{openReview.actor} 发起于 {openReview.createdAt && new Date(openReview.createdAt).toLocaleString('zh-CN')}
            <div>结论只存在本平台，QA 仓没有任何变化。</div>
          </div>
        )}
      </Drawer>
    </div>
  )
}

const SEVERITY = { blocker: C.red, major: C.orange, minor: C.gray }

// 一次评审有两拨读者，需要的东西不是同一个东西 —— 所以分两页，不做成一页里的折叠。
//
// **人**（测试经理/项目经理）：三十秒决定要不要停下来处理。他不需要知道
// 是哪一句 `assert_status 200`，他需要知道"这个域标着已覆盖的 7 条里有 5 条是 P0，
// 这次运行一条都没执行"。细节混在里面，这句话就被埋掉了。
//
// **AI / 动手改脚本的人**：要的恰恰是被埋掉的那些 —— 哪个文件、哪一句、改成什么。
// 判据锚点（evidence）是从脚本正文原样抄的，能直接 grep 到。
//
// 默认停在「给人看」那一页：打开这个抽屉的十有八九是人。
function ReviewTabs({ r, onOpenFile, projectId }) {
  return (
    <Tabs
      size="small" defaultActiveKey="human"
      items={[
        { key: 'human', label: '给人看 · 结论', children: <ReviewBrief r={r} /> },
        {
          key: 'ai',
          label: '给 AI / 整改 · 细节',
          children: <ReviewBody r={r} onOpenFile={onOpenFile} projectId={projectId} />,
        },
      ]}
    />
  )
}

// 人话那一页。**只说结论和后果**，一个脚本路径都不出现。
// 「我是怎么看的」。别人第一次看到这份结论，第一个念头是"你凭什么这么说" ——
// 与其等他质疑，不如先把方法和边界摆出来。三句话，不解释术语。
// 「我是怎么看的」+「这次读了多少」合成一块，**默认折起来**。
// 这两段每个域都一模一样，24 个域就是同一段话读 24 遍 —— 第二个域起它就是噪声。
// 但也不能删：别人第一眼的质疑就是"你凭什么这么说"。折起来 = 想看的点开，
// 不想看的不占屏。⚠ 只有"没读全"那句是例外，它必须一直露在外面。
function HowIRead({ res, r }) {
  const [open, setOpen] = useState(false)
  const c = res.coverage || {}
  const total = c.scenariosTotal || res.scenarioCount || r?.scenarioCount || 0
  const shown = c.scenariosShown
  const missedS = shown != null && total > shown ? total - shown : 0
  const missedF = (c.scriptsTotal || 0) - (c.scriptsRead || 0)
  const cut = (res.reviewedScripts || []).filter(x => x.truncated).length
  const batches = c.batches || 1
  const read = c.scriptsRead || (res.reviewedScripts || []).length
  const failed = c.batchesFailed || []
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 12, color: C.gray, lineHeight: 1.9 }}>
        读了 <b style={{ color: C.ink }}>{read}</b> 份脚本正文
        {batches > 1 && `（分 ${batches} 批读完再合并）`}
        、{shown != null ? shown : total} 条场景，一份都没真跑。
        <a onClick={() => setOpen(v => !v)}
           style={{ marginLeft: 8, color: C.teal, cursor: 'pointer' }}>
          {open ? '收起' : '怎么看的？'}
        </a>
      </div>
      {(missedS > 0 || missedF > 0 || cut > 0) && (
        <div style={{ fontSize: 12, color: C.orange, lineHeight: 1.9 }}>
          ⚠ 这个域共 {total} 条场景
          {missedS > 0 && `，其余 ${missedS} 条这次没进模型`}
          {missedF > 0 && `；还有 ${missedF} 份脚本没读进来`}
          {cut > 0 && `；${cut} 份正文被截断（截断的不下结论）`}
          —— 上面的结论只覆盖读到的这部分。
        </div>
      )}
      {/* 有批次没读成，这句必须露在外面：少读一批 = 少读十几份脚本，
          而"少读了"和"没问题"在页面上长得一模一样 */}
      {failed.length > 0 && (
        <div style={{ fontSize: 12, color: C.red, lineHeight: 1.9 }}>
          ⚠ {failed.map(i => `第 ${i} 批`).join('、')}没读成（网关限流或超时），
          那几批的脚本这一趟等于没看 —— 重跑一次这个域就补上了。
        </div>
      )}
      {open && (
        <div style={{
          background: 'rgba(0,0,0,0.015)', border: `1px solid ${C.line}`,
          borderRadius: 8, padding: '12px 14px', marginTop: 8, fontSize: 12.5,
          color: C.gray, lineHeight: 2,
        }}>
          <div>① 读清单里这个域的场景 —— 它<b>说要验</b>什么；</div>
          <div>② 读认领了这些场景的脚本正文 —— 它<b>实际在验</b>什么；</div>
          <div>③ 一条条对，只问一个问题：<b style={{ color: C.ink }}>这条断言能不能失败？</b>
            改坏了会红才算真在验，恒真的断言跑绿等于没跑。</div>
          <div style={{ marginTop: 6 }}>
            没做的事：脚本<b>一份都没真跑</b>（只读正文），也没碰 QA 仓一个字。
            {batches > 1 && `脚本一次装不进一轮对话，切成 ${batches} 批各读各的，每批都拿到完整场景清单，最后合并 —— ${read} 份全读了，不是抽了几份。`}
          </div>
        </div>
      )}
    </div>
  )
}

// 按「谁动手」分栏，每条**只给一句人话**（`oneLine`）——
// 上一版这里渲染的是 `problem`（写给动手改脚本的人看的那段），一条三四行、
// 一个域十几行，24 个域连着看就是几天。技术描述留在隔壁「给 AI / 整改」那页。
// 每栏最多露 3 条：人在这一页只做一个决定 —— 这个域要不要停下来处理。
const GROUP_MAX = 3

function BlameGroup({ kind, rows }) {
  if (!rows.length) return null
  const m = BLAME[kind]
  const head = rows.slice(0, GROUP_MAX)
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ width: 3, height: 13, background: m.color, borderRadius: 2, flexShrink: 0 }} />
        <b style={{ color: C.ink }}>{m.title}</b>
        <span style={{ color: m.color, fontWeight: 600, whiteSpace: 'nowrap' }}>{rows.length} 条</span>
      </div>
      {/* 释义单独一行：跟标题挤一行会把「7 条」折成两行，数字被撕开比不写还糟 */}
      <div style={{ fontSize: 12, color: C.gray, marginLeft: 11, lineHeight: 1.8 }}>{m.why}</div>
      {head.map((g, i) => (
        <div key={i} style={{ display: 'flex', gap: 8, marginLeft: 11, padding: '2px 0', lineHeight: 1.9 }}>
          {/* 清单那栏的条目没有场景号，别拿一列「—」去占位 */}
          {g.id && (
            <span style={{ color: C.gray, flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>{g.id}</span>
          )}
          <span>{g.oneLine || truncate(g.problem) || '—'}</span>
        </div>
      ))}
      {rows.length > head.length && (
        <div style={{ marginLeft: 11, fontSize: 12, color: C.faint, lineHeight: 1.9 }}>
          还有 {rows.length - head.length} 条 —— 要逐条看切到「给 AI / 整改」那一页
        </div>
      )}
    </div>
  )
}

// 老记录没有 `oneLine`（那是后加的字段），只能把 `problem` 截一截顶上。
// 截出来会不通顺，但比整段技术描述糊在人看的那页上强。
const truncate = t => (t && t.length > 32 ? `${t.slice(0, 32)}…` : t)

function ReviewBrief({ r }) {
  const res = r.result || {}
  const b = res.brief || {}
  const v = VERDICT[res.verdict]
  const gaps = res.scriptGaps || []
  // `catalogGaps` 是另一张表（清单缺场景 / 口径不符），此前只在「给 AI」那页露过 ——
  // 于是人看的这页出现过一句「清单要商量：17 处」底下却只列 1 条。同一屏里两个数打架，
  // 读的人只会得出「这页的数不能信」。归谁动手就归到哪一栏，别让它无处可去。
  // `scenario` 是给动手的人写的一句话，冒号后面常挂着端点、方法名、字段名
  //（实测露出过「上游服务器凭据轮换：PUT 更新 auth_credential…」）——
  // 人看这页说好了一个路径一个字段名都不出现，所以只取冒号前那截，再截长度。
  const fromCatalog = (res.catalogGaps || []).map(c => ({
    oneLine: truncate((c.scenario || '').split(/[：:]/)[0].trim()), problem: c.why,
  }))
  const grouped = BLAME_ORDER.map(k => [
    k, k === 'catalog'
      ? [...gaps.filter(g => blameOf(g) === k), ...fromCatalog]
      : gaps.filter(g => blameOf(g) === k),
  ])
  const mine = gaps.filter(g => blameOf(g) === 'script').length
  const total = gaps.length + fromCatalog.length
  return (
    <div style={{ fontSize: 13 }}>
      <div style={{
        padding: '14px 16px', borderRadius: 8, marginBottom: 16,
        background: 'rgba(0,0,0,0.02)', border: `1px solid ${C.line}`,
      }}>
        <div style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.8, color: C.ink }}>
          {b.headline || res.summary || '这一轮没给出结论'}
        </div>
        {v && (
          <div style={{ fontSize: 12.5, color: C.gray, marginTop: 8, lineHeight: 1.9 }}>
            这次判「<b style={{
              color: res.verdict === 'ok' ? C.teal : res.verdict === 'bad' ? C.red : C.orange,
            }}>{v.text}</b>」= {v.why}
          </div>
        )}
      </div>

      <HowIRead res={res} r={r} />

      {b.points?.length > 0 && (
        <div style={{ marginBottom: 18 }}>
          {b.points.map((x, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, padding: '5px 0', lineHeight: 1.9 }}>
              <span style={{ color: C.orange, flexShrink: 0 }}>•</span>
              <span>{x}</span>
            </div>
          ))}
        </div>
      )}

      {total > 0 && (
        <div style={{ marginBottom: 16, paddingTop: 12, borderTop: `1px solid ${C.line}` }}>
          <div style={{ fontWeight: 600, color: C.ink, marginBottom: 10 }}>
            抓到 {total} 条，按谁动手分开看
          </div>
          {grouped.map(([k, rows]) => <BlameGroup key={k} kind={k} rows={rows} />)}
        </div>
      )}

      {b.solid?.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 600, color: C.ink, marginBottom: 6 }}>撑得住的部分</div>
          {b.solid.map((x, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, padding: '3px 0', lineHeight: 1.9 }}>
              <span style={{ color: C.teal, flexShrink: 0 }}>✓</span>
              <span>{x}</span>
            </div>
          ))}
        </div>
      )}

      {b.nextStep && (
        <Alert
          type="info" showIcon style={{ marginBottom: 16 }}
          message="下一步" description={b.nextStep}
        />
      )}

      {/* 数字只留他要的那三个：验没验到、有几条真要人家改、这个环境自己缺什么。
          「要改的脚本」以前算的是全部 gaps —— 把环境的锅算进了 QA 头上，改成只数 script 那栏。 */}
      <div style={{ display: 'flex', gap: 24, padding: '12px 0', borderTop: `1px solid ${C.line}` }}>
        <Num label="这次的结论" value={v?.text || '—'}
             color={res.verdict === 'ok' ? C.teal : res.verdict === 'bad' ? C.red : C.orange} />
        <Num label="QA 的脚本要改" value={mine}
             hint={total > mine ? `另 ${total - mine} 条不是脚本的事` : ''}
             color={mine ? C.orange : C.teal} />
        {/* 这块数的是**变量个数**，上面那组数的是**受影响的场景条数** —— 两个不同的东西，
            一屏之内摆在一起(实测 10 和 7)，标签不把单位写清楚，读的人第一反应是"这页数打架"。 */}
        <Num label="这条环境记录里缺的变量" value={`${(res.envMissing || []).length} 个`}
             hint={(res.envMissing || []).length
               ? '不是 QA 的问题；上面「环境要铺」那几条是受它影响的场景'
               : ''}
             color={(res.envMissing || []).length ? C.gray : C.teal} />
      </div>

      {/* 「这次读了多少」已经并进上面的 HowIRead 了，这儿不再重复一遍 */}
      <div style={{ fontSize: 12, color: C.gray, marginTop: 10, lineHeight: 1.9 }}>
        <b>结论是建议</b>，不是门禁 —— 清单和脚本都是 QA 自己维护的，平台只读。
        要具体到"改哪个文件的哪一句"，切到隔壁「给 AI / 整改」那一页。
      </div>
    </div>
  )
}

// 「这次到底看了多少」。**上限截掉的那部分必须写在这儿** ——
// 页面上方写着「场景 75 条」，而进模型的只有 60 条；不说的话读的人默认 75 条都评过了。
// 截断本身不是问题（额度有限，先给 P0/高风险），把截断说成全量才是。
function Scanned({ res, r }) {
  const c = res.coverage || {}
  const total = c.scenariosTotal || res.scenarioCount || r.scenarioCount
  const shown = c.scenariosShown
  const missedS = shown != null && total > shown ? total - shown : 0
  const missedF = (c.scriptsTotal || 0) - (c.scriptsRead || 0)
  const cut = (res.reviewedScripts || []).filter(x => x.truncated).length
  return (
    <div style={{
      fontSize: 12, color: C.gray, marginTop: 12, paddingTop: 10,
      borderTop: `1px solid ${C.line}`, lineHeight: 1.9,
    }}>
      这次读了 {(res.reviewedScripts || []).length} 份脚本
      {cut > 0 && `（其中 ${cut} 份正文太长被截断，截断的那几份不下结论）`}
      ，评了 {shown != null ? shown : total} 条场景。
      {(missedS > 0 || missedF > 0) && (
        <div style={{ color: C.orange }}>
          ⚠ 这个域共 {total} 条场景
          {missedS > 0 && `，其余 ${missedS} 条这次没进模型`}
          {missedF > 0 && `；还有 ${missedF} 份脚本没读进来`}
          —— 上面的结论只覆盖读到的这部分。
        </div>
      )}
    </div>
  )
}

function Num({ label, value, hint, color }) {
  return (
    <div>
      <div style={{ fontSize: 12, color: C.gray }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 600, color, lineHeight: 1.6 }}>{value}</div>
      {hint && <div style={{ fontSize: 11, color: C.faint }}>{hint}</div>}
    </div>
  )
}

// 评审结论的正文。四块的顺序 = 测试员下一步该干什么的顺序：
// 先看「声明了没验到」（覆盖率是虚的），再看环境跑不跑得起来，最后才是补什么、先补哪条。
function ReviewBody({ r, onOpenFile, projectId }) {
  const res = r.result || {}
  const empty = !res.scriptGaps?.length && !res.catalogGaps?.length
    && !res.nextUp?.length && !res.envMissing?.length
  return (
    <div style={{ fontSize: 13 }}>
      <TakeAway r={r} projectId={projectId} />
      {res.summary && (
        <div style={{ marginBottom: 16, lineHeight: 1.9 }}><Rich text={res.summary} /></div>
      )}

      <Section title="抓到的问题（按谁动手排）"
               hint="脚本头写了 @scenario，但正文没验到那件事 —— QA 自己的门禁查不了这一层">
        {res.scriptGaps?.length ? [...res.scriptGaps]
          .sort((a, c) => BLAME_ORDER.indexOf(blameOf(a)) - BLAME_ORDER.indexOf(blameOf(c)))
          .map((g, i) => (
          <div key={i} style={{ padding: '8px 0', borderTop: i ? '1px dashed rgba(0,0,0,0.06)' : 'none' }}>
            <Space size={6} wrap style={{ marginBottom: 4 }}>
              {g.id && <Tag color="blue" style={{ margin: 0 }}>{g.id}</Tag>}
              {/* 动手的人也要先知道这条归谁：改脚本解决不了的那些，别让他白改一遍 */}
              <Tag style={{ margin: 0, color: BLAME[blameOf(g)].color,
                            borderColor: BLAME[blameOf(g)].color }}>
                {BLAME[blameOf(g)].title}
              </Tag>
              {g.severity && <Tag color={SEVERITY[g.severity] ? undefined : 'default'}
                                  style={{ margin: 0, color: SEVERITY[g.severity], borderColor: SEVERITY[g.severity] }}>
                {g.severity}</Tag>}
              {g.path && (
                <a onClick={() => onOpenFile(g.path)} style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                  {g.path}
                </a>
              )}
            </Space>
            <div style={{ lineHeight: 1.8 }}><Rich text={g.problem} /></div>
            {/* 判据锚点：从脚本正文原样抄的，拿去 grep 就能定位到要改的那一句。
                没有它，"这条断言不够"就只是一句评价，接手的人还得自己把整份脚本读一遍。 */}
            {g.evidence && (
              <pre style={{
                margin: '6px 0', padding: '6px 10px', background: 'rgba(0,0,0,0.03)',
                borderRadius: 4, fontFamily: 'var(--font-mono)', fontSize: 12,
                color: '#476582', whiteSpace: 'pre-wrap', overflowX: 'auto',
              }}>{g.evidence}</pre>
            )}
            {g.fix && <div style={{ color: C.gray, lineHeight: 1.8 }}>建议改成：<Rich text={g.fix} /></div>}
          </div>
        )) : <Nothing text="逐条读下来没抓到「声明了没验到」的" />}
      </Section>

      <Section title="我们这侧环境记录里没有的名字（不是脚本的问题）"
               hint={`脚本引用的、或 config 里声明「要从外面传」的，而我们这条 ${r.environmentName || '所选环境'} 记录里没有。代码算的，不是模型猜的 —— 但它只说明我们这侧没记着，推不出 QA 自己跑的时候也缺`}>
        {res.envMissing?.length ? (
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            {res.envMissing.map(v => (
              <div key={v.name}>
                <Tag color="warning" style={{ fontFamily: 'var(--font-mono)' }}>{v.name}</Tag>
                <Tooltip title={(v.scripts || []).join('\n')}>
                  <span style={{ fontSize: 12, color: C.gray }}>
                    {(v.scripts || []).map(p => p.split('/').pop()).join('、')}
                  </span>
                </Tooltip>
              </div>
            ))}
            <div style={{ fontSize: 12, color: C.faint }}>
              公共库里真赋过值的、自带兜底值的、shell 自带的、夹具运行时拼出来的都已经排掉。
              写成 <code>{'export X="${X:-}"'}</code> 的算缺 ——
              那是仓库在明说这个值得从环境来，没配就整条静默跳过。
              <div style={{ marginTop: 4 }}>
                ⚠ 两件事别搞混：在平台这边补上变量<b>不会</b>让 QA 的脚本真跑起来（值要在真正跑套件的地方注入）；
                而平台这边没记着，也<b>不等于</b>那边缺。所以这一列不构成对 QA 的意见。
              </div>
            </div>
          </Space>
        ) : <Nothing text="脚本要的变量这个环境都有" />}
      </Section>

      <Section title="清单本身漏了什么" hint="这个域的场景之间明显缺的一环 —— 清单是别人维护的，这只是建议">
        {res.catalogGaps?.length ? res.catalogGaps.map((g, i) => (
          <div key={i} style={{ padding: '6px 0', lineHeight: 1.8 }}>
            <Rich text={g.scenario || g.problem} />
            {g.why && <div style={{ color: C.gray }}><Rich text={g.why} /></div>}
          </div>
        )) : <Nothing text="没看出明显缺的一环" />}
      </Section>

      <Section title="待补的先做哪条" hint="只在标「待补」的场景里挑">
        {res.nextUp?.length ? res.nextUp.map((g, i) => (
          <div key={i} style={{ padding: '6px 0', lineHeight: 1.8 }}>
            <Space size={6}>
              <Tag style={{ margin: 0 }}>{i + 1}</Tag>
              {g.id && <Tag color="blue" style={{ margin: 0 }}>{g.id}</Tag>}
              {/* 动手的人也要先知道这条归谁：改脚本解决不了的那些，别让他白改一遍 */}
              <Tag style={{ margin: 0, color: BLAME[blameOf(g)].color,
                            borderColor: BLAME[blameOf(g)].color }}>
                {BLAME[blameOf(g)].title}
              </Tag>
            </Space>
            <div><Rich text={g.why || g.problem} /></div>
          </div>
        )) : <Nothing text="这个域没有待补的场景" />}
      </Section>

      {empty && <Empty description="模型这一轮什么都没说 —— 重评一次试试" />}

      <Scanned res={res} r={r} />
    </div>
  )
}

// 「QA 那边怎么拿到这份结论」—— 只能是他自己来拉，因为平台对 QA 仓永远只读。
//
// 所以这里给的是**文本**：复制走贴 issue、或存成 .md 交给他那边的 AI 改脚本。
// QA 那边跑 Claude Code 的话有第三条路：MCP 工具 lum_get_qa_review，直接拿同一份东西。
// 三条路都是"拉"，平台一个字节都不会往那个仓库写。
function TakeAway({ r, projectId }) {
  const [busy, setBusy] = useState(false)

  const fetchMd = async () => {
    const res = await api.get(
      `/projects/${projectId}/qa-catalog/reviews/${r.id}/export`, { params: { format: 'md' } })
    return res.data
  }

  const copy = async () => {
    setBusy(true)
    try {
      const d = await fetchMd()
      await navigator.clipboard.writeText(d.markdown)
      message.success('已复制 Markdown 全文，可直接贴到 issue 或交给 AI')
    } catch (e) {
      message.error(e.message || '复制失败')
    } finally { setBusy(false) }
  }

  const download = async () => {
    setBusy(true)
    try {
      const d = await fetchMd()
      const url = URL.createObjectURL(new Blob([d.markdown], { type: 'text/markdown' }))
      const a = document.createElement('a')
      a.href = url; a.download = d.filename; a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      message.error(e.message || '导出失败')
    } finally { setBusy(false) }
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
      padding: '8px 12px', marginBottom: 14, borderRadius: 6,
      background: 'rgba(14,165,160,0.06)', border: '1px solid rgba(14,165,160,0.2)',
    }}>
      <span style={{ fontSize: 12, color: C.ink }}>把这份结论交给 QA：</span>
      <Button size="small" icon={<CopyOutlined />} loading={busy} onClick={copy}>复制 Markdown</Button>
      <Button size="small" icon={<DownloadOutlined />} loading={busy} onClick={download}>存成 .md</Button>
      <Tooltip title={
        <div style={{ fontSize: 12, lineHeight: 1.9 }}>
          QA 那边跑 Claude Code 的话，让它直接调 MCP 工具
          <code> lum_get_qa_review</code>（带 project_id 和 domain）拿同一份东西，
          不用人来回传。
          <div style={{ marginTop: 6 }}>
            三条路都是<b>他来拉</b> —— 平台对 QA 仓永远只读，不会替他往仓库里放文件。
            他那边的 <code>check-coverage.sh</code> 拿清单当判据来源，
            我们多写一个文件，他就会红在一个查不到原因的地方。
          </div>
        </div>
      }>
        <span style={{ fontSize: 12, color: C.gray, cursor: 'help', borderBottom: `1px dashed ${C.faint}` }}>
          QA 用 MCP 直接拉？
        </span>
      </Tooltip>
    </div>
  )
}

function Section({ title, hint, children }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ fontWeight: 600, color: C.ink }}>{title}</div>
      <div style={{ fontSize: 12, color: C.gray, marginBottom: 6 }}>{hint}</div>
      {children}
    </div>
  )
}

const Nothing = ({ text }) => <div style={{ fontSize: 12, color: C.faint }}>{text}</div>
