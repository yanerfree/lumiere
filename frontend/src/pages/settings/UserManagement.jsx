import { useState, useEffect, useCallback } from 'react'
import { timeColumn } from '../../utils/timeCol'
import { Table, Button, Tag, Modal, Form, Input, Select, Switch, message, Popconfirm, Space, Avatar, Spin } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, UserOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../../utils/request'

const ROLE_CONFIG = {
  admin: { label: '系统管理员', color: '#e8453c', bg: 'rgba(232,69,60,0.1)' },
  user: { label: '普通用户', color: '#7c5cbf', bg: 'rgba(124,92,191,0.1)' },
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
      // 「用户」这一列不写宽度：其余列都写死时 antd 会把富余宽度按比例摊给每一列，
      // 结果「创建时间」被撑到 231px 装 112px 的内容，整张表全是空白。
      // 留一列不定宽来吸收余量，其他列就能保持声明的宽度。
      title: '用户', dataIndex: 'username',
      render: (v) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Avatar size={28} style={{ background: 'rgba(124,92,191,0.12)', color: '#7c5cbf', fontSize: 12, border: '1.5px solid rgba(124,92,191,0.25)' }}>{v[0].toUpperCase()}</Avatar>
          <span style={{ fontWeight: 500 }}>{v}</span>
        </div>
      ),
    },
    {
      title: '角色', dataIndex: 'role', width: 130, align: 'center',
      render: (v) => {
        const cfg = ROLE_CONFIG[v]
        return <Tag style={{ color: cfg.color, background: cfg.bg, border: 'none' }}>{cfg.label}</Tag>
      },
    },
    {
      title: '状态', dataIndex: 'isActive', width: 100, align: 'center',
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
      title: '操作', width: 120, align: 'center',
      render: (_, record) => (
        <Space size={4}>
          <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} style={{ color: '#86909c' }} />
          {record.username !== 'admin' && (
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
