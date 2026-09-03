/*
 * CC 反馈 —— 外部 Claude Code 报回来的**平台自身**问题的处理面。
 *
 * 全局，不挂项目（判据见 docs/cc-feedback-channel.md §2）：一条平台缺陷不该
 * 按项目分成 N 条，处理方也只有维护者一拨。项目只当来源线索显示。
 *
 * 这一页的重点不是"看"，是**回音**：done / wont_fix 必须写回复，后端硬校验。
 * 判「不需要处理」而不写正确做法，等于让 CC 下一轮照原样再撞一次 ——
 * 而平台这边只会静默 +1，两边都以为对方在动。
 *
 * **2026-09-01 起：判是 AI 的活，人只兜底。** 反馈一进来就自动分诊，页面上人做两件事：
 * 看结论、和拍板 AI 说自己判不了的那几条（「等人拍板」筛得到）。所以
 *   · 行内操作是「AI 处理」，不是「处理」；上面还有个「批量处理」把积压的一次推完；
 *   · **手工录入的入口去掉了** —— 这张表的正常来源是 CC 自己报，人代录一条没有用途
 *     （接口还留着，导入脚本和 API 测试走它）。
 * 「人拍板」那一组按钮**没有删**：AI 判不了的、和人要改判 AI 的，都得从那儿走。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Table, Tag, Input, Select, Space, Button, Drawer, Modal, Form, message,
  Tooltip, Empty, Alert, Spin, Badge, Progress,
} from 'antd'
import { SearchOutlined, ReloadOutlined, RobotOutlined } from '@ant-design/icons'
import { timeColumn, formatTimeFull } from '../../utils/timeCol'
import { api } from '../../utils/request'

const { TextArea } = Input

const CATEGORY_META = {
  bug: { label: '缺陷', color: '#e8453c', bg: 'rgba(232,69,60,0.1)' },
  improvement: { label: '优化', color: '#ff7d00', bg: 'rgba(255,125,0,0.1)' },
  requirement: { label: '需求', color: '#4e8af0', bg: 'rgba(78,138,240,0.1)' },
}
const STATUS_META = {
  new: { label: '待处理', color: '#e8453c', bg: 'rgba(232,69,60,0.1)' },
  triaged: { label: '已认下', color: '#ff7d00', bg: 'rgba(255,125,0,0.1)' },
  in_progress: { label: '处理中', color: '#4e8af0', bg: 'rgba(78,138,240,0.1)' },
  done: { label: '已处理', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  wont_fix: { label: '不需要处理', color: '#86909c', bg: 'rgba(0,0,0,0.05)' },
  duplicate: { label: '重复', color: '#86909c', bg: 'rgba(0,0,0,0.05)' },
}
const SEVERITY_META = {
  high: { label: '高', color: '#e8453c' },
  medium: { label: '中', color: '#ff7d00' },
  low: { label: '低', color: '#86909c' },
}

// 范围 = **坏掉的是哪一块子系统**，不是「手按在哪个工具上」（那个是 toolName，
// 留在「来源」列里，两列不互相替代）。也**不是**工具货架分类的复制：
// 按货架分类，AI 评审那一整块会挂在「用例·手工步骤」名下，整块隐形。
// 值和顺序跟后端 `models/cc_feedback.py` 的 AREAS 对齐（那边是唯一出处），
// 这里只补中文名 —— 顺序按「离 CC 干活最近」排，不按字典序。
// **故意不给 14 个域配 14 种颜色**：这一列是分类不是信号，上了色就等于
// 让最刺眼的那个域看着最要紧，而「要紧」已经有优先级列在说了。
const AREA_META = {
  ai_review: { label: 'AI 评审' },
  sync: { label: '回推入库与校验' },
  case: { label: '用例读写' },
  gate: { label: '交付门禁与体检' },
  api_run: { label: '接口场景执行' },
  report: { label: '执行报告与覆盖' },
  ui_script: { label: 'UI 脚本执行' },
  diff: { label: '版本对账' },
  qa_review: { label: 'QA 仓对账' },
  apidoc: { label: '接口库' },
  note: { label: '项目须知' },
  spec: { label: '接入规范与工具描述' },
  env: { label: '环境与变量' },
  other: { label: '其它' },
}
// 「还没判过域」。**和 other 是两件事**：other = 判过了、确实归不进任何一档；
// 这个 = 还没人判。混成一个值，「没判」会永久伪装成「判过了没归属」。
const AREA_NONE = '__none__'

// 来源三分，**别把 import 并进「页面录入」**：这三个数的比例回答的是
// 「通道到底有没有替掉人肉搬运」—— import 是通道开通前攒的存量，
// 并进页面那一档，这个问题就永远查不出来了。
const SOURCE_META = {
  cc: { short: 'CC', full: '外部 Claude Code', color: '#7c5cbf', bg: 'rgba(124,92,191,0.08)' },
  import: { short: '导入', full: '通道开通前的存量（汇总文档导入）', color: '#0fa47f', bg: 'rgba(15,164,127,0.08)' },
  human: { short: '页面', full: '页面录入', color: '#86909c', bg: 'rgba(0,0,0,0.04)' },
}

// 谁落的这个裁定。**默认就是 ai** —— 人打开这一页是来看结论的。
// 这一列要露出来的原因只有一个：AI 判的「不需要处理」能被带新证据的重报翻案，
// 人判的不能。看不见是谁判的，就看不出这条还能不能翻。
const DECIDER_META = {
  ai: { label: 'AI', color: '#7c5cbf', bg: 'rgba(124,92,191,0.08)',
        tip: 'AI 自己落的裁定。判「不需要处理」的话 CC 带新证据重报能翻案' },
  human: { label: '人', color: '#0ea5a0', bg: 'rgba(14,165,160,0.08)',
           tip: '人拍的板。判「不需要处理」从此终局，重报也不翻' },
  system: { label: '平台', color: '#86909c', bg: 'rgba(0,0,0,0.04)',
            tip: '平台按规则落的，不经模型' },
}

// 处置动作。每个动作**自己说清它要什么** —— 回音必填与否写在这儿，
// 页面据此决定弹不弹输入框，后端再硬校验一遍（两层，页面这层只是不让人白跑一趟）。
const ACTIONS = [
  {
    status: 'triaged', label: '认下并分类', needsCategory: true,
    tip: '定类 + 严重度，表示这条被接受了。回音可以晚点补',
  },
  {
    status: 'in_progress', label: '开始处理', needsCategory: true,
    tip: '正在改。CC 那边看到的是「处理中」',
  },
  {
    status: 'done', label: '处理完了', needsCategory: true, needsResolution: true,
    resolutionLabel: '回音（必填）',
    resolutionPlaceholder:
      '写「现在该怎么做」，不是「修好了」。\n' +
      '涉及后端改动的，提醒他：平台后端不带 --reload，得等重启后才生效。',
  },
  {
    status: 'wont_fix', label: '不需要处理', danger: true, needsResolution: true,
    resolutionLabel: '回音（必填）',
    resolutionPlaceholder:
      '说清为什么不做；如果是他没找对方法，**把正确方法写出来** ——\n' +
      '只说「你错了」等于没回音，下一轮照原样再撞一次。',
    tip: '这条回音会永久短路同指纹的后续上报：他再报同一件事，当场收到这段话',
  },
  {
    status: 'duplicate', label: '标为重复', needsDuplicate: true,
    tip: '并到另一条上，填那条的 id',
  },
]

/** 抽屉里的一行「标签 值」 */
function Field({ label, children }) {
  return (
    <div style={{ display: 'flex', gap: 12, padding: '7px 0', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
      <div style={{ width: 84, flexShrink: 0, fontSize: 12, color: '#86909c' }}>{label}</div>
      <div style={{ flex: 1, minWidth: 0, fontSize: 13, color: '#1d2129', wordBreak: 'break-word' }}>{children}</div>
    </div>
  )
}

function Block({ title, children, extra }) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <div style={{ fontSize: 12, color: '#86909c' }}>{title}</div>
        {extra}
      </div>
      {children}
    </div>
  )
}

const preStyle = {
  margin: 0, padding: '12px 14px', background: 'transparent', borderRadius: 12,
  fontSize: 12, lineHeight: 1.85, overflow: 'auto', maxHeight: 420,
  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
  border: '1px solid rgba(0,0,0,0.04)', color: '#1d2129',
}

export default function CCFeedback() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [summary, setSummary] = useState({})
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  // 默认只看待处理 —— 这一页的用途是"还欠 CC 什么"，不是流水账
  const [statusFilter, setStatusFilter] = useState('__pending__')
  const [categoryFilter, setCategoryFilter] = useState(null)
  const [areaFilter, setAreaFilter] = useState(null)
  const [keyword, setKeyword] = useState('')

  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [action, setAction] = useState(null)   // 当前弹出的处置动作
  const [form] = Form.useForm()
  const [selected, setSelected] = useState([])
  const [batch, setBatch] = useState({})      // 后端那个全局单批的进度
  const [rowBusy, setRowBusy] = useState(null) // 正在跑 AI 的那一行

  const fetchList = useCallback(async () => {
    setLoading(true)
    try {
      const p = new URLSearchParams()
      p.append('page', page)
      p.append('pageSize', pageSize)
      if (statusFilter === '__pending__') p.append('pendingOnly', 'true')
      // 「等人拍板」是跨状态的一撮（AI 说判不了的挂在 new 上、AI 判的 wont_fix
      // 被带新证据重报的挂在 wont_fix 上），所以走独立开关不是 status 值
      else if (statusFilter === '__human__') p.append('awaitingHuman', 'true')
      else if (statusFilter) p.append('status', statusFilter)
      if (categoryFilter) p.append('category', categoryFilter)
      // AREA_NONE 直接透传：后端把它读作「area is null」，
      // 不能在这里翻译成空字符串 —— 那等于不筛
      if (areaFilter) p.append('area', areaFilter)
      if (keyword) p.append('keyword', keyword)
      const res = await api.get(`/cc-feedback?${p.toString()}`)
      setItems(res.data.items || [])
      setTotal(res.data.total || 0)
      setSummary(res.data.summary || {})
      setBatch(res.data.summary?.batch || {})
    } catch {
      // 读这一类的失败**不再自己弹** —— request() 已经弹过一次，而且弹的是
      // 服务端那句（「无权限执行此操作」/ 具体报错），比再叠一句「加载失败」
      // 有用。这一页也只加载这一件事，不存在弄不清是哪个请求挂了。
      setItems([]); setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, statusFilter, categoryFilter, areaFilter, keyword])

  useEffect(() => { fetchList() }, [fetchList])

  // 批次在跑的时候每 3 秒刷一次：状态会一条条变，看得见进度人才不会以为卡死了
  useEffect(() => {
    if (!batch?.running) return undefined
    const t = setInterval(async () => {
      try {
        const res = await api.get('/cc-feedback/batch-status', { silent: true })
        setBatch(res.data || {})
        if (!res.data?.running) {
          message.success(`批量处理跑完了：判了 ${res.data?.done ?? 0} 条`
            + (res.data?.needsHuman ? `，其中 ${res.data.needsHuman} 条 AI 说判不了（在「等人拍板」里）` : '')
            + (res.data?.failed ? `，${res.data.failed} 条没成（多半是限流，重新点一次即可）` : ''))
        }
        fetchList()
      } catch { /* 轮询失败不弹 —— 弹了就是每 3 秒一条 toast */ }
    }, 3000)
    return () => clearInterval(t)
  }, [batch?.running, fetchList])

  const startBatch = async () => {
    try {
      const res = await api.post('/cc-feedback/ai-handle',
        selected.length ? { ids: selected } : {}, { silent: true })
      setSelected([])
      setBatch(res.data?.batch || { running: true })
      message.success(`已交给 AI：${res.data?.accepted ?? 0} 条，顺序判完（判不了的会落到「等人拍板」）`)
    } catch (err) {
      message.error(`${err?.message || '发起失败'}${err?.detail ? ` ${err.detail}` : ''}`)
    }
  }

  // 行内「AI 处理」：单条，跑完当场把结果落到这一行上
  const runRowAi = async (row) => {
    setRowBusy(row.id)
    try {
      const res = await api.post(`/cc-feedback/${row.id}/analyze`, null, { silent: true })
      message.success(res.data?.note || 'AI 判完了')
      fetchList()
      if (detail?.id === row.id) openDetail(row)
    } catch (err) {
      message.error(`${err?.message || 'AI 处理失败'}${err?.detail ? ` ${err.detail}` : ''}`)
    } finally { setRowBusy(null) }
  }

  const openDetail = async (row) => {
    setDetail(row); setDetailLoading(true)
    try {
      const res = await api.get(`/cc-feedback/${row.id}`)
      setDetail(res.data)
    } catch {
      // 吞掉：request() 已经弹过服务端那句了。这里若不 catch，onClick 里的
      // 这个 promise 会变成 unhandled rejection。抽屉退回 row 里那份摘要，
      // 不至于白屏。
    } finally { setDetailLoading(false) }
  }

  const runAnalyze = async () => {
    setAnalyzing(true)
    try {
      const res = await api.post(`/cc-feedback/${detail.id}/analyze`, null, { silent: true })
      // 它现在**直接落裁定**，所以整条都要换掉（状态/类别/回音都可能变了），
      // 不能只把 aiAnalysis 拼回去 —— 那样抽屉上的状态会停在旧值上
      setDetail(d => ({ ...d, ...res.data }))
      message.success(res.data?.note || 'AI 判完了')
      fetchList()
    } catch (err) {
      // 后端把 why/howTo 拼进 error.detail（例：没绑模型时告诉你去哪绑）。
      // silent 是为了别叠两条 toast —— request() 默认已经弹过一次结论了。
      message.error(`${err?.message || 'AI 分析失败'}${err?.detail ? ` ${err.detail}` : ''}`)
    } finally { setAnalyzing(false) }
  }

  const openAction = (a, prefill) => {
    setAction(a)
    form.setFieldsValue({
      category: prefill?.category ?? detail?.category ?? detail?.reportedCategory ?? undefined,
      severity: prefill?.severity ?? detail?.severity ?? undefined,
      area: prefill?.area ?? detail?.area ?? undefined,
      resolution: prefill?.resolution ?? '',
      duplicateOf: '',
    })
  }

  const submitAction = async () => {
    const v = await form.validateFields()
    try {
      const res = await api.post(`/cc-feedback/${detail.id}/triage`, {
        status: action.status,
        category: v.category || undefined,
        severity: v.severity || undefined,
        area: v.area || undefined,
        resolution: v.resolution || undefined,
        duplicateOf: v.duplicateOf || undefined,
      }, { silent: true })
      setDetail(d => ({ ...d, ...res.data }))
      setAction(null)
      message.success(`已置为「${STATUS_META[action.status].label}」`)
      fetchList()
    } catch (err) {
      message.error(`${err?.message || '处置失败'}${err?.detail ? ` ${err.detail}` : ''}`)
    }
  }


  // 列顺序按「先看是什么事，再看要不要紧，最后看谁报的」排 —— 标题在最左，
  // 来源（谁报的/哪个工具/哪个项目）挪到最后：它是查线索时才看的东西。
  const columns = [
    {
      title: '标题', dataIndex: 'title', ellipsis: true,
      render: (v, r) => (
        <a style={{ color: '#1d2129', fontWeight: 500 }} onClick={() => openDetail(r)} title={v}>
          {v}
          {r.needsHuman && (
            <Tooltip title={r.needsHuman}>
              <Tag style={{ marginLeft: 6, fontSize: 11, color: '#ff7d00', background: 'rgba(255,125,0,0.1)', border: 'none' }}>等人拍板</Tag>
            </Tooltip>
          )}
          {r.sampled && (
            <Tooltip title="AI 判的「不需要处理」抽检样本（每 5 条抽 1）—— 裁定已经生效，复核只为校准它判得准不准">
              <Tag style={{ marginLeft: 6, fontSize: 11, color: '#7c5cbf', background: 'rgba(124,92,191,0.08)', border: 'none' }}>抽检</Tag>
            </Tooltip>
          )}
          {r.reopenedFrom && (
            <Tooltip title="这条是同一件事再次发生 —— 上一条已经结掉了，复发另起一行（结论已经过一次，复发是新信息）">
              <Tag style={{ marginLeft: 6, fontSize: 11, color: '#e8453c', background: 'rgba(232,69,60,0.1)', border: 'none' }}>复发</Tag>
            </Tooltip>
          )}
        </a>
      ),
    },
    {
      // 排在标题右边而不是最右：读一行的顺序是「哪一块坏了 → 什么事」，
      // 挪到最后就退化成查线索时才看的东西（toolName 就在那儿）。
      title: '范围', dataIndex: 'area', width: 132, align: 'center',
      render: (v, r) => {
        const m = AREA_META[v]
        if (!m) {
          return (
            <Tooltip title="还没判过属于哪一块 —— AI 处理一遍就有了。注意它和「其它」不是一回事：「其它」是判过了、确实归不进任何一档">
              <span style={{ color: '#c9cdd4' }}>待判</span>
            </Tooltip>
          )
        }
        return (
          <Tooltip title={`坏在这一块：${r.areaLabel || m.label}（涉及工具见「来源」列）`}>
            <Tag style={{ color: '#4e5969', background: 'rgba(0,0,0,0.04)', border: 'none', margin: 0 }}>
              {r.areaLabel || m.label}
            </Tag>
          </Tooltip>
        )
      },
    },
    {
      // 判据是「会不会导致假绿」，不是「看着急不急」—— 写进 Tooltip，
      // 否则这一列会退化成谁写得凶谁排前面
      title: '优先级', dataIndex: 'severity', width: 90, align: 'center',
      render: v => {
        const m = SEVERITY_META[v]
        if (!m) return <Tooltip title="还没定 —— AI 判完就有了"><span style={{ color: '#c9cdd4' }}>待定</span></Tooltip>
        return (
          <Tooltip title="高=会导致假绿或让人做出错误决定；中=费事但绕得过去；低=纯体验">
            <Tag style={{ color: m.color, background: `${m.color}14`, border: 'none' }}>{m.label}</Tag>
          </Tooltip>
        )
      },
    },
    {
      // CC 报的类和平台判的类不一致时并排显示 —— 这是「他判断错了」唯一可统计的形状，
      // 藏进详情就等于没有。
      title: '类别', dataIndex: 'category', width: 140, align: 'center',
      render: (v, r) => {
        const m = CATEGORY_META[v]
        const rm = CATEGORY_META[r.reportedCategory]
        if (!m && !rm) return <span style={{ color: '#c9cdd4' }}>-</span>
        return (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
            <Tag style={{ color: (m || rm).color, background: (m || rm).bg, border: 'none', margin: 0 }}>
              {(m || rm).label}{!m && <span style={{ opacity: 0.65 }}> · 待定</span>}
            </Tag>
            {r.categoryMismatch && (
              <Tooltip title={`CC 报的是「${rm?.label}」，平台判为「${m?.label}」`}>
                <span style={{ fontSize: 11, color: '#ff7d00' }}>他报的是{rm?.label}</span>
              </Tooltip>
            )}
          </div>
        )
      },
    },
    {
      title: '状态', dataIndex: 'status', width: 118, align: 'center',
      render: (v, r) => {
        const m = STATUS_META[v] || { label: v, color: '#86909c', bg: 'rgba(0,0,0,0.04)' }
        const d = DECIDER_META[r.decidedBy]
        return (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
            <Tag style={{ color: m.color, background: m.bg, border: 'none', margin: 0 }}>{m.label}</Tag>
            {d && (
              <Tooltip title={d.tip}>
                <span style={{ fontSize: 11, color: '#86909c' }}>{d.label}判的</span>
              </Tooltip>
            )}
          </div>
        )
      },
    },
    {
      // 撞了几次 = 优先级最硬的信号（同一件事被不同会话反复撞到）
      title: '撞了几次', dataIndex: 'occurrences', width: 96, align: 'center',
      render: v => v > 1
        ? <Badge count={v} style={{ backgroundColor: '#ff7d00' }} />
        : <span style={{ color: '#c9cdd4' }}>1</span>,
    },
    timeColumn({ key: 'lastSeenAt', title: '最近一次', width: 120 }),
    {
      title: '来源', width: 230,
      render: (_, r) => (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 12 }}>
          <span style={{ color: '#4e5969' }}>
            {(() => {
              const sm = SOURCE_META[r.source] || SOURCE_META.human
              return (
                <Tag style={{ color: sm.color, background: sm.bg, border: 'none', fontSize: 11 }}>
                  {sm.short} · {r.reporter || '未命名'}
                </Tag>
              )
            })()}
          </span>
          <span style={{ color: '#86909c' }}>
            {r.toolName ? <code style={{ fontSize: 11 }}>{r.toolName}</code> : '—'}
            {r.projectName ? ` · ${r.projectName}` : ''}
          </span>
        </div>
      ),
    },
    {
      // 行内动作是**跑 AI**，不是「打开去人工处理」—— 人打开这一页的常态是看结论。
      // 要人动手的那几条自己带「等人拍板」标，点标题进抽屉走「人拍板」那一组按钮。
      title: '操作', width: 96, align: 'center',
      render: (_, r) => (
        <Button
          size="small" type="link" icon={<RobotOutlined />}
          loading={rowBusy === r.id} disabled={batch?.running}
          onClick={() => runRowAi(r)}
        >AI 处理</Button>
      ),
    },
  ]

  const ai = detail?.aiAnalysis

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: '#1d2129' }}>CC 反馈</h2>
        <span style={{ fontSize: 13, color: '#86909c' }}>
          外部 Claude Code 通过 <code>lum_report_feedback</code> 报回来的**平台自身**问题。
          处置结论会作为回音回到它那边（<code>lum_list_my_feedback</code> / <code>lum_next_duty</code>）
        </span>
      </div>

      <div style={{ display: 'flex', gap: 10, marginBottom: 12, padding: '12px 16px', borderRadius: 14 }}>
        <Input
          prefix={<SearchOutlined style={{ color: '#c9cdd4' }} />}
          placeholder="搜标题 / 正文 / 工具名..."
          value={keyword} onChange={e => setKeyword(e.target.value)}
          onPressEnter={() => { setPage(1); fetchList() }}
          allowClear style={{ width: 260 }}
        />
        <Select
          value={statusFilter} onChange={v => { setStatusFilter(v); setPage(1) }}
          style={{ width: 150 }} allowClear placeholder="状态"
          options={[
            { value: '__pending__', label: `待处理（${summary.pending ?? 0}）` },
            // 人真正要动手的只有这一撮，所以给它一个一等的位置
            { value: '__human__', label: `等人拍板（${summary.awaitingHuman ?? 0}）` },
            ...Object.entries(STATUS_META).map(([k, m]) => ({
              value: k, label: `${m.label}（${summary.byStatus?.[k] ?? 0}）`,
            })),
          ]}
        />
        <Select
          value={categoryFilter} onChange={v => { setCategoryFilter(v); setPage(1) }}
          style={{ width: 120 }} allowClear placeholder="类别"
          options={Object.entries(CATEGORY_META).map(([k, m]) => ({ value: k, label: m.label }))}
        />
        <Button icon={<ReloadOutlined />} onClick={fetchList}>刷新</Button>
        <div style={{ flex: 1 }} />
        {selected.length > 0 && (
          <span style={{ alignSelf: 'center', fontSize: 12, color: '#86909c' }}>
            选了 {selected.length} 条
            <a style={{ marginLeft: 6 }} onClick={() => setSelected([])}>清空</a>
          </span>
        )}
        <Tooltip title={selected.length
          ? `把勾选的 ${selected.length} 条交给 AI 判（顺序跑，判不了的落到「等人拍板」）`
          : '不勾就是把「待处理」里全部交给 AI —— 最常见的动作是把积压一次推完'}>
          <Button
            type="primary" icon={<RobotOutlined />}
            loading={!!batch?.running} onClick={startBatch}
          >
            {batch?.running ? '正在批量处理…' : (selected.length ? `批量处理（${selected.length}）` : '批量处理全部待处理')}
          </Button>
        </Tooltip>
      </div>

      {/* 按域的计数块。需求要的是「一眼看出哪一块问题最多」，而一列 Tag 只做到
          「能查」—— 得把数摊开摆在这儿才看得出比例。
          只摆有数的：14 个域里今天有 3 个是 0 条，摆成 3 个点不动的按钮是噪声。
          但 **0 条本身是信息**（UI 脚本执行 / 环境与变量 / QA 仓对账 一条没有，
          说明那几块 CC 还没真用起来，不是"它们没问题"），所以尾巴上用一句灰字带过，
          不做成能点的东西。 */}
      {(() => {
        const by = summary.byArea || {}
        const hit = Object.keys(AREA_META)
          .filter(k => (by[k] || 0) > 0)
          .sort((a, b) => (by[b] || 0) - (by[a] || 0))
        const zero = Object.keys(AREA_META).filter(k => !(by[k] > 0))
        const none = by[AREA_NONE] || 0
        const chips = [
          // 「全部」故意不带数：它等于"不按域筛"，而当前列表的条数还受状态/类别/
          // 关键词影响，摆一个数上去就会和右边那些块加起来对不上
          { key: null, label: '全部', count: null },
          ...hit.map(k => ({ key: k, label: AREA_META[k].label, count: by[k] })),
          ...(none > 0 ? [{
            key: AREA_NONE, label: '待判域', count: none,
            tip: '还没判过属于哪一块。点「批量处理」交给 AI 就会填上 —— 它只填空的，不会盖掉人判过的',
          }] : []),
        ]
        if (!hit.length && !none) return null
        return (
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginBottom: 12 }}>
            {chips.map(c => {
              const on = areaFilter === c.key || (c.key === null && !areaFilter)
              const chip = (
                <a
                  key={c.key ?? '__all__'}
                  onClick={() => { setAreaFilter(c.key); setPage(1) }}
                  style={{
                    fontSize: 12, lineHeight: '22px', padding: '0 10px', borderRadius: 11,
                    color: on ? '#1d2129' : '#4e5969',
                    background: on ? 'rgba(78,138,240,0.12)' : 'transparent',
                    border: `1px solid ${on ? 'rgba(78,138,240,0.35)' : 'rgba(0,0,0,0.06)'}`,
                  }}
                >
                  {c.label}
                  {c.count != null && (
                    <span style={{ marginLeft: 5, color: on ? '#4e8af0' : '#86909c' }}>{c.count}</span>
                  )}
                </a>
              )
              return c.tip ? <Tooltip key={c.key} title={c.tip}>{chip}</Tooltip> : chip
            })}
            {zero.length > 0 && (
              <span style={{ fontSize: 12, color: '#c9cdd4', marginLeft: 4 }}>
                · 0 条：{zero.map(k => AREA_META[k].label).join(' / ')}
              </span>
            )}
          </div>
        )
      })()}

      {/* 进度：一条条判是几分钟的事，没有进度条人会以为它卡死了然后重复点 */}
      {(batch?.running || batch?.finishedAt) && (
        <Alert
          type={batch?.running ? 'info' : 'success'}
          style={{ marginBottom: 12 }} showIcon
          message={batch?.running
            ? `AI 正在批量处理：${batch.done + batch.failed} / ${batch.total}`
            : `上一批处理完了：判了 ${batch.done ?? 0} 条`
              + (batch.needsHuman ? `，${batch.needsHuman} 条 AI 说判不了` : '')
              + (batch.failed ? `，${batch.failed} 条没成` : '')}
          description={
            <div>
              <Progress
                percent={batch.total ? Math.round(((batch.done + batch.failed) / batch.total) * 100) : 0}
                size="small" status={batch?.running ? 'active' : 'normal'}
              />
              <span style={{ fontSize: 12, color: '#86909c' }}>
                顺序跑，不并发 —— 并发打模型会一起撞限流然后一起降级，那条降级通道对这种正文会回空，结果是一批全废。
                {batch.needsHuman ? '　判不了的在「等人拍板」里。' : ''}
                {batch.failed ? '　没成的多半是限流，重新点一次即可（已经判过的不会重判）。' : ''}
              </span>
            </div>
          }
        />
      )}

      <div style={{ borderRadius: 14, padding: 2 }}>
        <Table
          dataSource={items} columns={columns} rowKey="id" size="small" loading={loading}
          rowSelection={{
            selectedRowKeys: selected,
            onChange: setSelected,
            // 已经判过的也允许勾 —— 改了提示词想重判一批是正常需求
          }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  <div style={{ fontSize: 13, color: '#86909c', lineHeight: 1.9, textAlign: 'left', maxWidth: 560, margin: '0 auto' }}>
                    <div style={{ color: '#4e5969', fontWeight: 500 }}>CC 还没报过问题。</div>
                    它撞到平台自己的毛病时会走 <code>lum_report_feedback</code> 报到这里 ——
                    被测系统的缺陷走 <code>lum_submit_analysis</code>，
                    被测系统的反直觉行为走 <code>lum_add_project_note</code>，各有各的家。
                    <div style={{ marginTop: 6 }}>
                      想确认通道是通的：在连着本平台的 Claude Code 里让它调一次
                      <code> lum_report_feedback</code> —— 报进来的会**自动**交给 AI 分诊，
                      判不了的才会落到「等人拍板」等你。
                    </div>
                  </div>
                }
              />
            ),
          }}
          pagination={{
            current: page, pageSize, total, size: 'small',
            showTotal: t => `共 ${t} 条` + (summary.total ? ` / 全部 ${summary.total} 条` : ''),
            showSizeChanger: true, pageSizeOptions: [20, 50, 100],
            onChange: (p, ps) => { setPage(p); setPageSize(ps) },
          }}
        />
      </div>

      <Drawer
        title={detail?.title || '反馈详情'}
        open={!!detail} onClose={() => { setDetail(null); setAction(null) }} width={760}
      >
        <Spin spinning={detailLoading}>
          {detail && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {detail.needsHuman && (
                <Alert type="warning" showIcon
                  message="AI 说它判不了这条 —— 要你拍板"
                  description={
                    <div>
                      <div style={{ whiteSpace: 'pre-wrap' }}>{detail.needsHuman}</div>
                      <div style={{ marginTop: 6, fontSize: 12, color: '#86909c' }}>
                        下面「人拍板」那一组按钮就是出口：选一个动作、写回音、提交。
                        提交完这条就不再欠人什么了。
                      </div>
                    </div>
                  } />
              )}
              {detail.sampled && (
                <Alert type="info" showIcon
                  message="抽检样本（每 5 条抽 1）"
                  description="AI 判的「不需要处理」抽出来给人看一眼 —— **裁定已经生效**，这里不拦它，
                  复核只用来校准 AI 判得准不准。觉得判错了就直接改判。" />
              )}
              {detail.status === 'wont_fix' && (
                <Alert type="warning" showIcon
                  message={`这条是「不需要处理」${detail.decidedBy === 'ai' ? '（AI 判的）' : ''}`}
                  description={detail.decidedBy === 'ai'
                    ? '同指纹的后续上报会被这段回音当场短路。但这条是 AI 判的 —— CC 带上跟上次不同的现象重报一次会转成「等人拍板」，不会永久关死。'
                    : '同指纹的后续上报会被这段回音当场短路，而且人判的是终局（重报也不翻）。改主意的话，把状态改回「已认下」。'} />
              )}

              <div style={{ display: 'flex', flexDirection: 'column' }}>
                <Field label="状态">
                  <Tag style={{
                    color: STATUS_META[detail.status]?.color, background: STATUS_META[detail.status]?.bg, border: 'none',
                  }}>{detail.statusLabel || detail.status}</Tag>
                  {detail.severity && (
                    <span style={{ marginLeft: 8, fontSize: 12, color: SEVERITY_META[detail.severity]?.color }}>
                      严重度 {SEVERITY_META[detail.severity]?.label}
                    </span>
                  )}
                </Field>
                <Field label="类别">
                  {detail.categoryLabel || <span style={{ color: '#c9cdd4' }}>还没定类</span>}
                  {/* 还没定类时也要把 CC 自己报的那一类露出来：抽屉上第一个动作
                      就叫「认下并分类」，而「认下」认的正是这个值。不显示的话，
                      人得先关掉抽屉回列表上看一眼才知道自己在认什么。 */}
                  {!detail.category && detail.reportedCategoryLabel && (
                    <span style={{ marginLeft: 8, fontSize: 12, color: '#86909c' }}>
                      （上报时报的是「{detail.reportedCategoryLabel}」）
                    </span>
                  )}
                  {detail.categoryMismatch && (
                    <span style={{ marginLeft: 8, fontSize: 12, color: '#ff7d00' }}>
                      （CC 报的是「{detail.reportedCategoryLabel}」）
                    </span>
                  )}
                </Field>
                <Field label="范围">
                  {detail.areaLabel || AREA_META[detail.area]?.label || (
                    <span style={{ color: '#c9cdd4' }}>
                      还没判过属于哪一块
                      <span style={{ marginLeft: 6, fontSize: 12 }}>
                        （和「其它」不是一回事：那个是判过了、确实归不进任何一档）
                      </span>
                    </span>
                  )}
                </Field>
                <Field label="来源">
                  {`${(SOURCE_META[detail.source] || SOURCE_META.human).full} · ${detail.reporter || '未命名'}`}
                  {detail.projectName ? ` · ${detail.projectName}` : ''}
                </Field>
                {detail.toolName && <Field label="涉及工具"><code>{detail.toolName}</code></Field>}
                <Field label="撞了几次">
                  {detail.occurrences}
                  <span style={{ color: '#86909c', fontSize: 12 }}>
                    {'　'}首次 {formatTimeFull(detail.createdAt)}{'　'}最近 {formatTimeFull(detail.lastSeenAt)}
                  </span>
                </Field>
                {detail.handledBy && (
                  <Field label="处置">
                    {detail.handledBy} · {formatTimeFull(detail.handledAt)}
                    {DECIDER_META[detail.decidedBy] && (
                      <Tooltip title={DECIDER_META[detail.decidedBy].tip}>
                        <Tag style={{
                          marginLeft: 8, fontSize: 11, border: 'none',
                          color: DECIDER_META[detail.decidedBy].color,
                          background: DECIDER_META[detail.decidedBy].bg,
                        }}>{DECIDER_META[detail.decidedBy].label}判的</Tag>
                      </Tooltip>
                    )}
                  </Field>
                )}
              </div>

              <Block title="正文"><pre style={preStyle}>{detail.body}</pre></Block>

              {detail.evidence && Object.keys(detail.evidence).length > 0 && (
                <Block title="证据">
                  <div style={{ display: 'flex', flexDirection: 'column' }}>
                    {detail.evidence.expected && <Field label="说好的">{detail.evidence.expected}</Field>}
                    {detail.evidence.actual && <Field label="实际">{detail.evidence.actual}</Field>}
                    {detail.evidence.repro && <Field label="怎么复现">{detail.evidence.repro}</Field>}
                    {detail.evidence.refs?.length > 0 && <Field label="关联">{detail.evidence.refs.join('、')}</Field>}
                  </div>
                </Block>
              )}

              {detail.resolution && (
                <Block title={`回音（CC 那边看到的就是这段${detail.ackAt ? '，已取走' : '，还没取走'}）`}>
                  <pre style={{ ...preStyle, borderColor: 'rgba(14,165,160,0.25)' }}>{detail.resolution}</pre>
                </Block>
              )}

              <Block
                title="AI 处置（判据 + 裁定，直接生效）"
                extra={
                  <Button size="small" icon={<RobotOutlined />} loading={analyzing} onClick={runAnalyze}>
                    {ai ? '重新判一次' : 'AI 处理'}
                  </Button>
                }
              >
                {!ai && (
                  <div style={{ fontSize: 12, color: '#c9cdd4' }}>
                    还没判过（自动分诊那次可能限流了）。点右上角让它判 —— 它看的是**这个工具的描述和实现源码 + 全部 63 个工具的清单**，
                    所以能判出「平台其实有这个能力、是他没找对方法」这一类。判不了的会落成「等人拍板」。
                  </div>
                )}
                {ai?.parseFailed && <pre style={preStyle}>{ai.raw}</pre>}
                {ai && !ai.parseFailed && (
                  <div style={{ border: '1px solid rgba(0,0,0,0.04)', borderRadius: 12, padding: '10px 14px' }}>
                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                      <Field label="判为">
                        {CATEGORY_META[ai.category]?.label || ai.category || '-'}
                        {ai.severity && <span style={{ marginLeft: 8, color: SEVERITY_META[ai.severity]?.color }}>优先级 {SEVERITY_META[ai.severity]?.label}</span>}
                        {ai.verdict && (
                          <span style={{ marginLeft: 8, color: '#86909c', fontSize: 12 }}>
                            已置为「{STATUS_META[ai.verdict]?.label || ai.verdict}」
                          </span>
                        )}
                      </Field>
                      {ai.reasoning && <Field label="判据">{ai.reasoning}</Field>}
                      {ai.risk && <Field label="不处理会怎样">{ai.risk}</Field>}
                      {ai.fixHint && <Field label="改哪儿">{ai.fixHint}</Field>}
                      {ai.needsHuman && <Field label="它判不了什么">{ai.needsHuman}</Field>}
                      {ai.coercedFromDone && <Field label="口径修正">{ai.coercedFromDone}</Field>}
                    </div>
                    <div style={{ marginTop: 10, fontSize: 11, color: '#c9cdd4' }}>
                      回音在上面「回音」那一块（CC 那边看到的就是它）。
                      {ai.model ? `\u3000· ${ai.model}` : ''}{ai.at ? `\u3000· ${formatTimeFull(ai.at)}` : ''}
                    </div>
                  </div>
                )}
              </Block>

              {/* 人拍板：**不是主路**。留着是因为 AI 判不了的那几条得有出口，
                  以及人要改判 AI 的时候。标题写清这件事，否则下一个人会以为
                  每条都得从这儿走一遍。 */}
              <Block title={detail.needsHuman ? '人拍板（这条在等你）' : '人拍板（AI 判不了、或你要改判时才用）'}>
                <Space wrap>
                  {ACTIONS.map(a => (
                    <Tooltip key={a.status} title={a.tip}>
                      <Button
                        danger={a.danger}
                        type={a.status === 'done' ? 'primary' : 'default'}
                        disabled={detail.status === a.status}
                        onClick={() => openAction(a)}
                      >{a.label}</Button>
                    </Tooltip>
                  ))}
                </Space>
              </Block>
            </div>
          )}
        </Spin>
      </Drawer>

      <Modal
        title={action ? `置为「${STATUS_META[action.status].label}」` : ''}
        open={!!action} onCancel={() => setAction(null)} onOk={submitAction}
        okText="提交" width={620} destroyOnHidden
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          {action?.needsCategory && (
            <>
              <Form.Item
                name="category" label="类别"
                rules={[{ required: true, message: '认下一条反馈时必须定类' }]}
                extra="bug=说了会做 A 实际做了 B；优化=行为没错但代价不合理/容易把人带错路；需求=平台今天没有这个能力"
              >
                <Select options={Object.entries(CATEGORY_META).map(([k, m]) => ({ value: k, label: m.label }))} />
              </Form.Item>
              <Form.Item
                name="severity" label="严重度"
                extra="判据是会不会导致假绿或让人做出错误决定：会 → 高；只是费事、绕得过去 → 中；纯体验 → 低"
              >
                <Select allowClear options={Object.entries(SEVERITY_META).map(([k, m]) => ({ value: k, label: m.label }))} />
              </Form.Item>
            </>
          )}
          {!action?.needsDuplicate && (
            <Form.Item
              name="area" label="范围（坏的是哪一块）"
              extra="和「涉及工具」不是一回事：工具是他手按在哪儿，范围是坏的那一块。留空 = 不改动现有值"
            >
              <Select
                allowClear showSearch optionFilterProp="label"
                placeholder="不改"
                options={Object.entries(AREA_META).map(([k, m]) => ({ value: k, label: m.label }))}
              />
            </Form.Item>
          )}
          {action?.needsResolution && (
            <Form.Item
              name="resolution" label={action.resolutionLabel}
              rules={[{ required: true, message: '必须写回音 —— 这条通道的全部价值就在回音上' }]}
            >
              <TextArea rows={7} placeholder={action.resolutionPlaceholder} />
            </Form.Item>
          )}
          {action?.needsDuplicate && (
            <Form.Item name="duplicateOf" label="并到哪一条（反馈 id）" rules={[{ required: true, message: '标为重复必须说明并到哪一条' }]}>
              <Input placeholder="从另一条的详情地址里复制 id" />
            </Form.Item>
          )}
        </Form>
      </Modal>

    </div>
  )
}
