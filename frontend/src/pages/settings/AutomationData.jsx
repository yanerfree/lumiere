import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import {
  Card, Table, Button, Modal, Form, Input, Switch, Tag, Tooltip,
  Popconfirm, message, Space, Typography, Empty, Alert,
} from 'antd'
import {
  PlusOutlined, EditOutlined, DeleteOutlined, DatabaseOutlined,
  KeyOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'

const { Text, Paragraph } = Typography

// 凭证类环境变量识别（多角色账号/密码/token）
const CRED_KEY = /(USER(NAME)?|PASSWORD|PWD|TOKEN|SECRET|ACCOUNT|LOGIN|ROLE)/i
const SECRET_KEY = /(PASSWORD|PWD|TOKEN|SECRET)/i

function maskSecret(key, value) {
  if (!value) return value
  if (SECRET_KEY.test(key)) return value.length <= 2 ? '••' : value[0] + '••••' + value.slice(-1)
  return value
}

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
      // 平台不执行 create_def，只登记备查。原来这列叫「缺失可自动创建」+「支持」，
      // 是在承诺代码做不到的事 —— 用户照着理解，跑起来只会拿到「变量未解析」。
      title: '缺失时怎么办',
      dataIndex: 'createDef',
      width: 190,
      render: (v) => v
        ? <Tooltip title="平台不会替你建。Claude Code 调 tb_list_global_data(probe=true) 看到 missing 后，照这份定义自己调接口造出来">
            <Tag color="blue">CC 按定义自建</Tag>
          </Tooltip>
        : <Tooltip title="没登记创建方式，缺失时只能人工处理">
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

  // ---- 凭证概览（只读，聚合各环境的凭证类变量，多角色） ----
  const [creds, setCreds] = useState([])
  const [credLoading, setCredLoading] = useState(false)

  const loadCreds = useCallback(async () => {
    setCredLoading(true)
    try {
      const envRes = await api.get('/environments')
      const envs = envRes.data || []
      const rows = []
      for (const env of envs) {
        try {
          const varRes = await api.get(`/environments/${env.id}/variables`)
          for (const v of (varRes.data || [])) {
            if (CRED_KEY.test(v.key)) {
              rows.push({
                key: `${env.id}:${v.key}`,
                env: env.name,
                varKey: v.key,
                value: maskSecret(v.key, v.value),
              })
            }
          }
        } catch { /* 跳过单个环境失败 */ }
      }
      setCreds(rows)
    } catch {
      // 环境接口失败静默
    } finally {
      setCredLoading(false)
    }
  }, [])

  useEffect(() => { loadCreds() }, [loadCreds])

  const credCols = [
    { title: '环境', dataIndex: 'env', width: 160, render: (v) => <Tag>{v}</Tag> },
    { title: '变量名', dataIndex: 'varKey', render: (v) => <Text code>{v}</Text> },
    { title: '值', dataIndex: 'value', render: (v) => <Text type="secondary">{v}</Text> },
  ]

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>
        <DatabaseOutlined /> 自动化数据
      </Typography.Title>
      <Paragraph type="secondary" style={{ marginBottom: 20 }}>
        项目级自动化测试所需的全局数据。<Text strong>共享资源</Text>由 Claude Code 活体验证时自动登记，跑自动化前平台会预检它在当前环境存不存在——
        <Text strong>探到就注入成变量；探不到只报「变量未解析」，平台不会替你补建</Text>（登记的 createDef 只备查、不执行，由 CC 自己照着造）。长期保留、绝不被用例删除；
        <Text strong>凭证</Text>沿用各环境的环境变量（多角色账号/密码/Token），此处仅聚合展示。
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
        title={<span><KeyOutlined /> 凭证概览（只读）</span>}
        extra={<Button icon={<ReloadOutlined />} size="small" onClick={loadCreds}>刷新</Button>}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="凭证在「环境管理」里以环境变量维护，此处按角色/环境聚合展示。密码/Token 类已脱敏。"
        />
        <Table
          rowKey="key"
          size="small"
          loading={credLoading}
          columns={credCols}
          dataSource={creds}
          pagination={false}
          locale={{ emptyText: <Empty description="未在环境变量中发现凭证类变量（USER/PASSWORD/TOKEN 等）" /> }}
        />
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
    </div>
  )
}
