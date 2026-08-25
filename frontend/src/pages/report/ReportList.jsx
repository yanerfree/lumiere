import { useState, useEffect, useCallback } from 'react'
import { TimeCell } from '../../utils/timeCol'
import { Button, Space, Spin, Empty, Input, Pagination, Modal, Tooltip, message } from 'antd'
import { SearchOutlined, ReloadOutlined, DownloadOutlined, DeleteOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../utils/request'
import { useBranch } from '../../utils/branch'

// 「入口」= 从哪儿点的；「执行」= 真跑的什么。两件事，分两列，别再共用「类型」这个词。
const ENTRY_LABELS = {
  plan: '测试计划', api_test: '接口测试', scenario_test: '功能场景', adhoc: '批量执行',
}
const EXEC_LABELS = { ui: 'UI', api: '接口', mixed: '混合' }

// 一份报告"完了没有"原来只看 completedAt 有没有值 —— 没有就一律写「执行中」。
// 于是库里那条 8-12 的报告在页面上"执行了 12 天"，而它其实是**手动计划在等人录结果**。
// 「在跑」只能等，「等人录」是待办 —— 混成一个词，那条待办就永远不会被认领。
const STATUS_META = {
  completed: { label: '已完成', color: '#0ea5a0', bg: 'rgba(14,165,160,0.12)',
               border: 'rgba(14,165,160,0.28)', dot: '#0ea5a0' },
  running: { label: '执行中', color: '#d48806', bg: 'rgba(250,173,20,0.14)',
             border: 'rgba(250,173,20,0.3)', dot: '#faad14',
             tip: '还在跑，刷新看进度' },
  pending_manual: { label: '待录入', color: '#4e8af0', bg: 'rgba(78,138,240,0.10)',
                    border: 'rgba(78,138,240,0.28)', dot: '#4e8af0',
                    tip: (r) => `${r.pendingManual || 0} 条手工用例等着人录结果 —— 点进报告逐条录，录完自动收尾。这不是"在跑"，是待办。` },
  stalled: { label: '已中断', color: '#86909c', bg: 'rgba(0,0,0,0.05)',
             border: 'rgba(0,0,0,0.10)', dot: '#c9cdd4',
             tip: (r) => `该自动跑完的没跑完，也没有进程在跑了（重启 / 被杀 / 报错退出都长这样）：还差 ${r.pendingAuto || 0} 条。重新执行一次，或者把这份报告删掉。` },
  done_unsealed: { label: '已完成', color: '#0ea5a0', bg: 'rgba(14,165,160,0.12)',
                   border: 'rgba(14,165,160,0.28)', dot: '#0ea5a0',
                   tip: '每条用例都有结果了，只是收尾那一步没落库（completed_at 为空）。结果本身是全的。' },
}

function fmt(ms) {
  if (!ms && ms !== 0) return '-'
  if (ms < 1000) return ms + 'ms'
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's'
  return Math.floor(ms / 60000) + 'm ' + Math.round((ms % 60000) / 1000) + 's'
}

function rateColor(v) {
  if (v >= 95) return '#0ea5a0'
  if (v >= 80) return '#faad14'
  return '#e8453c'
}

const th = { fontSize: 12, color: '#86909c', fontWeight: 500, whiteSpace: 'nowrap' }

export default function ReportList() {
  const navigate = useNavigate()
  const { projectId } = useParams()
  const [globalBranchId] = useBranch(projectId)
  const [reports, setReports] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [typeFilter, setTypeFilter] = useState('')

  const fetchReports = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    try {
      let url = `/projects/${projectId}/reports?page=${page}&pageSize=${pageSize}`
      if (typeFilter) url += `&reportType=${typeFilter}`
      if (globalBranchId) url += `&branchId=${globalBranchId}`
      const res = await api.get(url)
      setReports(res.data || [])
      setTotal(res.pagination?.total || 0)
    } catch { /* */ } finally { setLoading(false) }
  }, [projectId, page, pageSize, typeFilter, globalBranchId])

  useEffect(() => { fetchReports() }, [fetchReports])

  const handleExport = async (e, reportId) => {
    e.stopPropagation()
    try {
      const blob = await api.download(`/projects/${projectId}/reports/${reportId}/export/excel`)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `report-${reportId}.xlsx`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      message.error('导出失败')
    }
  }

  const handleDelete = (e, reportId, planName) => {
    e.stopPropagation()
    Modal.confirm({
      title: '确认删除',
      content: `确定删除「${planName}」的执行报告？删除后不可恢复。`,
      okText: '删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api.del(`/projects/${projectId}/reports/${reportId}`)
          message.success('删除成功')
          fetchReports()
        } catch (err) {
          message.error(err?.response?.data?.error?.message || err?.message || '删除失败')
        }
      },
    })
  }

  const filtered = keyword
    ? reports.filter(r => (r.reportName || r.planName || '').toLowerCase().includes(keyword.toLowerCase()))
    : reports

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 70px)' }}>
      {/* Toolbar */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, flexShrink: 0 }}>
        <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0, color: '#1d2129' }}>测试报告</h2>
        <Space size={8}>
          <Space size={0}>
            {[
              { key: '', label: '全部' },
              { key: 'plan', label: '测试计划' },
              { key: 'api_test', label: '接口测试' },
              { key: 'scenario_test', label: '功能场景' },
              { key: 'adhoc', label: '批量执行' },
            ].map(f => (
              <Button key={f.key} size="small" type={typeFilter === f.key ? 'primary' : 'default'}
                onClick={() => { setTypeFilter(f.key); setPage(1) }}
                style={{ borderRadius: 0, ...(f.key === '' ? { borderRadius: '6px 0 0 6px' } : f.key === 'adhoc' ? { borderRadius: '0 6px 6px 0' } : {}) }}>
                {f.label}
              </Button>
            ))}
          </Space>
          <Input
            prefix={<SearchOutlined style={{ color: '#c9cdd4' }} />}
            placeholder="搜索报告名称"
            value={keyword} onChange={e => setKeyword(e.target.value)}
            allowClear style={{ width: 200 }} size="small"
          />
          <Button size="small" icon={<ReloadOutlined />} onClick={fetchReports} loading={loading}>刷新</Button>
        </Space>
      </div>

      {loading ? <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spin /></div> :
        filtered.length === 0 ? <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Empty description="暂无报告" /></div> : <>
        {/* Table */}
        <div style={{ background: 'var(--panel-bg)', border: 'none', borderRadius: 16, backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)', flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'center', padding: '0 16px', height: 36, background: 'var(--table-header-bg)', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0 }}>
            {/* 名称列原来 flex:4 + maxWidth 520，右边九列被挤成一坨，而报告名
                长的那几条本来就靠 tooltip 看全 —— 宽度给了它也读不完整。
                收到 340 之后右侧每一列都松了一截。 */}
            <div style={{ flex: 3, maxWidth: 440, ...th }}>报告名称</div>
            <div style={{ flex: 1 }} />
            {/* 「入口」和「跑的什么」是两件事，此前挤在一列叫「类型」——
                于是报告页清一色「接口测试」，用例页清一色 UI，看着像互相打架。 */}
            <div style={{ width: 72, textAlign: 'center', flexShrink: 0, ...th }}>入口</div>
            <div style={{ width: 56, textAlign: 'center', flexShrink: 0, ...th }}>执行</div>
            <div style={{ width: 84, textAlign: 'center', flexShrink: 0, ...th }}>环境</div>
            <div style={{ width: 88, textAlign: 'center', flexShrink: 0, ...th }}>状态</div>
            {/* 图例放表头 —— 原来每一行都印一遍「通过/失败/总计」，10px 还折成两行 */}
            <div style={{ width: 112, textAlign: 'center', flexShrink: 0, ...th }}>
              结果 <span style={{ color: '#c9cdd4', fontWeight: 400 }}>通过/失败/总</span>
            </div>
            <div style={{ width: 64, textAlign: 'center', flexShrink: 0, ...th }}>通过率</div>
            <div style={{ width: 56, textAlign: 'right', flexShrink: 0, ...th }}>耗时</div>
            <div style={{ width: 104, textAlign: 'center', flexShrink: 0, ...th }}>执行时间</div>
            <div style={{ width: 84, textAlign: 'center', flexShrink: 0, ...th }}>操作</div>
          </div>
          {/* Body */}
          <div style={{ flex: 1, overflow: 'auto' }}>
            {filtered.map(r => {
              const isCompleted = !!r.completedAt
              return (
                <div key={r.id}
                  onClick={() => navigate(`/projects/${projectId}/reports/${r.id}`)}
                  style={{
                    display: 'flex', alignItems: 'center', padding: '0 16px', height: 44,
                    borderBottom: '1px solid rgba(0,0,0,0.04)', cursor: 'pointer', transition: 'background .12s',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(0,0,0,0.02)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  {/* Report name */}
                  <div style={{ flex: 3, maxWidth: 440, minWidth: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Tooltip title={r.reportName || r.planName} placement="topLeft" mouseEnterDelay={0.3}>
                      <span style={{ fontWeight: 500, fontSize: 13, color: '#1d2129', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {r.reportName || r.planName}
                      </span>
                    </Tooltip>
                  </div>
                  {/* 余量放在名称列后面，位置必须和表头里那个 spacer 一致，否则列错位 */}
                  <div style={{ flex: 1 }} />

                  {/* 入口：从哪儿发起的 */}
                  <div style={{ width: 72, textAlign: 'center', flexShrink: 0 }}>
                    <Tooltip title="从哪个入口发起的这次执行">
                      <span style={{
                        fontSize: 11, padding: '1px 6px', borderRadius: 6,
                        background: r.reportType === 'adhoc' ? 'rgba(250,173,20,0.08)' : r.reportType === 'scenario_test' ? 'rgba(14,165,160,0.06)' : 'rgba(78,138,240,0.06)',
                        color: r.reportType === 'adhoc' ? '#ff7d00' : r.reportType === 'scenario_test' ? '#0ea5a0' : '#4e8af0',
                      }}>
                        {ENTRY_LABELS[r.reportType] || '测试计划'}
                      </span>
                    </Tooltip>
                  </div>

                  {/* 执行方式：跑的是 UI 脚本还是接口场景 */}
                  <div style={{ width: 56, textAlign: 'center', flexShrink: 0 }}>
                    {r.execKind ? (
                      <span style={{
                        fontSize: 11, padding: '1px 6px', borderRadius: 6,
                        background: r.execKind === 'ui' ? '#f5f0ff' : r.execKind === 'mixed' ? 'rgba(250,173,20,0.08)' : '#e0f7f6',
                        color: r.execKind === 'ui' ? '#7c5cbf' : r.execKind === 'mixed' ? '#ff7d00' : '#0ea5a0',
                      }}>
                        {EXEC_LABELS[r.execKind]}
                      </span>
                    ) : (
                      <Tooltip title="这份报告生成时还没记录执行方式，从计划和执行痕迹里都推不出来">
                        <span style={{ fontSize: 11, color: '#c9cdd4' }}>—</span>
                      </Tooltip>
                    )}
                  </div>

                  {/* Environment —— 空着要说清是为什么空。
                      接口执行那条通道以前压根没把选中的环境写进报告（库里 24/24 条
                      都是 NULL），于是这一列半张表是「-」，看着像功能坏了。
                      现在新报告会记；老报告补不出来 —— 编一个"默认环境"比留白更坏。 */}
                  <div style={{ width: 84, textAlign: 'center', flexShrink: 0 }}>
                    {r.environmentName ? (
                      <span style={{ fontSize: 12, color: '#86909c' }}>
                        {r.environmentName}
                      </span>
                    ) : (
                      <Tooltip title="这次执行没记下环境（08-24 之前的接口执行不写这一项）。之后跑的都会有。">
                        <span style={{ fontSize: 11, color: '#c9cdd4', borderBottom: '1px dotted #e5e6eb' }}>—</span>
                      </Tooltip>
                    )}
                  </div>

                  {/* Status */}
                  <div style={{ width: 88, textAlign: 'center', flexShrink: 0 }}>
                    {(() => {
                      const st = STATUS_META[r.status] || STATUS_META[isCompleted ? 'completed' : 'running']
                      const tip = typeof st.tip === 'function' ? st.tip(r) : st.tip
                      const tag = (
                        <span style={{
                          display: 'inline-flex', alignItems: 'center', gap: 4,
                          fontSize: 11, padding: '2px 8px', borderRadius: 12,
                          background: st.bg, border: `1px solid ${st.border}`, color: st.color,
                        }}>
                          <span style={{ width: 6, height: 6, borderRadius: '50%', background: st.dot }} />
                          {st.label}
                        </span>
                      )
                      return tip ? <Tooltip title={tip}>{tag}</Tooltip> : tag
                    })()}
                  </div>

                  {/* Results */}
                  <div style={{ width: 112, textAlign: 'center', fontSize: 12, fontFamily: 'var(--font-mono)', flexShrink: 0 }}>
                    <span style={{ color: '#0ea5a0' }}>{r.passed}</span>
                    <span style={{ color: '#c9cdd4' }}> / </span>
                    <span style={{ color: '#e8453c' }}>{r.failed + r.error}</span>
                    <span style={{ color: '#c9cdd4' }}> / </span>
                    <span style={{ color: '#4e5969' }}>{r.totalScenarios}</span>
                  </div>

                  {/* Pass rate */}
                  <div style={{ width: 64, textAlign: 'center', flexShrink: 0 }}>
                    {r.passRate != null ? (
                      <span style={{ fontSize: 13, fontWeight: 600, fontFamily: 'var(--font-mono)', color: rateColor(r.passRate) }}>
                        {r.passRate}%
                      </span>
                    ) : <span style={{ fontSize: 11, color: '#c9cdd4' }}>-</span>}
                  </div>

                  {/* Duration */}
                  <div style={{ width: 56, textAlign: 'right', fontSize: 12, fontFamily: 'var(--font-mono)', color: '#86909c', flexShrink: 0 }}>
                    {fmt(r.totalDurationMs)}
                  </div>

                  {/* 执行时间：独立一列，紧挨操作列 */}
                  <div style={{ width: 104, textAlign: 'center', flexShrink: 0 }}>
                    <TimeCell value={r.executedAt} />
                  </div>

                  {/* Actions */}
                  <div style={{ width: 84, display: 'flex', justifyContent: 'center', gap: 0, flexShrink: 0 }}>
                    <Button type="text" size="small" style={{ fontSize: 12, color: '#0ea5a0' }}
                      onClick={e => handleExport(e, r.id)}>导出</Button>
                    <Button type="text" size="small" danger style={{ fontSize: 12 }}
                      onClick={e => handleDelete(e, r.id, r.reportName || r.planName)}>删除</Button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* Pagination */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0 2px', flexShrink: 0 }}>
          <span style={{ fontSize: 12, color: '#86909c' }}>共 {total} 条</span>
          <Pagination current={page} total={total} pageSize={pageSize} size="small"
            showSizeChanger showQuickJumper
            pageSizeOptions={[10, 20, 50, 100]}
            onChange={(p, ps) => { if (ps !== pageSize) { setPageSize(ps); setPage(1) } else { setPage(p) } }} />
        </div>
        </>
      }
    </div>
  )
}
