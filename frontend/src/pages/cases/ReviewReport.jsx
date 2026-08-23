import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Table, Tag, Button, Empty, Spin, Tooltip, Drawer, Switch, Progress, Popconfirm, message } from 'antd'
import { ReloadOutlined, PauseCircleOutlined } from '@ant-design/icons'
import { api } from '../../utils/request'
import { useBranch } from '../../utils/branch'
import mdBold from '../../utils/mdBold'

// 审核报告：**一行一次审核**（review-spec §6）。
//
// 这页以前是"一行一个模块"，塞不进类型，而且点一行只是跳到筛好的用例列表 ——
// 那是筛选器，不叫报告。一眼看不出是审了整个模块还是抽了三条的报告，
// 过两周就没人信（§4）：挑三条好的一审，就能宣布模块没问题。
const KIND = {
  module_full:        { label: '模块全量', color: 'blue',    hint: '这个模块全部用例 + 体检。**只有这种能代表模块情况**' },
  module_incremental: { label: '模块增量', color: 'cyan',    hint: '只审没审过的和被打回的，其余沿用上次结论' },
  sample:             { label: '抽审',     color: 'default', hint: '只审勾选的这几条 —— 不能代表整个模块' },
  single:             { label: '单条',     color: 'default', hint: '详情页发起的单条审核' },
  checkup:            { label: '体检',     color: 'purple',  hint: '不跑用例，只看模块覆盖缺口' },
}

const STATUS = {
  queued:    { label: '排队中', color: 'default' },
  running:   { label: '进行中', color: 'processing' },
  paused:    { label: '已暂停', color: 'warning' },
  done:      { label: '已完成', color: 'success' },
  partial:   { label: '部分失败', color: 'error' },
  cancelled: { label: '已取消', color: 'default' },
}

// 「无法审核」要跟通过/打回分开显示。混进打回的话，「打回 7 条」里其实有 4 条
// 是环境没配 —— 人会去改 7 条没毛病的用例（§9）。
const VERDICT = {
  approved:     { label: '通过', color: '#0ea5a0' },
  rejected:     { label: '打回', color: '#e8453c' },
  inconclusive: { label: '无法审核', color: '#faad14' },
}

const RUN_STATE = {
  no_env: '没有可用环境', nothing_to_run: '没有可跑的产物', env_down: '环境挂了',
  script_bug: '脚本自己跑不起来', system_bug: '被测系统没通过（已开跟进单）', ok: '跑通了',
}

function fmt(t) {
  if (!t) return '—'
  const d = new Date(t)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ` +
    `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export default function ReviewReport() {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const [branchId] = useBranch(projectId)
  const [rows, setRows] = useState(null)
  const [queue, setQueue] = useState({})
  const [loading, setLoading] = useState(false)
  const [includeCC, setIncludeCC] = useState(false)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const base = `/projects/${projectId}/branches/${branchId}`

  const load = useCallback(async () => {
    if (!branchId) return
    setLoading(true)
    try {
      const r = await api.get(`${base}/ai-review/batches`, { params: { mine: !includeCC } })
      setRows(r.data?.batches || [])
      setQueue(r.data?.queue || {})
    } catch { /* request.js 已提示 */ } finally { setLoading(false) }
  }, [projectId, branchId, includeCC])

  useEffect(() => { load() }, [load])

  // 有在跑的就自动刷 —— 这页是"看结果"的地方，让人手动点刷新才看得到进度，
  // 等于把轮询这件事外包给人。没有活跃批次时不轮询，别白打接口。
  useEffect(() => {
    const active = (rows || []).some(r => r.status === 'running' || r.status === 'queued')
    if (!active) return
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [rows, load])

  const openDetail = async (id) => {
    setDetailLoading(true)
    setDetail({ batchId: id })
    try {
      const r = await api.get(`${base}/ai-review/batches/${id}`)
      setDetail(r.data)
    } catch { setDetail(null) } finally { setDetailLoading(false) }
  }

  const act = async (id, what) => {
    try {
      await api.post(`${base}/ai-review/batches/${id}/${what}`)
      message.success(what === 'cancel' ? '已取消' : '已继续')
      load()
    } catch (e) { message.error(e?.response?.data?.error?.message || '操作失败') }
  }

  const columns = [
    { title: '类型', dataIndex: 'kind', width: 92,
      render: v => <Tooltip title={KIND[v]?.hint}><Tag color={KIND[v]?.color} style={{ margin: 0 }}>{KIND[v]?.label || v}</Tag></Tooltip> },
    { title: '范围', dataIndex: 'scopeLabel', width: 170,
      render: (v, r) => <a onClick={() => openDetail(r.batchId)}>{v || '—'}</a> },
    { title: '环境', dataIndex: 'environment', width: 100,
      render: v => v || <span style={{ color: '#c9cdd4' }}>—</span> },
    { title: '发起人', dataIndex: 'actor', width: 90,
      render: (v, r) => <span style={{ fontSize: 12 }}>{v || '—'}
        {r.actorKind !== 'human' && <Tag style={{ marginLeft: 4, fontSize: 11 }}>CC</Tag>}</span> },
    { title: '状态 / 结果', width: 250,
      render: (_, r) => {
        const st = STATUS[r.status] || {}
        if (r.status === 'running' || r.status === 'queued') {
          return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Tag color={st.color} style={{ margin: 0 }}>{st.label}</Tag>
              <Progress percent={r.total ? Math.round(r.done * 100 / r.total) : 0}
                size="small" style={{ width: 90, margin: 0 }}
                format={() => `${r.done}/${r.total}`} />
              {r.current && <span style={{ fontSize: 11, color: '#86909c' }}>{r.current}</span>}
            </div>
          )
        }
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <Tag color={st.color} style={{ margin: 0 }}>{st.label}</Tag>
            {r.approved > 0 && <span style={{ fontSize: 12, color: VERDICT.approved.color }}>通过 {r.approved}</span>}
            {r.rejected > 0 && <span style={{ fontSize: 12, color: VERDICT.rejected.color }}>打回 {r.rejected}</span>}
            {/* 「无法审核」单独一列数字 —— 它既不是通过也不是打回，
                藏进任何一边都会让这批结论的含金量看不出来 */}
            {r.inconclusive > 0 && (
              <Tooltip title="没真跑成功（缺环境 / 环境挂了 / 没有可跑的产物）—— 既不算通过也不算打回">
                <span style={{ fontSize: 12, color: VERDICT.inconclusive.color }}>无法审核 {r.inconclusive}</span>
              </Tooltip>
            )}
            {r.failed > 0 && <span style={{ fontSize: 12, color: '#86909c' }}>异常 {r.failed}</span>}
            {r.note && <Tooltip title={r.note}><PauseCircleOutlined style={{ color: '#faad14' }} /></Tooltip>}
          </div>
        )
      } },
    { title: '时间', dataIndex: 'createdAt', width: 96,
      render: v => <span style={{ fontSize: 12, color: '#86909c' }}>{fmt(v)}</span> },
    { title: '', width: 108, align: 'right',
      render: (_, r) => (
        <span style={{ display: 'inline-flex', gap: 6 }}>
          {(r.status === 'queued' || r.status === 'running') && (
            <Popconfirm title="取消这一批？" description="正在跑的那条会做完再停" onConfirm={() => act(r.batchId, 'cancel')}>
              <Button size="small" type="text" danger>取消</Button>
            </Popconfirm>
          )}
          {r.status === 'paused' && (
            <Button size="small" type="link" onClick={() => act(r.batchId, 'resume')}>继续</Button>
          )}
        </span>
      ) },
  ]

  if (!branchId) return <Card><Empty description="请先选择分支" /></Card>

  const waiting = (queue.queued || 0) + (queue.running || 0) + (queue.paused || 0)

  return (
    <>
      <Card
        title={<span>审核报告
          {/* 「还有多少在排队」只有几个数，不值一张表 —— 放标题右边那行字（§6） */}
          <span style={{ fontSize: 12, color: '#86909c', marginLeft: 12, fontWeight: 400 }}>
            {waiting ? `在跑/排队：${waiting} 批` : '当前没有在跑的审核'}
            · 一行 = 一次审核；只有「模块全量」能代表这个模块的情况
          </span>
        </span>}
        extra={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 12 }}>
            {/* CC 每次回推都会自审，一天几十条 —— 全混在一起就找不到自己点的那次了 */}
            <span style={{ fontSize: 12, color: '#86909c' }}>
              包含 CC 自审 <Switch size="small" checked={includeCC} onChange={setIncludeCC} />
            </span>
            <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          </span>
        }
        styles={{ body: { padding: rows?.length ? 0 : 24 } }}
      >
        {loading && !rows ? <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div> : (
          <Table size="small" rowKey="batchId" columns={columns} dataSource={rows || []}
            pagination={false} loading={loading}
            locale={{ emptyText: '还没审过 —— 去用例管理选中模块点「AI 审核」' }} />
        )}
      </Card>

      <Drawer open={!!detail} onClose={() => setDetail(null)} width={720}
        title={detail ? `${detail.scopeLabel || ''} · ${KIND[detail.kind]?.label || ''}${detail.environment ? ' · ' + detail.environment : ''}` : ''}>
        {detailLoading ? <Spin /> : detail && detail.status && (
          <ModuleReport d={detail} projectId={projectId} navigate={navigate} />
        )}
      </Drawer>
    </>
  )
}

// 模块报告（§7）。**价值在共性问题和覆盖缺口**：一条一条看只知道"这条不行"；
// 看模块才知道"这一整片都犯同一个错"和"这个模块压根没测到的地方"。
function ModuleReport({ d, projectId, navigate }) {
  const r = d.report || {}
  return (
    <div style={{ fontSize: 13 }}>
      {/* 这次能不能代表整个模块，要写在最上面 —— 不写的话抽审三条的报告
          过两周会被当成"这个模块审过了" */}
      <div style={{ padding: '8px 12px', marginBottom: 14, borderRadius: 6,
        background: d.representative ? 'rgba(14,165,160,0.08)' : 'rgba(250,173,20,0.10)',
        // 抽审那句要比"可以代表模块"重一档 —— 这句是唯一防止"抽三条被当成审过了"的东西
        fontWeight: d.representative ? 400 : 600 }}>
        {d.scopeNote}
      </div>

      <div style={{ marginBottom: 16 }}>
        <b>结论</b>：
        <span style={{ color: VERDICT.approved.color, marginLeft: 8 }}>通过 {d.approved}</span>
        <span style={{ color: VERDICT.rejected.color, marginLeft: 12 }}>打回 {d.rejected}</span>
        {d.inconclusive > 0 && <span style={{ color: VERDICT.inconclusive.color, marginLeft: 12 }}>
          无法审核 {d.inconclusive}</span>}
        {d.failed > 0 && <span style={{ color: '#86909c', marginLeft: 12 }}>异常 {d.failed}</span>}
        {d.note && <div style={{ marginTop: 6, color: '#faad14' }}>{d.note}</div>}
      </div>

      {!!(r.commonIssues || []).length && (
        <section style={{ marginBottom: 18 }}>
          <b>共性问题</b> <span style={{ color: '#86909c', fontSize: 12 }}>—— 改一处能修一片</span>
          <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
            {r.commonIssues.map((c, i) => (
              <li key={i} style={{ marginBottom: 6 }}>
                <Tag color={c.severity === 'blocker' ? 'error' : 'warning'}
                  style={{ fontSize: 11 }}>{c.count} 条</Tag>
                {/* label 是人话（「验的端点页面根本不调」），kind 是判据名 ——
                    只显示 kind 的话没人知道该改什么，而这一块的价值全在"改一处修一片" */}
                <b>{c.label || c.kind}</b>
                <div style={{ color: '#4e5969', marginTop: 2 }}>{c.sample}</div>
                <div style={{ color: '#86909c', fontSize: 11 }}>{(c.cases || []).join('、')}</div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!!(r.coverageGaps || []).length && (
        <section style={{ marginBottom: 18 }}>
          <b>覆盖缺口</b> <span style={{ color: '#86909c', fontSize: 12 }}>
            —— 这个模块还缺什么。**建议清单，不参与任何一条用例过不过**</span>
          <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
            {r.coverageGaps.map((g, i) => (
              <Tooltip key={i} title={(g.phrasings || []).length > 1
                ? <div style={{ fontSize: 12 }}>各条原话：{g.phrasings.map((t, j) => <div key={j}>· {t}</div>)}</div>
                : null}>
                <li style={{ marginBottom: 4 }}>
                  <Tag color={g.count > 1 ? 'warning' : undefined} style={{ fontSize: 11 }}>{g.count}×</Tag>
                  <b>{mdBold(g.display || g.topic || g.gap)}</b>
                  {g.matchedTopic ? <span style={{ color: '#86909c', marginLeft: 6 }}>{mdBold(g.gap)}</span> : null}
                </li>
              </Tooltip>
            ))}
          </ul>
          {r.coverageGapsTotal > (r.coverageGaps || []).length && (
            <div style={{ fontSize: 11, color: '#86909c' }}>
              还有 {r.coverageGapsTotal - r.coverageGaps.length} 类没显示
            </div>
          )}
        </section>
      )}

      {!!(d.items || []).length && (
        <details>
          <summary style={{ cursor: 'pointer' }}><b>逐条结果</b>（{d.items.length}）</summary>
          <div style={{ marginTop: 8 }}>
            {d.items.map(it => (
              <div key={it.caseId} style={{ display: 'flex', gap: 10, padding: '4px 0',
                borderBottom: '1px solid #f0f0f0' }}>
                <a style={{ minWidth: 130 }}
                  onClick={() => navigate(`/projects/${projectId}/cases/${it.caseId}`)}>{it.caseCode}</a>
                <span style={{ color: VERDICT[it.verdict]?.color || '#86909c', minWidth: 64 }}>
                  {VERDICT[it.verdict]?.label || it.status}
                </span>
                <span style={{ color: '#86909c', fontSize: 12 }}>
                  {it.error || RUN_STATE[it.runState] || ''}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
