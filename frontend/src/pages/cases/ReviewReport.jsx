import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Table, Tag, Button, Empty, Spin, Tooltip, Drawer, Switch, Select, Progress, Popconfirm, message } from 'antd'
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
  // CC 调 lum_review_case 自审的那一条。**跟「单条」分开** —— 那个是人在详情页
  // 发起、排队跑的；这个是 MCP 在进程内直接跑的，账本上这行主要是「正在审」的
  // 标记（防它超时后重复触发）。混成一种，报告里就分不出"人审的"和"CC 自审的"。
  cc_inline:          { label: 'CC 自审', color: 'default', hint: 'CC 调 lum_review_case 自己过的一遍，不占队列' },
}

// 覆盖分布六格的显示顺序（增删改查 + 异常 + 权限）。后端算的时候是这个顺序，
// 但存进 JSONB 就被重排了，所以这里再定一次。
const OP_ORDER = ['创建', '查询', '修改', '删除', '异常', '权限']

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
  const [kindFilter, setKindFilter] = useState()
  const [statusFilter, setStatusFilter] = useState()
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const base = `/projects/${projectId}/branches/${branchId}`

  // 接口最多给 200 条，默认 50。**筛选是在已加载的这些行里筛的** ——
  // 所以取到的正好是上限时要说一声，否则「按类型筛完只有 2 条」会被当成
  // "这个项目只审过 2 次"，而实际是第 51 次之后的没在手里。
  const LIMIT = 50

  const load = useCallback(async () => {
    if (!branchId) return
    setLoading(true)
    try {
      // 这页原来把参数写成 axios 那套 `{ params: {...} }`，而 `api.get` 是 fetch 包的，
      // 第二个参数直接摊进 fetch config —— 参数被静默丢掉，于是 mine 永远不发，
      // 后端默认 mine=True，「包含 CC 自审」这个开关**从来没生效过**
      // （实测：开关拨到 on，请求还是不带参数，8 条里只回 3 条）。
      // 2026-08-31 起 utils/request.js 已经支持 `{ params }` 了，两种写法都行；
      // 这里保留手拼是因为它在跑、没必要动。
      const q = new URLSearchParams({ mine: String(!includeCC), limit: String(LIMIT) })
      const r = await api.get(`${base}/ai-review/batches?${q}`)
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
    { title: '类型', dataIndex: 'kind', width: 100,
      // hint 里带 `**…**`（「**只有这种能代表模块情况**」）—— 直接塞 Tooltip
      // 会把星号原样显示出来，正好显示在最该被看见的那句上
      render: v => <Tooltip title={mdBold(KIND[v]?.hint)}><Tag color={KIND[v]?.color} style={{ margin: 0 }}>{KIND[v]?.label || v}</Tag></Tooltip> },
    // 这一列以前**故意不给宽度**，让它一个人吃掉所有富余宽度。那是在
    // 「声明总宽 906px、实际容器 1642px」的前提下唯一能看的办法 —— 否则 antd
    // 把 736px 的余量按比例摊给每一列，92px 的类型格被拉成 167px，
    // 一个 60px 的 Tag 在中间飘着。
    //
    // 但代价是这一列自己被撑到七八百像素，一个模块名后面拖一片空白。
    // 现在换成**所有列都钉宽、且总宽贴近真实容器**（下面各列加起来 ~1528px，
    // 1920 屏的内容区约 1690px）—— 余量只剩一成，按比例摊下去每列各长几个像素，
    // 谁都不会被拉变形。这也是「整体协调」的做法：余量要小到看不出来，
    // 而不是找一列去背。窄屏（<1528px）时表格自己出横向滚动，各列保持声明宽度。
    { title: '范围', dataIndex: 'scopeLabel', width: 480, ellipsis: true,
      render: (v, r) => <a onClick={() => openDetail(r.batchId)}>{v || '—'}</a> },
    { title: '环境', dataIndex: 'environment', width: 128, ellipsis: true,
      render: v => v || <span style={{ color: '#c9cdd4' }}>—</span> },
    // 下限不是随手写的：`admin` + 「CC」标签在 90px 里正好折成两行，
    // 整行跟着长到 60px（实测 UAG 那 5 行都是），一张表凭空高出三成。
    // 所以这一列**不能低于 116**，往上加宽随意。
    { title: '发起人', dataIndex: 'actor', width: 136,
      render: (v, r) => <span style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{v || '—'}
        {r.actorKind !== 'human' && <Tag style={{ marginLeft: 4, fontSize: 11 }}>CC</Tag>}</span> },
    // 状态和结果**分两列**：合成一列时「已完成」和「打回 2」挤在同一格，
    // 而这两件事是分开看的 —— 状态答"跑完了没"，结果答"这批得出了什么"。
    { title: '状态', dataIndex: 'status', width: 116,
      render: (v, r) => {
        const st = STATUS[v] || {}
        return <Tag color={st.color} style={{ margin: 0 }}>{st.label || v}
          {(v === 'running' || v === 'queued') && r.total ? ` ${r.done}/${r.total}` : ''}</Tag>
      } },
    { title: '结果', width: 260,
      render: (_, r) => {
        if (r.status === 'running' || r.status === 'queued') {
          return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Progress percent={r.total ? Math.round(r.done * 100 / r.total) : 0}
                size="small" style={{ width: 90, margin: 0 }}
                format={() => `${r.done}/${r.total}`} />
              {r.current && <span style={{ fontSize: 11, color: '#86909c' }}>{r.current}</span>}
            </div>
          )
        }
        const nums = [
          r.approved > 0 && <span key="a" style={{ fontSize: 12, color: VERDICT.approved.color }}>通过 {r.approved}</span>,
          r.rejected > 0 && <span key="r" style={{ fontSize: 12, color: VERDICT.rejected.color }}>打回 {r.rejected}</span>,
          /* 「无法审核」单独一个数 —— 它既不是通过也不是打回，
             藏进任何一边都会让这批结论的含金量看不出来 */
          r.inconclusive > 0 && (
            <Tooltip key="i" title="没真跑成功（缺环境 / 环境挂了 / 没有可跑的产物）—— 既不算通过也不算打回">
              <span style={{ fontSize: 12, color: VERDICT.inconclusive.color }}>无法审核 {r.inconclusive}</span>
            </Tooltip>
          ),
          r.failed > 0 && <span key="f" style={{ fontSize: 12, color: '#86909c' }}>异常 {r.failed}</span>,
        ].filter(Boolean)
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {/* 取消掉的那批一个数都没有 —— 空着会像"结果还没出"，明写一个横杠 */}
            {nums.length ? nums : <span style={{ color: '#c9cdd4' }}>—</span>}
            {r.note && <Tooltip title={r.note}><PauseCircleOutlined style={{ color: '#faad14' }} /></Tooltip>}
          </div>
        )
      } },
    { title: '时间', dataIndex: 'createdAt', width: 116,
      render: v => <span style={{ fontSize: 12, color: '#86909c' }}>{fmt(v)}</span> },
    // 操作列原来是个没标题的空列：只有排队中/在跑/暂停的行才长出按钮，
    // 而报告页上绝大多数行都是跑完的 —— 于是整列常年空着，连表头都没有。
    // 「查看」补进来：之前打开右侧抽屉的唯一办法是点「范围」那个链接，
    // 链接看着像跳转，没人知道点它会出报告。
    { title: '操作', width: 140,
      render: (_, r) => (
        <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
          <Button size="small" type="link" style={{ padding: 0 }}
            onClick={() => openDetail(r.batchId)}>查看</Button>
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

  // 筛选项从**已加载的这些行**里长出来（跟国际化词典那两个下拉一样）：
  // 把 KIND/STATUS 六七种全列出来，选中一个没有的值就得到一张空表，
  // 分不清是"这个项目没这么审过"还是"筛错了"。
  const opts = (key, dict) => [...new Set((rows || []).map(r => r[key]).filter(Boolean))]
    .map(v => ({ value: v, label: dict[v]?.label || v }))
  const visible = (rows || []).filter(r =>
    (!kindFilter || r.kind === kindFilter) && (!statusFilter || r.status === statusFilter))
  const filtered = !!(kindFilter || statusFilter)

  return (
    <>
      {/* 页头跟全站一套：h2 18px + 一行 13px 灰字 + 右侧工具条。原来标题整个塞在
          Card title 里（别的列表页都是页头在卡片外面），这页就矮别人一档。 */}
      <div style={{ display: 'flex', justifyContent: 'space-between',
        alignItems: 'center', marginBottom: 10 }}>
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: '#1d2129' }}>审核报告</h2>
          {/* 「还有多少在排队」只有几个数，不值一张表 —— 放标题底下那行字（§6） */}
          <span style={{ fontSize: 13, color: '#86909c' }}>
            一行 = 一次审核；只有「模块全量」能代表这个模块的情况
            {waiting ? ` · 在跑/排队：${waiting} 批` : ' · 当前没有在跑的审核'}
            {filtered ? ` · 筛出 ${visible.length}/${rows.length} 条` : ''}
            {/* 到上限就明说：筛选只在这 50 条里筛 */}
            {rows && rows.length >= LIMIT ? ` · 只列出最近 ${LIMIT} 次` : ''}
          </span>
        </div>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 12 }}>
          <Select allowClear size="small" style={{ width: 122 }} placeholder="按类型筛选"
            value={kindFilter} onChange={setKindFilter} options={opts('kind', KIND)} />
          <Select allowClear size="small" style={{ width: 122 }} placeholder="按状态筛选"
            value={statusFilter} onChange={setStatusFilter} options={opts('status', STATUS)} />
          {/* CC 每次回推都会自审，一天几十条 —— 全混在一起就找不到自己点的那次了 */}
          <span style={{ fontSize: 12, color: '#86909c' }}>
            包含 CC 自审 <Switch size="small" checked={includeCC} onChange={setIncludeCC} />
          </span>
          <Button size="small" icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        </span>
      </div>
      <Card styles={{ body: { padding: visible.length ? 0 : 24 } }}>
        {loading && !rows ? <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div> : (
          <Table size="small" rowKey="batchId" columns={columns} dataSource={visible}
            loading={loading}
            // 这页一次最多取 LIMIT(50) 条，以前 pagination={false} 全摊开 ——
            // 50 行连着 8 列，往下滚三屏才到底，而表头不跟着滚。
            // 分页是在**已加载的这 50 条**里翻（跟上面两个筛选器同一个前提），
            // 上限那句提示留在页头，别让人以为翻到最后一页就是全部历史。
            pagination={{
              defaultPageSize: 20,
              size: 'small',
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50],
              showTotal: t => `共 ${t} 条`,
              // Card body 在有数据时 padding 是 0（为了让表格贴边），
              // 分页条得自己把内缩补回来，否则它贴在卡片右边线上。
              style: { margin: '12px 16px' },
            }}
            locale={{ emptyText: filtered
              ? '这个筛选条件下没有审核记录'
              : '还没审过 —— 去用例管理选中模块点「AI 审核」' }} />
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

// 模块报告（§7）三块：共性问题 + 覆盖缺口 + 覆盖分布。一条一条看只知道"这条不行"；
// 看模块才知道"这一整片都犯同一个错"、"这个模块压根没测到的地方"，
// 以及"这些用例合起来偏在哪"。
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
            —— 这个模块还缺什么。<b>建议清单，不参与任何一条用例过不过</b></span>
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

      {r.coverageSkew && (!!(r.coverageSkew.notes || []).length || r.coverageSkew.ops) && (
        <section style={{ marginBottom: 18 }}>
          <b>覆盖分布</b> <span style={{ color: '#86909c', fontSize: 12 }}>
            —— 这些用例合起来偏不偏。代码数的个数，不问模型，同一份报告每次打开都一样</span>
          {/* 六类各几条摊开摆着，而不是只报"倾斜了"：0 那一格是这一块最有用的东西 ——
              「删除 0」比任何一句"建议补充异常场景"都具体，而它只有摆在旁边的
              非零格子衬着才刺眼 */}
          {r.coverageSkew.ops && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: '8px 0 0' }}>
              {/* 按 OP_ORDER 摆，**不用 Object.entries 的顺序** —— 这份 ops 存在
                  JSONB 里，Postgres 不保留插入顺序（按键长度+字典序重排），
                  页面上就成了「修改 创建 删除 异常 权限 查询」：增删改查读起来
                  是乱的，而这一块全靠横着扫一眼看出哪格是 0 */}
              {OP_ORDER.filter(k => k in r.coverageSkew.ops)
                .concat(Object.keys(r.coverageSkew.ops).filter(k => !OP_ORDER.includes(k)))
                .map(k => [k, r.coverageSkew.ops[k]]).map(([k, v]) => (
                <Tag key={k} color={v === 0 ? 'error' : undefined} style={{ fontSize: 12, margin: 0 }}>
                  {k} {v}
                </Tag>
              ))}
              {r.coverageSkew.p0 && (
                <Tag color={r.coverageSkew.p0.ratio > (r.coverageSkew.p0.cap ?? 0.15) ? 'warning' : undefined}
                  style={{ fontSize: 12, margin: 0 }}>
                  P0 {r.coverageSkew.p0.count}/{r.coverageSkew.total}
                  （{Math.round(r.coverageSkew.p0.ratio * 100)}%）
                </Tag>
              )}
            </div>
          )}
          {!!(r.coverageSkew.notes || []).length && (
            <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
              {r.coverageSkew.notes.map((n, i) => (
                <li key={i} style={{ marginBottom: 4, color: '#4e5969' }}>{n}</li>
              ))}
            </ul>
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
