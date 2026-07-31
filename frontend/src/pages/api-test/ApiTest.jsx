import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { Modal, Form, Input, TreeSelect, message, Select, Tag, Button, Tooltip, Drawer, Space } from 'antd'
import { PlayCircleOutlined, RobotOutlined, CopyOutlined, ScissorOutlined, BranchesOutlined } from '@ant-design/icons'
import { api } from '../../utils/request'
import { useBranch } from '../../utils/branch'
import { useEnv, buildEnvOptions } from '../../utils/env'
import RunResultPanel from './components/RunResultPanel'
import StepRunDrawer from './components/StepRunDrawer'
import FolderTree from './components/FolderTree'
import ScenarioList from './components/ScenarioList'
import StepList from './components/StepList'
import StepEditor from './components/StepEditor'
import GenerateModal from './components/GenerateModal'

export default function ApiTest() {
  const { projectId } = useParams()
  const [searchParams] = useSearchParams()
  const [globalBranchId] = useBranch(projectId)
  const [branchId, setBranchId] = useState(null)
  const [scenarios, setScenarios] = useState([])
  const [loading, setLoading] = useState(false)
  const [selectedScenario, setSelectedScenario] = useState(null)
  const [selectedStep, setSelectedStep] = useState(null)
  const [genOpen, setGenOpen] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [genProgress, setGenProgress] = useState([])
  const [form] = Form.useForm()
  const [running, setRunning] = useState(false)
  const [runningStepId, setRunningStepId] = useState(null)
  const [saveStatus, setSaveStatus] = useState(null) // null | 'saving' | 'saved'
  const saveTimerRef = useRef(null)
  const [searchKeyword, setSearchKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  // 默认只看本模块自己的单接口场景。用例编排链归属某条用例，在「用例详情 → 接口测试」里维护，
  // 混在这里会把模块淹掉（用户明确要求两个页面的数据不要混）。留个开关，不做成看不见。
  const [kindFilter, setKindFilter] = useState('single')
  const [folderModalOpen, setFolderModalOpen] = useState(false)
  const [newFolderName, setNewFolderName] = useState('')
  const [newFolderParent, setNewFolderParent] = useState(null)
  const [folderTree, setFolderTree] = useState([])
  const [selectedFolderId, setSelectedFolderId] = useState(null)
  const [selectedFolderIds, setSelectedFolderIds] = useState([])
  const [runResponse, setRunResponse] = useState(null)
  const [showStepDrawer, setShowStepDrawer] = useState(false)
  const [stepDrawerName, setStepDrawerName] = useState('')
  const [createScenarioOpen, setCreateScenarioOpen] = useState(false)
  const [showRunPanel, setShowRunPanel] = useState(false)
  const [runStepResults, setRunStepResults] = useState([])
  const [runReportId, setRunReportId] = useState(null)
  const [splitMode, setSplitMode] = useState(false)
  const [optimizeOpen, setOptimizeOpen] = useState(false)
  const [optimizeSuggestion, setOptimizeSuggestion] = useState('')
  const [optimizing, setOptimizing] = useState(false)
  const [optimizePlan, setOptimizePlan] = useState(null)
  const [createForm] = Form.useForm()
  const [environments, setEnvironments] = useState([])
  const [envId, setEnvId] = useEnv(projectId)
  const [apiList, setApiList] = useState([])
  const [projectInfo, setProjectInfo] = useState({ name: '', branchName: '' })

  useEffect(() => {
    api.get('/environments').then(res => setEnvironments(res.data || [])).catch(() => {})
  }, [])

  useEffect(() => {
    if (!projectId) return
    api.get('/projects').then(res => {
      const p = (res.data || []).find(x => x.id === projectId)
      if (p) setProjectInfo(prev => ({ ...prev, name: p.name }))
    }).catch(() => {})
  }, [projectId])

  useEffect(() => {
    if (!projectId || !branchId) return
    api.get(`/projects/${projectId}/branches`).then(res => {
      const b = (res.data || []).find(x => x.id === branchId)
      if (b) setProjectInfo(prev => ({ ...prev, branchName: b.name || '' }))
    }).catch(() => {})
  }, [projectId, branchId])

  useEffect(() => {
    if (!projectId || !branchId) return
    api.get(`/projects/${projectId}/api-nodes?branch_id=${branchId}`).then(res => {
      const nodes = res.data || []
      setApiList(nodes.filter(n => n.nodeType === 'endpoint'))
    }).catch(() => {})
  }, [projectId, branchId])

  const changeEnv = (id) => {
    setEnvId(id || null)
  }

  useEffect(() => {
    if (!projectId) return
    // 优先使用全局分支；没有则取第一个分支
    if (globalBranchId) {
      setBranchId(globalBranchId)
      // 切换分支时清空选中状态。但带 ?scenarioId= 深链进来时不能清——
      // globalBranchId 往往比深链晚一拍落定，会把刚加载的场景又冲掉。
      if (!searchParams.get('scenarioId')) {
        setSelectedScenario(null)
        setSelectedStep(null)
        setSelectedFolderId(null)
        setSelectedFolderIds([])
      }
      return
    }
    api.get(`/projects/${projectId}/branches`).then(res => {
      const b = (res.data || [])[0]
      if (b) setBranchId(b.id)
    }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, globalBranchId])

  const fetchFolders = useCallback(async () => {
    if (!branchId) return
    try {
      // kind 必须和 fetchScenarios 一致，否则目录树的计数会和列表对不上
      const res = await api.get(`/projects/${projectId}/branches/${branchId}/api-tests/folders?kind=${kindFilter}`)
      const tree = res.data || []
      setFolderTree(tree)
      if (!selectedFolderId && !selectedScenario && tree.length > 0) {
        setSelectedFolderId(tree[0].id)
      }
    } catch { /* */ }
  }, [projectId, branchId, kindFilter])

  useEffect(() => { fetchFolders() }, [fetchFolders])

  const fetchScenarios = useCallback(async () => {
    if (!branchId) return
    setLoading(true)
    try {
      const res = await api.get(`/projects/${projectId}/branches/${branchId}/api-tests?kind=${kindFilter}`)
      setScenarios(res.data || [])
    } catch { /* */ } finally { setLoading(false) }
  }, [projectId, branchId, kindFilter])

  useEffect(() => { fetchScenarios() }, [fetchScenarios])


  const loadScenario = async (id, { keepStep = false } = {}) => {
    try {
      const res = await api.get(`/projects/${projectId}/branches/${branchId}/api-tests/${id}`)
      setSelectedScenario(res.data)
      const steps = res.data?.steps || []
      // 初始化步骤快照，避免 onBlur 时误判为"有变化"
      for (const s of steps) {
        lastSavedRef.current[s.id] = { name: s.name, method: s.method, url: s.url, body: s.body, headers: s.headers, assertions: s.assertions, variablesExtract: s.variablesExtract }
      }
      if (keepStep) {
        setSelectedStep(prev => {
          if (!prev) return steps[0] || null
          const fresh = steps.find(s => s.id === prev.id)
          return fresh || steps[0] || null
        })
      } else {
        setRunResponse(null)
        setSelectedStep(steps[0] || null)
      }
    } catch { /* */ }
  }

  // 支持从用例详情带 ?scenarioId= 直接定位到某条场景（否则只能到模块首页自己翻）。
  // 必须放在 loadScenario 定义之后：依赖数组在渲染期求值，提前引用会 TDZ 报错。
  // loadScenario 是普通函数、每次渲染都是新引用，不能进依赖数组（会每帧重跑）。
  const jumpedRef = useRef(false)
  useEffect(() => {
    const sid = searchParams.get('scenarioId')
    if (!sid || !branchId || jumpedRef.current) return
    jumpedRef.current = true
    loadScenario(sid)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [branchId, searchParams])

  const handleDelete = async (id) => {
    try {
      await api.del(`/projects/${projectId}/branches/${branchId}/api-tests/${id}`)
      message.success('已删除')
      if (selectedScenario?.id === id) { setSelectedScenario(null); setSelectedStep(null) }
      fetchScenarios()
    } catch { message.error('删除失败') }
  }

  const handleGenerate = async () => {
    try {
      const v = await form.validateFields()
      setGenerating(true); setGenProgress([])
      const payload = {
        folderId: v.targetFolder || undefined,
        envVariables: v.envVars ? JSON.parse(v.envVars) : undefined,
      }
      if (v.apiIds?.length) {
        payload.apiIds = v.apiIds
      } else {
        payload.apiInfo = v.apiInfo
      }
      if (v.envId) {
        payload.envId = v.envId
      }
      api.stream(`/projects/${projectId}/branches/${branchId}/api-tests/generate`, payload, {
        onChunk: (data) => {
          if (data.type === 'step_start') setGenProgress(prev => [...prev, `⏳ ${data.title}`])
          if (data.type === 'step_done') setGenProgress(prev => [...prev, `✅ ${data.summary}`])
          if (data.type === 'scenario_created') setGenProgress(prev => [...prev, `📋 ${data.code} ${data.title} (${data.stepCount}步)`])
          if (data.type === 'error') { message.error(data.message); setGenerating(false) }
        },
        onDone: () => {
          message.success('测试场景已生成')
          setGenerating(false); setGenOpen(false); form.resetFields()
          fetchScenarios(); fetchFolders()
        },
        onError: (msg) => { message.error(msg); setGenerating(false) },
      })
    } catch { /* */ }
  }

  const handleRunStep = async () => {
    if (!selectedStep) return
    setRunningStepId(selectedStep.id); setRunResponse(null)
    setStepDrawerName(selectedStep.name || '')
    try {
      const res = await api.post(`/projects/${projectId}/branches/${branchId}/api-tests/${selectedScenario.id}/run-step/${selectedStep.id}`, envId ? { envId } : {})
      setRunResponse(res.data)
      setShowStepDrawer(true)
      loadScenario(selectedScenario.id, { keepStep: true })
    } catch (e) {
      setRunResponse({ error: e.message || '执行失败' })
      setShowStepDrawer(true)
      message.error(e.message || '执行失败')
    } finally { setRunningStepId(null) }
  }

  const handleRunAll = async () => {
    if (!selectedScenario) return
    setRunning(true)
    setShowRunPanel(true)
    setRunStepResults([])
    setRunReportId(null)
    api.stream(`/projects/${projectId}/branches/${branchId}/api-tests/run`, {
      scenarioIds: [selectedScenario.id],
      envId: envId || undefined,
    }, {
      onChunk: (data) => {
        if (data.type === 'step_result') {
          setRunStepResults(prev => [...prev, {
            stepId: data.stepId,
            stepName: data.stepName,
            method: data.method || '',
            status: data.status,
            statusCode: data.statusCode,
            duration: data.duration,
          }])
        }
        if (data.type === 'report_created') {
          setRunReportId(data.reportId)
        }
        if (data.type === 'run_done' || data.type === 'scenario_done') {
          setRunning(false)
          loadScenario(selectedScenario.id, { keepStep: true })
        }
      },
      onDone: () => {
        setRunning(false)
        loadScenario(selectedScenario.id, { keepStep: true })
      },
      onError: (msg) => { message.error(msg); setRunning(false) },
    })
  }

  const handleAiOptimize = async (suggestion) => {
    const res = await api.post(`/projects/${projectId}/branches/${branchId}/api-tests/${selectedScenario.id}/ai-optimize`, { suggestion })
    return res.data
  }

  const handleApplyOptimize = async (changes) => {
    try {
      const res = await api.post(`/projects/${projectId}/branches/${branchId}/api-tests/${selectedScenario.id}/ai-optimize/apply`, { changes })
      loadScenario(selectedScenario.id)
      return res.data
    } catch { message.error('应用失败'); return null }
  }

  const handleCopyScenario = async () => {
    if (!selectedScenario) return
    try {
      const res = await api.post(`/projects/${projectId}/branches/${branchId}/api-tests/${selectedScenario.id}/copy`)
      message.success('场景已复制')
      fetchScenarios(); fetchFolders()
      loadScenario(res.data.id)
    } catch { message.error('复制失败') }
  }

  const handleNewVersion = async () => {
    if (!selectedScenario) return
    try {
      const res = await api.post(`/projects/${projectId}/branches/${branchId}/api-tests/${selectedScenario.id}/new-version`)
      message.success('已创建新版本（草稿），原版本已废弃')
      fetchScenarios(); fetchFolders()
      loadScenario(res.data.id)
    } catch (e) { message.error(e.message || '更新版本失败') }
  }

  const lastSavedRef = useRef({})

  const saveStep = async (stepId, updates) => {
    const prev = lastSavedRef.current[stepId] || {}
    const changed = Object.keys(updates).some(k => JSON.stringify(updates[k]) !== JSON.stringify(prev[k]))
    if (!changed) return

    try {
      setSaveStatus('saving')
      const res = await api.put(`/projects/${projectId}/branches/${branchId}/api-tests/${selectedScenario.id}/steps/${stepId}`, updates)
      lastSavedRef.current[stepId] = { ...prev, ...res.data }
      if (selectedStep?.id === stepId) {
        setSelectedStep(p => ({ ...p, ...res.data }))
      }
      const scRes = await api.get(`/projects/${projectId}/branches/${branchId}/api-tests/${selectedScenario.id}`)
      setSelectedScenario(scRes.data)
      setSaveStatus('saved')
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = setTimeout(() => setSaveStatus(null), 2000)
    } catch { message.error('保存失败'); setSaveStatus(null) }
  }

  const saveScenario = async (updates) => {
    try {
      await api.put(`/projects/${projectId}/branches/${branchId}/api-tests/${selectedScenario.id}`, updates)
      fetchScenarios()
      loadScenario(selectedScenario.id)
    } catch { message.error('保存失败') }
  }

  const addStep = async () => {
    try {
      await api.post(`/projects/${projectId}/branches/${branchId}/api-tests/${selectedScenario.id}/steps`, { name: '新步骤', method: 'GET', url: '${BASE_URL}/' })
      loadScenario(selectedScenario.id)
    } catch { message.error('添加失败') }
  }

  const removeStep = async (stepId) => {
    try {
      await api.del(`/projects/${projectId}/branches/${branchId}/api-tests/${selectedScenario.id}/steps/${stepId}`)
      if (selectedStep?.id === stepId) setSelectedStep(null)
      loadScenario(selectedScenario.id)
    } catch { message.error('删除失败') }
  }

  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) return
    try {
      await api.post(`/projects/${projectId}/branches/${branchId}/api-tests/folders?name=${encodeURIComponent(newFolderName.trim())}${newFolderParent ? `&parent_id=${newFolderParent}` : ''}`)
      message.success('文件夹已创建')
      setFolderModalOpen(false)
      setNewFolderName('')
      setNewFolderParent(null)
      fetchFolders()
    } catch { /* */ }
  }

  const handleDeleteFolder = async (folderId) => {
    try {
      await api.del(`/projects/${projectId}/branches/${branchId}/api-tests/folders/${folderId}`)
      message.success('文件夹已删除')
      fetchFolders()
    } catch { /* */ }
  }

  const handleRenameFolder = async (folderId, name) => {
    try {
      await api.put(`/projects/${projectId}/branches/${branchId}/api-tests/folders/${folderId}?name=${encodeURIComponent(name)}`)
      message.success('已重命名')
      fetchFolders()
    } catch (e) { message.error(e.message || '重命名失败') }
  }

  const handleSplitScenario = async (stepIds) => {
    if (!selectedScenario || !stepIds?.length) return
    try {
      const res = await api.post(`/projects/${projectId}/branches/${branchId}/api-tests/${selectedScenario.id}/split`, { stepIds })
      message.success('已拆分为新场景')
      fetchScenarios(); fetchFolders()
      loadScenario(res.data.id)
    } catch (e) { message.error(e.message || '拆分失败') }
  }

  const handleReorderSteps = async (stepIds) => {
    if (!selectedScenario) return
    try {
      await api.put(`/projects/${projectId}/branches/${branchId}/api-tests/${selectedScenario.id}/steps/reorder`, { stepIds })
      loadScenario(selectedScenario.id)
    } catch (e) { message.error(e.message || '排序失败') }
  }

  const handleCreateScenario = async () => {
    try {
      const v = await createForm.validateFields()
      await api.post(`/projects/${projectId}/branches/${branchId}/api-tests`, {
        title: v.title,
        priority: v.priority || 'P1',
        folderId: v.folderId || undefined,
      })
      message.success('场景已创建')
      setCreateScenarioOpen(false)
      createForm.resetFields()
      fetchScenarios()
    } catch { /* */ }
  }

  const handleBatchOperation = async (action, ids, folderId) => {
    if (!ids?.length) { message.warning('请先选择场景'); return }
    try {
      await api.put(`/projects/${projectId}/branches/${branchId}/api-tests/batch`, {
        ids, action, ...(action === 'move' ? { folderId: folderId || null } : {}),
      })
      message.success('操作成功')
      fetchScenarios(); fetchFolders()
    } catch (e) { message.error(e.message || '操作失败') }
  }

  const handleMoveScenario = async (scenarioId, folderId) => {
    try {
      await api.put(`/projects/${projectId}/branches/${branchId}/api-tests/${scenarioId}`, {
        folderId: folderId || '',
      })
      message.success('已移动')
      fetchScenarios(); fetchFolders()
    } catch { message.error('移动失败') }
  }

  const buildParentSelect = (nodes) => nodes.map(n => ({
    value: n.id, title: n.name,
    children: n.children?.length > 0 ? buildParentSelect(n.children) : undefined,
  }))

  const stepWithResponse = selectedStep ? { ...selectedStep, _runResponse: runResponse || selectedStep.lastResponse } : null

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 64px)', overflow: 'hidden' }}>
      <FolderTree
        folderTree={folderTree}
        scenarios={scenarios}
        loading={loading}
        selectedFolderId={selectedFolderId}
        selectedScenarioId={selectedScenario?.id}
        onSelectFolder={(folderId, descendantIds) => { setSelectedFolderId(folderId); setSelectedFolderIds(descendantIds || [folderId]); setSelectedScenario(null); setSelectedStep(null) }}
        onSelectScenario={(id) => { setSelectedFolderId(null); loadScenario(id) }}
        onDeleteScenario={handleDelete}
        onDeleteFolder={handleDeleteFolder}
        onCreateFolder={() => { setNewFolderName(''); setFolderModalOpen(true) }}
        onMoveScenario={handleMoveScenario}
        onRenameFolder={handleRenameFolder}
      />

      {!selectedScenario ? (
        <ScenarioList
          scenarios={scenarios}
          selectedFolderIds={selectedFolderIds}
          loading={loading}
          searchKeyword={searchKeyword}
          onSearchChange={setSearchKeyword}
          statusFilter={statusFilter}
          onStatusChange={setStatusFilter}
          kindFilter={kindFilter}
          onKindChange={setKindFilter}
          onSelectScenario={(id) => loadScenario(id)}
          onDelete={handleDelete}
          onGenerate={() => { setGenOpen(true); form.resetFields() }}
          onCreate={() => { setCreateScenarioOpen(true); createForm.resetFields() }}
          onBatch={handleBatchOperation}
          folderTree={folderTree}
        />
      ) : (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* ── 顶部工具栏（与列表页统一风格） ── */}
          <div style={{ padding: '10px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0, borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
            <Space size={8} wrap>
              <span style={{ fontWeight: 600, fontSize: 14, color: '#1d2129' }}>{selectedScenario.code}</span>
              <span style={{ color: '#595959', fontSize: 13, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block' }}>{selectedScenario.title}</span>
              <Tag color={selectedScenario.source === 'ai' ? 'blue' : 'default'} style={{ fontSize: 10 }}>
                {selectedScenario.source === 'ai' ? 'AI' : '手动'}
              </Tag>
              <Select size="small" value={selectedScenario.status} onChange={v => saveScenario({ status: v })}
                variant="borderless" style={{ fontSize: 11 }}
                options={[
                  { value: 'draft', label: <Tag>草稿</Tag> },
                  { value: 'published', label: <Tag color="#0ea5a0">已发布</Tag> },
                  { value: 'deprecated', label: <Tag color="default">已废弃</Tag> },
                ]}
              />
              {environments?.length > 0 && (
                <Select size="small" value={envId} onChange={changeEnv} allowClear
                  popupMatchSelectWidth={false}
                  placeholder="运行环境" style={{ width: 160 }}
                  options={buildEnvOptions(environments)} />
              )}
              <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={handleRunAll} loading={running}>运行全部</Button>
              {saveStatus && (
                <span style={{ fontSize: 12, color: saveStatus === 'saved' ? '#0ea5a0' : '#86909c', display: 'inline-flex', alignItems: 'center', gap: 4, transition: 'opacity 0.3s', opacity: saveStatus ? 1 : 0 }}>
                  {saveStatus === 'saving' ? '保存中...' : '✓ 已保存'}
                </span>
              )}
            </Space>
            <Space size={8}>
              {selectedScenario.status === 'draft' && (
                <Button size="small" icon={<RobotOutlined />} onClick={() => setOptimizeOpen(true)}>AI 优化</Button>
              )}
              <Button size="small" icon={<CopyOutlined />} onClick={handleCopyScenario}>复制</Button>
              {selectedScenario.status === 'draft' && (
                <Button size="small" icon={<ScissorOutlined />} onClick={() => setSplitMode(prev => !prev)}>拆分</Button>
              )}
              {selectedScenario.status === 'published' && (
                <Button size="small" icon={<BranchesOutlined />} onClick={handleNewVersion}>更新版本</Button>
              )}
              <Button type="text" size="small" onClick={() => { setSelectedScenario(null); setSelectedStep(null); setShowRunPanel(false) }}>
                ✕ 返回
              </Button>
            </Space>
          </div>
          {/* ── 步骤列表 + 编辑器/结果面板 ── */}
          <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
            <StepList
              scenario={selectedScenario}
              selectedStepId={selectedStep?.id}
              readonly={selectedScenario?.status !== 'draft'}
              onSelectStep={(step) => { setSelectedStep(step); setRunResponse(null) }}
              onAddStep={addStep}
              onReorderSteps={handleReorderSteps}
              splitMode={splitMode}
              onSplitModeChange={setSplitMode}
              onSplitScenario={handleSplitScenario}
            />
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'transparent' }}>
              <StepEditor
                step={stepWithResponse}
                running={!!runningStepId}
                readonly={selectedScenario?.status !== 'draft'}
                onSaveStep={saveStep}
                onRemoveStep={removeStep}
                onRunStep={handleRunStep}
                onStepChange={setSelectedStep}
              />
            </div>
          </div>
        </div>
      )}

      {/* AI 优化抽屉 */}
      <Drawer title="AI 优化" open={optimizeOpen} onClose={() => setOptimizeOpen(false)} width={420}
        footer={optimizePlan?.changes ? (
          <Space>
            <Button onClick={() => setOptimizePlan(null)}>重新分析</Button>
            <Button type="primary" onClick={async () => {
              const result = await handleApplyOptimize(optimizePlan.changes)
              if (result) { message.success(`已应用 ${result.applied} 项修改`); setOptimizeOpen(false) }
            }}>确认执行</Button>
          </Space>
        ) : null}
      >
        {!optimizePlan ? (
          <>
            <div style={{ marginBottom: 12, fontSize: 13, color: '#595959' }}>输入修改建议，AI 分析后给出方案：</div>
            <Input.TextArea rows={4} value={optimizeSuggestion} onChange={e => setOptimizeSuggestion(e.target.value)}
              placeholder="例如：增加中文用户名的测试、把超时时间改为10秒..." />
            <Button type="primary" icon={<RobotOutlined />} loading={optimizing} style={{ marginTop: 12 }}
              onClick={async () => {
                if (!optimizeSuggestion.trim()) return
                setOptimizing(true)
                try { setOptimizePlan(await handleAiOptimize(optimizeSuggestion)) }
                catch { message.error('分析失败') }
                finally { setOptimizing(false) }
              }}>分析方案</Button>
          </>
        ) : optimizePlan.error ? (
          <div style={{ color: '#e8453c' }}>{optimizePlan.error}</div>
        ) : (
          <>
            <div style={{ marginBottom: 12, fontWeight: 600 }}>{optimizePlan.summary}</div>
            {(optimizePlan.changes || []).map((c, i) => (
              <div key={i} style={{ padding: '8px 12px', marginBottom: 8, borderRadius: 6, border: '1px solid rgba(0,0,0,0.06)', background: c.action === 'add' ? 'rgba(14,165,160,0.06)' : c.action === 'delete' ? 'rgba(232,69,60,0.06)' : 'rgba(78,138,240,0.06)' }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                  {c.action === 'add' ? '+ 新增' : c.action === 'delete' ? '- 删除' : '~ 修改'}
                  {c.step?.name ? ` — ${c.step.name}` : ''}
                </div>
                {c.reason && <div style={{ fontSize: 11, color: '#8c8c8c' }}>{c.reason}</div>}
              </div>
            ))}
          </>
        )}
      </Drawer>

      {/* 运行结果抽屉 */}
      <Drawer
        title={null}
        open={showRunPanel}
        onClose={() => setShowRunPanel(false)}
        width={520}
        destroyOnClose={false}
        closable={false}
        styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column', height: '100%' } }}
      >
        <RunResultPanel
          results={runStepResults}
          scenario={selectedScenario}
          running={running}
          onClose={() => setShowRunPanel(false)}
          reportId={runReportId}
          envName={environments.find(e => e.id === envId)?.name}
          projectId={projectId}
        />
      </Drawer>

      {/* 单步运行结果抽屉 */}
      <Drawer
        title={null}
        open={showStepDrawer}
        onClose={() => setShowStepDrawer(false)}
        width={600}
        closable={false}
        styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column', height: '100%' } }}
      >
        <StepRunDrawer
          response={runResponse}
          stepName={stepDrawerName}
          onClose={() => setShowStepDrawer(false)}
        />
      </Drawer>

      <GenerateModal
        open={genOpen}
        onClose={() => setGenOpen(false)}
        form={form}
        generating={generating}
        genProgress={genProgress}
        onGenerate={handleGenerate}
        folderTree={folderTree}
        environments={environments}
        apiList={apiList}
        projectName={projectInfo.name}
        branchName={projectInfo.branchName}
        branchId={branchId}
      />

      <Modal
        title="新建模块"
        open={folderModalOpen}
        onOk={handleCreateFolder}
        onCancel={() => setFolderModalOpen(false)}
        okText="创建"
        cancelText="取消"
        width={420}
      >
        <Form layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item label="模块名称" required>
            <Input
              value={newFolderName}
              onChange={e => setNewFolderName(e.target.value)}
              placeholder="如：AUTH、USER_MGMT"
              onPressEnter={handleCreateFolder}
            />
          </Form.Item>
          <Form.Item label="父模块（可选）">
            <TreeSelect
              value={newFolderParent}
              onChange={setNewFolderParent}
              treeData={buildParentSelect(folderTree)}
              placeholder="顶级模块（不选则为一级模块）"
              allowClear
              style={{ width: '100%' }}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="新建场景"
        open={createScenarioOpen}
        onOk={handleCreateScenario}
        onCancel={() => setCreateScenarioOpen(false)}
        okText="创建"
        cancelText="取消"
        width={480}
      >
        <Form form={createForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="title" label="场景标题" rules={[{ required: true, message: '请输入标题' }]}>
            <Input placeholder="如：创建用户-正向测试" />
          </Form.Item>
          <Form.Item name="priority" label="优先级" initialValue="P1">
            <Select options={[
              { value: 'P0', label: 'P0 - 最高' },
              { value: 'P1', label: 'P1 - 高' },
              { value: 'P2', label: 'P2 - 中' },
              { value: 'P3', label: 'P3 - 低' },
            ]} />
          </Form.Item>
          <Form.Item name="folderId" label="目标文件夹（可选）">
            <TreeSelect
              treeData={buildParentSelect(folderTree)}
              placeholder="不选则不归入文件夹"
              allowClear
              style={{ width: '100%' }}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
