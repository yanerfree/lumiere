import { useState, useEffect, useCallback } from 'react'
import { TimeCell } from '../../utils/timeCol'
import { Card, Tag, Button, Descriptions, Space, Spin, Empty, message, Input, Select, InputNumber } from 'antd'
import { ClockCircleOutlined, EditOutlined, PlayCircleOutlined, CheckOutlined, ArrowLeftOutlined, SaveOutlined, SyncOutlined, BarChartOutlined, CloseOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../utils/request'
import { buildEnvOptions } from '../../utils/env'
import CasePicker from '../../components/CasePicker'

const planStatusMap = {
  draft: { label: '草稿', color: '#c9cdd4', bg: 'rgba(0,0,0,0.03)' },
  executing: { label: '执行中', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  completed: { label: '已完成', color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  paused: { label: '已暂停', color: '#faad14', bg: 'rgba(250,173,20,0.12)' },
  pending_manual: { label: '待手动录入', color: '#7c5cbf', bg: 'rgba(124,92,191,0.06)' },
  archived: { label: '已归档', color: '#86909c', bg: 'rgba(0,0,0,0.03)' },
}

function fmt(ms) {
  if (!ms && ms !== 0) return '-'
  if (ms < 1000) return ms + 'ms'
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's'
  return Math.floor(ms / 60000) + 'm ' + Math.round((ms % 60000) / 1000) + 's'
}

export default function PlanDetail() {
  const navigate = useNavigate()
  const { projectId, planId } = useParams()
  const [plan, setPlan] = useState(null)
  const [executions, setExecutions] = useState([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState('')
  const [editEnvId, setEditEnvId] = useState(null)
  const [editChannelId, setEditChannelId] = useState(null)
  const [editRetry, setEditRetry] = useState(0)
  const [editCB, setEditCB] = useState(null)
  const [editCaseIds, setEditCaseIds] = useState([])
  const [environments, setEnvironments] = useState([])
  const [channels, setChannels] = useState([])
  const [casePickerOpen, setCasePickerOpen] = useState(false)
  const [executing, setExecuting] = useState(false)
  const [execMessage, setExecMessage] = useState('')

  const fetchData = useCallback(async () => {
    if (!projectId || !planId) return
    setLoading(true)
    try {
      const [planRes, execRes] = await Promise.all([
        api.get(`/projects/${projectId}/plans/${planId}`),
        api.get(`/projects/${projectId}/plans/${planId}/executions`),
      ])
      setPlan(planRes.data)
      setExecutions(execRes.data || [])
    } catch { /* */ } finally { setLoading(false) }
  }, [projectId, planId])

  useEffect(() => { fetchData() }, [fetchData])

  const handleExecute = async () => {
    try {
      const res = await api.post(`/projects/${projectId}/plans/${planId}/execute`)
      fetchData()
      const taskId = res.data?.taskId
      if (!taskId) return
      setExecuting(true)
      setExecMessage('排队中...')
      const poll = setInterval(async () => {
        try {
          const status = await api.get(`/tasks/${taskId}/status`)
          const s = status.data
          setExecMessage(s.message || s.status)
          if (s.status === 'running') fetchData()
          if (s.status === 'completed') {
            clearInterval(poll)
            setExecuting(false)
            setExecMessage('')
            message.success('执行完成')
            fetchData()
          } else if (s.status === 'failed') {
            clearInterval(poll)
            setExecuting(false)
            setExecMessage('')
            message.error(s.message || '执行失败')
            fetchData()
          }
        } catch { /* */ }
      }, 3000)
    } catch (err) {
      message.error(err?.response?.data?.error?.message || err?.message || '执行失败')
    }
  }

  const handleComplete = async () => {
    try {
      await api.post(`/projects/${projectId}/plans/${planId}/complete`)
      message.success('计划已完成')
      fetchData()
    } catch (err) {
      message.error(err?.response?.data?.error?.message || err?.message || '操作失败')
    }
  }

  const handleSave = async () => {
    if (!editName.trim()) { message.warning('计划名称不能为空'); return }
    try {
      const payload = {
        name: editName,
        environmentId: editEnvId,
        channelId: editChannelId,
        retryCount: editRetry,
        circuitBreaker: editCB,
        caseIds: editCaseIds,
      }
      await api.put(`/projects/${projectId}/plans/${planId}`, payload)
      message.success('保存成功')
      setEditing(false)
      fetchData()
    } catch (err) {
      message.error(err?.response?.data?.error?.message || err?.message || '保存失败')
    }
  }

  const startEdit = async () => {
    setEditName(plan?.name || '')
    setEditEnvId(plan?.environmentId || null)
    setEditChannelId(plan?.channelId || null)
    setEditRetry(plan?.retryCount ?? 0)
    setEditCB(plan?.circuitBreaker || null)
    setEditCaseIds(plan?.caseIds || [])
    try {
      const [envRes, chRes] = await Promise.all([
        api.get(`/projects/${projectId}/environments`),
        api.get('/channels'),
      ])
      setEnvironments(envRes.data || [])
      setChannels(chRes.data || [])
    } catch { /* */ }
    setEditing(true)
  }

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin /></div>
  if (!plan) return <Empty description="计划不存在" />

  const ps = planStatusMap[plan.status] || planStatusMap.draft

  return (
    <div>
      {/* Header */}
      <Card styles={{ body: { padding: '16px 24px' } }} style={{ marginBottom: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
              <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)} />
              {editing ? (
                <Input value={editName} onChange={e => setEditName(e.target.value)} style={{ width: 300, fontSize: 18, fontWeight: 600 }}
                  onPressEnter={handleSave} autoFocus />
              ) : (
                <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>{plan.name}</h2>
              )}
              {/* 实色底白字 → 淡底 + 细边框 + 彩色字，跟列表页的状态胶囊一致 */}
              <Tag style={{ background: ps.bg, color: ps.color, border: '1px solid rgba(0,0,0,0.06)' }}>{ps.label}</Tag>
              {executing && <Tag icon={<SyncOutlined spin />} color="processing">{execMessage}</Tag>}
              <Tag style={{ background: 'rgba(0,0,0,0.02)', color: '#86909c', border: 'none' }}>{plan.planType === 'automated' ? '自动化' : '手动'}</Tag>
              <Tag style={{ background: 'rgba(0,0,0,0.02)', color: '#86909c', border: 'none' }}>{plan.testType?.toUpperCase()}</Tag>
            </div>
            <Space size={20} style={{ fontSize: 13, color: '#86909c', paddingLeft: 40 }}>
              <span><ClockCircleOutlined style={{ marginRight: 4 }} />创建于 {new Date(plan.createdAt).toLocaleString('zh-CN')}</span>
              {plan.executedAt && <span>执行于 {new Date(plan.executedAt).toLocaleString('zh-CN')}</span>}
            </Space>
          </div>
          <Space>
            {plan.status !== 'executing' && !editing && (
              <Button icon={<EditOutlined />} onClick={startEdit}>编辑</Button>
            )}
            {editing && (<>
              <Button onClick={() => setEditing(false)}>取消</Button>
              <Button type="primary" icon={<SaveOutlined />} onClick={handleSave}>保存</Button>
            </>)}
            {plan.status === 'draft' && !editing && (
              <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleExecute} loading={executing}>启动执行</Button>
            )}
            {(plan.status === 'completed' || plan.status === 'paused') && !executing && (
              <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleExecute} loading={executing}>重新执行</Button>
            )}
            {plan.status === 'pending_manual' && !executing && (
              <Button icon={<EditOutlined />} onClick={() => navigate(`/projects/${projectId}/plans/${planId}/manual-record`)}>手动录入</Button>
            )}
            {(plan.status === 'pending_manual' || plan.status === 'executing') && !executing && (
              <Button type="primary" icon={<CheckOutlined />} onClick={handleComplete}>确认完成</Button>
            )}
          </Space>
        </div>
      </Card>

      {/* Plan Config */}
      <Card style={{ marginBottom: 8 }} title={<span style={{ fontSize: 14, fontWeight: 600 }}>计划配置</span>}
        styles={{ header: { borderBottom: '1px solid rgba(0,0,0,0.04)', minHeight: 44 }, body: { padding: '12px 24px' } }}>
        {editing ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ display: 'flex', gap: 16 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, color: '#86909c', marginBottom: 4 }}>计划类型</div>
                <Tag style={{ background: 'rgba(0,0,0,0.03)', color: '#86909c' }}>{plan.planType === 'automated' ? '自动化' : '手动'}</Tag>
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, color: '#86909c', marginBottom: 4 }}>测试类型</div>
                <Tag style={{ background: 'rgba(0,0,0,0.03)', color: '#86909c' }}>{plan.testType?.toUpperCase()}</Tag>
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, color: '#86909c', marginBottom: 4 }}>目标环境</div>
                <Select value={editEnvId} onChange={setEditEnvId} allowClear placeholder="选择环境"
                  style={{ width: '100%' }} size="small" popupMatchSelectWidth={false}
                  options={buildEnvOptions(environments)} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, color: '#86909c', marginBottom: 4 }}>通知渠道</div>
                <Select value={editChannelId} onChange={setEditChannelId} allowClear placeholder="选择渠道"
                  style={{ width: '100%' }} size="small"
                  options={channels.map(c => ({ value: c.id, label: c.name }))} />
              </div>
            </div>
            {plan.planType === 'automated' && (
              <div style={{ display: 'flex', gap: 16 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, color: '#86909c', marginBottom: 4 }}>失败重试</div>
                  <InputNumber value={editRetry} onChange={v => setEditRetry(v ?? 0)} min={0} max={3}
                    size="small" style={{ width: '100%' }} addonAfter="次" />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, color: '#86909c', marginBottom: 4 }}>熔断-连续失败</div>
                  <InputNumber value={editCB?.consecutive ?? 5} min={1} max={100} size="small" style={{ width: '100%' }} addonAfter="条"
                    onChange={v => setEditCB(prev => ({ ...(prev || { consecutive: 5, rate: 50 }), consecutive: v ?? 5 }))} />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, color: '#86909c', marginBottom: 4 }}>熔断-失败率</div>
                  <InputNumber value={editCB?.rate ?? 50} min={1} max={100} size="small" style={{ width: '100%' }} addonAfter="%"
                    onChange={v => setEditCB(prev => ({ ...(prev || { consecutive: 5, rate: 50 }), rate: v ?? 50 }))} />
                </div>
                <div style={{ flex: 1 }} />
              </div>
            )}
            <div>
              <div style={{ fontSize: 13, color: '#86909c', marginBottom: 4 }}>关联用例</div>
              <Space>
                <Button size="small" onClick={() => setCasePickerOpen(true)}>选择用例</Button>
                <span style={{ fontSize: 13, color: '#4e5969' }}>已选 {editCaseIds.length} 条</span>
              </Space>
            </div>
          </div>
        ) : (
          <Descriptions column={4} size="small">
            <Descriptions.Item label="计划类型">{plan.planType === 'automated' ? '自动化' : '手动'}</Descriptions.Item>
            <Descriptions.Item label="测试类型">{plan.testType?.toUpperCase()}</Descriptions.Item>
            <Descriptions.Item label="目标环境">{plan.environmentName || <span style={{ color: '#c9cdd4' }}>未配置</span>}</Descriptions.Item>
            <Descriptions.Item label="通知渠道">{plan.channelName || <span style={{ color: '#c9cdd4' }}>未配置</span>}</Descriptions.Item>
            <Descriptions.Item label="失败重试">{plan.retryCount} 次</Descriptions.Item>
            <Descriptions.Item label="关联用例">{plan.caseIds?.length || 0} 条</Descriptions.Item>
            {plan.circuitBreaker && <>
              <Descriptions.Item label="熔断-连续失败">{plan.circuitBreaker.consecutive} 条</Descriptions.Item>
              <Descriptions.Item label="熔断-失败率">{plan.circuitBreaker.rate}%</Descriptions.Item>
            </>}
          </Descriptions>
        )}
      </Card>

      {editing && (
        <CasePicker
          open={casePickerOpen}
          projectId={projectId}
          selectedIds={editCaseIds}
          onOk={(ids) => { setEditCaseIds(ids); setCasePickerOpen(false) }}
          onCancel={() => setCasePickerOpen(false)}
        />
      )}

      {/* Execution History */}
      <Card title={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>执行历史</span>
          <span style={{ fontSize: 13, color: '#86909c', fontWeight: 400 }}>共 {executions.length} 次执行</span>
        </div>
      } styles={{ header: { borderBottom: '1px solid rgba(0,0,0,0.04)', minHeight: 44 } }}>
        {executions.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40, color: '#c9cdd4' }}>
            {plan.status === 'draft' ? '尚未执行，点击上方"启动执行"开始' : '暂无执行记录'}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
            {/* Table Header */}
            <div style={{ display: 'flex', padding: '8px 16px', background: 'var(--table-header-bg)', borderRadius: '8px 8px 0 0', fontSize: 12, color: '#86909c', fontWeight: 500 }}>
              <div style={{ width: 50 }}>#</div>
              <div style={{ flex: 2, textAlign: 'center' }}>状态</div>
              <div style={{ flex: 3 }}>结果 <span style={{ color: '#c9cdd4', fontWeight: 400 }}>通过/失败/总</span></div>
              <div style={{ flex: 2, textAlign: 'center' }}>通过率</div>
              <div style={{ flex: 2, textAlign: 'right' }}>耗时</div>
              <div style={{ width: 112, textAlign: 'center' }}>执行时间</div>
              <div style={{ flex: 2, textAlign: 'center' }}>操作</div>
            </div>
            {executions.map((exec, i) => {
              const num = executions.length - i
              const isCompleted = !!exec.completedAt
              const isRunning = !exec.completedAt && exec.passed === 0 && exec.failed === 0
              return (
                <div key={exec.id} style={{
                  display: 'flex', alignItems: 'center', padding: '12px 16px',
                  borderBottom: '1px solid rgba(0,0,0,0.04)', fontSize: 13,
                }}>
                  <div style={{ width: 50, color: '#86909c', fontWeight: 600 }}>#{num}</div>
                  <div style={{ flex: 2, textAlign: 'center' }}>
                    {isRunning ? (
                      <Tag icon={<SyncOutlined spin />} color="processing">执行中</Tag>
                    ) : (
                      <Tag style={{
                        background: isCompleted ? 'rgba(14,165,160,0.12)' : 'rgba(250,173,20,0.14)',
                        color: isCompleted ? '#0ea5a0' : '#d48806',
                        border: `1px solid ${isCompleted ? 'rgba(14,165,160,0.28)' : 'rgba(250,173,20,0.3)'}`,
                      }}>
                        {isCompleted ? '已完成' : '进行中'}
                      </Tag>
                    )}
                  </div>
                  <div style={{ flex: 3 }}>
                    <span style={{ color: '#0ea5a0', fontWeight: 500 }}>{exec.passed}</span>
                    <span style={{ color: '#86909c' }}> / </span>
                    <span style={{ color: '#e8453c', fontWeight: 500 }}>{exec.failed + exec.error}</span>
                    <span style={{ color: '#86909c' }}> / </span>
                    <span>{exec.totalScenarios}</span>
                  </div>
                  <div style={{ flex: 2, textAlign: 'center' }}>
                    {exec.passRate != null ? (
                      <span style={{ fontWeight: 600, color: exec.passRate >= 95 ? '#0ea5a0' : exec.passRate >= 80 ? '#faad14' : '#e8453c' }}>
                        {exec.passRate}%
                      </span>
                    ) : '-'}
                  </div>
                  <div style={{ flex: 2, textAlign: 'right', fontFamily: 'var(--font-mono)', color: '#86909c' }}>
                    {fmt(exec.totalDurationMs)}
                  </div>
                  <div style={{ width: 112, textAlign: 'center' }}>
                    <TimeCell value={exec.executedAt} />
                  </div>
                  <div style={{ flex: 2, textAlign: 'center' }}>
                    <Button type="link" size="small" icon={<BarChartOutlined />}
                      onClick={() => navigate(`/projects/${projectId}/reports/${exec.id}`)}>
                      查看报告
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Card>
    </div>
  )
}
