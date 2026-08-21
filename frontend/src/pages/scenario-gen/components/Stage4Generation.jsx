import { useState, useEffect, useRef } from 'react'
import { Card, Tag, Progress, Button, Space, Typography, Empty, Tabs, message } from 'antd'
import { ReloadOutlined, PauseCircleOutlined } from '@ant-design/icons'
import { api } from '../../../utils/request'

const { Text } = Typography

const DIM_COLORS = {
  positive: '#0ea5a0', negative: 'red', boundary: 'orange',
  permission: 'purple', data: 'cyan', state: 'blue',
}
const DIM_LABELS = {
  positive: '正向', negative: '异常', boundary: '边界',
  permission: '权限', data: '数据', state: '状态',
}

export default function Stage4Generation({ projectId, branchId, taskId, onDone }) {
  const [task, setTask] = useState(null)
  const [events, setEvents] = useState([])
  const [cases, setCases] = useState([])
  const [dimFilter, setDimFilter] = useState('all')
  const sseRef = useRef(null)

  const basePath = `/projects/${projectId}/branches/${branchId}/scenario-gen/tasks/${taskId}`

  useEffect(() => {
    api.get(basePath).then(res => setTask(res.data)).catch(() => {})

    const { abort } = api.sseStream(`${basePath}/events`, {
      afterSeq: 0,
      onEvent: (data) => {
        setEvents(prev => [...prev, data])
        if (data.type === 'case_created' && data.payload) {
          setCases(prev => [...prev, data.payload])
        }
        if (data.type === 'task_state') {
          setTask(prev => prev ? { ...prev, status: data.payload?.status } : prev)
        }
      },
      onEnd: (data) => {
        setTask(prev => prev ? { ...prev, status: data.task_status } : prev)
        if (data.task_status === 'completed' || data.task_status === 'partial_failed') {
          onDone?.(data)
        }
      },
      onError: () => {},
    })
    sseRef.current = { abort }
    return () => abort()
  }, [basePath])

  const progress = task?.progress || {}
  const total = progress.total || '?'
  const succeeded = progress.succeeded || cases.length
  const failed = progress.failed || 0
  const pct = total !== '?' && total > 0 ? Math.round(((succeeded + failed) / total) * 100) : 0

  const isTerminal = ['completed', 'partial_failed', 'failed', 'aborted'].includes(task?.status)

  const handleAbort = async () => {
    try {
      await api.post(`${basePath}/abort`)
    } catch { /* */ }
  }

  const handleResume = async () => {
    try {
      await api.post(`${basePath}/resume`)
      message.success('已发起断点续跑')
      api.get(basePath).then(res => setTask(res.data)).catch(() => {})
    } catch { /* request.js handles */ }
  }

  const filteredCases = dimFilter === 'all'
    ? cases
    : cases.filter(c => c.dimension === dimFilter)

  const dimCounts = {}
  for (const c of cases) {
    const d = c.dimension || 'unknown'
    dimCounts[d] = (dimCounts[d] || 0) + 1
  }

  return (
    <div style={{ display: 'flex', gap: 16, minHeight: 500 }}>
      {/* 左栏：进度流 */}
      <div style={{ width: 360, flexShrink: 0 }}>
        <Card size="small" style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <Text strong>{isTerminal ? '生成完成' : '生成中'}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {succeeded}/{total}
              {failed > 0 && <span style={{ color: '#e8453c' }}> · {failed} 失败</span>}
            </Text>
          </div>
          <Progress percent={pct} status={task?.status === 'failed' ? 'exception' : isTerminal ? 'success' : 'active'} size="small" />
        </Card>

        <div style={{ maxHeight: 400, overflow: 'auto' }}>
          {events.filter(e => ['point_start', 'case_created', 'case_skipped', 'point_failed'].includes(e.type)).map((ev, i) => (
            <div key={i} style={{ fontSize: 12, padding: '4px 0', borderBottom: '1px solid rgba(0,0,0,0.04)', display: 'flex', alignItems: 'center', gap: 6 }}>
              {ev.type === 'point_start' && <span style={{ color: '#faad14' }}>⟳</span>}
              {ev.type === 'case_created' && <span style={{ color: '#0ea5a0' }}>✓</span>}
              {ev.type === 'case_skipped' && <span style={{ color: '#bfc4cd' }}>⊘</span>}
              {ev.type === 'point_failed' && <span style={{ color: '#e8453c' }}>✕</span>}
              <Text type="secondary" style={{ fontSize: 11 }}>
                {ev.payload?.title || ev.payload?.ref || ev.payload?.error_message?.slice(0, 60) || ev.type}
              </Text>
            </div>
          ))}
        </div>

        {!isTerminal && (
          <div style={{ marginTop: 12 }}>
            <Text type="secondary" style={{ fontSize: 11 }}>可离开此页，任务后台继续</Text>
            <Button size="small" danger icon={<PauseCircleOutlined />} onClick={handleAbort} style={{ marginLeft: 8 }}>中止</Button>
          </div>
        )}
        {task?.status === 'partial_failed' && (
          <Button size="small" type="primary" onClick={handleResume} icon={<ReloadOutlined />} style={{ marginTop: 8 }}>
            从断点继续
          </Button>
        )}
        {task?.errorMessage && (
          <div style={{ marginTop: 8, padding: 8, background: 'var(--red-bg)', borderRadius: 6, fontSize: 12, color: '#e8453c' }}>
            {task.errorMessage}
          </div>
        )}
      </div>

      {/* 右栏：产出卡片墙 */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <Space size={4}>
            <Tag color={dimFilter === 'all' ? 'blue' : 'default'} style={{ cursor: 'pointer' }}
              onClick={() => setDimFilter('all')}>全部 {cases.length}</Tag>
            {Object.entries(dimCounts).map(([dim, count]) => (
              <Tag key={dim} color={dimFilter === dim ? (DIM_COLORS[dim] || 'blue') : 'default'}
                style={{ cursor: 'pointer' }} onClick={() => setDimFilter(dim)}>
                {DIM_LABELS[dim] || dim} {count}
              </Tag>
            ))}
          </Space>
        </div>

        {filteredCases.length === 0 ? (
          <Empty description={isTerminal ? "无匹配用例" : "等待用例生成..."} style={{ marginTop: 80 }} />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {filteredCases.map((c, i) => (
              <Card key={i} size="small" hoverable style={{
                animation: !isTerminal && i === filteredCases.length - 1 ? 'fadeIn 0.5s' : undefined,
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div>
                    <Text strong style={{ fontSize: 13 }}>{c.case_code || c.title}</Text>
                    <div style={{ marginTop: 2 }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>{c.title}</Text>
                    </div>
                  </div>
                  <Space size={4}>
                    {c.dimension && <Tag color={DIM_COLORS[c.dimension]}>{DIM_LABELS[c.dimension] || c.dimension}</Tag>}
                    {c.priority && <Tag color={c.priority === 'P0' ? 'red' : c.priority === 'P1' ? 'orange' : 'blue'}>{c.priority}</Tag>}
                    {c.score != null && (
                      <Tag color={c.score >= 85 ? 'cyan' : c.score >= 70 ? 'blue' : 'orange'}>{c.score}</Tag>
                    )}
                    <Tag>待审核</Tag>
                  </Space>
                </div>
                {c.steps && (
                  <div style={{ marginTop: 6, fontSize: 11, color: '#8c919e' }}>
                    {c.steps.length} 步骤
                    {c.test_method && <span> · {c.test_method}</span>}
                  </div>
                )}
              </Card>
            ))}
          </div>
        )}
        <style>{`@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }`}</style>
      </div>
    </div>
  )
}
