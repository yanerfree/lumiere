/**
 * 服务与端口 —— 这套环境里所有监听端口的东西，一屏看完谁在跑、谁没起。
 *
 * 数据来自 GET /api/system/services（进程内服务读 manager 状态，进程外的并发 TCP/HTTP 探测）。
 * 「前端 Web」那行后端看不到，由 utils/serviceList.js 在前端补进「平台核心」组。
 *
 * 只读页：不在这里启停服务。内建 Mock 各自页面有启停按钮（走「去管理」跳过去），
 * 外部长驻服务要跑 shell 脚本，未启动时把命令摆出来给人复制。
 *
 * 配色一律走 styles/global.css 的 CSS 变量（teal 主色 #0ea5a0 + 玻璃拟态面板），
 * 别再引入 antd 默认的 #0ea5a0 / #4e8af0 那套，会和全站的清新空灵风打架。
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Space, Tooltip, Switch, Spin, Empty, App as AntApp } from 'antd'
import { ReloadOutlined, CopyOutlined, ArrowRightOutlined } from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'
import { withFrontendRow } from '../../utils/serviceList'

// 玻璃拟态面板 —— 与 HttpClient / OAuth2Mock / EnvConfig 保持同一套（无边框，靠阴影和模糊分层）
const PANEL = {
  background: 'rgba(255,255,255,0.55)',
  backdropFilter: 'blur(12px)',
  WebkitBackdropFilter: 'blur(12px)',
  borderRadius: 16,
  boxShadow: '0 2px 12px rgba(0,0,0,0.04)',
}

// 状态配色：在跑=主色 teal，没起=中性灰。刻意只用这两档，扫一眼就是"亮的活着"
const STATUS = {
  up: { label: '运行中', fg: 'var(--primary)', dot: 'var(--primary)' },
  down: { label: '未启动', fg: 'var(--text-secondary)', dot: 'var(--gray)' },
  notConfigured: { label: '未配置', fg: 'var(--text-placeholder)', dot: '#e5e6eb' },
}
const st = (s) => STATUS[s] || STATUS.notConfigured

// 类型标签走全站色板：内建=主色，外部长驻=紫，基础依赖=蓝
// （不用橙——橙在这套色板里是警示色，给"基础依赖"上橙会误读成出问题了）
// 底色统一用 10% alpha 而不是色板里的 --*-bg 实色：面板本身是半透明玻璃，
// 实色底（#edf3ff 这种）叠上去几乎看不见，三个标签的轻重会不一致。
const KIND = {
  '内建': { fg: 'var(--primary)', bg: 'rgba(14,165,160,0.10)' },
  '外部长驻': { fg: 'var(--purple)', bg: 'rgba(124,92,191,0.10)' },
  '基础依赖': { fg: 'var(--blue)', bg: 'rgba(78,138,240,0.10)' },
}
const kindStyle = (k) => KIND[k] || { fg: 'var(--text-secondary)', bg: 'var(--gray-bg)' }

function StatusDot({ status, size = 7 }) {
  return (
    <span style={{
      display: 'inline-block', width: size, height: size, borderRadius: '50%',
      background: st(status).dot,
      // 在跑的加一圈光晕，没起的不加 —— 余光扫过去"有光的就是活的"
      boxShadow: status === 'up' ? '0 0 0 3px rgba(14,165,160,0.14)' : 'none',
    }} />
  )
}

function KindTag({ kind }) {
  const s = kindStyle(kind)
  return (
    <span style={{
      display: 'inline-block', padding: '0 9px', borderRadius: 12, fontSize: 12,
      lineHeight: '20px', whiteSpace: 'nowrap', fontWeight: 500, letterSpacing: 0.3,
      background: s.bg, color: s.fg,
    }}>{kind}</span>
  )
}

function Counter({ label, value, color }) {
  return (
    <div style={{ minWidth: 72 }}>
      <div style={{ fontSize: 12, color: 'var(--text-secondary)', letterSpacing: 0.3 }}>{label}</div>
      <div style={{
        fontSize: 26, lineHeight: 1.3, fontWeight: 600, color,
        fontFamily: 'var(--font-mono)',
      }}>{value}</div>
    </div>
  )
}

export default function SystemServices() {
  return <AntApp><SystemServicesInner /></AntApp>
}

function SystemServicesInner() {
  const { message } = AntApp.useApp()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [auto, setAuto] = useState(true)
  const timerRef = useRef(null)

  const load = useCallback(async (showSpin = false) => {
    if (showSpin) setLoading(true)
    try {
      // 本接口直接返回 {summary, groups}，不套 {data} 信封
      setData(await api.get('/system/services'))
    } catch { /* request.js 已弹错误 */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { load(true) }, [load])

  useEffect(() => {
    if (!auto) return
    timerRef.current = setInterval(() => load(false), 10000)
    return () => clearInterval(timerRef.current)
  }, [auto, load])

  const copy = (text) => {
    copyToClipboard(text).then(() => message.success('已复制：' + text)).catch(() => message.error('复制失败'))
  }

  // 「前端 Web」后端看不到，在这里本地补进「平台核心」组（与顶栏胶囊共用同一份逻辑，保证两处数字一致）
  const { groups, summary } = withFrontendRow(data)
  const total = summary?.total || 0
  const upCount = summary?.up || 0
  const downCount = summary?.down || 0
  const ncCount = summary?.notConfigured || 0
  const pct = total ? Math.round((upCount / total) * 100) : 0

  return (
    <div style={{ letterSpacing: 0.3 }}>
      <style>{`
        .svc-row { transition: background 0.2s ease; }
        .svc-row:hover { background: rgba(255,255,255,0.45); }
        .svc-port { transition: opacity 0.2s ease; }
        .svc-port:hover { opacity: 0.65; }
        .svc-hint { transition: background 0.2s ease; }
        .svc-hint:hover { background: #ffeed6; }
      `}</style>

      {/* 概览：标题 + 进度条 + 计数 */}
      <div style={{ ...PANEL, padding: '16px 20px', marginBottom: 12 }}>
        <div style={{
          display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between',
          flexWrap: 'wrap', gap: 16,
        }}>
          <div style={{ flex: 1, minWidth: 320 }}>
            <div style={{
              fontSize: 15, fontWeight: 600, color: 'var(--text-primary)',
              letterSpacing: 0.5, marginBottom: 10,
            }}>服务与端口</div>
            <Space size={30} align="start">
              <Counter label="运行中" value={upCount} color="var(--primary)" />
              <Counter label="未启动" value={downCount}
                color={downCount ? 'var(--orange)' : 'var(--text-placeholder)'} />
              {ncCount > 0 && <Counter label="未配置" value={ncCount} color="var(--text-placeholder)" />}
              <Counter label="总计" value={total} color="var(--text-regular)" />
            </Space>
            {/* 细进度条：不用读数字也能看出"是不是都起来了" */}
            <div style={{
              height: 4, borderRadius: 2, marginTop: 12, maxWidth: 460,
              background: 'rgba(0,0,0,0.05)', overflow: 'hidden',
            }}>
              <div style={{
                width: pct + '%', height: '100%', borderRadius: 2,
                background: 'linear-gradient(90deg, #2ec4b6, #0ea5a0)',
                transition: 'width 0.4s ease',
              }} />
            </div>
          </div>
          <Space size={10}>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>自动刷新</span>
            <Switch size="small" checked={auto} onChange={setAuto} />
            <Button size="small" icon={<ReloadOutlined />} onClick={() => load(true)}>刷新</Button>
          </Space>
        </div>
      </div>

      {loading && !data ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
      ) : !groups.length ? (
        <div style={{ ...PANEL, padding: 40 }}><Empty description="拿不到服务列表" /></div>
      ) : groups.map(group => {
        const groupUp = group.items.filter(i => i.status === 'up').length
        return (
          <div key={group.key} style={{ ...PANEL, padding: '12px 20px 6px', marginBottom: 12 }}>
            {/* 组标题：主色渐变小竖条 + 名称 + 在跑比例 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span style={{
                width: 3, height: 13, borderRadius: 2,
                background: 'linear-gradient(180deg, #2ec4b6, #0ea5a0)',
              }} />
              <span style={{
                fontSize: 13, fontWeight: 600, color: 'var(--text-regular)', letterSpacing: 0.5,
              }}>{group.name}</span>
              <span style={{ fontSize: 12, color: 'var(--text-placeholder)' }}>
                {groupUp}/{group.items.length} 在跑
              </span>
            </div>

            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <tbody>
                {group.items.map(item => {
                  const s = st(item.status)
                  return (
                    <tr key={item.key} className="svc-row"
                      style={{ borderTop: '1px solid rgba(0,0,0,0.03)' }}>
                      {/* 状态 */}
                      <td style={{ padding: '9px 8px 9px 4px', width: 78, whiteSpace: 'nowrap' }}>
                        <StatusDot status={item.status} />
                        <span style={{ marginLeft: 7, fontSize: 12, color: s.fg }}>{s.label}</span>
                      </td>
                      {/* 名称 */}
                      <td style={{
                        padding: '9px 8px', width: 148, fontWeight: 500,
                        color: item.status === 'up' ? 'var(--text-primary)' : 'var(--text-secondary)',
                      }}>{item.name}</td>
                      {/* 端口 —— 全页最该被一眼看到的东西 */}
                      <td style={{ padding: '9px 8px', width: 86 }}>
                        {item.port ? (
                          <Tooltip title={item.url ? `点击复制 ${item.url}` : '点击复制端口'}>
                            <span className="svc-port" onClick={() => copy(item.url || String(item.port))}
                              style={{
                                fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 600,
                                cursor: 'pointer', letterSpacing: 0.5,
                                color: item.status === 'up' ? 'var(--primary)' : 'var(--text-placeholder)',
                              }}>:{item.port}</span>
                          </Tooltip>
                        ) : <span style={{ color: 'var(--text-placeholder)' }}>—</span>}
                      </td>
                      {/* 地址 */}
                      <td style={{ padding: '9px 8px', width: 248, whiteSpace: 'nowrap' }}>
                        <span style={{
                          fontSize: 12, fontFamily: 'var(--font-mono)',
                          // 整行随状态一起变淡，否则没起的服务地址还是深色，行内轻重不齐
                          color: item.status === 'up' ? 'var(--text-secondary)' : 'var(--text-placeholder)',
                        }}>{item.url || '—'}</span>
                      </td>
                      {/* 类型 */}
                      <td style={{ padding: '9px 8px', width: 82 }}><KindTag kind={item.kind} /></td>
                      {/* 说明 / 没起时给启动办法 */}
                      <td style={{ padding: '9px 8px', color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.7 }}>
                        {item.desc}
                        {item.status !== 'up' && item.startHint && (
                          <div style={{ marginTop: 4 }}>
                            <span style={{ color: 'var(--orange)', marginRight: 5 }}>启动方式</span>
                            <span className="svc-hint" onClick={() => copy(item.startHint)}
                              style={{
                                fontFamily: 'var(--font-mono)', fontSize: 12, cursor: 'pointer',
                                background: 'var(--orange-bg)', borderRadius: 8,
                                padding: '2px 8px', color: '#c25e00',
                              }}>
                              {item.startHint} <CopyOutlined style={{ fontSize: 11, opacity: 0.6 }} />
                            </span>
                          </div>
                        )}
                      </td>
                      {/* 操作 */}
                      <td style={{ padding: '9px 0', width: 82, textAlign: 'right' }}>
                        {item.manageUrl && (
                          <Button type="link" size="small" style={{ fontSize: 12, padding: 0 }}
                            onClick={() => navigate(item.manageUrl)}>
                            去管理 <ArrowRightOutlined style={{ fontSize: 11 }} />
                          </Button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )
      })}
    </div>
  )
}
