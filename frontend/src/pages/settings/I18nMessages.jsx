import { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams } from 'react-router-dom'
import {
  Card, Table, Button, Modal, Form, Input, Select, Tag, Tooltip,
  Popconfirm, message, Space, Typography, Empty, Alert, Statistic, Row, Col,
} from 'antd'
import {
  PlusOutlined, EditOutlined, DeleteOutlined, TranslationOutlined,
  ReloadOutlined, ScanOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'

const { Text, Paragraph } = Typography

// 分类选项（与采集器推断的分类对齐）
const CATEGORY_OPTIONS = [
  { value: 'button', label: '按钮 button' },
  { value: 'placeholder', label: '占位符 placeholder' },
  { value: 'label', label: '标签 label' },
  { value: 'text', label: '文本/Toast text' },
  { value: 'title', label: '标题 title' },
  { value: 'tab', label: '标签页 tab' },
  { value: 'link', label: '链接 link' },
  { value: 'menu', label: '菜单 menu' },
  { value: 'option', label: '选项 option' },
]

const CATEGORY_COLOR = {
  button: 'blue', placeholder: 'geekblue', label: 'cyan', text: 'default',
  title: 'purple', tab: 'gold', link: 'magenta', menu: 'green', option: 'lime',
}

const enOf = (r) => (r.translations && r.translations.en) || ''

export default function I18nMessages() {
  const { projectId } = useParams()
  const base = `/projects/${projectId}/i18n-messages`

  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    try {
      const res = await api.get(base)
      setRows(res.data || [])
    } catch {
      message.error('加载词典失败')
    } finally {
      setLoading(false)
    }
  }, [projectId, base])

  useEffect(() => { load() }, [load])

  const stats = useMemo(() => {
    const total = rows.length
    const translated = rows.filter((r) => enOf(r).trim()).length
    return { total, translated, untranslated: total - translated }
  }, [rows])

  const handleScan = async () => {
    setScanning(true)
    try {
      const res = await api.post(`${base}/harvest`)
      const { added = 0, scanned = 0 } = res.data || {}
      message.success(`采集完成：新增 ${added} 条文案（扫描 ${scanned} 个脚本）`)
      load()
    } catch {
      message.error('采集失败')
    } finally {
      setScanning(false)
    }
  }

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    setModalOpen(true)
  }

  const openEdit = (r) => {
    setEditing(r)
    form.setFieldsValue({
      keyText: r.keyText,
      category: r.category || undefined,
      en: enOf(r),
      description: r.description || '',
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    let values
    try {
      values = await form.validateFields()
    } catch { return }
    // en 归并进 translations，保留其它语种
    const translations = { ...(editing?.translations || {}) }
    if (values.en && values.en.trim()) translations.en = values.en.trim()
    else delete translations.en
    const payload = {
      keyText: values.keyText,
      category: values.category || null,
      description: values.description || null,
      translations,
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
      load()
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
      load()
    } catch {
      message.error('删除失败')
    }
  }

  const columns = [
    {
      title: '中文原文（键）',
      dataIndex: 'keyText',
      render: (v) => <Text strong>{v}</Text>,
    },
    {
      title: '分类',
      dataIndex: 'category',
      width: 130,
      render: (v) => v ? <Tag color={CATEGORY_COLOR[v] || 'default'}>{v}</Tag> : <Text type="secondary">—</Text>,
    },
    {
      title: '英文 (en)',
      key: 'en',
      render: (_, r) => {
        const en = enOf(r)
        return en ? <Text>{en}</Text> : <Text type="secondary" italic>待补</Text>
      },
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 100,
      align: 'center',
      render: (v) => v === 'manual'
        ? <Tag color="orange">手工</Tag>
        : <Tag color="green">采集</Tag>,
    },
    {
      title: '操作',
      width: 110,
      render: (_, r) => (
        <Space>
          <Tooltip title="编辑"><Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} /></Tooltip>
          <Popconfirm title="确定删除该词条？" onConfirm={() => handleDelete(r)} okText="删除" cancelText="取消">
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>
        <TranslationOutlined /> 国际化词典
      </Typography.Title>
      <Paragraph type="secondary" style={{ marginBottom: 20 }}>
        项目级 UI 文案词典。以<Text strong>中文原文</Text>为键，沉淀被测系统的按钮/占位符/标签/Toast 等文案，
        为脚本国际化打底座。点<Text strong>「扫描脚本采集」</Text>从已生成的 UI 脚本自动抽取中文文案；
        英文列（en）现留空，待英文环境跑测时再补。
      </Paragraph>

      <Card style={{ marginBottom: 16 }}>
        <Row gutter={16}>
          <Col span={8}><Statistic title="总词条" value={stats.total} /></Col>
          <Col span={8}><Statistic title="已翻译 (en)" value={stats.translated} valueStyle={{ color: '#52c41a' }} /></Col>
          <Col span={8}><Statistic title="待补" value={stats.untranslated} valueStyle={{ color: '#faad14' }} /></Col>
        </Row>
      </Card>

      <Card
        title={<span><TranslationOutlined /> 文案词条</span>}
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} size="small" onClick={load}>刷新</Button>
            <Button icon={<ScanOutlined />} size="small" loading={scanning} onClick={handleScan}>扫描脚本采集</Button>
            <Button type="primary" icon={<PlusOutlined />} size="small" onClick={openCreate}>新增词条</Button>
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="「采集」来源的词条键为脚本里抓到的中文原文，不建议改键；如需修正翻译，编辑 en 列即可。"
        />
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={rows}
          pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
          locale={{ emptyText: <Empty description="暂无词条，点击「扫描脚本采集」从已生成脚本抽取，或「新增词条」手工录入" /> }}
        />
      </Card>

      <Modal
        title={editing ? '编辑词条' : '新增词条'}
        open={modalOpen}
        onOk={handleSubmit}
        confirmLoading={saving}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        cancelText="取消"
        width={560}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="keyText"
            label="中文原文（键）"
            rules={[{ required: true, message: '请输入中文原文' }]}
            tooltip="被测系统里真实的中文文案，如「确认绑定」。这是词典的键，二期脚本用它做匹配。"
          >
            <Input placeholder="如 确认绑定 / 请输入 用户名 / 导入成功" disabled={!!(editing && editing.source === 'harvested')} />
          </Form.Item>
          <Form.Item name="category" label="分类">
            <Select allowClear placeholder="按定位方式分类（可选）" options={CATEGORY_OPTIONS} />
          </Form.Item>
          <Form.Item name="en" label="英文 (en)" tooltip="留空表示待补；英文环境跑测时可回填。">
            <Input placeholder="如 Confirm Binding（留空=待补）" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input placeholder="这条文案出现在哪、上下文（可选）" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
