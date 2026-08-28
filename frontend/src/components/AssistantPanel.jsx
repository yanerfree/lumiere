import { useState, useEffect, useRef, useCallback } from 'react'
import { Drawer, Button, Input, Tag, Empty, Spin, Tooltip, message as antdMessage } from 'antd'
import { RobotOutlined, SendOutlined, CloseOutlined, CheckOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { api } from '../utils/request'

// 顶栏「AI 助手」抽屉。能力面完全由后端 /assistant/capabilities 按当前用户权限过滤后给出——
// 前端不硬编码任何工具清单，看得见的就是能做的（写操作仍需二次确认后才落库）。
//
// 三层收口在后端（services/assistant/__init__.py）：可见性过滤、execute 复检权限、守卫服务。
// 前端只负责：把对话流式渲染出来、把 proposal 变成一张确认卡、点确认后调 /assistant/execute。

const ACCENT = '#7cacf8'

function ProposalCard({ proposal, onExecute, executing, result, execError }) {
  // proposal: { tool, label, mutates, scope, args }
  const argEntries = Object.entries(proposal.args || {})
  return (
    <div style={{
      marginTop: 8, border: `1px solid ${proposal.mutates ? 'rgba(245,185,113,0.5)' : 'rgba(124,172,248,0.35)'}`,
      borderRadius: 10, padding: '10px 12px', background: proposal.mutates ? 'rgba(245,185,113,0.06)' : 'rgba(124,172,248,0.05)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <ThunderboltOutlined style={{ color: proposal.mutates ? '#f5b971' : ACCENT, fontSize: 13 }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: '#1d2129' }}>{proposal.label}</span>
        <Tag style={{ margin: 0, fontSize: 11 }} color={proposal.mutates ? 'orange' : 'blue'}>
          {proposal.mutates ? '写操作' : '读操作'}
        </Tag>
      </div>
      {argEntries.length > 0 && (
        <div style={{ fontSize: 12, color: '#5a6472', marginBottom: 8, lineHeight: 1.7 }}>
          {argEntries.map(([k, v]) => (
            <div key={k}>
              <span style={{ color: '#8c919e' }}>{k}：</span>
              <span>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
            </div>
          ))}
        </div>
      )}
      {result != null ? (
        <div style={{ fontSize: 12, color: '#3a9d5d', display: 'flex', alignItems: 'flex-start', gap: 5 }}>
          <CheckOutlined style={{ marginTop: 3 }} />
          <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontFamily: 'inherit', color: '#3a7d5d' }}>
            {typeof result === 'object' ? JSON.stringify(result, null, 2) : String(result)}
          </pre>
        </div>
      ) : execError ? (
        <div style={{ fontSize: 12, color: '#e57373' }}>执行失败：{execError}</div>
      ) : proposal.mutates ? (
        <Button type="primary" size="small" loading={executing} onClick={onExecute}
          style={{ background: '#f5a623', borderColor: '#f5a623' }}>
          确认执行
        </Button>
      ) : (
        <Button type="link" size="small" loading={executing} onClick={onExecute} style={{ padding: 0, height: 'auto' }}>
          {executing ? '执行中…' : '点此执行'}
        </Button>
      )}
    </div>
  )
}

export default function AssistantPanel({ projectId }) {
  const [open, setOpen] = useState(false)
  const [caps, setCaps] = useState(null)          // { is_super_admin, system_role, capabilities:[] }
  const [capsLoading, setCapsLoading] = useState(false)
  const [messages, setMessages] = useState([])    // { role, content, proposal, executing, result, execError, streaming, error }
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const streamRef = useRef(null)
  const bodyRef = useRef(null)

  const loadCaps = useCallback(async () => {
    setCapsLoading(true)
    try {
      const qs = projectId ? `?project_id=${projectId}` : ''
      const res = await api.get(`/assistant/capabilities${qs}`)
      // 响应过了驼峰化中间件：is_super_admin → isSuperAdmin。归一一下，下面统一读 is_super_admin。
      const d = res.data || {}
      setCaps({ ...d, is_super_admin: d.isSuperAdmin ?? d.is_super_admin })
    } catch {
      setCaps(null)
    } finally {
      setCapsLoading(false)
    }
  }, [projectId])

  // 打开时（或项目切换时）刷新能力面：换项目 = 换权限语境，能做的事会变
  useEffect(() => {
    if (open) loadCaps()
  }, [open, loadCaps])

  // 新消息滚到底
  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [messages])

  // 卸载/关闭时中断在飞的流
  useEffect(() => () => { streamRef.current?.abort() }, [])

  const runExecute = useCallback(async (msgIndex, proposal) => {
    setMessages((prev) => prev.map((m, i) => (i === msgIndex ? { ...m, executing: true, execError: null } : m)))
    try {
      const res = await api.post('/assistant/execute', {
        project_id: projectId || null,
        tool: proposal.tool,
        args: proposal.args || {},
      })
      setMessages((prev) => prev.map((m, i) => (i === msgIndex ? { ...m, executing: false, result: res.data?.result ?? {} } : m)))
      if (proposal.mutates) antdMessage.success(`已执行：${proposal.label}`)
    } catch (e) {
      setMessages((prev) => prev.map((m, i) => (i === msgIndex ? { ...m, executing: false, execError: e.message || '执行失败' } : m)))
    }
  }, [projectId])

  const send = useCallback(() => {
    const text = input.trim()
    if (!text || sending) return
    setInput('')
    // 送给后端的历史只要 role+content（proposal/result 等前端态不回传）
    const history = messages
      .filter((m) => !m.error && (m.role === 'user' || (m.role === 'assistant' && m.content)))
      .map((m) => ({ role: m.role, content: m.content }))
    const userMsg = { role: 'user', content: text }
    const assistantMsg = { role: 'assistant', content: '', streaming: true, proposal: null }
    setMessages((prev) => [...prev, userMsg, assistantMsg])
    const assistantIndex = messages.length + 1
    setSending(true)

    streamRef.current = api.stream(
      '/assistant/chat',
      { project_id: projectId || null, messages: [...history, userMsg] },
      {
        onChunk: (data) => {
          setMessages((prev) => prev.map((m, i) => (i === assistantIndex ? { ...m, content: m.content + (data.content || '') } : m)))
        },
        onDone: (data) => {
          setMessages((prev) => prev.map((m, i) => (
            i === assistantIndex
              ? { ...m, content: data.content ?? m.content, proposal: data.proposal || null, streaming: false }
              : m
          )))
          setSending(false)
          // 读操作（查询类）自动执行，省一次点击；写操作等用户点「确认执行」
          if (data.proposal && !data.proposal.mutates) {
            runExecute(assistantIndex, data.proposal)
          }
        },
        onError: (msg) => {
          setMessages((prev) => prev.map((m, i) => (
            i === assistantIndex ? { ...m, content: m.content, streaming: false, error: true, execError: msg } : m
          )))
          setSending(false)
        },
      },
    )
  }, [input, sending, messages, projectId, runExecute])

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  const capList = caps?.capabilities || []
  const readCaps = capList.filter((c) => !c.mutates)
  const writeCaps = capList.filter((c) => c.mutates)

  return (
    <>
      {/* 悬浮入口 —— 右下角，与顶栏「AI 助手」同一入口 */}
      <Tooltip title="AI 助手" placement="left">
        <button
          onClick={() => setOpen(true)}
          style={{
            position: 'fixed', right: 22, bottom: 26, width: 48, height: 48, borderRadius: '50%',
            border: 'none', cursor: 'pointer', zIndex: 1000,
            background: `linear-gradient(135deg, ${ACCENT}, #9d7cf8)`,
            boxShadow: '0 6px 18px rgba(124,172,248,0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <RobotOutlined style={{ color: '#fff', fontSize: 20 }} />
        </button>
      </Tooltip>

      <Drawer
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <RobotOutlined style={{ color: ACCENT }} />
            <span style={{ fontSize: 15 }}>AI 助手</span>
            {caps && (
              <Tag style={{ margin: 0, fontSize: 11 }} color={caps.is_super_admin ? 'gold' : 'blue'}>
                {caps.is_super_admin ? '管理员·全权限' : `可用 ${capList.length} 项`}
              </Tag>
            )}
          </div>
        }
        placement="right"
        width={420}
        open={open}
        onClose={() => setOpen(false)}
        closeIcon={<CloseOutlined />}
        styles={{ body: { display: 'flex', flexDirection: 'column', padding: 0 } }}
      >
        {/* 对话区 */}
        <div ref={bodyRef} style={{ flex: 1, overflow: 'auto', padding: '14px 16px' }}>
          {messages.length === 0 && (
            <div style={{ marginTop: 8 }}>
              {capsLoading ? (
                <div style={{ textAlign: 'center', padding: 24 }}><Spin size="small" /></div>
              ) : capList.length === 0 ? (
                <Empty description="当前语境下没有可执行的操作，只能回答问题" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : (
                <div style={{ fontSize: 12.5, color: '#5a6472', lineHeight: 1.9 }}>
                  <div style={{ marginBottom: 10, color: '#8c919e' }}>
                    你好，我能在你的权限范围内帮你操作平台。当前可做：
                  </div>
                  {readCaps.length > 0 && (
                    <div style={{ marginBottom: 8 }}>
                      <Tag color="blue" style={{ fontSize: 11 }}>读</Tag>
                      {readCaps.map((c) => <span key={c.key} style={{ marginRight: 8 }}>{c.label}</span>)}
                    </div>
                  )}
                  {writeCaps.length > 0 && (
                    <div style={{ marginBottom: 8 }}>
                      <Tag color="orange" style={{ fontSize: 11 }}>写</Tag>
                      {writeCaps.map((c) => <span key={c.key} style={{ marginRight: 8 }}>{c.label}</span>)}
                    </div>
                  )}
                  <div style={{ marginTop: 10, color: '#bfc4cd', fontSize: 12 }}>
                    例如：「列出这个项目的用例」「新建一个叫 staging 的环境」
                  </div>
                </div>
              )}
            </div>
          )}

          {messages.map((m, i) => (
            <div key={i} style={{ marginBottom: 14, display: 'flex', flexDirection: 'column', alignItems: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
              <div style={{
                maxWidth: '88%', padding: '8px 12px', borderRadius: 12, fontSize: 13, lineHeight: 1.6,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                background: m.role === 'user' ? `linear-gradient(135deg, ${ACCENT}, #9d7cf8)` : 'rgba(0,0,0,0.035)',
                color: m.role === 'user' ? '#fff' : '#1d2129',
                border: m.error ? '1px solid rgba(229,115,115,0.5)' : 'none',
              }}>
                {m.content || (m.streaming ? <span style={{ color: '#bfc4cd' }}>思考中…</span> : '')}
                {m.error && m.execError && (
                  <div style={{ color: '#e57373', marginTop: 4, fontSize: 12 }}>{m.execError}</div>
                )}
              </div>
              {m.proposal && (
                <div style={{ width: '88%' }}>
                  <ProposalCard
                    proposal={m.proposal}
                    executing={m.executing}
                    result={m.result}
                    execError={m.execError}
                    onExecute={() => runExecute(i, m.proposal)}
                  />
                </div>
              )}
            </div>
          ))}
        </div>

        {/* 输入区 */}
        <div style={{ borderTop: '1px solid rgba(0,0,0,0.06)', padding: '10px 12px', display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <Input.TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="描述你想做的事，回车发送"
            autoSize={{ minRows: 1, maxRows: 4 }}
            style={{ resize: 'none', fontSize: 13 }}
            disabled={sending}
          />
          <Button type="primary" icon={<SendOutlined />} onClick={send} loading={sending}
            disabled={!input.trim()} style={{ background: ACCENT, borderColor: ACCENT }} />
        </div>
      </Drawer>
    </>
  )
}
