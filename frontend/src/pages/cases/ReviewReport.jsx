import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Table, Tag, Button, Empty, Spin, Tooltip, message } from 'antd'
import { SearchOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../../utils/request'
import { useBranch } from '../../utils/branch'

// 模块审核报告。**这一页存在的理由是「覆盖缺口」**：
// 每条用例审核时提的模块级缺口，原来各存一份、散在 review_reason 里没人看得见，
// 而它是唯一指向"该补哪些用例"的东西。合并之后「越权被 3 条提到」就是一条清单。
//
// 状态跟着轮次走（未审 / 整改中 / 通过），因为 CC 会看完整改、再提交、AI 再审 ——
// 只有当前值的话跟进不了。
const STATUS = {
  未审: { color: undefined, hint: '这个模块还没跑过 AI 审核' },
  整改中: { color: 'error', hint: '有用例被打回或已提交整改待复审' },
  通过: { color: 'success', hint: '审过的用例都通过了' },
}

export default function ReviewReport() {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const [branchId] = useBranch(projectId)
  const [rows, setRows] = useState(null)
  const [loading, setLoading] = useState(false)
  const [reviewing, setReviewing] = useState(null)

  const load = useCallback(async () => {
    if (!branchId) return
    setLoading(true)
    try {
      const r = await api.get(`/projects/${projectId}/branches/${branchId}/review-report`)
      setRows(r.data?.modules || [])
    } catch { /* request.js 已提示 */ } finally { setLoading(false) }
  }, [projectId, branchId])

  useEffect(() => { load() }, [load])

  const runModule = async (mod) => {
    setReviewing(mod.module)
    try {
      const r = await api.post(`/projects/${projectId}/branches/${branchId}/ai-review/batch`,
        { folderId: mod.folderId })
      message.success(`${mod.module}：通过 ${r.data.approved} / 打回 ${r.data.rejected}`)
      load()
    } catch (e) {
      message.error(e?.response?.data?.error?.message || '审核失败')
    } finally { setReviewing(null) }
  }

  const columns = [
    { title: '模块', dataIndex: 'module', width: 160,
      render: (v, r) => (
        <a onClick={() => navigate(`/projects/${projectId}/cases?module=${encodeURIComponent(v)}`)}>
          {v}<span style={{ color: '#86909c', marginLeft: 6, fontSize: 12 }}>({r.total})</span>
        </a>
      ) },
    { title: '状态', dataIndex: 'status', width: 96, align: 'center',
      render: v => <Tooltip title={STATUS[v]?.hint}><Tag color={STATUS[v]?.color} style={{ margin: 0 }}>{v}</Tag></Tooltip> },
    { title: '通过 / 打回 / 待复审', width: 160, align: 'center',
      render: (_, r) => (
        <span style={{ fontSize: 12 }}>
          <b style={{ color: '#0ea5a0' }}>{r.approved}</b> / <b style={{ color: '#e8453c' }}>{r.rejected}</b>
          {' / '}<b style={{ color: '#fa8c16' }}>{r.resubmitted}</b>
        </span>
      ) },
    { title: '未审', dataIndex: 'notReviewed', width: 68, align: 'center',
      render: (v, r) => <span style={{ color: '#86909c', fontSize: 12 }}>{v + r.pending}</span> },
    { title: '均分', dataIndex: 'avgScore', width: 60, align: 'center',
      render: v => v == null ? <span style={{ color: '#c9cdd4' }}>—</span>
        : <b style={{ color: v >= 85 ? '#0ea5a0' : v >= 70 ? '#4e8af0' : '#faad14' }}>{v}</b> },
    // 缺口按**话题**归并（后端 review/gap_merge.py），不按字面 —— LLM 每轮措辞都不一样，
    // 按字面比的结果是同一件事（越权）拆成三条各 1×，而这一列的信息量全在 count 上。
    // 各条原话放 tooltip：合并了就得能核对"这几条到底是不是一件事"，否则归并是黑箱。
    { title: '覆盖缺口（按话题归并 · 被提到几次）', dataIndex: 'gaps',
      render: (gaps, r) => !gaps?.length ? <span style={{ color: '#c9cdd4' }}>—</span> : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          {gaps.map((g, i) => (
            <Tooltip key={i} title={
              <div style={{ fontSize: 12 }}>
                <div>提到它的用例：{g.cases.join('、')}</div>
                {(g.phrasings || []).length > 1 && (
                  <div style={{ marginTop: 4 }}>各条原话：
                    {g.phrasings.map((t, j) => <div key={j}>· {t}</div>)}
                  </div>
                )}
              </div>
            }>
              <span style={{ fontSize: 12 }}>
                <Tag color={g.count > 1 ? 'warning' : undefined}
                  style={{ fontSize: 10, margin: '0 6px 0 0' }}>{g.count}×</Tag>
                <b>{g.display || g.topic || g.gap}</b>
                {/* 话题命中了才把原话作为副文本跟在后面；没命中时 display 本身就是原话，
                    再跟一遍就是重复（而话题键那串实词签名不该露给人看）。 */}
                {g.matchedTopic ? <span style={{ color: '#86909c', marginLeft: 6 }}>{g.gap}</span> : null}
              </span>
            </Tooltip>
          ))}
          {r.gapsTotal > gaps.length && (
            <span style={{ fontSize: 11, color: '#86909c' }}>
              还有 {r.gapsTotal - gaps.length} 类没显示（只列前 {gaps.length} 个）
            </span>
          )}
        </div>
      ) },
    { title: '操作', width: 92, align: 'center',
      render: (_, r) => (
        <Button size="small" icon={<SearchOutlined />} loading={reviewing === r.module}
          onClick={() => runModule(r)}>审核</Button>
      ) },
  ]

  if (!branchId) return <Card><Empty description="请先选择分支" /></Card>

  return (
    <Card
      title={<span>审核报告<span style={{ fontSize: 12, color: '#86909c', marginLeft: 10 }}>
        按模块看审核进展；覆盖缺口按话题归并（越权/并发/边界…）—— 被提到多次的说明这个模块真缺这一类；悬停看各条原话
      </span></span>}
      extra={<Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>}
      styles={{ body: { padding: rows?.length ? 0 : 24 } }}
    >
      {loading && !rows ? <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div> : (
        <Table size="small" rowKey="module" columns={columns} dataSource={rows || []}
          pagination={false} loading={loading}
          locale={{ emptyText: '这个分支下还没有用例' }} />
      )}
    </Card>
  )
}
