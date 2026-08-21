import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import {
  Card, Table, Button, Modal, Form, Input, Switch, Tag, Tooltip,
  Popconfirm, message, Space, Typography, Empty, Alert, Select,
} from 'antd'
import {
  PlusOutlined, EditOutlined, DeleteOutlined, DatabaseOutlined,
  ReloadOutlined, BulbOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'

const { Text, Paragraph } = Typography

// 「凭证概览」那张表 2026-08-21 去掉了：它聚合展示各环境的凭证类变量，
// 跟「环境配置」页是同一份数据，两处显示只会让人不知道该改哪边。
// 随之删掉的还有本地的 maskSecret/CRED_KEY/SECRET_KEY —— 共享资源的 createDef
// 由后端 sync.py 的 _mask_deep 脱敏后才下发，前端不需要自己再脱一遍。

// 安全解析 JSON 文本框；空串按空对象/ null
function parseJsonField(text, { nullable = false } = {}) {
  const t = (text || '').trim()
  if (!t) return nullable ? null : {}
  return JSON.parse(t) // 抛错由调用方捕获
}

export default function AutomationData() {
  const { projectId } = useParams()
  const base = `/projects/${projectId}/automation-resources`

  // ---- 共享资源 ----
  const [resources, setResources] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)

  const loadResources = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    try {
      const res = await api.get(base)
      setResources(res.data || [])
    } catch (e) {
      message.error('加载共享资源失败')
    } finally {
      setLoading(false)
    }
  }, [projectId, base])

  useEffect(() => { loadResources() }, [loadResources])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ keep: true, existsCheckText: '', createDefText: '' })
    setModalOpen(true)
  }

  const openEdit = (r) => {
    setEditing(r)
    form.setFieldsValue({
      name: r.name,
      description: r.description || '',
      keep: r.keep,
      existsCheckText: r.existsCheck && Object.keys(r.existsCheck).length
        ? JSON.stringify(r.existsCheck, null, 2) : '',
      createDefText: r.createDef ? JSON.stringify(r.createDef, null, 2) : '',
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    let values
    try {
      values = await form.validateFields()
    } catch { return }
    let existsCheck, createDef
    try {
      existsCheck = parseJsonField(values.existsCheckText)
    } catch {
      message.error('存在性检查不是合法 JSON')
      return
    }
    try {
      createDef = parseJsonField(values.createDefText, { nullable: true })
    } catch {
      message.error('创建定义不是合法 JSON')
      return
    }
    const payload = {
      name: values.name,
      description: values.description || null,
      keep: values.keep,
      existsCheck,
      createDef,
    }
    setSaving(true)
    try {
      if (editing) {
        await api.put(`${base}/${editing.id}`, payload)
        message.success('已更新')
      } else {
        await api.post(base, payload)
        message.success('已创建')
      }
      setModalOpen(false)
      loadResources()
    } catch (e) {
      message.error(e?.response?.data?.detail?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (r) => {
    try {
      await api.del(`${base}/${r.id}`)
      message.success('已删除')
      loadResources()
    } catch {
      message.error('删除失败')
    }
  }

  const resourceCols = [
    {
      title: '资源名',
      dataIndex: 'name',
      render: (v) => <Text strong>{v}</Text>,
    },
    {
      title: '存在性检查',
      dataIndex: 'existsCheck',
      render: (v) => {
        if (!v || !Object.keys(v).length) return <Text type="secondary">—</Text>
        return <Text code style={{ fontSize: 12 }}>{v.method || 'GET'} {v.url || ''}</Text>
      },
    },
    {
      // **这一列的文案错过两次，方向相反。** 最早叫「缺失可自动创建」+「支持」，
      // 而当时代码只存不执行 —— 过度承诺。改成「平台不会替你建」之后，
      // 执行期又真的会建了（api_test_runner._resolve_automation_resources →
      // _auto_create_resource），于是变成往低了说，同一类 bug 换了个方向。
      // 现在按**入口**说，不说"平台建不建"这种全局话：
      //   预检/本页 只探不建（precheck_service 只输出 canCreate）
      //   跑场景/跑 UI 脚本之前 探到 missing 且有 create_def → 补建 → 复探
      // 只认 missing（请求成功且明确没匹配上）；401/5xx/超时是 unknown，一律不动 ——
      // 一次 token 过期就照着建，会在被测环境里造一堆 keep=true 没人清的重复底座。
      title: '缺失时怎么办',
      dataIndex: 'createDef',
      width: 190,
      render: (v) => v
        ? <Tooltip title="跑场景或 UI 脚本之前，平台探到「确实没有」会照这份定义补建，然后复探一次（只认 missing；401/超时算没查成，不动）。本页和预检只探不建。">
            <Tag color="blue">执行前自动补建</Tag>
          </Tooltip>
        : <Tooltip title="没登记创建方式。缺失时平台补不了，链子会红在「变量未解析」上 —— 补一份 create_def，或者在场景开头自己建、末尾删">
            <Tag>未登记创建方式</Tag>
          </Tooltip>,
    },
    {
      title: '长期保留',
      dataIndex: 'keep',
      width: 90,
      align: 'center',
      render: (v) => v ? <Tag color="green">保留</Tag> : <Tag color="orange">可清理</Tag>,
    },
    { title: '说明', dataIndex: 'description', render: (v) => v || <Text type="secondary">—</Text> },
    {
      title: '操作',
      width: 120,
      render: (_, r) => (
        <Space>
          <Tooltip title="编辑"><Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} /></Tooltip>
          <Popconfirm title="确定删除该共享资源？" onConfirm={() => handleDelete(r)} okText="删除" cancelText="取消">
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]


  // ---- 项目须知 ----
  // 写用例的人（和 CC）**必须知道、但接口文档里看不出来**的那些事：
  // 「404 有两种，只断状态码会误判」「offline 会连带把 enabled 置 false」。
  // 它们过去只活在某一次会话的上下文里，会话一结束就没了，下一轮从零再踩一遍。
  const NOTE_CATS = [
    { value: 'api_note', label: '接口/系统行为' },
    { value: 'bug_pattern', label: '踩过的坑' },
    { value: 'custom', label: '其它' },
  ]
  const NOTE_MAX = 200   // 和后端 project_notes.MAX_CONTENT 一致
  const [notes, setNotes] = useState([])
  const [noteLoading, setNoteLoading] = useState(false)
  const [noteOpen, setNoteOpen] = useState(false)
  const [noteForm] = Form.useForm()
  const [editingNote, setEditingNote] = useState(null)

  const loadNotes = useCallback(async () => {
    setNoteLoading(true)
    try {
      // 路由是 /api/projects/{id}/knowledge —— **路径参数不是查询参数**。
      // 写成 ?projectId= 会 404，而拦截器把它吞了：页面只是空着，不报错。
      const res = await api.get(`/projects/${projectId}/knowledge`)
      // review_feedback 是 AI 评审自己写的，不是项目知识 —— 混在一起
      // 人分不清"系统的事实"和"对用例的意见"
      setNotes((res.data || []).filter(n => n.category !== 'review_feedback'))
    } catch { /* 拦截器已弹错 */ } finally { setNoteLoading(false) }
  }, [projectId])
  useEffect(() => { if (projectId) loadNotes() }, [loadNotes, projectId])

  const saveNote = async () => {
    const v = await noteForm.validateFields()
    try {
      if (editingNote) await api.delete(`/projects/${projectId}/knowledge/${editingNote.id}`)
      await api.post(`/projects/${projectId}/knowledge`, v)
      message.success(editingNote ? '已更新' : '已添加')
      setNoteOpen(false); setEditingNote(null); noteForm.resetFields(); loadNotes()
    } catch (e) { message.error(e.message || '保存失败') }
  }

  const noteCols = [
    { title: '分类', dataIndex: 'category', width: 110,
      render: c => <Tag color={c === 'bug_pattern' ? 'orange' : c === 'api_note' ? 'blue' : 'default'}>
        {NOTE_CATS.find(x => x.value === c)?.label || c}</Tag> },
    { title: '一句话说清', dataIndex: 'title', width: 260,
      render: t => <span style={{ fontWeight: 500 }}>{t}</span> },
    { title: '正文', dataIndex: 'content',
      render: c => <span style={{ fontSize: 12, color: '#4e5969', whiteSpace: 'pre-wrap' }}>{c}</span> },
    { title: '谁写的', dataIndex: 'source', width: 90,
      render: s => <Tag color={s === 'cc' ? 'cyan' : 'default'}>{s === 'cc' ? 'CC 回写' : '人工'}</Tag> },
    { title: '操作', width: 90, render: (_, r) => (
      <Space size={4}>
        <Button type="text" size="small" icon={<EditOutlined />}
          onClick={() => { setEditingNote(r); noteForm.setFieldsValue(r); setNoteOpen(true) }} />
        <Popconfirm title="删掉这条须知？" onConfirm={async () => {
          await api.delete(`/projects/${projectId}/knowledge/${r.id}`); message.success('已删除'); loadNotes()
        }} okText="删除" cancelText="取消">
          <Button type="text" size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space>
    ) },
  ]

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>
        <DatabaseOutlined /> 自动化数据
      </Typography.Title>
      <Paragraph type="secondary" style={{ marginBottom: 20 }}>
        项目级自动化测试所需的全局数据。<Text strong>共享资源</Text>由 Claude Code 活体验证时自动登记，跑自动化前平台会探它在当前环境存不存在——
        <Text strong>探到就注入成变量（接口场景和 UI 脚本共用同一份）；确实没有、且登记过 createDef 的，平台会照它补建再复探一次</Text>
        （401 / 超时算「没查成」，一律不动，免得在被测环境里造一堆没人清的重复底座）。<Text strong>本页只探不建</Text>，补建发生在跑场景/跑脚本之前。长期保留、绝不被用例删除；
        <Text strong>凭证不在这里</Text>——多角色账号/密码/Token 一律是环境变量，去「项目配置 → 环境配置」维护，本页不再重复展示同一份数据。
      </Paragraph>

      <Card
        title={<span><DatabaseOutlined /> 共享资源</span>}
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} size="small" onClick={loadResources}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} size="small" onClick={openCreate}>新增资源</Button>
          </Space>
        }
        style={{ marginBottom: 24 }}
      >
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={resourceCols}
          dataSource={resources}
          pagination={false}
          locale={{ emptyText: <Empty description="暂无共享资源。主入口不是人手填 —— Claude Code 活体验证时遇到多条用例共用、重建代价大的底座（上游/负载、隔离上下文），会自己调 tb_upsert_automation_resource 登记到这里。也可点「新增资源」手工补。" /> }}
        />
      </Card>


      <Card
        style={{ marginBottom: 16 }}
        title={<span><BulbOutlined /> 项目须知</span>}
        extra={<Space>
          <Button icon={<ReloadOutlined />} size="small" onClick={loadNotes}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} size="small"
            onClick={() => { setEditingNote(null); noteForm.resetFields(); setNoteOpen(true) }}>新增须知</Button>
        </Space>}
      >
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message={<span style={{ fontSize: 12.5 }}>
            写用例必须知道、但接口文档里看不出来的那些事 —— 比如「404 有两种，
            上游的 404 和网关无路由的 404 不是一回事，只断状态码会误判成没生效」。
            人和 Claude Code 都能往里写（CC 用 tb_add_project_note），
            动手写用例之前 CC 会先读一遍。
            一条只说一件事、正文 {NOTE_MAX} 字以内 —— 这些内容每次生成都要整个喂给 CC，
            长了直接挤占它的上下文。
          </span>} />
        <Table rowKey="id" size="small" loading={noteLoading} columns={noteCols}
          dataSource={notes} pagination={false}
          locale={{ emptyText: <Empty description="还没有项目须知。跑一轮下来「原来这个接口是这样」的那些发现，写进来，下一轮就不用再踩一遍。" /> }} />
      </Card>

      <Modal
        title={editing ? '编辑共享资源' : '新增共享资源'}
        open={modalOpen}
        onOk={handleSubmit}
        confirmLoading={saving}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        cancelText="取消"
        width={620}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="资源名" rules={[{ required: true, message: '请输入资源名' }]}>
            <Input placeholder="如 default-upstream / 共享测试服务" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input placeholder="这条资源是什么、给哪些用例用" />
          </Form.Item>
          <Form.Item
            name="existsCheckText"
            label="存在性检查 (JSON)"
            tooltip='跑自动化前如何判断它已存在，以及抽哪个字段当变量。extract 的路径相对 match 命中的那一条写（直接 "id"），不要写 "data[0].id" —— 下标是另一种写死，列表顺序一变就抽到别的资源。不写 extract 就只判断存在、不注入任何变量。'
          >
            <Input.TextArea spellCheck={false} rows={5} placeholder={'{"method":"GET","url":"/api/v1/upstreams",\n "match":{"field":"name","equals":"default-upstream"},\n "extract":{"upstreamId":"id"}}'} style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }} />
          </Form.Item>
          <Form.Item
            name="createDefText"
            label="创建定义 (JSON，可选)"
            tooltip="缺失时如何创建（仅在用户确认后使用）；留空表示缺失时只提示确认、不自动建"
          >
            <Input.TextArea spellCheck={false} rows={5} placeholder='留空=仅确认；或 {"method":"POST","url":"/api/v1/upstreams","body":{...}}' style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }} />
          </Form.Item>
          <Form.Item name="keep" label="长期保留（绝不被测试删除）" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    
      <Modal open={noteOpen} title={editingNote ? '编辑须知' : '新增须知'}
        onCancel={() => { setNoteOpen(false); setEditingNote(null) }} onOk={saveNote}
        okText="保存" cancelText="取消" destroyOnHidden>
        <Form form={noteForm} layout="vertical" initialValues={{ category: 'api_note' }}>
          <Form.Item name="category" label="分类" rules={[{ required: true }]}>
            <Select options={NOTE_CATS} />
          </Form.Item>
          <Form.Item name="title" label="一句话说清是什么" rules={[{ required: true, max: 200 }]}
            extra="例如「404 有两种，别只看状态码」">
            <Input placeholder="现象是什么" />
          </Form.Item>
          <Form.Item name="content" label="正文" extra={`一条只说一件事，写成「现象 + 别踩的坑」。${NOTE_MAX} 字以内 —— 这些内容每次生成都会整个喂给 CC。`}
            rules={[{ required: true }, { max: NOTE_MAX, message: `超过 ${NOTE_MAX} 字了，拆成两条` }]}>
            <Input.TextArea rows={4} showCount maxLength={NOTE_MAX}
              placeholder="转发路径不填=按路由原样转发，上游无此路径回 404（上游的 404，HTML）；和网关「无此路由」的 404 完全不同。只断状态码会误判成「没生效」。" />
          </Form.Item>
        </Form>
      </Modal>

    </div>
  )
}
