/**
 * 代理观测 —— 验证「配了出站代理，请求是否真的走了代理」。
 *
 * 使用流程（全程在页面上完成，不用回终端）：
 *   点「清零」 -> 切到被测系统点一下按钮 -> 切回本页看有没有新记录。
 *   有 = 走代理了；没有 = 没走。
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Button, Space, Tag, Switch, Input, InputNumber, Tooltip, Typography,
  Popconfirm, Alert, Drawer, Spin, Tabs, App as AntApp
} from 'antd'
import {
  ReloadOutlined, ClearOutlined, CopyOutlined, PlayCircleOutlined,
  PauseCircleOutlined, ThunderboltOutlined, FileTextOutlined
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'

const { Text } = Typography
const MONO = 'var(--font-mono)'

// 形态用颜色区分：CONNECT 隧道(Node/undici) vs 转发(Go/http.Transport)
const KIND_COLOR = {
  CONNECT: { bg: 'rgba(124,92,191,0.1)', fg: '#7c5cbf', bd: 'rgba(124,92,191,0.3)' },
  GET: { bg: 'rgba(14,165,160,0.1)', fg: '#0ea5a0', bd: 'rgba(14,165,160,0.25)' },
  POST: { bg: 'rgba(255,125,0,0.1)', fg: '#d46b08', bd: 'rgba(255,125,0,0.3)' },
  PUT: { bg: 'rgba(78,138,240,0.1)', fg: '#0958d9', bd: '#91caff' },
  DELETE: { bg: 'rgba(232,69,60,0.1)', fg: '#cf1322', bd: '#ffccc7' },
}
const kindStyle = (k) => KIND_COLOR[k] || { bg: 'rgba(0,0,0,0.03)', fg: '#86909c', bd: '#c9cdd4' }

function KindTag({ kind }) {
  const s = kindStyle(kind)
  return (
    <span style={{
      display: 'inline-block', minWidth: 74, textAlign: 'center', padding: '1px 8px',
      borderRadius: 6, fontSize: 12, fontWeight: 700, fontFamily: MONO,
      background: s.bg, color: s.fg, border: `1px solid ${s.bd}`,
    }}>{kind}</span>
  )
}

// 明细抽屉里的一段报文
function Block({ title, sub, content, empty, onCopy }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>{title}</span>
        {sub && <Text type="secondary" style={{ fontSize: 12 }}>{sub}</Text>}
        <span style={{ flex: 1 }} />
        {content && onCopy && (
          <Button size="small" type="text" icon={<CopyOutlined />}
            style={{ fontSize: 11, color: '#86909c' }} onClick={onCopy}>复制</Button>
        )}
      </div>
      {content
        ? <pre style={{
            margin: 0, padding: 12, borderRadius: 10, maxHeight: 300, overflow: 'auto',
            background: '#1e1e2e', color: '#cdd6f4', fontSize: 12, lineHeight: 1.65,
            fontFamily: MONO, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>{content}</pre>
        : <Text type="secondary" style={{ fontSize: 12 }}>{empty}</Text>}
    </div>
  )
}

function Counter({ label, value, color, hint }) {
  return (
    <Tooltip title={hint}>
      <div style={{ minWidth: 118 }}>
        <div style={{ fontSize: 12, color: '#86909c', marginBottom: 2 }}>{label}</div>
        <div style={{ fontSize: 34, lineHeight: 1.1, fontWeight: 700, color, fontFamily: MONO }}>
          {value}
        </div>
      </div>
    </Tooltip>
  )
}

// 用 antd 的 <App> 包一层，内部走 App.useApp() 拿 message。
// 直接用静态 message.xxx() 会在控制台报 "Static function can not consume context" 告警。
export default function ProxyProbe() {
  return <AntApp><ProxyProbeInner /></AntApp>
}

function ProxyProbeInner() {
  const { message } = AntApp.useApp()
  const [status, setStatus] = useState(null)
  const [stats, setStats] = useState({ connectCount: 0, httpCount: 0, withAuthCount: 0, errors: 0 })
  const [records, setRecords] = useState([])
  const [flash, setFlash] = useState({})          // 新记录高亮闪一下
  const [busy, setBusy] = useState(false)
  // 故障注入本地态
  const [rejectAll, setRejectAll] = useState(false)
  const [authOn, setAuthOn] = useState(false)
  const [authUser, setAuthUser] = useState('svc')
  const [authPass, setAuthPass] = useState('')       // 不预填密码
  const [delay, setDelay] = useState(0)
  // 明细抽屉
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const seenIds = useRef(new Set())   // 只用来判断哪些是新记录（高亮闪一下）
  const firstLoad = useRef(true)
  const inFlight = useRef(false)      // 轮询互斥：上一次还没回来就不再发
  const timer = useRef(null)

  // 代理地址取后端探测到的内网 IP，不用 window.location.hostname ——
  // 如果测试人员是从 localhost 打开 testBench 的，那样拼出来会是 http://127.0.0.1:28900，
  // 复制给容器用等于让容器连它自己，日志永远为空，会被误判成「出站代理没生效」。
  const proxyAddr = status?.proxyUrl || ''
  const loopbackOnly = !!status && !status.lanIp

  const poll = useCallback(async () => {
    // 轮询有三个触发源（定时器 / 切回标签页 / 操作后主动刷新），可能叠在一起。
    // 加互斥 + 整体替换，双保险防止同一批记录被拼两遍。
    if (inFlight.current) return
    inFlight.current = true
    try {
      const r = await api.get('/proxy-probe/records?limit=200')
      const d = r.data || r
      setStatus(d)
      setStats(d.stats || {})
      // 后端按 id 升序回；页面要最新的在最上面。
      // **整体替换，不往已有数组里追加** —— 追加会因并发轮询产生重复行。
      const list = (d.records || []).slice().reverse()
      setRecords(list)
      // 新记录判定用后端给的 id，不用时间戳（同一秒可能有多条）
      const ids = new Set(list.map(x => x.id))
      if (firstLoad.current) {
        firstLoad.current = false
      } else {
        const fresh = list.filter(x => !seenIds.current.has(x.id)).map(x => x.id)
        if (fresh.length) {
          setFlash(Object.fromEntries(fresh.map(id => [id, true])))
          setTimeout(() => setFlash({}), 1600)
        }
      }
      seenIds.current = ids
    } catch { /* 轮询失败不弹窗，避免切标签页回来一屏报错 */ }
    finally { inFlight.current = false }
  }, [])

  useEffect(() => {
    poll()
    timer.current = setInterval(poll, 1000)   // 轮询 1 秒，够了，不用 WebSocket
    // 后台标签页里定时器会被浏览器降频，切回来时立刻补一次，保证「切回来就能看到」
    const onVisible = () => { if (!document.hidden) poll() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { clearInterval(timer.current); document.removeEventListener('visibilitychange', onVisible) }
  }, [poll])

  useEffect(() => {
    if (!status?.injection) return
    setRejectAll(status.injection.rejectAll)
    setAuthOn(status.injection.authOn)
    setDelay(status.injection.delay || 0)
    if (status.injection.authUser) setAuthUser(status.injection.authUser)
  }, [status?.injection?.rejectAll, status?.injection?.authOn, status?.injection?.delay])

  const doReset = async () => {
    try {
      await api.post('/proxy-probe/reset')
      // 只清「见过的 id」；不要重置 firstLoad ——
      // 清零后列表本来是空的，不存在首屏全闪的问题，
      // 重置反而会把清零后第一条记录的高亮吞掉，而那条最该被注意到。
      seenIds.current = new Set()
      setRecords([])
      setStats({ connectCount: 0, httpCount: 0, withAuthCount: 0, errors: 0 })
      message.success('已清零，现在去被测系统触发一次请求')
      poll()
    } catch { message.error('清零失败') }
  }

  const toggleService = async () => {
    setBusy(true)
    try {
      await api.post(status?.running ? '/proxy-probe/stop' : '/proxy-probe/start')
      await poll()
      message.success(status?.running ? '已停止监听' : '已启动监听')
    } catch (e) {
      message.error('操作失败：' + (e?.response?.data?.detail || e.message || '未知错误'))
    } finally { setBusy(false) }
  }

  const openDetail = async (row) => {
    setDetail({ ...row })           // 先用列表已有的字段把抽屉撑开，再补明细
    setDetailLoading(true)
    try {
      const r = await api.get(`/proxy-probe/records/${row.id}`)
      setDetail(r.data || r)
    } catch {
      message.error('取明细失败（可能已被「清零」清掉）')
    } finally { setDetailLoading(false) }
  }

  const copy = (text, label) => { copyToClipboard(text); message.success('已复制' + label) }

  const pushInject = async (patch) => {
    try {
      await api.post('/proxy-probe/inject', patch)
      poll()
    } catch { message.error('故障注入设置失败') }
  }

  const total = (stats.connectCount || 0) + (stats.httpCount || 0)
  const running = !!status?.running

  return (
    <div style={{ padding: 20, maxWidth: 1200 }}>
      {/* ---------- 顶部：状态 + 代理地址 ---------- */}
      <div style={{
        background: 'rgba(255,255,255,0.55)', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 14,
        padding: '16px 20px', marginBottom: 14,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 17, fontWeight: 700 }}>代理观测</span>
          <Tag color={running ? 'green' : 'default'} style={{ marginInlineEnd: 0 }}>
            {running ? '● 运行中' : '○ 已停止'}
          </Tag>
          {status && (
            <Text type="secondary" style={{ fontFamily: MONO, fontSize: 12 }}>
              监听 {status.host}:{status.port}
            </Text>
          )}
          <span style={{ flex: 1 }} />
          <Button size="small" icon={running ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
            loading={busy} onClick={toggleService}>
            {running ? '停止' : '启动'}
          </Button>
          <Button size="small" icon={<ReloadOutlined />} onClick={poll}>刷新</Button>
        </div>

        <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>代理地址（复制给被测系统填「出站代理」）</Text>
          <Input spellCheck={false} readOnly value={proxyAddr} style={{ width: 300, fontFamily: MONO }} size="small" />
          <Button size="small" icon={<CopyOutlined />}
            onClick={() => { copyToClipboard(proxyAddr); message.success('已复制：' + proxyAddr) }}>
            复制
          </Button>
          {status?.logFile && (
            <Tooltip title={'日志文件照写，事后追溯用：' + status.logFile}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                <FileTextOutlined /> 日志文件
              </Text>
            </Tooltip>
          )}
        </div>
        {!running && (
          <Alert style={{ marginTop: 12 }} type="warning" showIcon
            title="监听未启动，任何请求都不会被记录 —— 先点上面的「启动」" />
        )}
        {loopbackOnly && (
          <Alert style={{ marginTop: 12 }} type="error" showIcon
            title="没探测到内网 IP，上面的地址可能是回环地址"
            description="容器里的 127.0.0.1 是容器自己，填了它请求打不到这里，表现就是本页永远没有记录 —— 请手动换成本机内网 IP。" />
        )}
      </div>

      {/* ---------- 计数区 + 清零 ---------- */}
      <div style={{
        background: 'rgba(255,255,255,0.55)', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 14,
        padding: '18px 20px', marginBottom: 14,
        display: 'flex', alignItems: 'center', gap: 40, flexWrap: 'wrap',
      }}>
        <Counter label="总请求数" value={total} color="#141414"
          hint="经过本代理的请求总数。清零后仍是 0，说明请求没走代理" />
        <Counter label="CONNECT 隧道" value={stats.connectCount || 0} color="#7c5cbf"
          hint="CONNECT 形态，Node.js / undici 那条链路" />
        <Counter label="转发 (absolute-URI)" value={stats.httpCount || 0} color="#0ea5a0"
          hint="absolute-URI 形态，Go net/http Transport 那条链路" />
        <Counter label="带认证" value={stats.withAuthCount || 0} color="#0958d9"
          hint="带了 Proxy-Authorization 的请求数" />
        <Counter label="失败" value={stats.errors || 0} color="#cf1322"
          hint="代理收到了请求但没转发成功的次数" />
        <span style={{ flex: 1 }} />
        <Popconfirm title="清零计数并清空记录？" description="用于在一次测试前打基线" onConfirm={doReset}
          okText="清零" cancelText="取消">
          {/* 原来是 48px 高、16px 字的大红实心块，比全站任何按钮都重一档，
              在这个页面里比「停止 / 刷新」抢眼太多。收回常规尺寸，
              危险语义靠 danger 的红色表达就够了。 */}
          <Button type="primary" danger icon={<ClearOutlined />}
            style={{ fontWeight: 500, paddingInline: 18 }}>
            清零
          </Button>
        </Popconfirm>
      </div>

      {/* ---------- 工具区：故障注入 ---------- */}
      <div style={{
        background: 'rgba(255,255,255,0.55)', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 14,
        padding: '14px 20px', marginBottom: 14,
      }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
          <ThunderboltOutlined style={{ color: '#ff7d00' }} /> 故障注入
          <Text type="secondary" style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
            实时生效，不用重启
          </Text>
        </div>
        <Space size={28} wrap>
          <Space size={8}>
            <Switch checked={rejectAll} size="small"
              onChange={v => { setRejectAll(v); pushInject({ rejectAll: v }) }} />
            <Tooltip title="所有请求立即断开。用于验证代理不可达时，被测系统是否明确报错，而不是假成功或无限等待">
              {/* 点文字也能切，别让人对着小开关瞄 */}
              <span style={{ fontSize: 13, cursor: 'pointer', userSelect: 'none' }}
                onClick={() => { const v = !rejectAll; setRejectAll(v); pushInject({ rejectAll: v }) }}>
                拒绝所有请求
              </span>
            </Tooltip>
          </Space>

          <Space size={8}>
            <Switch checked={authOn} size="small"
              onChange={v => {
                setAuthOn(v)
                pushInject({ authRequired: v ? `${authUser}:${authPass}` : '' })
              }} />
            <Tooltip title="凭证缺失或错误就返回 407。这是最硬的断言：把「凭证传对了吗」从看日志猜，变成传错就连不上">
              <span style={{ fontSize: 13, cursor: 'pointer', userSelect: 'none' }}
                onClick={() => {
                  const v = !authOn
                  setAuthOn(v)
                  pushInject({ authRequired: v ? `${authUser}:${authPass}` : '' })
                }}>
                强制认证
              </span>
            </Tooltip>
            <Input size="small" style={{ width: 110 }} placeholder="用户名" value={authUser}
              disabled={!authOn}
              onChange={e => setAuthUser(e.target.value)}
              onBlur={() => authOn && pushInject({ authRequired: `${authUser}:${authPass}` })} />
            <Input.Password size="small" style={{ width: 130 }} placeholder="密码" value={authPass}
              disabled={!authOn}
              onChange={e => setAuthPass(e.target.value)}
              onBlur={() => authOn && pushInject({ authRequired: `${authUser}:${authPass}` })} />
          </Space>

          <Space size={8}>
            <Tooltip title="转发前延迟指定秒数。用于验证被测系统的超时与重试逻辑">
              <span style={{ fontSize: 13 }}>延迟</span>
            </Tooltip>
            <InputNumber size="small" min={0} max={120} step={1} style={{ width: 74 }}
              value={delay}
              onChange={v => setDelay(v || 0)}
              onBlur={() => pushInject({ delay })} />
            <span style={{ fontSize: 13, color: '#86909c' }}>秒</span>
          </Space>
        </Space>
      </div>

      {/* ---------- 实时请求列表 ---------- */}
      <div style={{ background: 'rgba(255,255,255,0.55)', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 14, overflow: 'hidden' }}>
        <div style={{
          padding: '12px 20px', borderBottom: '1px solid #f5f5f5',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>实时请求</span>
          <Text type="secondary" style={{ fontSize: 12 }}>每秒自动刷新，最新的在最上面</Text>
          <span style={{ flex: 1 }} />
          <Text type="secondary" style={{ fontSize: 12 }}>{records.length} 条</Text>
        </div>

        {records.length === 0 ? (
          <div style={{ padding: '52px 20px', textAlign: 'center' }}>
            <div style={{ fontSize: 16, color: '#4e5969', marginBottom: 10 }}>等待请求…</div>
            <div style={{ fontSize: 13, color: '#86909c', lineHeight: 2 }}>
              现在去被测系统页面触发一次请求，这里会实时显示。<br />
              <span style={{ color: '#d4380d', fontWeight: 600 }}>
                如果操作完这里仍然是空的，说明请求没有走代理。
              </span>
            </div>
          </div>
        ) : (
          <div style={{ maxHeight: 520, overflow: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: 'rgba(0,0,0,0.03)', color: '#86909c', fontSize: 12 }}>
                  <th style={{ textAlign: 'left', padding: '8px 12px', width: 88 }}>时间</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px', width: 96 }}>形态</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px' }}>目标地址</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px', width: 150 }}>认证</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px', width: 280 }}>结果</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px', width: 76 }}></th>
                </tr>
              </thead>
              <tbody>
                {records.map(r => (
                  <tr key={r.id} data-rec-id={r.id} onClick={() => openDetail(r)} style={{
                    borderTop: '1px solid #f5f5f5',
                    background: flash[r.id] ? '#fffbe6' : 'transparent',
                    transition: 'background 1.2s ease',
                    cursor: 'pointer',
                  }}>
                    <td style={{ padding: '8px 12px', fontFamily: MONO, color: '#4e5969' }}>{r.time}</td>
                    <td style={{ padding: '8px 12px' }}><KindTag kind={r.kind} /></td>
                    <td style={{ padding: '8px 12px', fontFamily: MONO, wordBreak: 'break-all' }}>
                      {r.target}
                    </td>
                    <td style={{ padding: '8px 12px' }}>
                      {r.auth
                        ? <Tooltip title={`Proxy-Authorization: ${r.authRaw || '?'}　→　${r.user}:${r.password}`}>
                            <Tag color="blue" style={{ fontFamily: MONO, cursor: 'help' }}>
                              user={r.user || '?'}
                            </Tag>
                          </Tooltip>
                        : <Tag>no-auth</Tag>}
                    </td>
                    <td style={{ padding: '8px 12px' }}>
                      {r.ok === true && <Text style={{ color: '#0ea5a0' }}>成功{r.reason ? ' · ' + r.reason : ''}</Text>}
                      {r.ok === false && <Text style={{ color: '#cf1322' }}>失败 · {r.reason}</Text>}
                      {r.ok === null && <Text type="secondary">进行中…</Text>}
                    </td>
                    <td style={{ padding: '8px 12px' }}>
                      <Button size="small" type="link" style={{ fontSize: 12, padding: 0 }}
                        onClick={e => { e.stopPropagation(); openDetail(r) }}>看报文</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ---------- 明细抽屉：按「两跳」分开，别把收到的和转发出去的混在一起 ---------- */}
      {/* antd v6 里 Drawer 的 width 已弃用，改用 size 预设 */}
      <Drawer open={!!detail} size="large" onClose={() => setDetail(null)}
        title={detail
          ? <Space size={8}>
              <KindTag kind={detail.kind} />
              <span style={{ fontFamily: MONO, fontSize: 13 }}>{detail.target}</span>
              <Text type="secondary" style={{ fontSize: 12 }}>{detail.time}</Text>
            </Space>
          : ''}>
        {detail && (
          <Spin spinning={detailLoading}>
            <div style={{ marginBottom: 14 }}>
              {detail.ok === false
                ? <Alert type="error" showIcon title={'失败 · ' + (detail.reason || '')} />
                : <Alert type="success" showIcon title={'成功 · ' + (detail.reason || '')} />}
            </div>

            {/* 链路一目了然：谁 -> 代理 -> 谁 */}
            <div style={{
              background: 'rgba(0,0,0,0.03)', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 10,
              padding: '10px 14px', marginBottom: 16, fontSize: 12, fontFamily: MONO,
              display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
            }}>
              <Tag color="geekblue" style={{ fontFamily: MONO }}>客户端 {detail.client || '?'}</Tag>
              <span style={{ color: '#86909c' }}>──▶</span>
              <Tag color="purple" style={{ fontFamily: MONO }}>代理 :{status?.port}</Tag>
              <span style={{ color: '#86909c' }}>──▶</span>
              <Tag color="green" style={{ fontFamily: MONO }}>上游 {detail.target}</Tag>
            </div>

            {detail.auth && (
              <div style={{ marginBottom: 16, fontSize: 12, lineHeight: 2 }}>
                <span style={{ color: '#86909c' }}>凭证解码（base64 肉眼看不出内容，这里解开给你核对）：</span>
                <div style={{ fontFamily: MONO, marginTop: 4 }}>
                  用户名 <Tag color="blue">{detail.user ?? '(解析失败)'}</Tag>
                  密码 <Tag color="blue">{detail.password ?? '(解析失败)'}</Tag>
                  <Button size="small" type="link" style={{ fontSize: 12 }}
                    onClick={() => copy(`${detail.user}:${detail.password}`, '凭证')}>复制 user:pass</Button>
                </div>
              </div>
            )}

            <Tabs size="small" items={[
              {
                key: 'c2p',
                label: '① 客户端 ⇆ 代理',
                children: (
                  <>
                    <Block
                      title="客户端发给代理的请求" sub="原样，未做任何删改"
                      content={detail.c2pRequest}
                      empty="没抓到（可能连请求行都没解析出来）"
                      onCopy={() => copy(detail.c2pRequest, '客户端请求')} />
                    {detail.kind !== 'CONNECT' && (
                      <Block
                        title="请求体" sub="最多 4KB，只旁抄不缓冲"
                        content={detail.c2pReqBody} empty="无请求体"
                        onCopy={() => copy(detail.c2pReqBody, '请求体')} />
                    )}
                    <Block
                      title="代理回给客户端的应答" sub="这一跳客户端最终看到的东西"
                      content={detail.p2cResponse}
                      empty="没有回应答（被立即断开，或还没到应答那一步）"
                      onCopy={() => copy(detail.p2cResponse, '代理应答')} />
                    {detail.p2cNote && (
                      <div style={{ marginTop: -10, marginBottom: 16, fontSize: 12, color: '#d4380d' }}>
                        {detail.p2cNote}
                      </div>
                    )}
                  </>
                ),
              },
              {
                key: 'p2u',
                label: '② 代理 ⇆ 上游',
                children: (
                  <>
                    <Block
                      title="代理发给上游的请求"
                      sub={detail.kind === 'CONNECT' ? 'CONNECT 不发 HTTP 请求' : '改写后'}
                      content={detail.p2uRequest}
                      empty="没有转发（在连上游之前就被拒绝/失败了）"
                      onCopy={() => copy(detail.p2uRequest, '转发请求')} />

                    {detail.kind !== 'CONNECT' && (
                      <div style={{ marginTop: -10, marginBottom: 16, fontSize: 12, color: '#86909c', lineHeight: 1.9 }}>
                        跟 ① 里的请求对比就能确认两件事：请求行有没有从 <code>absolute-URI</code>
                        改写成 <code>origin-form</code>（不改，规范上游会回 400）；逐跳头有没有剥掉。
                        {detail.stripped?.length
                          ? <div>本次剥掉的逐跳头：{detail.stripped.map(h => (
                              <Tag key={h} color="orange" style={{ fontFamily: MONO, marginTop: 4 }}>{h}</Tag>))}</div>
                          : <div>本次没有需要剥的逐跳头。</div>}
                      </div>
                    )}

                    <Block
                      title="上游回给代理的响应" sub="状态行 + 响应头"
                      content={detail.u2pResponse}
                      empty={detail.kind === 'CONNECT'
                        ? 'CONNECT 只建 TCP 连接，这一跳没有 HTTP 响应'
                        : '没抓到响应（上游没回，或连上游就失败了）'}
                      onCopy={() => copy(detail.u2pResponse, '上游响应头')} />
                    {detail.kind !== 'CONNECT' && (
                      <Block
                        title="响应体" sub="最多 4KB，只旁抄不缓冲"
                        content={detail.u2pRespBody} empty="无响应体"
                        onCopy={() => copy(detail.u2pRespBody, '响应体')} />
                    )}
                  </>
                ),
              },
              ...(detail.kind === 'CONNECT' ? [{
                key: 'tunnel',
                label: '③ 隧道内数据' + (detail.tunnelUpKind === 'tls' ? '（TLS 加密）' : ''),
                children: (
                  <>
                    <div style={{ marginBottom: 14 }}>
                      <Alert type={detail.tunnelUpKind === 'tls' ? 'warning' : 'info'} showIcon
                        title={
                          detail.tunnelUpKind === 'tls'
                            ? '隧道内是 TLS 加密流量（已确认，不是猜的）'
                            : detail.tunnelUpKind === 'http'
                              ? '隧道内是明文 HTTP，能直接看到内容'
                              : '隧道内数据类型见下'}
                        description="判定依据：TLS record 的首字节固定是 0x16（handshake）+ 版本 0x03xx。下面把原始字节的十六进制也列出来，可自行核对。" />
                    </div>
                    <Block
                      title="上行（客户端 → 上游）" sub={'类型：' + (detail.tunnelUpKind || '-')}
                      content={detail.tunnelUp} empty="没有上行数据"
                      onCopy={() => copy(detail.tunnelUp, '上行数据')} />
                    <Block
                      title="下行（上游 → 客户端）" sub={'类型：' + (detail.tunnelDownKind || '-')}
                      content={detail.tunnelDown} empty="没有下行数据"
                      onCopy={() => copy(detail.tunnelDown, '下行数据')} />
                  </>
                ),
              }] : []),
            ]} />
          </Spin>
        )}
      </Drawer>

      <div style={{ marginTop: 12, fontSize: 12, color: '#86909c', lineHeight: 1.9 }}>
        判读方式：<b>列表里有记录 = 走了代理；清零后操作完仍然是空的 = 没走代理。</b>
        点任意一行可看两跳报文：<b>① 客户端 ⇆ 代理</b>（别人给代理的）、<b>② 代理 ⇆ 上游</b>（代理转发出去的），CONNECT 另有 <b>③ 隧道内数据</b>。
        形态列区分链路 —— <span style={{ color: '#7c5cbf' }}>CONNECT</span> 是 Node.js / undici 那条，
        <span style={{ color: '#0ea5a0' }}>GET/POST</span> 是 Go net/http 那条。
        报文原样显示，不做删改；凭证在明细里会解码出用户名和密码，方便核对被测系统送的对不对。
      </div>
    </div>
  )
}
