/**
 * 节点池测试 —— 起几个假的大模型节点，往网关发一批请求，看每条落到了哪个节点。
 *
 * 用法：① 起节点，把每个节点的地址填进网关的节点池；② 填网关地址发一批；
 * ③ 看每个节点接了几条。把某个节点切成「报错/变慢」再发一批，就能看网关怎么躲开它。
 *
 * 页面上填的设置只记在本浏览器（localStorage），不落库 —— 用户 2026-11-04 选的口径。
 * 假节点本身是后端全局一份：两个人同时用会互相改到对方的节点。
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Button, Space, Tag, Input, InputNumber, Select, Checkbox, Radio, Typography,
  Alert, Drawer, Table, Progress, Tooltip, Popconfirm, App as AntApp,
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, CopyOutlined, SendOutlined, StopOutlined,
  PlayCircleOutlined, PauseCircleOutlined, ClearOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'

const { Text } = Typography
const MONO = 'var(--font-mono)'
const STORE_KEY = 'nodePoolTester.v1'
const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

const DEFAULT_NODES = ['A', 'B', 'C'].map(id => ({ id, name: `节点${id}`, mode: 'normal', slowSeconds: 5 }))
const DEFAULT_SEND = {
  targetUrl: '', model: 'nodepool-fake', headers: [{ key: 'Authorization', value: '', enabled: true }],
  count: 30, concurrency: 1, timeoutSeconds: 60, stream: false,
  sessionMode: 'none', sessionPlace: 'uag_metadata', sessionField: 'session_id', sessionValue: '会话-001',
  systemPrompt: '', userPrompt: '你好', autoNumber: true,
}
const MODE_OPTIONS = [
  { value: 'normal', label: '正常' },
  { value: 'error', label: '报错(500)' },
  { value: 'slow', label: '变慢' },
  { value: 'ready_503', label: '仅 /ready 返回 503' },
  { value: 'stream_break', label: '流式中途出错' },
]
const MODE_HINT = {
  normal: '正常回答',
  error: '每次都回 500，健康检查照常报好',
  slow: '等几秒再回答',
  ready_503: '对话照常回答，只有 /ready 健康检查报不可用',
  stream_break: '流式回答说到一半断开；非流式照常回答',
}

function loadSaved() {
  try {
    const s = JSON.parse(localStorage.getItem(STORE_KEY))
    return {
      nodes: Array.isArray(s?.nodes) && s.nodes.length ? s.nodes : DEFAULT_NODES,
      send: { ...DEFAULT_SEND, ...(s?.send || {}) },
    }
  } catch {
    return { nodes: DEFAULT_NODES, send: DEFAULT_SEND }
  }
}

const panel = {
  background: 'var(--panel-bg)', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 14,
  padding: '16px 20px', marginBottom: 14,
}
const Title = ({ n, children, extra }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
    <span style={{ fontSize: 14, fontWeight: 700 }}>{n} {children}</span>
    <span style={{ flex: 1 }} />
    {extra}
  </div>
)
const Field = ({ label, children, width }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginRight: 18, marginBottom: 8, width }}>
    <Text style={{ fontSize: 13, whiteSpace: 'nowrap' }}>{label}</Text>{children}
  </span>
)

export default function NodePool() {
  const { message } = AntApp.useApp()
  const saved = useRef(loadSaved()).current
  const [nodes, setNodes] = useState(saved.nodes)      // 页面上的节点清单（记在浏览器）
  const [send, setSend] = useState(saved.send)
  const [status, setStatus] = useState(null)            // 后端真实状态
  const [run, setRun] = useState(null)
  const [detail, setDetail] = useState(null)
  const [applying, setApplying] = useState(false)
  const [sending, setSending] = useState(false)
  const applyTimer = useRef(null)

  useEffect(() => {
    localStorage.setItem(STORE_KEY, JSON.stringify({ nodes, send }))
  }, [nodes, send])

  const live = Object.fromEntries((status?.nodes || []).map(n => [n.id, n]))
  const anyRunning = (status?.nodes || []).some(n => n.running)

  const refreshStatus = useCallback(async () => {
    try {
      const s = await api.get('/node-pool/status', { silent: true })
      setStatus(s)
      return s
    } catch { return null }
  }, [])

  const refreshRun = useCallback(async () => {
    try {
      const r = await api.get('/node-pool/runs/current', { silent: true })
      setRun(r)
      return r
    } catch { return null }
  }, [])

  // 首次进来：后端已经有节点在跑 → 以后端为准（端口、名字带回来）
  useEffect(() => {
    (async () => {
      const s = await refreshStatus()
      await refreshRun()
      if (s?.nodes?.length) {
        setNodes(s.nodes.map(n => ({ id: n.id, name: n.name, mode: n.mode, slowSeconds: n.slowSeconds, port: n.port })))
      }
    })()
  }, [refreshStatus, refreshRun])

  // 轮询：发送中 1 秒一次，平时 3 秒刷一下节点计数
  useEffect(() => {
    const active = run?.running
    const t = setInterval(() => {
      refreshStatus()
      if (active) refreshRun()
    }, active ? 1000 : 3000)
    return () => clearInterval(t)
  }, [run?.running, refreshStatus, refreshRun])

  const applyNodes = useCallback(async (list) => {
    setApplying(true)
    try {
      const s = await api.put('/node-pool/nodes', {
        nodes: list.map(n => ({ id: n.id, name: n.name, mode: n.mode, slowSeconds: n.slowSeconds ?? 5, port: n.port })),
      })
      setStatus(s)
      // 端口以后端分配为准，记下来，下次还用同一个（网关里不用重填）
      const ports = Object.fromEntries(s.nodes.map(n => [n.id, n.port]))
      setNodes(cur => cur.map(n => (ports[n.id] ? { ...n, port: ports[n.id] } : n)))
      if (s.errors?.length) message.error(s.errors.join('；'))
    } catch { /* 已提示 */ } finally {
      setApplying(false)
    }
  }, [message])

  // 改了节点：正在跑就立刻同步给后端（改名字的输入框防抖一下）
  const changeNodes = (list, { debounce = false } = {}) => {
    setNodes(list)
    if (!anyRunning) return
    clearTimeout(applyTimer.current)
    if (debounce) applyTimer.current = setTimeout(() => applyNodes(list), 600)
    else applyNodes(list)
  }
  const patchNode = (id, patch, opts) => changeNodes(nodes.map(n => (n.id === id ? { ...n, ...patch } : n)), opts)

  const addNode = () => {
    const used = new Set(nodes.map(n => n.id))
    const id = [...LETTERS].find(l => !used.has(l)) || Math.random().toString(16).slice(2, 6)
    changeNodes([...nodes, { id, name: `节点${id}`, mode: 'normal', slowSeconds: 5 }])
  }

  const stopAll = async () => {
    try { setStatus(await api.post('/node-pool/stop-all')) } catch { /* 已提示 */ }
  }

  const resetCounters = async () => {
    try { setStatus(await api.post('/node-pool/reset-counters')) } catch { /* 已提示 */ }
  }

  const setS = (patch) => setSend(s => ({ ...s, ...patch }))

  const doSend = async () => {
    if (!send.targetUrl.trim()) { message.warning('先填「发送地址」'); return }
    setSending(true)
    try {
      const r = await api.post('/node-pool/runs', { ...send, headers: send.headers.filter(h => h.key.trim()) })
      setRun(r)
    } catch { /* 已提示 */ } finally {
      setSending(false)
    }
  }
  const stopSend = async () => {
    try { await api.post('/node-pool/runs/current/stop'); refreshRun() } catch { /* 已提示 */ }
  }

  const openDetail = async (seq) => {
    try { setDetail(await api.get(`/node-pool/runs/current/rows/${seq}`)) } catch { /* 已提示 */ }
  }

  const host = status?.host || window.location.hostname
  const addrOf = (n) => (live[n.id]?.url || (n.port ? `http://${host}:${n.port}` : '启动后分配'))

  // ─────────── ① 假节点 ───────────
  const nodePanel = (
    <div style={panel}>
      <Title n="①" extra={
        <Space>
          {anyRunning ? (
            <Popconfirm title="把所有假节点都停掉？" description="网关那边会发现这些节点全连不上" onConfirm={stopAll}
              okText="停掉" cancelText="取消">
              <Button size="small" icon={<PauseCircleOutlined />}>停止全部节点</Button>
            </Popconfirm>
          ) : (
            <Button size="small" type="primary" icon={<PlayCircleOutlined />} loading={applying}
              onClick={() => applyNodes(nodes)}>启动节点</Button>
          )}
          <Tooltip title="把每个节点「收到几次」「健康检查几次」归零">
            <Button size="small" icon={<ClearOutlined />} onClick={resetCounters} disabled={!anyRunning}>计数清零</Button>
          </Tooltip>
        </Space>
      }>
        假节点（模拟机器坏了 / 变慢）
        <Text type="secondary" style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
          把下面每个节点的地址填进网关的节点池里；状态随时切换，立刻生效
        </Text>
      </Title>
      {!anyRunning && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          title="节点还没启动，网关现在找不到它们 —— 先点右上角「启动节点」" />
      )}
      <Table size="small" rowKey="id" pagination={false} dataSource={nodes} columns={[
        { title: '节点名', width: 150, render: (_, n) => (
          <Input size="small" value={n.name} maxLength={30} style={{ width: 130 }}
            onChange={e => patchNode(n.id, { name: e.target.value }, { debounce: true })} />
        ) },
        { title: '状态', width: 260, render: (_, n) => (
          <Space size={6}>
            <Tooltip title={MODE_HINT[n.mode]}>
              <Select size="small" style={{ width: 160 }} value={n.mode} options={MODE_OPTIONS}
                onChange={v => patchNode(n.id, { mode: v })} />
            </Tooltip>
            {n.mode === 'slow' && (
              <InputNumber size="small" min={0} max={300} value={n.slowSeconds ?? 5} style={{ width: 80 }}
                suffix="秒" onChange={v => patchNode(n.id, { slowSeconds: v ?? 0 }, { debounce: true })} />
            )}
          </Space>
        ) },
        { title: '地址（填进网关）', render: (_, n) => (
          <Space size={4}>
            <Text style={{ fontFamily: MONO, fontSize: 12 }}>{addrOf(n)}</Text>
            {(live[n.id]?.url || n.port) && (
              <Button size="small" type="text" icon={<CopyOutlined />}
                onClick={() => { copyToClipboard(addrOf(n)); message.success('已复制：' + addrOf(n)) }} />
            )}
          </Space>
        ) },
        { title: '运行', width: 90, render: (_, n) => {
          const l = live[n.id]
          if (l?.running) return <Tag color="green">● 运行中</Tag>
          if (l?.startError) return <Tooltip title={l.startError}><Tag color="red">起不来</Tag></Tooltip>
          return <Tag>○ 未运行</Tag>
        } },
        { title: <Tooltip title="收到的对话请求次数，含报错和网关重试的">收到几次</Tooltip>, width: 90,
          render: (_, n) => live[n.id]?.received ?? '-' },
        { title: <Tooltip title="网关来问 /ready、/health 的次数。一直是 0 说明网关没在做健康检查">健康检查</Tooltip>,
          width: 90, render: (_, n) => live[n.id]?.probes ?? '-' },
        { title: '', width: 50, render: (_, n) => (
          <Button size="small" type="text" danger icon={<DeleteOutlined />} disabled={nodes.length <= 1}
            onClick={() => changeNodes(nodes.filter(x => x.id !== n.id))} />
        ) },
      ]} />
      <Button size="small" icon={<PlusOutlined />} style={{ marginTop: 10 }} onClick={addNode}
        disabled={nodes.length >= (status?.maxNodes || 30)}>加一个节点</Button>
    </div>
  )

  // ─────────── ② 发请求 ───────────
  const headerRows = send.headers
  const setHeader = (i, patch) => setS({ headers: headerRows.map((h, j) => (j === i ? { ...h, ...patch } : h)) })

  const sendPanel = (
    <div style={panel}>
      <Title n="②">发请求</Title>
      <div>
        <Field label="发送地址">
          <Input value={send.targetUrl} onChange={e => setS({ targetUrl: e.target.value })} style={{ width: 460 }}
            placeholder="网关的对话地址，例如 http://网关地址/v1/chat/completions" spellCheck={false} />
        </Field>
        <Field label="模型名">
          <Input value={send.model} onChange={e => setS({ model: e.target.value })} style={{ width: 180 }} />
        </Field>
      </div>
      <div style={{ marginBottom: 8 }}>
        <Text style={{ fontSize: 13 }}>请求头</Text>
        <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>密钥之类的填这里，例如 Authorization = Bearer xxx</Text>
        {headerRows.map((h, i) => (
          <div key={i} style={{ display: 'flex', gap: 6, marginTop: 6, alignItems: 'center' }}>
            <Checkbox checked={h.enabled !== false} onChange={e => setHeader(i, { enabled: e.target.checked })} />
            <Input size="small" placeholder="名字" value={h.key} style={{ width: 200 }}
              onChange={e => setHeader(i, { key: e.target.value })} />
            <Input size="small" placeholder="值" value={h.value} style={{ width: 380 }}
              onChange={e => setHeader(i, { value: e.target.value })} />
            <Button size="small" type="text" danger icon={<DeleteOutlined />}
              onClick={() => setS({ headers: headerRows.filter((_, j) => j !== i) })} />
          </div>
        ))}
        <Button size="small" type="dashed" icon={<PlusOutlined />} style={{ marginTop: 6 }}
          onClick={() => setS({ headers: [...headerRows, { key: '', value: '', enabled: true }] })}>加一行</Button>
      </div>
      <div>
        <Field label="发几次"><InputNumber min={1} max={1000} value={send.count} onChange={v => setS({ count: v || 1 })} /></Field>
        <Field label="同时发几个"><InputNumber min={1} max={50} value={send.concurrency} onChange={v => setS({ concurrency: v || 1 })} /></Field>
        <Field label="每条最多等"><InputNumber min={1} max={600} value={send.timeoutSeconds} suffix="秒"
          onChange={v => setS({ timeoutSeconds: v || 60 })} style={{ width: 100 }} /></Field>
        <Checkbox checked={send.stream} onChange={e => setS({ stream: e.target.checked })}>
          流式请求（回答一段段吐出来，stream=true）
        </Checkbox>
      </div>
      <div style={{ marginBottom: 8 }}>
        <Text strong style={{ fontSize: 13, marginRight: 10 }}>会话键：</Text>
        <Radio.Group value={send.sessionMode} onChange={e => setS({ sessionMode: e.target.value })} options={[
          { value: 'none', label: '不带' },
          { value: 'same', label: '每次都带同一个' },
          { value: 'each', label: '每次带不同的' },
        ]} />
      </div>
      {send.sessionMode !== 'none' && (
        <div>
          <Field label="放在">
            <Select value={send.sessionPlace} style={{ width: 230 }} onChange={v => setS({ sessionPlace: v })} options={[
              { value: 'uag_metadata', label: 'metadata（x-uag-metadata 请求头）' },
              { value: 'body_metadata', label: '请求体里的 metadata 字段' },
              { value: 'header', label: '普通请求头' },
            ]} />
          </Field>
          <Field label="字段名"><Input value={send.sessionField} style={{ width: 150 }} onChange={e => setS({ sessionField: e.target.value })} /></Field>
          <Field label={send.sessionMode === 'same' ? '会话值' : '会话值开头'}>
            <Input value={send.sessionValue} style={{ width: 150 }} onChange={e => setS({ sessionValue: e.target.value })} />
          </Field>
        </div>
      )}
      <div style={{ marginTop: 4 }}>
        <Text strong style={{ fontSize: 13 }}>对话内容：</Text>
        <div style={{ marginTop: 6 }}>
          <Text style={{ fontSize: 13 }}>系统提示词（可留空）</Text>
          <Input.TextArea rows={2} value={send.systemPrompt} onChange={e => setS({ systemPrompt: e.target.value })} />
        </div>
        <div style={{ marginTop: 8 }}>
          <Text style={{ fontSize: 13 }}>用户问题</Text>
          <Input.TextArea rows={2} value={send.userPrompt} onChange={e => setS({ userPrompt: e.target.value })} />
        </div>
        <Checkbox style={{ marginTop: 6 }} checked={send.autoNumber} onChange={e => setS({ autoNumber: e.target.checked })}>
          每次在用户问题后面自动加一个不同的编号（让每条问题都不一样）
        </Checkbox>
      </div>
      <div style={{ marginTop: 14, display: 'flex', gap: 10, alignItems: 'center' }}>
        {run?.running ? (
          <Button danger icon={<StopOutlined />} onClick={stopSend}>停止发送</Button>
        ) : (
          <Button type="primary" icon={<SendOutlined />} loading={sending} onClick={doSend}>发送</Button>
        )}
        {!anyRunning && <Text type="warning" style={{ fontSize: 12 }}>假节点没在运行，发出去网关也找不到它们</Text>}
      </div>
    </div>
  )

  // ─────────── ③ 结果 ───────────
  const resultPanel = (
    <div style={panel}>
      <Title n="③">结果：每个节点接了多少条</Title>
      {!run ? <Text type="secondary">还没发</Text> : (
        <>
          <div style={{ marginBottom: 10 }}>
            <Progress percent={Math.round((run.done / run.total) * 100)} size="small" style={{ maxWidth: 420 }}
              status={run.running ? 'active' : (run.failed ? 'exception' : 'success')}
              format={() => `${run.done}/${run.total}`} />
            <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
              发往 <span style={{ fontFamily: MONO }}>{run.targetUrl}</span>
              {run.running ? '，正在发…' : (run.cancelled ? '，已手动停止' : '，发完了')}
            </Text>
          </div>
          {run.sticky && !run.running && run.sessionMode === 'same' && (
            <Alert style={{ marginBottom: 10 }} showIcon type={run.sticky.sameNode ? 'success' : 'warning'}
              title={run.sticky.sameNode
                ? `同一个会话全部落在同一个节点：是（${run.sticky.nodes.join('') || '无成功的'}）`
                : `同一个会话全部落在同一个节点：否，分散到了 ${run.sticky.nodes.join('、')}`} />
          )}
          <Table size="small" pagination={false} rowKey="node" style={{ maxWidth: 560, marginBottom: 10 }}
            dataSource={[
              ...run.perNode,
              ...(run.failed ? [{ node: '失败（没拿到正常回答）', answered: run.failed, received: null, bad: true }] : []),
              ...(run.unknown ? [{ node: '成功但认不出是哪个节点', answered: run.unknown, received: null, bad: true }] : []),
            ]}
            columns={[
              { title: '结果', dataIndex: 'node', render: (v, r) => (r.bad ? <Text type="danger">{v}</Text> : v) },
              { title: <Tooltip title="最终由它给出正常回答的条数">回答了几条</Tooltip>, dataIndex: 'answered', width: 110 },
              { title: <Tooltip title="它收到的次数，包括它报错后网关转给别人的那些">收到几次</Tooltip>,
                dataIndex: 'received', width: 100, render: v => (v == null ? '' : v) },
            ]} />
          {run.retried > 0 && (
            <Text type="secondary" style={{ display: 'block', fontSize: 12, marginBottom: 8 }}>
              有 {run.retried} 条被网关换节点重发过（先到的节点出错，又转给了别的节点）
            </Text>
          )}
          {run.rows.length > 0 && (
            <div style={{ fontSize: 12, color: 'var(--text-secondary, #86909c)', marginBottom: 10, lineHeight: 1.9 }}>
              逐条顺序：{run.rows.map((r, i) => (
                <span key={r.seq}>
                  {i > 0 && ' → '}
                  <a onClick={() => openDetail(r.seq)} style={{ color: r.ok ? undefined : '#cf1322' }}>
                    {r.ok ? (r.node || '认不出') : '失败'}
                  </a>
                </span>
              ))}
            </div>
          )}
          <Table size="small" rowKey="seq" dataSource={run.rows} pagination={{ pageSize: 20, size: 'small' }}
            onRow={r => ({ onClick: () => openDetail(r.seq), style: { cursor: 'pointer' } })}
            columns={[
              { title: '第几条', dataIndex: 'seq', width: 70 },
              { title: '结果', width: 80, render: (_, r) => (r.ok ? <Tag color="green">成功</Tag> : <Tag color="red">失败</Tag>) },
              { title: '谁回答的', dataIndex: 'node', width: 110, render: v => v || '-' },
              { title: '经过的节点', dataIndex: 'via', width: 180, render: v => (v?.length ? v.join(' → ') : <Text type="secondary">没到任何假节点</Text>) },
              { title: '会话值', dataIndex: 'sessionValue', width: 130, render: v => v || '-' },
              { title: '状态码', dataIndex: 'status', width: 80, render: v => v ?? '-' },
              { title: '用时', dataIndex: 'durationMs', width: 90, render: v => (v == null ? '-' : `${v} 毫秒`) },
              { title: '出错原因', dataIndex: 'error', render: v => (v ? <Text type="danger">{v}</Text> : '') },
            ]} />
        </>
      )}
    </div>
  )

  const pre = (v) => (
    <pre style={{ fontFamily: MONO, fontSize: 12, background: 'rgba(0,0,0,0.03)', padding: 10, borderRadius: 8,
      whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: 300, overflow: 'auto', margin: 0 }}>
      {typeof v === 'string' ? v : JSON.stringify(v, null, 2)}
    </pre>
  )
  const Sec = ({ title, children }) => (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>{title}</div>{children}
    </div>
  )

  return (
    <div style={{ padding: 20, maxWidth: 1200 }}>
      <div style={{ marginBottom: 12 }}>
        <span style={{ fontSize: 17, fontWeight: 700 }}>节点池测试</span>
        <Text type="secondary" style={{ fontSize: 12, marginLeft: 10 }}>
          起几个假节点 → 往网关发一批请求 → 看每条落到了哪个节点。页面上填的内容只记在你这个浏览器里。
        </Text>
      </div>
      {nodePanel}
      {sendPanel}
      {resultPanel}

      <Drawer open={!!detail} onClose={() => setDetail(null)} size="large"
        title={detail ? `第 ${detail.seq} 条 · ${detail.ok ? '成功' : '失败'}${detail.node ? ' · ' + detail.node + ' 回答' : ''}` : ''}>
        {detail && (
          <>
            {detail.error && <Alert type="error" showIcon style={{ marginBottom: 12 }} title={detail.error} />}
            <Sec title="经过的节点">
              {detail.nodeReceived?.length
                ? detail.nodeReceived.map((h, i) => (
                  <div key={i} style={{ marginBottom: 4 }}>
                    {i + 1}. {h.node} <Text type="secondary">（{h.path}，节点回的是 {h.status ?? '—'}）</Text>
                  </div>
                ))
                : <Text type="secondary">没有任何假节点收到这一条（网关没转出来，或转给了不在这里的节点）</Text>}
            </Sec>
            <Sec title={`回答（用时 ${detail.durationMs ?? '-'} 毫秒，状态码 ${detail.status ?? '-'}）`}>
              {pre(detail.answer || detail.responseBody || '（空）')}
            </Sec>
            <Sec title="发出去的请求头">{pre(detail.requestHeaders)}</Sec>
            <Sec title="发出去的请求内容">{pre(detail.requestBody)}</Sec>
            {detail.nodeReceived?.length > 0 && (
              <Sec title={`节点实际收到的请求头（${detail.nodeReceived[detail.nodeReceived.length - 1].node}）`}>
                {pre(detail.nodeReceived[detail.nodeReceived.length - 1].headers)}
              </Sec>
            )}
            {detail.nodeReceived?.length > 0 && (
              <Sec title="节点实际收到的请求内容">{pre(detail.nodeReceived[detail.nodeReceived.length - 1].body)}</Sec>
            )}
            <Sec title="网关返回的原文">{pre(detail.responseBody || '（空）')}</Sec>
          </>
        )}
      </Drawer>
    </div>
  )
}
