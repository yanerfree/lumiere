/**
 * 顶栏服务状态胶囊 —— 任何页面都能一眼看到"环境里几个服务在跑"。
 *
 * 17 个服务全铺到顶栏会挤爆，所以这里只给汇总（● 服务 17/17），
 * hover 出 Popover 展开分组明细，点它跳 /settings/services 看全表。
 *
 * 轮询 30s（详情页是 10s）：顶栏只要能反映"有东西挂了"就够，没必要更勤。
 * 配色跟全站一致走 CSS 变量：全好=主色 teal，有挂的=橙，拿不到=灰。
 */
import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Popover } from 'antd'
import { api } from '../utils/request'
import { withFrontendRow } from '../utils/serviceList'

const DOT = { up: '#0ea5a0', down: '#c9cdd4', notConfigured: '#e5e6eb' }

// 胶囊三态。与顶栏其它元素同高（22），走 pill 圆角，和全站按钮的 20px 圆角呼应
const TONE = {
  ok: { dot: '#0ea5a0', fg: '#0ea5a0', bg: 'rgba(14,165,160,0.10)' },
  warn: { dot: '#ff7d00', fg: '#e07000', bg: 'rgba(255,125,0,0.10)' },
  unknown: { dot: '#c9cdd4', fg: '#86909c', bg: 'rgba(0,0,0,0.035)' },
}

function Dot({ status, size = 6 }) {
  return (
    <span style={{
      display: 'inline-block', width: size, height: size, borderRadius: '50%',
      background: DOT[status] || DOT.notConfigured,
      boxShadow: status === 'up' ? '0 0 0 2.5px rgba(14,165,160,0.14)' : 'none',
    }} />
  )
}

export default function ServiceStatusBadge() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [failed, setFailed] = useState(false)
  const timerRef = useRef(null)

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        // 顶栏是常驻轮询，失败不该弹全局 message 刷屏，所以走 silent
        const res = await api.get('/system/services', { silent: true })
        if (!alive) return
        setData(res); setFailed(false)
      } catch {
        if (alive) setFailed(true)
      }
    }
    load()
    timerRef.current = setInterval(load, 30000)
    return () => { alive = false; clearInterval(timerRef.current) }
  }, [])

  // 与详情页共用补全逻辑，否则顶栏 16/17、页面 17，两处数字对不上
  const { groups, summary } = withFrontendRow(data)
  // 未配置的不算"挂了"（没接网关不该报橙），只有 down 才算异常
  const bad = summary?.down || 0
  const tone = failed || !summary ? TONE.unknown : bad === 0 ? TONE.ok : TONE.warn
  const label = failed || !summary ? '服务 —' : `服务 ${summary.up}/${summary.total}`

  const content = (
    <div style={{ width: 316, letterSpacing: 0.3 }}>
      {/* 只让清单滚动，"查看全部"钉在底部 —— 17 项撑满时它会被挤出可视区 */}
      {/* 620 ≈ 17 项 + 4 个组标题的实测高度(~575)再留点余量，常规屏幕不用滚；矮屏靠 vh 退化成滚动 */}
      <div style={{ maxHeight: 'min(74vh, 620px)', overflow: 'auto' }}>
        {groups.map(g => (
          <div key={g.key} style={{ marginBottom: 10 }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4,
            }}>
              <span style={{
                width: 2.5, height: 11, borderRadius: 2,
                background: 'linear-gradient(180deg, #2ec4b6, #0ea5a0)',
              }} />
              <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 500 }}>
                {g.name}
              </span>
              <span style={{ fontSize: 12, color: 'var(--text-placeholder)' }}>
                {g.items.filter(i => i.status === 'up').length}/{g.items.length}
              </span>
            </div>
            {g.items.map(i => (
              <div key={i.key} style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '3.5px 4px 3.5px 9px', fontSize: 12, borderRadius: 8,
              }}>
                <Dot status={i.status} />
                <span style={{
                  flex: 1,
                  color: i.status === 'up' ? 'var(--text-primary)' : 'var(--text-placeholder)',
                }}>{i.name}</span>
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600, letterSpacing: 0.5,
                  color: i.status === 'up' ? 'var(--primary)' : 'var(--text-placeholder)',
                }}>{i.port ? ':' + i.port : '—'}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
      <div
        onClick={() => navigate('/settings/services')}
        style={{
          borderTop: '1px solid rgba(0,0,0,0.05)', paddingTop: 8, marginTop: 2,
          fontSize: 12, color: 'var(--primary)', cursor: 'pointer', textAlign: 'center',
          fontWeight: 500,
        }}>
        查看全部 →
      </div>
    </div>
  )

  return (
    <Popover content={content} title={null} placement="bottomRight"
      styles={{ container: { padding: '12px 14px' } }}>
      <div
        onClick={() => navigate('/settings/services')}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
          height: 22, padding: '0 10px', borderRadius: 11,
          background: tone.bg, transition: 'background 0.2s ease',
        }}>
        <span style={{
          display: 'inline-block', width: 6, height: 6, borderRadius: '50%',
          background: tone.dot,
        }} />
        <span style={{
          fontSize: 12, color: tone.fg, fontFamily: 'var(--font-mono)',
          lineHeight: '20px', letterSpacing: 0.5,
        }}>{label}</span>
      </div>
    </Popover>
  )
}
