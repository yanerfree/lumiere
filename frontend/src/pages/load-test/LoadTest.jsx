import { useState, useEffect, useRef, useMemo } from 'react'
import {
  Button, Space, Input, Select, Tag, Radio, Popconfirm, Empty,
  InputNumber, message, Card, Modal, Collapse, Tabs,
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, SaveOutlined, PlayCircleOutlined,
  PauseCircleOutlined, ThunderboltOutlined, HistoryOutlined,
  CaretRightOutlined, CloseOutlined, ExportOutlined, CopyOutlined, DownloadOutlined,
} from '@ant-design/icons'
import { api, getValidToken } from '../../utils/request'

const MONO = 'var(--font-mono)'
const ACCENT = '#e8453c'
const MC = { GET: '#0ea5a0', POST: '#ff7d00', PUT: '#4e8af0', DELETE: '#e8453c', PATCH: '#7c5cbf' }

const GLASS = {
  background: 'rgba(255,255,255,0.35)',
  borderRadius: 12,
  border: '1px solid rgba(255,255,255,0.18)',
  backdropFilter: 'blur(8px)',
}

const emptyScenario = {
  name: '', description: '', concurrentUsers: 10, rampUpSeconds: 5,
  totalIterations: 100, durationSeconds: null, variables: [],
}

const emptyStep = {
  name: '', method: 'GET', url: '', headers: [], body: '',
  bodyType: 'none', extractions: [], assertions: [],
}

/* ───────────────────────── component ───────────────────────── */

export default function LoadTest() {
  /* — scenarios — */
  const [scenarios, setScenarios] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [k6, setK6] = useState({ open: false, loading: false, filename: '', script: '', guide: '' })
  const [form, setForm] = useState({ ...emptyScenario })
  const [mode, setMode] = useState('iterations')
  const [dirty, setDirty] = useState(false)

  /* — steps — */
  const [steps, setSteps] = useState([])
  const [expandedStepId, setExpandedStepId] = useState(null)

  /* — tabs — */
  const [activeTab, setActiveTab] = useState('config')

  /* — running — */
  const [running, setRunning] = useState(false)
  const [runId, setRunId] = useState(null)
  const [metrics, setMetrics] = useState(null)
  const [elapsed, setElapsed] = useState(0)
  const sseRef = useRef(null)
  const timerRef = useRef(null)

  /* — history — */
  const [runs, setRuns] = useState([])
  const [globalRuns, setGlobalRuns] = useState([])
  const [historyModalOpen, setHistoryModalOpen] = useState(false)
  const [detailRun, setDetailRun] = useState(null)

  /* ─────── load scenarios ─────── */
  const loadScenarios = async () => {
    try {
      const res = await api.get('/load-test/scenarios')
      setScenarios(Array.isArray(res) ? res : res.data || [])
    } catch { /* ignore */ }
  }

  useEffect(() => { loadScenarios() }, [])

  /* ─────── select scenario ─────── */
  const selectScenario = async (id) => {
    setSelectedId(id)
    setActiveTab('config')
    setDirty(false)
    try {
      const res = await api.get(`/load-test/scenarios/${id}`)
      const s = res.data || res
      setForm({
        name: s.name || '',
        description: s.description || '',
        concurrentUsers: s.concurrentUsers ?? 10,
        rampUpSeconds: s.rampUpSeconds ?? 5,
        totalIterations: s.totalIterations ?? 100,
        durationSeconds: s.durationSeconds || null,
        variables: s.variables || [],
      })
      setMode(s.durationSeconds > 0 ? 'duration' : 'iterations')
      loadSteps(id)
      loadRuns(id)
    } catch { message.error('加载场景失败') }
  }

  /* ─────── create scenario ─────── */
  const createScenario = async () => {
    try {
      // 带序号，别让列表里堆一排一模一样的「新场景」—— 建三个就分不清谁是谁了。
      // 取现有同名里的最大序号 +1，删掉中间某个也不会撞名。
      const used = scenarios
        .map(s => /^新场景(?:\s*(\d+))?$/.exec((s.name || '').trim()))
        .filter(Boolean)
        .map(m => Number(m[1] || 1))
      const nextNo = used.length ? Math.max(...used) + 1 : 1
      const res = await api.post('/load-test/scenarios', { ...emptyScenario, name: `新场景 ${nextNo}` })
      message.success('场景已创建')
      await loadScenarios()
      const id = res.data?.id || res.id
      if (id) selectScenario(id)
    } catch { message.error('创建失败') }
  }

  /* ─────── save scenario ─────── */
  const saveScenario = async () => {
    if (!selectedId) return
    const payload = { ...form }
    if (mode === 'iterations') payload.durationSeconds = null
    else payload.totalIterations = null
    try {
      await api.put(`/load-test/scenarios/${selectedId}`, payload)
      message.success('已保存')
      setDirty(false)
      loadScenarios()
    } catch { message.error('保存失败') }
  }

  /* ─────── delete scenario ─────── */
  const deleteScenario = async (id) => {
    try {
      await api.del(`/load-test/scenarios/${id}`)
      message.success('已删除')
      if (selectedId === id) { setSelectedId(null); setForm({ ...emptyScenario }); setSteps([]) }
      loadScenarios()
    } catch { message.error('删除失败') }
  }

  /* ─────── steps ─────── */
  const loadSteps = async (scenarioId) => {
    try {
      const res = await api.get(`/load-test/scenarios/${scenarioId}/steps`)
      setSteps(Array.isArray(res) ? res : res.data || [])
    } catch { setSteps([]) }
  }

  const createStep = async () => {
    if (!selectedId) return
    try {
      await api.post(`/load-test/scenarios/${selectedId}/steps`, { ...emptyStep })
      message.success('步骤已添加')
      loadSteps(selectedId)
    } catch { message.error('添加步骤失败') }
  }

  const updateStep = async (stepId, data) => {
    try {
      await api.put(`/load-test/steps/${stepId}`, data)
      loadSteps(selectedId)
    } catch { message.error('保存步骤失败') }
  }

  const deleteStep = async (stepId) => {
    try {
      await api.del(`/load-test/steps/${stepId}`)
      message.success('步骤已删除')
      loadSteps(selectedId)
    } catch { message.error('删除步骤失败') }
  }

  /* ─────── run test ─────── */
  const startTest = async () => {
    if (!selectedId) return
    try {
      const res = await api.post(`/load-test/scenarios/${selectedId}/run`)
      const rid = res.data?.runId || res.runId
      setRunId(rid)
      setRunning(true)
      setMetrics(null)
      setElapsed(0)
      timerRef.current = setInterval(() => setElapsed(e => e + 1), 1000)
      startSSE(rid)
    } catch { message.error('启动测试失败') }
  }

  const cancelTest = async () => {
    if (!runId) return
    try {
      await api.post(`/load-test/runs/${runId}/cancel`)
      message.info('测试已取消')
    } catch { /* ignore */ }
    stopRunning()
  }

  const stopRunning = () => {
    setRunning(false)
    if (sseRef.current) { sseRef.current.abort(); sseRef.current = null }
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
  }

  useEffect(() => () => { stopRunning() }, [])

  /* ─────── SSE stream ─────── */
  const startSSE = (rid) => {
    const controller = new AbortController()
    sseRef.current = controller

    ;(async () => {
      try {
        const token = await getValidToken()
        const res = await fetch(`/api/load-test/runs/${rid}/stream`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          signal: controller.signal,
        })
        if (!res.ok) { message.error('SSE 连接失败'); stopRunning(); return }
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const parts = buffer.split('\n\n')
          buffer = parts.pop()
          for (const part of parts) {
            const trimmed = part.trim()
            if (!trimmed.startsWith('data: ')) continue
            try {
              const event = JSON.parse(trimmed.slice(6))
              if (event.type === 'metrics') setMetrics(event.data)
              if (event.type === 'done') {
                setMetrics(event.data)
                stopRunning()
                if (selectedId) loadRuns(selectedId)
              }
              if (event.type === 'error') {
                message.error(event.data?.message || '测试执行异常')
                stopRunning()
              }
            } catch { /* skip */ }
          }
        }
      } catch (err) {
        if (err.name !== 'AbortError') { message.error('连接断开'); stopRunning() }
      }
    })()
  }

  /* ─────── history ─────── */
  const loadRuns = async (scenarioId) => {
    try {
      const res = await api.get(`/load-test/scenarios/${scenarioId}/runs`)
      setRuns(Array.isArray(res) ? res : res.data || [])
    } catch { setRuns([]) }
  }

  const loadGlobalRuns = async () => {
    const allRuns = []
    for (const s of scenarios) {
      try {
        const res = await api.get(`/load-test/scenarios/${s.id}/runs`)
        ;(Array.isArray(res) ? res : res.data || []).forEach(r => allRuns.push({ ...r, scenarioName: s.name }))
      } catch { /* skip */ }
    }
    allRuns.sort((a, b) => new Date(b.createdAt || b.startedAt) - new Date(a.createdAt || a.startedAt))
    setGlobalRuns(allRuns)
  }

  const viewRunDetail = async (rid) => {
    try {
      const res = await api.get(`/load-test/runs/${rid}`)
      setDetailRun(res.data || res)
    } catch { message.error('加载详情失败') }
  }

  const deleteRun = async (rid) => {
    try {
      await api.del(`/load-test/runs/${rid}`)
      message.success('已删除')
      if (selectedId) loadRuns(selectedId)
    } catch { message.error('删除失败') }
  }

  /* ─────── form helpers ─────── */
  const setField = (k, v) => { setForm(f => ({ ...f, [k]: v })); setDirty(true) }

  const addVariable = () => {
    setForm(f => ({ ...f, variables: [...f.variables, { name: '', values: '' }] }))
    setDirty(true)
  }

  const updateVariable = (idx, key, val) => {
    setForm(f => {
      const vars = [...f.variables]
      vars[idx] = { ...vars[idx], [key]: val }
      return { ...f, variables: vars }
    })
    setDirty(true)
  }

  const removeVariable = (idx) => {
    setForm(f => ({ ...f, variables: f.variables.filter((_, i) => i !== idx) }))
    setDirty(true)
  }

  /* ─────── step local edit state ─────── */
  const updateStepLocal = (stepId, key, val) => {
    setSteps(prev => prev.map(s => s.id === stepId ? { ...s, [key]: val } : s))
  }

  /* ─────── computed ─────── */
  const selectedScenario = useMemo(
    () => scenarios.find(s => s.id === selectedId),
    [scenarios, selectedId],
  )

  /* ═══════════════════════ RENDER HELPERS ═══════════════════════ */

  /* ─── Stat Card ─── */
  const StatCard = ({ label, value, color, suffix = '' }) => (
    <div style={{ ...GLASS, padding: '16px 20px', flex: '1 1 0', minWidth: 140, textAlign: 'center' }}>
      <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 32, fontWeight: 700, fontFamily: MONO, color: color || '#333', lineHeight: 1.2 }}>
        {value}{suffix && <span style={{ fontSize: 14, marginLeft: 2 }}>{suffix}</span>}
      </div>
    </div>
  )

  /* ─── Step Editor ─── */
  const renderStepEditor = (step, idx) => {
    const isExpanded = expandedStepId === step.id
    const label = step.name || `${step.method} ${step.url || '(未配置)'}`
    const headers = step.headers || []
    const extractions = step.extractions || []
    const assertions = step.assertions || []
    const showBody = ['POST', 'PUT', 'PATCH'].includes(step.method)

    const headerContent = (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%' }}>
        <Tag color={MC[step.method] || '#666'} style={{ margin: 0, fontWeight: 600 }}>{step.method}</Tag>
        <span style={{ flex: 1, fontFamily: MONO, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {step.url || '(未配置URL)'}
        </span>
        <span style={{ color: '#888', fontSize: 12 }}>Step {idx + 1}</span>
      </div>
    )

    return (
      <Card
        key={step.id}
        size="small"
        style={{ marginBottom: 8, borderRadius: 8, cursor: 'pointer', border: isExpanded ? `1px solid ${ACCENT}` : undefined }}
        styles={{ header: { padding: '8px 12px', minHeight: 'auto' }, body: { padding: isExpanded ? 12 : 0, display: isExpanded ? 'block' : 'none' } }}
        title={headerContent}
        onClick={() => !isExpanded && setExpandedStepId(step.id)}
        extra={
          <Space size={4} onClick={e => e.stopPropagation()}>
            {isExpanded && (
              <Button size="small" type="link" icon={<SaveOutlined />} onClick={() => updateStep(step.id, step)}>
                保存
              </Button>
            )}
            <Popconfirm title="确定删除此步骤？" onConfirm={() => deleteStep(step.id)}>
              <Button size="small" type="link" danger icon={<DeleteOutlined />} />
            </Popconfirm>
            {isExpanded && (
              <Button size="small" type="link" icon={<CloseOutlined />} onClick={() => setExpandedStepId(null)} />
            )}
          </Space>
        }
      >
        {isExpanded && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* Name */}
            <div>
              <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>步骤名称</div>
              <Input
                size="small"
                placeholder="可选，自动生成"
                value={step.name}
                onChange={e => updateStepLocal(step.id, 'name', e.target.value)}
              />
            </div>
            {/* Method + URL */}
            <div style={{ display: 'flex', gap: 8 }}>
              <Select
                size="small"
                value={step.method}
                style={{ width: 110 }}
                onChange={v => updateStepLocal(step.id, 'method', v)}
                options={['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map(m => ({ label: m, value: m }))}
              />
              <Input spellCheck={false}
                size="small"
                style={{ flex: 1, fontFamily: MONO }}
                placeholder="https://api.example.com/path  (支持 ${variable})"
                value={step.url}
                onChange={e => updateStepLocal(step.id, 'url', e.target.value)}
              />
            </div>

            {/* Sub-sections */}
            <Collapse
              size="small"
              expandIcon={({ isActive }) => <CaretRightOutlined rotate={isActive ? 90 : 0} />}
              items={[
                {
                  key: 'headers',
                  label: `Headers (${headers.length})`,
                  children: (
                    <div>
                      {headers.map((h, hi) => (
                        <div key={hi} style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
                          <Input size="small" placeholder="Key" value={h.key} style={{ flex: 1 }}
                            onChange={e => {
                              const arr = [...headers]; arr[hi] = { ...arr[hi], key: e.target.value }
                              updateStepLocal(step.id, 'headers', arr)
                            }} />
                          <Input size="small" placeholder="Value" value={h.value} style={{ flex: 2 }}
                            onChange={e => {
                              const arr = [...headers]; arr[hi] = { ...arr[hi], value: e.target.value }
                              updateStepLocal(step.id, 'headers', arr)
                            }} />
                          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => {
                            updateStepLocal(step.id, 'headers', headers.filter((_, i) => i !== hi))
                          }} />
                        </div>
                      ))}
                      <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={() => {
                        updateStepLocal(step.id, 'headers', [...headers, { key: '', value: '' }])
                      }}>添加</Button>
                    </div>
                  ),
                },
                ...(showBody ? [{
                  key: 'body',
                  label: 'Body',
                  children: (
                    <div>
                      <Radio.Group size="small" value={step.bodyType || 'none'} onChange={e => updateStepLocal(step.id, 'bodyType', e.target.value)}
                        style={{ marginBottom: 8 }}>
                        <Radio.Button value="none">None</Radio.Button>
                        <Radio.Button value="json">JSON</Radio.Button>
                        <Radio.Button value="text">Text</Radio.Button>
                      </Radio.Group>
                      {(step.bodyType && step.bodyType !== 'none') && (
                        <Input.TextArea spellCheck={false}
                          rows={5}
                          style={{ fontFamily: MONO, fontSize: 12 }}
                          value={step.body}
                          onChange={e => updateStepLocal(step.id, 'body', e.target.value)}
                          placeholder={step.bodyType === 'json' ? '{"key": "value"}' : 'request body'}
                        />
                      )}
                    </div>
                  ),
                }] : []),
                {
                  key: 'extractions',
                  label: `变量提取 (${extractions.length})`,
                  children: (
                    <div>
                      {extractions.map((ex, ei) => (
                        <div key={ei} style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
                          <Input size="small" placeholder="变量名" value={ex.variableName} style={{ flex: 1 }}
                            onChange={e => {
                              const arr = [...extractions]; arr[ei] = { ...arr[ei], variableName: e.target.value }
                              updateStepLocal(step.id, 'extractions', arr)
                            }} />
                          <Input spellCheck={false} size="small" placeholder="$.data.token" value={ex.jsonpath} style={{ flex: 2, fontFamily: MONO }}
                            onChange={e => {
                              const arr = [...extractions]; arr[ei] = { ...arr[ei], jsonpath: e.target.value }
                              updateStepLocal(step.id, 'extractions', arr)
                            }} />
                          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => {
                            updateStepLocal(step.id, 'extractions', extractions.filter((_, i) => i !== ei))
                          }} />
                        </div>
                      ))}
                      <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={() => {
                        updateStepLocal(step.id, 'extractions', [...extractions, { variableName: '', jsonpath: '' }])
                      }}>添加</Button>
                    </div>
                  ),
                },
                {
                  key: 'assertions',
                  label: `断言 (${assertions.length})`,
                  children: (
                    <div>
                      {assertions.map((a, ai) => (
                        <div key={ai} style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
                          <Select size="small" value={a.type} style={{ width: 140 }}
                            onChange={v => {
                              const arr = [...assertions]; arr[ai] = { ...arr[ai], type: v }
                              updateStepLocal(step.id, 'assertions', arr)
                            }}
                            options={[
                              { label: 'Status Code', value: 'status' },
                              { label: 'Body Contains', value: 'body_contains' },
                              { label: 'JSONPath', value: 'jsonpath' },
                            ]}
                          />
                          <Input spellCheck={false} size="small" placeholder="期望值" value={a.value} style={{ flex: 1, fontFamily: MONO }}
                            onChange={e => {
                              const arr = [...assertions]; arr[ai] = { ...arr[ai], value: e.target.value }
                              updateStepLocal(step.id, 'assertions', arr)
                            }} />
                          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => {
                            updateStepLocal(step.id, 'assertions', assertions.filter((_, i) => i !== ai))
                          }} />
                        </div>
                      ))}
                      <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={() => {
                        updateStepLocal(step.id, 'assertions', [...assertions, { type: 'status', value: '200' }])
                      }}>添加</Button>
                    </div>
                  ),
                },
              ]}
            />
          </div>
        )}
      </Card>
    )
  }

  const exportK6 = async () => {
    if (!selectedId) { message.warning('请先选择场景'); return }
    setK6({ open: true, loading: true, filename: '', script: '', guide: '' })
    try {
      const d = await api.get(`/load-test/scenarios/${selectedId}/k6-script`)
      setK6({ open: true, loading: false, filename: d.filename || 'scenario.js', script: d.script || '', guide: d.deployGuide || '' })
    } catch (e) {
      setK6({ open: false, loading: false, filename: '', script: '', guide: '' })
      message.error(`生成 k6 脚本失败: ${e?.response?.data?.error || e.message || '未知错误'}`)
    }
  }

  const copyText = async (text) => {
    try { await navigator.clipboard.writeText(text); message.success('已复制') }
    catch { message.error('复制失败，请手动选择') }
  }

  const downloadText = (name, text) => {
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob); a.download = name; a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 1000)
  }

  const renderK6Modal = () => (
    <Modal
      title={<span><ExportOutlined style={{ color: ACCENT, marginRight: 8 }} />导出 k6 压测脚本</span>}
      open={k6.open}
      onCancel={() => setK6(k => ({ ...k, open: false }))}
      footer={null}
      width={860}
    >
      {k6.loading ? (
        <div style={{ textAlign: 'center', padding: 40, color: '#888' }}>生成中…</div>
      ) : (
        <Tabs
          items={[
            {
              key: 'script',
              label: '脚本 (k6 .js)',
              children: (
                <div>
                  <Space style={{ marginBottom: 8 }}>
                    <Button size="small" icon={<CopyOutlined />} onClick={() => copyText(k6.script)}>复制</Button>
                    <Button size="small" icon={<DownloadOutlined />} onClick={() => downloadText(k6.filename, k6.script)}>下载 {k6.filename}</Button>
                  </Space>
                  <pre style={{ maxHeight: 460, overflow: 'auto', background: '#1e1e1e', color: '#d4d4d4', padding: 12, borderRadius: 8, fontFamily: MONO, fontSize: 12, margin: 0 }}>{k6.script}</pre>
                </div>
              ),
            },
            {
              key: 'guide',
              label: '部署指引',
              children: (
                <div>
                  <Space style={{ marginBottom: 8 }}>
                    <Button size="small" icon={<CopyOutlined />} onClick={() => copyText(k6.guide)}>复制</Button>
                    <Button size="small" icon={<DownloadOutlined />} onClick={() => downloadText('k6-deploy-guide.md', k6.guide)}>下载 .md</Button>
                  </Space>
                  <pre style={{ maxHeight: 460, overflow: 'auto', background: 'rgba(0,0,0,0.03)', color: '#333', padding: 12, borderRadius: 8, fontFamily: MONO, fontSize: 12, whiteSpace: 'pre-wrap', margin: 0 }}>{k6.guide}</pre>
                </div>
              ),
            },
          ]}
        />
      )}
    </Modal>
  )

  /* ─── Config Tab ─── */
  const renderConfigTab = () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, padding: 4 }}>
      <div>
        <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>场景名称</div>
        <Input value={form.name} onChange={e => setField('name', e.target.value)} placeholder="给场景起个名字" />
      </div>
      <div>
        <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>描述</div>
        <Input.TextArea rows={2} value={form.description} onChange={e => setField('description', e.target.value)} placeholder="可选" />
      </div>
      <div style={{ display: 'flex', gap: 16 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>并发用户</div>
          <InputNumber min={1} max={10000} value={form.concurrentUsers} onChange={v => setField('concurrentUsers', v)} style={{ width: '100%' }} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>递增时间 (秒)</div>
          <InputNumber min={0} max={3600} value={form.rampUpSeconds} onChange={v => setField('rampUpSeconds', v)} style={{ width: '100%' }} />
        </div>
      </div>

      <div>
        <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>执行模式</div>
        <Radio.Group value={mode} onChange={e => { setMode(e.target.value); setDirty(true) }}>
          <Radio value="iterations">
            <Space>
              迭代次数
              <InputNumber
                size="small" min={1} max={1000000}
                value={form.totalIterations}
                onChange={v => setField('totalIterations', v)}
                disabled={mode !== 'iterations'}
                style={{ width: 100 }}
              />
            </Space>
          </Radio>
          <Radio value="duration">
            <Space>
              持续时间 (秒)
              <InputNumber
                size="small" min={1} max={86400}
                value={form.durationSeconds}
                onChange={v => setField('durationSeconds', v)}
                disabled={mode !== 'duration'}
                style={{ width: 100 }}
              />
            </Space>
          </Radio>
        </Radio.Group>
      </div>

      {/* Variables */}
      <div>
        <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>变量参数化</div>
        {form.variables.map((v, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <Input
              size="small" placeholder="变量名" value={v.name} style={{ width: 140 }}
              onChange={e => updateVariable(i, 'name', e.target.value)}
            />
            <Input
              size="small" placeholder="值 (逗号分隔)" value={v.values} style={{ flex: 1 }}
              onChange={e => updateVariable(i, 'values', e.target.value)}
            />
            <Button size="small" danger icon={<DeleteOutlined />} onClick={() => removeVariable(i)} />
          </div>
        ))}
        <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={addVariable}>添加变量</Button>
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 8, paddingTop: 8, borderTop: '1px solid rgba(0,0,0,0.06)' }}>
        <Button icon={<SaveOutlined />} onClick={saveScenario} disabled={!dirty}>保存</Button>
        <Button type="primary" icon={<PlayCircleOutlined />} onClick={startTest}
          style={{ background: ACCENT, borderColor: ACCENT }}
          disabled={steps.length === 0}
        >
          开始测试
        </Button>
        <Button icon={<ExportOutlined />} onClick={exportK6} disabled={steps.length === 0}>导出 k6 脚本</Button>
      </div>
    </div>
  )

  /* ─── Steps Tab ─── */
  const renderStepsTab = () => (
    <div>
      {steps.length === 0 ? (
        <Empty description="暂无步骤" style={{ marginTop: 40 }}>
          <Button type="dashed" icon={<PlusOutlined />} onClick={createStep}>添加第一个步骤</Button>
        </Empty>
      ) : (
        <>
          {steps.map((s, i) => renderStepEditor(s, i))}
          <Button type="dashed" icon={<PlusOutlined />} onClick={createStep} block style={{ marginTop: 8 }}>
            添加步骤
          </Button>
        </>
      )}
    </div>
  )

  /* ─── History Tab ─── */
  const statusTag = (status) => {
    const map = { completed: { color: 'green', text: '完成' }, cancelled: { color: 'orange', text: '已取消' }, error: { color: 'red', text: '异常' }, running: { color: 'blue', text: '运行中' } }
    const c = map[status] || { color: 'default', text: status }
    return <Tag color={c.color}>{c.text}</Tag>
  }

  const formatTime = (t) => t ? new Date(t).toLocaleString('zh-CN') : '-'

  const renderHistoryTab = () => (
    <div>
      {runs.length === 0 ? (
        <Empty description="暂无执行记录" style={{ marginTop: 40 }} />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {runs.map(r => (
            <div
              key={r.id}
              style={{ ...GLASS, padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer' }}
              onClick={() => viewRunDetail(r.id)}
            >
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 500 }}>{formatTime(r.createdAt || r.startedAt)}</div>
                <div style={{ fontSize: 12, color: '#888', marginTop: 2 }}>
                  请求: {r.totalRequests ?? '-'} | 成功率: {r.successRate != null ? `${(r.successRate * 100).toFixed(1)}%` : '-'} | 平均延迟: {r.avgLatency != null ? `${r.avgLatency}ms` : '-'}
                </div>
              </div>
              {statusTag(r.status)}
              <span style={{ fontSize: 12, color: '#888' }}>{r.duration ? `${r.duration}s` : ''}</span>
              <Popconfirm title="确定删除此记录？" onConfirm={e => { e?.stopPropagation(); deleteRun(r.id) }}>
                <Button size="small" type="link" danger icon={<DeleteOutlined />} onClick={e => e.stopPropagation()} />
              </Popconfirm>
            </div>
          ))}
        </div>
      )}
    </div>
  )

  /* ─── Dashboard (during test run) ─── */
  const renderDashboard = () => {
    const m = metrics || {}
    const stepStats = m.stepStats || []
    const errors = m.errors || []
    const successRate = m.totalRequests > 0 ? ((m.successCount || 0) / m.totalRequests * 100) : 0
    const errorRate = m.totalRequests > 0 ? ((m.errorCount || 0) / m.totalRequests * 100) : 0

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {/* Stat cards row 1 */}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <StatCard label="总请求" value={m.totalRequests?.toLocaleString() ?? 0} color="#333" />
          <StatCard label="成功率" value={successRate.toFixed(1)} color="#0ea5a0" suffix="%" />
          <StatCard label="QPS" value={m.qps?.toLocaleString() ?? 0} color="#4e8af0" />
          {errorRate > 0 && <StatCard label="错误率" value={errorRate.toFixed(1)} color={ACCENT} suffix="%" />}
        </div>

        {/* Latency cards row 2 */}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <StatCard label="Avg" value={m.avgLatency ?? '-'} suffix="ms" />
          <StatCard label="P50" value={m.p50 ?? '-'} suffix="ms" />
          <StatCard label="P95" value={m.p95 ?? '-'} suffix="ms" color="#ff7d00" />
          <StatCard label="P99" value={m.p99 ?? '-'} suffix="ms" color={ACCENT} />
        </div>

        {/* Step stats */}
        {stepStats.length > 0 && (
          <div style={{ ...GLASS, padding: 16 }}>
            <div style={{ fontWeight: 600, marginBottom: 12 }}>步骤统计</div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, fontFamily: MONO }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #eee' }}>
                    {['步骤名', '请求数', '成功', '失败', 'Avg(ms)', 'P50', 'P95'].map(h => (
                      <th key={h} style={{ padding: '6px 10px', textAlign: 'left', fontWeight: 500, color: '#888', fontSize: 12 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {stepStats.map((s, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid #f5f5f5' }}>
                      <td style={{ padding: '6px 10px', fontFamily: 'inherit' }}>{s.name || `Step ${i + 1}`}</td>
                      <td style={{ padding: '6px 10px' }}>{s.totalRequests}</td>
                      <td style={{ padding: '6px 10px', color: '#0ea5a0' }}>{s.successCount}</td>
                      <td style={{ padding: '6px 10px', color: s.errorCount > 0 ? ACCENT : '#ccc' }}>{s.errorCount}</td>
                      <td style={{ padding: '6px 10px' }}>{s.avgLatency}</td>
                      <td style={{ padding: '6px 10px' }}>{s.p50}</td>
                      <td style={{ padding: '6px 10px' }}>{s.p95}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Error distribution */}
        {errors.length > 0 && (
          <div style={{ ...GLASS, padding: 16 }}>
            <div style={{ fontWeight: 600, marginBottom: 12, color: ACCENT }}>错误分布</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #eee' }}>
                  <th style={{ padding: '6px 10px', textAlign: 'left', fontWeight: 500, color: '#888', fontSize: 12 }}>错误类型</th>
                  <th style={{ padding: '6px 10px', textAlign: 'left', fontWeight: 500, color: '#888', fontSize: 12 }}>次数</th>
                </tr>
              </thead>
              <tbody>
                {errors.map((e, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid #f5f5f5' }}>
                    <td style={{ padding: '6px 10px', color: ACCENT }}>{e.type || e.message}</td>
                    <td style={{ padding: '6px 10px', fontFamily: MONO, fontWeight: 600 }}>{e.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    )
  }

  /* ─── Run Detail Modal ─── */
  const renderDetailModal = () => {
    if (!detailRun) return null
    const d = detailRun
    const successRate = d.totalRequests > 0 ? ((d.successCount || 0) / d.totalRequests * 100).toFixed(1) : '0.0'
    return (
      <Modal
        open={!!detailRun}
        title="执行详情"
        onCancel={() => setDetailRun(null)}
        footer={null}
        width={640}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <StatCard label="总请求" value={d.totalRequests?.toLocaleString() ?? 0} color="#333" />
            <StatCard label="成功率" value={successRate} color="#0ea5a0" suffix="%" />
            <StatCard label="QPS" value={d.qps ?? '-'} color="#4e8af0" />
          </div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <StatCard label="Avg" value={d.avgLatency ?? '-'} suffix="ms" />
            <StatCard label="P50" value={d.p50 ?? '-'} suffix="ms" />
            <StatCard label="P95" value={d.p95 ?? '-'} suffix="ms" />
            <StatCard label="P99" value={d.p99 ?? '-'} suffix="ms" />
          </div>
          <div style={{ fontSize: 12, color: '#888' }}>
            <div>状态: {statusTag(d.status)}</div>
            <div>开始: {formatTime(d.startedAt || d.createdAt)}</div>
            {d.duration && <div>耗时: {d.duration}s</div>}
          </div>
        </div>
      </Modal>
    )
  }

  /* ═══════════════════════ MAIN LAYOUT ═══════════════════════ */

  const tabItems = [
    { key: 'config', label: '场景配置' },
    { key: 'steps', label: `步骤编排 (${steps.length})` },
    { key: 'history', label: '历史' },
  ]

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* ─── Header ─── */}
      <div style={{
        padding: '12px 20px', display: 'flex', alignItems: 'center', gap: 12,
        borderBottom: '1px solid rgba(0,0,0,0.06)', flexShrink: 0,
      }}>
        {running ? (
          <>
            <ThunderboltOutlined style={{ color: ACCENT, fontSize: 18 }} spin />
            <span style={{ fontWeight: 600, color: ACCENT }}>Running...</span>
            <span style={{ fontSize: 13, color: '#888', fontFamily: MONO }}>耗时: {elapsed}s</span>
            <div style={{ flex: 1 }} />
            <Button danger icon={<PauseCircleOutlined />} onClick={cancelTest}>取消测试</Button>
          </>
        ) : (
          <>
            <ThunderboltOutlined style={{ color: ACCENT, fontSize: 18 }} />
            <span style={{ fontWeight: 600, fontSize: 16 }}>压力测试</span>
            <div style={{ flex: 1 }} />
            <Button
              icon={<HistoryOutlined />}
              onClick={() => { loadGlobalRuns(); setHistoryModalOpen(true) }}
            >
              执行历史
            </Button>
          </>
        )}
      </div>

      {/* ─── Body ─── */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* ─── Left Panel: Scenario List ─── */}
        <div style={{
          width: 280, flexShrink: 0, borderRight: '1px solid rgba(0,0,0,0.06)',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}>
          <div style={{ padding: '12px 12px 8px' }}>
            <Button type="dashed" icon={<PlusOutlined />} block onClick={createScenario}>
              新建场景
            </Button>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 8px' }}>
            {scenarios.length === 0 ? (
              <Empty description="暂无场景" style={{ marginTop: 40 }} />
            ) : (
              scenarios.map(s => (
                <div
                  key={s.id}
                  onClick={() => selectScenario(s.id)}
                  style={{
                    padding: '10px 12px', borderRadius: 8, marginBottom: 4, cursor: 'pointer',
                    background: selectedId === s.id ? `${ACCENT}10` : 'transparent',
                    border: selectedId === s.id ? `1px solid ${ACCENT}30` : '1px solid transparent',
                    transition: 'all .15s',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <ThunderboltOutlined style={{ color: selectedId === s.id ? ACCENT : '#bbb', fontSize: 14 }} />
                    <span style={{
                      flex: 1, fontSize: 13, fontWeight: selectedId === s.id ? 600 : 400,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {s.name || '未命名场景'}
                    </span>
                    <Popconfirm title="确定删除？" onConfirm={e => { e?.stopPropagation(); deleteScenario(s.id) }}>
                      <Button
                        size="small" type="link" danger icon={<DeleteOutlined />}
                        style={{ opacity: 0.5 }}
                        onClick={e => e.stopPropagation()}
                      />
                    </Popconfirm>
                  </div>
                  {/* 副行始终显示压测参数 —— 原来只在有描述时才出现，而描述默认是空的，
                      于是列表里一排「新场景」除了名字什么都没有，得一个个点进去才知道谁是谁。 */}
                  <div style={{ fontSize: 11, color: '#999', marginTop: 2, marginLeft: 22, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {s.description
                      ? s.description
                      : `${s.concurrentUsers ?? '—'} 并发 · ${s.durationSeconds ? `${s.durationSeconds}s` : `${s.totalIterations ?? '—'} 次`}`}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* ─── Right Panel ─── */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {running ? (
            /* Dashboard mode */
            <div style={{ flex: 1, overflowY: 'auto', padding: 20 }}>
              {renderDashboard()}
            </div>
          ) : selectedId ? (
            /* Tab mode */
            <>
              <div style={{
                display: 'flex', gap: 0, borderBottom: '1px solid rgba(0,0,0,0.06)', flexShrink: 0,
                padding: '0 16px',
              }}>
                {tabItems.map(t => (
                  <div
                    key={t.key}
                    onClick={() => setActiveTab(t.key)}
                    style={{
                      padding: '10px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 500,
                      borderBottom: activeTab === t.key ? `2px solid ${ACCENT}` : '2px solid transparent',
                      color: activeTab === t.key ? ACCENT : '#666',
                      transition: 'all .15s',
                    }}
                  >
                    {t.label}
                  </div>
                ))}
              </div>
              <div style={{ flex: 1, overflowY: 'auto', padding: 20 }}>
                {activeTab === 'config' && renderConfigTab()}
                {activeTab === 'steps' && renderStepsTab()}
                {activeTab === 'history' && renderHistoryTab()}
              </div>
            </>
          ) : (
            /* Empty state */
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Empty description="选择或创建一个测试场景" />
            </div>
          )}
        </div>
      </div>

      {/* ─── Global History Modal ─── */}
      <Modal
        open={historyModalOpen}
        title="全部执行历史"
        onCancel={() => setHistoryModalOpen(false)}
        footer={null}
        width={720}
      >
        {globalRuns.length === 0 ? (
          <Empty description="暂无执行记录" />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 480, overflowY: 'auto' }}>
            {globalRuns.map(r => (
              <div
                key={r.id}
                style={{ ...GLASS, padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer' }}
                onClick={() => { setHistoryModalOpen(false); viewRunDetail(r.id) }}
              >
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>
                    {r.scenarioName || '场景'}
                    <span style={{ color: '#bbb', marginLeft: 8, fontWeight: 400 }}>{formatTime(r.createdAt || r.startedAt)}</span>
                  </div>
                  <div style={{ fontSize: 12, color: '#888', marginTop: 2, fontFamily: MONO }}>
                    请求: {r.totalRequests ?? '-'} | 成功率: {r.successRate != null ? `${(r.successRate * 100).toFixed(1)}%` : '-'} | Avg: {r.avgLatency ?? '-'}ms
                  </div>
                </div>
                {statusTag(r.status)}
              </div>
            ))}
          </div>
        )}
      </Modal>

      {/* ─── Run Detail Modal ─── */}
      {renderDetailModal()}
      {renderK6Modal()}
    </div>
  )
}
