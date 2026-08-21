import { useState, useMemo, useEffect } from 'react'
import { Input, Button, Space, Tag, message, Radio, Switch, InputNumber, Select, Tooltip, Popconfirm } from 'antd'
import {
  ToolOutlined, FormatPainterOutlined, SwapOutlined, ClockCircleOutlined,
  FileSearchOutlined, DatabaseOutlined, DiffOutlined, CopyOutlined,
  ReloadOutlined, ThunderboltOutlined, LoadingOutlined, SafetyOutlined,
  SaveOutlined, DeleteOutlined, ImportOutlined, HistoryOutlined
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'

const { TextArea } = Input
const FONT = "'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei UI', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', sans-serif"
const MONO = 'var(--font-mono)'

// 每个工具一个色，但**全部取自全站色板**（青碧 / 蓝 / 橙 / 紫 / 浅青 / 红 / 琥珀）。
// 原来这里用的是 Material 那一套（#43a047 绿、#00897b、#d81b60 玫红…），
// 跟全站的青碧主色不搭 —— 走查里工具箱是唯一还带自己一套绿的页面。
// light/bg/pale 一律取 primary 的 rgba，透明度按 0.1 / 0.1 / 0.05 三档，
// 跟别处的语义色底保持同一档。
const THEMES = {
  json:      { primary: '#0ea5a0', light: 'rgba(14,165,160,0.1)',  bg: 'rgba(14,165,160,0.1)',  pale: 'rgba(14,165,160,0.05)',  border: 'rgba(14,165,160,0.25)' },
  codec:     { primary: '#4e8af0', light: 'rgba(78,138,240,0.1)',  bg: 'rgba(78,138,240,0.1)',  pale: 'rgba(78,138,240,0.05)',  border: 'rgba(78,138,240,0.25)' },
  timestamp: { primary: '#ff7d00', light: 'rgba(255,125,0,0.1)',   bg: 'rgba(255,125,0,0.1)',   pale: 'rgba(255,125,0,0.05)',   border: 'rgba(255,125,0,0.25)' },
  regex:     { primary: '#7c5cbf', light: 'rgba(124,92,191,0.1)',  bg: 'rgba(124,92,191,0.1)',  pale: 'rgba(124,92,191,0.05)',  border: 'rgba(124,92,191,0.25)' },
  datagen:   { primary: '#2ec4b6', light: 'rgba(46,196,182,0.1)',  bg: 'rgba(46,196,182,0.1)',  pale: 'rgba(46,196,182,0.05)',  border: 'rgba(46,196,182,0.25)' },
  diff:      { primary: '#e8453c', light: 'rgba(232,69,60,0.1)',   bg: 'rgba(232,69,60,0.1)',   pale: 'rgba(232,69,60,0.05)',   border: 'rgba(232,69,60,0.25)' },
  jwt:       { primary: '#faad14', light: 'rgba(250,173,20,0.12)', bg: 'rgba(250,173,20,0.12)', pale: 'rgba(250,173,20,0.06)',  border: 'rgba(250,173,20,0.25)' },
}

const TOOLS = [
  { key: 'json', icon: <FormatPainterOutlined />, label: 'JSON 工具', desc: '格式化 · 压缩 · 转义' },
  { key: 'codec', icon: <SwapOutlined />, label: '编解码 / Hash', desc: 'Base64 · URL · Hash' },
  { key: 'timestamp', icon: <ClockCircleOutlined />, label: '时间戳转换', desc: '秒 ↔ 日期时间' },
  { key: 'regex', icon: <FileSearchOutlined />, label: '正则测试', desc: '实时匹配 · AI 生成' },
  { key: 'datagen', icon: <DatabaseOutlined />, label: '数据生成', desc: '手机 · 身份证 · UUID' },
  { key: 'diff', icon: <DiffOutlined />, label: '文本对比', desc: 'LCS 逐行差异' },
  { key: 'jwt', icon: <SafetyOutlined />, label: '认证工具', desc: 'JWT · Basic · HMAC · AK/SK' },
]

const copy = (text) => copyToClipboard(text).then(() => message.success('已复制'))

const Dot = ({ color }) => (
  <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: 3, background: color, marginRight: 6, verticalAlign: 'middle', opacity: 0.7 }} />
)

const TOOLBOX_CSS = `
.toolbox-page {
  font-family: 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei UI', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', sans-serif;
  letter-spacing: 0.3px;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
.toolbox-page .ant-btn {
  border-radius: 20px !important;
  transition: all 0.2s ease !important;
  font-weight: 500 !important;
  font-size: 12.5px !important;
  letter-spacing: 0.5px !important;
  box-shadow: none !important;
}
.toolbox-page .ant-btn:not(:disabled):hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.1) !important;
}
.toolbox-page .ant-btn-primary:not(:disabled) {
  background: var(--tb-primary) !important;
  border-color: var(--tb-primary) !important;
}
.toolbox-page .ant-btn-primary:not(:disabled):hover {
  filter: brightness(1.08);
}
.toolbox-page .ant-btn:not(.ant-btn-primary):not(.ant-btn-text):not(.ant-btn-link):not(:disabled) {
  color: var(--tb-primary) !important;
  border-color: var(--tb-border) !important;
}
.toolbox-page .ant-btn:not(.ant-btn-primary):not(.ant-btn-text):not(.ant-btn-link):not(:disabled):hover {
  background: var(--tb-pale) !important;
  border-color: var(--tb-primary) !important;
}
.toolbox-page textarea.ant-input,
.toolbox-page .ant-input,
.toolbox-page .ant-input-affix-wrapper {
  border-radius: 12px !important;
  border-width: 1.5px !important;
  transition: border-color 0.2s, box-shadow 0.2s !important;
}
.toolbox-page textarea.ant-input:focus,
.toolbox-page .ant-input:focus,
.toolbox-page .ant-input-affix-wrapper-focused {
  border-color: var(--tb-primary) !important;
  box-shadow: 0 0 0 3px var(--tb-pale) !important;
}
.toolbox-page .ant-input-number {
  border-radius: 12px !important;
  border-width: 1.5px !important;
}
.toolbox-page .ant-tag {
  border-radius: 12px !important;
  font-size: 11.5px !important;
  letter-spacing: 0.5px !important;
}
.toolbox-page .ant-radio-group .ant-radio-button-wrapper:first-child {
  border-radius: 14px 0 0 14px !important;
}
.toolbox-page .ant-radio-group .ant-radio-button-wrapper:last-child {
  border-radius: 0 14px 14px 0 !important;
}
.toolbox-page .ant-radio-button-wrapper-checked:not(.ant-radio-button-wrapper-disabled) {
  background: var(--tb-primary) !important;
  border-color: var(--tb-primary) !important;
}
.toolbox-page .ant-switch-checked {
  background: var(--tb-primary) !important;
}
.tb-nav-item {
  transition: all 0.25s ease !important;
  cursor: pointer;
}
.tb-nav-item:hover {
  transform: translateX(3px);
}
.tb-content-fade {
  animation: tbFadeIn 0.25s ease;
}
@keyframes tbFadeIn {
  from { opacity: 0.5; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}
`

// ━━━ JSON 工具 ━━━
function JsonTool({ theme }) {
  const [input, setInput] = useState('')
  const [output, setOutput] = useState('')
  const [error, setError] = useState('')
  const [autoFormat, setAutoFormat] = useState(false)

  useEffect(() => {
    if (!autoFormat || !input.trim()) { setOutput(''); setError(''); return }
    try { setOutput(JSON.stringify(JSON.parse(input), null, 2)); setError('') }
    catch (e) { setOutput(''); setError(e.message) }
  }, [input, autoFormat])

  const handleFormat = () => {
    if (!input.trim()) return
    try { const o = JSON.stringify(JSON.parse(input), null, 2); setOutput(o); setError('') }
    catch (e) { setError(e.message) }
  }
  const handleCompress = () => {
    if (!input.trim()) return
    try { const o = JSON.stringify(JSON.parse(input)); setOutput(o); setError('') }
    catch (e) { setError(e.message) }
  }
  const handleEscape = () => {
    if (!input) return
    setOutput(JSON.stringify(input))
  }
  const handleUnescape = () => {
    if (!input.trim()) return
    try {
      const parsed = JSON.parse(input)
      setOutput(typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2))
      setError('')
    } catch (e) { setError(e.message) }
  }

  const stats = useMemo(() => {
    if (!input) return null
    return { chars: input.length, lines: input.split('\n').length, bytes: new Blob([input]).size }
  }, [input])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <Space size={8}>
          <Button type="primary" size="small" onClick={handleFormat}>格式化</Button>
          <Button size="small" onClick={handleCompress}>压缩</Button>
          <Button size="small" onClick={handleEscape}>转义</Button>
          <Button size="small" onClick={handleUnescape}>去转义</Button>
          <span style={{ fontSize: 12, color: theme.primary, opacity: 0.8, fontFamily: FONT, letterSpacing: 0.5 }}>
            <Switch size="small" checked={autoFormat} onChange={setAutoFormat} style={{ marginRight: 4 }} />实时格式化
          </span>
        </Space>
        {stats && <span style={{ fontSize: 12, color: theme.primary, opacity: 0.6, fontFamily: FONT, letterSpacing: 0.5 }}>{stats.chars} 字符 · {stats.lines} 行 · {stats.bytes} B</span>}
      </div>
      {error && <div style={{ color: '#e8453c', fontSize: 12, marginBottom: 10, padding: '6px 12px', background: 'rgba(232,69,60,0.1)', borderRadius: 12, border: '1px solid rgba(232,69,60,0.25)' }}>{error}</div>}
      <div style={{ flex: 1, display: 'flex', gap: 14, minHeight: 0 }}>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{ fontSize: 12, color: theme.primary, fontWeight: 600, fontFamily: FONT, letterSpacing: 0.8 }}>
              <Dot color={theme.primary} />输入
            </span>
            <Button type="link" size="small" style={{ padding: 0, fontSize: 11, height: 'auto', color: theme.primary }}
              onClick={() => setInput('')}>清空</Button>
          </div>
          <TextArea spellCheck={false} value={input} onChange={e => { setInput(e.target.value); setError('') }}
            style={{ flex: 1, fontFamily: MONO, fontSize: 12, resize: 'none', borderColor: theme.border }}
            placeholder={'粘贴 JSON 试试看 ~\n\n支持：对象、数组、转义字符串 "{\\"key\\":\\"val\\"}"'} />
        </div>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{ fontSize: 12, color: theme.primary, fontWeight: 600, fontFamily: FONT, letterSpacing: 0.8 }}>
              <Dot color={theme.primary} />输出
            </span>
            <Button type="link" size="small" icon={<CopyOutlined />} style={{ padding: 0, fontSize: 11, height: 'auto', color: theme.primary }}
              disabled={!output} onClick={() => copy(output)}>复制</Button>
          </div>
          <TextArea spellCheck={false} value={output} readOnly
            style={{ flex: 1, fontFamily: MONO, fontSize: 12, resize: 'none', background: theme.pale, borderColor: theme.border }}
            placeholder="结果会出现在这里 ✨" />
        </div>
      </div>
    </div>
  )
}

// ━━━ 编解码 / Hash ━━━
function CodecTool({ theme }) {
  const [input, setInput] = useState('')
  const [output, setOutput] = useState('')
  const [mode, setMode] = useState('base64')

  const actions = {
    base64: {
      encode: () => btoa(unescape(encodeURIComponent(input))),
      decode: () => decodeURIComponent(escape(atob(input))),
    },
    url: {
      encode: () => encodeURIComponent(input),
      decode: () => decodeURIComponent(input),
    },
    unicode: {
      encode: () => Array.from(input).map(c => {
        const cp = c.codePointAt(0)
        if (cp <= 127) return c
        return cp > 0xFFFF ? `\\u{${cp.toString(16)}}` : `\\u${cp.toString(16).padStart(4, '0')}`
      }).join(''),
      decode: () => input.replace(/\\u\{([0-9a-fA-F]+)\}|\\u([0-9a-fA-F]{4})/g, (_, p1, p2) => String.fromCodePoint(parseInt(p1 || p2, 16))),
    },
    sha1: { encode: () => simpleHash(input, 'SHA-1') },
    sha256: { encode: () => simpleHash(input, 'SHA-256') },
  }

  const handleAction = async (action) => {
    if (!input) { message.warning('请先输入内容'); return }
    try {
      const fn = actions[mode]?.[action]
      if (!fn) return
      const result = await fn()
      setOutput(result)
    } catch (e) { message.error('操作失败：' + (e.message || '格式不正确')) }
  }

  const isHash = mode === 'sha1' || mode === 'sha256'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
        <Radio.Group value={mode} onChange={e => { setMode(e.target.value); setOutput('') }} size="small" buttonStyle="solid">
          <Radio.Button value="base64">Base64</Radio.Button>
          <Radio.Button value="url">URL</Radio.Button>
          <Radio.Button value="unicode">Unicode</Radio.Button>
          <Radio.Button value="sha1">SHA-1</Radio.Button>
          <Radio.Button value="sha256">SHA-256</Radio.Button>
        </Radio.Group>
        <Button type="primary" size="small" onClick={() => handleAction('encode')}>{isHash ? '计算' : '编码 →'}</Button>
        {!isHash && <Button size="small" onClick={() => handleAction('decode')}>← 解码</Button>}
        <Button size="small" icon={<CopyOutlined />} disabled={!output} onClick={() => copy(output)}>复制</Button>
      </div>
      <div style={{ flex: 1, display: 'flex', gap: 14, minHeight: 0 }}>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6, fontFamily: FONT, letterSpacing: 0.8 }}>
            <Dot color={theme.primary} />输入
          </div>
          <TextArea spellCheck={false} value={input} onChange={e => setInput(e.target.value)}
            style={{ flex: 1, fontFamily: MONO, fontSize: 12, resize: 'none', borderColor: theme.border }}
            placeholder="输入要处理的内容 ~" />
        </div>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6, fontFamily: FONT, letterSpacing: 0.8 }}>
            <Dot color={theme.primary} />输出
          </div>
          <TextArea spellCheck={false} value={output} readOnly
            style={{ flex: 1, fontFamily: MONO, fontSize: 12, resize: 'none', background: theme.pale, borderColor: theme.border }} />
        </div>
      </div>
    </div>
  )
}

async function simpleHash(text, algo) {
  const data = new TextEncoder().encode(text)
  const buf = await crypto.subtle.digest(algo, data)
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('')
}

// ━━━ 时间戳转换 ━━━
function TimestampTool({ theme }) {
  const [ts, setTs] = useState('')
  const [dt, setDt] = useState('')
  const [now, setNow] = useState(Math.floor(Date.now() / 1000))

  useEffect(() => {
    const timer = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 1000)
    return () => clearInterval(timer)
  }, [])

  const tsToDate = () => {
    if (!ts) return
    const n = Number(ts)
    const d = ts.length > 10 ? new Date(n) : new Date(n * 1000)
    if (isNaN(d.getTime())) { message.error('无效时间戳'); return }
    setDt(formatDate(d))
  }
  const dateToTs = () => {
    if (!dt) return
    const d = new Date(dt)
    if (isNaN(d.getTime())) { message.error('无效日期'); return }
    setTs(String(Math.floor(d.getTime() / 1000)))
  }

  const quickOffset = (days) => {
    const d = new Date()
    d.setHours(0, 0, 0, 0)
    d.setDate(d.getDate() + days)
    setTs(String(Math.floor(d.getTime() / 1000)))
    setDt(formatDate(d))
  }

  return (
    <div style={{ padding: 20 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20,
        padding: '14px 18px', background: theme.bg, borderRadius: 16,
        border: `1.5px solid ${theme.border}`,
      }}>
        <span style={{ fontSize: 12, color: theme.primary, fontWeight: 600, fontFamily: FONT, letterSpacing: 1 }}>⏱ 当前</span>
        <span style={{ fontFamily: MONO, fontSize: 17, fontWeight: 700, color: '#1d2129', minWidth: 110, letterSpacing: 0.5 }}>{now}</span>
        <span style={{ fontSize: 12, color: '#4e5969', fontFamily: FONT }}>{new Date(now * 1000).toLocaleString('zh-CN', { hour12: false })}</span>
        <Button size="small" icon={<CopyOutlined />} onClick={() => copy(String(now))}>复制</Button>
        <Button size="small" icon={<CopyOutlined />} onClick={() => copy(String(now * 1000))}>复制毫秒</Button>
      </div>

      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-end', marginBottom: 20 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6, fontFamily: FONT, letterSpacing: 0.8 }}>
            <Dot color={theme.primary} />时间戳（秒/毫秒）
          </div>
          <Input spellCheck={false} value={ts} onChange={e => setTs(e.target.value)} placeholder="1719820800"
            style={{ fontFamily: MONO, borderColor: theme.border }} onPressEnter={tsToDate} allowClear />
        </div>
        <Button type="primary" onClick={tsToDate}>→ 转日期</Button>
        <Button onClick={dateToTs}>← 转时间戳</Button>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6, fontFamily: FONT, letterSpacing: 0.8 }}>
            <Dot color={theme.primary} />日期时间
          </div>
          <Input value={dt} onChange={e => setDt(e.target.value)} placeholder="2024-07-01 12:00:00"
            style={{ borderColor: theme.border }} onPressEnter={dateToTs} allowClear />
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 10, fontFamily: FONT, letterSpacing: 0.8 }}>
          <Dot color={theme.primary} />快捷选择
        </div>
        <Space size={8} wrap>
          {[
            { label: '7天前', d: -7 }, { label: '3天前', d: -3 }, { label: '昨天', d: -1 },
            { label: '今天 0:00', d: 0 },
            { label: '明天', d: 1 }, { label: '3天后', d: 3 }, { label: '7天后', d: 7 }, { label: '30天后', d: 30 },
          ].map(q => (
            <Button key={q.label} size="small" onClick={() => {
              if (q.d === 0) {
                const d = new Date(); d.setHours(0,0,0,0)
                setTs(String(Math.floor(d.getTime() / 1000))); setDt(formatDate(d))
              } else quickOffset(q.d)
            }}>{q.label}</Button>
          ))}
        </Space>
      </div>

      <div style={{ fontSize: 12, color: theme.primary, opacity: 0.5, fontFamily: FONT, letterSpacing: 0.5 }}>
        支持 10 位（秒）和 13 位（毫秒）时间戳
      </div>
    </div>
  )
}

function formatDate(d) {
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// ━━━ 正则测试 ━━━
function RegexTool({ theme }) {
  const [pattern, setPattern] = useState('')
  const [flags, setFlags] = useState('g')
  const [text, setText] = useState('')
  const [aiInput, setAiInput] = useState('')
  const [aiLoading, setAiLoading] = useState(false)
  const [aiExplanation, setAiExplanation] = useState('')

  const { highlighted, matchCount, groups, regexError } = useMemo(() => {
    if (!pattern) return { highlighted: null, matchCount: 0, groups: [], regexError: null }
    try { new RegExp(pattern, flags) } catch (e) {
      return { highlighted: null, matchCount: 0, groups: [], regexError: e.message }
    }
    if (!text) return { highlighted: null, matchCount: 0, groups: [], regexError: null }
    try {
      const tempRe = new RegExp(pattern, flags.includes('g') ? flags : flags + 'g')
      let count = 0
      const parts = []
      const grps = []
      let lastIndex = 0
      let match
      while ((match = tempRe.exec(text)) !== null) {
        if (match.index > lastIndex) parts.push({ text: text.slice(lastIndex, match.index), matched: false })
        parts.push({ text: match[0], matched: true, index: count })
        if (match.length > 1) grps.push({ index: count, full: match[0], groups: match.slice(1) })
        lastIndex = tempRe.lastIndex
        count++
        if (!match[0].length) { tempRe.lastIndex++; if (tempRe.lastIndex > text.length) break }
      }
      if (lastIndex < text.length) parts.push({ text: text.slice(lastIndex), matched: false })
      return { highlighted: parts, matchCount: count, groups: grps, regexError: null }
    } catch (e) { return { highlighted: null, matchCount: 0, groups: [], regexError: e.message } }
  }, [pattern, flags, text])

  const handleAiGenerate = async () => {
    if (!aiInput.trim()) return
    setAiLoading(true)
    setAiExplanation('')
    try {
      const res = await api.post('/toolbox/generate-regex', { description: aiInput.trim() })
      const d = res.data?.data || res.data || res
      if (d.error) { message.error(d.error); return }
      if (d.regex) { setPattern(d.regex); if (d.flags) setFlags(d.flags) }
      if (d.explanation) setAiExplanation(d.explanation)
    } catch (e) { message.error('生成失败') } finally { setAiLoading(false) }
  }

  const COMMON = [
    { label: '手机号', re: '1[3-9]\\d{9}' },
    { label: '邮箱', re: '[\\w.-]+@[\\w.-]+\\.\\w+' },
    { label: 'IP地址', re: '\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}' },
    { label: 'URL', re: 'https?://[\\w./\\-?=&#%]+' },
    { label: '身份证', re: '\\d{17}[\\dXx]' },
    { label: '日期', re: '\\d{4}[-/]\\d{1,2}[-/]\\d{1,2}' },
    { label: '中文', re: '[\\u4e00-\\u9fa5]+' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 20 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontSize: 15, color: theme.primary, flexShrink: 0, fontWeight: 300, fontFamily: MONO }}>/</span>
        <Input spellCheck={false} value={pattern} onChange={e => setPattern(e.target.value)}
          style={{ flex: 1, fontFamily: MONO, borderColor: theme.border }} placeholder="输入正则表达式..." />
        <span style={{ fontSize: 15, color: theme.primary, flexShrink: 0, fontWeight: 300, fontFamily: MONO }}>/</span>
        <Input spellCheck={false} value={flags} onChange={e => setFlags(e.target.value)}
          style={{ width: 54, fontFamily: MONO, textAlign: 'center', borderColor: theme.border }} placeholder="g" />
        {matchCount > 0 && <Tag color="cyan" style={{ borderRadius: 12 }}>{matchCount} 个匹配</Tag>}
        {pattern && matchCount === 0 && !regexError && text && <Tag color="orange" style={{ borderRadius: 12 }}>无匹配</Tag>}
        {regexError && <Tag color="red" style={{ borderRadius: 12 }}>语法错误</Tag>}
      </div>
      {regexError && (
        <div style={{ color: '#e8453c', fontSize: 12, marginBottom: 10, padding: '6px 12px', background: 'rgba(232,69,60,0.1)', borderRadius: 12, border: '1px solid rgba(232,69,60,0.25)' }}>{regexError}</div>
      )}

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <Input value={aiInput} onChange={e => setAiInput(e.target.value)}
          placeholder="用自然语言描述，AI 帮你写正则 ~"
          style={{ width: 280, borderColor: theme.border }} size="small"
          onPressEnter={handleAiGenerate}
          suffix={
            <Button type="text" size="small" loading={aiLoading}
              icon={aiLoading ? <LoadingOutlined /> : <ThunderboltOutlined style={{ color: theme.primary }} />}
              onClick={handleAiGenerate} style={{ margin: '-4px -7px' }} />
          }
        />
        <span style={{ fontSize: 11, color: '#c9cdd4' }}>|</span>
        <span style={{ fontSize: 12, color: theme.primary, opacity: 0.6, fontFamily: FONT, letterSpacing: 0.5 }}>常用:</span>
        {COMMON.map(c => (
          <Tag key={c.label} style={{ cursor: 'pointer', fontSize: 11, borderRadius: 12, color: theme.primary, borderColor: theme.border }}
            onClick={() => setPattern(c.re)}>{c.label}</Tag>
        ))}
      </div>
      {aiExplanation && (
        <div style={{ fontSize: 12, color: theme.primary, marginBottom: 10, padding: '8px 12px', background: theme.pale, borderRadius: 12, border: `1px solid ${theme.border}` }}>
          {aiExplanation}
        </div>
      )}

      <div style={{ flex: 1, display: 'flex', gap: 14, minHeight: 0 }}>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6, fontFamily: FONT, letterSpacing: 0.8 }}>
            <Dot color={theme.primary} />测试文本
          </div>
          <TextArea spellCheck={false} value={text} onChange={e => setText(e.target.value)}
            style={{ flex: 1, fontFamily: MONO, fontSize: 12, resize: 'none', borderColor: theme.border }}
            placeholder="输入要匹配的文本 ~" />
        </div>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6, fontFamily: FONT, letterSpacing: 0.8 }}>
            <Dot color={theme.primary} />匹配结果
          </div>
          <div style={{
            flex: 1, overflow: 'auto', padding: 14, background: theme.pale, borderRadius: 12,
            border: `1.5px solid ${theme.border}`, fontFamily: MONO, fontSize: 12, lineHeight: 1.8,
            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>
            {highlighted ? highlighted.map((p, i) => p.matched
              ? <mark key={i} style={{ background: theme.light, padding: '2px 4px', borderRadius: 4, color: theme.primary }}>{p.text}</mark>
              : <span key={i}>{p.text}</span>
            ) : <span style={{ color: '#c9cdd4' }}>{regexError ? '请修正正则语法' : text ? '输入正则后实时匹配' : '等待输入...'}</span>}
          </div>
          {groups.length > 0 && (
            <div style={{ marginTop: 10, maxHeight: 80, overflow: 'auto' }}>
              <div style={{ fontSize: 11, color: theme.primary, opacity: 0.7, marginBottom: 4 }}>捕获组</div>
              {groups.map((g, i) => (
                <div key={i} style={{ fontSize: 11, fontFamily: MONO, padding: '2px 0' }}>
                  <span style={{ color: theme.primary, opacity: 0.6 }}>#{g.index + 1}</span> {g.groups.map((v, j) => (
                    <Tag key={j} style={{ fontSize: 11, margin: '0 2px', borderRadius: 10, color: theme.primary, borderColor: theme.border }}>${j + 1}: {v}</Tag>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ━━━ 数据生成 ━━━
function DataGenTool({ theme }) {
  const [results, setResults] = useState([])
  const [count, setCount] = useState(10)
  const [activeType, setActiveType] = useState(null)

  const randomPhone = () => {
    const pre = ['130','131','132','133','134','135','136','137','138','139','150','151','152','153','155','156','157','158','159','176','177','178','180','181','182','183','185','186','187','188','189']
    return pre[Math.floor(Math.random() * pre.length)] + String(Math.floor(Math.random() * 100000000)).padStart(8, '0')
  }
  const randomIdCard = () => {
    const area = ['110101','310101','440305','330102','510107','420106','320105','500103','610104','370202']
    const a = area[Math.floor(Math.random() * area.length)]
    const y = 1970 + Math.floor(Math.random() * 40)
    const m = String(Math.floor(Math.random() * 12) + 1).padStart(2, '0')
    const d = String(Math.floor(Math.random() * 28) + 1).padStart(2, '0')
    const seq = String(Math.floor(Math.random() * 999) + 1).padStart(3, '0')
    const base = `${a}${y}${m}${d}${seq}`
    const w = [7,9,10,5,8,4,2,1,6,3,7,9,10,5,8,4,2]
    let sum = 0; for (let i = 0; i < 17; i++) sum += parseInt(base[i]) * w[i]
    return base + '10X98765432'[sum % 11]
  }
  const randomEmail = () => {
    const n = ['test','user','dev','qa','mock','zhang','wang','li','zhao','hello','admin']
    const d = ['example.com','test.com','mock.org','gmail.com','qq.com','163.com']
    return `${n[Math.floor(Math.random() * n.length)]}${Math.floor(Math.random() * 9999)}@${d[Math.floor(Math.random() * d.length)]}`
  }
  const randomName = () => {
    const f = ['张','王','李','赵','刘','陈','杨','黄','周','吴','徐','孙','马','朱','胡','林','郭','何','高','罗']
    const s = ['伟','芳','秀英','敏','静','丽','强','磊','洋','勇','艳','杰','娟','涛','明','超','霞','平','刚','建国','小红','志强']
    return f[Math.floor(Math.random() * f.length)] + s[Math.floor(Math.random() * s.length)]
  }
  const randomUUID = () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16)
  })
  const randomAddr = () => {
    const city = ['北京市朝阳区','上海市浦东新区','广州市天河区','深圳市南山区','杭州市西湖区','成都市武侯区','武汉市江汉区','南京市鼓楼区']
    const road = ['长安街','南京路','中山路','人民路','建设路','解放路','光华大道','科技路']
    return `${city[Math.floor(Math.random() * city.length)]}${road[Math.floor(Math.random() * road.length)]}${Math.floor(Math.random() * 999) + 1}号`
  }
  const randomMixed = () => `${randomName()}\t${randomPhone()}\t${randomEmail()}\t${randomIdCard()}`

  const generators = [
    { key: 'phone', label: '手机号', fn: randomPhone },
    { key: 'idcard', label: '身份证', fn: randomIdCard },
    { key: 'email', label: '邮箱', fn: randomEmail },
    { key: 'name', label: '姓名', fn: randomName },
    { key: 'uuid', label: 'UUID', fn: randomUUID },
    { key: 'addr', label: '地址', fn: randomAddr },
    { key: 'mixed', label: '综合（姓名+手机+邮箱+身份证）', fn: randomMixed },
  ]

  const generate = (g) => {
    setActiveType(g.key)
    setResults(Array.from({ length: count }, () => g.fn()))
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 20 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: theme.primary, fontWeight: 600, fontFamily: FONT, letterSpacing: 0.8 }}>
          <Dot color={theme.primary} />数量
        </span>
        <InputNumber value={count} onChange={v => v != null && setCount(Math.max(1, Math.min(200, v)))}
          min={1} max={200} size="small" style={{ width: 65 }} />
        {generators.map(g => (
          <Button key={g.key} size="small" type={activeType === g.key ? 'primary' : 'default'}
            onClick={() => generate(g)}>{g.label}</Button>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
        {results.length > 0 && <>
          <Button size="small" icon={<CopyOutlined />} onClick={() => copy(results.join('\n'))}>复制全部</Button>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => {
            const g = generators.find(x => x.key === activeType)
            if (g) setResults(Array.from({ length: count }, () => g.fn()))
          }}>重新生成</Button>
        </>}
      </div>
      <div style={{ flex: 1, overflow: 'auto', borderRadius: 14 }}>
        {results.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {results.map((r, i) => (
              <div key={i} style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '7px 14px', background: i % 2 === 0 ? theme.pale : 'rgba(255,255,255,0.6)',
                borderRadius: 10,
              }}>
                <span style={{ fontFamily: MONO, fontSize: 12, color: '#1d2129', whiteSpace: 'pre' }}>{r}</span>
                <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copy(r)}
                  style={{ flexShrink: 0, color: theme.primary, opacity: 0.6 }} />
              </div>
            ))}
          </div>
        ) : (
          <div style={{ textAlign: 'center', padding: 50, color: theme.primary, opacity: 0.4, fontSize: 12, fontFamily: FONT, letterSpacing: 1 }}>
            选择数据类型开始生成 ✨
          </div>
        )}
      </div>
    </div>
  )
}

// ━━━ 文本对比（LCS diff）━━━
function DiffTool({ theme }) {
  const [left, setLeft] = useState('')
  const [right, setRight] = useState('')
  const [diffResult, setDiffResult] = useState(null)

  const handleDiff = () => {
    const a = left.split('\n'), b = right.split('\n')
    const lcs = computeLCS(a, b)
    const result = []
    let ai = 0, bi = 0
    for (const [la, lb] of lcs) {
      while (ai < la) result.push({ type: 'removed', left: a[ai++], right: '' })
      while (bi < lb) result.push({ type: 'added', left: '', right: b[bi++] })
      result.push({ type: 'same', left: a[ai], right: b[bi] })
      ai++; bi++
    }
    while (ai < a.length) result.push({ type: 'removed', left: a[ai++], right: '' })
    while (bi < b.length) result.push({ type: 'added', left: '', right: b[bi++] })
    setDiffResult(result)
  }

  const diffCount = diffResult ? diffResult.filter(r => r.type !== 'same').length : 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
        <Button type="primary" size="small" onClick={handleDiff} disabled={!left && !right}>对比</Button>
        {diffResult && <Button size="small" onClick={() => setDiffResult(null)}>返回编辑</Button>}
        <Button size="small" onClick={() => { setLeft(''); setRight(''); setDiffResult(null) }}>清空</Button>
        {diffResult && (
          <span style={{ fontSize: 12, color: theme.primary, opacity: 0.7, fontFamily: FONT }}>
            {diffResult.length} 行，
            {diffCount > 0
              ? <span style={{ color: '#e8453c' }}>{diffCount} 处差异</span>
              : <span style={{ color: '#0ea5a0' }}>完全一致 ✓</span>}
          </span>
        )}
      </div>
      {!diffResult ? (
        <div style={{ flex: 1, display: 'flex', gap: 14, minHeight: 0 }}>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
            <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6, fontFamily: FONT, letterSpacing: 0.8 }}>
              <Dot color={theme.primary} />文本 A
            </div>
            <TextArea spellCheck={false} value={left} onChange={e => setLeft(e.target.value)}
              style={{ flex: 1, fontFamily: MONO, fontSize: 12, resize: 'none', borderColor: theme.border }}
              placeholder="粘贴文本 A ~" />
          </div>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
            <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6, fontFamily: FONT, letterSpacing: 0.8 }}>
              <Dot color={theme.primary} />文本 B
            </div>
            <TextArea spellCheck={false} value={right} onChange={e => setRight(e.target.value)}
              style={{ flex: 1, fontFamily: MONO, fontSize: 12, resize: 'none', borderColor: theme.border }}
              placeholder="粘贴文本 B ~" />
          </div>
        </div>
      ) : (
        <div style={{ flex: 1, overflow: 'auto', border: `1.5px solid ${theme.border}`, borderRadius: 14 }}>
          {diffResult.map((r, i) => {
            const bg = r.type === 'removed' ? 'rgba(255,205,210,0.4)' : r.type === 'added' ? 'rgba(200,230,201,0.5)' : 'transparent'
            const sign = r.type === 'removed' ? '−' : r.type === 'added' ? '+' : ' '
            const signColor = r.type === 'removed' ? '#e8453c' : r.type === 'added' ? '#0ea5a0' : '#c9cdd4'
            const content = r.type === 'removed' ? r.left : r.type === 'added' ? r.right : r.left
            return (
              <div key={i} style={{
                display: 'flex', fontFamily: MONO, fontSize: 12, lineHeight: 1.7,
                background: bg, borderBottom: '1px solid rgba(0,0,0,0.03)', minHeight: 22,
              }}>
                <span style={{ width: 35, textAlign: 'right', padding: '0 6px', color: '#c9cdd4', fontSize: 11, flexShrink: 0, lineHeight: '22px' }}>{i + 1}</span>
                <span style={{ width: 18, textAlign: 'center', color: signColor, fontWeight: 600, flexShrink: 0, lineHeight: '22px' }}>{sign}</span>
                <span style={{ padding: '0 8px', flex: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{content}</span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function computeLCS(a, b) {
  const m = a.length, n = b.length
  if (m > 5000 || n > 5000) {
    const result = []
    let i = 0, j = 0
    while (i < m && j < n) {
      if (a[i] === b[j]) { result.push([i, j]); i++; j++ }
      else {
        let fi = -1, fj = -1
        for (let k = 1; k <= 3 && i + k < m; k++) { if (a[i + k] === b[j]) { fi = k; break } }
        for (let k = 1; k <= 3 && j + k < n; k++) { if (a[i] === b[j + k]) { fj = k; break } }
        if (fi >= 0 && (fj < 0 || fi <= fj)) i += fi
        else if (fj >= 0) j += fj
        else { i++; j++ }
      }
    }
    return result
  }
  const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1))
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] = a[i-1] === b[j-1] ? dp[i-1][j-1] + 1 : Math.max(dp[i-1][j], dp[i][j-1])
  const result = []
  let i = m, j = n
  while (i > 0 && j > 0) {
    if (a[i-1] === b[j-1]) { result.unshift([i-1, j-1]); i--; j-- }
    else if (dp[i-1][j] > dp[i][j-1]) i--
    else j--
  }
  return result
}

// ━━━ 认证保存 ━━━
const AUTH_STORAGE_KEY = type => `tb_auth_saved_${type}`

function getAuthSaved(type) {
  try { return JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY(type)) || '[]') }
  catch { return [] }
}
function setAuthSaved(type, list) {
  localStorage.setItem(AUTH_STORAGE_KEY(type), JSON.stringify(list))
}
function addAuthSaved(type, entry) {
  const list = getAuthSaved(type)
  list.unshift({ ...entry, id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), savedAt: new Date().toISOString() })
  if (list.length > 50) list.length = 50
  setAuthSaved(type, list)
  return list
}
function removeAuthSaved(type, id) {
  const list = getAuthSaved(type).filter(e => e.id !== id)
  setAuthSaved(type, list)
  return list
}

function AuthSavedList({ type, theme, onRestore, getCopyText }) {
  const [list, setList] = useState(() => getAuthSaved(type))
  const [expanded, setExpanded] = useState(false)

  const refresh = () => setList(getAuthSaved(type))
  const doSave = (entry) => { setList(addAuthSaved(type, entry)); setExpanded(true); message.success('已保存') }
  const doDelete = (id) => { setList(removeAuthSaved(type, id)); message.success('已删除') }

  return { list, refresh, doSave, expanded, setExpanded, ui: list.length > 0 || expanded ? (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', userSelect: 'none', marginBottom: expanded ? 10 : 0 }}
        onClick={() => setExpanded(!expanded)}>
        <HistoryOutlined style={{ color: theme.primary, marginRight: 6, fontSize: 13 }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: theme.primary }}>已保存</span>
        <Tag style={{ marginLeft: 8, borderRadius: 10, fontSize: 11 }}>{list.length}</Tag>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: '#aaa' }}>{expanded ? '收起 ▴' : '展开 ▾'}</span>
      </div>
      {expanded && (list.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 16, color: '#c9cdd4', fontSize: 12 }}>暂无保存记录</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 300, overflow: 'auto' }}>
          {list.map(item => (
            <div key={item.id} style={{
              padding: '10px 14px', background: theme.pale, borderRadius: 10,
              border: `1px solid ${theme.border}`, display: 'flex', alignItems: 'center', gap: 10
            }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#333', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {item.name}
                </div>
                <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                  {new Date(item.savedAt).toLocaleString('zh-CN')}
                </div>
              </div>
              {getCopyText && (
                <Tooltip title="复制 Token / 结果">
                  <Button type="text" size="small" icon={<CopyOutlined />}
                    onClick={() => { const t = getCopyText(item); if (t) copy(t) }} />
                </Tooltip>
              )}
              <Tooltip title="恢复到面板">
                <Button type="text" size="small" icon={<ImportOutlined />}
                  style={{ color: theme.primary }}
                  onClick={() => { onRestore(item); message.success('已恢复到面板') }} />
              </Tooltip>
              <Popconfirm title="确认删除？" onConfirm={() => doDelete(item.id)} okText="删除" cancelText="取消">
                <Button type="text" size="small" icon={<DeleteOutlined />} danger />
              </Popconfirm>
            </div>
          ))}
        </div>
      ))}
    </div>
  ) : null }
}

// ━━━ JWT 工具 ━━━
function base64urlEncode(str) {
  return btoa(unescape(encodeURIComponent(str))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}
function base64urlDecode(str) {
  str = str.replace(/-/g, '+').replace(/_/g, '/')
  while (str.length % 4) str += '='
  return decodeURIComponent(escape(atob(str)))
}

function JwtTool({ theme }) {
  const [authMode, setAuthMode] = useState('jwt')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 20 }}>
      <div style={{ marginBottom: 14 }}>
        <Radio.Group value={authMode} onChange={e => setAuthMode(e.target.value)} size="small" buttonStyle="solid">
          <Radio.Button value="jwt">JWT</Radio.Button>
          <Radio.Button value="basic">Basic Auth</Radio.Button>
          <Radio.Button value="hmac">HMAC 签名</Radio.Button>
          <Radio.Button value="aksk">AK/SK</Radio.Button>
          <Radio.Button value="oauth2">OAuth2</Radio.Button>
        </Radio.Group>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {authMode === 'jwt' && <JwtPanel theme={theme} />}
        {authMode === 'basic' && <BasicAuthPanel theme={theme} />}
        {authMode === 'hmac' && <HmacPanel theme={theme} />}
        {authMode === 'aksk' && <AkSkPanel theme={theme} />}
        {authMode === 'oauth2' && <OAuth2Panel theme={theme} />}
      </div>
    </div>
  )
}

function JwtPanel({ theme }) {
  const [secret, setSecret] = useState('')
  const [expHours, setExpHours] = useState(24)
  const [customClaims, setCustomClaims] = useState('{\n  "sub": "user123",\n  "role": "admin"\n}')
  const [result, setResult] = useState(null)
  const [generating, setGenerating] = useState(false)
  const [decodeInput, setDecodeInput] = useState('')
  const [tab, setTab] = useState('generate')

  const saved = AuthSavedList({
    type: 'jwt', theme,
    onRestore: (item) => {
      setSecret(item.inputs.secret || '')
      setExpHours(item.inputs.expHours || 24)
      setCustomClaims(item.inputs.customClaims || '{}')
      if (item.result) setResult({ ...item.result, expAt: new Date(item.result.expAt) })
      setTab('generate')
    },
    getCopyText: (item) => item.result?.token,
  })

  const handleSave = () => {
    if (!result) return
    const claims = (() => { try { return JSON.parse(customClaims) } catch { return {} } })()
    const name = claims.sub ? `JWT · ${claims.sub}` : `JWT · ${new Date().toLocaleTimeString('zh-CN')}`
    saved.doSave({ name, inputs: { secret, expHours, customClaims }, result: { ...result, expAt: result.expAt.toISOString() } })
  }

  const handleGenerate = async () => {
    if (!secret.trim()) { message.warning('请输入 Secret'); return }
    setGenerating(true)
    try {
      let claims = {}
      try { claims = JSON.parse(customClaims) } catch { claims = { sub: 'user123' } }
      const now = Math.floor(Date.now() / 1000)
      const payload = { ...claims, iat: now, exp: now + expHours * 3600 }
      const res = await api.post('/toolbox/jwt-sign', { payload, secret: secret.trim() })
      const d = res.data?.data || res.data || res
      if (d.error || res.error) { message.error(d.error || res.error); return }
      setResult({ token: d.token, payload, expAt: new Date((now + expHours * 3600) * 1000) })
    } catch (e) { message.error(e.message) }
    finally { setGenerating(false) }
  }

  const decoded = useMemo(() => {
    if (!decodeInput.trim()) return null
    const parts = decodeInput.trim().split('.')
    if (parts.length !== 3) return { error: '格式不正确（需要 3 段）' }
    try {
      const header = JSON.parse(base64urlDecode(parts[0]))
      const pl = JSON.parse(base64urlDecode(parts[1]))
      const exp = pl.exp ? new Date(pl.exp * 1000) : null
      return { header, payload: pl, exp, isExpired: exp ? exp < new Date() : false }
    } catch (e) { return { error: e.message } }
  }, [decodeInput])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ marginBottom: 14 }}>
        <Radio.Group value={tab} onChange={e => setTab(e.target.value)} size="small">
          <Radio value="generate">生成 Token</Radio>
          <Radio value="decode">解码 Token</Radio>
        </Radio.Group>
      </div>

      {tab === 'generate' ? (
        <div style={{ flex: 1, overflow: 'auto' }}>
          {/* 输入区 */}
          <div style={{ display: 'flex', gap: 14, marginBottom: 14 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>Secret 密钥 *</div>
              <Input.Password value={secret} onChange={e => setSecret(e.target.value)} placeholder="输入你的 JWT Secret" style={{ fontFamily: MONO }} />
            </div>
            <div style={{ width: 120 }}>
              <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>有效期</div>
              <Select value={expHours} onChange={setExpHours} style={{ width: '100%' }} options={[
                { value: 1, label: '1 小时' }, { value: 6, label: '6 小时' },
                { value: 24, label: '1 天' }, { value: 168, label: '7 天' },
                { value: 720, label: '30 天' }, { value: 8760, label: '1 年' },
                { value: 876000, label: '永不过期' },
              ]} />
            </div>
            <div style={{ display: 'flex', alignItems: 'flex-end' }}>
              <Button type="primary" onClick={handleGenerate} loading={generating}>生成 Token</Button>
            </div>
          </div>
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>自定义 Claims（可选）</div>
            <TextArea spellCheck={false} value={customClaims} onChange={e => setCustomClaims(e.target.value)} rows={3}
              style={{ fontFamily: MONO, fontSize: 12, borderColor: theme.border }}
              placeholder='{"sub": "user123", "role": "admin"}' />
          </div>

          {/* 结果区 */}
          {result && (
            <div style={{ border: `1px solid ${theme.border}`, borderRadius: 12, padding: 16, background: theme.pale }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: theme.primary, marginBottom: 10 }}>生成结果</div>

              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>JWT Token</div>
                <div style={{ padding: 10, background: '#fff', borderRadius: 8, fontFamily: MONO, fontSize: 11, wordBreak: 'break-all', border: '1px solid rgba(0,0,0,0.06)', position: 'relative' }}>
                  {result.token}
                  <Button type="link" size="small" icon={<CopyOutlined />} style={{ position: 'absolute', top: 4, right: 4, fontSize: 11 }}
                    onClick={() => copy(result.token)}>复制</Button>
                </div>
              </div>

              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>请求时这样用</div>
                <div style={{ padding: 10, background: '#1e1e2e', color: '#cdd6f4', borderRadius: 8, fontFamily: MONO, fontSize: 12, lineHeight: 1.8 }}>
                  <div>Authorization: <span style={{ color: '#a6e3a1' }}>Bearer {result.token.substring(0, 20)}...{result.token.substring(result.token.length - 10)}</span></div>
                </div>
                <Button size="small" icon={<CopyOutlined />} style={{ marginTop: 6 }}
                  onClick={() => copy(`Bearer ${result.token}`)}>复制完整 Header 值</Button>
              </div>

              <div style={{ display: 'flex', gap: 12, marginBottom: 14 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>cURL 示例</div>
                  <div style={{ padding: 8, background: '#1e1e2e', color: '#cdd6f4', borderRadius: 8, fontFamily: MONO, fontSize: 11, lineHeight: 1.6 }}>
                    curl -H "Authorization: Bearer {result.token.substring(0, 15)}..." \<br/>
                    {'  '}https://api.example.com/endpoint
                  </div>
                  <Button size="small" icon={<CopyOutlined />} style={{ marginTop: 4 }}
                    onClick={() => copy(`curl -H "Authorization: Bearer ${result.token}" https://api.example.com/endpoint`)}>复制 cURL</Button>
                </div>
                <div>
                  <div style={{ fontSize: 11, color: '#86909c', lineHeight: 2 }}>
                    <div>签发时间: {new Date().toLocaleString('zh-CN')}</div>
                    <div>过期时间: {result.expAt.toLocaleString('zh-CN')}</div>
                    <div>算法: HS256</div>
                  </div>
                </div>
              </div>

              <div style={{ borderTop: `1px dashed ${theme.border}`, paddingTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
                <Button icon={<SaveOutlined />} onClick={handleSave}>保存此凭证</Button>
              </div>
            </div>
          )}

          {!result && (
            <div style={{ textAlign: 'center', padding: 30, color: '#c9cdd4', fontSize: 13 }}>
              输入 Secret 后点击「生成 Token」，会给你一个可直接使用的 JWT
            </div>
          )}
        </div>
      ) : (
        <div style={{ flex: 1, display: 'flex', gap: 14, minHeight: 0 }}>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
            <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>粘贴 Token</div>
            <TextArea spellCheck={false} value={decodeInput} onChange={e => setDecodeInput(e.target.value)}
              style={{ flex: 1, fontFamily: MONO, fontSize: 12, resize: 'none', borderColor: theme.border }}
              placeholder="粘贴 JWT Token 自动解码..." />
          </div>
          <div style={{ flex: 1, overflow: 'auto' }}>
            {decoded ? (decoded.error ? (
              <div style={{ color: '#e8453c', padding: 12, background: 'rgba(232,69,60,0.1)', borderRadius: 12, fontSize: 12 }}>{decoded.error}</div>
            ) : (<>
              {decoded.isExpired && <Tag color="red" style={{ marginBottom: 8 }}>已过期</Tag>}
              {decoded.exp && !decoded.isExpired && <Tag color="cyan" style={{ marginBottom: 8 }}>有效至 {decoded.exp.toLocaleString('zh-CN')}</Tag>}
              <div style={{ marginBottom: 10 }}>
                <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Header</div>
                <pre style={{ margin: 0, padding: 8, background: theme.pale, borderRadius: 8, fontSize: 11, fontFamily: MONO, border: `1px solid ${theme.border}` }}>
                  {JSON.stringify(decoded.header, null, 2)}</pre>
              </div>
              <div>
                <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Payload</div>
                <pre style={{ margin: 0, padding: 8, background: theme.pale, borderRadius: 8, fontSize: 11, fontFamily: MONO, border: `1px solid ${theme.border}` }}>
                  {JSON.stringify(decoded.payload, null, 2)}</pre>
              </div>
            </>)) : <div style={{ color: '#c9cdd4', padding: 20, textAlign: 'center', fontSize: 13 }}>粘贴 JWT 自动解码</div>}
          </div>
        </div>
      )}

      {saved.ui}
    </div>
  )
}

function BasicAuthPanel({ theme }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const result = useMemo(() => {
    if (!username) return ''
    return btoa(unescape(encodeURIComponent(`${username}:${password}`)))
  }, [username, password])

  const saved = AuthSavedList({
    type: 'basic', theme,
    onRestore: (item) => {
      setUsername(item.inputs.username || '')
      setPassword(item.inputs.password || '')
    },
    getCopyText: (item) => `Basic ${item.result?.encoded}`,
  })

  const handleSave = () => {
    if (!result) return
    saved.doSave({ name: `Basic · ${username}`, inputs: { username, password }, result: { encoded: result } })
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>用户名</div>
          <Input value={username} onChange={e => setUsername(e.target.value)} placeholder="username" />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>密码</div>
          <Input.Password value={password} onChange={e => setPassword(e.target.value)} placeholder="password" />
        </div>
      </div>
      {result && (<>
        <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>Base64 编码</div>
        <div style={{ padding: 10, background: theme.pale, borderRadius: 10, fontFamily: MONO, fontSize: 12, border: `1px solid ${theme.border}`, marginBottom: 12, wordBreak: 'break-all' }}>
          {result}
          <Button type="link" size="small" icon={<CopyOutlined />} style={{ float: 'right', fontSize: 11 }}
            onClick={() => copy(result)}>复制</Button>
        </div>
        <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>Authorization Header</div>
        <div style={{ padding: 10, background: theme.pale, borderRadius: 10, fontFamily: MONO, fontSize: 12, border: `1px solid ${theme.border}`, marginBottom: 12, wordBreak: 'break-all' }}>
          Basic {result}
          <Button type="link" size="small" icon={<CopyOutlined />} style={{ float: 'right', fontSize: 11 }}
            onClick={() => copy(`Basic ${result}`)}>复制</Button>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Button icon={<SaveOutlined />} onClick={handleSave}>保存此凭证</Button>
        </div>
      </>)}

      {saved.ui}
    </div>
  )
}

function HmacPanel({ theme }) {
  const [message_, setMessage_] = useState('')
  const [secret, setSecret] = useState('')
  const [algo, setAlgo] = useState('SHA-256')
  const [result, setResult] = useState(null)
  const [signing, setSigning] = useState(false)

  const saved = AuthSavedList({
    type: 'hmac', theme,
    onRestore: (item) => {
      setMessage_(item.inputs.message || '')
      setSecret(item.inputs.secret || '')
      setAlgo(item.inputs.algo || 'SHA-256')
      if (item.result) setResult(item.result)
    },
    getCopyText: (item) => item.result?.base64,
  })

  const handleSave = () => {
    if (!result) return
    const preview = message_.length > 20 ? message_.substring(0, 20) + '...' : message_
    saved.doSave({ name: `HMAC · ${algo} · ${preview}`, inputs: { message: message_, secret, algo }, result })
  }

  const handleSign = async () => {
    if (!message_ || !secret) { message.warning('请输入消息和密钥'); return }
    setSigning(true)
    try {
      const res = await api.post('/toolbox/hmac-sign', { message: message_, secret, algorithm: algo })
      const d = res.data?.data || res.data || res
      if (d.error || res.error) { message.error(d.error || res.error); return }
      setResult(d)
    } catch (e) { message.error(e.message) }
    finally { setSigning(false) }
  }

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>算法</div>
        <Radio.Group value={algo} onChange={e => setAlgo(e.target.value)} size="small">
          <Radio value="SHA-256">SHA256</Radio>
          <Radio value="SHA-1">SHA1</Radio>
          <Radio value="SHA-384">SHA384</Radio>
          <Radio value="SHA-512">SHA512</Radio>
        </Radio.Group>
      </div>
      <div style={{ display: 'flex', gap: 14, marginBottom: 14 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>待签名消息</div>
          <TextArea spellCheck={false} value={message_} onChange={e => setMessage_(e.target.value)} rows={4}
            style={{ fontFamily: MONO, fontSize: 12, borderColor: theme.border }}
            placeholder={'输入要签名的内容，例如：\nGET\\n/api/users\\ntimestamp=1234567890'} />
        </div>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>密钥 (Secret Key)</div>
          <Input spellCheck={false} value={secret} onChange={e => setSecret(e.target.value)}
            style={{ fontFamily: MONO, fontSize: 12, borderColor: theme.border, marginBottom: 12 }} placeholder="your-secret-key" />
          <Button type="primary" onClick={handleSign} loading={signing} block>生成签名</Button>
        </div>
      </div>
      {result ? (<>
        <div style={{ display: 'flex', gap: 14, marginBottom: 12 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Hex</div>
            <div style={{ padding: 10, background: theme.pale, borderRadius: 10, fontFamily: MONO, fontSize: 11, border: `1px solid ${theme.border}`, wordBreak: 'break-all' }}>
              {result.hex}
              <Button type="link" size="small" icon={<CopyOutlined />} style={{ float: 'right', padding: 0, fontSize: 11 }}
                onClick={() => copy(result.hex)}>复制</Button>
            </div>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Base64</div>
            <div style={{ padding: 10, background: theme.pale, borderRadius: 10, fontFamily: MONO, fontSize: 11, border: `1px solid ${theme.border}`, wordBreak: 'break-all' }}>
              {result.base64}
              <Button type="link" size="small" icon={<CopyOutlined />} style={{ float: 'right', padding: 0, fontSize: 11 }}
                onClick={() => copy(result.base64)}>复制</Button>
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Button icon={<SaveOutlined />} onClick={handleSave}>保存此凭证</Button>
        </div>
      </>) : (
        <div style={{ textAlign: 'center', padding: 20, color: '#c9cdd4', fontSize: 13 }}>输入消息和密钥后点击「生成签名」</div>
      )}

      {saved.ui}
    </div>
  )
}

function AkSkPanel({ theme }) {
  const [accessKey, setAccessKey] = useState('')
  const [secretKey, setSecretKey] = useState('')
  const [method, setMethod] = useState('GET')
  const [path, setPath] = useState('/api/resource')
  const [signTime, setSignTime] = useState(() => {
    const d = new Date()
    const pad = n => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  })
  const [result, setResult] = useState(null)
  const [signing, setSigning] = useState(false)

  const saved = AuthSavedList({
    type: 'aksk', theme,
    onRestore: (item) => {
      setAccessKey(item.inputs.accessKey || '')
      setSecretKey(item.inputs.secretKey || '')
      setMethod(item.inputs.method || 'GET')
      setPath(item.inputs.path || '/api/resource')
      setSignTime(item.inputs.signTime || '')
      if (item.result) setResult(item.result)
    },
    getCopyText: (item) => item.result?.auth,
  })

  const handleSave = () => {
    if (!result) return
    saved.doSave({ name: `AK/SK · ${method} ${path}`, inputs: { accessKey, secretKey, method, path, signTime }, result })
  }

  const getTs = () => {
    const d = new Date(signTime)
    return isNaN(d.getTime()) ? Math.floor(Date.now() / 1000) : Math.floor(d.getTime() / 1000)
  }

  const refreshTime = () => {
    const d = new Date()
    const pad = n => String(n).padStart(2, '0')
    setSignTime(`${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`)
  }

  const handleSign = async () => {
    if (!accessKey || !secretKey) { message.warning('请输入 AK/SK'); return }
    setSigning(true)
    try {
      const ts = String(getTs())
      const stringToSign = `${method}\n${path}\n${ts}\n${accessKey}`
      const res = await api.post('/toolbox/hmac-sign', { message: stringToSign, secret: secretKey, algorithm: 'SHA-256' })
      const d = res.data?.data || res.data || res
      if (d.error || res.error) { message.error(d.error || res.error); return }
      setResult({ stringToSign, signature: d.base64, auth: `AK ${accessKey}:${d.base64}`, ts })
    } catch (e) { message.error(e.message) }
    finally { setSigning(false) }
  }

  return (
    <div>
      {/* AK/SK 输入 */}
      <div style={{ display: 'flex', gap: 14, marginBottom: 16 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>Access Key (AK)</div>
          <Input spellCheck={false} value={accessKey} onChange={e => setAccessKey(e.target.value)} style={{ fontFamily: MONO, fontSize: 12 }} placeholder="输入 Access Key" />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>Secret Key (SK)</div>
          <Input.Password value={secretKey} onChange={e => setSecretKey(e.target.value)} style={{ fontFamily: MONO, fontSize: 12 }} placeholder="输入 Secret Key" />
        </div>
      </div>

      {/* 请求信息 + 时间 */}
      <div style={{ display: 'flex', gap: 14, marginBottom: 16, alignItems: 'flex-end' }}>
        <div style={{ width: 90 }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>Method</div>
          <Select value={method} onChange={setMethod} style={{ width: '100%' }}
            options={['GET','POST','PUT','DELETE','PATCH'].map(m => ({ value: m, label: m }))} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>请求路径</div>
          <Input spellCheck={false} value={path} onChange={e => setPath(e.target.value)} style={{ fontFamily: MONO, fontSize: 12 }} placeholder="/api/resource" />
        </div>
        <div style={{ width: 200 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <span style={{ fontSize: 12, color: theme.primary, fontWeight: 600 }}>签名时间</span>
            <Button type="link" size="small" style={{ padding: 0, fontSize: 11, height: 'auto', color: theme.primary }}
              onClick={refreshTime}>当前时间</Button>
          </div>
          <Input value={signTime} onChange={e => setSignTime(e.target.value)} style={{ fontSize: 12 }}
            placeholder="2024-07-01 12:00:00" />
        </div>
        <Button type="primary" onClick={handleSign} loading={signing} style={{ height: 32 }}>生成签名</Button>
      </div>

      {/* 结果 */}
      {result && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>签名字符串</div>
              <pre style={{ margin: 0, padding: 10, background: theme.pale, borderRadius: 10, fontFamily: MONO, fontSize: 11, border: `1px solid ${theme.border}`, whiteSpace: 'pre-wrap' }}>{result.stringToSign}</pre>
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Signature (Base64)</div>
              <div style={{ padding: 10, background: theme.pale, borderRadius: 10, fontFamily: MONO, fontSize: 11, border: `1px solid ${theme.border}`, wordBreak: 'break-all' }}>
                {result.signature}
                <Button type="link" size="small" icon={<CopyOutlined />} style={{ float: 'right', padding: 0, fontSize: 11 }}
                  onClick={() => copy(result.signature)}>复制</Button>
              </div>
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>请求头（可直接复制使用）</div>
            <div style={{ padding: 10, background: theme.pale, borderRadius: 10, fontFamily: MONO, fontSize: 12, border: `1px solid ${theme.border}`, lineHeight: 1.8 }}>
              <div>Authorization: <strong>{result.auth}</strong>
                <Button type="link" size="small" icon={<CopyOutlined />} style={{ padding: 0, fontSize: 11, marginLeft: 8 }}
                  onClick={() => copy(result.auth)}>复制</Button></div>
              <div>X-Timestamp: <strong>{result.ts}</strong>
                <Button type="link" size="small" icon={<CopyOutlined />} style={{ padding: 0, fontSize: 11, marginLeft: 8 }}
                  onClick={() => copy(result.ts)}>复制</Button></div>
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button icon={<SaveOutlined />} onClick={handleSave}>保存此凭证</Button>
          </div>
        </div>
      )}

      {!result && (
        <div style={{ textAlign: 'center', padding: 30, color: '#c9cdd4', fontSize: 13 }}>
          填写 AK/SK 和请求信息后点击「生成签名」
        </div>
      )}

      {saved.ui}
    </div>
  )
}

function OAuth2Panel({ theme }) {
  const [grantType, setGrantType] = useState('client_credentials')
  const [tokenUrl, setTokenUrl] = useState('')
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [scope, setScope] = useState('')
  const [fetching, setFetching] = useState(false)
  const [tokenResult, setTokenResult] = useState(null)

  const saved = AuthSavedList({
    type: 'oauth2', theme,
    onRestore: (item) => {
      setGrantType(item.inputs.grantType || 'client_credentials')
      setTokenUrl(item.inputs.tokenUrl || '')
      setClientId(item.inputs.clientId || '')
      setClientSecret(item.inputs.clientSecret || '')
      setScope(item.inputs.scope || '')
      if (item.result) setTokenResult(item.result)
    },
    getCopyText: (item) => item.result?.parsed?.access_token,
  })

  const handleSave = () => {
    if (!tokenResult || tokenResult.error) return
    const name = `OAuth2 · ${clientId || tokenUrl}`
    saved.doSave({ name, inputs: { grantType, tokenUrl, clientId, clientSecret, scope }, result: tokenResult })
  }

  const handleFetch = async () => {
    if (!tokenUrl || !clientId) { message.warning('请填写 Token URL 和 Client ID'); return }
    setFetching(true); setTokenResult(null)
    try {
      const bodyParts = [`grant_type=${grantType}`, `client_id=${encodeURIComponent(clientId)}`]
      if (clientSecret) bodyParts.push(`client_secret=${encodeURIComponent(clientSecret)}`)
      if (scope) bodyParts.push(`scope=${encodeURIComponent(scope)}`)
      const res = await api.post('/toolbox/http-request', {
        method: 'POST', url: tokenUrl,
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: bodyParts.join('&'),
      })
      const d = res.data?.data || res.data || res
      if (d.error) { setTokenResult({ error: d.error }); return }
      try {
        const parsed = JSON.parse(d.body)
        setTokenResult({ raw: d, parsed })
      } catch { setTokenResult({ raw: d }) }
    } catch (e) { setTokenResult({ error: e.message }) }
    finally { setFetching(false) }
  }

  return (
    <div>
      {/* 配置区 — 卡片样式 */}
      <div style={{ padding: 16, background: theme.pale, borderRadius: 14, border: `1px solid ${theme.border}`, marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-end', marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>Grant Type</div>
            <Radio.Group value={grantType} onChange={e => setGrantType(e.target.value)} size="small">
              <Radio value="client_credentials">Client Credentials</Radio>
              <Radio value="password">Password</Radio>
            </Radio.Group>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>Token URL *</div>
            <Input spellCheck={false} value={tokenUrl} onChange={e => setTokenUrl(e.target.value)} placeholder="https://auth.example.com/oauth/token" style={{ fontFamily: MONO, fontSize: 12 }} />
          </div>
        </div>
        <div style={{ display: 'flex', gap: 14, marginBottom: 14 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>Client ID *</div>
            <Input spellCheck={false} value={clientId} onChange={e => setClientId(e.target.value)} style={{ fontFamily: MONO, fontSize: 12 }} placeholder="your-client-id" />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>Client Secret</div>
            <Input.Password value={clientSecret} onChange={e => setClientSecret(e.target.value)} style={{ fontFamily: MONO, fontSize: 12 }} placeholder="your-client-secret" />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>Scope（可选）</div>
            <Input spellCheck={false} value={scope} onChange={e => setScope(e.target.value)} placeholder="read write" style={{ fontFamily: MONO, fontSize: 12 }} />
          </div>
        </div>
        <Button type="primary" loading={fetching} onClick={handleFetch}>获取 Token</Button>
      </div>

      {/* 结果区 */}
      {tokenResult ? (tokenResult.error ? (
        <div style={{ color: '#e8453c', padding: 14, background: 'rgba(232,69,60,0.1)', borderRadius: 12, fontSize: 12, border: '1px solid rgba(232,69,60,0.25)' }}>{tokenResult.error}</div>
      ) : (
        <div style={{ padding: 16, border: `1px solid ${theme.border}`, borderRadius: 14, background: '#fff' }}>
          {tokenResult.parsed?.access_token && (<>
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>Access Token</div>
              <div style={{ padding: 10, background: theme.pale, borderRadius: 10, fontFamily: MONO, fontSize: 11, border: `1px solid ${theme.border}`, wordBreak: 'break-all', position: 'relative' }}>
                {tokenResult.parsed.access_token}
                <Button type="link" size="small" icon={<CopyOutlined />} style={{ position: 'absolute', top: 4, right: 4, fontSize: 11 }}
                  onClick={() => copy(tokenResult.parsed.access_token)}>复制</Button>
              </div>
            </div>
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>请求时这样用</div>
              <div style={{ padding: 10, background: '#1e1e2e', color: '#cdd6f4', borderRadius: 10, fontFamily: MONO, fontSize: 12 }}>
                Authorization: <span style={{ color: '#a6e3a1' }}>Bearer {tokenResult.parsed.access_token.substring(0, 30)}...</span>
              </div>
              <Button size="small" icon={<CopyOutlined />} style={{ marginTop: 6 }}
                onClick={() => copy(`Bearer ${tokenResult.parsed.access_token}`)}>复制完整 Header 值</Button>
            </div>
            <div style={{ display: 'flex', gap: 16, fontSize: 11, color: '#86909c', marginBottom: 14 }}>
              {tokenResult.parsed.expires_in && <span>有效期: {tokenResult.parsed.expires_in}秒</span>}
              {tokenResult.parsed.token_type && <span>类型: {tokenResult.parsed.token_type}</span>}
              {tokenResult.parsed.scope && <span>Scope: {tokenResult.parsed.scope}</span>}
            </div>
          </>)}
          {!tokenResult.parsed?.access_token && (
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 12, color: theme.primary, fontWeight: 600, marginBottom: 6 }}>响应</div>
              <pre style={{ padding: 10, background: theme.pale, borderRadius: 10, fontFamily: MONO, fontSize: 11, border: `1px solid ${theme.border}`, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0, maxHeight: 200, overflow: 'auto' }}>
                {tokenResult.parsed ? JSON.stringify(tokenResult.parsed, null, 2) : tokenResult.raw?.body || '(empty)'}
              </pre>
            </div>
          )}
          <div style={{ borderTop: `1px dashed ${theme.border}`, paddingTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
            <Button icon={<SaveOutlined />} onClick={handleSave}>保存此凭证</Button>
          </div>
        </div>
      )) : (
        <div style={{ textAlign: 'center', padding: 30, color: '#c9cdd4', fontSize: 13 }}>
          填写 OAuth2 配置后点击「获取 Token」
        </div>
      )}

      {saved.ui}
    </div>
  )
}

// ━━━ 主页面 ━━━
// ━━━ 主页面 ━━━
const TOOL_MAP = { json: JsonTool, codec: CodecTool, timestamp: TimestampTool, regex: RegexTool, datagen: DataGenTool, diff: DiffTool, jwt: JwtTool }

export default function Toolbox() {
  const [activeTool, setActiveTool] = useState('json')
  const theme = THEMES[activeTool]
  const ActiveComponent = TOOL_MAP[activeTool]

  const gradient = `linear-gradient(160deg, ${theme.light}88 0%, #f0ecfb44 40%, #edf5f044 70%, ${theme.pale} 100%)`

  return (
    <div className="toolbox-page" style={{
      '--tb-primary': theme.primary,
      '--tb-light': theme.light,
      '--tb-bg': theme.bg,
      '--tb-pale': theme.pale,
      '--tb-border': theme.border,
      display: 'flex', flexDirection: 'column',
      height: 'calc(100vh - 70px)',
      background: gradient,
      transition: 'background 0.5s ease',
    }}>
      <style>{TOOLBOX_CSS}</style>

      <div style={{
        padding: '10px 24px',
        background: 'rgba(255,255,255,0.45)',
        borderBottom: '1px solid rgba(0,0,0,0.04)',
        display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0,
      }}>
        {/* 图标块不做实色：淡底 + 细边框 + 彩色图标（全站其他头像/图标都是这个做法） */}
        <div style={{
          width: 34, height: 34, borderRadius: 11,
          background: theme.light,
          border: `1px solid ${theme.border}`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          transition: 'all 0.3s ease',
        }}>
          <ToolOutlined style={{ fontSize: 17, color: theme.primary }} />
        </div>
        <div>
          <div style={{ fontWeight: 700, fontSize: 16, color: '#1d2129', lineHeight: 1.3, fontFamily: FONT, letterSpacing: 1 }}>工具箱</div>
          <div style={{ fontSize: 11, color: '#86909c', lineHeight: 1.2, fontFamily: FONT, letterSpacing: 0.5 }}>开发者的随身百宝箱</div>
        </div>
      </div>

      <div style={{ flex: 1, display: 'flex', minHeight: 0, padding: '8px 10px 10px', gap: 8 }}>
        <div style={{
          width: 200, flexShrink: 0,
          background: 'rgba(255,255,255,0.5)',
          borderRadius: 16,
          overflow: 'auto',
          padding: '10px 8px 8px',
          display: 'flex', flexDirection: 'column',
        }}>
          <div style={{ flex: 1 }}>
            {TOOLS.map(t => {
              const tt = THEMES[t.key]
              const active = activeTool === t.key
              return (
                <div key={t.key} className="tb-nav-item" onClick={() => setActiveTool(t.key)} style={{
                  padding: '10px 12px', borderRadius: 12, marginBottom: 4,
                  display: 'flex', alignItems: 'center', gap: 10,
                  background: active ? tt.bg : 'transparent',
                  borderLeft: active ? `4px solid ${tt.primary}` : '4px solid transparent',
                  boxShadow: active ? `0 2px 8px ${tt.primary}15` : 'none',
                }}>
                  <span style={{
                    fontSize: 18, color: active ? tt.primary : '#86909c',
                    transition: 'color 0.25s',
                  }}>{t.icon}</span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{
                      fontSize: 13, fontWeight: active ? 600 : 400,
                      color: active ? tt.primary : '#4e5969',
                      transition: 'all 0.25s', lineHeight: 1.3,
                      fontFamily: FONT, letterSpacing: 0.5,
                    }}>{t.label}</div>
                    <div style={{
                      fontSize: 11, color: active ? `${tt.primary}99` : '#c9cdd4',
                      transition: 'color 0.25s', lineHeight: 1.4,
                      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                      fontFamily: FONT, letterSpacing: 0.3,
                    }}>{t.desc}</div>
                  </div>
                </div>
              )
            })}
          </div>
          <div style={{ textAlign: 'center', fontSize: 12, color: '#c9cdd4', padding: '10px 0', letterSpacing: 4, fontFamily: FONT }}>
            ✿ 宁静致远
          </div>
        </div>

        <div style={{
          flex: 1, minWidth: 0, overflow: 'hidden',
          background: 'rgba(255,255,255,0.4)',
          borderRadius: 16,
        }}>
          <div key={activeTool} className="tb-content-fade" style={{ height: '100%' }}>
            <ActiveComponent theme={theme} />
          </div>
        </div>
      </div>
    </div>
  )
}
