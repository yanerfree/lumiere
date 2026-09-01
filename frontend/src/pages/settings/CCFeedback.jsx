/*
 * CC 反馈 —— 外部 Claude Code 报回来的**平台自身**问题的处理面。
 *
 * 全局，不挂项目（判据见 docs/cc-feedback-channel.md §2）：一条平台缺陷不该
 * 按项目分成 N 条，处理方也只有维护者一拨。项目只当来源线索显示。
 *
 * 这一页的重点不是"看"，是**回音**：done / wont_fix 必须写回复，后端硬校验。
 * 判「不需要处理」而不写正确做法，等于让 CC 下一轮照原样再撞一次 ——
 * 而平台这边只会静默 +1，两边都以为对方在动。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Table, Tag, Input, Select, Space, Button, Drawer, Modal, Form, message,
  Tooltip, Empty, Alert, Spin, Badge,
} from 'antd'
import { SearchOutlined, ReloadOutlined, PlusOutlined, RobotOutlined } from '@ant-design/icons'
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

// 来源三分，**别把 import 并进「页面录入」**：这三个数的比例回答的是
// 「通道到底有没有替掉人肉搬运」—— import 是通道开通前攒的存量，
// 并进页面那一档，这个问题就永远查不出来了。
const SOURCE_META = {
  cc: { short: 'CC', full: '外部 Claude Code', color: '#7c5cbf', bg: 'rgba(124,92,191,0.08)' },
  import: { short: '导入', full: '通道开通前的存量（汇总文档导入）', color: '#0fa47f', bg: 'rgba(15,164,127,0.08)' },
  human: { short: '页面', full: '页面录入', color: '#86909c', bg: 'rgba(0,0,0,0.04)' },
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
  const [keyword, setKeyword] = useState('')

  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [action, setAction] = useState(null)   // 当前弹出的处置动作
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm()
  const [createForm] = Form.useForm()

  const fetchList = useCallback(async () => {
    setLoading(true)
    try {
      const p = new URLSearchParams()
      p.append('page', page)
      p.append('pageSize', pageSize)
      if (statusFilter === '__pending__') p.append('pendingOnly', 'true')
      else if (statusFilter) p.append('status', statusFilter)
      if (categoryFilter) p.append('category', categoryFilter)
      if (keyword) p.append('keyword', keyword)
      const res = await api.get(`/cc-feedback?${p.toString()}`)
      setItems(res.data.items || [])
      setTotal(res.data.total || 0)
      setSummary(res.data.summary || {})
    } catch {
      // 读这一类的失败**不再自己弹** —— request() 已经弹过一次，而且弹的是
      // 服务端那句（「无权限执行此操作」/ 具体报错），比再叠一句「加载失败」
      // 有用。这一页也只加载这一件事，不存在弄不清是哪个请求挂了。
      setItems([]); setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, statusFilter, categoryFilter, keyword])

  useEffect(() => { fetchList() }, [fetchList])

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
      setDetail(d => ({ ...d, aiAnalysis: res.data.aiAnalysis }))
      message.success('分析完成 —— 这是建议，采纳与否你定')
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

  const submitCreate = async () => {
    const v = await createForm.validateFields()
    try {
      const res = await api.post('/cc-feedback', {
        title: v.title, body: v.body, category: v.category, toolName: v.toolName,
        evidence: (v.expected || v.actual) ? { expected: v.expected, actual: v.actual } : undefined,
      }, { silent: true })
      setCreating(false); createForm.resetFields()
      message.success(res.data?.merged ? `并到已有的一条上（第 ${res.data.occurrences} 次）` : '已记录')
      fetchList()
    } catch (err) {
      // 闸门的拒绝理由本身是设计的一部分，原样显示 —— 只说「失败」等于让人猜。
      // err.detail 就是后端拼的 why + howTo（见 utils/request.js）。
      message.error(`${err?.message || '提交失败'}${err?.detail ? ` ${err.detail}` : ''}`)
    }
  }

  const columns = [
    {
      title: '状态', dataIndex: 'status', width: 110, align: 'center',
      render: v => {
        const m = STATUS_META[v] || { label: v, color: '#86909c', bg: 'rgba(0,0,0,0.04)' }
        return <Tag style={{ color: m.color, background: m.bg, border: 'none' }}>{m.label}</Tag>
      },
    },
    {
      // CC 报的类和平台判的类不一致时并排显示 —— 这是「他判断错了」唯一可统计的形状，
      // 藏进详情就等于没有。
      title: '类别', dataIndex: 'category', width: 150, align: 'center',
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
      title: '标题', dataIndex: 'title', ellipsis: true,
      render: (v, r) => (
        <a style={{ color: '#1d2129', fontWeight: 500 }} onClick={() => openDetail(r)} title={v}>
          {v}
          {r.reopenedFrom && (
            <Tooltip title="这条是同一件事再次发生 —— 上一条已经结掉了，复发另起一行（结论已经过一次，复发是新信息）">
              <Tag style={{ marginLeft: 6, fontSize: 11, color: '#e8453c', background: 'rgba(232,69,60,0.1)', border: 'none' }}>复发</Tag>
            </Tooltip>
          )}
        </a>
      ),
    },
    {
      title: '来源', width: 260,
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
      // 撞了几次 = 优先级最硬的信号（同一件事被不同会话反复撞到）
      title: '撞了几次', dataIndex: 'occurrences', width: 100, align: 'center',
      render: v => v > 1
        ? <Badge count={v} style={{ backgroundColor: '#ff7d00' }} />
        : <span style={{ color: '#c9cdd4' }}>1</span>,
    },
    timeColumn({ key: 'lastSeenAt', title: '最近一次', width: 120 }),
    {
      title: '操作', width: 90, align: 'center',
      render: (_, r) => <a style={{ fontSize: 12, color: '#0ea5a0' }} onClick={() => openDetail(r)}>处理</a>,
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
        <Tooltip title="人也可以代录一条（走的是和 MCP 同一套闸门和归并，规矩不会分叉）">
          <Button icon={<PlusOutlined />} onClick={() => setCreating(true)}>手工录入</Button>
        </Tooltip>
      </div>

      <div style={{ borderRadius: 14, padding: 2 }}>
        <Table
          dataSource={items} columns={columns} rowKey="id" size="small" loading={loading}
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
                      <code> lum_report_feedback</code>，或者点右上角「手工录入」——
                      两条路走的是同一个 <code>report()</code>。
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
              {detail.status === 'wont_fix' && (
                <Alert type="warning" showIcon
                  message="这条是「不需要处理」"
                  description="同指纹的后续上报会被这段回音当场短路 —— CC 再报同一件事，收到的就是它。改主意的话，把状态改回「已认下」。" />
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
                <Field label="来源">
                  {`${(SOURCE_META[detail.source] || SOURCE_META.human).full} · ${detail.reporter || '未命名'}`}
                  {detail.projectName ? ` · ${detail.projectName}` : ''}
                </Field>
                {detail.toolName && <Field label="涉及工具"><code>{detail.toolName}</code></Field>}
                <Field label="撞了几次">
                  {detail.occurrences}
                  <span style={{ color: '#86909c', fontSize: 12 }}>
                    {'　'}首次 {formatTimeFull(detail.createdAt)}　最近 {formatTimeFull(detail.lastSeenAt)}
                  </span>
                </Field>
                {detail.handledBy && (
                  <Field label="处置">{detail.handledBy} · {formatTimeFull(detail.handledAt)}</Field>
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
                title="AI 分析（建议，不改状态）"
                extra={
                  <Button size="small" icon={<RobotOutlined />} loading={analyzing} onClick={runAnalyze}>
                    {ai ? '重新分析' : 'AI 分析'}
                  </Button>
                }
              >
                {!ai && <div style={{ fontSize: 12, color: '#c9cdd4' }}>还没跑过。它替你把这条读一遍并给出判类、严重度和回音草稿 —— 采纳与否你定</div>}
                {ai?.parseFailed && <pre style={preStyle}>{ai.raw}</pre>}
                {ai && !ai.parseFailed && (
                  <div style={{ border: '1px solid rgba(0,0,0,0.04)', borderRadius: 12, padding: '10px 14px' }}>
                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                      <Field label="判为">
                        {CATEGORY_META[ai.category]?.label || ai.category || '-'}
                        {ai.severity && <span style={{ marginLeft: 8, color: SEVERITY_META[ai.severity]?.color }}>严重度 {SEVERITY_META[ai.severity]?.label}</span>}
                        {ai.suggestedStatus && (
                          <span style={{ marginLeft: 8, color: '#86909c', fontSize: 12 }}>
                            建议置为「{STATUS_META[ai.suggestedStatus]?.label || ai.suggestedStatus}」
                          </span>
                        )}
                      </Field>
                      {ai.reasoning && <Field label="判据">{ai.reasoning}</Field>}
                      {ai.risk && <Field label="不处理会怎样">{ai.risk}</Field>}
                      {ai.suggestedResolution && <Field label="回音草稿">{ai.suggestedResolution}</Field>}
                    </div>
                    <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 10 }}>
                      <Button
                        size="small" type="primary" ghost
                        disabled={!ai.suggestedStatus}
                        onClick={() => {
                          const a = ACTIONS.find(x => x.status === ai.suggestedStatus)
                          if (!a) return message.warning('这条建议没给出可执行的状态')
                          openAction(a, { category: ai.category, severity: ai.severity, resolution: ai.suggestedResolution })
                        }}
                      >采纳到表单</Button>
                      <span style={{ fontSize: 11, color: '#c9cdd4' }}>
                        采纳只是把建议填进处置表单，还要你自己按提交　{ai.model ? `· ${ai.model}` : ''}
                      </span>
                    </div>
                  </div>
                )}
              </Block>

              <Block title="处置">
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

      <Modal
        title="手工录入一条反馈"
        open={creating} onCancel={() => setCreating(false)} onOk={submitCreate}
        okText="提交" width={680} destroyOnHidden
      >
        <Alert
          type="info" showIcon style={{ marginBottom: 12 }}
          message="走的是和 MCP 完全同一个入口"
          description="证据闸门、指纹归并、「不需要处理」短路对手工录入一样生效 —— 两条路各写一套校验，迟早会漂成两种规矩。"
        />
        <Form form={createForm} layout="vertical">
          <Form.Item name="title" label="标题" rules={[{ required: true, message: '一句话说清是什么毛病' }]}>
            <Input placeholder="例：lum_get_case 不返回 bugRefs" />
          </Form.Item>
          <Form.Item name="category" label="类别" rules={[{ required: true }]}>
            <Select options={Object.entries(CATEGORY_META).map(([k, m]) => ({ value: k, label: m.label }))} />
          </Form.Item>
          <Form.Item name="toolName" label="涉及工具 / 模块">
            <Input placeholder="例：lum_sync_orchestrated_scenario、审核队列、用例列表页" />
          </Form.Item>
          <Form.Item
            name="body" label="正文" rules={[{ required: true, message: '至少 40 字' }]}
            extra="写三段：①想干什么 ②平台实际怎么反应的（原始返回/报错抄一段）③期望它怎么反应"
          >
            <TextArea rows={8} />
          </Form.Item>
          <Form.Item name="expected" label="说好的是什么" extra="类别选「缺陷」时这两栏必填 —— 想不清楚的多半不是缺陷，是用法">
            <TextArea rows={2} />
          </Form.Item>
          <Form.Item name="actual" label="实际是什么">
            <TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
