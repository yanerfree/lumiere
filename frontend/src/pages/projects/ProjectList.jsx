import { useState, useEffect, useCallback, useMemo } from 'react'
import { Card, Row, Col, Button, Tag, Modal, Form, Input, Select, Space, message, Spin, Empty, Popconfirm, Pagination, Table, Avatar } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, RightOutlined, GitlabOutlined, FolderOpenOutlined, ReloadOutlined, TeamOutlined, UserOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { api } from '../../utils/request'

const PROJECT_ROLES = [
  { value: 'project_admin', label: '项目管理员' },
  { value: 'developer', label: '开发' },
  { value: 'tester', label: '测试' },
  { value: 'guest', label: '访客' },
]

const ROLE_TAG = {
  project_admin: { color: '#e8453c', bg: 'rgba(232,69,60,0.1)' },
  developer: { color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  tester: { color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)' },
  guest: { color: '#86909c', bg: 'rgba(0,0,0,0.02)' },
}

// ---- 成员管理弹窗 ----
function MemberModal({ project, open, onClose }) {
  const [members, setMembers] = useState([])
  const [allUsers, setAllUsers] = useState([])
  const [loading, setLoading] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [addForm] = Form.useForm()
  const [saving, setSaving] = useState(false)

  const fetchMembers = useCallback(async () => {
    if (!project) return
    setLoading(true)
    try {
      const res = await api.get(`/projects/${project.id}/members`)
      setMembers(res.data)
    } catch { /* */ } finally { setLoading(false) }
  }, [project])

  const fetchUsers = useCallback(async () => {
    try {
      const res = await api.get('/users')
      setAllUsers(res.data)
    } catch { /* 非 admin 可能 403，忽略 */ }
  }, [])

  useEffect(() => {
    if (open) { fetchMembers(); fetchUsers() }
  }, [open, fetchMembers, fetchUsers])

  // 可添加的用户 = 全部用户 - 已是成员的
  const addableUsers = useMemo(() => {
    const memberIds = new Set(members.map(m => m.userId))
    return allUsers.filter(u => !memberIds.has(u.id))
  }, [allUsers, members])

  const handleAdd = async () => {
    let values
    try { values = await addForm.validateFields() } catch { return }
    setSaving(true)
    try {
      await api.post(`/projects/${project.id}/members`, { userId: values.userId, role: values.role })
      message.success('成员添加成功')
      setAddOpen(false)
      addForm.resetFields()
      fetchMembers()
    } catch { /* */ } finally { setSaving(false) }
  }

  const handleRoleChange = async (member, newRole) => {
    try {
      await api.put(`/projects/${project.id}/members/${member.userId}`, { role: newRole })
      message.success('角色已更新')
      fetchMembers()
    } catch { /* */ }
  }

  const handleRemove = async (member) => {
    try {
      await api.del(`/projects/${project.id}/members/${member.userId}`)
      message.success('成员已移除')
      fetchMembers()
    } catch { /* */ }
  }

  const columns = [
    {
      title: '用户', dataIndex: 'username', width: 160,
      render: v => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Avatar size={24} style={{ background: 'rgba(124,92,191,0.12)', color: '#7c5cbf', fontSize: 11, border: '1.5px solid rgba(124,92,191,0.25)' }}>{v?.[0]?.toUpperCase()}</Avatar>
          <span style={{ fontWeight: 500 }}>{v}</span>
        </div>
      ),
    },
    {
      title: '角色', dataIndex: 'role', width: 160,
      render: (v, record) => (
        <Select
          value={v}
          size="small"
          style={{ width: 130 }}
          options={PROJECT_ROLES}
          onChange={(newRole) => handleRoleChange(record, newRole)}
        />
      ),
    },
    {
      title: '加入时间', dataIndex: 'joinedAt', width: 160,
      render: v => <span style={{ fontSize: 13, color: '#86909c' }}>{v ? new Date(v).toLocaleString('zh-CN') : '-'}</span>,
    },
    {
      title: '操作', width: 80, align: 'center',
      render: (_, record) => (
        <Popconfirm title={`确定移除 ${record.username}？`} onConfirm={() => handleRemove(record)}>
          <Button type="text" size="small" icon={<DeleteOutlined />} style={{ color: '#e8453c' }} />
        </Popconfirm>
      ),
    },
  ]

  return (
    <Modal
      title={`成员管理 — ${project?.name || ''}`}
      open={open}
      onCancel={onClose}
      footer={null}
      width={640}
    >
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => { addForm.resetFields(); setAddOpen(true) }}>
          添加成员
        </Button>
      </div>
      <Table
        dataSource={members}
        columns={columns}
        rowKey="id"
        size="small"
        loading={loading}
        pagination={false}
        locale={{ emptyText: '暂无成员' }}
      />

      {/* 添加成员子弹窗 */}
      <Modal
        title="添加成员"
        open={addOpen}
        onOk={handleAdd}
        onCancel={() => setAddOpen(false)}
        okText="添加"
        cancelText="取消"
        confirmLoading={saving}
        width={420}
      >
        <Form form={addForm} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="userId" label="选择用户" rules={[{ required: true, message: '请选择用户' }]}>
            <Select
              placeholder="搜索用户名"
              showSearch
              optionFilterProp="label"
              options={addableUsers.map(u => ({ value: u.id, label: u.username }))}
            />
          </Form.Item>
          <Form.Item name="role" label="项目角色" rules={[{ required: true, message: '请选择角色' }]} initialValue="tester">
            <Select options={PROJECT_ROLES} />
          </Form.Item>
        </Form>
      </Modal>
    </Modal>
  )
}

// ---- 主页面 ----
export default function ProjectList() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [modalOpen, setModalOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editingProject, setEditingProject] = useState(null)
  const [form] = Form.useForm()

  const [page, setPage] = useState(1)
  const pageSize = 8

  // 成员管理弹窗
  const [memberProject, setMemberProject] = useState(null)
  const [memberOpen, setMemberOpen] = useState(false)

  const user = JSON.parse(localStorage.getItem('user') || '{}')
  const isAdmin = user.role === 'admin'

  const pagedProjects = useMemo(() => {
    const start = (page - 1) * pageSize
    return projects.slice(start, start + pageSize)
  }, [projects, page])

  const fetchProjects = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/projects')
      setProjects(res.data)
    } catch { /* request.js 已展示错误 */ } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchProjects() }, [fetchProjects])

  const openCreate = () => {
    setEditingProject(null)
    form.resetFields()
    setModalOpen(true)
  }

  const openEdit = (e, project) => {
    e.stopPropagation()
    setEditingProject(project)
    form.setFieldsValue({
      name: project.name,
      description: project.description,
      gitUrl: project.gitUrl,
      scriptBasePath: project.scriptBasePath,
    })
    setModalOpen(true)
  }

  const openMembers = (e, project) => {
    e.stopPropagation()
    setMemberProject(project)
    setMemberOpen(true)
  }

  const handleSave = async () => {
    let values
    try { values = await form.validateFields() } catch { return }

    setSaving(true)
    try {
      if (editingProject) {
        await api.put(`/projects/${editingProject.id}`, {
          description: values.description || null,
          gitUrl: values.gitUrl,
          scriptBasePath: values.scriptBasePath,
        })
        message.success('项目已更新')
      } else {
        await api.post('/projects', {
          name: values.name,
          description: values.description || null,
          gitUrl: values.gitUrl,
          scriptBasePath: values.scriptBasePath,
        })
        message.success('项目创建成功，已自动创建默认分支配置（main）')
      }
      setModalOpen(false)
      form.resetFields()
      fetchProjects()
    } catch { /* request.js 已展示错误 */ } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (e, project) => {
    e.stopPropagation()
    try {
      await api.del(`/projects/${project.id}`)
      message.success('项目已删除')
      fetchProjects()
    } catch { /* request.js 已展示错误 */ }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ fontSize: 20, fontWeight: 600, color: '#1d2129' }}>项目列表</h2>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchProjects} loading={loading}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>创建项目</Button>
        </Space>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}><Spin /></div>
      ) : projects.length === 0 ? (
        <Empty description="暂无项目" style={{ marginTop: 80 }}>
          <Button type="primary" onClick={openCreate}>创建第一个项目</Button>
        </Empty>
      ) : (
        <>
        <Row gutter={[12, 12]}>
          {pagedProjects.map((p, idx) => {
            // 项目图标不做实色渐变块：淡底 + 细边框 + 彩色图标。
            // 色值全部取自全站色板（青碧 / 蓝 / 紫 / 橙 / 浅青 / 红），
            // 原来那套 #f0a0c0 粉、#f5b971 杏、#a78bda 淡紫是这一页自己的颜色。
            const CARD_COLORS = [
              { fg: '#0ea5a0', bg: 'rgba(14,165,160,0.12)', border: 'rgba(14,165,160,0.28)' },
              { fg: '#4e8af0', bg: 'rgba(78,138,240,0.12)', border: 'rgba(78,138,240,0.28)' },
              { fg: '#7c5cbf', bg: 'rgba(124,92,191,0.12)', border: 'rgba(124,92,191,0.28)' },
              { fg: '#ff7d00', bg: 'rgba(255,125,0,0.12)', border: 'rgba(255,125,0,0.28)' },
              { fg: '#2ec4b6', bg: 'rgba(46,196,182,0.12)', border: 'rgba(46,196,182,0.28)' },
              { fg: '#e8453c', bg: 'rgba(232,69,60,0.12)', border: 'rgba(232,69,60,0.28)' },
            ]
            const cc = CARD_COLORS[idx % CARD_COLORS.length]
            return (
            <Col span={6} key={p.id}>
              <Card
                hoverable
                onClick={() => navigate(`/projects/${p.id}/cases`)}
                style={{ height: '100%' }}
                styles={{ body: { padding: 20 } }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                  <div style={{
                    width: 36, height: 36, borderRadius: 18,
                    background: cc.bg, border: `1px solid ${cc.border}`,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 16,
                  }}>
                    <FolderOpenOutlined style={{ color: cc.fg }} />
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 15, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</div>
                    <div style={{ fontSize: 12, color: '#86909c', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.description || '暂无描述'}</div>
                  </div>
                </div>

                <div style={{
                  margin: '12px 0', padding: '10px 0',
                  borderTop: '1px solid rgba(0,0,0,0.04)', borderBottom: '1px solid rgba(0,0,0,0.04)',
                  fontSize: 12, color: '#86909c',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                    <GitlabOutlined />
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.gitUrl || '未配置 Git 仓库'}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <FolderOpenOutlined />
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.scriptBasePath || '未配置脚本路径'}</span>
                  </div>
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 11, color: '#c9cdd4' }}>
                  <span>创建于 {new Date(p.createdAt).toLocaleDateString('zh-CN')}</span>
                  <RightOutlined style={{ fontSize: 12 }} />
                </div>

                <div style={{ marginTop: 10, display: 'flex', gap: 8 }}>
                  <Button size="small" type="text" icon={<TeamOutlined />} onClick={(e) => openMembers(e, p)}>成员</Button>
                  <Button size="small" type="text" icon={<EditOutlined />} onClick={(e) => openEdit(e, p)}>编辑</Button>
                  <Popconfirm
                    title={`确定删除项目「${p.name}」？`}
                    // 文案必须写清「会删什么」+「什么情况下删不动」：外键改成全 CASCADE 后
                    // 一次点击是真的会把接口场景、计划、报告一起物理删掉；而有用例/知识条目/
                    // 需求文档的项目后端会直接 409 挡回来（PROJECT_NOT_EMPTY）
                    description={<span style={{ display: 'inline-block', maxWidth: 260 }}>
                      分支、成员、接口场景、计划和报告将一并永久删除，无法恢复。
                      <br />
                      项目下若还有用例、知识条目或需求文档，需先清空或转移才能删除。
                    </span>}
                    onConfirm={(e) => handleDelete(e, p)}
                    onCancel={(e) => e.stopPropagation()}
                  >
                    <Button size="small" type="text" icon={<DeleteOutlined />} onClick={(e) => e.stopPropagation()} style={{ color: '#e8453c' }}>删除</Button>
                  </Popconfirm>
                </div>
              </Card>
            </Col>
          )})}
        </Row>
        {projects.length > pageSize && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
            <Pagination
              current={page}
              total={projects.length}
              pageSize={pageSize}
              onChange={setPage}
              size="small"
              showTotal={t => `共 ${t} 个项目`}
            />
          </div>
        )}
        </>
      )}

      {/* 创建/编辑项目弹窗 */}
      <Modal
        title={editingProject ? '编辑项目' : '创建项目'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => { setModalOpen(false); form.resetFields() }}
        okText={editingProject ? '保存' : '创建'}
        cancelText="取消"
        confirmLoading={saving}
        width={520}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            name="name" label="项目名称"
            rules={[{ required: true, message: '请输入项目名称' }]}
          >
            <Input placeholder="如：API网关管理系统" disabled={!!editingProject} />
          </Form.Item>
          <Form.Item name="description" label="项目描述">
            <Input placeholder="简要描述项目用途" />
          </Form.Item>
          <Form.Item name="gitUrl" label="Git 仓库地址（可选）">
            <Input prefix={<GitlabOutlined style={{ color: '#c9cdd4' }} />} placeholder="git@code.example.com:team/repo.git（不填则为纯手动用例项目）" />
          </Form.Item>
          <Form.Item name="scriptBasePath" label="脚本基础路径（可选）">
            <Input prefix={<FolderOpenOutlined style={{ color: '#c9cdd4' }} />} placeholder="/workspace/repos/project-name（不填则不支持脚本同步）" />
          </Form.Item>
          {!editingProject && (
            <div style={{ padding: '8px 12px', background: 'var(--green-bg)', borderRadius: 12, fontSize: 12, color: '#0ea5a0' }}>
              创建后系统将自动生成默认分支配置（名称: default，分支: main）
            </div>
          )}
        </Form>
      </Modal>

      {/* 成员管理弹窗 */}
      <MemberModal
        project={memberProject}
        open={memberOpen}
        onClose={() => setMemberOpen(false)}
      />
    </div>
  )
}
