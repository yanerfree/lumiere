import { useState, useEffect, useCallback } from 'react'
import { Table, Tag, Input, Select, DatePicker, Space, message, Drawer, Tooltip } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import { api } from '../../utils/request'

const { RangePicker } = DatePicker

const ACTION_CONFIG = {
  create: { label: '创建', color: '#0ea5a0', bg: '#e0f7f6' },
  update: { label: '修改', color: '#0ea5a0', bg: '#e0f7f6' },
  delete: { label: '删除', color: '#e8453c', bg: '#fff2f0' },
  execute: { label: '执行', color: '#faad14', bg: '#fffbe6' },
  import: { label: '导入', color: '#0ea5a0', bg: '#e0f7f6' },
  archive: { label: '归档', color: '#c9cdd4', bg: 'rgba(0,0,0,0.04)' },
  login: { label: '登录', color: '#7c5cbf', bg: 'rgba(124,92,191,0.06)' },
  logout: { label: '登出', color: '#7c5cbf', bg: 'rgba(124,92,191,0.06)' },
  sync: { label: '同步', color: '#0ea5a0', bg: '#e0f7f6' },
  change_password: { label: '改密', color: '#fa8c16', bg: '#fff7e6' },
}

const TARGET_TYPES = ['user', 'project', 'branch', 'case', 'plan', 'environment', 'channel']
const TARGET_TYPE_LABELS = {
  user: '用户', project: '项目', branch: '分支配置', case: '用例',
  plan: '计划', environment: '环境', channel: '通知渠道',
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
      title: '时间', dataIndex: 'createdAt', width: 170,
      render: v => <span style={{ fontSize: 13, color: '#86909c', fontFamily: 'var(--font-mono)' }}>
        {v ? new Date(v).toLocaleString('zh-CN', { hour12: false }) : '-'}
      </span>,
    },
    {
      title: '操作人', dataIndex: 'username', width: 165,
      // 光有 username 分不出是哪台 CC —— 建 Key 的接口只能给自己建，
      // 所有 CC 的 Key 归属人都是同一个（通常是 admin）。actorLabel 是那把 Key 的名字，
      // 它才是「哪个连接」的身份，所以跟人名并列显示，而不是藏进详情。
      // 标签另起一行而不是跟人名并排：并排时这一列得留到 230px 才不截断，
      // 每行还高低不齐；只有小半数行有标签，那些宽度全是白占的。
      render: (v, r) => (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 3 }}>
          <span style={{ fontWeight: 500 }}>{v || '-'}</span>
          {r.actorType === 'mcp' && (
            <Tooltip title={`外部 Claude Code 通过 MCP 调用${r.actorLabel ? `，连接：${r.actorLabel}` : '（该 Key 未命名）'}`}>
              <Tag style={{
                color: '#7c5cbf', background: 'rgba(124,92,191,0.08)', border: 'none',
                fontSize: 11, margin: 0, maxWidth: 145, overflow: 'hidden',
                textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>CC · {r.actorLabel || '未命名'}</Tag>
            </Tooltip>
          )}
        </div>
      ),
    },
    {
      title: '操作', dataIndex: 'action', width: 80, align: 'center',
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
      title: '对象名称', dataIndex: 'targetName', width: 200,
      render: v => <span style={{ fontSize: 13, color: '#4e5969' }}>{v || '-'}</span>,
    },
    {
      title: '变更摘要', dataIndex: 'changes',
      render: v => {
        if (!v) return <span style={{ color: '#c9cdd4' }}>-</span>
        const text = typeof v === 'string' ? v : JSON.stringify(v)
        return <span style={{ fontSize: 12, color: '#86909c', cursor: 'default' }}
          title={text}>{text.length > 80 ? text.substring(0, 80) + '...' : text}</span>
      },
    },
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
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', rowGap: 16 }}>
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>操作时间</div>
                <div style={{ fontSize: 13 }}>{detailLog.createdAt ? new Date(detailLog.createdAt).toLocaleString('zh-CN', { hour12: false }) : '-'}</div>
              </div>
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>操作人</div>
                <div style={{ fontSize: 13, fontWeight: 500 }}>{detailLog.username || '-'}</div>
              </div>
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>操作来源</div>
                <div style={{ fontSize: 13 }}>
                  {detailLog.actorType === 'mcp'
                    ? <>外部 Claude Code<span style={{ color: '#86909c' }}>{detailLog.actorLabel ? ` · ${detailLog.actorLabel}` : ''}</span></>
                    : <span style={{ color: '#86909c' }}>页面操作</span>}
                </div>
              </div>
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>操作类型</div>
                <Tag style={{ color: (ACTION_CONFIG[detailLog.action] || {}).color || '#86909c', background: (ACTION_CONFIG[detailLog.action] || {}).bg || 'rgba(0,0,0,0.04)', border: 'none' }}>
                  {(ACTION_CONFIG[detailLog.action] || {}).label || detailLog.action}
                </Tag>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', rowGap: 16 }}>
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>对象类型</div>
                <div style={{ fontSize: 13 }}>{TARGET_TYPE_LABELS[detailLog.targetType] || detailLog.targetType || '-'}</div>
              </div>
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>对象名称</div>
                <div style={{ fontSize: 13, fontWeight: 500 }}>{detailLog.targetName || '-'}</div>
              </div>
              {detailLog.projectName && (
                <div>
                  <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>所属项目</div>
                  <div style={{ fontSize: 13 }}>{detailLog.projectName}</div>
                </div>
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
            {detailLog.traceId && (
              <div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>Trace ID</div>
                <div style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: '#86909c' }}>{detailLog.traceId}</div>
              </div>
            )}
          </div>
        )}
      </Drawer>
    </div>
  )
}
