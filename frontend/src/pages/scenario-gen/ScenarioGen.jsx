import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Table, Tag, Space, Empty, Spin, message } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../../utils/request'
import WizardStepper from './components/WizardStepper'
import Stage1Input from './components/Stage1Input'
import Stage2Requirements from './components/Stage2Requirements'
import Stage3ScenarioModel from './components/Stage3ScenarioModel'
import Stage4Generation from './components/Stage4Generation'
import Stage5Review from './components/Stage5Review'

const STATUS_MAP = {
  extracting: { color: 'processing', label: '提取中' },
  model_ready: { color: 'warning', label: '待确认模型' },
  confirmed: { color: 'blue', label: '已确认' },
  generating: { color: 'processing', label: '生成中' },
  completed: { color: 'cyan', label: '已完成' },
  partial_failed: { color: 'warning', label: '部分失败' },
  failed: { color: 'error', label: '失败' },
  aborted: { color: 'default', label: '已中止' },
}

const STAGE_FROM_STATUS = {
  extracting: 'input',
  model_ready: 'requirements',
  confirmed: 'model',
  generating: 'generate',
  completed: 'review',
  partial_failed: 'generate',
  failed: 'generate',
  aborted: 'generate',
}

export default function ScenarioGen() {
  const { projectId } = useParams()
  const [searchParams] = useSearchParams()
  const taskId = searchParams.get('taskId')
  const navigate = useNavigate()

  if (taskId) {
    return <TaskDetail projectId={projectId} taskId={taskId} />
  }
  return <TaskCenter projectId={projectId} />
}

function TaskCenter({ projectId }) {
  const [tasks, setTasks] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const branchId = localStorage.getItem(`branch_${projectId}`) || ''

  const fetchTasks = useCallback(async () => {
    if (!branchId) return
    setLoading(true)
    try {
      const res = await api.get(`/projects/${projectId}/branches/${branchId}/scenario-gen/tasks?page=${page}&pageSize=20`)
      setTasks(res.data.items || [])
      setTotal(res.data.total || 0)
    } catch { /* request.js handles */ }
    finally { setLoading(false) }
  }, [projectId, branchId, page])

  useEffect(() => { fetchTasks() }, [fetchTasks])

  const columns = [
    {
      title: '任务名称', dataIndex: 'title', key: 'title',
      render: (t) => <span style={{ fontWeight: 500 }}>{t}</span>,
    },
    {
      title: '阶段', dataIndex: 'status', key: 'status',
      render: (s) => {
        const info = STATUS_MAP[s] || { color: 'default', label: s }
        return <Tag color={info.color}>{info.label}</Tag>
      },
    },
    {
      title: '进度', dataIndex: 'progress', key: 'progress',
      render: (p) => {
        if (!p) return '—'
        return `${p.succeeded || 0}/${p.total || '?'}`
      },
    },
    { title: '创建时间', dataIndex: 'createdAt', key: 'createdAt',
      render: (t) => t ? new Date(t).toLocaleString('zh-CN') : '—',
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: '#1d2129' }}>AI 生成手工测试用例</h3>
          <span style={{ fontSize: 12, color: '#8c919e' }}>从需求文档自动生成测试步骤（操作 + 预期结果），生成后进入用例管理待审核</span>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchTasks}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />}
            onClick={() => navigate(`/projects/${projectId}/scenario-gen?taskId=new`)}>
            新建生成任务
          </Button>
        </Space>
      </div>

      {!branchId ? (
        <Empty description="请先在顶部选择分支" />
      ) : (
        <Table
          dataSource={tasks}
          columns={columns}
          rowKey="id"
          loading={loading}
          pagination={{ current: page, total, pageSize: 20, onChange: setPage, showTotal: t => `共 ${t} 条` }}
          onRow={(record) => ({
            onClick: () => navigate(`/projects/${projectId}/scenario-gen?taskId=${record.id}`),
            style: { cursor: 'pointer' },
          })}
          locale={{ emptyText: (
            <Empty
              description={
                <div>
                  <p style={{ marginBottom: 8 }}>还没有生成任务</p>
                  <p style={{ fontSize: 12, color: '#8c919e' }}>
                    粘贴一份需求文档，AI 自动生成手工测试用例（操作步骤 + 预期结果），
                    审核通过后可加入测试计划执行
                  </p>
                </div>
              }
            >
              <Button type="primary" icon={<PlusOutlined />}
                onClick={() => navigate(`/projects/${projectId}/scenario-gen?taskId=new`)}>
                新建生成任务
              </Button>
            </Empty>
          )}}
        />
      )}
    </div>
  )
}

function TaskDetail({ projectId, taskId }) {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const branchId = localStorage.getItem(`branch_${projectId}`) || ''
  const stage = searchParams.get('stage') || 'input'

  const [task, setTask] = useState(null)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState(null)

  useEffect(() => {
    if (taskId === 'new' || !branchId) return
    setLoading(true)
    setLoadError(null)
    api.get(`/projects/${projectId}/branches/${branchId}/scenario-gen/tasks/${taskId}`)
      .then(res => {
        setTask(res.data)
        let autoStage = STAGE_FROM_STATUS[res.data.status] || 'input'
        if (res.data.status === 'model_ready' && res.data.hasModel) {
          autoStage = 'model'
        }
        if (!searchParams.get('stage')) {
          setSearchParams({ taskId, stage: autoStage }, { replace: true })
        }
      })
      .catch(() => {
        setLoadError('任务不存在，或它属于别的分支')
        message.error('任务不存在或加载失败')
      })
      .finally(() => setLoading(false))
  }, [taskId, branchId])

  // 下面这三种情况原来都渲染成**一片空白**：没有任何一个 stage 分支匹配，也没有提示。
  // 实测踩到的是第一种 —— 直接粘 URL 进来（新设备、清了缓存、同事转发的链接），
  // localStorage 里没有 branch_<projectId>，effect 直接 return，task 永远是 null，
  // stage 永远兜底成 'input'，而 'input' 只对 taskId==='new' 有分支。
  // 后端明明已经把需求点和场景模型都跑出来了，人看到的是白板。
  const fallback = (title, hint) => (
    <div style={{ textAlign: 'center', padding: '60px 20px', color: '#8c919e' }}>
      <div style={{ fontSize: 15, color: '#1d2129', marginBottom: 8 }}>{title}</div>
      <div style={{ fontSize: 13, marginBottom: 16 }}>{hint}</div>
      <Button onClick={() => navigate(`/projects/${projectId}/scenario-gen`)}>返回任务中心</Button>
    </div>
  )

  const handleStageChange = (newStage) => {
    setSearchParams({ taskId, stage: newStage }, { replace: true })
  }

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
        <Button type="text" onClick={() => navigate(`/projects/${projectId}/scenario-gen`)}
          style={{ color: '#8c919e' }}>
          ← 返回任务中心
        </Button>
        {task && <span style={{ color: '#1d2129', fontWeight: 500 }}>{task.title}</span>}
        {task && <Tag color={STATUS_MAP[task.status]?.color}>{STATUS_MAP[task.status]?.label}</Tag>}
      </div>

      <WizardStepper currentStage={stage} onStageClick={handleStageChange} taskStatus={task?.status} hasModel={task?.hasModel} />

      <div style={{ marginTop: 24, padding: 24, background: 'rgba(255,255,255,0.6)', borderRadius: 12,
        border: '1px solid rgba(0,0,0,0.04)', minHeight: 400 }}>
        {taskId === 'new' && stage === 'input' && (
          <Stage1Input
            projectId={projectId}
            branchId={branchId}
            onTaskCreated={(data) => {
              setTask(data)
              setSearchParams({ taskId: data.id, stage: 'requirements' }, { replace: true })
            }}
          />
        )}
        {taskId !== 'new' && stage === 'requirements' && (
          <Stage2Requirements
            projectId={projectId}
            branchId={branchId}
            taskId={taskId}
            taskStatus={task?.status}
            docContent={task?.docContent}
            healthCheck={task?.healthCheck}
            onConfirm={(data) => {
              setTask(data)
              setSearchParams({ taskId, stage: 'model' }, { replace: true })
            }}
          />
        )}
        {stage === 'model' && (
          <Stage3ScenarioModel
            projectId={projectId}
            branchId={branchId}
            taskId={taskId}
            onConfirm={(data) => {
              setTask(data)
              setSearchParams({ taskId, stage: 'generate' }, { replace: true })
            }}
          />
        )}
        {stage === 'generate' && (
          <Stage4Generation
            projectId={projectId}
            branchId={branchId}
            taskId={taskId}
            onDone={(data) => {
              setSearchParams({ taskId, stage: 'review' }, { replace: true })
            }}
          />
        )}
        {stage === 'review' && (
          <Stage5Review
            projectId={projectId}
            branchId={branchId}
            taskId={taskId}
          />
        )}

        {/* 兜底：任何一种"没有分支能渲染"的情况，都要说一句人话，不能白屏 */}
        {taskId !== 'new' && !branchId && fallback(
          '没有选中分支',
          '这条任务挂在某个分支下。回任务中心（或先在顶栏选好分支）再进来。',
        )}
        {taskId !== 'new' && branchId && loading && (
          <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
        )}
        {taskId !== 'new' && branchId && !loading && loadError && fallback('打不开这条任务', loadError)}
        {taskId !== 'new' && branchId && !loading && !loadError && task && stage === 'input' && fallback(
          '这条任务已经过了"输入需求"这一步',
          `当前状态：${STATUS_MAP[task.status]?.label || task.status}。点上面的步骤条挑一步进去。`,
        )}
      </div>
    </div>
  )
}
