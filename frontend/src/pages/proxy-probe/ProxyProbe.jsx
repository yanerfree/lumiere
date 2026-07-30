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
  Popconfirm, Alert, Drawer, Spin, App as AntApp
} from 'antd'
import {
  ReloadOutlined, ClearOutlined, CopyOutlined, PlayCircleOutlined,
  PauseCircleOutlined, ThunderboltOutlined, FileTextOutlined
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'

const { Text } = Typography
const MONO = "'SF Mono', Monaco, Menlo, Consolas, monospace"

// 形态用颜色区分：CONNECT 隧道(Node/undici) vs 转发(Go/http.Transport)
const KIND_COLOR = {
  CONNECT: { bg: '#f9f0ff', fg: '#722ed1', bd: '#d3adf7' },
  GET: { bg: '#f6ffed', fg: '#389e0d', bd: '#b7eb8f' },
  POST: { bg: '#fff7e6', fg: '#d46b08', bd: '#ffd591' },
  PUT: { bg: '#e6f4ff', fg: '#0958d9', bd: '#91caff' },
  DELETE: { bg: '#fff1f0', fg: '#cf1322', bd: '#ffccc7' },
}
const kindStyle = (k) => KIND_COLOR[k] || { bg: '#fafafa', fg: '#8c8c8c', bd: '#d9d9d9' }

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
            style={{ fontSize: 11, color: '#8c8c8c' }} onClick={onCopy}>复制</Button>
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
        <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 2 }}>{label}</div>
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
        background: '#fff', border: '1px solid #f0f0f0', borderRadius: 14,
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
          <Input readOnly value={proxyAddr} style={{ width: 300, fontFamily: MONO }} size="small" />
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
        background: '#fff', border: '1px solid #f0f0f0', borderRadius: 14,
        padding: '18px 20px', marginBottom: 14,
        display: 'flex', alignItems: 'center', gap: 40, flexWrap: 'wrap',
      }}>
        <Counter label="总请求数" value={total} color="#141414"
          hint="经过本代理的请求总数。清零后仍是 0，说明请求没走代理" />
        <Counter label="CONNECT 隧道" value={stats.connectCount || 0} color="#722ed1"
          hint="CONNECT 形态，Node.js / undici 那条链路" />
        <Counter label="转发 (absolute-URI)" value={stats.httpCount || 0} color="#389e0d"
          hint="absolute-URI 形态，Go net/http Transport 那条链路" />
        <Counter label="带认证" value={stats.withAuthCount || 0} color="#0958d9"
          hint="带了 Proxy-Authorization 的请求数" />
        <Counter label="失败" value={stats.errors || 0} color="#cf1322"
          hint="代理收到了请求但没转发成功的次数" />
        <span style={{ flex: 1 }} />
        <Popconfirm title="清零计数并清空记录？" description="用于在一次测试前打基线" onConfirm={doReset}
          okText="清零" cancelText="取消">
          <Button type="primary" danger size="large" icon={<ClearOutlined />}
            style={{ height: 48, fontSize: 16, fontWeight: 600, paddingInline: 26 }}>
            清零
          </Button>
        </Popconfirm>
      </div>

      {/* ---------- 工具区：故障注入 ---------- */}
      <div style={{
        background: '#fff', border: '1px solid #f0f0f0', borderRadius: 14,
        padding: '14px 20px', marginBottom: 14,
      }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
          <ThunderboltOutlined style={{ color: '#fa8c16' }} /> 故障注入
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
            <span style={{ fontSize: 13, color: '#8c8c8c' }}>秒</span>
          </Space>
        </Space>
      </div>

      {/* ---------- 实时请求列表 ---------- */}
      <div style={{ background: '#fff', border: '1px solid #f0f0f0', borderRadius: 14, overflow: 'hidden' }}>
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
            <div style={{ fontSize: 16, color: '#595959', marginBottom: 10 }}>等待请求…</div>
            <div style={{ fontSize: 13, color: '#8c8c8c', lineHeight: 2 }}>
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
                <tr style={{ background: '#fafafa', color: '#8c8c8c', fontSize: 12 }}>
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
                    <td style={{ padding: '8px 12px', fontFamily: MONO, color: '#595959' }}>{r.time}</td>
                    <td style={{ padding: '8px 12px' }}><KindTag kind={r.kind} /></td>
                    <td style={{ padding: '8px 12px', fontFamily: MONO, wordBreak: 'break-all' }}>
                      {r.target}
                    </td>
                    <td style={{ padding: '8px 12px' }}>
                      {r.auth
                        ? <Tag color="blue" style={{ fontFamily: MONO }}>user={r.user || '?'}</Tag>
                        : <Tag>no-auth</Tag>}
                    </td>
                    <td style={{ padding: '8px 12px' }}>
                      {r.ok === true && <Text style={{ color: '#389e0d' }}>成功{r.reason ? ' · ' + r.reason : ''}</Text>}
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

      {/* ---------- 明细抽屉：原始请求 / 转发请求 / 上游响应 ---------- */}
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
            <div style={{ marginBottom: 16 }}>
              {detail.ok === false
                ? <Alert type="error" showIcon title={'失败 · ' + (detail.reason || '')} />
                : <Alert type="success" showIcon title={'成功 · ' + (detail.reason || '')} />}
            </div>

            <Block
              title="① 原始请求" sub="客户端 → 代理，原样"
              content={detail.rawRequest}
              empty="没抓到（可能是请求行都没解析出来）"
              onCopy={() => copy(detail.rawRequest, '原始请求')} />

            <Block
              title="② 转发给上游的请求" sub="代理 → 上游，改写后"
              content={detail.forwardedRequest}
              empty="没有转发（在连上游之前就被拒绝/失败了）"
              onCopy={() => copy(detail.forwardedRequest, '转发请求')} />

            {detail.kind !== 'CONNECT' && (
              <div style={{ marginTop: -8, marginBottom: 18, fontSize: 12, color: '#8c8c8c', lineHeight: 1.9 }}>
                对比 ① 和 ② 就能确认两件事：请求行有没有从 <code>absolute-URI</code> 改写成
                <code> origin-form</code>（不改规范上游会回 400）；逐跳头有没有剥掉。
                {detail.stripped?.length
                  ? <div>本次剥掉的逐跳头：{detail.stripped.map(h => (
                      <Tag key={h} color="orange" style={{ fontFamily: MONO, marginTop: 4 }}>{h}</Tag>))}</div>
                  : <div>本次没有需要剥的逐跳头。</div>}
              </div>
            )}

            <Block
              title="③ 上游响应" sub="上游 → 客户端，状态行 + 响应头"
              content={detail.responseHead}
              empty="没抓到响应（上游没回，或连上游就失败了）"
              onCopy={() => copy(detail.responseHead, '响应头')} />

            <Block
              title="请求体预览" sub="最多 4KB，只旁抄不缓冲"
              content={detail.reqBody} empty="无请求体"
              onCopy={() => copy(detail.reqBody, '请求体')} />

            <Block
              title="响应体预览" sub="最多 4KB，只旁抄不缓冲"
              content={detail.respBody} empty="无响应体"
              onCopy={() => copy(detail.respBody, '响应体')} />

            <Alert type="info" showIcon
              title="密码永不记录"
              description="Proxy-Authorization 的值在入库前就被打掉了，只保留解析出的用户名。上面看到的是打码后的内容，不是原文。" />
          </Spin>
        )}
      </Drawer>

      <div style={{ marginTop: 12, fontSize: 12, color: '#8c8c8c', lineHeight: 1.9 }}>
        判读方式：<b>列表里有记录 = 走了代理；清零后操作完仍然是空的 = 没走代理。</b>
        点任意一行可看<b>原始请求 / 转发请求 / 上游响应</b>三段报文。
        形态列区分链路 —— <span style={{ color: '#722ed1' }}>CONNECT</span> 是 Node.js / undici 那条，
        <span style={{ color: '#389e0d' }}>GET/POST</span> 是 Go net/http 那条。
        密码永不显示，只显示用户名。
      </div>
    </div>
  )
}
