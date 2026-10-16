import { useState, useEffect, useCallback } from 'react'
import { Card, Table, Button, Input, Modal, Form, Drawer, Space, Switch, Popconfirm, Empty, Typography, message } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
import { api } from '../../utils/request'
import { usePermissions } from '../../utils/PermissionContext'

// 我的记录：每人自己记点东西（标题 + 正文）。
// 可见范围由后端定：本人只拿到自己的；系统管理员拿到所有人的（多一列「作者」），
// 但别人的记录在抽屉里只读 —— 改/删只给作者本人（后端同样拦）。
// 游客是账号级只读，新建/编辑/删除按钮一律不出（后端非 GET 闸门也会 403）。

const fmt = (s) => (s ? new Date(s).toLocaleString('zh-CN', { hour12: false }) : '')

export default function MyNotes() {
  const { systemRole } = usePermissions()
  const isAdmin = systemRole === 'admin'
  const canWrite = systemRole !== 'guest'

  const [list, setList] = useState([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [mineOnly, setMineOnly] = useState(false)

  const [viewing, setViewing] = useState(null)      // 抽屉里正在看的那条
  const [editing, setEditing] = useState(null)      // null=关；{}=新建；{id,...}=编辑
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const p = new URLSearchParams()
      if (keyword.trim()) p.set('keyword', keyword.trim())
      if (mineOnly) p.set('mine_only', 'true')
      const res = await api.get(`/notes?${p.toString()}`)
      setList(res.data || [])
    } catch { /* request.js 已提示 */ } finally { setLoading(false) }
  }, [keyword, mineOnly])

  useEffect(() => { load() }, [load])

  // 弹窗 forceRender，表单常驻，每次打开直接灌值。
  // 别改成 destroyOnHidden + initialValues：useForm 的实例跨挂载复用，initialValues 只在
  // 第一次生效 —— 实测「新建一条后再点编辑」标题是空的，保存被必填拦下，看着像没反应。
  const openEditor = (note) => {
    form.resetFields()  // 清掉上次留下的校验报错
    form.setFieldsValue({ title: note?.title || '', content: note?.content || '' })
    setEditing(note || {})
  }

  const handleSave = async () => {
    let values
    try { values = await form.validateFields() } catch { return }
    setSaving(true)
    try {
      const res = editing?.id
        ? await api.put(`/notes/${editing.id}`, values)
        : await api.post('/notes', values)
      message.success('已保存')
      setEditing(null)
      // 从抽屉里点的编辑，保存后抽屉里要是新内容，不是旧的
      if (viewing && editing?.id === viewing.id) setViewing(res.data)
      load()
    } catch { /* request.js 已提示 */ } finally { setSaving(false) }
  }

  const handleDelete = async (note) => {
    try {
      await api.delete(`/notes/${note.id}`)
      message.success('已删除')
      setViewing(null)
      load()
    } catch { /* request.js 已提示 */ }
  }

  const columns = [
    {
      title: '标题', dataIndex: 'title', ellipsis: true,
      render: (v) => <a data-testid="note-title">{v}</a>,
    },
    ...(isAdmin ? [{ title: '作者', dataIndex: 'username', width: 140 }] : []),
    { title: '更新时间', dataIndex: 'updatedAt', width: 180, render: fmt },
  ]

  return (
    <Card
      title="我的记录"
      extra={
        <Space>
          {isAdmin && (
            <Space size={6}>
              <span style={{ color: '#8c919e', fontSize: 13 }}>只看我的</span>
              <Switch size="small" checked={mineOnly} onChange={setMineOnly} data-testid="notes-mine-only" />
            </Space>
          )}
          <Input.Search
            allowClear placeholder="搜标题或内容" style={{ width: 220 }}
            onSearch={setKeyword} data-testid="notes-search"
          />
          {canWrite && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor(null)} data-testid="notes-create">
              新建记录
            </Button>
          )}
        </Space>
      }
    >
      <Table
        rowKey="id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={list}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        onRow={(r) => ({ onClick: () => setViewing(r), style: { cursor: 'pointer' } })}
        locale={{ emptyText: <Empty description={canWrite ? '还没有记录，点右上角「新建记录」写一条' : '还没有记录'} /> }}
      />

      <Drawer
        title={viewing?.title}
        open={!!viewing}
        onClose={() => setViewing(null)}
        width={560}
        extra={viewing?.mine && canWrite && (
          <Space>
            <Button icon={<EditOutlined />} onClick={() => openEditor(viewing)} data-testid="note-edit">编辑</Button>
            <Popconfirm title="删除这条记录？删了找不回来" okText="删除" cancelText="取消"
              okButtonProps={{ danger: true }} onConfirm={() => handleDelete(viewing)}>
              <Button danger icon={<DeleteOutlined />} data-testid="note-delete">删除</Button>
            </Popconfirm>
          </Space>
        )}
      >
        {viewing && (
          <>
            <div style={{ color: '#8c919e', fontSize: 12, marginBottom: 16 }}>
              <Space size={16} wrap>
                {!viewing.mine && <span>作者：{viewing.username}</span>}
                <span>创建于 {fmt(viewing.createdAt)}</span>
                <span>更新于 {fmt(viewing.updatedAt)}</span>
              </Space>
            </div>
            {viewing.content
              ? <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', fontSize: 14 }} data-testid="note-content">
                  {viewing.content}
                </Typography.Paragraph>
              : <span style={{ color: '#c9cdd4' }}>（没有正文）</span>}
          </>
        )}
      </Drawer>

      <Modal
        title={editing?.id ? '编辑记录' : '新建记录'}
        open={!!editing}
        onCancel={() => setEditing(null)}
        onOk={handleSave}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        width={640}
        forceRender
        // 常驻挂载的弹窗比抽屉先进 DOM，同层级时会被抽屉盖住（实测从抽屉点「编辑」弹窗在底下点不到）
        zIndex={1100}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="title" label="标题"
            rules={[{ required: true, whitespace: true, message: '请填写标题' }, { max: 200, message: '标题最多 200 字' }]}>
            <Input placeholder="一句话说清记的是什么" maxLength={200} data-testid="note-form-title" />
          </Form.Item>
          <Form.Item name="content" label="内容">
            <Input.TextArea placeholder="想记什么都写这儿" autoSize={{ minRows: 8, maxRows: 20 }} data-testid="note-form-content" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
