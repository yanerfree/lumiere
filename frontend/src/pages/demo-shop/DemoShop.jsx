import { useState, useEffect, useRef } from 'react'
import {
  Button, Space, Input, Tag, Badge, Table, Modal, message, Tooltip, Drawer,
  Select, InputNumber, Popconfirm, Empty, Alert, Descriptions, Statistic, Typography,
} from 'antd'
import {
  PlayCircleOutlined, PauseCircleOutlined, ReloadOutlined, CopyOutlined,
  PlusOutlined, DeleteOutlined, ShoppingCartOutlined, QuestionCircleOutlined,
  ClearOutlined, ExportOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'

const MONO = 'var(--font-mono)'

// 状态配色和后端 STATUS_LABELS 一一对应；后端加了状态这里要跟着加，
// 不然新状态会显示成灰色无名标签（不会报错，所以容易漏）
const STATUS_META = {
  pending: { label: '待支付', color: 'orange' },
  paid: { label: '已支付', color: 'blue' },
  shipped: { label: '已发货', color: 'purple' },
  completed: { label: '已完成', color: 'green' },
  cancelled: { label: '已取消', color: 'default' },
}

// 每个状态下还能点什么。和后端 ALLOWED_TRANSITIONS 同源 ——
// 这里少一条只是按钮不出现，多一条会让人点出 409。
const NEXT_ACTIONS = {
  pending: [
    { key: 'pay', label: '支付', type: 'primary' },
    { key: 'cancel', label: '取消', danger: true },
  ],
  paid: [
    { key: 'ship', label: '发货', type: 'primary' },
    { key: 'cancel', label: '取消', danger: true },
  ],
  shipped: [{ key: 'complete', label: '完成', type: 'primary' }],
  completed: [],
  cancelled: [],
}

export default function DemoShop() {
  const [status, setStatus] = useState({ running: false, port: 29000, stats: null, accounts: [] })
  const [orders, setOrders] = useState([])
  const [ordersTotal, setOrdersTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [keyword, setKeyword] = useState('')
  const [products, setProducts] = useState([])
  const [logs, setLogs] = useState([])
  const [tab, setTab] = useState('orders')
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [detail, setDetail] = useState(null)
  const [form, setForm] = useState({ customerName: '', customerPhone: '', remark: '', items: [{ sku: '', quantity: 1 }] })
  const pollRef = useRef(null)

  useEffect(() => {
    refreshAll()
    pollRef.current = setInterval(fetchStatus, 5000)
    return () => clearInterval(pollRef.current)
  }, [])

  useEffect(() => { fetchOrders() }, [page, statusFilter])

  const fetchStatus = async () => {
    try { const r = await api.get('/demo-shop/status'); setStatus(r.data || r) } catch {}
  }
  const fetchOrders = async () => {
    setLoading(true)
    try {
      const q = new URLSearchParams({ page: String(page), pageSize: '20' })
      if (statusFilter) q.set('status', statusFilter)
      if (keyword.trim()) q.set('keyword', keyword.trim())
      const r = await api.get(`/demo-shop/orders?${q}`)
      setOrders(r.data || [])
      setOrdersTotal(r.total ?? 0)
    } catch { message.error('订单读取失败') } finally { setLoading(false) }
  }
  const fetchProducts = async () => {
    try { const r = await api.get('/demo-shop/products?pageSize=100'); setProducts(r.data || []) } catch {}
  }
  const fetchLogs = async () => {
    try { const r = await api.get('/demo-shop/logs?limit=200'); setLogs((r.data || r)?.data || r.data || []) } catch {}
  }
  const refreshAll = () => { fetchStatus(); fetchOrders(); fetchProducts(); fetchLogs() }

  const handleStartStop = async () => {
    try {
      if (status.running) { await api.post('/demo-shop/stop'); message.success('订单服务已停止') }
      else {
        const r = await api.post('/demo-shop/start')
        if (r.ok === false) { message.error(`启动失败：${r.error || '端口可能被占用'}`); return }
        message.success(`订单服务已启动，端口 ${r.port}`)
      }
      setTimeout(fetchStatus, 400)
    } catch (e) { message.error('操作失败') }
  }

  const handleCreate = async () => {
    const items = form.items.filter(i => i.sku)
    if (!form.customerName.trim()) { message.warning('请填客户名'); return }
    if (!items.length) { message.warning('至少选一件商品'); return }
    try {
      await api.post('/demo-shop/orders', { ...form, customerName: form.customerName.trim(), items })
      message.success('已下单，库存已扣')
      setCreateOpen(false)
      setForm({ customerName: '', customerPhone: '', remark: '', items: [{ sku: '', quantity: 1 }] })
      refreshAll()
    } catch (e) {
      message.error(e?.response?.data?.error || '下单失败')
    }
  }

  const doAction = async (orderNo, action) => {
    try {
      await api.post(`/demo-shop/orders/${orderNo}/${action}`)
      message.success('操作成功')
      refreshAll()
    } catch (e) { message.error(e?.response?.data?.error || '操作失败') }
  }

  const doDelete = async (orderNo) => {
    try {
      await api.delete(`/demo-shop/orders/${orderNo}`)
      message.success('已删除')
      refreshAll()
    } catch (e) { message.error(e?.response?.data?.error || '删除失败') }
  }

  const doReset = async () => {
    try {
      const r = await api.post('/demo-shop/reset')
      const d = r.data || r
      message.success(`已重置：清掉 ${d.deletedOrders} 条订单，商品回到出厂值`)
      refreshAll()
    } catch { message.error('重置失败') }
  }

  const baseUrl = `http://${window.location.hostname}:${status.port}`
  const s = status.stats || {}

  const orderColumns = [
    {
      title: '订单号', dataIndex: 'orderNo', width: 160,
      render: (v, r) => <a style={{ fontFamily: MONO, fontSize: 12 }} onClick={() => setDetail(r)}>{v}</a>,
    },
    { title: '客户', dataIndex: 'customerName', width: 120 },
    {
      title: '状态', dataIndex: 'status', width: 90,
      render: v => <Tag color={STATUS_META[v]?.color}>{STATUS_META[v]?.label || v}</Tag>,
    },
    {
      title: '金额', dataIndex: 'totalAmount', width: 110, align: 'right',
      render: v => <span style={{ fontFamily: MONO }}>¥{v}</span>,
    },
    {
      title: '商品', dataIndex: 'items', width: 200, ellipsis: true,
      render: items => (items || []).map(i => `${i.productName}×${i.quantity}`).join('，'),
    },
    {
      title: '下单时间', dataIndex: 'createdAt', width: 150,
      render: v => <span style={{ fontSize: 12, color: '#86909c' }}>{v ? new Date(v).toLocaleString('zh-CN') : '-'}</span>,
    },
    {
      title: '操作', key: 'act', width: 210, fixed: 'right',
      render: (_, r) => (
        <Space size={4}>
          {(NEXT_ACTIONS[r.status] || []).map(a => (
            <Popconfirm key={a.key} title={`确认${a.label}？`} onConfirm={() => doAction(r.orderNo, a.key)}>
              <Button size="small" type={a.type} danger={a.danger}>{a.label}</Button>
            </Popconfirm>
          ))}
          <Popconfirm title="删除这条订单？" description="待支付/已支付的会把库存还回去" onConfirm={() => doDelete(r.orderNo)}>
            <Button size="small" icon={<DeleteOutlined />} danger />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const productColumns = [
    { title: 'SKU', dataIndex: 'sku', width: 110, render: v => <span style={{ fontFamily: MONO, fontSize: 12 }}>{v}</span> },
    { title: '商品名', dataIndex: 'name' },
    { title: '分类', dataIndex: 'category', width: 90 },
    { title: '单价', dataIndex: 'price', width: 100, align: 'right', render: v => <span style={{ fontFamily: MONO }}>¥{v}</span> },
    {
      title: '库存', dataIndex: 'stock', width: 90, align: 'right',
      render: v => <span style={{ fontFamily: MONO, color: v > 0 ? '#1d2129' : '#f53f3f' }}>{v}</span>,
    },
    {
      title: '状态', dataIndex: 'active', width: 90,
      render: v => v ? <Tag color="green">在售</Tag> : <Tag>已下架</Tag>,
    },
  ]

  const logColumns = [
    { title: '时间', dataIndex: 'ts', width: 160, render: v => <span style={{ fontSize: 12, color: '#86909c' }}>{new Date(v).toLocaleString('zh-CN')}</span> },
    { title: '方法', dataIndex: 'method', width: 70, render: v => <Tag style={{ fontFamily: MONO, fontSize: 11 }}>{v}</Tag> },
    { title: '路径', dataIndex: 'path', ellipsis: true, render: v => <span style={{ fontFamily: MONO, fontSize: 12 }}>{v}</span> },
    {
      title: '状态码', dataIndex: 'status', width: 80,
      render: v => <Tag color={v < 300 ? 'green' : v < 500 ? 'orange' : 'red'} style={{ fontFamily: MONO }}>{v}</Tag>,
    },
    { title: '耗时', dataIndex: 'durationMs', width: 80, align: 'right', render: v => <span style={{ fontFamily: MONO, fontSize: 12 }}>{v}ms</span> },
    { title: '调用人', dataIndex: 'actor', width: 90 },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 70px)' }}>
      <div className="lum-page-strip" style={{ padding: '8px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <ShoppingCartOutlined style={{ fontSize: 18, color: '#0ea5a0' }} />
          <span style={{ fontWeight: 600, fontSize: 16 }}>订单服务</span>
          <Tag color="cyan" style={{ fontSize: 11 }}>真后端·会落库</Tag>
          <Badge status={status.running ? 'success' : 'default'} text={
            <span style={{ fontSize: 12, color: status.running ? '#0ea5a0' : '#86909c' }}>
              {status.running ? `LIVE :${status.port}` : 'STOPPED'}
            </span>
          } />
          {s.orders != null && <Tag style={{ fontSize: 11 }}>{s.orders} 条订单</Tag>}
        </div>
        <Space size={8}>
          <Button size="small" icon={<QuestionCircleOutlined />} onClick={() => setHelpOpen(true)}>怎么用</Button>
          <Tooltip title="复制服务地址，填到测试环境的 BASE_URL">
            <Button size="small" icon={<CopyOutlined />} onClick={() => { copyToClipboard(baseUrl); message.success('已复制') }}>
              {baseUrl}
            </Button>
          </Tooltip>
          {status.running && (
            <Button size="small" icon={<ExportOutlined />} onClick={() => window.open(`${baseUrl}/docs`, '_blank')}>接口文档</Button>
          )}
          <Button size="small" icon={<ReloadOutlined />} onClick={refreshAll}>刷新</Button>
          <Button size="small" type={status.running ? 'default' : 'primary'} danger={status.running}
            icon={status.running ? <PauseCircleOutlined /> : <PlayCircleOutlined />} onClick={handleStartStop}>
            {status.running ? '停止服务' : '启动服务'}
          </Button>
        </Space>
      </div>

      {!status.running && (
        <Alert type="info" showIcon style={{ margin: '10px 10px 0', fontSize: 12 }}
          message="服务没启动时，这一页的订单照样能增删改（走平台自己的接口）；但用例里填的那个地址打不通，要点右上角「启动服务」。" />
      )}

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, padding: 10, gap: 10 }}>
        <div style={{ display: 'flex', gap: 10, flexShrink: 0 }}>
          {[
            { t: '订单总数', v: s.orders ?? '-' },
            { t: '待支付', v: s.ordersByStatus?.pending ?? '-' },
            { t: '已完成', v: s.ordersByStatus?.completed ?? '-' },
            { t: '已收金额', v: s.revenue != null ? `¥${s.revenue}` : '-' },
            { t: '缺货商品', v: s.outOfStock ?? '-' },
          ].map(c => (
            <div key={c.t} style={{ flex: 1, background: 'var(--panel-bg)', borderRadius: 14, padding: '10px 14px', boxShadow: '0 2px 12px rgba(0,0,0,0.04)' }}>
              <Statistic title={c.t} value={c.v} valueStyle={{ fontSize: 20 }} />
            </div>
          ))}
        </div>

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: 'var(--panel-bg)', backdropFilter: 'blur(16px)', borderRadius: 16, boxShadow: '0 2px 12px rgba(0,0,0,0.04)', minHeight: 0 }}>
          <div style={{ display: 'flex', borderBottom: '1px solid rgba(0,0,0,0.04)', padding: '0 16px', gap: 4, alignItems: 'center' }}>
            {['orders', 'products', 'logs'].map(k => (
              <div key={k} onClick={() => { setTab(k); if (k === 'logs') fetchLogs(); if (k === 'products') fetchProducts() }}
                style={{
                  padding: '8px 14px', fontSize: 13, cursor: 'pointer', fontWeight: tab === k ? 600 : 400,
                  color: tab === k ? '#0ea5a0' : '#86909c',
                  borderBottom: tab === k ? '2px solid #0ea5a0' : '2px solid transparent',
                }}>
                {{ orders: '订单', products: '商品', logs: '请求日志' }[k]}
              </div>
            ))}
            <div style={{ flex: 1 }} />
            {tab === 'orders' && (
              <Space size={6}>
                <Input.Search size="small" placeholder="订单号 / 客户名" style={{ width: 180 }} allowClear
                  value={keyword} onChange={e => setKeyword(e.target.value)} onSearch={() => { setPage(1); fetchOrders() }} />
                <Select size="small" style={{ width: 110 }} value={statusFilter} onChange={v => { setStatusFilter(v); setPage(1) }}
                  options={[{ value: '', label: '全部状态' }, ...Object.entries(STATUS_META).map(([k, v]) => ({ value: k, label: v.label }))]} />
                <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => { fetchProducts(); setCreateOpen(true) }}>新建订单</Button>
                <Popconfirm title="重置样例数据" description="会清空所有订单，商品恢复出厂库存。确定？" onConfirm={doReset} okButtonProps={{ danger: true }}>
                  <Button size="small" danger icon={<ClearOutlined />}>重置样例数据</Button>
                </Popconfirm>
              </Space>
            )}
            {tab === 'logs' && (
              <Button size="small" icon={<ClearOutlined />} onClick={async () => { await api.delete('/demo-shop/logs'); fetchLogs() }}>清空日志</Button>
            )}
          </div>

          <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
            {tab === 'orders' && (
              <Table size="small" rowKey="orderNo" loading={loading} columns={orderColumns} dataSource={orders}
                scroll={{ x: 1040 }}
                pagination={{ current: page, pageSize: 20, total: ordersTotal, onChange: setPage, showTotal: t => `共 ${t} 条` }}
                locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有订单，点右上角「新建订单」" /> }} />
            )}
            {tab === 'products' && (
              <Table size="small" rowKey="sku" columns={productColumns} dataSource={products} pagination={false} />
            )}
            {tab === 'logs' && (
              <Table size="small" rowKey="id" columns={logColumns} dataSource={logs} pagination={{ pageSize: 30 }}
                expandable={{
                  expandedRowRender: r => (
                    <div style={{ fontFamily: MONO, fontSize: 12 }}>
                      <div style={{ color: '#86909c', marginBottom: 4 }}>请求内容</div>
                      <pre style={{ margin: 0, whiteSpace: 'pre-wrap', maxHeight: 160, overflow: 'auto' }}>{r.requestBody || '（无请求体）'}</pre>
                      <div style={{ color: '#86909c', margin: '8px 0 4px' }}>返回内容</div>
                      <pre style={{ margin: 0, whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto' }}>{r.responseSnippet || '（无）'}</pre>
                    </div>
                  ),
                }}
                locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="这里只记 29000 端口上收到的请求。在这个页面上点按钮走的是平台接口，不会出现在这儿。" /> }} />
            )}
          </div>
        </div>
      </div>

      {/* 新建订单 */}
      <Modal open={createOpen} title="新建订单" onCancel={() => setCreateOpen(false)} onOk={handleCreate} okText="下单" width={620}>
        <div style={{ display: 'flex', gap: 10, marginBottom: 10 }}>
          <Input placeholder="客户名（必填）" value={form.customerName} onChange={e => setForm(f => ({ ...f, customerName: e.target.value }))} />
          <Input placeholder="手机号" value={form.customerPhone} onChange={e => setForm(f => ({ ...f, customerPhone: e.target.value }))} />
        </div>
        {form.items.map((it, idx) => (
          <div key={idx} style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
            <Select style={{ flex: 1 }} placeholder="选商品" value={it.sku || undefined} showSearch optionFilterProp="label"
              onChange={v => setForm(f => ({ ...f, items: f.items.map((x, i) => i === idx ? { ...x, sku: v } : x) }))}
              options={products.map(p => ({
                value: p.sku,
                label: `${p.name}　¥${p.price}　库存 ${p.stock}${p.active ? '' : '（已下架）'}`,
              }))} />
            <InputNumber min={1} max={99} value={it.quantity}
              onChange={v => setForm(f => ({ ...f, items: f.items.map((x, i) => i === idx ? { ...x, quantity: v || 1 } : x) }))} />
            <Button icon={<DeleteOutlined />} disabled={form.items.length === 1}
              onClick={() => setForm(f => ({ ...f, items: f.items.filter((_, i) => i !== idx) }))} />
          </div>
        ))}
        <Button size="small" icon={<PlusOutlined />} onClick={() => setForm(f => ({ ...f, items: [...f.items, { sku: '', quantity: 1 }] }))}>再加一行</Button>
        <Input.TextArea style={{ marginTop: 10 }} rows={2} placeholder="备注" value={form.remark}
          onChange={e => setForm(f => ({ ...f, remark: e.target.value }))} />
        <Alert type="info" showIcon style={{ marginTop: 10, fontSize: 12 }}
          message="下单会真的扣库存。选了已下架或库存不够的商品，会像真系统一样报错，不会放过去。" />
      </Modal>

      {/* 订单详情 */}
      <Modal open={!!detail} title={detail?.orderNo} footer={null} onCancel={() => setDetail(null)} width={620}>
        {detail && (
          <>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="客户">{detail.customerName}</Descriptions.Item>
              <Descriptions.Item label="手机">{detail.customerPhone || '-'}</Descriptions.Item>
              <Descriptions.Item label="状态"><Tag color={STATUS_META[detail.status]?.color}>{STATUS_META[detail.status]?.label}</Tag></Descriptions.Item>
              <Descriptions.Item label="金额">¥{detail.totalAmount}</Descriptions.Item>
              <Descriptions.Item label="下单人">{detail.createdBy}</Descriptions.Item>
              <Descriptions.Item label="备注">{detail.remark || '-'}</Descriptions.Item>
            </Descriptions>
            <Table style={{ marginTop: 12 }} size="small" rowKey="sku" pagination={false} dataSource={detail.items}
              columns={[
                { title: '商品', dataIndex: 'productName' },
                { title: '单价', dataIndex: 'unitPrice', width: 90, align: 'right', render: v => `¥${v}` },
                { title: '数量', dataIndex: 'quantity', width: 70, align: 'right' },
                { title: '小计', dataIndex: 'subtotal', width: 100, align: 'right', render: v => `¥${v}` },
              ]} />
          </>
        )}
      </Modal>

      {/* 使用说明 */}
      <Drawer open={helpOpen} onClose={() => setHelpOpen(false)} width={560} title="订单服务怎么用">
        <Typography.Paragraph>
          这是平台自带的一个<b>真的小后端</b>，专门用来练手和演示：下单会真扣库存，发过货的订单取消不了，
          删订单真的删掉。和隔壁的 Mock 不一样 —— Mock 是你配什么它答什么，这个是自己有规矩的。
        </Typography.Paragraph>

        <Typography.Title level={5}>一、先把服务启动</Typography.Title>
        <Typography.Paragraph>
          点右上角「启动服务」，变成绿色 LIVE 就行。服务地址：
          <Typography.Text code copyable style={{ fontFamily: MONO }}>{baseUrl}</Typography.Text>
          <br />把这个地址填到「项目设置 → 环境变量」的 BASE_URL，用例就能打它了。
        </Typography.Paragraph>

        <Typography.Title level={5}>二、账号</Typography.Title>
        <Typography.Paragraph>
          先调 <Typography.Text code>POST /api/login</Typography.Text> 拿 token，之后每个请求带
          <Typography.Text code>Authorization: Bearer &lt;token&gt;</Typography.Text>。
        </Typography.Paragraph>
        <Table size="small" pagination={false} rowKey="username" dataSource={status.accounts || []}
          columns={[
            { title: '账号', dataIndex: 'username', render: v => <span style={{ fontFamily: MONO }}>{v}</span> },
            { title: '密码', dataIndex: 'password', render: v => <span style={{ fontFamily: MONO }}>{v}</span> },
            { title: '能干什么', dataIndex: 'role', render: v => v === 'admin' ? '全部，含删订单 / 重置数据' : '下单、发货，不能删订单' },
          ]} />

        <Typography.Title level={5} style={{ marginTop: 16 }}>三、有哪些接口</Typography.Title>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          完整的在 <a onClick={() => window.open(`${baseUrl}/docs`, '_blank')}>{baseUrl}/docs</a>（服务启动后可点）。常用这几条：
        </Typography.Paragraph>
        <ul style={{ fontFamily: MONO, fontSize: 12, lineHeight: 2, paddingLeft: 18 }}>
          <li>POST /api/login — 登录拿 token</li>
          <li>GET /api/products — 商品列表</li>
          <li>GET /api/orders — 订单列表（status / keyword / page 可筛）</li>
          <li>POST /api/orders — 下单，会扣库存</li>
          <li>POST /api/orders/&#123;订单号&#125;/pay|ship|complete|cancel — 状态流转</li>
          <li>DELETE /api/orders/&#123;订单号&#125; — 删订单，只有 admin 能删</li>
          <li>GET /api/stats — 统计</li>
        </ul>

        <Typography.Title level={5}>四、它会怎么报错（这些都是故意的）</Typography.Title>
        <ul style={{ fontSize: 13, lineHeight: 2, paddingLeft: 18 }}>
          <li>不带 token → 401；token 伪造/过期 → 401</li>
          <li>店员账号删订单 → 403</li>
          <li>买已下架的商品 → 409；库存不够 → 409（带还剩几件）</li>
          <li>已发货的订单点取消 → 409</li>
          <li>订单号不存在 → 404</li>
          <li>数量填 0 或 100 → 422</li>
        </ul>
        <Alert type="info" showIcon style={{ fontSize: 12 }}
          message="错误返回长这样：{ code: 'OUT_OF_STOCK', message: '库存不足：…' }。断言写 code，别断言中文 —— 文案会改，code 不会。" />

        <Typography.Title level={5} style={{ marginTop: 16 }}>五、数据弄乱了怎么办</Typography.Title>
        <Typography.Paragraph>
          「订单」页右上角「重置样例数据」：清空所有订单，商品库存回到出厂值。商品编号（SKU-001 这些）不会变，
          所以写好的用例重置之后照样能跑。
        </Typography.Paragraph>
      </Drawer>
    </div>
  )
}
