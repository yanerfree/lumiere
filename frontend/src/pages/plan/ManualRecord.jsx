import { useState, useMemo, useEffect, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Button, Radio, Input, Tag, Progress, message, Tooltip, Badge, Spin } from 'antd'
import { ArrowLeftOutlined, CheckCircleFilled, CloseCircleFilled, ClockCircleOutlined, UserOutlined } from '@ant-design/icons'
import { api } from '../../utils/request'
import { PERM } from '../../utils/permissions'
import { usePermissions } from '../../utils/PermissionContext'

const PRIORITY_CONFIG = {
  P0: { color: '#fff', bg: '#e8453c' },
  P1: { color: '#fff', bg: '#ff7d00' },
  P2: { color: '#fff', bg: '#4e8af0' },
  P3: { color: '#fff', bg: 'rgba(0,0,0,0.15)' },
}

const FILTER_OPTIONS = [
  { label: '全部', value: 'all' },
  { label: '待录入', value: 'pending' },
  { label: '已通过', value: 'passed' },
  { label: '已失败', value: 'failed' },
]

export default function ManualRecord() {
  const { projectId, planId } = useParams()
  const navigate = useNavigate()
  const { has } = usePermissions()
  const canReport = has(PERM.REPORT_WRITE)

  const [plan, setPlan] = useState(null)
  const [scenarios, setScenarios] = useState([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState(null)
  const [filter, setFilter] = useState('all')
  const [saving, setSaving] = useState(false)

  // 本地编辑状态（result + remark）
  const [localEdits, setLocalEdits] = useState({})

  const fetchData = useCallback(async () => {
    if (!projectId || !planId) return
    setLoading(true)
    try {
      const [planRes, resultsRes] = await Promise.all([
        api.get(`/projects/${projectId}/plans/${planId}`),
        api.get(`/projects/${projectId}/plans/${planId}/results`),
      ])
      setPlan(planRes.data)
      if (resultsRes.data) {
        const list = resultsRes.data.scenarios || []
        setScenarios(list)
        if (!selectedId && list.length > 0) {
          setSelectedId(list[0].id)
        }
      }
    } catch { /* */ } finally { setLoading(false) }
  }, [projectId, planId])

  useEffect(() => { fetchData() }, [fetchData])

  const filteredCases = useMemo(() => {
    if (filter === 'all') return scenarios
    return scenarios.filter(s => {
      const edit = localEdits[s.id]
      const status = edit?.result || s.status
      if (filter === 'pending') return status === 'pending'
      return status === filter
    })
  }, [scenarios, filter, localEdits])

  const selected = scenarios.find(s => s.id === selectedId)
  const selectedEdit = selectedId ? (localEdits[selectedId] || {}) : {}

  const getEffectiveStatus = (s) => localEdits[s.id]?.result || s.status

  const recordedCount = scenarios.filter(s => getEffectiveStatus(s) !== 'pending').length
  const totalCount = scenarios.length

  const updateLocalEdit = (id, updates) => {
    setLocalEdits(prev => ({ ...prev, [id]: { ...prev[id], ...updates } }))
  }

  const handleSave = async () => {
    const result = selectedEdit.result
    if (!result) {
      message.warning('请先选择测试结果')
      return
    }
    setSaving(true)
    try {
      const res = await api.post(`/projects/${projectId}/plans/${planId}/manual-record`, {
        scenarioId: selectedId,
        status: result,
        remark: selectedEdit.remark || null,
      })
      setScenarios(prev => prev.map(s => s.id === selectedId ? { ...s, status: result, remark: selectedEdit.remark } : s))

      if (res.data?.allDone) {
        message.success('全部录入完成，计划已自动完成')
        navigate(-1)
        return
      }

      message.success('已保存')
      const pending = scenarios.filter(s => s.id !== selectedId && getEffectiveStatus(s) === 'pending')
      if (pending.length > 0) {
        setSelectedId(pending[0].id)
      }
    } catch { /* */ } finally { setSaving(false) }
  }

  const getStatusIcon = (s) => {
    const status = getEffectiveStatus(s)
    if (status === 'passed') return <CheckCircleFilled style={{ color: '#0ea5a0', fontSize: 16 }} />
    if (status === 'failed') return <CloseCircleFilled style={{ color: '#e8453c', fontSize: 16 }} />
    return <ClockCircleOutlined style={{ color: '#c9cdd4', fontSize: 16 }} />
  }

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin /></div>

  const pendingCount = scenarios.filter(s => s.id !== selectedId && getEffectiveStatus(s) === 'pending').length
  const isLastPending = selectedEdit.result && pendingCount === 0

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'transparent' }}>
      {/* 顶栏 */}
      <div style={{ height: 54, background: 'transparent', borderBottom: '1px solid rgba(0,0,0,0.04)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 20px', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)} />
          <span style={{ fontSize: 15, fontWeight: 600, color: '#1d2129' }}>{plan?.name || '手动录入'}</span>
          <Tag color="blue" style={{ borderRadius: 12 }}>手动录入</Tag>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 13, color: '#86909c' }}>已录入</span>
            <span style={{ fontSize: 15, fontWeight: 600, color: '#1d2129' }}>{recordedCount}/{totalCount}</span>
            {totalCount > 0 && <Progress percent={Math.round(recordedCount / totalCount * 100)} size="small" style={{ width: 120, marginBottom: 0 }} strokeColor="#0ea5a0" />}
          </div>
        </div>
      </div>

      {/* 主内容 */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* 左侧列表 */}
        <div style={{ width: 320, background: 'transparent', borderRight: '1px solid rgba(0,0,0,0.04)', display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
          <div style={{ padding: '12px 14px', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
            <Radio.Group value={filter} onChange={e => setFilter(e.target.value)} optionType="button" buttonStyle="solid" size="small" options={FILTER_OPTIONS} />
          </div>
          <div style={{ flex: 1, overflow: 'auto' }}>
            {filteredCases.map(s => (
              <div key={s.id} onClick={() => setSelectedId(s.id)} style={{
                padding: '10px 14px', cursor: 'pointer', borderBottom: '1px solid rgba(0,0,0,0.04)',
                background: selectedId === s.id ? '#e0f7f6' : 'transparent',
                borderLeft: selectedId === s.id ? '3px solid #0ea5a0' : '3px solid transparent',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {getStatusIcon(s)}
                  <span style={{ fontSize: 13, fontWeight: 500, color: '#1d2129', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {s.scenarioName}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4, paddingLeft: 24 }}>
                  <span style={{ fontSize: 11, color: '#c9cdd4' }}>{s.caseCode}</span>
                </div>
              </div>
            ))}
            {filteredCases.length === 0 && <div style={{ textAlign: 'center', padding: 40, color: '#c9cdd4', fontSize: 13 }}>暂无用例</div>}
          </div>
        </div>

        {/* 右侧录入 */}
        <div style={{ flex: 1, overflow: 'auto', padding: '20px 28px' }}>
          {selected ? (<>
            <div style={{ marginBottom: 24 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{ fontSize: 12, color: '#86909c' }}>{selected.caseCode}</span>
              </div>
              <h2 style={{ fontSize: 18, fontWeight: 600, color: '#1d2129', margin: 0 }}>{selected.scenarioName}</h2>
            </div>

            {/* 录入表单 */}
            <div style={{ marginTop: 24, padding: '20px 24px', background: 'transparent', borderRadius: 12, border: '1px solid rgba(0,0,0,0.05)' }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: '#1d2129', marginBottom: 16 }}>录入结果</div>
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 13, color: '#4e5969', marginBottom: 8 }}>测试结果</div>
                <Radio.Group value={selectedEdit.result || (selected.status !== 'pending' ? selected.status : undefined)} onChange={e => updateLocalEdit(selected.id, { result: e.target.value })} size="large">
                  <Radio.Button value="passed" style={{ borderRadius: '8px 0 0 8px', ...((selectedEdit.result || selected.status) === 'passed' ? { background: '#0ea5a0', borderColor: '#0ea5a0', color: '#fff' } : {}) }}>
                    <CheckCircleFilled style={{ marginRight: 4 }} /> 通过
                  </Radio.Button>
                  <Radio.Button value="failed" style={{ borderRadius: '0 8px 8px 0', ...((selectedEdit.result || selected.status) === 'failed' ? { background: '#ff7875', borderColor: '#ff7875', color: '#fff' } : {}) }}>
                    <CloseCircleFilled style={{ marginRight: 4 }} /> 失败
                  </Radio.Button>
                </Radio.Group>
              </div>
              <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: 13, color: '#4e5969', marginBottom: 8 }}>备注（可选）</div>
                <Input.TextArea value={selectedEdit.remark ?? selected.remark ?? ''} onChange={e => updateLocalEdit(selected.id, { remark: e.target.value })} placeholder="填写测试过程中的发现或问题描述..." rows={3} style={{ resize: 'none' }} />
              </div>
              {canReport && (
                <Button type="primary" onClick={handleSave} loading={saving} block style={{ height: 40 }}>
                  {isLastPending ? '保存' : '保存并下一条'}
                </Button>
              )}
            </div>
          </>) : (
            <div style={{ textAlign: 'center', padding: 80, color: '#c9cdd4' }}>请从左侧选择一条用例</div>
          )}
        </div>
      </div>
    </div>
  )
}
