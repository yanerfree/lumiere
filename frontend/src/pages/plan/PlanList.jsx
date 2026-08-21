import { useState, useEffect, useCallback } from 'react'
import { Tag, Button, Radio, Input, Space, Modal, Form, Select, InputNumber, message, Empty, Spin, Pagination, Tooltip } from 'antd'
import { PlusOutlined, SearchOutlined, ReloadOutlined, PlayCircleOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../utils/request'
import { buildEnvOptions } from '../../utils/env'
import { useBranch } from '../../utils/branch'
import CasePicker from '../../components/CasePicker'

const statusMap = {
  draft:          { label: '草稿',     color: '#86909c', bg: 'rgba(0,0,0,0.03)', dot: '#c9cdd4' },
  executing:      { label: '执行中',   color: '#0ea5a0', bg: '#e0f7f6', dot: '#0ea5a0' },
  paused:         { label: '已暂停',   color: '#faad14', bg: '#fffbe6', dot: '#faad14' },
  pending_manual: { label: '待录入',   color: '#7c5cbf', bg: 'rgba(124,92,191,0.06)', dot: '#7c5cbf' },
  completed:      { label: '已完成',   color: '#0ea5a0', bg: '#e0f7f6', dot: '#0ea5a0' },
  archived:       { label: '已归档',   color: '#86909c', bg: 'rgba(0,0,0,0.03)', dot: '#c9cdd4' },
}

const th = { fontSize: 12, color: '#86909c', fontWeight: 500, whiteSpace: 'nowrap' }

export default function PlanList() {
  const navigate = useNavigate()
  const { projectId } = useParams()
  const [globalBranchId] = useBranch(projectId)
  const [plans, setPlans] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const planType = Form.useWatch('planType', form)
  const watchedCaseIds = Form.useWatch('caseIds', form)

  const [branches, setBranches] = useState([])
  const [environments, setEnvironments] = useState([])
  const [channels, setChannels] = useState([])
  const [selectedBranch, setSelectedBranch] = useState(null)
  const [casePickerOpen, setCasePickerOpen] = useState(false)
  const [editingPlan, setEditingPlan] = useState(null)

  const fetchPlans = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ page, pageSize })
      if (tab) params.set('status', tab)
      if (globalBranchId) params.set('branchId', globalBranchId)
      const res = await api.get(`/projects/${projectId}/plans?${params}`)
      setPlans(res.data || [])
      setTotal(res.pagination?.total || 0)
    } catch { /* */ } finally { setLoading(false) }
  }, [projectId, tab, page, pageSize, globalBranchId])

  useEffect(() => { fetchPlans() }, [fetchPlans])

  const openCreate = async () => {
    form.resetFields()
    setCreateOpen(true)
    try {
      const [brRes, envRes, chRes] = await Promise.all([
        api.get(`/projects/${projectId}/branches`),
        api.get(`/projects/${projectId}/environments`),
        api.get('/channels'),
      ])
      setBranches(brRes.data || [])
      setEnvironments(envRes.data || [])
      setChannels(chRes.data || [])
      // 默认选中全局分支，退回第一个 active 分支
      const list = brRes.data || []
      const preferred = list.find(b => b.id === globalBranchId) || list.find(b => b.status === 'active')
      if (preferred) {
        setSelectedBranch(preferred.id)
      }
    } catch { /* */ }
  }

  const handleBranchChange = (branchId) => {
    setSelectedBranch(branchId)
    form.setFieldValue('caseIds', [])
  }

  const handleCreate = async () => {
    let values
    try { values = await form.validateFields() } catch { return }
    setSaving(true)
    try {
      await api.post(`/projects/${projectId}/plans`, {
        name: values.name,
        planType: values.planType,
        testType: values.testType,
        caseIds: values.caseIds,
        branchId: selectedBranch || globalBranchId || null,
        environmentId: values.environmentId || null,
        channelId: values.channelId || null,
        retryCount: values.retryCount || 0,
        circuitBreaker: values.planType === 'automated' ? {
          consecutive: values.consecutive || 5,
          rate: values.rate || 50,
        } : null,
      })
      message.success('计划创建成功')
      setCreateOpen(false)
      fetchPlans()
    } catch { /* */ } finally { setSaving(false) }
  }

  const handleQuickExecute = async (e, planId) => {
    e.stopPropagation()
    try {
      await api.post(`/projects/${projectId}/plans/${planId}/execute`)
      message.success('已启动执行')
      fetchPlans()
    } catch (err) {
      message.error(err?.response?.data?.error?.message || err?.message || '执行失败')
    }
  }

  /**
   * 计划生命周期动作。后端四条端点（pause/resume/abort/archive）一直都在，
   * 页面上却一个都没接 —— 于是「已暂停」「已归档」这两个筛选页签永远是空的：
   * 有筛选、有状态色、有标签，就是没有任何路径能走到那个状态。
   * unarchive 是这次补的：archive 收 draft/completed，而 reopen 只认 completed，
   * 归档点下去原本回不来。
   */
  const LIFECYCLE = {
    pause: { label: '暂停', ok: '已暂停' },
    resume: { label: '恢复', ok: '已恢复执行' },
    abort: { label: '终止', ok: '已终止，未执行的记为跳过', confirm: '终止后未执行的用例会被记为「跳过」，确定？' },
    archive: { label: '归档', ok: '已归档' },
    unarchive: { label: '取消归档', ok: '已取消归档' },
  }

  const handleLifecycle = (e, planId, planName, action) => {
    e.stopPropagation()
    const cfg = LIFECYCLE[action]
    const run = async () => {
      try {
        await api.post(`/projects/${projectId}/plans/${planId}/${action}`)
        message.success(cfg.ok)
        fetchPlans()
      } catch (err) {
        message.error(err?.response?.data?.error?.message || err?.message || `${cfg.label}失败`)
      }
    }
    if (cfg.confirm) {
      Modal.confirm({
        title: `确认${cfg.label}`,
        content: `计划「${planName}」：${cfg.confirm}`,
        okText: cfg.label, cancelText: '取消', okButtonProps: { danger: true }, onOk: run,
      })
    } else {
      run()
    }
  }

  const handleQuickDelete = (e, planId, planName) => {
    e.stopPropagation()
    Modal.confirm({
      title: '确认删除',
      content: `确定删除计划「${planName}」？`,
      okText: '删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api.del(`/projects/${projectId}/plans/${planId}`)
          message.success('删除成功')
          fetchPlans()
        } catch (err) {
          message.error(err?.response?.data?.error?.message || err?.message || '删除失败')
        }
      },
    })
  }

  const filteredPlans = keyword
    ? plans.filter(p => p.name.toLowerCase().includes(keyword.toLowerCase()))
    : plans

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 70px)' }}>
      {/* Toolbar */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, flexShrink: 0 }}>
        <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0, color: '#1d2129' }}>测试计划</h2>
        <Space size={8}>
          <Input
            prefix={<SearchOutlined style={{ color: '#c9cdd4' }} />}
            placeholder="搜索计划名称"
            value={keyword} onChange={e => setKeyword(e.target.value)}
            allowClear style={{ width: 200 }} size="small"
          />
          <Button size="small" icon={<ReloadOutlined />} onClick={fetchPlans} loading={loading}>刷新</Button>
          <Button size="small" type="primary" icon={<PlusOutlined />} onClick={openCreate}>创建计划</Button>
        </Space>
      </div>

      {/* Tabs */}
      <div style={{ marginBottom: 6, flexShrink: 0 }}>
        <Radio.Group value={tab} onChange={e => { setTab(e.target.value); setPage(1) }} size="small" buttonStyle="solid">
          <Radio.Button value="">全部 ({total})</Radio.Button>
          {Object.entries(statusMap).map(([k, v]) => (
            <Radio.Button key={k} value={k}>{v.label}</Radio.Button>
          ))}
        </Radio.Group>
      </div>

      {loading ? <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spin /></div> :
        filteredPlans.length === 0 ? <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Empty description="暂无计划" /></div> : <>
        {/* Table */}
        <div style={{ background: 'rgba(255,255,255,0.3)', border: 'none', borderRadius: 16, backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)', flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'center', padding: '0 16px', height: 36, background: 'rgba(0,0,0,0.02)', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0 }}>
            <div style={{ flex: 4, ...th }}>计划名称</div>
            <div style={{ width: 62, textAlign: 'center', flexShrink: 0, ...th }}>类型</div>
            <div style={{ width: 62, textAlign: 'center', flexShrink: 0, ...th }}>执行</div>
            <div style={{ width: 90, textAlign: 'center', flexShrink: 0, ...th }}>环境</div>
            <div style={{ width: 60, textAlign: 'center', flexShrink: 0, ...th }}>用例</div>
            <div style={{ width: 80, textAlign: 'center', flexShrink: 0, ...th }}>状态</div>
            <div style={{ width: 240, textAlign: 'center', flexShrink: 0, ...th }}>操作</div>
          </div>
          {/* Body */}
          <div style={{ flex: 1, overflow: 'auto' }}>
            {filteredPlans.map((plan, i) => {
              const s = statusMap[plan.status] || statusMap.draft
              return (
                <div key={plan.id}
                  onClick={() => navigate(`/projects/${projectId}/plans/${plan.id}`)}
                  style={{
                    display: 'flex', alignItems: 'center', padding: '0 16px', height: 44,
                    borderBottom: '1px solid rgba(0,0,0,0.04)', cursor: 'pointer', transition: 'background .12s',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(0,0,0,0.02)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  {/* Name + meta */}
                  <div style={{ flex: 4, minWidth: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontWeight: 500, fontSize: 13, color: '#1d2129', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {plan.name}
                    </span>
                    <span style={{ fontSize: 11, color: '#c9cdd4', flexShrink: 0 }}>
                      {new Date(plan.createdAt).toLocaleDateString('zh-CN')}
                    </span>
                  </div>

                  {/* Type：自动化/手动 —— 这一列回答的不是"跑的什么" */}
                  <div style={{ width: 62, textAlign: 'center', flexShrink: 0 }}>
                    <span style={{ fontSize: 12, color: '#86909c' }}>
                      {plan.planType === 'automated' ? '自动化' : '手动'}
                    </span>
                  </div>

                  {/* 执行方式：UI 脚本还是接口场景。库里一直有 test_type，
                      只是从没显示过 —— 12 条计划全是接口，页面上完全看不出来 */}
                  <div style={{ width: 62, textAlign: 'center', flexShrink: 0 }}>
                    {plan.testType ? (
                      <span style={{
                        fontSize: 11, padding: '1px 6px', borderRadius: 6,
                        background: plan.testType === 'e2e' ? '#f5f0ff' : '#e0f7f6',
                        color: plan.testType === 'e2e' ? '#7c5cbf' : '#0ea5a0',
                      }}>{plan.testType === 'e2e' ? 'UI' : '接口'}</span>
                    ) : <span style={{ fontSize: 11, color: '#c9cdd4' }}>—</span>}
                  </div>

                  {/* Environment */}
                  <div style={{ width: 90, textAlign: 'center', flexShrink: 0 }}>
                    {plan.environmentName ? (
                      <span style={{ fontSize: 12, color: '#86909c' }}>
                        {plan.environmentName}
                      </span>
                    ) : <span style={{ fontSize: 11, color: '#c9cdd4' }}>-</span>}
                  </div>

                  {/* Case count */}
                  <div style={{ width: 60, textAlign: 'center', flexShrink: 0, fontSize: 13, color: '#4e5969', fontFamily: 'var(--font-mono)' }}>
                    {plan.caseCount}
                  </div>

                  {/* Status */}
                  <div style={{ width: 80, textAlign: 'center', flexShrink: 0 }}>
                    <span style={{
                      display: 'inline-flex', alignItems: 'center', gap: 4,
                      fontSize: 11, padding: '2px 8px', borderRadius: 12,
                      background: s.dot, color: '#fff',
                    }}>
                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'transparent' }} />
                      {s.label}
                    </span>
                  </div>

                  {/* Actions */}
                  <div style={{ width: 240, flexShrink: 0, display: 'flex', justifyContent: 'flex-end', gap: 4 }}>
                    {['draft', 'completed', 'paused'].includes(plan.status) && (
                      <Button type="text" size="small" style={{ fontSize: 12, color: '#86909c' }}
                        onClick={async e => {
                          e.stopPropagation()
                          try {
                            const res = await api.get(`/projects/${projectId}/plans/${plan.id}`)
                            setEditingPlan(res.data)
                            setCasePickerOpen(true)
                          } catch { message.error('加载计划详情失败') }
                        }}>修改用例</Button>
                    )}
                    {plan.status === 'draft' && (
                      <Button type="text" size="small" style={{ fontSize: 12, color: '#0ea5a0' }}
                        onClick={e => handleQuickExecute(e, plan.id)}>执行</Button>
                    )}
                    {(plan.status === 'completed' || plan.status === 'paused') && (
                      <Button type="text" size="small" style={{ fontSize: 12, color: '#0ea5a0' }}
                        onClick={e => {
                          e.stopPropagation()
                          Modal.confirm({
                            title: '确认重新执行',
                            content: `确定重新执行计划「${plan.name}」？将产生一条新的执行记录。`,
                            okText: '确认执行',
                            cancelText: '取消',
                            onOk: () => handleQuickExecute({ stopPropagation: () => {} }, plan.id),
                          })
                        }}>重新执行</Button>
                    )}
                    {/* 生命周期：状态决定能做什么，和后端的前置校验一一对应 */}
                    {plan.status === 'executing' && (
                      <Button type="text" size="small" style={{ fontSize: 12, color: '#faad14' }}
                        onClick={e => handleLifecycle(e, plan.id, plan.name, 'pause')}>暂停</Button>
                    )}
                    {plan.status === 'paused' && (
                      <Button type="text" size="small" style={{ fontSize: 12, color: '#0ea5a0' }}
                        onClick={e => handleLifecycle(e, plan.id, plan.name, 'resume')}>恢复</Button>
                    )}
                    {['executing', 'paused'].includes(plan.status) && (
                      <Button type="text" size="small" danger style={{ fontSize: 12 }}
                        onClick={e => handleLifecycle(e, plan.id, plan.name, 'abort')}>终止</Button>
                    )}
                    {['draft', 'completed'].includes(plan.status) && (
                      <Button type="text" size="small" style={{ fontSize: 12, color: '#86909c' }}
                        onClick={e => handleLifecycle(e, plan.id, plan.name, 'archive')}>归档</Button>
                    )}
                    {plan.status === 'archived' && (
                      <Button type="text" size="small" style={{ fontSize: 12, color: '#0ea5a0' }}
                        onClick={e => handleLifecycle(e, plan.id, plan.name, 'unarchive')}>取消归档</Button>
                    )}
                    {plan.status !== 'executing' && (
                      <Button type="text" size="small" danger style={{ fontSize: 12 }}
                        onClick={e => handleQuickDelete(e, plan.id, plan.name)}>删除</Button>
                    )}
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

      {/* 创建计划弹窗 */}
      <Modal title="创建测试计划" open={createOpen} onOk={handleCreate} onCancel={() => setCreateOpen(false)}
        okText="创建" cancelText="取消" confirmLoading={saving} width={600}>
        <Form form={form} layout="vertical" style={{ marginTop: 16 }} initialValues={{ planType: 'automated', testType: 'api', retryCount: 2 }}>
          <Form.Item name="name" label="计划名称" rules={[{ required: true, message: '请输入计划名称' }]}>
            <Input placeholder="如：API 回归测试 — Sprint 13" />
          </Form.Item>
          <div style={{ display: 'flex', gap: 16 }}>
            <Form.Item name="planType" label="计划类型" rules={[{ required: true }]} style={{ flex: 1 }}>
              <Select options={[{ value: 'automated', label: '自动化' }, { value: 'manual', label: '手动' }]} />
            </Form.Item>
            <Form.Item name="testType" label="测试类型" rules={[{ required: true }]} style={{ flex: 1 }} extra="同一计划不可混合 API 和 E2E">
              <Select options={[{ value: 'api', label: 'API' }, { value: 'e2e', label: 'E2E' }]} />
            </Form.Item>
          </div>
          <Form.Item label="分支配置（选择后加载该分支的用例）">
            <Select placeholder="选择分支" value={selectedBranch} onChange={handleBranchChange}
              options={branches.filter(b => b.status === 'active').map(b => ({ value: b.id, label: `${b.name} (${b.branch})` }))} />
          </Form.Item>
          <Form.Item name="caseIds" label="选择用例" rules={[{ required: true, message: '请至少选择 1 条用例' }]}>
            <div>
              <Space>
                <Button onClick={() => setCasePickerOpen(true)} disabled={!selectedBranch}>
                  {selectedBranch ? '选择用例' : '请先选择分支'}
                </Button>
                <span style={{ fontSize: 13, color: '#86909c' }}>
                  已选 {(watchedCaseIds || []).length} 条
                </span>
              </Space>
            </div>
          </Form.Item>
          <div style={{ display: 'flex', gap: 16 }}>
            <Form.Item name="environmentId" label="目标环境"
              rules={planType === 'automated' ? [{ required: true, message: '自动化计划必须选择环境' }] : []} style={{ flex: 1 }}>
              <Select placeholder="选择环境" allowClear popupMatchSelectWidth={false} options={buildEnvOptions(environments)} />
            </Form.Item>
            <Form.Item name="channelId" label="通知渠道" style={{ flex: 1 }}>
              <Select placeholder="选择通知渠道" allowClear options={channels.map(c => ({ value: c.id, label: c.name }))} />
            </Form.Item>
          </div>
          {planType === 'automated' && (
            <div style={{ display: 'flex', gap: 16 }}>
              <Form.Item name="retryCount" label="失败重试" style={{ flex: 1 }}>
                <InputNumber min={0} max={3} style={{ width: '100%' }} addonAfter="次" />
              </Form.Item>
              <Form.Item name="consecutive" label="熔断-连续失败" style={{ flex: 1 }} initialValue={5}>
                <InputNumber min={1} max={100} style={{ width: '100%' }} addonAfter="条" />
              </Form.Item>
              <Form.Item name="rate" label="熔断-失败率" style={{ flex: 1 }} initialValue={50}>
                <InputNumber min={1} max={100} style={{ width: '100%' }} addonAfter="%" />
              </Form.Item>
            </div>
          )}
        </Form>
      </Modal>

      <CasePicker
        open={casePickerOpen}
        projectId={projectId}
        selectedIds={editingPlan ? (editingPlan.caseIds || []) : (form.getFieldValue('caseIds') || [])}
        onOk={async (ids) => {
          if (editingPlan) {
            try {
              await api.put(`/projects/${projectId}/plans/${editingPlan.id}`, { caseIds: ids })
              message.success('用例已更新')
              fetchPlans()
            } catch (err) {
              message.error(err?.response?.data?.error?.message || err?.message || '保存失败')
            }
          } else {
            form.setFieldsValue({ caseIds: ids })
          }
          setCasePickerOpen(false)
          setEditingPlan(null)
        }}
        onCancel={() => { setCasePickerOpen(false); setEditingPlan(null) }}
      />
    </div>
  )
}
