import { useState, useEffect, useCallback } from 'react'
import { Card, Table, Button, Input, Modal, Form, Drawer, Space, Switch, Popconfirm, Empty, Typography, Radio, Tag, Checkbox, Tooltip, message } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, StarFilled, StarOutlined, HolderOutlined } from '@ant-design/icons'
import './MyNotes.css'
import { api } from '../../utils/request'
import { usePermissions } from '../../utils/PermissionContext'

// 我的记录：每人自己记点东西（标题 + 正文）。
// 可见范围由后端定：本人只拿到自己的；系统管理员拿到所有人的（多一列「作者」），
// 但别人的记录在抽屉里只读 —— 改/删只给作者本人（后端同样拦）。
// 游客是账号级只读，新建/编辑/删除按钮一律不出（后端非 GET 闸门也会 403）。

const KIND_LABEL = { todo: '待办', note: '笔记' }

const fmt = (s) => (s ? new Date(s).toLocaleString('zh-CN', { hour12: false }) : '')

export default function MyNotes() {
  const { systemRole } = usePermissions()
  const isAdmin = systemRole === 'admin'
  const canWrite = systemRole !== 'guest'

  const [list, setList] = useState([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [mineOnly, setMineOnly] = useState(false)
  const [filters, setFilters] = useState({})        // 列头筛选（类型 / 状态），前端过滤
  const [overId, setOverId] = useState(null)
  // 「作者」列默认藏起来（大多数时候看的是自己的），要看的人自己打开，记在本机浏览器里
  const [showAuthor, setShowAuthor] = useState(() => localStorage.getItem('notes.showAuthor') === '1')
  const toggleAuthor = (v) => { setShowAuthor(v); localStorage.setItem('notes.showAuthor', v ? '1' : '0') }
  const [dragId, setDragId] = useState(null)

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
    form.setFieldsValue({ title: note?.title || '', content: note?.content || '', kind: note?.kind || 'note', status: note?.status || 'pending' })
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

  // 列表上直接点收藏 / 勾完成：只改一个字段，改完整表重拉（顺序由后端定）
  const patch = async (note, body) => {
    try {
      const res = await api.patch(`/notes/${note.id}`, body)
      if (viewing?.id === note.id) setViewing(res.data)
      load()
    } catch { /* request.js 已提示 */ }
  }

  // 拖拽排序只在「看的是自己完整列表」时开：搜索/筛类型/管理员看全部时，
  // 列表只是一部分，拖出来的顺位没法落到完整列表上。
  const canDrag = canWrite && !keyword.trim() && !Object.values(filters).some((v) => v?.length) && (!isAdmin || mineOnly)

  const handleDrop = async (target) => {
    const from = list.find((n) => n.id === dragId)
    setDragId(null); setOverId(null)
    if (!from || from.id === target.id) return
    // 收藏组和普通组各排各的，收藏永远在最前，不跨组
    if (from.isFavorite !== target.isFavorite) { message.info('收藏的和没收藏的不能互相拖，想置顶请点星标'); return }
    const next = list.filter((n) => n.id !== from.id)
    next.splice(next.findIndex((n) => n.id === target.id) + (list.indexOf(from) < list.indexOf(target) ? 1 : 0), 0, from)
    setList(next)  // 先乐观更新，别等接口
    try { await api.post('/notes/reorder', { ids: next.filter((n) => n.mine).map((n) => n.id) }) }
    catch { load() }
  }

  const columns = [
    ...(canDrag ? [{
      title: '', width: 36, align: 'center',
      render: () => <Tooltip title="按住拖动排序"><HolderOutlined style={{ color: '#c9cdd4', cursor: 'grab' }} /></Tooltip>,
    }] : []),
    {
      title: '', width: 44, align: 'center',
      render: (_, r) => {
        const star = r.isFavorite
          ? <StarFilled style={{ color: '#f5a623', fontSize: 16 }} />
          : <StarOutlined style={{ color: '#c9cdd4', fontSize: 16 }} />
        return r.mine && canWrite
          ? <span className="note-fav" data-testid="note-fav" data-fav={r.isFavorite ? '1' : '0'} style={{ cursor: 'pointer' }}
              onClick={(e) => { e.stopPropagation(); patch(r, { isFavorite: !r.isFavorite }) }}>{star}</span>
          : (r.isFavorite ? star : null)
      },
    },
    {
      title: '类型', dataIndex: 'kind', width: 100,
      filters: [{ text: '待办', value: 'todo' }, { text: '笔记', value: 'note' }],
      filteredValue: filters.kind || null,
      onFilter: (v, r) => r.kind === v,
      render: (v) => <span className={`note-pill ${v}`} data-testid="note-kind"><i className="dot" />{KIND_LABEL[v]}</span>,
    },
    {
      title: '标题', dataIndex: 'title', ellipsis: true,
      render: (v, r) => (
        <Space size={8}>
          {r.kind === 'todo' && (
            <Checkbox checked={r.status === 'done'} disabled={!r.mine || !canWrite} data-testid="note-done-check"
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => patch(r, { status: e.target.checked ? 'done' : 'pending' })} />
          )}
          <a data-testid="note-title" className={`note-title ${r.kind === 'todo' && r.status === 'done' ? 'done' : ''}`}>{v}</a>
        </Space>
      ),
    },
    {
      title: '状态', dataIndex: 'status', width: 110,
      filters: [{ text: '待完成', value: 'pending' }, { text: '已完成', value: 'done' }],
      filteredValue: filters.status || null,
      onFilter: (v, r) => r.kind === 'todo' && r.status === v,
      render: (v, r) => r.kind === 'todo'
        ? (r.mine && canWrite
            ? <Tooltip title={v === 'done' ? '点一下改回待完成' : '点一下标记已完成'}>
                <span className={`note-pill ${v} clickable`} data-testid="note-status"
                  onClick={(e) => { e.stopPropagation(); patch(r, { status: v === 'done' ? 'pending' : 'done' }) }}>
                  <i className="dot" />{v === 'done' ? '已完成' : '待完成'}
                </span>
              </Tooltip>
            : <span className={`note-pill ${v}`} data-testid="note-status"><i className="dot" />{v === 'done' ? '已完成' : '待完成'}</span>)
        : <span style={{ color: '#c9cdd4' }}>—</span>,
    },
    ...(isAdmin && showAuthor ? [{ title: '作者', dataIndex: 'username', width: 120 }] : []),
    { title: '创建时间', dataIndex: 'createdAt', width: 170, render: fmt },
    {
      title: '完成时间', dataIndex: 'completedAt', width: 170,
      render: (v, r) => (r.kind === 'todo' && r.status === 'done' && v)
        ? <span style={{ color: '#16a34a' }} data-testid="note-completed-at">{fmt(v)}</span>
        : <span style={{ color: '#c9cdd4' }}>—</span>,
    },
  ]

  return (
    <Card
      title="我的记录"
      extra={
        <Space>
          {isAdmin && (
            <Space size={6}>
              <span style={{ color: '#8c919e', fontSize: 13 }}>显示作者</span>
              <Switch size="small" checked={showAuthor} onChange={toggleAuthor} data-testid="notes-show-author" />
              <span style={{ color: '#8c919e', fontSize: 13, marginLeft: 8 }}>只看我的</span>
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
        className="notes-table"
        onChange={(_, f) => setFilters(f)}
        rowClassName={(r) => (dragId && overId === r.id && dragId !== r.id ? 'note-row-drag-over' : '')}
        loading={loading}
        columns={columns}
        dataSource={list}
        pagination={false}
        onRow={(r) => ({
          onClick: () => setViewing(r),
          style: { cursor: 'pointer', opacity: dragId === r.id ? 0.4 : 1 },
          draggable: canDrag && r.mine,
          onDragStart: () => setDragId(r.id),
          onDragEnd: () => setDragId(null),
          onDragOver: (e) => { if (dragId) { e.preventDefault(); setOverId(r.id) } },
          onDrop: (e) => { e.preventDefault(); handleDrop(r) },
        })}
        locale={{ emptyText: <Empty description={canWrite ? '还没有记录，点右上角「新建记录」写一条' : '还没有记录'} /> }}
      />

      <Drawer
        title={viewing && <Space size={8}><Tag color={viewing.kind === 'todo' ? 'orange' : 'blue'}>{KIND_LABEL[viewing.kind]}</Tag>{viewing.title}</Space>}
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
                {viewing.kind === 'todo' && <span>状态：{viewing.status === 'done' ? '已完成' : '待完成'}</span>}
                <span>创建于 {fmt(viewing.createdAt)}</span>
                {viewing.kind === 'todo' && viewing.status === 'done' && viewing.completedAt && <span>完成于 {fmt(viewing.completedAt)}</span>}
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
        <Form form={form} layout="vertical" initialValues={{ kind: 'note', status: 'pending' }}>
          <Form.Item name="kind" label="类型">
            <Radio.Group optionType="button" buttonStyle="solid" data-testid="note-form-kind"
              options={[{ label: '笔记', value: 'note' }, { label: '待办', value: 'todo' }]} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(a, b) => a.kind !== b.kind}>
            {({ getFieldValue }) => getFieldValue('kind') === 'todo' && (
              <Form.Item name="status" label="状态">
                <Radio.Group options={[{ label: '待完成', value: 'pending' }, { label: '已完成', value: 'done' }]} data-testid="note-form-status" />
              </Form.Item>
            )}
          </Form.Item>
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
