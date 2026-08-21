import { useState, useEffect, useCallback } from 'react'
import { timeColumn } from '../../utils/timeCol'
import { Button, Input, Table, Modal, Form, message, Popconfirm, Tag, Space, Empty, Spin, Tooltip } from 'antd'
import { PlusOutlined, DeleteOutlined, EditOutlined, BellOutlined, EyeOutlined, EyeInvisibleOutlined } from '@ant-design/icons'
import { api } from '../../utils/request'

export default function ChannelConfig() {
  const [channels, setChannels] = useState([])
  const [loading, setLoading] = useState(true)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [revealedIds, setRevealedIds] = useState(new Set())

  const fetchChannels = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/channels')
      setChannels(res.data || [])
    } catch { /* */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { fetchChannels() }, [fetchChannels])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    setModalOpen(true)
  }

  const openEdit = (record) => {
    setEditing(record)
    form.setFieldsValue({ name: record.name, webhookUrl: record.webhookUrl })
    setModalOpen(true)
  }

  const handleSave = async () => {
    let values
    try { values = await form.validateFields() } catch { return }
    setSaving(true)
    try {
      if (editing) {
        await api.put(`/channels/${editing.id}`, { name: values.name, webhookUrl: values.webhookUrl })
        message.success('渠道已更新')
      } else {
        await api.post('/channels', { name: values.name, webhookUrl: values.webhookUrl })
        message.success('渠道创建成功')
      }
      setModalOpen(false)
      form.resetFields()
      fetchChannels()
    } catch { /* */ } finally { setSaving(false) }
  }

  const handleDelete = async (id) => {
    try {
      await api.del(`/channels/${id}`)
      message.success('渠道已删除')
      fetchChannels()
    } catch { /* */ }
  }

  const toggleReveal = (id) => {
    setRevealedIds(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const maskUrl = (url) => {
    if (!url) return '-'
    if (url.length <= 40) return url
    return url.substring(0, 30) + '***' + url.substring(url.length - 7)
  }

  const columns = [
    {
      title: '渠道名称', dataIndex: 'name', width: 200,
      render: v => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <BellOutlined style={{ color: '#0ea5a0' }} />
          <span style={{ fontWeight: 500 }}>{v}</span>
        </div>
      ),
    },
    {
      title: 'Webhook URL', dataIndex: 'webhookUrl',
      render: (v, record) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: '#4e5969' }}>
            {revealedIds.has(record.id) ? v : maskUrl(v)}
          </span>
          <Button type="text" size="small" icon={revealedIds.has(record.id) ? <EyeInvisibleOutlined /> : <EyeOutlined />}
            onClick={() => toggleReveal(record.id)} style={{ color: '#c9cdd4' }} />
        </div>
      ),
    },
    timeColumn({ key: 'createdAt', title: '创建时间' }),
    {
      title: '操作', width: 120, align: 'center',
      render: (_, record) => (
        <Space size={4}>
          <Tooltip title="编辑">
            <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          </Tooltip>
          <Popconfirm title={`确定删除「${record.name}」？`} onConfirm={() => handleDelete(record.id)}>
            <Tooltip title="删除">
              <Button type="text" size="small" icon={<DeleteOutlined />} style={{ color: '#e8453c' }} />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: '#1d2129' }}>通知渠道</h2>
          <span style={{ fontSize: 13, color: '#86909c' }}>配置钉钉 Webhook 通知渠道，用于测试完成和熔断时的消息推送</span>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增渠道</Button>
      </div>

      {loading ? <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div> :
        channels.length === 0 ? (
          <Empty description="暂无通知渠道" style={{ marginTop: 60 }}>
            <Button type="primary" onClick={openCreate}>创建第一个渠道</Button>
          </Empty>
        ) : (
          <Table dataSource={channels} columns={columns} rowKey="id" size="small"
            pagination={false} style={{ borderRadius: 14 }} />
        )
      }

      <Modal
        title={editing ? '编辑渠道' : '新增通知渠道'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => { setModalOpen(false); form.resetFields() }}
        okText={editing ? '保存' : '创建'}
        cancelText="取消"
        confirmLoading={saving}
        width={520}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="渠道名称" rules={[{ required: true, message: '请输入渠道名称' }]}>
            <Input placeholder="如：测试团队群、项目通知群" />
          </Form.Item>
          <Form.Item name="webhookUrl" label="Webhook URL" rules={[{ required: true, message: '请输入 Webhook URL' }]}
            extra="钉钉群机器人的 Webhook 地址">
            <Input.TextArea spellCheck={false} placeholder="https://oapi.dingtalk.com/robot/send?access_token=..." rows={3}
              style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
