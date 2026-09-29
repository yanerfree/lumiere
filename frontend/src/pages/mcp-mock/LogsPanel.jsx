import { useState, useEffect, useCallback, Fragment } from 'react'
import { Button, Space, Tag, Radio, Pagination, Popconfirm, message } from 'antd'
import { ReloadOutlined, ClearOutlined } from '@ant-design/icons'
import { api } from '../../utils/request'

const MONO = 'var(--font-mono)'
const MODE_LABEL = { success: '成功', error: '失败', custom: '自定义', validate: '参数校验' }
const PAGE = 50

const box = {
  maxHeight: 140, overflow: 'auto', margin: 0, padding: 8, borderRadius: 12,
  background: 'transparent', border: '1px solid rgba(0,0,0,0.04)', fontSize: 11,
  fontFamily: MONO, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
}

/** 一个服务的调用日志。`serverId` 变了要整个重来（换服务看的是另一批记录，页码也得归零）。 */
export default function LogsPanel({ serverId, onCleared }) {
  const [logs, setLogs] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [expanded, setExpanded] = useState(null)

  const fetch = useCallback(async (p = 1, st = status) => {
    if (!serverId) return
    const q = new URLSearchParams({ serverId, limit: String(PAGE), offset: String((p - 1) * PAGE) })
    if (st) q.set('status', st)
    try {
      const r = await api.get(`/mcp-mock/logs?${q.toString()}`)
      const d = r.data !== undefined ? r : (r || {})
      setLogs(d.data || [])
      setTotal(d.total ?? 0)
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverId, status])

  useEffect(() => { setPage(1); setExpanded(null); fetch(1, status) }, [serverId, fetch, status])

  const clear = async () => {
    try {
      await api.delete(`/mcp-mock/logs?serverId=${serverId}`)
      message.success('已清空这个服务的日志')
      setLogs([]); setTotal(0); setPage(1)
      onCleared?.()
    } catch {}
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{
        padding: '8px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0,
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <Space size={10}>
          <span style={{ fontSize: 13, fontWeight: 500, color: '#1d2129' }}>共 {total} 条</span>
          <Radio.Group size="small" value={status} onChange={e => { setStatus(e.target.value); setPage(1) }}>
            <Radio.Button value="" style={{ fontSize: 11 }}>全部</Radio.Button>
            <Radio.Button value="ok" style={{ fontSize: 11 }}>成功</Radio.Button>
            <Radio.Button value="error" style={{ fontSize: 11 }}>失败</Radio.Button>
          </Radio.Group>
        </Space>
        <Space size={4}>
          <Button size="small" type="text" icon={<ReloadOutlined />} onClick={() => fetch(page, status)} />
          <Popconfirm title="清空这个服务的调用日志？" okText="清空" cancelText="再想想" onConfirm={clear}>
            <Button size="small" type="text" danger icon={<ClearOutlined />} />
          </Popconfirm>
        </Space>
      </div>

      <div style={{ flex: 1, overflow: 'auto' }}>
        <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: 'rgba(255,255,255,0.45)', position: 'sticky', top: 0, zIndex: 1 }}>
              {['时间', '工具', '来源', '模式', '结果', '耗时'].map((h, i) => (
                <th key={h} style={{
                  padding: '6px 10px', textAlign: i === 5 ? 'right' : 'left', fontWeight: 500,
                  fontSize: 11, color: '#86909c', borderBottom: '1px solid rgba(0,0,0,0.04)', whiteSpace: 'nowrap',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {logs.map(l => (
              <Fragment key={l.id}>
                <tr onClick={() => setExpanded(expanded === l.id ? null : l.id)} style={{
                  cursor: 'pointer', borderBottom: '1px solid rgba(0,0,0,0.03)',
                  background: expanded === l.id ? 'rgba(124,92,191,0.08)' : 'transparent',
                }}>
                  <td style={{ padding: '5px 10px', whiteSpace: 'nowrap', fontSize: 11, color: '#86909c' }}>
                    {l.timestamp ? new Date(l.timestamp).toLocaleTimeString('zh-CN', { hour12: false }) : '—'}
                  </td>
                  <td style={{ padding: '5px 10px', fontFamily: MONO, fontSize: 11 }}>{l.tool}</td>
                  <td style={{ padding: '5px 10px' }}>
                    <Tag style={{ margin: 0, fontSize: 11 }} color={l.source === 'call' ? 'cyan' : 'orange'}>
                      {l.source === 'call' ? '页面试调' : '客户端'}
                    </Tag>
                  </td>
                  <td style={{ padding: '5px 10px', fontSize: 11, color: '#86909c' }}>{MODE_LABEL[l.mode] || l.mode || '—'}</td>
                  <td style={{ padding: '5px 10px' }}>
                    {l.rejectKind === 'validate'
                      ? <Tag color="red" style={{ margin: 0, fontSize: 11 }}>参数被打回</Tag>
                      : <Tag color={l.isError ? 'red' : 'cyan'} style={{ margin: 0, fontSize: 11 }}>{l.isError ? '失败' : '成功'}</Tag>}
                  </td>
                  <td style={{ padding: '5px 10px', textAlign: 'right', fontSize: 11, color: '#86909c', whiteSpace: 'nowrap' }}>
                    {l.elapsedMs ?? 0}ms
                  </td>
                </tr>
                {expanded === l.id && (
                  <tr>
                    <td colSpan={6} style={{ padding: '10px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                      {l.rejectDetail && (
                        <div style={{ marginBottom: 8, fontSize: 12, color: '#e8453c' }}>被打回的原因：{l.rejectDetail}</div>
                      )}
                      <div style={{ display: 'flex', gap: 24 }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4, fontWeight: 500 }}>对面传了什么</div>
                          <pre style={box}>{JSON.stringify(l.arguments || {}, null, 2)}</pre>
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4, fontWeight: 500 }}>我们回了什么</div>
                          <pre style={box}>{(() => {
                            try { return JSON.stringify(JSON.parse(l.response), null, 2) } catch { return l.response || '—' }
                          })()}</pre>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {logs.length === 0 && (
              <tr><td colSpan={6} style={{ textAlign: 'center', padding: 40, color: '#c9cdd4', fontSize: 12 }}>
                还没有调用记录 —— 把上面的接入地址填进客户端，或者在「工具」页里试调一次
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {total > PAGE && (
        <div style={{ padding: '8px 16px', borderTop: '1px solid rgba(0,0,0,0.04)', flexShrink: 0, textAlign: 'right' }}>
          <Pagination size="small" current={page} pageSize={PAGE} total={total} showSizeChanger={false}
            showTotal={t => `共 ${t} 条`}
            onChange={p => { setPage(p); setExpanded(null); fetch(p, status) }} />
        </div>
      )}
    </div>
  )
}
