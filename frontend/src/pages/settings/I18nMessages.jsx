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
// 分类要让人**一眼看出这条是什么**，尤其分出「校验错误」和「提示消息」——
// 那两类是断言里最常用的，混在 text 里就等于没分类
// （导入时我曾给 2400 条一律写 "text"，一列全是 text，筛不了）。
// 校验错误和提示消息**必须分开**：「必填」「格式不对」是可预期的输入校验，
// 而提示里还混着成功消息，断言的写法完全不同。
const CATEGORY_OPTIONS = [
  { value: 'button', label: '按钮' },
  { value: 'validation', label: '校验错误' },
  { value: 'message', label: '提示消息' },
  { value: 'status', label: '状态值' },
  { value: 'placeholder', label: '输入占位' },
  { value: 'label', label: '标签/字段名' },
  { value: 'title', label: '标题' },
  { value: 'tab', label: '页签' },
  { value: 'link', label: '链接' },
  { value: 'menu', label: '菜单' },
  { value: 'option', label: '下拉选项' },
  { value: 'text', label: '其他文本' },
]
const CATEGORY_LABEL = Object.fromEntries(CATEGORY_OPTIONS.map((o) => [o.value, o.label]))

const CATEGORY_COLOR = {
  button: 'blue', validation: 'red', message: 'orange', status: 'green',
  placeholder: 'geekblue', label: 'cyan', title: 'purple', tab: 'gold',
  link: 'magenta', menu: 'lime', option: 'volcano', text: 'default',
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

// 模块是**存下来的字段**，不是从键算的。
//
// 原来我从键的第一段实时推导（apps → 应用管理）。三个问题：派生值放在列表上，
// 人默认它能改、实际改不了；键写错了（svc.foo）它就跟着显示 svc，而这时该改的
// 是键；命名空间和「测试人员认得的菜单名」也不总是一一对应。
// 所以存字段：导入时按命名空间预填一次，之后人和 CC 都能改。
//
// 上一版试图把整条键翻成中文路径
// （`subscription.providerDetail.tenantCard.crossTenantSubscriber`
//   → 「订阅管理 › provider detail › tenant card › cross tenant subscriber」）。
// 那是错的：中间的业务词没有翻译表，只能露出英文，结果半中半洋比纯英文更难读，
// 还占两行、把键列挤成三行。**做一件缺翻译表的翻译，半成品比不做更糟。**
// 键剩下的部分本来就可读（tenantCard 一眼就是卡片），不需要我再翻一遍。
const NS_LABEL = {
  common: '通用', services: '服务管理', subscription: '订阅管理', apps: '应用管理',
  auth: '登录认证', dashboard: '概览', gateway: '网关', upstream: '负载', menu: '菜单',
  tenant: '租户', application: '应用',
}
const moduleOf = (r) => r?.module || ''
// 新建时按键的第一段给个默认值，省得每条都手打；人可以改掉。
const guessModule = (key) => {
  if (!key) return ''
  return NS_LABEL[key.split('.')[0]] || ''
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
  // 按模块筛选（键的第一段）。**模块不是字段，是键的一部分** ——
  // 所以它是筛选器，不是列、也不是表单项。
  // 做成一列的问题：那是个派生值，看得到却改不了；而命名写错了该改的是**键**，
  // 不存在"模块错了"这回事 —— 用一列暴露它只会让人以为这是可维护的字段。
  const [moduleFilter, setModuleFilter] = useState()
  const [catFilter, setCatFilter] = useState()
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

  // 出现过的模块（键的第一段），给筛选器用
  const moduleOptions = useMemo(() => {
    const set = new Set()
    rows.forEach((r) => { const m = moduleOf(r); if (m) set.add(m) })
    return [...set].sort().map((m) => ({ value: m, label: m }))
  }, [rows])

  const visibleRows = useMemo(
    () => rows.filter((r) => (!moduleFilter || moduleOf(r) === moduleFilter)
                          && (!catFilter || r.category === catFilter)),
    [rows, moduleFilter, catFilter])

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
    form.setFieldsValue({ source: 'manual' })
    setModalOpen(true)
  }

  const openEdit = (r) => {
    setEditing(r)
    form.setFieldsValue({
      keyText: r.keyText,
      module: r.module || undefined,
      source: r.source || 'manual',
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
      module: values.module || null,
      source: values.source || 'manual',
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
      width: 330,
      render: (v) => (
        <>
          <Text style={{ fontFamily: 'var(--font-mono)', fontSize: 12, wordBreak: 'break-all' }}>{v}</Text>
          {/[\u4e00-\u9fff]/.test(v) && (
            <Tag color="orange" style={{ marginLeft: 6, fontSize: 11 }}>键不该用中文</Tag>
          )}
        </>
      ),
    },
    {
      title: '模块',
      dataIndex: 'module',
      width: 100,
      render: (v) => v ? <Text style={{ fontSize: 12 }}>{v}</Text> : <Text type="secondary">—</Text>,
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
      render: (v) => v
        ? <Tag color={CATEGORY_COLOR[v] || 'default'}>{CATEGORY_LABEL[v] || v}</Tag>
        : <Text type="secondary">—</Text>,
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
          <Col span={8}><Statistic title="已翻译 (en)" value={stats.translated} valueStyle={{ color: '#0ea5a0' }} /></Col>
          <Col span={8}><Statistic title="待补" value={stats.untranslated} valueStyle={{ color: '#faad14' }} /></Col>
        </Row>
      </Card>

      <Card
        title={<span><TranslationOutlined /> 文案词条</span>}
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} size="small" onClick={load}>刷新</Button>
            <Select allowClear size="small" style={{ width: 122 }} placeholder="按模块筛选"
              value={moduleFilter} onChange={setModuleFilter} options={moduleOptions} />
            <Select allowClear size="small" style={{ width: 122 }} placeholder="按分类筛选"
              value={catFilter} onChange={setCatFilter}
              options={[...new Set(rows.map((r) => r.category).filter(Boolean))]
                .map((c) => ({ value: c, label: CATEGORY_LABEL[c] || c }))} />
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
          dataSource={visibleRows}
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
              style={{ fontFamily: 'var(--font-mono)' }}
              onChange={(e) => {
                // 模块还空着就按键的第一段带一个默认值。**只在空的时候带** ——
                // 人已经改过了就不要再覆盖回去。
                if (!form.getFieldValue('module')) {
                  const g = guessModule(e.target.value)
                  if (g) form.setFieldsValue({ module: g })
                }
              }} />
          </Form.Item>
          {/* **列表上有的字段，弹窗里必须都能看到 —— 新建和编辑都要。**
              缺了就是「列表看到一个值、进去找不着」，这条被指出过三次。
              不可编辑的也要摆出来并说明为什么，不能干脆不显示。
              （反过来也成立：一个**改不了又没法维护**的派生值，压根不该出现在列表上 ——
                「模块」原来是一列，已经删了，改成上方的筛选器。） */}
          <Form.Item name="module" label="模块"
            tooltip="这条文案属于哪个一级菜单。新建时按键的第一段自动带一个，可以改。">
            <Select allowClear showSearch placeholder="如 服务管理"
              options={[...new Set([...Object.values(NS_LABEL), ...rows.map((r) => r.module).filter(Boolean)])]
                .sort().map((m) => ({ value: m, label: m }))} />
          </Form.Item>
          <Form.Item name="source" label="来源"
            tooltip="这条词从哪来。分错了可以改 —— 列表上看得到却改不了，比能改更让人困惑。">
            <Select options={[
              { value: 'manual', label: '手工录入' },
              { value: 'sut_locale', label: '从被测系统 locale 导入' },
              { value: 'harvested', label: '扫脚本采集（历史）' },
            ]} />
          </Form.Item>
          <Form.Item name="category" label="分类">
            <Select allowClear placeholder="如 按钮 / 校验错误" options={CATEGORY_OPTIONS} />
          </Form.Item>
          <Form.Item name="zh" label="中文 (zh)"
            tooltip="键制下中文是这个键的值之一，不是键本身。TEST_LANGUAGE=zh 时取它。">
            <Input placeholder="如 创建服务" />
          </Form.Item>
          <Form.Item name="en" label="英文 (en)" tooltip="留空表示待补；TEST_LANGUAGE=en 时取它。">
            <Input placeholder="如 Confirm Binding（留空=待补）" />
          </Form.Item>
          <Form.Item name="description" label="说明"
            tooltip="**这条文案在页面上的具体位置**：一级菜单 › 页面 › 区域 › 控件。例：服务管理 › 服务详情页 › 头部「更多」下拉。也可以写它在什么条件下才出现。">
            <Input placeholder="如 服务管理 › 服务详情页 › 头部「更多」下拉" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
