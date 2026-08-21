import { useState, useEffect, useCallback } from 'react'
import { timeColumn } from '../../utils/timeCol'
import { Table, Tag, Input, Select, DatePicker, Space, message, Drawer, Tooltip } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import { api } from '../../utils/request'

const { RangePicker } = DatePicker

const ACTION_CONFIG = {
  create: { label: '创建', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  update: { label: '修改', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  delete: { label: '删除', color: '#e8453c', bg: 'rgba(232,69,60,0.1)' },
  execute: { label: '执行', color: '#faad14', bg: 'rgba(250,173,20,0.12)' },
  import: { label: '导入', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  archive: { label: '归档', color: '#c9cdd4', bg: 'rgba(0,0,0,0.04)' },
  login: { label: '登录', color: '#7c5cbf', bg: 'rgba(124,92,191,0.06)' },
  logout: { label: '登出', color: '#7c5cbf', bg: 'rgba(124,92,191,0.06)' },
  sync: { label: '同步', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  change_password: { label: '改密', color: '#ff7d00', bg: 'rgba(255,125,0,0.1)' },
  // 下面这些后端一直在写，前端没配 label —— 于是表格里直接露出 `hard_delete`
  // `rename` 这种裸 key（走查截图里两条都在）。取自后端 audit 的全部 action 值。
  hard_delete: { label: '彻底删除', color: '#e8453c', bg: 'rgba(232,69,60,0.1)' },
  empty_trash: { label: '清空回收站', color: '#e8453c', bg: 'rgba(232,69,60,0.1)' },
  rename: { label: '重命名', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  reorder: { label: '排序', color: '#86909c', bg: 'rgba(0,0,0,0.04)' },
  copy: { label: '复制', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  copy_from: { label: '跨分支复制', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  clone: { label: '克隆', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  batch_update: { label: '批量修改', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  update_variables: { label: '改变量', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  unarchive: { label: '取消归档', color: '#86909c', bg: 'rgba(0,0,0,0.04)' },
  activate: { label: '启用', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  pause: { label: '暂停', color: '#faad14', bg: 'rgba(250,173,20,0.12)' },
  resume: { label: '恢复', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  abort: { label: '中止', color: '#e8453c', bg: 'rgba(232,69,60,0.1)' },
  complete: { label: '完成', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  reopen: { label: '重新打开', color: '#4e8af0', bg: 'rgba(78,138,240,0.1)' },
  quarantine: { label: '隔离', color: '#ff7d00', bg: 'rgba(255,125,0,0.1)' },
  release_quarantine: { label: '解除隔离', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  add_member: { label: '加成员', color: '#7c5cbf', bg: 'rgba(124,92,191,0.1)' },
  remove_member: { label: '移除成员', color: '#e8453c', bg: 'rgba(232,69,60,0.1)' },
  update_member: { label: '改成员', color: '#7c5cbf', bg: 'rgba(124,92,191,0.1)' },
  confirm_model: { label: '确认模型', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  confirm_expected: { label: '确认判据', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  refresh_token_reuse_detected: { label: '令牌复用', color: '#e8453c', bg: 'rgba(232,69,60,0.1)' },
}

const TARGET_TYPES = ['user', 'project', 'branch', 'case', 'plan', 'environment', 'channel']
const TARGET_TYPE_LABELS = {
  user: '用户', project: '项目', branch: '分支配置', case: '用例',
  plan: '计划', environment: '环境', channel: '通知渠道',
}

/** 详情抽屉里的一行「标签 值」。标签定宽，值换行时不会跑到标签下面。 */
function Field({ label, children }) {
  return (
    <div style={{ display: 'flex', gap: 12, padding: '7px 0', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
      <div style={{ width: 76, flexShrink: 0, fontSize: 12, color: '#86909c' }}>{label}</div>
      <div style={{ flex: 1, minWidth: 0, fontSize: 13, color: '#1d2129', wordBreak: 'break-word' }}>{children}</div>
    </div>
  )
}

export default function AuditLogs() {
  const { projectId } = useParams()
  const [logs, setLogs] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(15)
  const [keyword, setKeyword] = useState('')
  const [actionFilter, setActionFilter] = useState(null)
  const [targetFilter, setTargetFilter] = useState(null)
  const [actorFilter, setActorFilter] = useState(null)
  const [dateRange, setDateRange] = useState(null)
  const [detailLog, setDetailLog] = useState(null)

  const fetchLogs = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      params.append('page', page)
      params.append('pageSize', pageSize)
      if (keyword) params.append('keyword', keyword)
      if (actionFilter) params.append('action', actionFilter)
      if (actorFilter) params.append('actorType', actorFilter)
      if (targetFilter) params.append('targetType', targetFilter)
      if (dateRange?.[0]) params.append('startTime', dateRange[0].toISOString())
      if (dateRange?.[1]) params.append('endTime', dateRange[1].toISOString())

      const basePath = projectId ? `/projects/${projectId}/logs` : '/logs'
      const res = await api.get(`${basePath}?${params.toString()}`)
      const data = res.data
      setLogs(data.items || [])
      setTotal(data.total || 0)
    } catch (err) {
      // 非 admin 会收到 403，静默处理
      if (err?.response?.status !== 403) {
        message.error('加载日志失败')
      }
      setLogs([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [projectId, page, pageSize, keyword, actionFilter, targetFilter, actorFilter, dateRange])

  useEffect(() => { fetchLogs() }, [fetchLogs])

  const columns = [
    {
      title: '操作人', dataIndex: 'username', width: 132, align: 'center',
      // 光有 username 分不出是哪台 CC —— 建 Key 的接口只能给自己建，
      // 所有 CC 的 Key 归属人都是同一个（通常是 admin）。actorLabel 是那把 Key 的名字，
      // 它才是「哪个连接」的身份，所以跟人名并列显示，而不是藏进详情。
      // 标签另起一行而不是跟人名并排：并排时这一列得留到 230px 才不截断，
      // 每行还高低不齐；只有小半数行有标签，那些宽度全是白占的。
      render: (v, r) => (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
          <span style={{ fontWeight: 500 }}>{v || '-'}</span>
          {r.actorType === 'mcp' && (
            <Tooltip title={`外部 Claude Code 通过 MCP 调用${r.actorLabel ? `，连接：${r.actorLabel}` : '（该 Key 未命名）'}`}>
              <Tag style={{
                color: '#7c5cbf', background: 'rgba(124,92,191,0.08)', border: 'none',
                fontSize: 11, margin: 0, maxWidth: 116, overflow: 'hidden',
                textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>CC · {r.actorLabel || '未命名'}</Tag>
            </Tooltip>
          )}
        </div>
      ),
    },
    {
      // 跟下面那列（详情按钮）同名叫「操作」，一张表里两个「操作」表头分不清谁是谁。
      // 改成跟上方筛选器同名的「操作类型」。
      title: '操作类型', dataIndex: 'action', width: 88, align: 'center',
      render: v => {
        const cfg = ACTION_CONFIG[v] || { label: v, color: '#86909c', bg: 'rgba(0,0,0,0.04)' }
        return <Tag style={{ color: cfg.color, background: cfg.bg, border: 'none' }}>{cfg.label}</Tag>
      },
    },
    ...(!projectId ? [{
      title: '所属项目', dataIndex: 'projectName', width: 120,
      render: v => v ? <span style={{ fontSize: 13, color: '#4e5969' }}>{v}</span> : <span style={{ color: '#c9cdd4' }}>-</span>,
    }] : []),
    {
      title: '对象类型', dataIndex: 'targetType', width: 90, align: 'center',
      render: v => <Tag style={{ color: '#4e5969', background: 'rgba(0,0,0,0.04)', border: 'none' }}>
        {TARGET_TYPE_LABELS[v] || v}
      </Tag>,
    },
    {
      // 「对象名称」和「变更摘要」都不定宽，富余宽度对半分。原来只有摘要那列不定宽，
      // 宽屏上白占的空间全堆给它（大半行摘要还是「-」），标题反倒被 200px 挤成两行；
      // 反过来只让名称不定宽也不对 —— 修改类的摘要 JSON 比标题还长。
      title: '对象名称', dataIndex: 'targetName',
      render: v => <span style={{ fontSize: 13, color: '#4e5969' }}>{v || '-'}</span>,
    },
    {
      // 单行省略交给 CSS 而不是 substring(0, 80) —— 按字符截断跟实际像素宽没关系，
      // 窄屏还是溢出、宽屏又留白。全文在 title 悬浮和详情里都还在。
      title: '变更摘要', dataIndex: 'changes', ellipsis: true,
      render: v => {
        if (!v) return <span style={{ color: '#c9cdd4' }}>-</span>
        const text = typeof v === 'string' ? v : JSON.stringify(v)
        return <span style={{ fontSize: 12, color: '#86909c', cursor: 'default' }} title={text}>{text}</span>
      },
    },
    timeColumn({ key: 'createdAt', title: '时间' }),
    {
      title: '操作', width: 60, align: 'center',
      render: (_, record) => (
        <a style={{ fontSize: 12, color: '#0ea5a0' }} onClick={() => setDetailLog(record)}>详情</a>
      ),
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: '#1d2129' }}>{projectId ? '项目操作日志' : '操作日志'}</h2>
        <span style={{ fontSize: 13, color: '#86909c' }}>{projectId ? '记录本项目内的操作行为' : '记录所有用户操作行为，默认展示最近 7 天'}</span>
      </div>

      <div style={{
        display: 'flex', gap: 10, marginBottom: 12, padding: '12px 16px',
        borderRadius: 14,
      }}>
        <Input
          prefix={<SearchOutlined style={{ color: '#c9cdd4' }} />}
          placeholder="搜索对象名称..."
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
          onPressEnter={fetchLogs}
          allowClear
          style={{ width: 260 }}
        />
        <Select
          placeholder="操作类型"
          value={actionFilter}
          onChange={v => { setActionFilter(v); setPage(1) }}
          allowClear
          style={{ width: 130 }}
          options={Object.entries(ACTION_CONFIG).map(([k, v]) => ({ value: k, label: v.label }))}
        />
        <Tooltip title="「页面操作」按 actor_type 为空来筛 —— 2026-08-21 之前没记来源，那批 CC 操作也会落在这里">
          <Select
            placeholder="操作来源"
            value={actorFilter}
            onChange={v => { setActorFilter(v); setPage(1) }}
            allowClear
            style={{ width: 140 }}
            options={[
              { value: 'mcp', label: '外部 Claude Code' },
              { value: 'human', label: '页面操作' },
            ]}
          />
        </Tooltip>
        <Select
          placeholder="对象类型"
          value={targetFilter}
          onChange={v => { setTargetFilter(v); setPage(1) }}
          allowClear
          style={{ width: 130 }}
          options={TARGET_TYPES.map(t => ({ value: t, label: TARGET_TYPE_LABELS[t] || t }))}
        />
        <RangePicker
          size="middle" style={{ width: 260 }}
          placeholder={['开始日期', '结束日期']}
          onChange={v => { setDateRange(v); setPage(1) }}
        />
      </div>

      <div style={{ borderRadius: 14, padding: 2 }}>
        <Table
          dataSource={logs}
          columns={columns}
          rowKey="id"
          size="small"
          loading={loading}
          pagination={{
            current: page,
            pageSize,
            total,
            size: 'small',
            showTotal: t => `共 ${t} 条记录`,
            showSizeChanger: true,
            pageSizeOptions: [15, 30, 50],
            onChange: (p, ps) => { setPage(p); setPageSize(ps) },
          }}
        />
      </div>

      <Drawer
        title="操作日志详情"
        open={!!detailLog}
        onClose={() => setDetailLog(null)}
        width={600}
      >
        {detailLog && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* 一行一个字段。原来是两行横排挤 7 个字段：加进「操作来源」之后
                标题被压成两行、值还换行，抽屉加宽也只是把拥挤往后推一格。 */}
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              <Field label="操作时间">
                {detailLog.createdAt ? new Date(detailLog.createdAt).toLocaleString('zh-CN', { hour12: false }) : '-'}
              </Field>
              <Field label="操作人"><span style={{ fontWeight: 500 }}>{detailLog.username || '-'}</span></Field>
              <Field label="操作来源">
                {detailLog.actorType === 'mcp'
                  ? <>外部 Claude Code<span style={{ color: '#86909c' }}>{detailLog.actorLabel ? ` · ${detailLog.actorLabel}` : ''}</span></>
                  : <span style={{ color: '#86909c' }}>页面操作</span>}
              </Field>
              <Field label="操作类型">
                <Tag style={{ color: (ACTION_CONFIG[detailLog.action] || {}).color || '#86909c', background: (ACTION_CONFIG[detailLog.action] || {}).bg || 'rgba(0,0,0,0.04)', border: 'none' }}>
                  {(ACTION_CONFIG[detailLog.action] || {}).label || detailLog.action}
                </Tag>
              </Field>
              <Field label="对象类型">{TARGET_TYPE_LABELS[detailLog.targetType] || detailLog.targetType || '-'}</Field>
              <Field label="对象名称"><span style={{ fontWeight: 500 }}>{detailLog.targetName || '-'}</span></Field>
              {detailLog.projectName && <Field label="所属项目">{detailLog.projectName}</Field>}
              {detailLog.traceId && (
                <Field label="Trace ID">
                  <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: '#86909c' }}>{detailLog.traceId}</span>
                </Field>
              )}
            </div>
            {detailLog.changes && (
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 6 }}>变更摘要</div>
                <pre style={{
                  margin: 0, padding: '12px 14px', background: 'transparent', borderRadius: 12,
                  fontSize: 12, lineHeight: 1.8, overflow: 'auto', maxHeight: 400,
                  whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  fontFamily: 'var(--font-mono)',
                  border: '1px solid rgba(0,0,0,0.04)',
                }}>
                  {typeof detailLog.changes === 'string' ? detailLog.changes : JSON.stringify(detailLog.changes, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )}
      </Drawer>
    </div>
  )
}
