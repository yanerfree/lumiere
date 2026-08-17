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

// 词典里的语种键是 BCP-47（en-US / zh-CN）—— 只认裸 'en' 的话，
// 从被测系统 locale 导进来的 2400+ 条译文一条都读不到，页面上「已翻译」恒为 0。
const pick = (r, lang) => {
  const t = r?.translations || {}
  if (t[lang]) return t[lang]
  const hit = Object.keys(t).find(k => k.split('-')[0] === lang && t[k])
  return hit ? t[hit] : ''
}
const enOf = (r) => pick(r, 'en')
// 中文：key 制的行译文里有 zh-CN；采集器那批是拿中文当 key，中文就在 key 上。
const zhOf = (r) => pick(r, 'zh') || r.keyText || r.key_text || ''

// 键本身就编码了位置：`services.detail.btn.enable` = 服务管理 › 详情页 › 按钮 › enable。
// **在页面上直接把它翻出来，不要指望「说明」那一栏**——说明是导入时写死的一句话
// （「从被测系统 locale 导入：操作」），既看不出位置、又会随文案改动过期。
// 从键推导则永远和键一致。
const NS_LABEL = {
  common: '通用', services: '服务管理', subscription: '订阅管理', apps: '应用管理',
  auth: '登录认证', dashboard: '概览', gateway: '网关', upstream: '负载', menu: '菜单',
  tenant: '租户', application: '应用',
}
const SEG_LABEL = {
  btn: '按钮', button: '按钮', form: '表单', modal: '弹窗', dialog: '弹窗', drawer: '抽屉',
  list: '列表', table: '表格', detail: '详情页', create: '创建页', edit: '编辑页',
  tab: '页签', tabs: '页签', msg: '提示', message: '提示', toast: '提示',
  placeholder: '占位符', title: '标题', label: '标签', validation: '校验',
  filter: '筛选', status: '状态', empty: '空态', confirm: '确认', error: '错误',
  success: '成功', tip: '说明', column: '列', field: '字段', action: '操作',
  lifecycle: '生命周期', version: '版本', manage: '管理', overview: '概览',
}
// 驼峰拆开，并把**结尾那个控件类型**翻出来：bindModal → bind 弹窗、configTitle → config 标题。
// 只翻结尾那一个词 —— 键的命名习惯是「做什么 + 是什么控件」，控件类型在最后。
// 中间的业务词不翻：那是被测系统自己的叫法，硬翻会翻错。
const TYPE_TAIL = {
  modal: '弹窗', dialog: '弹窗', drawer: '抽屉', btn: '按钮', button: '按钮',
  title: '标题', label: '标签', msg: '提示', tip: '说明', tab: '页签',
  form: '表单', list: '列表', table: '表格', column: '列', field: '字段',
  placeholder: '占位符', error: '错误', success: '成功', empty: '空态',
}
const humanize = (seg) => {
  const words = seg.replace(/([a-z0-9])([A-Z])/g, '$1 $2').toLowerCase().split(' ')
  const tail = TYPE_TAIL[words[words.length - 1]]
  if (tail && words.length > 1) return words.slice(0, -1).join(' ') + ' ' + tail
  return tail || words.join(' ')
}
const keyPath = (key) => {
  if (!key || /[\u4e00-\u9fff]/.test(key)) return null   // 中文当键的没有路径可推
  const segs = key.split('.')
  const out = segs.map((seg, i) => {
    if (i === 0) return NS_LABEL[seg] || seg
    return SEG_LABEL[seg] || humanize(seg)
  })
  return out.join(' › ')
}

export default function I18nMessages() {
  const { projectId } = useParams()
  const base = `/projects/${projectId}/i18n-messages`

  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [scanResult, setScanResult] = useState(null)
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
      const d = res.data || {}
      const { scanned = 0, mapped = [], unmapped = [] } = d
      // **它不再往词典里插词条了。** 以前是拿脚本里的中文原文当键插进来 ——
      // 中文既是键又是值，中文一改键就失效；而且 translations 是空的，
      // t() 查不到译文就返回键（正好是中文），和没这条一模一样。
      // 现在它做的是反查：脚本里的硬编码中文该换成哪个语言中立的键。
      if (scanned === 0) {
        message.warning('这个项目还没有 UI 脚本，没什么可查的。先把脚本回推上来再来扫。')
      } else {
        setScanResult({ scanned, mapped, unmapped })
      }
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
      // 中文也要能改 —— 原来弹窗里只有英文一栏，键制之后中文是**值**，
      // 改不了等于这条词只能改一半。
      zh: pick(r, 'zh'),
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
    // 中英文都归并进 translations，保留其它语种
    const translations = { ...(editing?.translations || {}) }
    for (const [lang, val] of [['zh-CN', values.zh], ['en-US', values.en]]) {
      if (val && val.trim()) translations[lang] = val.trim()
      else delete translations[lang]
    }
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

  // 扫描结果（不写库，只报告）
  const scanPanel = scanResult && (
    <Alert type={scanResult.unmapped.length ? 'warning' : 'info'} showIcon
      style={{ marginBottom: 16 }} closable onClose={() => setScanResult(null)}
      message={`扫了 ${scanResult.scanned} 个 UI 脚本：${scanResult.mapped.length} 处硬编码中文可以换成键，${scanResult.unmapped.length} 处在词典里找不到`}
      description={
        <div style={{ fontSize: 12 }}>
          {scanResult.unmapped.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <Text strong type="danger">找不到键（英文环境会挂）：</Text>
              {scanResult.unmapped.map(x => (
                <div key={x.text}>
                  「{x.text}」 <Text type="secondary">用在 {x.cases.join('、')}</Text>
                </div>
              ))}
              <Text type="secondary">
                要么被测系统自己硬编码了中文没走 i18n，要么脚本里的文案过期了。
              </Text>
            </div>
          )}
          {scanResult.mapped.length > 0 && (
            <div>
              <Text strong>照这个改成 t("…")：</Text>
              {scanResult.mapped.map(x => (
                <div key={x.text}>
                  「{x.text}」 → <Text code>t("{x.key}")</Text>{' '}
                  <Text type="secondary">用在 {x.cases.join('、')}</Text>
                </div>
              ))}
            </div>
          )}
        </div>
      } />
  )

  const columns = [
    {
      // 原来这列没给宽度、又套了 <Text strong code>：键被挤成两三行、
      // 每行还带一圈灰底描边，一屏十几条叠起来全是框，什么都读不进去。
      // 键是给人复制到脚本里的，等宽字体够了，不要加粗也不要边框。
      title: '键',
      dataIndex: 'keyText',
      width: 300,
      render: (v) => (
        <div>
          <Text style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, wordBreak: 'break-all' }}>{v}</Text>
          {/[\u4e00-\u9fff]/.test(v) && (
            <Tag color="orange" style={{ marginLeft: 6, fontSize: 11 }}>键不该用中文</Tag>
          )}
          {keyPath(v) && (
            <div style={{ fontSize: 11.5, color: '#86909c', marginTop: 2 }}>{keyPath(v)}</div>
          )}
        </div>
      ),
    },
    {
      // 中文和英文必须挨着 —— 原来中间夹了「分类」列，同一条文案的两种语言
      // 隔着一列比对，眼睛要来回跳。
      title: '中文 (zh)',
      key: 'zh',
      width: 240,
      render: (_, r) => zhOf(r) || <Text type="secondary">—</Text>,
    },
    {
      title: '英文 (en)',
      key: 'en',
      width: 260,
      render: (_, r) => {
        const en = enOf(r)
        return en ? <Text>{en}</Text> : <Text type="secondary" italic>待补</Text>
      },
    },
    {
      title: '分类',
      dataIndex: 'category',
      width: 120,
      render: (v) => v ? <Tag color={CATEGORY_COLOR[v] || 'default'}>{v}</Tag> : <Text type="secondary">—</Text>,
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 90,
      align: 'center',
      render: (v) => v === 'manual'
        ? <Tag color="orange">手工</Tag>
        : <Tag color="green">导入</Tag>,
    },
    {
      title: '操作',
      width: 100,
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
    <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>
        <TranslationOutlined /> 国际化词典
      </Typography.Title>
      <Paragraph type="secondary" style={{ marginBottom: 20 }}>
        项目级文案词典。<Text strong>键是语言中立的</Text>（如 <Text code>services.form.nameRequired</Text>），
        中文和英文都是它的值 —— 测试里引用键，切语种时取对应译文。
        UI 脚本写 <Text code>t("services.form.nameRequired")</Text>，接口断言写
        <Text code>{'${T:services.form.nameRequired}'}</Text>，
        跑哪种语言由全局变量 <Text code>TEST_LANGUAGE=zh|en</Text> 决定（不填就是中文）。
        <br />词典里查不到就原样返回，不会因为缺一条词让脚本挂掉。
        主要来源是从被测系统的 locale 文件导入（键和译文一并带进来）；
        点<Text strong>「扫描脚本检查」</Text>会扫所有 UI 脚本，
        把里面硬编码的中文反查成键、并列出词典里找不到的那些
        （<Text strong>那正是英文环境下会挂的地方</Text>）—— 只报告，不写词典。
      </Paragraph>

      {scanPanel}
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
            <Button icon={<ScanOutlined />} size="small" loading={scanning} onClick={handleScan}>扫描脚本检查</Button>
            <Button type="primary" icon={<PlusOutlined />} size="small" onClick={openCreate}>新增词条</Button>
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="键必须是语言中立的（services.form.nameRequired）—— 中文和英文都是它的值。别拿中文原文当键：中文既是键又是值，中文文案一改键就失效，而且是静默失效（t() 查不到就原样返回，红都不红）。"
        />
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={rows}
          pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
          locale={{ emptyText: <Empty description="暂无词条，点击「扫描脚本检查」从已生成脚本抽取，或「新增词条」手工录入" /> }}
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
            label="键"
            tooltip="语言中立的键，如 services.form.nameRequired。段落本身就是位置：命名空间.区域.控件类型.具体项。测试里引用它，切语种时取对应译文。"
            rules={[{ required: true, message: '请输入键' }]}
            tooltip="被测系统里真实的中文文案，如「确认绑定」。这是词典的键，二期脚本用它做匹配。"
          >
            <Input placeholder="如 services.detail.btn.enable"
              style={{ fontFamily: 'var(--font-mono)' }} />
          </Form.Item>
          <Form.Item name="category" label="分类">
            <Select allowClear placeholder="按定位方式分类（可选）" options={CATEGORY_OPTIONS} />
          </Form.Item>
          <Form.Item name="zh" label="中文 (zh)"
            tooltip="键制下中文是这个键的值之一，不是键本身。TEST_LANGUAGE=zh 时取它。">
            <Input placeholder="如 创建服务" />
          </Form.Item>
          <Form.Item name="en" label="英文 (en)" tooltip="留空表示待补；TEST_LANGUAGE=en 时取它。">
            <Input placeholder="如 Confirm Binding（留空=待补）" />
          </Form.Item>
          <Form.Item name="description" label="说明"
            tooltip="补充键推不出来的信息（比如这句话在什么条件下才出现）。位置不用写在这里 —— 键本身已经表达了，列表上会自动翻成「服务管理 › 详情页 › 按钮」。">
            <Input placeholder="这条文案出现在哪、上下文（可选）" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
