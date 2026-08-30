import { useState, useEffect, useCallback } from 'react'
import { timeColumn } from '../../utils/timeCol'
import { Table, Button, Tag, Modal, Form, Input, Select, Switch, message, Popconfirm, Space, Avatar, Tooltip } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, UserOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../../utils/request'
import { nameColor, avatarText } from '../../utils/nameColor'

const ROLE_CONFIG = {
  admin: { label: '系统管理员', color: '#e8453c', bg: 'rgba(232,69,60,0.1)' },
  user: { label: '普通用户', color: '#7c5cbf', bg: 'rgba(124,92,191,0.1)' },
  guest: { label: '游客', color: '#86909c', bg: 'rgba(134,144,156,0.12)' },
}

// 未知角色的兜底。**必须常驻，不是为了这一次**：这里此前没有兜底，
// 角色列直接取 ROLE_CONFIG[v].color —— 库里一旦出现表里没有的角色值，
// 整个用户管理页白屏（不是那一行坏，是 render 抛错、整张表都渲染不出来）。
// 2026-08-29 就是这么被引爆的：探针账号在库里留了一行 operator，
// 而 ROLE_CONFIG 只有 admin/user。角色值会随迁移、随版本回退、随手工改库变化，
// 前端不该假设自己永远认得全。
const roleCfg = (v) => ROLE_CONFIG[v] ?? { label: v || '未知', color: '#86909c', bg: 'rgba(134,144,156,0.12)' }

// 项目角色的中文名，跟「项目列表 → 成员管理」那份下拉同源（pages/projects/ProjectList.jsx）。
// 库里可能还留着旧名（project_admin/developer/tester），后端出门前已归一成
// manager/member（user_service.list_user_project_map），这里的兜底是防"归一表又漏了一个"。
const PROJECT_ROLE_LABEL = { manager: '项目管理员', member: '成员' }
const projectRoleLabel = (v) => PROJECT_ROLE_LABEL[v] ?? `${v || '未知'}（旧角色）`

// 一行最多平铺几个项目，多的收进 +N。3 个之后再往右排就开始挤「角色」列了。
const MAX_PROJECT_TAGS = 3

function ProjectChip({ project }) {
  const c = nameColor(project.name)
  return (
    <Tooltip title={`项目角色：${projectRoleLabel(project.role)}`} mouseEnterDelay={0.3}>
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        padding: '1px 8px 1px 6px', borderRadius: 6, fontSize: 12, lineHeight: '18px',
        background: 'rgba(255,255,255,0.55)', border: '1px solid rgba(0,0,0,0.06)',
        color: '#4e5969', maxWidth: 168,
      }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: c.color, flexShrink: 0 }} />
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{project.name}</span>
      </span>
    </Tooltip>
  )
}

/**
 * 「归属项目」列。
 *
 * 这里显示的是**成员表里真有的那几行**，不是「他能进哪些项目」——
 * 系统管理员绕过项目成员绑定（backend/app/deps/auth.py 的 require_project_role），
 * 所以 admin 哪怕一行成员记录都没有，照样能进全部项目。那种情况下把这一格
 * 留空或者画成「—」是**说反了**：看着像"这个管理员什么都看不到"。
 * 所以 admin 单独先给一枚「全部项目」，成员记录（如果有）再跟在后面。
 */
function ProjectCell({ user }) {
  const projects = user.projects || []
  const isAdmin = user.role === 'admin'
  const shown = projects.slice(0, MAX_PROJECT_TAGS)
  const rest = projects.slice(MAX_PROJECT_TAGS)

  if (!isAdmin && projects.length === 0) {
    // 不写「—」：普通用户没有任何项目绑定 = 他登进来什么也看不见，
    // 这是个需要有人处理的状态，不是"这格没数据"。
    return <span style={{ color: '#c9cdd4', fontSize: 12 }}>未加入项目</span>
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
      {isAdmin && (
        <Tooltip title="系统管理员绕过项目成员绑定，无需加入即可访问全部项目" mouseEnterDelay={0.3}>
          <span style={{
            padding: '1px 8px', borderRadius: 6, fontSize: 12, lineHeight: '18px',
            color: '#0ea5a0', background: 'rgba(14,165,160,0.1)',
            border: '1px solid rgba(14,165,160,0.2)',
          }}>全部项目</span>
        </Tooltip>
      )}
      {shown.map(p => <ProjectChip key={p.id} project={p} />)}
      {rest.length > 0 && (
        <Tooltip title={rest.map(p => `${p.name}（${projectRoleLabel(p.role)}）`).join('、')}>
          <span style={{ fontSize: 12, color: '#86909c', cursor: 'default' }}>+{rest.length}</span>
        </Tooltip>
      )}
    </div>
  )
}

export default function UserManagement() {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingUser, setEditingUser] = useState(null)
  const [form] = Form.useForm()

  const fetchUsers = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/users')
      setUsers(res.data)
    } catch { /* request.js 已展示错误 */ } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchUsers() }, [fetchUsers])

  const openCreate = () => {
    setEditingUser(null)
    form.resetFields()
    setModalOpen(true)
  }

  const openEdit = (user) => {
    setEditingUser(user)
    form.setFieldsValue({ username: user.username, role: user.role, isActive: user.isActive })
    setModalOpen(true)
  }

  const handleSave = async () => {
    let values
    try { values = await form.validateFields() } catch { return }

    setSaving(true)
    try {
      if (editingUser) {
        const payload = { role: values.role, isActive: values.isActive }
        // 留空表示不改密码，别把空串发上去
        if (values.password) payload.password = values.password
        await api.put(`/users/${editingUser.id}`, payload)
        message.success(values.password ? '用户已更新，密码已重置' : '用户已更新')
      } else {
        await api.post('/users', {
          username: values.username,
          password: values.password,
          role: values.role,
        })
        message.success('用户创建成功')
      }
      setModalOpen(false)
      form.resetFields()
      fetchUsers()
    } catch { /* request.js 已展示错误 */ } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (user) => {
    try {
      await api.del(`/users/${user.id}`)
      message.success('用户已删除')
      fetchUsers()
    } catch { /* request.js 已展示错误 */ }
  }

  const toggleActive = async (user) => {
    try {
      await api.put(`/users/${user.id}`, { isActive: !user.isActive })
      message.success(user.isActive ? '已停用' : '已启用')
      fetchUsers()
    } catch { /* request.js 已展示错误 */ }
  }

  const columns = [
    {
      title: '用户', dataIndex: 'username', width: 240,
      render: (v) => {
        const c = nameColor(v)
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <Avatar size={28} style={{ background: c.bg, color: c.color, fontSize: 12, fontWeight: 600, border: `1.5px solid ${c.border}`, flexShrink: 0 }}>{avatarText(v)}</Avatar>
            {/* 用户名最长 50 字符（schemas/user.py），220px 装不下 ——
                不截断的话这一格会撑成两三行，把整行的高度顶起来。 */}
            <Tooltip title={v} mouseEnterDelay={0.5}>
              <span style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v}</span>
            </Tooltip>
          </div>
        )
      },
    },
    {
      // 「归属项目」这一列不写宽度：其余列都写死时 antd 会把富余宽度按比例摊给每一列，
      // 结果「创建时间」被撑到 231px 装 112px 的内容，整张表全是空白。
      // 留一列不定宽来吸收余量，其他列就能保持声明的宽度 —— 而这一列本来就是变长内容，
      // 它来吃这份余量最合适（此前吃余量的是「用户」，那一列内容短，撑出来全是空白）。
      title: '归属项目',
      key: 'projects',
      render: (_, record) => <ProjectCell user={record} />,
    },
    {
      title: '角色', dataIndex: 'role', width: 120, align: 'center',
      render: (v) => {
        const cfg = roleCfg(v)
        return <Tag style={{ color: cfg.color, background: cfg.bg, border: 'none' }}>{cfg.label}</Tag>
      },
    },
    {
      title: '状态', dataIndex: 'isActive', width: 88, align: 'center',
      render: (v, record) => (
        <Switch
          size="small"
          checked={v}
          onChange={() => toggleActive(record)}
          checkedChildren="启用"
          unCheckedChildren="停用"
        />
      ),
    },
    timeColumn({ key: 'createdAt', title: '创建时间', align: 'center' }),
    {
      title: '操作', width: 96, align: 'center',
      render: (_, record) => (
        <Space size={4}>
          <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} style={{ color: '#86909c' }} />
          {/* 保护的是「系统管理员」这个角色，不是叫 admin 的那个人 ——
              按用户名判的话，把 admin 改名、或另建一个管理员账号，保护就失效了，
              而且失效得静默（按钮照常出现，删完才发现没人能管理系统了）。 */}
          {record.role !== 'admin' && (
            <Popconfirm
              title={`确定删除用户 ${record.username}？`}
              description={record.isActive ? '该用户当前处于启用状态' : undefined}
              onConfirm={() => handleDelete(record)}
            >
              <Button type="text" size="small" icon={<DeleteOutlined />} style={{ color: '#e8453c' }} />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: '#1d2129' }}>用户管理</h2>
          <span style={{ fontSize: 13, color: '#86909c' }}>管理系统用户账号与角色</span>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchUsers} loading={loading}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建用户</Button>
        </Space>
      </div>

      <div style={{ borderRadius: 14, padding: 2 }}>
        <Table
          dataSource={users}
          columns={columns}
          rowKey="id"
          size="small"
          loading={loading}
          pagination={{
            defaultPageSize: 20,
            size: 'small',
            showTotal: t => `共 ${t} 位用户`,
            // showSizeChanger 不显式开的话，antd 只在总数 > 50 时才给「每页几条」，
            // 十几个用户时永远看不到 —— 而恰恰是这种时候 admin/liyan 被挤到第 2 页找不着人。
            showSizeChanger: true,
            // 20 得留在选项里，否则从别的档位切回默认值就没路可走了
            pageSizeOptions: [10, 20, 50, 100, 500],
            showQuickJumper: true,
          }}
        />
      </div>

      <Modal
        title={editingUser ? '编辑用户' : '新建用户'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => { setModalOpen(false); form.resetFields() }}
        okText={editingUser ? '保存' : '创建'}
        cancelText="取消"
        confirmLoading={saving}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            name="username" label="用户名"
            rules={[
              { required: true, message: '请输入用户名' },
              { min: 2, message: '用户名至少 2 个字符' },
              { max: 50, message: '用户名最多 50 个字符' },
              { pattern: /^[a-zA-Z0-9_]+$/, message: '只允许字母、数字和下划线' },
            ]}
          >
            <Input
              prefix={<UserOutlined style={{ color: '#c9cdd4' }} />}
              placeholder="字母、数字、下划线，2-50 位"
              disabled={!!editingUser}
            />
          </Form.Item>
          {!editingUser && (
            <Form.Item
              name="password" label="密码"
              rules={[
                { required: true, message: '请输入密码' },
                { min: 6, message: '密码至少 6 个字符' },
                { max: 128, message: '密码最多 128 个字符' },
              ]}
            >
              <Input.Password placeholder="至少 6 位" />
            </Form.Item>
          )}
          {editingUser && (
            <Form.Item
              name="password" label="重置密码"
              extra="留空则不改动。重置后该用户所有已登录的地方都会被强制重新登录。"
              rules={[
                { min: 6, message: '密码至少 6 个字符' },
                { max: 128, message: '密码最多 128 个字符' },
              ]}
            >
              <Input.Password placeholder="不填就不改" autoComplete="new-password" />
            </Form.Item>
          )}
          <Form.Item
            name="role" label="系统角色"
            rules={[{ required: true, message: '请选择角色' }]}
            initialValue="user"
          >
            <Select options={[
              { value: 'admin', label: '系统管理员 — 可访问所有项目和系统配置' },
              { value: 'user', label: '普通用户 — 需通过项目成员绑定获得访问权限' },
              { value: 'guest', label: '游客 — 硬封顶只读，加进项目也只能看，不能改' },
            ]} />
          </Form.Item>
          {editingUser && (
            <Form.Item name="isActive" label="账号状态" valuePropName="checked">
              <Switch checkedChildren="启用" unCheckedChildren="停用" />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  )
}
