import { useState, useEffect, useCallback, useRef } from 'react'
import { Card, Input, Table, Tag, Button, Tree, Radio, Space, Pagination, Select, Modal, Upload, message, Form, Popconfirm, Tooltip, Empty, Spin, TreeSelect, Checkbox, Dropdown } from 'antd'
import { SearchOutlined, UploadOutlined, DownloadOutlined, PlusOutlined, InboxOutlined, SettingOutlined, EditOutlined, DeleteOutlined, CopyOutlined, StarFilled, RobotOutlined, LoadingOutlined, ApiOutlined, MenuFoldOutlined, MenuUnfoldOutlined, PlayCircleOutlined, ReloadOutlined, ClearOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { api, getValidToken } from '../../utils/request'
import { useBranch } from '../../utils/branch'
import { useEnv, buildEnvOptions } from '../../utils/env'
import TestForgeModal from './TestForgeModal'

// 和 scenario-gen/Stage5Review 用同一套分类，两处对不上的话质量统计会分裂
const REJECT_CATEGORIES = [
  { value: 'vague_expectation', label: '预期含糊' },
  { value: 'unspecific_data', label: '数据不具体' },
  { value: 'duplicate', label: '场景重复' },
  { value: 'misunderstood_requirement', label: '需求理解错' },
  { value: 'other', label: '其他' },
]

const priorityColors = { P0: '#fff', P1: '#fff', P2: '#fff', P3: '#fff' }
const priorityBg = { P0: '#e8453c', P1: '#ff7d00', P2: '#4e8af0', P3: 'rgba(0,0,0,0.08)' }
const statusMap = { automated: '已自动化', pending: '待自动化', script_removed: '脚本已移除', archived: '已归档' }
const statusColors = { automated: '#0ea5a0', pending: '#faad14', script_removed: '#e8453c', archived: '#bfbfbf' }
const statusBg = { automated: 'transparent', pending: 'transparent', script_removed: 'transparent', archived: 'transparent' }
// 状态体系 v2
const lifecycleMap = {
  draft: { label: '草稿', color: '#86909c', bg: 'rgba(0,0,0,0.03)' },
  done: { label: '完成', color: '#0ea5a0', bg: '#e0f7f6' },
  deprecated: { label: '废弃', color: '#e8453c', bg: '#fff2f0' },
}
const dimStatusMap = {
  not_started: { label: '未开始', color: '#c9cdd4' },
  draft: { label: '草稿', color: '#86909c' },
  debugging: { label: '调试中', color: '#faad14' },
  pending_review: { label: '待审', color: '#4e8af0' },
  executable: { label: '可执行', color: '#0ea5a0' },
  needs_fix: { label: '待修改', color: '#e8453c' },
}

// ---- 主页面 ----
export default function CaseManagement() {
  const navigate = useNavigate()
  const { projectId } = useParams()

  // 分支
  const [globalBranchId] = useBranch(projectId)

  // 项目环境
  const [runEnvId, setRunEnvId] = useEnv(projectId)
  const [environments, setEnvironments] = useState([])
  const [batchRunning, setBatchRunning] = useState(false)

  // 目录树
  const [folderTree, setFolderTree] = useState([])
  const [selectedFolderId, setSelectedFolderId] = useState(null)

  // 用例列表
  const [cases, setCases] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [readyFilter, setReadyFilter] = useState('')
  const [selectedRowKeys, setSelectedRowKeys] = useState([])
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  // 导入
  const [importOpen, setImportOpen] = useState(false)
  const [testforgeOpen, setTestforgeOpen] = useState(false)
  const [importResult, setImportResult] = useState(null)
  const [importing, setImporting] = useState(false)

  // 新建用例
  const [createCaseOpen, setCreateCaseOpen] = useState(false)
  const [createCaseForm] = Form.useForm()
  const [savingCase, setSavingCase] = useState(false)

  // 新建模块
  const [folderModalOpen, setFolderModalOpen] = useState(false)
  const [folderForm] = Form.useForm()
  const [savingFolder, setSavingFolder] = useState(false)

  // 导航面板折叠 & 拖拽调宽
  const [navCollapsed, setNavCollapsed] = useState(false)
  const [navWidth, setNavWidth] = useState(220)
  const resizingRef = useRef(false)
  const startXRef = useRef(0)
  const startWidthRef = useRef(220)

  const onResizeStart = useCallback((e) => {
    e.preventDefault()
    resizingRef.current = true
    startXRef.current = e.clientX
    startWidthRef.current = navWidth
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const onMove = (ev) => {
      if (!resizingRef.current) return
      const delta = ev.clientX - startXRef.current
      const next = Math.max(160, Math.min(400, startWidthRef.current + delta))
      setNavWidth(next)
    }
    const onUp = () => {
      resizingRef.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [navWidth])

  // ---- 数据加载 ----
  useEffect(() => {
    api.get('/environments').then(res => setEnvironments(res.data || [])).catch(() => {})
  }, [])

  const fetchFolders = useCallback(async () => {
    if (!projectId || !globalBranchId) return
    try {
      const res = await api.get(`/projects/${projectId}/branches/${globalBranchId}/folders`)
      setFolderTree(res.data || [])
    } catch { /* */ }
  }, [projectId, globalBranchId])

  const fetchCases = useCallback(async () => {
    if (!projectId || !globalBranchId) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ page, pageSize })
      if (keyword) params.set('keyword', keyword)
      if (statusFilter === 'deleted') {
        params.set('includeDeleted', 'true')
      } else if (statusFilter === 'pending_review') {
        params.set('reviewStatus', 'pending_review')
      } else if (['draft', 'done', 'deprecated'].includes(statusFilter)) {
        params.set('lifecycleStatus', statusFilter)
      } else if (statusFilter) {
        params.set('automationStatus', statusFilter)
      }
      // 维度就绪度筛选（如 ui:executable）——供批量执行前挑"该维度可跑"的用例
      if (readyFilter) {
        const [dim, st] = readyFilter.split(':')
        params.set(`${dim}Status`, st)
      }
      if (selectedFolderId) params.set('folderId', selectedFolderId)
      const res = await api.get(`/projects/${projectId}/branches/${globalBranchId}/cases?${params}`)
      setCases(res.data || [])
      setTotal(res.pagination?.total || 0)
    } catch { /* */ } finally { setLoading(false) }
  }, [projectId, globalBranchId, page, pageSize, keyword, statusFilter, readyFilter, selectedFolderId])

  useEffect(() => { fetchFolders() }, [fetchFolders])
  useEffect(() => { fetchCases() }, [fetchCases])

  // ---- 新建模块 ----
  const handleCreateFolder = async () => {
    let values
    try { values = await folderForm.validateFields() } catch { return }
    if (!globalBranchId) { message.warning('请先选择分支'); return }
    setSavingFolder(true)
    try {
      const params = new URLSearchParams({ name: values.name })
      if (values.parentId) params.set('parentId', values.parentId)
      await api.post(`/projects/${projectId}/branches/${globalBranchId}/folders?${params}`)
      message.success('模块创建成功')
      setFolderModalOpen(false)
      folderForm.resetFields()
      fetchFolders()
    } catch { /* */ } finally { setSavingFolder(false) }
  }

  // 从 folderTree 中根据 id 找到 folder name（递归查找）
  // 批量执行弹窗
  const [batchExecOpen, setBatchExecOpen] = useState(false)
  const [batchExecType, setBatchExecType] = useState('api')
  const [batchPrecheck, setBatchPrecheck] = useState({ total: 0, executable: 0, skipped: 0 })

  // 「跳过」有两种，原先合成一句「N 个无脚本」，是句假话：
  // 一条接口场景跑通过 69 次的用例，只因 apiStatus 还停在 debugging 就被算进"无脚本"。
  // 人看到"无脚本"会去写脚本，而实际该做的只是把状态推到「可执行」。
  const precheck = (selected, type) => {
    let executable = 0, notReady = 0, missing = 0
    selected.forEach(c => {
      const dim = type === 'api' ? c.apiStatus : c.uiStatus
      const has = type === 'api' ? c.hasApi : c.hasUi
      if (dim === 'executable' || (c.scriptRefFile && c.automationStatus === 'automated')) executable++
      else if (has) notReady++
      else missing++
    })
    return { total: selected.length, executable, notReady, missing, skipped: notReady + missing }
  }

  const openBatchExec = () => {
    if (!selectedRowKeys.length) { message.warning('请先选择用例'); return }
    const selected = cases.filter(c => selectedRowKeys.includes(c.id))
    setBatchExecType('ui')
    setBatchPrecheck(precheck(selected, 'ui'))
    setBatchExecOpen(true)
  }

  const updatePrecheck = (type) => {
    setBatchExecType(type)
    setBatchPrecheck(precheck(cases.filter(c => selectedRowKeys.includes(c.id)), type))
  }

  const handleBatchExec = async () => {
    if (!runEnvId) { message.warning('请选择执行环境'); return }
    setBatchRunning(true)
    try {
      const res = await api.post(`/projects/${projectId}/reports/execute-adhoc`, {
        caseIds: selectedRowKeys,
        branchId: globalBranchId,
        type: batchExecType,
        envId: runEnvId,
      })
      const d = res.data || res
      message.success(`执行已启动：${d.executable} 条可执行，${d.skipped} 条跳过`)
      setBatchExecOpen(false)
      setSelectedRowKeys([])
      navigate(`/projects/${projectId}/reports/${d.reportId}`)
    } catch (e) {
      message.error(e.message || '执行失败')
    } finally {
      setBatchRunning(false)
    }
  }

  const findFolderNameById = (nodes, targetId) => {
    for (const n of nodes) {
      if (n.id === targetId) return n.name
      if (n.children?.length) {
        const found = findFolderNameById(n.children, targetId)
        if (found) return found
      }
    }
    return null
  }

  // 构建模块 TreeSelect 数据（支持 N 层，显示完整路径）
  const buildFolderTreeSelect = (nodes, parentPath = '') => nodes.map(n => {
    const fullPath = parentPath ? `${parentPath} / ${n.name}` : n.name
    return {
      value: n.name,
      title: fullPath,
      id: n.id,
      fullPath,
      children: n.children?.length > 0 ? buildFolderTreeSelect(n.children, fullPath) : undefined,
    }
  })
  const folderTreeSelectData = buildFolderTreeSelect(folderTree)

  // 构建父模块 TreeSelect（创建模块时选父级）
  const buildParentTreeSelect = (nodes, parentPath = '') => nodes.map(n => {
    const fullPath = parentPath ? `${parentPath} / ${n.name}` : n.name
    return {
      value: n.id,
      title: fullPath,
      children: n.children?.length > 0 ? buildParentTreeSelect(n.children, fullPath) : undefined,
    }
  })
  const parentTreeSelectData = buildParentTreeSelect(folderTree)

  // ---- 新建用例 ----
  const handleCreateCase = async () => {
    let values
    try { values = await createCaseForm.validateFields() } catch { return }
    if (!globalBranchId) { message.warning('请先选择分支'); return }
    setSavingCase(true)
    try {
      await api.post(`/projects/${projectId}/branches/${globalBranchId}/cases`, {
        title: values.title,
        type: values.type,
        module: values.module,
        priority: values.priority || 'P2',
        steps: [{ seq: 1, action: '待补充', expected: '' }],
        apiScenario: values.initApi ? { steps: [{ seq: 1, phase: 'action', action: '待补充', expected: '', apiEndpoint: '' }], scriptRefFile: '', scriptRefFunc: '', variablesUsed: [] } : undefined,
        uiScenario: values.initUi ? { steps: [{ seq: 1, phase: 'action', action: '待补充', expected: '', uiTarget: '' }], scriptRefFile: '', scriptRefFunc: '', variablesUsed: [] } : undefined,
        // 勾选的维度就是"这条要做到什么程度"。Claude Code 断点续跑靠它判还欠什么 ——
        // 不带这个字段的话，人建的用例在 CC 眼里永远只需要手工步骤。
        targetLevel: values.initUi ? 'full' : (values.initApi ? 'spec_api' : 'spec'),
      })
      message.success('用例创建成功')
      setCreateCaseOpen(false)
      createCaseForm.resetFields()
      fetchCases()
      fetchFolders()
    } catch { /* */ } finally { setSavingCase(false) }
  }

  // ---- 导出 ----
  // ---- 导出 Excel（后端生成） ----
  const [exporting, setExporting] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [reviewLoading, setReviewLoading] = useState(false)
  const [reviewResult, setReviewResult] = useState(null)
  const [reviewSteps, setReviewSteps] = useState([])

  // 空目录清理：目录是建用例时顺带创建的，硬删用例从不回收它（已在后端修掉），
  // 加上手动建了没用的，攒下来一屏 (0)，人分不清哪些是真模块。
  // 只列不自动删 —— 空目录也可能是人先搭好的结构。
  const [emptyFolders, setEmptyFolders] = useState(null)
  const [emptyPicked, setEmptyPicked] = useState([])

  const openEmptyFolders = async () => {
    try {
      const res = await api.get(`/projects/${projectId}/branches/${globalBranchId}/folders/empty`)
      const list = res.data || []
      if (!list.length) { message.info('没有空目录'); return }
      setEmptyFolders(list)
      setEmptyPicked(list.map(f => f.id))
    } catch (e) { message.error(e.message || '加载失败') }
  }

  const doPruneFolders = async () => {
    try {
      const res = await api.post(`/projects/${projectId}/branches/${globalBranchId}/folders/prune-empty`,
        { folderIds: emptyPicked })
      message.success(`已清理 ${res.data?.pruned ?? 0} 个空目录`)
      setEmptyFolders(null)
      fetchFolders()
    } catch (e) { message.error(e.message || '清理失败') }
  }

  // 就地审核：列表上看到「待审」就能在列表上处理掉，不用绕去生成向导的第 5 步。
  // 打回**必须带理由**——后端 case_service 会硬校验 review_reason.category，
  // 只发 reviewStatus 会 400。所以「通过」直接走，「打回」先弹理由。
  const [rejectFor, setRejectFor] = useState(null)
  const [rejectCategory, setRejectCategory] = useState('vague_expectation')
  const [rejectText, setRejectText] = useState('')

  const approveCase = async (caseId) => {
    try {
      await api.put(`/projects/${projectId}/branches/${globalBranchId}/cases/${caseId}`, { reviewStatus: 'approved' })
      message.success('已通过')
      fetchCases()
    } catch (e) { message.error(e.message || '操作失败') }
  }

  const rejectCase = async () => {
    try {
      await api.put(`/projects/${projectId}/branches/${globalBranchId}/cases/${rejectFor}`, {
        reviewStatus: 'rejected',
        reviewReason: { category: rejectCategory, text: rejectText },
      })
      message.success('已打回')
      setRejectFor(null); setRejectText('')
      fetchCases()
    } catch (e) { message.error(e.message || '操作失败') }
  }

  const handleQualityReview = () => {
    if (!globalBranchId) { message.warning('请先选择分支'); return }
    setReviewOpen(true)
    setReviewResult(null)
    setReviewSteps([])
    setReviewLoading(true)

    const url = `/projects/${projectId}/branches/${globalBranchId}/skills/tb-quality-review`
    const body = { folderId: selectedFolderId || undefined }

    api.stream(url, body, {
      onChunk: (data) => {
        if (data.type === 'step_start' || data.type === 'step_done') {
          setReviewSteps(prev => [...prev, data])
        }
        if (data.type === 'error') {
          message.error(data.message)
          setReviewLoading(false)
        }
      },
      onDone: (data) => {
        if (data && data.report) {
          setReviewResult(data)
        }
        setReviewLoading(false)
      },
      onError: (msg) => { message.error(msg); setReviewLoading(false) },
    })
  }
  const handleExport = async () => {
    if (!globalBranchId) { message.warning('请先选择分支'); return }
    setExporting(true)
    try {
      // 跟页面看到的一致：勾了行就只导勾的，没勾就带上当前所有筛选。
      // 此前只传 keyword/automationStatus/folderId，筛「待审核」照样导全部 105 条。
      const params = new URLSearchParams()
      if (selectedRowKeys.length) {
        params.set('caseIds', selectedRowKeys.join(','))
      } else {
        if (keyword) params.set('keyword', keyword)
        if (selectedFolderId) params.set('folderId', selectedFolderId)
        if (statusFilter === 'pending_review') params.set('reviewStatus', 'pending_review')
        else if (['draft', 'done', 'deprecated'].includes(statusFilter)) params.set('lifecycleStatus', statusFilter)
        else if (statusFilter && statusFilter !== 'deleted') params.set('automationStatus', statusFilter)
        if (readyFilter) {
          const [dim, st] = readyFilter.split(':')
          params.set(`${dim}Status`, st)
        }
      }

      const token = await getValidToken()
      const res = await fetch(`/api/projects/${projectId}/branches/${globalBranchId}/cases/export/excel?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      })

      if (!res.ok) {
        message.error('导出失败')
        return
      }

      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `用例导出-${new Date().toISOString().slice(0, 10)}.xlsx`
      a.click()
      URL.revokeObjectURL(url)
      message.success('导出成功')
    } catch {
      message.error('导出失败')
    } finally {
      setExporting(false)
    }
  }

  // ---- 导入 ----
  // 同步删除默认关。开着的话，"之前导入过但这次文件里没有"的用例会被删掉 ——
  // 实测传一个只含 1 条用例的 Excel，删掉了 3 条无关用例，界面上一句提示都没有。
  const [syncDelete, setSyncDelete] = useState(false)

  const handleImportFile = async (file) => {
    if (!globalBranchId) return false
    setImporting(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const token = await getValidToken()
      const res = await fetch(`/api/projects/${projectId}/branches/${globalBranchId}/cases/import?syncDelete=${syncDelete}`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      })
      const data = await res.json()
      if (!res.ok) {
        message.error(data?.error?.message || '导入失败')
      } else {
        setImportResult(data.data)
      }
    } catch { message.error('导入失败') } finally { setImporting(false) }
    return false
  }

  const handleImportClose = () => {
    setImportOpen(false)
    if (importResult) {
      setImportResult(null)
      fetchCases()
      fetchFolders()
    }
  }

  // ---- 目录树 ----
  const buildTreeData = (nodes) => nodes.map(n => ({
    title: `${n.name} (${n.caseCount})`,
    key: n.id,
    children: n.children?.length > 0 ? buildTreeData(n.children) : undefined,
  }))

  const treeData = buildTreeData(folderTree)

  const onTreeSelect = (keys) => {
    setSelectedFolderId(keys.length > 0 ? keys[0] : null)
    setPage(1)
  }

  // ---- 列表列（可配置） ----
  const allColumns = [
    { key: 'caseCode', title: '用例ID', dataIndex: 'caseCode', width: 135, defaultVisible: true, render: v => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: '#86909c' }}>{v}</span> },
    { key: 'title', title: '标题', dataIndex: 'title', ellipsis: true, defaultVisible: true, fixed: true, render: (v, row) => (
      <span
        onClick={() => navigate(`/projects/${projectId}/cases/${row.id}?branchId=${globalBranchId}`)}
        style={{ color: '#1d2129', cursor: 'pointer', fontWeight: 500 }}
        onMouseEnter={e => e.target.style.color = '#0ea5a0'}
        onMouseLeave={e => e.target.style.color = '#1d2129'}
      >{row.isCore && <Tooltip title="核心/标杆用例（供其他用例参考生成）"><StarFilled style={{ color: '#fa8c16', marginRight: 4, fontSize: 12 }} /></Tooltip>}{v}</span>
    )},
    { key: 'type', title: '类型', dataIndex: 'type', width: 50, defaultVisible: true, render: v => <span style={{ fontSize: 11, color: '#86909c' }}>{v?.toUpperCase()}</span> },
    // 三个标签各有各的真实来源，后端 list_case_assets 已经把两个存储取过并集了。
    // 别再回头读 row.apiScenario —— 那只是其中一个存储，MCP 回推的场景不在里面。
    { key: 'scenarios', title: '场景', width: 112, defaultVisible: true, render: (_, row) => {
      const apiSt = row.apiScenarioStatus
      const uiSt = row.uiScenarioStatus
      const apiColor = apiSt === 'completed' ? '#0ea5a0' : apiSt === 'debugging' ? '#faad14' : '#86909c'
      const uiColor = uiSt === 'completed' ? '#7c5cbf' : uiSt === 'debugging' ? '#faad14' : '#86909c'
      const none = !row.hasManual && !row.hasApi && !row.hasUi
      if (none) return <span style={{ fontSize: 11, color: '#c9cdd4' }}>空壳</span>
      return (
        <Space size={2}>
          {row.hasManual && <Tag style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px', border: 'none', background: '#e0f7f6', color: '#0ea5a0', margin: 0 }}>手动</Tag>}
          {row.hasApi && <Tag style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px', border: 'none', background: '#e0f7f6', color: apiColor, margin: 0 }}>
            {row.isApiTemplate && <StarFilled style={{ fontSize: 9, color: '#faad14', marginRight: 2 }} />}接口
          </Tag>}
          {row.hasUi && <Tag style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px', border: 'none', background: '#f5f0ff', color: uiColor, margin: 0 }}>
            {row.isUiTemplate && <StarFilled style={{ fontSize: 9, color: '#faad14', marginRight: 2 }} />}UI
          </Tag>}
        </Space>
      )
    }},
    { key: 'priority', title: '优先级', dataIndex: 'priority', width: 56, align: 'center', defaultVisible: true, render: v => <Tag style={{ background: priorityBg[v], color: priorityColors[v], border: 'none', margin: 0 }}>{v}</Tag> },
    { key: 'module', title: '模块', dataIndex: 'module', width: 100, defaultVisible: false, render: v => <span style={{ fontSize: 12 }}>{v || '-'}</span> },
    { key: 'subModule', title: '子模块', dataIndex: 'subModule', width: 100, defaultVisible: false, render: v => <span style={{ fontSize: 12 }}>{v || '-'}</span> },
    { key: 'lifecycleStatus', title: '状态', dataIndex: 'lifecycleStatus', width: 68, defaultVisible: true, render: v => { const m = lifecycleMap[v] || lifecycleMap.draft; return <Tag style={{ background: m.bg, color: m.color, border: 'none', margin: 0, fontSize: 11 }}>{m.label}</Tag> } },
    // 三个维度挤成 10px 的小圆点，得逐个 hover 才知道是什么 —— 字号提到 11、
    // 整组一个 tooltip 一次说清三维，不用挨个悬停
    { key: 'dimStatus', title: '手动/UI/接口', dataIndex: 'manualStatus', width: 150, defaultVisible: true, render: (_, r) => {
      const dims = [['手', '手动', r.manualStatus], ['U', 'UI', r.uiStatus], ['接', '接口', r.apiStatus]]
      return (
        <Tooltip title={dims.map(([, full, st]) => `${full}：${(dimStatusMap[st] || dimStatusMap.not_started).label}`).join('　')}>
          <span style={{ display: 'inline-flex', gap: 5 }}>
            {dims.map(([k, , st]) => {
              const m = dimStatusMap[st] || dimStatusMap.not_started
              return <span key={k} style={{ fontSize: 11, padding: '0 5px', borderRadius: 6, background: m.color + '1f', color: m.color, lineHeight: '18px' }}>{k}</span>
            })}
          </span>
        </Tooltip>
      )
    } },
    { key: 'source', title: '来源', dataIndex: 'source', width: 48, align: 'center', defaultVisible: true, render: v => <span style={{ fontSize: 11, color: v === 'ai' ? '#7cacf8' : '#c9cdd4' }}>{v === 'imported' ? '导入' : v === 'ai' ? 'AI' : '手动'}</span> },
    // 三种状态，别混成一个 F：
    //   人工标记 F   —— 人自己撤
    //   已隔离       —— 人主动点的，到期自己回来，**执行时跳过**
    //   不稳定       —— 平台检测到的，**照常执行**，只是提示该去查
    // 检测到不稳定不等于被隔离：自动把它藏起来 = 自动让人不去查这个问题。
    { key: 'isFlaky', dataIndex: 'isFlaky', width: 66, align: 'center', defaultVisible: true,
      title: (
        <Tooltip title="结果反复翻转的用例会自动打上标记。这一列大部分时候是空的 —— 空 = 目前没有不稳定的用例，不是功能没跑。">
          <span style={{ borderBottom: '1px dotted #c9cdd4' }}>Flaky</span>
        </Tooltip>
      ),
      render: (v, r) => {
        const until = r.quarantinedUntil ? new Date(r.quarantinedUntil) : null
        const quarantined = until && until > new Date()
        if (v) return <Tooltip title="人工标记为 Flaky，执行时跳过；要恢复请在用例详情里取消标记"><Tag color="#fff7e6" style={{ color: '#faad14', border: 'none', margin: 0 }}>F</Tag></Tooltip>
        if (quarantined) return (
          <Tooltip title={`已隔离（人工），执行时跳过；${until.toLocaleString('zh-CN')} 到期后自动恢复`}>
            <Tag color="#fff1f0" style={{ color: '#e8453c', border: 'none', margin: 0 }}>隔离</Tag>
          </Tooltip>
        )
        if (r.flakyEvidence) return (
          <Tooltip title={`${r.flakyEvidence?.note || '结果反复翻转'}。**仍会照常执行** —— 打开用例详情看"该往哪儿看"`}>
            <Tag color="#fff7e6" style={{ color: '#fa8c16', border: 'none', margin: 0 }}>不稳定</Tag>
          </Tooltip>
        )
        return null
      } },
    // 「待审」以前只是个标签：列表里看得到，却只能去 AI 生成用例的第 5 步才审得了。
    // 看得见 ≠ 做得了 —— 在哪看到就在哪能处理，点标签直接通过/打回。
    { key: 'reviewStatus', title: '审核', dataIndex: 'reviewStatus', width: 62, align: 'center', defaultVisible: true, render: (v, row) => {
      if (!v) return null
      if (v === 'approved') return <Tag style={{ fontSize: 10, background: '#e0f7f6', color: '#0ea5a0', border: 'none', margin: 0 }}>已审</Tag>
      if (v === 'rejected') return <Tag color="error" style={{ fontSize: 10, margin: 0 }}>已拒</Tag>
      return (
        <Dropdown trigger={['click']} menu={{ items: [
          { key: 'approved', label: '通过' },
          { key: 'rejected', label: '打回', danger: true },
        ], onClick: ({ key, domEvent }) => { domEvent.stopPropagation(); key === 'approved' ? approveCase(row.id) : setRejectFor(row.id) } }}>
          <Tag onClick={e => e.stopPropagation()}
            style={{ fontSize: 10, cursor: 'pointer', background: 'rgba(78,138,240,0.08)', color: '#4e8af0', border: 'none', margin: 0 }}>
            待审 ▾
          </Tag>
        </Dropdown>
      )
    }},
    { key: 'qualityScore', title: '评分', dataIndex: 'qualityScore', width: 48, align: 'center', defaultVisible: false, render: v => {
      if (!v || v.total == null) return <span style={{ color: '#c9cdd4' }}>—</span>
      const color = v.total >= 85 ? '#0ea5a0' : v.total >= 70 ? '#4e8af0' : '#faad14'
      return <span style={{ color, fontWeight: 600, fontSize: 12 }}>{v.total}</span>
    }},
    { key: 'scriptRefFile', title: '脚本文件', dataIndex: 'scriptRefFile', width: 200, ellipsis: true, defaultVisible: false, render: v => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: '#86909c' }}>{v || '-'}</span> },
    { key: 'teaId', title: 'TEA ID', dataIndex: 'teaId', width: 150, defaultVisible: false, render: v => <span style={{ fontSize: 12, color: '#86909c' }}>{v || '-'}</span> },
    { key: 'createdAt', title: '创建时间', dataIndex: 'createdAt', width: 150, defaultVisible: false, render: v => <span style={{ fontSize: 12, color: '#86909c' }}>{v ? new Date(v).toLocaleString('zh-CN') : '-'}</span> },
    { key: 'updatedAt', title: '更新时间', dataIndex: 'updatedAt', width: 150, defaultVisible: false, render: v => <span style={{ fontSize: 12, color: '#86909c' }}>{v ? new Date(v).toLocaleString('zh-CN') : '-'}</span> },
    { key: 'actions', title: '操作', width: 80, align: 'center', defaultVisible: true, render: (_, row) => (
      statusFilter === 'deleted' ? (
        <Popconfirm title="确定彻底删除此用例？此操作不可恢复！" onConfirm={async () => {
          try {
            await api.post(`/projects/${projectId}/branches/${globalBranchId}/cases/batch`, { caseIds: [row.id], action: 'hard_delete' })
            message.success('已彻底删除')
            fetchCases()
          } catch { /* */ }
        }}>
          <Button type="link" size="small" danger style={{ fontSize: 12, padding: '0 4px' }}>彻底删除</Button>
        </Popconfirm>
      ) : (
        <Space size={6}>
          <Tooltip title="复制用例">
            <span
              onClick={async (e) => {
                e.stopPropagation()
                try {
                  await api.post(`/projects/${projectId}/branches/${globalBranchId}/cases/${row.id}/copy`)
                  message.success('复制成功')
                  fetchCases()
                } catch { message.error('复制失败') }
              }}
              style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 30, height: 26, borderRadius: 6, cursor: 'pointer', color: '#0ea5a0', background: 'rgba(14,165,160,0.08)', transition: 'all 0.2s' }}
              onMouseEnter={e => { e.currentTarget.style.background = 'rgba(14,165,160,0.18)'; e.currentTarget.style.transform = 'scale(1.08)' }}
              onMouseLeave={e => { e.currentTarget.style.background = 'rgba(14,165,160,0.08)'; e.currentTarget.style.transform = 'scale(1)' }}
            ><CopyOutlined style={{ fontSize: 13 }} /></span>
          </Tooltip>
          <Popconfirm title="确定删除此用例？" onConfirm={async () => {
            try {
              await api.del(`/projects/${projectId}/branches/${globalBranchId}/cases/${row.id}`)
              message.success('已删除')
              fetchCases()
              fetchFolders()
            } catch { /* */ }
          }}>
            <Tooltip title="删除用例">
              <span
                onClick={e => e.stopPropagation()}
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 30, height: 26, borderRadius: 6, cursor: 'pointer', color: '#e8453c', background: 'rgba(232,69,60,0.06)', transition: 'all 0.2s' }}
                onMouseEnter={e => { e.currentTarget.style.background = 'rgba(232,69,60,0.15)'; e.currentTarget.style.transform = 'scale(1.08)' }}
                onMouseLeave={e => { e.currentTarget.style.background = 'rgba(232,69,60,0.06)'; e.currentTarget.style.transform = 'scale(1)' }}
              ><DeleteOutlined style={{ fontSize: 13 }} /></span>
            </Tooltip>
          </Popconfirm>
        </Space>
      )
    )},
  ]

  const [visibleColumnKeys, setVisibleColumnKeys] = useState(() =>
    allColumns.filter(c => c.defaultVisible).map(c => c.key)
  )
  const [columnSettingOpen, setColumnSettingOpen] = useState(false)

  const columns = [
    ...allColumns.filter(c => c.fixed || visibleColumnKeys.includes(c.key)),
    {
      title: (
        <Tooltip title="列设置">
          <SettingOutlined
            onClick={() => setColumnSettingOpen(true)}
            style={{ color: '#c9cdd4', cursor: 'pointer', fontSize: 14 }}
            onMouseEnter={e => e.target.style.color = '#0ea5a0'}
            onMouseLeave={e => e.target.style.color = '#c9cdd4'}
          />
        </Tooltip>
      ),
      key: '_settings',
      width: 40,
      align: 'center',
      render: () => null,
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 70px)' }}>
      <div style={{ flex: 1, display: 'flex', gap: 0, minHeight: 0 }}>
        {/* 左侧树 */}
        {!navCollapsed && (
          <Card style={{ width: navWidth, flexShrink: 0, overflow: 'auto', borderRadius: '16px 0 0 16px' }}
            styles={{ body: { padding: '8px 4px' }, header: { padding: '0 8px 0 12px', minHeight: 36, borderBottom: '1px solid rgba(0,0,0,0.04)' } }}
            title={<span style={{ fontSize: 13, fontWeight: 600 }}>用例导航</span>}
            extra={
              <Space size={0}>
                <Tooltip title="刷新目录">
                  <Button type="text" size="small" icon={<ReloadOutlined />} onClick={() => { fetchFolders(); fetchCases() }} style={{ color: '#c9cdd4' }} />
                </Tooltip>
                <Button type="text" size="small" icon={<PlusOutlined />} onClick={() => setFolderModalOpen(true)} style={{ color: '#0ea5a0' }} />
                <Tooltip title="清理空目录">
                  <Button type="text" size="small" icon={<ClearOutlined />} onClick={openEmptyFolders} style={{ color: '#c9cdd4' }} />
                </Tooltip>
                <Tooltip title="收起导航">
                  <Button type="text" size="small" icon={<MenuFoldOutlined />} onClick={() => setNavCollapsed(true)} style={{ color: '#c9cdd4' }} />
                </Tooltip>
              </Space>
            }>
            {treeData.length > 0 ? (
              <Tree
                treeData={treeData}
                defaultExpandAll
                onSelect={onTreeSelect}
                blockNode
                style={{ fontSize: 13 }}
                selectedKeys={selectedFolderId ? [selectedFolderId] : []}
                titleRender={(node) => (
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
                    <span>{node.title}</span>
                    <Popconfirm
                      title="确定删除此目录？"
                      description="仅允许删除空目录"
                      onConfirm={async (e) => {
                        e?.stopPropagation()
                        try {
                          await api.del(`/projects/${projectId}/branches/${globalBranchId}/folders/${node.key}`)
                          message.success('目录已删除')
                          fetchFolders()
                        } catch { /* request.js 显示错误 */ }
                      }}
                      onCancel={e => e?.stopPropagation()}
                    >
                      <Button type="text" size="small" icon={<DeleteOutlined />}
                        onClick={e => e.stopPropagation()}
                        style={{ color: '#c9cdd4', opacity: 0.5, fontSize: 11 }}
                        onMouseEnter={e => e.currentTarget.style.opacity = 1}
                        onMouseLeave={e => e.currentTarget.style.opacity = 0.5} />
                    </Popconfirm>
                  </div>
                )}
              />
            ) : (
              <div style={{ textAlign: 'center', padding: 20, color: '#86909c', fontSize: 12 }}>
                暂无目录
                <br />
                <Button type="link" size="small" onClick={() => setFolderModalOpen(true)}>+ 创建模块</Button>
              </div>
            )}
          </Card>
        )}

        {/* 拖拽调宽手柄 / 展开按钮 */}
        {navCollapsed ? (
          <Tooltip title="展开导航" placement="right">
            <div
              onClick={() => setNavCollapsed(false)}
              style={{
                width: 20, flexShrink: 0, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: 'rgba(255,255,255,0.35)', borderRadius: '12px 0 0 12px', transition: 'background 0.2s',
              }}
              onMouseEnter={e => e.currentTarget.style.background = 'rgba(14,165,160,0.08)'}
              onMouseLeave={e => e.currentTarget.style.background = 'rgba(255,255,255,0.35)'}
            >
              <MenuUnfoldOutlined style={{ fontSize: 11, color: '#86909c' }} />
            </div>
          </Tooltip>
        ) : (
          <div
            onMouseDown={onResizeStart}
            style={{
              width: 6, flexShrink: 0, cursor: 'col-resize', display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: 'transparent', transition: 'background 0.2s',
            }}
            onMouseEnter={e => e.currentTarget.style.background = 'rgba(14,165,160,0.15)'}
            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
          >
            <div style={{ width: 2, height: 24, borderRadius: 1, background: 'rgba(0,0,0,0.08)' }} />
          </div>
        )}

        {/* 右侧列表 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
          {/* 工具栏 */}
          <Card styles={{ body: { padding: '8px 16px' } }} style={{ flexShrink: 0 }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 12px', alignItems: 'center' }}>
              <Input prefix={<SearchOutlined style={{ color: '#c9cdd4' }} />} placeholder="搜索用例ID或标题" value={keyword}
                onChange={e => { setKeyword(e.target.value); setPage(1) }} allowClear style={{ width: 220 }}
                onPressEnter={fetchCases} />
              <Radio.Group value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1) }} size="small" buttonStyle="solid">
                <Radio.Button value="">全部</Radio.Button>
                <Radio.Button value="draft">草稿</Radio.Button>
                <Radio.Button value="done">完成</Radio.Button>
                <Radio.Button value="deprecated">废弃</Radio.Button>
                <Radio.Button value="pending_review">待审核</Radio.Button>
                <Radio.Button value="deleted">已删除</Radio.Button>
              </Radio.Group>
              <Select size="small" value={readyFilter} onChange={v => { setReadyFilter(v); setPage(1) }}
                style={{ width: 150 }} popupMatchSelectWidth={false}
                options={[
                  { value: '', label: '就绪度：不限' },
                  { value: 'ui:executable', label: 'UI 可执行' },
                  { value: 'ui:pending_review', label: 'UI 待审' },
                  { value: 'api:executable', label: '接口 可执行' },
                  { value: 'api:pending_review', label: '接口 待审' },
                  { value: 'manual:executable', label: '手动 可执行' },
                ]} />
              <span style={{ flex: 1 }} />
              <Space size={6} wrap>
                <Tooltip title="粘贴需求文档（PRD/用户故事），AI 自动生成手工测试用例（操作步骤+预期结果）">
                  <Button type="primary" icon={<RobotOutlined />}
                    onClick={() => navigate(`/projects/${projectId}/scenario-gen?taskId=new`)}>
                    AI 生成用例
                  </Button>
                </Tooltip>
                <Tooltip title="从 API 接口定义生成手工测试用例，需要接口信息">
                  <Button ghost icon={<ApiOutlined />} onClick={() => setTestforgeOpen(true)}>从接口生成</Button>
                </Tooltip>
                {/* 批量「AI 生成脚本」已下线：走的是 scripts/generate-stream 那条平台侧生成管道，
                    实测跑不通（详情页的单条入口同批下线）。UI 脚本改由外部 Claude Code 写好跑通后
                    经 tb_sync_ui_script 回推。 */}
                <Tooltip title="AI 从完整性/准确性/有效性/可执行性 4 维度评审当前模块的用例质量，输出评分和改进建议">
                  <Button icon={<SearchOutlined />} onClick={() => handleQualityReview()}>AI 评审</Button>
                </Tooltip>
                <Button icon={<UploadOutlined />} size="small" onClick={() => setImportOpen(true)}>导入</Button>
                <Tooltip title={selectedRowKeys.length
                  ? `导出勾选的 ${selectedRowKeys.length} 条`
                  : '导出当前筛选下的全部用例（含手动步骤、前置条件、预期结果；接口场景和 UI 脚本正文不在表格里）'}>
                  <Button icon={<DownloadOutlined />} size="small" onClick={handleExport} loading={exporting}>
                    导出{selectedRowKeys.length ? ` (${selectedRowKeys.length})` : ''}
                  </Button>
                </Tooltip>
                <Button type="primary" icon={<PlusOutlined />} size="small" onClick={() => {
                  createCaseForm.resetFields()
                  if (selectedFolderId) {
                    const folderName = findFolderNameById(folderTree, selectedFolderId)
                    if (folderName) createCaseForm.setFieldValue('module', folderName)
                  }
                  setCreateCaseOpen(true)
                }}>新建用例</Button>
                {statusFilter === 'deleted' && total > 0 && (
                  <Popconfirm
                    title="清空回收站"
                    description={`将彻底删除全部 ${total} 条已删除用例，不可恢复。关联的脚本、场景变量会一并清理；历史测试报告保留但解除关联。`}
                    okText="确认清空" okButtonProps={{ danger: true }} cancelText="取消"
                    onConfirm={async () => {
                      try {
                        const r = await api.post(`/projects/${projectId}/branches/${globalBranchId}/cases/empty-trash`)
                        message.success(`已彻底删除 ${r.data?.succeeded ?? 0} 条`)
                        setSelectedRowKeys([]); fetchCases()
                      } catch (e) { message.error(e?.message || '清空失败') }
                    }}>
                    <Button danger size="small" icon={<DeleteOutlined />}>清空回收站 ({total})</Button>
                  </Popconfirm>
                )}
              </Space>
            </div>
            {selectedRowKeys.length > 0 && (
              <div style={{ marginTop: 10, padding: '8px 12px', background: statusFilter === 'deleted' ? '#fff2f0' : '#e0f7f6', borderRadius: 12, display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ fontSize: 13, color: statusFilter === 'deleted' ? '#e8453c' : '#0ea5a0' }}>已选 {selectedRowKeys.length} 条</span>
                {statusFilter === 'deleted' ? (
                  <Popconfirm title={`确定彻底删除 ${selectedRowKeys.length} 条用例？此操作不可恢复！`} onConfirm={async () => {
                    try {
                      await api.post(`/projects/${projectId}/branches/${globalBranchId}/cases/batch`, { caseIds: selectedRowKeys, action: 'hard_delete' })
                      message.success('批量彻底删除成功'); setSelectedRowKeys([]); fetchCases()
                    } catch { /* */ }
                  }}>
                    <Button size="small" type="link" danger>批量彻底删除</Button>
                  </Popconfirm>
                ) : (<>
                <Button size="small" type="primary" icon={<PlayCircleOutlined />}
                  onClick={openBatchExec}>
                  批量执行
                </Button>
                <div style={{ width: 1, height: 16, background: 'rgba(0,0,0,0.1)' }} />
                <Popconfirm title={`确定归档 ${selectedRowKeys.length} 条用例？`} onConfirm={async () => {
                  try {
                    await api.post(`/projects/${projectId}/branches/${globalBranchId}/cases/batch`, { caseIds: selectedRowKeys, action: 'archive' })
                    message.success('批量归档成功'); setSelectedRowKeys([]); fetchCases()
                  } catch { /* */ }
                }}>
                  <Button size="small" type="link">批量归档</Button>
                </Popconfirm>
                <Select size="small" placeholder="修改优先级" style={{ width: 110 }}
                  onChange={async (val) => {
                    try {
                      await api.post(`/projects/${projectId}/branches/${globalBranchId}/cases/batch`, { caseIds: selectedRowKeys, action: 'set_priority', priority: val })
                      message.success('优先级已修改'); setSelectedRowKeys([]); fetchCases()
                    } catch { /* */ }
                  }}
                  options={['P0','P1','P2','P3'].map(p => ({ value: p, label: p }))}
                />
                <Popconfirm title={`确定删除 ${selectedRowKeys.length} 条用例？`} onConfirm={async () => {
                  try {
                    await api.post(`/projects/${projectId}/branches/${globalBranchId}/cases/batch`, {
                      action: 'delete', caseIds: selectedRowKeys,
                    })
                    message.success('批量删除成功')
                    setSelectedRowKeys([])
                    fetchCases()
                    fetchFolders()
                  } catch { /* */ }
                }}>
                  <Button size="small" type="link" danger>批量删除</Button>
                </Popconfirm>
                </>)}
              </div>
            )}
          </Card>

          {/* 表格 */}
          <Card style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }} styles={{ body: { padding: 0, flex: 1, display: 'flex', flexDirection: 'column' } }}>
            <Table
              dataSource={cases}
              columns={columns}
              rowKey="id"
              pagination={false}
              size="small"
              loading={loading}
              scroll={{ y: 'calc(100vh - 330px)' }}
              rowSelection={{ selectedRowKeys: selectedRowKeys, onChange: setSelectedRowKeys }}
              style={{ flex: 1 }}
              locale={{ emptyText: <Empty description="暂无用例" /> }}
              onRow={(record) => ({ style: { cursor: 'pointer' }, onClick: (e) => { if (e.target.closest('.ant-checkbox-wrapper, .ant-btn, .ant-popconfirm, a')) return; navigate(`/projects/${projectId}/cases/${record.id}?branchId=${globalBranchId}`) } })}
            />
            <div style={{ padding: '12px 16px', borderTop: '1px solid rgba(0,0,0,0.04)', display: 'flex', justifyContent: 'flex-end' }}>
              <Pagination current={page} pageSize={pageSize} total={total}
                showSizeChanger pageSizeOptions={[20, 50, 100]} size="small" showTotal={t => `共 ${t} 条`}
                onChange={(p, s) => { setPage(p); setPageSize(s) }} />
            </div>
          </Card>
        </div>
      </div>

      {/* 导入用例弹窗 */}
      <Modal
        title="导入用例"
        open={importOpen}
        onCancel={handleImportClose}
        footer={importResult ? [<Button key="ok" type="primary" onClick={handleImportClose}>完成</Button>] : null}
        width={520}
      >
        {!importResult ? (
          <>
          <div style={{ marginBottom: 12, padding: '10px 12px', background: 'rgba(0,0,0,0.02)', borderRadius: 10 }}>
            <Checkbox checked={syncDelete} onChange={e => setSyncDelete(e.target.checked)}>
              <span style={{ fontSize: 13 }}>同步删除</span>
            </Checkbox>
            <div style={{ fontSize: 12, color: '#86909c', marginTop: 4, lineHeight: 1.6 }}>
              不勾（默认）：只新增和更新，文件里没有的用例原样保留。<br />
              勾上：把<b>之前导入过、这次文件里没有</b>的用例移到回收站 —— 用于"以这个文件为准"整体覆盖。
            </div>
          </div>
          <Upload.Dragger accept=".json,.xlsx" showUploadList={false} beforeUpload={handleImportFile} disabled={importing} style={{ padding: '32px 0' }}>
            {importing ? <Spin tip="正在导入..." /> : (<>
              <p><InboxOutlined style={{ fontSize: 40, color: '#0ea5a0' }} /></p>
              <p style={{ fontSize: 14, color: '#1d2129', marginTop: 8 }}>点击或拖拽上传用例文件</p>
              <p style={{ fontSize: 12, color: '#86909c' }}>支持 .json（TEA 格式）和 .xlsx（Excel 导出格式）</p>
            </>)}
          </Upload.Dragger>
          </>
        ) : (
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {[
              { label: '新增', count: importResult.new, color: '#0ea5a0', bg: '#e0f7f6' },
              { label: '更新', count: importResult.updated, color: '#0ea5a0', bg: '#e0f7f6' },
              { label: '移除', count: importResult.removed, color: '#e8453c', bg: '#fff2f0' },
              { label: '跳过', count: importResult.skipped, color: '#86909c', bg: 'rgba(0,0,0,0.03)' },
            ].filter(s => s.label !== '移除' || syncDelete).map(s => (
              <div key={s.label} style={{ flex: 1, textAlign: 'center', padding: '16px 0', background: s.bg, borderRadius: 12 }}>
                <div style={{ fontSize: 28, fontWeight: 700, color: s.color }}>{s.count}</div>
                <div style={{ fontSize: 12, color: '#86909c' }}>{s.label}</div>
              </div>
            ))}
            {!syncDelete && importResult.notInFile > 0 && (
              <div style={{ flexBasis: '100%', fontSize: 12, color: '#86909c', lineHeight: 1.7 }}>
                另有 <b>{importResult.notInFile}</b> 条之前导入过的用例不在这个文件里，已<b>原样保留</b>
                {importResult.notInFileSample?.length
                  ? `（如「${importResult.notInFileSample.slice(0, 2).join('」「')}」）` : ''}
                。要以文件为准整体覆盖，重新导入时勾上「同步删除」。
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* 新建用例弹窗 */}
      <Modal
        title="新建用例"
        open={createCaseOpen}
        onOk={handleCreateCase}
        onCancel={() => setCreateCaseOpen(false)}
        okText="创建"
        cancelText="取消"
        confirmLoading={savingCase}
        width={520}
      >
        <Form form={createCaseForm} layout="vertical" style={{ marginTop: 12 }} initialValues={{ type: 'api', priority: 'P2' }}>
          <Form.Item name="title" label="用例标题" rules={[{ required: true, message: '请输入标题' }]}>
            <Input placeholder="如：登录成功跳转首页" maxLength={200} />
          </Form.Item>
          <div style={{ display: 'flex', gap: 16 }}>
            <Form.Item name="type" label="测试类型" rules={[{ required: true }]} style={{ flex: 1 }}>
              <Select options={[{ value: 'api', label: 'API' }, { value: 'e2e', label: 'E2E' }]} />
            </Form.Item>
            <Form.Item name="priority" label="优先级" style={{ flex: 1 }}>
              <Select options={[{ value: 'P0', label: 'P0' }, { value: 'P1', label: 'P1' }, { value: 'P2', label: 'P2' }, { value: 'P3', label: 'P3' }]} />
            </Form.Item>
          </div>
          <div>
            <Form.Item name="module" label="所属目录" rules={[{ required: true, message: '请选择目录' }]}>
              <TreeSelect
                placeholder="选择目录"
                showSearch
                treeNodeFilterProp="title"
                treeData={folderTreeSelectData}
                treeDefaultExpandAll
                style={{ width: '100%' }}
                notFoundContent={<span style={{ color: '#86909c', fontSize: 12 }}>无目录，请先在左侧导航创建</span>}
              />
            </Form.Item>
          </div>
          <div style={{ padding: '8px 12px', background: 'rgba(0,0,0,0.02)', borderRadius: 12 }}>
            <div style={{ fontSize: 12, color: '#86909c', marginBottom: 8 }}>
              这条要做到什么程度？<span style={{ color: '#c9cdd4' }}>
                （都不勾＝只要手工步骤。Claude Code 补自动化时按这个判还欠什么）
              </span>
            </div>
            <Space>
              <Form.Item name="initApi" valuePropName="checked" noStyle>
                <Checkbox>接口测试场景</Checkbox>
              </Form.Item>
              <Form.Item name="initUi" valuePropName="checked" noStyle>
                <Checkbox>UI 测试场景</Checkbox>
              </Form.Item>
            </Space>
          </div>
        </Form>
      </Modal>

      {/* 新建模块弹窗 */}
      <Modal
        title="新建模块"
        open={folderModalOpen}
        onOk={handleCreateFolder}
        onCancel={() => { setFolderModalOpen(false); folderForm.resetFields() }}
        okText="创建"
        cancelText="取消"
        confirmLoading={savingFolder}
        width={420}
      >
        <Form form={folderForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="name" label="模块名称"
            rules={[{ required: true, message: '请输入模块名称' }, { max: 50, message: '最多50个字符' }]}
          >
            <Input placeholder="如：AUTH、USER_MGMT" style={{ textTransform: 'uppercase' }} />
          </Form.Item>
          <Form.Item name="parentId" label="父模块（可选）">
            <TreeSelect
              placeholder="顶级模块（不选则为一级模块）"
              allowClear
              treeData={parentTreeSelectData}
              treeDefaultExpandAll
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 列设置弹窗 */}
      <Modal
        title="列表字段设置"
        open={columnSettingOpen}
        onCancel={() => setColumnSettingOpen(false)}
        footer={[
          <Button key="reset" onClick={() => setVisibleColumnKeys(allColumns.filter(c => c.defaultVisible).map(c => c.key))}>恢复默认</Button>,
          <Button key="ok" type="primary" onClick={() => setColumnSettingOpen(false)}>确定</Button>,
        ]}
        width={400}
      >
        <div style={{ marginTop: 12 }}>
          <p style={{ fontSize: 12, color: '#86909c', marginBottom: 12 }}>勾选需要显示的列（标题列始终显示）</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {allColumns.filter(c => !c.fixed).map(col => (
              <label key={col.key} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', padding: '4px 8px', borderRadius: 12, background: visibleColumnKeys.includes(col.key) ? '#e0f7f6' : 'transparent' }}>
                <input
                  type="checkbox"
                  checked={visibleColumnKeys.includes(col.key)}
                  onChange={e => {
                    if (e.target.checked) {
                      setVisibleColumnKeys(prev => [...prev, col.key])
                    } else {
                      setVisibleColumnKeys(prev => prev.filter(k => k !== col.key))
                    }
                  }}
                />
                <span style={{ fontSize: 13 }}>{col.title}</span>
                {col.defaultVisible && <Tag style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px', border: 'none', background: '#e0f7f6', color: '#0ea5a0' }}>默认</Tag>}
              </label>
            ))}
          </div>
        </div>
      </Modal>

      {/* AI 质量评审结果 */}
      <Modal
        title={<Space><SearchOutlined /> AI 质量评审</Space>}
        open={reviewOpen}
        onCancel={() => setReviewOpen(false)}
        width={700}
        footer={[<Button key="close" onClick={() => setReviewOpen(false)}>关闭</Button>]}
      >
        {reviewLoading && (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <LoadingOutlined style={{ fontSize: 24 }} />
            <p style={{ marginTop: 12 }}>AI 正在评审用例质量...</p>
            {reviewSteps.map((s, i) => (
              <Tag key={i} style={{ margin: 2 }}>{s.title || s.summary || s.step}</Tag>
            ))}
          </div>
        )}
        {reviewResult && (
          <div>
            <div style={{ textAlign: 'center', marginBottom: 20 }}>
              <div style={{ fontSize: 48, fontWeight: 700, color: reviewResult.score >= 75 ? '#0ea5a0' : reviewResult.score >= 60 ? '#faad14' : '#e8453c' }}>
                {reviewResult.score}
              </div>
              <Tag color={reviewResult.score >= 75 ? 'cyan' : reviewResult.score >= 60 ? 'warning' : 'error'} style={{ fontSize: 14 }}>
                {reviewResult.level}
              </Tag>
              <div style={{ marginTop: 8, color: '#86909c' }}>
                评审了 {reviewResult.caseCount} 条用例，涉及 {reviewResult.apiCount} 个 API 端点
              </div>
            </div>

            {reviewResult.report?.dimensions && (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 16 }}>
                {Object.entries(reviewResult.report.dimensions).map(([key, dim]) => (
                  <div key={key} style={{ padding: '8px 12px', background: 'transparent', borderRadius: 12, borderLeft: `3px solid ${dim.score >= 80 ? '#0ea5a0' : dim.score >= 60 ? '#faad14' : '#e8453c'}` }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{fontWeight:600}}>{{completeness:'完整性',accuracy:'准确性',effectiveness:'有效性',executability:'可执行性'}[key] || key}</span>
                      <span style={{fontWeight:600}}>{dim.score} 分 ({dim.weight}%)</span>
                    </div>
                    {dim.issues?.length > 0 && dim.issues.map((issue, i) => (
                      <div key={i} style={{ fontSize: 12, color: '#e8453c', marginTop: 2 }}>- {issue}</div>
                    ))}
                  </div>
                ))}
              </div>
            )}

            {reviewResult.report?.suggestions?.length > 0 && (
              <div style={{ padding: '8px 12px', background: '#e0f7f6', borderRadius: 12 }}>
                <span style={{fontWeight:600}}>改进建议：</span>
                {reviewResult.report.suggestions.map((s, i) => (
                  <div key={i} style={{ fontSize: 13, marginTop: 4 }}>• {s}</div>
                ))}
              </div>
            )}
          </div>
        )}
        {!reviewLoading && !reviewResult && (
          <div style={{ textAlign: 'center', padding: 40, color: '#86909c' }}>
            评审结果加载中或解析失败，请重试
          </div>
        )}
      </Modal>

      <TestForgeModal
        projectId={projectId}
        branchId={globalBranchId}
        folders={folderTree}
        open={testforgeOpen}
        onClose={() => setTestforgeOpen(false)}
        onImported={() => fetchCases()}
      />

      {/* 空目录清理：名单摆出来，勾了才删 */}
      <Modal title="清理空目录" open={!!emptyFolders} onCancel={() => setEmptyFolders(null)}
        okText={`删除选中的 ${emptyPicked.length} 个`} cancelText="取消"
        okButtonProps={{ danger: true, disabled: !emptyPicked.length }}
        onOk={doPruneFolders} width={440}>
        <div style={{ padding: '4px 0' }}>
          <div style={{ fontSize: 12, color: '#86909c', marginBottom: 10, lineHeight: 1.7 }}>
            下面这些目录没有任何用例、也没有子目录。多数是彻底删除用例后留下的空壳；
            但也可能是你先搭好的结构 —— 不想删的取消勾选。
          </div>
          <div style={{ maxHeight: 300, overflow: 'auto' }}>
            <Checkbox.Group value={emptyPicked} onChange={setEmptyPicked}
              style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(emptyFolders || []).map(f => (
                <Checkbox key={f.id} value={f.id}>
                  {f.name}
                  <span style={{ fontSize: 11, color: '#c9cdd4', marginLeft: 8 }}>
                    {f.createdAt ? new Date(f.createdAt).toLocaleDateString('zh-CN') : ''}
                  </span>
                </Checkbox>
              ))}
            </Checkbox.Group>
          </div>
        </div>
      </Modal>

      {/* 打回理由 —— 分类是必填项，后端拿它做质量归因统计 */}
      <Modal title="打回这条用例" open={!!rejectFor} onCancel={() => setRejectFor(null)}
        okText="确认打回" cancelText="取消" okButtonProps={{ danger: true }}
        onOk={rejectCase} width={380}>
        <div style={{ padding: '8px 0' }}>
          <div style={{ fontSize: 12, color: '#86909c', marginBottom: 8 }}>打回原因 *</div>
          <Radio.Group value={rejectCategory} onChange={e => setRejectCategory(e.target.value)}
            style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {REJECT_CATEGORIES.map(c => <Radio key={c.value} value={c.value}>{c.label}</Radio>)}
          </Radio.Group>
          <Input.TextArea rows={2} placeholder="补充说明（可选）" value={rejectText}
            onChange={e => setRejectText(e.target.value)} style={{ marginTop: 10 }} />
        </div>
      </Modal>

      {/* 批量执行弹窗 */}
      <Modal title="批量执行" open={batchExecOpen} onCancel={() => setBatchExecOpen(false)}
        okText="开始执行" cancelText="取消" confirmLoading={batchRunning}
        onOk={handleBatchExec} okButtonProps={{ disabled: !runEnvId || batchPrecheck.executable === 0 }}
        width={440}>
        <div style={{ padding: '8px 0' }}>
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: '#86909c', marginBottom: 6 }}>执行类型</div>
            <Radio.Group value={batchExecType} onChange={e => updatePrecheck(e.target.value)} buttonStyle="solid" size="small">
              <Radio.Button value="api">接口测试 (API)</Radio.Button>
              <Radio.Button value="ui">UI 测试 (E2E)</Radio.Button>
            </Radio.Group>
          </div>
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: '#86909c', marginBottom: 6 }}>执行环境 *</div>
            <Select value={runEnvId} onChange={setRunEnvId} placeholder="请选择环境"
              style={{ width: '100%' }} popupMatchSelectWidth={false}
              options={buildEnvOptions(environments)} />
          </div>
          <div style={{ padding: '12px 16px', background: 'rgba(0,0,0,0.02)', borderRadius: 12, fontSize: 13 }}>
            <div style={{ marginBottom: 4 }}>共选中 <b>{batchPrecheck.total}</b> 个用例</div>
            <div style={{ color: '#0ea5a0' }}>
              {batchPrecheck.executable} 个会执行<span style={{ color: '#c9cdd4' }}>（{batchExecType === 'api' ? '接口' : 'UI'}状态 = 可执行）</span>
            </div>
            {batchPrecheck.notReady > 0 && (
              <div style={{ color: '#faad14', marginTop: 2 }}>
                {batchPrecheck.notReady} 个<b>有{batchExecType === 'api' ? '接口场景' : 'UI 脚本'}但状态还不是「可执行」</b>，这次跳过
                <div style={{ fontSize: 11, color: '#86909c', marginTop: 2, lineHeight: 1.5 }}>
                  跑通一次就会自动推到「待复核」，确认没问题后在用例详情把它改成「可执行」，才会进回归。
                </div>
              </div>
            )}
            {batchPrecheck.missing > 0 && (
              <div style={{ color: '#86909c', marginTop: 2 }}>
                {batchPrecheck.missing} 个还没有{batchExecType === 'api' ? '接口场景' : 'UI 脚本'}，这次跳过
              </div>
            )}
            {batchPrecheck.executable === 0 && (
              <div style={{ color: '#e8453c', marginTop: 6 }}>这批里没有能执行的用例</div>
            )}
          </div>
        </div>
      </Modal>
    </div>
  )
}
