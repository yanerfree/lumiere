import { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import { Input, Select, Button, Tag, Space, Tooltip, Dropdown, Popover, Checkbox, Spin, message, AutoComplete, Modal , InputNumber} from 'antd'
import {
  PlusOutlined, DeleteOutlined, HolderOutlined, CaretRightOutlined, CaretDownOutlined,
  FolderOutlined, RetweetOutlined, BranchesOutlined, ApiOutlined,
  ClockCircleOutlined, UnorderedListOutlined, ThunderboltOutlined, CopyOutlined,
  CodeOutlined, EditOutlined, CheckCircleOutlined, FieldStringOutlined, GlobalOutlined,
  SendOutlined, FormatPainterOutlined, LockOutlined, LoadingOutlined,
  SwapOutlined, SearchOutlined, ColumnHeightOutlined, ImportOutlined, DownOutlined,
  FormOutlined,
} from '@ant-design/icons'
import { api } from '../utils/request'
import { copyToClipboard } from '../utils/clipboard'

const methodColors = {
  GET: { color: '#0ea5a0', bg: 'rgba(14,165,160,0.1)', border: 'rgba(14,165,160,0.3)' },
  POST: { color: '#ff7d00', bg: 'rgba(255,125,0,0.1)', border: 'rgba(255,125,0,0.3)' },
  PUT: { color: '#faad14', bg: 'rgba(250,173,20,0.12)', border: 'rgba(250,173,20,0.3)' },
  PATCH: { color: '#7c5cbf', bg: 'rgba(124,92,191,0.1)', border: 'rgba(124,92,191,0.3)' },
  DELETE: { color: '#e8453c', bg: 'rgba(232,69,60,0.1)', border: 'rgba(232,69,60,0.3)' },
}

// ---- 动态变量 ----
const dynamicVars = [
  { key: '$uuid', label: 'UUID', desc: '随机 UUID v4', example: 'a1b2c3d4-...' },
  { key: '$timestamp', label: '时间戳', desc: '当前 Unix 时间戳（秒）', example: '1717488000' },
  { key: '$timestampMs', label: '毫秒时间戳', desc: '当前 Unix 时间戳（毫秒）', example: '1717488000123' },
  { key: '$isoDate', label: 'ISO 日期', desc: 'ISO 8601 格式', example: '2026-06-04T12:00:00Z' },
  { key: '$randomInt', label: '随机整数', desc: '0-99999 随机数', example: '42731' },
  { key: '$randomFloat', label: '随机浮点', desc: '0-1 随机浮点数', example: '0.7823' },
  { key: '$randomEmail', label: '随机邮箱', desc: '随机邮箱地址', example: 'user_38271@test.com' },
  { key: '$randomPhone', label: '随机手机号', desc: '随机 11 位手机号', example: '138xxxx1234' },
  { key: '$randomString', label: '随机字符串', desc: '8 位随机字符', example: 'aB3kP9mZ' },
  { key: '$randomName', label: '随机姓名', desc: '随机中文姓名', example: '张三' },
]

// ---- 前置脚本片段（按使用场景分组）----
const preScriptSnippets = [
  { key: 'setAuth', label: '从上下文设置认证', desc: '用前一步提取的 token 设置 Authorization', group: '认证 & Header',
    code: '# 从上下文取 token 设置认证头\ntoken = context.get("token", "")\nheaders["Authorization"] = f"Bearer {token}"' },
  { key: 'urlPathParam', label: 'URL 路径参数替换', desc: '用上下文变量替换 URL 中的占位符', group: '认证 & Header',
    code: '# 用上下文变量替换 URL 路径参数\nresource_id = context.get("resource_id", "")\nurl = url.replace("{resource_id}", str(resource_id))' },
  { key: 'modifyBodyField', label: '修改 Body 字段', desc: '解析 JSON Body 后修改指定字段', group: '请求体操作',
    code: 'import json\n_body = json.loads(body)\n_body["user_id"] = context.get("user_id", "")\n_body["project_id"] = context.get("project_id", "")\nbody = json.dumps(_body, ensure_ascii=False)' },
  { key: 'replaceBody', label: '替换整个请求体', desc: '用上下文数据动态构造完整 Body', group: '请求体操作',
    code: 'import json\nbody = json.dumps({\n    "username": context.get("username", "test_user"),\n    "password": "Test@123",\n    "role": "member",\n}, ensure_ascii=False)' },
  { key: 'genTestData', label: '生成唯一测试数据', desc: '随机用户名/邮箱存入 context 供后续使用', group: '数据生成',
    code: 'import random, string, time\n_suffix = f"{int(time.time())}_{random.randint(100,999)}"\ncontext["username"] = f"test_user_{_suffix}"\ncontext["email"] = f"test_{_suffix}@example.com"' },
]

const postScriptSnippets = [
  { key: 'extractToken', label: '提取登录 Token', desc: '从登录响应中提取 token 存入上下文', group: '提取数据',
    code: 'data = response.json()\ncontext["token"] = data["data"]["token"]\nprint(f"[POST] token 已提取: {context[\'token\'][:20]}...")' },
  { key: 'extractId', label: '提取资源 ID', desc: '创建资源后提取 ID 供后续步骤使用', group: '提取数据',
    code: 'data = response.json()\ncontext["resource_id"] = data["data"]["id"]\nprint(f"[POST] 资源 ID: {context[\'resource_id\']}")' },
  { key: 'extractBatch', label: '批量提取字段', desc: '一次提取多个字段到上下文', group: '提取数据',
    code: 'data = response.json()["data"]\nfor key in ["id", "name", "status"]:\n    context[key] = data.get(key, "")\n    print(f"[POST] {key} = {context[key]}")' },
  { key: 'prettyPrint', label: '格式化打印响应', desc: '美化输出状态码 + JSON 响应体', group: '调试 & 日志',
    code: 'import json\nprint(f"[POST] 状态码: {response.status_code}")\ntry:\n    print(f"[POST] 响应体:\\n{json.dumps(response.json(), indent=2, ensure_ascii=False)}")\nexcept:\n    print(f"[POST] 响应体: {response.text[:500]}")' },
  { key: 'errorLog', label: '条件错误日志', desc: '非 2xx 时记录详细错误信息', group: '调试 & 日志',
    code: 'if response.status_code >= 400:\n    print(f"[ERROR] {response.status_code} - {response.url}")\n    print(f"[ERROR] 响应: {response.text[:300]}")' },
  { key: 'chainCleanup', label: '链式清理资源', desc: '用上下文中的 ID + Token 发送删除请求', group: '清理',
    code: '# 清理测试创建的资源\nresource_id = context.get("resource_id")\nif resource_id:\n    _headers = {"Authorization": f"Bearer {context.get(\'token\', \'\')}"}\n    _resp = client.delete(f"/api/resources/{resource_id}", headers=_headers)\n    print(f"[CLEANUP] 删除 {resource_id}: {_resp.status_code}")' },
]

// ---- 工具组件 ----
function insertAtCursor(ref, cur, snippet, onChange) {
  const el = ref.current?.resizableTextArea?.textArea
  if (el) {
    const s = el.selectionStart, e = el.selectionEnd
    const before = cur.slice(0, s), after = cur.slice(e)
    const sep = before.length > 0 && !before.endsWith('\n') ? '\n' : ''
    onChange(before + sep + snippet + '\n' + after)
    requestAnimationFrame(() => { const p = (before + sep + snippet + '\n').length; el.setSelectionRange(p, p); el.focus() })
  } else {
    const sep = cur && !cur.endsWith('\n') ? '\n' : ''
    onChange(cur + sep + snippet + '\n')
  }
}

function SnippetPicker({ snippets, onInsert }) {
  const grouped = useMemo(() => {
    const groups = []
    let curGroup = null
    for (const s of snippets) {
      if (s.group && s.group !== curGroup) {
        curGroup = s.group
        groups.push({ type: 'header', label: curGroup })
      }
      groups.push({ type: 'item', ...s })
    }
    return groups
  }, [snippets])

  return (
    <Popover trigger="click" placement="bottomRight" arrow={false}
      content={
        <div style={{ width: 340, maxHeight: 400, overflow: 'auto' }}>
          <div style={{ fontSize: 11, color: '#86909c', padding: '4px 8px 6px', fontWeight: 600 }}>点击插入代码片段</div>
          {grouped.map((item, idx) => item.type === 'header' ? (
            <div key={`g-${idx}`} style={{ padding: '6px 10px 2px', fontSize: 11, color: '#0ea5a0', fontWeight: 600, borderTop: idx > 0 ? '1px solid rgba(0,0,0,0.06)' : 'none', marginTop: idx > 0 ? 4 : 0 }}>
              {item.label}
            </div>
          ) : (
            <div key={item.key} onClick={() => onInsert(item.code)}
              style={{ padding: '8px 10px', cursor: 'pointer', borderRadius: 6, marginBottom: 2 }}
              onMouseEnter={e => e.currentTarget.style.background = '#f0f5ff'}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                <CodeOutlined style={{ fontSize: 11, color: '#0ea5a0' }} />
                <span style={{ fontSize: 12, fontWeight: 600, color: '#1d2129' }}>{item.label}</span>
                <span style={{ fontSize: 11, color: '#86909c' }}>{item.desc}</span>
              </div>
              <pre style={{ margin: 0, padding: '4px 8px', background: 'rgba(0,0,0,0.02)', borderRadius: 8, fontSize: 11, color: '#4e5969', fontFamily: 'var(--font-mono)', lineHeight: 1.5, whiteSpace: 'pre-wrap', border: '1px solid rgba(0,0,0,0.05)', maxHeight: 80, overflow: 'hidden' }}>{item.code}</pre>
            </div>
          ))}
        </div>
      }>
      <Tooltip title="插入代码片段">
        <Button type="text" size="small" icon={<CodeOutlined />} style={{ color: '#0ea5a0', fontSize: 12 }}>片段</Button>
      </Tooltip>
    </Popover>
  )
}

function VarPicker({ onInsert }) {
  return (
    <Popover trigger="click" placement="bottomRight" arrow={false}
      content={
        <div style={{ width: 260, maxHeight: 300, overflow: 'auto' }}>
          <div style={{ fontSize: 11, color: '#86909c', padding: '4px 8px', fontWeight: 600 }}>点击插入动态变量</div>
          {dynamicVars.map(v => (
            <div key={v.key} onClick={() => onInsert(`{{${v.key}}}`)}
              style={{ padding: '6px 8px', cursor: 'pointer', borderRadius: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
              onMouseEnter={e => e.currentTarget.style.background = '#f0f5ff'}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
              <div>
                <div style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: '#0ea5a0' }}>{`{{${v.key}}}`}</div>
                <div style={{ fontSize: 11, color: '#86909c' }}>{v.desc}</div>
              </div>
              <span style={{ fontSize: 11, color: '#86909c', fontFamily: 'var(--font-mono)' }}>{v.example}</span>
            </div>
          ))}
        </div>
      }>
      <Tooltip title="插入动态变量">
        <Button type="text" size="small" icon={<ThunderboltOutlined />} style={{ color: '#faad14', fontSize: 12 }} />
      </Tooltip>
    </Popover>
  )
}

// ---- 常用 Header 预设 ----
const commonHeaders = [
  { value: 'Content-Type', desc: 'application/json' },
  { value: 'Accept', desc: 'application/json' },
  { value: 'Authorization', desc: 'Bearer <token>' },
  { value: 'X-Request-ID', desc: 'UUID 追踪' },
  { value: 'Cache-Control', desc: 'no-cache' },
  { value: 'Accept-Language', desc: 'zh-CN,en' },
  { value: 'User-Agent', desc: 'testBench/1.0' },
  { value: 'X-API-Key', desc: 'API 密钥' },
  { value: 'Origin', desc: 'CORS 来源' },
  { value: 'Referer', desc: '来源页面' },
]
const headerOptions = commonHeaders.map(h => ({ value: h.value, label: <span>{h.value} <span style={{ fontSize: 11, color: '#86909c' }}>{h.desc}</span></span> }))

// ---- cURL 解析器 ----
function parseCurl(curlStr) {
  const s = curlStr.replace(/\\\n/g, ' ').replace(/\s+/g, ' ').trim()
  const result = { method: 'GET', url: '', headers: [], body: '', bodyType: 'json', auth: { type: 'none' }, params: [] }

  const re = /'[^']*'|"[^"]*"|\S+/g
  const tokens = []
  let m
  while ((m = re.exec(s)) !== null) {
    let t = m[0]
    if ((t.startsWith("'") && t.endsWith("'")) || (t.startsWith('"') && t.endsWith('"'))) t = t.slice(1, -1)
    tokens.push(t)
  }

  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i]
    if (t === 'curl') continue
    if (t === '-X' || t === '--request') { result.method = (tokens[++i] || 'GET').toUpperCase(); continue }
    if (t === '-H' || t === '--header') {
      const hv = tokens[++i] || ''
      const ci = hv.indexOf(':')
      if (ci > 0) {
        const k = hv.slice(0, ci).trim(), v = hv.slice(ci + 1).trim()
        if (k.toLowerCase() === 'authorization') {
          if (v.toLowerCase().startsWith('bearer ')) { result.auth = { type: 'bearer', token: v.slice(7) }; continue }
          if (v.toLowerCase().startsWith('basic ')) {
            try { const d = atob(v.slice(6)); const [u, ...p] = d.split(':'); result.auth = { type: 'basic', username: u, password: p.join(':') } } catch {}
            continue
          }
        }
        result.headers.push({ key: k, value: v, enabled: true, desc: '' })
      }
      continue
    }
    if (t === '-d' || t === '--data' || t === '--data-raw' || t === '--data-binary') { result.body = tokens[++i] || ''; continue }
    if (t === '-u' || t === '--user') {
      const cred = tokens[++i] || ''
      const [u, ...p] = cred.split(':')
      result.auth = { type: 'basic', username: u, password: p.join(':') }
      continue
    }
    if (t.startsWith('-')) { if (!t.includes('=') && i + 1 < tokens.length && !tokens[i + 1].startsWith('-')) i++; continue }
    if (!result.url && (t.startsWith('http') || t.startsWith('/'))) {
      const urlObj = (() => { try { return new URL(t) } catch { return null } })()
      if (urlObj) {
        result.url = urlObj.origin + urlObj.pathname
        urlObj.searchParams.forEach((v, k) => result.params.push({ key: k, value: v, enabled: true, desc: '' }))
      } else {
        result.url = t
      }
    }
  }

  if (result.body) {
    try { JSON.parse(result.body); result.bodyType = 'json' } catch { result.bodyType = 'raw' }
    if (result.method === 'GET') result.method = 'POST'
  }

  const ct = result.headers.find(h => h.key.toLowerCase() === 'content-type')
  if (ct?.value?.includes('x-www-form-urlencoded')) result.bodyType = 'form'
  if (ct?.value?.includes('multipart/form-data')) result.bodyType = 'form-data'

  return result
}

// ---- KvEditor (Apifox 风格：checkbox + key + value + desc + bulk edit) ----
function KvEditor({ items = [], onChange, keyPh = 'Key', valPh = 'Value' }) {
  const [bulkMode, setBulkMode] = useState(false)
  const [bulkText, setBulkText] = useState('')
  const up = (i, f, v) => onChange(items.map((r, j) => j === i ? { ...r, [f]: v } : r))
  const typeName = keyPh === 'Header' ? '请求头' : '参数'
  const isHeader = keyPh === 'Header'

  const toBulk = () => {
    const t = items.filter(r => r.key).map(r => {
      const prefix = r.enabled === false ? '// ' : ''
      const desc = r.desc ? `  // ${r.desc}` : ''
      return `${prefix}${r.key}: ${r.value || ''}${desc}`
    }).join('\n')
    setBulkText(t)
    setBulkMode(true)
  }

  const fromBulk = () => {
    const newItems = bulkText.split('\n').filter(l => l.trim()).map(line => {
      const disabled = line.trimStart().startsWith('//')
      const clean = disabled ? line.replace(/^\s*\/\/\s*/, '') : line
      const descMatch = clean.match(/\s+\/\/\s*(.+)$/)
      const desc = descMatch ? descMatch[1] : ''
      const withoutDesc = descMatch ? clean.slice(0, descMatch.index) : clean
      const colonIdx = withoutDesc.indexOf(':')
      if (colonIdx < 0) return { key: withoutDesc.trim(), value: '', enabled: !disabled, desc }
      return { key: withoutDesc.slice(0, colonIdx).trim(), value: withoutDesc.slice(colonIdx + 1).trim(), enabled: !disabled, desc }
    })
    onChange(newItems)
    setBulkMode(false)
  }

  if (bulkMode) {
    return (
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
          <span style={{ fontSize: 11, color: '#86909c' }}>每行一个，格式: <code style={{ fontSize: 11, background: 'rgba(0,0,0,0.04)', padding: '1px 4px', borderRadius: 3 }}>key: value  // 描述</code></span>
          <Space size={4}>
            <Button size="small" onClick={() => setBulkMode(false)}>取消</Button>
            <Button size="small" type="primary" onClick={fromBulk}>确定</Button>
          </Space>
        </div>
        <Input.TextArea spellCheck={false} value={bulkText} onChange={e => setBulkText(e.target.value)}
          autoSize={{ minRows: 6, maxRows: 18 }} placeholder={`Content-Type: application/json\nAuthorization: Bearer {{token}}  // 认证\n// X-Debug: true  // 注释行=禁用`}
          style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} />
      </div>
    )
  }

  return (
    <div>
      {items.length > 0 && (
        <div style={{ display: 'flex', gap: 4, marginBottom: 4, padding: '0 4px', fontSize: 11, color: '#86909c', fontWeight: 600, alignItems: 'center' }}>
          <span style={{ width: 20 }}></span>
          <span style={{ flex: 3 }}>{keyPh}</span>
          <span style={{ flex: 4 }}>{valPh}</span>
          <span style={{ flex: 3 }}>描述</span>
          <span style={{ width: 24 }}></span>
          <Tooltip title="批量编辑">
            <Button type="text" size="small" icon={<EditOutlined />} onClick={toBulk} style={{ width: 20, height: 16, fontSize: 11, color: '#86909c' }} />
          </Tooltip>
        </div>
      )}
      {items.length === 0 && (
        <div style={{ padding: '16px 0', textAlign: 'center', color: '#86909c', fontSize: 12 }}>
          暂无{typeName}，点击下方按钮添加
        </div>
      )}
      {items.map((r, i) => (
        <div key={i} style={{ display: 'flex', gap: 4, marginBottom: 3, alignItems: 'center', opacity: r.enabled === false ? 0.45 : 1, transition: 'opacity 0.15s' }}>
          <Checkbox checked={r.enabled !== false} onChange={e => up(i, 'enabled', e.target.checked)} style={{ marginRight: -2 }} />
          {isHeader ? (
            <AutoComplete size="small" value={r.key} placeholder={keyPh} onChange={v => up(i, 'key', v)}
              options={headerOptions.filter(o => !r.key || o.value.toLowerCase().includes(r.key.toLowerCase()))}
              style={{ flex: 3, fontFamily: 'var(--font-mono)', fontSize: 11 }} />
          ) : (
            <Input spellCheck={false} size="small" value={r.key} placeholder={keyPh} onChange={e => up(i, 'key', e.target.value)} style={{ flex: 3, fontFamily: 'var(--font-mono)', fontSize: 11 }} />
          )}
          <Input spellCheck={false} size="small" value={r.value} placeholder={valPh} onChange={e => up(i, 'value', e.target.value)} style={{ flex: 4, fontFamily: 'var(--font-mono)', fontSize: 11 }} />
          <Input size="small" value={r.desc || ''} placeholder="描述" onChange={e => up(i, 'desc', e.target.value)} style={{ flex: 3, fontSize: 11, color: '#86909c' }} />
          <Button type="text" size="small" icon={<DeleteOutlined />} danger onClick={() => onChange(items.filter((_, j) => j !== i))} />
        </div>
      ))}
      <div style={{ display: 'flex', gap: 4 }}>
        <Button type="dashed" size="small" style={{ flex: 1 }} icon={<PlusOutlined />} onClick={() => onChange([...items, { key: '', value: '', enabled: true, desc: '' }])}>添加</Button>
        {items.length > 0 && <Tooltip title="批量编辑"><Button size="small" icon={<EditOutlined />} onClick={toBulk} /></Tooltip>}
      </div>
    </div>
  )
}

// ===========================================================================
// 左侧面板：紧凑步骤列表（Apifox 风格）
// ===========================================================================

function CompactApiRow({ step, index, isSelected, onClick, onRemove, onCopy, onDragStart, onDragOver, onDrop, runChecked, onRunCheck }) {
  const method = step.method || 'GET'
  const mc = methodColors[method] || methodColors.GET
  const label = step.action || step.url || '未命名请求'
  const subLabel = step.action && step.url ? step.url : null
  const postOps = getOps(step, 'postOperations')
  const assertCount = postOps.filter(o => o.type === 'assertion').length
  const extractCount = postOps.filter(o => o.type === 'extractor').length
  const [hovered, setHovered] = useState(false)

  return (
    <div draggable onDragStart={e => { e.dataTransfer.effectAllowed = 'move'; onDragStart?.(index) }}
      onDragOver={e => { e.preventDefault(); onDragOver?.(index) }}
      onDrop={e => { e.preventDefault(); onDrop?.(index) }}
      onClick={onClick}
      onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', cursor: 'pointer',
        background: isSelected ? mc.bg : hovered ? 'rgba(0,0,0,0.02)' : 'transparent',
        borderLeft: isSelected ? `3px solid ${mc.color}` : '3px solid transparent',
        transition: 'all 0.12s',
        // 没勾选的整行压暗：光靠一个空复选框，扫一眼 19 步看不出哪些不跑。
        opacity: onRunCheck && !runChecked ? 0.45 : 1,
      }}>
      {onRunCheck && (
        <Checkbox checked={runChecked} onClick={e => e.stopPropagation()}
          onChange={e => onRunCheck(e.target.checked)} style={{ flexShrink: 0 }} />
      )}
      <HolderOutlined style={{ color: 'rgba(0,0,0,0.25)', cursor: 'grab', fontSize: 11, flexShrink: 0, opacity: hovered ? 1 : 0, transition: 'opacity 0.1s' }} />
      <Tag style={{ margin: 0, fontWeight: 700, fontSize: 11, background: mc.bg, color: mc.color, border: 'none', padding: '0 5px', lineHeight: '16px', minWidth: 38, textAlign: 'center' }}>{method}</Tag>
      <div style={{ flex: 1, overflow: 'hidden', minWidth: 0 }}>
        <div style={{ fontSize: 12, color: '#1d2129', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          textDecoration: onRunCheck && !runChecked ? 'line-through' : 'none' }}>{label}</div>
        {subLabel && <div style={{ fontSize: 11, color: '#86909c', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'var(--font-mono)' }}>{subLabel}</div>}
      </div>
      <div style={{ display: 'flex', gap: 2, flexShrink: 0, alignItems: 'center' }}>
        {/* 光印两个数字（"3 2"），得逐个悬停才知道哪个是断言哪个是提取。
            加一个汉字前缀，不悬停也读得出来。tooltip 保留给要看全称的人。 */}
        {assertCount > 0 && <span title={`${assertCount} 个断言`} style={{ fontSize: 11, background: 'var(--green-bg)', color: '#0ea5a0', borderRadius: 8, padding: '0 4px', lineHeight: '16px', fontWeight: 600 }}>断{assertCount}</span>}
        {extractCount > 0 && <span title={`${extractCount} 个提取物`} style={{ fontSize: 11, background: 'var(--purple-bg)', color: '#7c5cbf', borderRadius: 8, padding: '0 4px', lineHeight: '16px', fontWeight: 600 }}>取{extractCount}</span>}
      </div>
      {hovered && (
        <div style={{ display: 'flex', gap: 0, flexShrink: 0 }} onClick={e => e.stopPropagation()}>
          <Tooltip title="复制"><Button type="text" size="small" icon={<CopyOutlined />} onClick={onCopy} style={{ width: 20, height: 20, fontSize: 11, color: '#86909c' }} /></Tooltip>
          <Tooltip title="删除"><Button type="text" size="small" icon={<DeleteOutlined />} danger onClick={onRemove} style={{ width: 20, height: 20, fontSize: 11 }} /></Tooltip>
        </div>
      )}
    </div>
  )
}

function CompactGroupRow({ node, children, onRemove }) {
  const [collapsed, setCollapsed] = useState(false)
  return (
    <div>
      <div onClick={() => setCollapsed(!collapsed)} style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', cursor: 'pointer', background: 'var(--purple-bg)',
      }}
        onMouseEnter={e => e.currentTarget.style.background = '#f0e6ff'}
        onMouseLeave={e => e.currentTarget.style.background = '#f9f0ff'}>
        {collapsed ? <CaretRightOutlined style={{ fontSize: 11, color: '#7c5cbf' }} /> : <CaretDownOutlined style={{ fontSize: 11, color: '#7c5cbf' }} />}
        <FolderOutlined style={{ color: '#7c5cbf', fontSize: 11 }} />
        <span style={{ fontSize: 11, color: '#7c5cbf', fontWeight: 500 }}>Group</span>
        <span style={{ fontSize: 12, color: '#7c5cbf', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{node.label || '分组'}</span>
        <span style={{ fontSize: 11, color: '#b37feb' }}>({(node.children || []).length})</span>
        <Button type="text" size="small" icon={<DeleteOutlined />} danger onClick={e => { e.stopPropagation(); onRemove() }} style={{ fontSize: 11, width: 20, height: 20 }} />
      </div>
      {!collapsed && <div style={{ paddingLeft: 16 }}>{children}</div>}
    </div>
  )
}

function CompactLoopRow({ node, children, onChange, onRemove }) {
  const [collapsed, setCollapsed] = useState(false)
  return (
    <div>
      <div onClick={() => setCollapsed(!collapsed)} style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', cursor: 'pointer', background: 'var(--green-bg)',
      }}
        onMouseEnter={e => e.currentTarget.style.background = '#d6edff'}
        onMouseLeave={e => e.currentTarget.style.background = '#e0f7f6'}>
        {collapsed ? <CaretRightOutlined style={{ fontSize: 11, color: '#0ea5a0' }} /> : <CaretDownOutlined style={{ fontSize: 11, color: '#0ea5a0' }} />}
        <RetweetOutlined style={{ color: '#0ea5a0', fontSize: 11 }} />
        <span style={{ fontSize: 11, color: '#0ea5a0', fontWeight: 500 }}>循环 {node.times || 3} 次</span>
        <span style={{ fontSize: 12, color: '#0ea5a0', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{node.label || ''}</span>
        <Button type="text" size="small" icon={<DeleteOutlined />} danger onClick={e => { e.stopPropagation(); onRemove() }} style={{ fontSize: 11, width: 20, height: 20 }} />
      </div>
      {!collapsed && <div style={{ paddingLeft: 16 }}>{children}</div>}
    </div>
  )
}

function CompactForEachRow({ node, children, onRemove }) {
  const [collapsed, setCollapsed] = useState(false)
  return (
    <div>
      <div onClick={() => setCollapsed(!collapsed)} style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', cursor: 'pointer', background: 'var(--green-bg)',
      }}
        onMouseEnter={e => e.currentTarget.style.background = '#b5f5ec'}
        onMouseLeave={e => e.currentTarget.style.background = '#e6fffb'}>
        {collapsed ? <CaretRightOutlined style={{ fontSize: 11, color: '#0ea5a0' }} /> : <CaretDownOutlined style={{ fontSize: 11, color: '#0ea5a0' }} />}
        <UnorderedListOutlined style={{ color: '#0ea5a0', fontSize: 11 }} />
        <span style={{ fontSize: 11, color: '#0ea5a0', fontWeight: 500 }}>ForEach</span>
        <span style={{ fontSize: 11, color: '#0ea5a0', fontFamily: 'var(--font-mono)' }}>{node.iterVar || 'item'}</span>
        <span style={{ fontSize: 11, color: '#86909c' }}>in</span>
        <span style={{ fontSize: 11, color: '#0ea5a0', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'var(--font-mono)' }}>{node.dataSource || '[]'}</span>
        <Button type="text" size="small" icon={<DeleteOutlined />} danger onClick={e => { e.stopPropagation(); onRemove() }} style={{ fontSize: 11, width: 20, height: 20 }} />
      </div>
      {!collapsed && <div style={{ paddingLeft: 16 }}>{children}</div>}
    </div>
  )
}

function CompactConditionRow({ node, onRemove, thenChildren, elseChildren }) {
  const [collapsed, setCollapsed] = useState(false)
  return (
    <div>
      <div onClick={() => setCollapsed(!collapsed)} style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', cursor: 'pointer', background: 'var(--orange-bg)',
      }}
        onMouseEnter={e => e.currentTarget.style.background = '#fff1b8'}
        onMouseLeave={e => e.currentTarget.style.background = '#fffbe6'}>
        {collapsed ? <CaretRightOutlined style={{ fontSize: 11, color: '#faad14' }} /> : <CaretDownOutlined style={{ fontSize: 11, color: '#faad14' }} />}
        <BranchesOutlined style={{ color: '#faad14', fontSize: 11 }} />
        <span style={{ fontSize: 11, color: '#faad14', fontWeight: 500 }}>IF</span>
        <span style={{ fontSize: 11, color: '#faad14', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'var(--font-mono)' }}>{node.condition || 'True'}</span>
        <Button type="text" size="small" icon={<DeleteOutlined />} danger onClick={e => { e.stopPropagation(); onRemove() }} style={{ fontSize: 11, width: 20, height: 20 }} />
      </div>
      {!collapsed && (
        <div>
          <div style={{ paddingLeft: 16 }}>
            <div style={{ fontSize: 11, color: '#0ea5a0', padding: '2px 10px', fontWeight: 600 }}>THEN</div>
            {thenChildren}
          </div>
          <div style={{ paddingLeft: 16 }}>
            <div style={{ fontSize: 11, color: '#e8453c', padding: '2px 10px', fontWeight: 600 }}>ELSE</div>
            {elseChildren}
          </div>
        </div>
      )}
    </div>
  )
}

function CompactWaitRow({ node, onRemove }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', background: 'rgba(0,0,0,0.02)' }}>
      <ClockCircleOutlined style={{ color: '#86909c', fontSize: 11 }} />
      <span style={{ fontSize: 11, color: '#86909c' }}>等待 {node.delay || 1000}ms</span>
      <span style={{ fontSize: 11, color: '#86909c', flex: 1 }}>{node.label || ''}</span>
      <Button type="text" size="small" icon={<DeleteOutlined />} danger onClick={onRemove} style={{ fontSize: 11, width: 20, height: 20 }} />
    </div>
  )
}

function CompactStepList({ steps, onChange, selectedId, onSelect, runSel }) {
  const [dragIdx, setDragIdx] = useState(null)
  const update = (i, ns) => onChange(steps.map((s, j) => j === i ? { ...ns, seq: j + 1 } : s))
  const remove = (i) => onChange(steps.filter((_, j) => j !== i).map((s, j) => ({ ...s, seq: j + 1 })))
  const copy = (i) => {
    const clone = JSON.parse(JSON.stringify(steps[i]))
    // **必须去掉 id**。深拷贝会把 id 一起复制走，而保存时是靠 `n.id 有没有值`
    // 判断新建还是更新的 —— 带着原 id 的副本会走 PUT，把原件覆盖掉：
    // 你点了复制，结果不是多一条，是原来那条被改名成「xxx (副本)」，还没有任何提示。
    delete clone.id
    // 执行痕迹也不能带过来：副本一次都没跑过，挂着原件的绿勾是假的
    delete clone.lastStatus
    clone.action = (clone.action || '') + ' (副本)'
    onChange([...steps.slice(0, i + 1), { ...clone, seq: i + 2 }, ...steps.slice(i + 1).map((s, j) => ({ ...s, seq: i + j + 3 }))])
  }
  const handleDragOver = (targetIdx) => {
    if (dragIdx === null || dragIdx === targetIdx) return
    const newSteps = [...steps]
    const [moved] = newSteps.splice(dragIdx, 1)
    newSteps.splice(targetIdx, 0, moved)
    onChange(newSteps.map((s, j) => ({ ...s, seq: j + 1 })))
    setDragIdx(targetIdx)
  }

  return (
    <div>
      {steps.map((s, i) => {
        const nt = s.nodeType || 'api'
        if (nt === 'group') return (
          <CompactGroupRow key={i} node={s} onRemove={() => remove(i)}>
            <CompactStepList steps={s.children || []} onChange={ch => update(i, { ...s, children: ch })} selectedId={selectedId} onSelect={onSelect} runSel={runSel} />
          </CompactGroupRow>
        )
        if (nt === 'loop') return (
          <CompactLoopRow key={i} node={s} onChange={ns => update(i, ns)} onRemove={() => remove(i)}>
            <CompactStepList steps={s.children || []} onChange={ch => update(i, { ...s, children: ch })} selectedId={selectedId} onSelect={onSelect} runSel={runSel} />
          </CompactLoopRow>
        )
        if (nt === 'forEach') return (
          <CompactForEachRow key={i} node={s} onRemove={() => remove(i)}>
            <CompactStepList steps={s.children || []} onChange={ch => update(i, { ...s, children: ch })} selectedId={selectedId} onSelect={onSelect} runSel={runSel} />
          </CompactForEachRow>
        )
        if (nt === 'condition') return (
          <CompactConditionRow key={i} node={s} onRemove={() => remove(i)}
            thenChildren={<CompactStepList steps={s.thenSteps || []} onChange={ch => update(i, { ...s, thenSteps: ch })} selectedId={selectedId} onSelect={onSelect} runSel={runSel} />}
            elseChildren={<CompactStepList steps={s.elseSteps || []} onChange={ch => update(i, { ...s, elseSteps: ch })} selectedId={selectedId} onSelect={onSelect} runSel={runSel} />}
          />
        )
        if (nt === 'wait') return <CompactWaitRow key={i} node={s} onRemove={() => remove(i)} />
        return <CompactApiRow key={i} step={s} index={i} isSelected={selectedId === s}
          onClick={() => onSelect(s, ns => update(i, ns))}
          onRemove={() => remove(i)} onCopy={() => copy(i)}
          onDragStart={setDragIdx} onDragOver={handleDragOver} onDrop={() => setDragIdx(null)}
          runChecked={runSel ? runSel.isChecked(s) : undefined}
          onRunCheck={runSel ? (c => runSel.toggle(s, c)) : undefined} />
      })}
    </div>
  )
}

// ===========================================================================
// 数据格式迁移（旧格式 → 新格式）
// ===========================================================================
function getOps(step, key) {
  if (step[key]) return step[key]
  if (key === 'preOperations') {
    const ops = []
    if (step.preScript?.trim()) ops.push({ type: 'script', code: step.preScript })
    return ops
  }
  const ops = []
  for (const a of (step.assertions || [])) ops.push({ type: 'assertion', assertType: a.type, path: a.path, operator: a.operator, expected: a.expected })
  for (const e of (step.extractors || [])) ops.push({ type: 'extractor', variable: e.variable, path: e.path })
  if (step.postScript?.trim()) ops.push({ type: 'script', code: step.postScript })
  return ops
}

// ===========================================================================
// 操作条目摘要文本
// ===========================================================================
const opMeta = {
  assertion: { icon: <CheckCircleOutlined />, color: '#0ea5a0', label: '断言' },
  extractor: { icon: <FieldStringOutlined />, color: '#7c5cbf', label: '提取变量' },
  headerSetter: { icon: <LockOutlined />, color: '#0ea5a0', label: '设置 Header' },
  bodySetter: { icon: <FormOutlined />, color: '#eb2f96', label: '设置 Body' },
  script: { icon: <CodeOutlined />, color: '#0ea5a0', label: '脚本' },
  wait: { icon: <ClockCircleOutlined />, color: '#86909c', label: '等待' },
}

const assertTypes = [{ value: 'status', label: '状态码' }, { value: 'jsonPath', label: 'Response JSON' }, { value: 'contains', label: '包含' }, { value: 'header', label: '响应头' }]
// 每一项都必须在后端 _VALID_OPS 里认得（api_test_runner）。下拉里多一个执行器不认的，
// 人选了就得到「不认识的操作符」，那一步永远失败 —— gt/lt 就这么错了很久。
const assertOps = [{ value: 'eq', label: '等于' }, { value: 'ne', label: '不等于' }, { value: 'gt', label: '大于' }, { value: 'lt', label: '小于' }, { value: 'gte', label: '大于等于' }, { value: 'lte', label: '小于等于' }, { value: 'contains', label: '包含' }, { value: 'notContains', label: '不包含' }, { value: 'notEmpty', label: '非空' }, { value: 'isEmpty', label: '为空' }, { value: 'notExists', label: '取不到(没命中)' }, { value: 'length', label: '长度等于' }, { value: 'in', label: '属于(多选其一)' }]

function opSummary(op) {
  if (op.type === 'assertion') {
    const t = assertTypes.find(x => x.value === op.assertType)?.label || op.assertType
    const o = assertOps.find(x => x.value === op.operator)?.label || op.operator
    if (op.assertType === 'status') return `${t} ${o} ${op.expected || ''}`
    return `${t} (${op.path || '...'}) ${o} ${op.expected || ''}`
  }
  if (op.type === 'extractor') return `${op.variable || '...'} 临时变量 Response JSON (${op.path || '...'})`
  if (op.type === 'headerSetter') {
    const val = op.sourceType === 'context' ? `{{${op.value || '...'}}}` : (op.value || '...')
    return `${op.headerKey || '...'}: ${val}`
  }
  if (op.type === 'bodySetter') {
    if (op.mode === 'replace') return `替换整个 Body`
    const val = op.sourceType === 'context' ? `{{${op.value || '...'}}}` : (op.value || '...')
    return `$.${op.path || '...'} = ${val}`
  }
  if (op.type === 'script') { const l = (op.code || '').trim().split('\n')[0]; return l ? (l.length > 50 ? l.slice(0, 50) + '...' : l) : '(空脚本)' }
  if (op.type === 'wait') return `${op.delay || 1000}ms${op.label ? '  ' + op.label : ''}`
  return ''
}

// ===========================================================================
// 单个操作条目（可展开编辑 + 可拖拽）
// ===========================================================================
function OperationItem({ op, index, onChange, onRemove, onDragStart, onDragOver, onDrop, snippets }) {
  const [expanded, setExpanded] = useState(false)
  const scriptRef = useRef(null)
  const meta = opMeta[op.type] || opMeta.script
  const up = (f, v) => onChange({ ...op, [f]: v })

  return (
    <div
      draggable
      onDragStart={e => { e.dataTransfer.effectAllowed = 'move'; onDragStart(index) }}
      onDragOver={e => { e.preventDefault(); onDragOver(index) }}
      onDrop={e => { e.preventDefault(); onDrop(index) }}
      style={{ border: '1px solid rgba(0,0,0,0.05)', borderRadius: 6, marginBottom: 4, background: 'transparent', transition: 'box-shadow 0.12s' }}
      onMouseEnter={e => e.currentTarget.style.boxShadow = '0 1px 4px rgba(0,0,0,0.06)'}
      onMouseLeave={e => e.currentTarget.style.boxShadow = 'none'}
    >
      <div onClick={() => setExpanded(!expanded)} style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '6px 8px', cursor: 'pointer', userSelect: 'none',
      }}>
        <HolderOutlined style={{ color: 'rgba(0,0,0,0.25)', cursor: 'grab', fontSize: 11, flexShrink: 0 }} />
        <span style={{ color: meta.color, fontSize: 11, flexShrink: 0 }}>{meta.icon}</span>
        <span style={{ fontSize: 11, color: meta.color, fontWeight: 500, flexShrink: 0 }}>{meta.label}</span>
        <span style={{ fontSize: 12, color: '#4e5969', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: op.type === 'script' ? 'var(--font-mono)' : 'inherit' }}>
          {opSummary(op)}
        </span>
        <Button type="text" size="small" icon={<DeleteOutlined />} danger onClick={e => { e.stopPropagation(); onRemove() }} style={{ width: 20, height: 20 }} />
        {expanded ? <CaretDownOutlined style={{ fontSize: 11, color: '#c9cdd4' }} /> : <CaretRightOutlined style={{ fontSize: 11, color: '#c9cdd4' }} />}
      </div>

      {expanded && (
        <div style={{ padding: '6px 10px 10px 28px', borderTop: '1px solid #f5f5f5' }}>
          {op.type === 'assertion' && (
            <div>
              <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexWrap: 'wrap' }}>
                <Select size="small" value={op.assertType || 'status'} onChange={v => up('assertType', v)} options={assertTypes} style={{ width: 120 }} />
                {(op.assertType === 'jsonPath' || op.assertType === 'header') && (
                  <Tooltip title="JSONPath 示例：$.data.id, $.list[0].name, $.total"><Input spellCheck={false} size="small" value={op.path || ''} placeholder="$.data.id" onChange={e => up('path', e.target.value)} style={{ width: 160, fontFamily: 'var(--font-mono)', fontSize: 11 }} /></Tooltip>
                )}
                {/* 80 宽度只够两个字：加了「大于等于」「长度等于」之后会截成「大于…」，
                    下拉里根本看不出选的是哪个。 */}
                <Select size="small" value={op.operator || 'eq'} onChange={v => up('operator', v)} options={assertOps} style={{ width: 104 }} />
                {!['notEmpty', 'isEmpty', 'notExists'].includes(op.operator) && (
                  <Input spellCheck={false} size="small" value={op.expected || ''} placeholder={op.assertType === 'status' ? '200' : '期望值'} onChange={e => up('expected', e.target.value)} style={{ flex: 1, minWidth: 80, fontFamily: 'var(--font-mono)', fontSize: 11 }} />
                )}
              </div>
              <div style={{ fontSize: 11, color: '#86909c', marginTop: 4 }}>
                {op.assertType === 'status' && '验证接口返回的 HTTP 状态码，如 200、201、404'}
                {op.assertType === 'jsonPath' && '用 JSONPath 定位响应 JSON 中的字段，如 $.data.token'}
                {op.assertType === 'contains' && '检查响应体文本是否包含指定字符串'}
                {op.assertType === 'header' && '检查响应头中指定字段的值，如 Content-Type'}
              </div>
            </div>
          )}
          {op.type === 'extractor' && (
            <div>
              <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                <Tooltip title="后续步骤可用 {{变量名}} 引用"><Input spellCheck={false} size="small" value={op.variable || ''} placeholder="如: token" onChange={e => up('variable', e.target.value)} style={{ width: 100, fontFamily: 'var(--font-mono)', fontSize: 11 }} /></Tooltip>
                <Tag style={{ margin: 0, fontSize: 11, background: 'var(--green-bg)', color: '#0ea5a0', border: 'none' }}>临时变量</Tag>
                <span style={{ fontSize: 11, color: '#86909c' }}>Response JSON</span>
                <Tooltip title="JSONPath 示例：$.data.token, $.list[0].id"><Input spellCheck={false} size="small" value={op.path || ''} placeholder="$.data.token" onChange={e => up('path', e.target.value)} style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 11 }} /></Tooltip>
              </div>
              <div style={{ fontSize: 11, color: '#86909c', marginTop: 4 }}>从响应 JSON 中提取值存为临时变量，后续步骤用 {'{{变量名}}'} 引用</div>
            </div>
          )}
          {op.type === 'headerSetter' && (
            <div>
              <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexWrap: 'wrap' }}>
                <AutoComplete size="small" value={op.headerKey || ''} options={headerOptions} onChange={v => up('headerKey', v)}
                  placeholder="Header 名称" style={{ width: 160, fontSize: 11 }} filterOption={(input, opt) => opt.value.toLowerCase().includes(input.toLowerCase())} />
                <Select size="small" value={op.sourceType || 'literal'} onChange={v => up('sourceType', v)} style={{ width: 110 }}
                  options={[{ value: 'literal', label: '直接输入' }, { value: 'context', label: '上下文变量' }]} />
                <Input spellCheck={false} size="small" value={op.value || ''} onChange={e => up('value', e.target.value)}
                  placeholder={op.sourceType === 'context' ? '变量名，如 token' : '值，如 Bearer xxx'}
                  prefix={op.sourceType === 'context' ? <span style={{ color: '#0ea5a0', fontSize: 11 }}>{'{{'}...{'}}'}</span> : null}
                  style={{ flex: 1, minWidth: 120, fontFamily: 'var(--font-mono)', fontSize: 11 }} />
              </div>
              <div style={{ fontSize: 11, color: '#86909c', marginTop: 4 }}>设置请求头字段，"上下文变量"模式会自动用前面步骤提取的值</div>
            </div>
          )}
          {op.type === 'bodySetter' && (
            <div>
              <div style={{ display: 'flex', gap: 4, alignItems: 'center', marginBottom: 6 }}>
                <Select size="small" value={op.mode || 'field'} onChange={v => up('mode', v)} style={{ width: 110 }}
                  options={[{ value: 'field', label: '设置字段' }, { value: 'replace', label: '替换整体' }]} />
                {op.mode !== 'replace' && (
                  <Select size="small" value={op.sourceType || 'literal'} onChange={v => up('sourceType', v)} style={{ width: 110 }}
                    options={[{ value: 'literal', label: '直接输入' }, { value: 'context', label: '上下文变量' }]} />
                )}
              </div>
              {(op.mode || 'field') === 'field' ? (
                <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                  <Input spellCheck={false} size="small" value={op.path || ''} onChange={e => up('path', e.target.value)}
                    placeholder="字段路径，如 data.user_id" style={{ width: 160, fontFamily: 'var(--font-mono)', fontSize: 11 }} />
                  <span style={{ fontSize: 11, color: '#86909c' }}>=</span>
                  <Input spellCheck={false} size="small" value={op.value || ''} onChange={e => up('value', e.target.value)}
                    placeholder={op.sourceType === 'context' ? '变量名' : '值'}
                    prefix={op.sourceType === 'context' ? <span style={{ color: '#0ea5a0', fontSize: 11 }}>{'{{'}...{'}}'}</span> : null}
                    style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 11 }} />
                </div>
              ) : (
                <div>
                  {op.sourceType === 'context' ? (
                    <Input spellCheck={false} size="small" value={op.value || ''} onChange={e => up('value', e.target.value)}
                      placeholder="上下文变量名" prefix={<span style={{ color: '#0ea5a0', fontSize: 11 }}>{'{{'}...{'}}'}</span>}
                      style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} />
                  ) : (
                    <Input.TextArea spellCheck={false} value={op.value || ''} onChange={e => up('value', e.target.value)}
                      placeholder='{"key": "value"}' autoSize={{ minRows: 2, maxRows: 8 }}
                      style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} />
                  )}
                </div>
              )}
              <div style={{ fontSize: 11, color: '#86909c', marginTop: 4 }}>
                {(op.mode || 'field') === 'field' ? '修改 Body 中的指定字段（支持嵌套路径，如 data.list.0.id）' : '用新内容替换整个请求体'}
              </div>
            </div>
          )}
          {op.type === 'script' && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 4 }}>
                <Space size={4}>
                  <VarPicker onInsert={v => { const c = op.code || ''; up('code', c + (c && !c.endsWith('\n') ? '\n' : '') + v) }} />
                  {snippets && <SnippetPicker snippets={snippets} onInsert={code => insertAtCursor(scriptRef, op.code || '', code, v => up('code', v))} />}
                </Space>
              </div>
              <Input.TextArea spellCheck={false} ref={scriptRef} value={op.code || ''} onChange={e => up('code', e.target.value)}
                placeholder="# 在此编写脚本，或点击「片段」快速插入" autoSize={{ minRows: 3, maxRows: 16 }}
                style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} />
            </div>
          )}
          {op.type === 'wait' && (
            <div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <Input size="small" value={op.delay ?? 1000} type="number" onChange={e => up('delay', parseInt(e.target.value) || 0)} style={{ width: 80, textAlign: 'center', fontSize: 11 }} />
                <span style={{ fontSize: 11, color: '#86909c' }}>ms</span>
                <Input size="small" value={op.label || ''} placeholder="描述（可选）" onChange={e => up('label', e.target.value)} style={{ flex: 1, fontSize: 11 }} />
              </div>
              <div style={{ fontSize: 11, color: '#86909c', marginTop: 4 }}>在两个操作之间暂停指定毫秒数，1000ms = 1秒</div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ===========================================================================
// 操作列表（扁平、可拖拽排序）
// ===========================================================================
function OperationList({ operations, onChange, addItems, snippets, infoBg, infoBorder, infoColor, infoIcon, infoTitle, infoDesc }) {
  const [dragIdx, setDragIdx] = useState(null)

  const handleDragOver = (targetIdx) => {
    if (dragIdx === null || dragIdx === targetIdx) return
    const newOps = [...operations]
    const [moved] = newOps.splice(dragIdx, 1)
    newOps.splice(targetIdx, 0, moved)
    onChange(newOps)
    setDragIdx(targetIdx)
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', background: infoBg, borderRadius: 6, marginBottom: 10, border: `1px solid ${infoBorder}` }}>
        <span style={{ fontSize: 13 }}>{infoIcon}</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: infoColor, fontWeight: 600 }}>{infoTitle}</div>
          <div style={{ fontSize: 11, color: '#4e5969', marginTop: 1 }}>{infoDesc}</div>
        </div>
      </div>

      {operations.length === 0 && (
        <div style={{ padding: '16px 12px', textAlign: 'center', color: '#86909c', fontSize: 11, border: '1px dashed rgba(0,0,0,0.08)', borderRadius: 6, marginBottom: 8 }}>
          暂无操作，点击下方按钮添加断言、提取变量或自定义脚本
        </div>
      )}
      {operations.map((op, i) => (
        <OperationItem key={i} op={op} index={i}
          onChange={newOp => onChange(operations.map((o, j) => j === i ? newOp : o))}
          onRemove={() => onChange(operations.filter((_, j) => j !== i))}
          onDragStart={setDragIdx} onDragOver={handleDragOver} onDrop={() => setDragIdx(null)}
          snippets={snippets} />
      ))}

      <Dropdown menu={{ items: addItems, onClick: ({ key }) => {
        const newOp = key === 'assertion' ? { type: 'assertion', assertType: 'status', operator: 'eq', expected: '200' }
          : key === 'extractor' ? { type: 'extractor', variable: '', path: '' }
          : key === 'headerSetter' ? { type: 'headerSetter', headerKey: '', value: '', sourceType: 'literal' }
          : key === 'bodySetter' ? { type: 'bodySetter', mode: 'field', path: '', value: '', sourceType: 'literal' }
          : key === 'script' ? { type: 'script', code: '# 在此编写脚本，点击右上角「片段」可快速插入常用模板\n' }
          : { type: 'wait', delay: 1000, label: '' }
        onChange([...operations, newOp])
      }}} trigger={['click']}>
        <Button type="link" icon={<PlusOutlined />} style={{ padding: '4px 0', marginTop: 4 }}>
          添加{infoTitle.replace('执行', '操作')}
        </Button>
      </Dropdown>
    </div>
  )
}

const preAddItems = [
  { key: 'headerSetter', icon: <LockOutlined />, label: <span>设置 Header<span style={{ fontSize: 11, color: '#86909c', marginLeft: 6 }}>添加/修改请求头字段</span></span> },
  { key: 'bodySetter', icon: <FormOutlined />, label: <span>设置 Body<span style={{ fontSize: 11, color: '#86909c', marginLeft: 6 }}>修改请求体字段或替换整体</span></span> },
  { type: 'divider' },
  { key: 'script', icon: <CodeOutlined />, label: <span>自定义脚本<span style={{ fontSize: 11, color: '#86909c', marginLeft: 6 }}>编写自定义前置逻辑</span></span> },
  { key: 'wait', icon: <ClockCircleOutlined />, label: <span>等待时间<span style={{ fontSize: 11, color: '#86909c', marginLeft: 6 }}>暂停指定毫秒数</span></span> },
]
const postAddItems = [
  { key: 'assertion', icon: <CheckCircleOutlined />, label: <span>断言<span style={{ fontSize: 11, color: '#86909c', marginLeft: 6 }}>验证状态码、响应体字段</span></span> },
  { key: 'extractor', icon: <FieldStringOutlined />, label: <span>提取变量<span style={{ fontSize: 11, color: '#86909c', marginLeft: 6 }}>从响应中提取值供后续步骤使用</span></span> },
  { key: 'script', icon: <CodeOutlined />, label: <span>自定义脚本<span style={{ fontSize: 11, color: '#86909c', marginLeft: 6 }}>编写自定义逻辑</span></span> },
  { key: 'wait', icon: <ClockCircleOutlined />, label: <span>等待时间<span style={{ fontSize: 11, color: '#86909c', marginLeft: 6 }}>暂停指定毫秒数</span></span> },
]

// ===========================================================================
// Auth 编辑器
// ===========================================================================
function AuthEditor({ auth, onChange }) {
  const a = auth || { type: 'none' }
  const up = (f, v) => onChange({ ...a, [f]: v })
  return (
    <div>
      <Select size="small" value={a.type || 'none'} onChange={v => up('type', v)} style={{ width: 200, marginBottom: 12 }}
        options={[
          { value: 'none', label: '无认证 (No Auth)' },
          { value: 'bearer', label: 'Bearer Token' },
          { value: 'basic', label: 'Basic Auth' },
          { value: 'apikey', label: 'API Key' },
        ]} />
      {a.type === 'bearer' && (
        <div>
          <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Token</div>
          <Input spellCheck={false} size="small" value={a.token || ''} onChange={e => up('token', e.target.value)}
            placeholder="输入 Token，支持 {{variable}}" style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} />
          <div style={{ fontSize: 11, color: '#86909c', marginTop: 6 }}>会自动添加 Authorization: Bearer {'<token>'} 请求头</div>
        </div>
      )}
      {a.type === 'basic' && (
        <div style={{ display: 'flex', gap: 8, flexDirection: 'column' }}>
          <div>
            <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>用户名</div>
            <Input size="small" value={a.username || ''} onChange={e => up('username', e.target.value)} placeholder="Username" style={{ fontSize: 11 }} />
          </div>
          <div>
            <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>密码</div>
            <Input.Password size="small" value={a.password || ''} onChange={e => up('password', e.target.value)} placeholder="Password" style={{ fontSize: 11 }} />
          </div>
          <div style={{ fontSize: 11, color: '#86909c' }}>自动进行 Base64 编码并添加 Authorization: Basic 请求头</div>
        </div>
      )}
      {a.type === 'apikey' && (
        <div style={{ display: 'flex', gap: 8, flexDirection: 'column' }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Key 名称</div>
              <Input size="small" value={a.keyName || ''} onChange={e => up('keyName', e.target.value)} placeholder="X-API-Key" style={{ fontSize: 11 }} />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>Key 值</div>
              <Input spellCheck={false} size="small" value={a.keyValue || ''} onChange={e => up('keyValue', e.target.value)} placeholder="your-api-key" style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} />
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>添加到</div>
            <Select size="small" value={a.keyIn || 'header'} onChange={v => up('keyIn', v)} style={{ width: 160 }}
              options={[{ value: 'header', label: 'Header' }, { value: 'query', label: 'Query Params' }]} />
          </div>
        </div>
      )}
      {a.type === 'none' && (
        <div style={{ padding: '16px 0', textAlign: 'center', color: '#86909c', fontSize: 12 }}>
          此请求不使用认证
        </div>
      )}
    </div>
  )
}

// ===========================================================================
// Response 面板（Pretty/Raw + 复制 + 搜索 + Cookie）
// ===========================================================================
function ResponsePanel({ response, onAddAssertion }) {
  const [viewTab, setViewTab] = useState('body')
  const [bodyMode, setBodyMode] = useState('pretty')
  const [search, setSearch] = useState('')
  if (!response) return null
  const r = response
  const sc = r.status_code || r.statusCode || 0
  const isOk = sc >= 200 && sc < 300
  const statusColor = sc === 0 ? '#e8453c' : isOk ? '#0ea5a0' : sc < 400 ? '#faad14' : '#e8453c'
  const durationMs = r.duration_ms || r.durationMs || 0
  const durationColor = durationMs < 200 ? '#0ea5a0' : durationMs < 1000 ? '#faad14' : '#e8453c'

  const rawBody = r.body || ''
  let prettyBody = rawBody
  let isJson = false
  try {
    const parsed = JSON.parse(rawBody)
    prettyBody = JSON.stringify(parsed, null, 2)
    isJson = true
  } catch {}

  const displayBody = bodyMode === 'pretty' && isJson ? prettyBody : rawBody
  const sizeStr = r.size > 1024 * 1024 ? `${(r.size / 1024 / 1024).toFixed(1)} MB` : r.size > 1024 ? `${(r.size / 1024).toFixed(1)} KB` : `${r.size || 0} B`
  const respHeaders = r.headers || []
  const cookies = respHeaders.filter(h => h.key.toLowerCase() === 'set-cookie').map(h => {
    const parts = h.value.split(';').map(p => p.trim())
    const [nv, ...attrs] = parts
    const eqIdx = nv.indexOf('=')
    return { name: eqIdx > 0 ? nv.slice(0, eqIdx) : nv, value: eqIdx > 0 ? nv.slice(eqIdx + 1) : '', attrs: attrs.join('; ') }
  })

  const copyBody = () => {
    copyToClipboard(displayBody).then(() => message.success('已复制到剪贴板'))
  }

  const renderJsonTree = (obj, path = '$') => {
    if (obj === null) return <span style={{ color: '#86909c' }}>null</span>
    if (typeof obj === 'boolean') return <span style={{ color: '#d32029' }}>{String(obj)}</span>
    if (typeof obj === 'number') return <span style={{ color: '#d32029' }}>{obj}</span>
    if (typeof obj === 'string') return <span style={{ color: '#0ea5a0' }}>"{obj}"</span>
    if (Array.isArray(obj)) {
      if (obj.length === 0) return <span>[]</span>
      return (
        <span>{'[\n'}
          {obj.map((item, i) => (
            <span key={i}>{'  '}{renderJsonTree(item, `${path}[${i}]`)}{i < obj.length - 1 ? ',\n' : '\n'}</span>
          ))}
        {']'}</span>
      )
    }
    if (typeof obj === 'object') {
      const entries = Object.entries(obj)
      if (entries.length === 0) return <span>{'{}'}</span>
      return (
        <span>{'{\n'}
          {entries.map(([k, v], i) => (
            <span key={k}>
              {'  '}<span style={{ color: '#0ea5a0' }}>"{k}"</span>: {renderJsonTree(v, `${path}.${k}`)}
              {onAddAssertion && typeof v !== 'object' && (
                <Tooltip title={`断言 ${path}.${k}`}>
                  <span onClick={(e) => { e.stopPropagation(); onAddAssertion(path + '.' + k, v) }}
                    style={{ cursor: 'pointer', marginLeft: 6, fontSize: 11, color: '#c9cdd4', opacity: 0.6, transition: 'opacity 0.1s' }}
                    onMouseEnter={e => e.target.style.opacity = 1} onMouseLeave={e => e.target.style.opacity = 0.6}>
                    +断言
                  </span>
                </Tooltip>
              )}
              {i < entries.length - 1 ? ',\n' : '\n'}
            </span>
          ))}
        {'}'}</span>
      )
    }
    return <span>{String(obj)}</span>
  }

  const highlightSearch = (text) => {
    if (!search) return text
    const parts = text.split(new RegExp(`(${search.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'))
    return parts.map((p, i) => p.toLowerCase() === search.toLowerCase()
      ? <mark key={i} style={{ background: 'rgba(250,173,20,0.3)', padding: 0 }}>{p}</mark> : p)
  }

  return (
    <div>
      {/* 状态栏 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', marginBottom: 8, borderBottom: '1px solid rgba(0,0,0,0.06)' }}>
        <Tooltip title={onAddAssertion ? '点击添加状态码断言' : ''}>
          <span onClick={() => onAddAssertion?.('status', sc)} style={{
            fontWeight: 700, fontSize: 14, color: statusColor, background: statusColor + '10', padding: '2px 8px', borderRadius: 8,
            cursor: onAddAssertion ? 'pointer' : 'default',
          }}>
            {sc} {r.status_text || r.statusText || ''}
          </span>
        </Tooltip>
        <span style={{ fontSize: 11, color: durationColor, fontWeight: 600 }}>{durationMs} ms</span>
        <span style={{ fontSize: 11, color: '#86909c' }}>{sizeStr}</span>
        <div style={{ flex: 1 }} />
        <div style={{ display: 'flex', gap: 0 }}>
          {[
            { key: 'body', label: 'Body' },
            { key: 'headers', label: `Headers (${respHeaders.length})` },
            ...(cookies.length > 0 ? [{ key: 'cookies', label: `Cookies (${cookies.length})` }] : []),
          ].map(t => (
            <div key={t.key} onClick={() => setViewTab(t.key)} style={{
              padding: '2px 10px', fontSize: 11, cursor: 'pointer',
              color: viewTab === t.key ? '#0ea5a0' : '#86909c', fontWeight: viewTab === t.key ? 600 : 400,
              borderBottom: viewTab === t.key ? '2px solid #0ea5a0' : '2px solid transparent',
            }}>{t.label}</div>
          ))}
        </div>
      </div>

      {/* Body */}
      {viewTab === 'body' && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <div style={{ display: 'flex', gap: 0 }}>
              {isJson && ['pretty', 'raw'].map(m => (
                <div key={m} onClick={() => setBodyMode(m)} style={{
                  padding: '2px 8px', fontSize: 11, cursor: 'pointer', borderRadius: 6,
                  background: bodyMode === m ? '#e0f7f6' : 'transparent',
                  color: bodyMode === m ? '#0ea5a0' : '#86909c', fontWeight: bodyMode === m ? 600 : 400,
                }}>{m === 'pretty' ? 'Pretty' : 'Raw'}</div>
              ))}
            </div>
            <Space size={4}>
              <Input size="small" prefix={<SearchOutlined style={{ color: '#c9cdd4' }} />} value={search}
                onChange={e => setSearch(e.target.value)} placeholder="搜索" allowClear
                style={{ width: 140, fontSize: 11 }} />
              <Tooltip title="复制"><Button size="small" icon={<CopyOutlined />} onClick={copyBody} /></Tooltip>
            </Space>
          </div>
          <pre style={{
            margin: 0, padding: 12, background: '#fafbfc', border: '1px solid rgba(0,0,0,0.05)', borderRadius: 6,
            fontFamily: 'var(--font-mono)', fontSize: 11, lineHeight: 1.6, maxHeight: 400, overflow: 'auto',
            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>{bodyMode === 'pretty' && isJson && onAddAssertion ? renderJsonTree(JSON.parse(rawBody)) : (search ? highlightSearch(displayBody) : displayBody)}</pre>
        </div>
      )}

      {/* Headers */}
      {viewTab === 'headers' && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 6 }}>
            <Tooltip title="复制全部">
              <Button size="small" icon={<CopyOutlined />} onClick={() => {
                const t = respHeaders.map(h => `${h.key}: ${h.value}`).join('\n')
                copyToClipboard(t).then(() => message.success('已复制'))
              }} />
            </Tooltip>
          </div>
          {respHeaders.map((h, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, padding: '4px 0', borderBottom: '1px solid #f8f8f8', fontSize: 11 }}>
              <span style={{ fontWeight: 600, color: '#4e5969', width: 200, flexShrink: 0, fontFamily: 'var(--font-mono)' }}>{h.key}</span>
              <span style={{ color: '#86909c', fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>{h.value}</span>
            </div>
          ))}
        </div>
      )}

      {/* Cookies */}
      {viewTab === 'cookies' && (
        <div>
          {cookies.map((c, i) => (
            <div key={i} style={{ padding: '6px 0', borderBottom: '1px solid #f8f8f8', fontSize: 11 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span style={{ fontWeight: 600, color: '#4e5969', fontFamily: 'var(--font-mono)' }}>{c.name}</span>
                <span style={{ color: '#0ea5a0', fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>{c.value}</span>
              </div>
              {c.attrs && <div style={{ fontSize: 11, color: '#86909c', marginTop: 2 }}>{c.attrs}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ===========================================================================
// 右侧面板：步骤详情编辑器
// ===========================================================================

function StepDetailPanel({ step, onChange, baseUrl }) {
  const [activeTab, setActiveTab] = useState('params')
  const [sending, setSending] = useState(false)
  const [response, setResponse] = useState(null)
  const [importCurlOpen, setImportCurlOpen] = useState(false)
  const [curlText, setCurlText] = useState('')
  const method = step.method || 'GET'
  const mc = methodColors[method] || methodColors.GET
  const up = (f, v) => onChange({ ...step, [f]: v })

  // URL 是否自带 base。除 http(s) 和 {{BASE_URL}} 外，**必须认 ${BASE_URL}**：
  // 后端步骤插值和 CC 回推用的都是 ${...} 语法。漏认会导致左侧再贴一个环境前缀，
  // 发送时拼成 http://host${BASE_URL}/xxx 的双份 base。
  const urlHasOwnBase = (url) => {
    if (!url) return false
    const lower = url.toLowerCase()
    return lower.startsWith('http://') || lower.startsWith('https://')
      || /^\{\{\s*base_url\s*\}\}/i.test(url) || /^\$\{\s*BASE_URL\s*\}/i.test(url)
  }

  // 输入框展示用：摘掉开头的 BASE_URL 占位符，只留路径（对齐 apifox 的做法）
  const BASE_PREFIX_RE = /^(\{\{\s*base_url\s*\}\}|\$\{\s*BASE_URL\s*\})/i
  const rawUrl = step.url || ''
  const isAbsolute = /^https?:\/\//i.test(rawUrl)
  const urlPathOnly = isAbsolute ? rawUrl : rawUrl.replace(BASE_PREFIX_RE, '')
  // 保存时补回前缀：原来有前缀就还原，绝对地址原样，其它默认加 ${BASE_URL}
  const restoreBase = (path) => {
    if (/^https?:\/\//i.test(path)) return path      // 用户要打别的地址
    const m = rawUrl.match(BASE_PREFIX_RE)
    return (m ? m[0] : '${BASE_URL}') + path
  }

  // 把两种写法的 BASE_URL 占位符都换成所选环境的实际地址
  const subBase = (url, base) => (url || '')
    .replace(/\{\{\s*base_url\s*\}\}/gi, base)
    .replace(/\$\{\s*BASE_URL\s*\}/gi, base)

  const paramCount = (step.params || []).filter(p => p.key && p.enabled !== false).length
  const headerCount = (step.headers || []).filter(h => h.key && h.enabled !== false).length
  const bodyHas = step.body?.trim() ? 1 : 0
  const preOps = getOps(step, 'preOperations')
  const postOps = getOps(step, 'postOperations')
  const preCount = preOps.length
  const postCount = postOps.length
  const hasAuth = step.auth?.type && step.auth.type !== 'none'

  // Params ↔ URL 同步
  const syncParamsFromUrl = (url) => {
    const qIdx = url.indexOf('?')
    if (qIdx < 0) return
    const path = url.slice(0, qIdx)
    const qs = url.slice(qIdx + 1)
    const newParams = qs.split('&').filter(Boolean).map(p => {
      const [k, ...rest] = p.split('=')
      return { key: decodeURIComponent(k), value: decodeURIComponent(rest.join('=')), enabled: true, desc: '' }
    })
    const existing = (step.params || []).filter(p => p.key && !newParams.find(np => np.key === p.key))
    onChange({ ...step, url: path, params: [...newParams, ...existing] })
  }

  const resolvedUrl = useMemo(() => {
    const base = baseUrl || ''
    const path = step.url || ''
    const hasOwnBase = urlHasOwnBase(path)
    const fullPath = hasOwnBase ? subBase(path, base) : base + path
    const enabledParams = (step.params || []).filter(p => p.key && p.enabled !== false)
    const qs = enabledParams.map(p => `${encodeURIComponent(p.key)}=${encodeURIComponent(p.value || '')}`).join('&')
    return fullPath + (qs ? '?' + qs : '')
  }, [baseUrl, step.url, step.params])

  const handleSend = async () => {
    setSending(true)
    setResponse(null)
    setActiveTab('response')
    try {
      const path = step.url || ''
      const fullUrl = urlHasOwnBase(path) ? subBase(path, baseUrl || '') : (baseUrl || '') + path
      const res = await api.post('/debug/send', {
        method: step.method || 'GET',
        url: fullUrl,
        params: step.params || [],
        headers: step.headers || [],
        body: step.body || '',
        bodyType: step.bodyType || 'json',
        auth: step.auth || null,
      })
      setResponse(res.data)
    } catch (e) {
      setResponse({ statusCode: 0, statusText: '请求失败', headers: [], body: e.message, durationMs: 0, size: 0 })
    }
    setSending(false)
  }

  const generateCurl = () => {
    const m = (step.method || 'GET').toUpperCase()
    const parts = [`curl -X ${m}`]
    const fullUrl = resolvedUrl || (baseUrl || '') + (step.url || '')
    parts.push(`  '${fullUrl}'`)
    const allHeaders = [...(step.headers || []).filter(h => h.key && h.enabled !== false)]
    if (step.auth?.type === 'bearer' && step.auth.token) allHeaders.push({ key: 'Authorization', value: `Bearer ${step.auth.token}` })
    if (step.auth?.type === 'basic' && step.auth.username) allHeaders.push({ key: 'Authorization', value: `Basic ${btoa(`${step.auth.username}:${step.auth.password || ''}`)}` })
    if (step.auth?.type === 'apikey' && step.auth.keyName && step.auth.keyIn !== 'query') allHeaders.push({ key: step.auth.keyName, value: step.auth.keyValue || '' })
    for (const h of allHeaders) parts.push(`  -H '${h.key}: ${h.value}'`)
    if (m !== 'GET' && step.body?.trim()) {
      if ((step.bodyType || 'json') === 'json') {
        if (!allHeaders.find(h => h.key.toLowerCase() === 'content-type')) parts.push(`  -H 'Content-Type: application/json'`)
        parts.push(`  -d '${step.body.replace(/'/g, "'\\''")}'`)
      } else {
        parts.push(`  -d '${step.body.replace(/'/g, "'\\''")}'`)
      }
    }
    return parts.join(' \\\n')
  }

  const copyCurl = () => {
    copyToClipboard(generateCurl()).then(() => message.success('cURL 已复制'))
  }

  const handleImportCurl = () => {
    if (!curlText.trim()) return
    const parsed = parseCurl(curlText)
    const urlPath = baseUrl && parsed.url.startsWith(baseUrl) ? parsed.url.slice(baseUrl.length) : parsed.url
    onChange({
      ...step,
      method: parsed.method,
      url: urlPath,
      headers: parsed.headers.length ? parsed.headers : step.headers,
      params: parsed.params.length ? parsed.params : step.params,
      body: parsed.body || step.body,
      bodyType: parsed.bodyType,
      auth: parsed.auth.type !== 'none' ? parsed.auth : step.auth,
    })
    setImportCurlOpen(false)
    setCurlText('')
    message.success('cURL 已导入')
  }

  const handleKeyDown = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      handleSend()
    }
  }

  const tabs = [
    { key: 'params', label: 'Params', count: paramCount },
    { key: 'body', label: 'Body', count: bodyHas },
    { key: 'headers', label: 'Headers', count: headerCount },
    { key: 'auth', label: 'Auth', count: hasAuth ? 1 : 0, icon: <LockOutlined style={{ fontSize: 11, marginRight: 2 }} /> },
    { key: 'pre', label: '前置操作', count: preCount },
    { key: 'post', label: '后置操作', count: postCount },
    { key: 'timing', label: '等待/重试', count: (step.retryTimeoutMs > 0 || step.waitMs > 0) ? 1 : 0 },
    ...(response ? [{ key: 'response', label: 'Response', count: 0, highlight: true }] : []),
  ]

  const [codeLang, setCodeLang] = useState('curl')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }} onKeyDown={handleKeyDown}>
      {/* 步骤名称 */}
      <div style={{ padding: '8px 16px 0', flexShrink: 0 }}>
        <Input size="small" variant="borderless" value={step.action || ''} onChange={e => up('action', e.target.value)}
          placeholder="步骤名称（如：登录、获取用户信息）"
          style={{ fontSize: 12, color: '#1d2129', padding: '0 4px' }} />
      </div>

      {/* Method + URL + Send */}
      <div style={{ padding: '6px 16px 8px', borderBottom: '1px solid rgba(0,0,0,0.06)', flexShrink: 0 }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <Select size="small" value={method} onChange={v => up('method', v)} style={{ width: 90 }}
            popupMatchSelectWidth={false}
            options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map(m => ({
              value: m, label: <span style={{ color: methodColors[m]?.color, fontWeight: 700 }}>{m}</span>
            }))} />
          <div style={{ flex: 1, display: 'flex', alignItems: 'center' }}>
            {baseUrl && (
              // 只读的环境前置标记（不可编辑）。URL 里已写了 ${BASE_URL} 时不再拼接，
              // 只作提示——但地址仍要能看到，否则不知道这条会打到哪个环境。
              <Tooltip title={isAbsolute
                ? `该步骤填的是完整地址，忽略环境前置 URL（${baseUrl}）`
                : `当前环境前置 URL：${baseUrl}（只读，随所选环境变化；要打别的地址请直接填完整 http(s) 地址）`}>
                <span style={{ fontSize: 11, color: '#86909c', background: 'rgba(0,0,0,0.02)', border: '1px solid rgba(0,0,0,0.08)', borderRight: 'none',
                  borderRadius: '8px 0 0 8px', padding: '3px 8px', whiteSpace: 'nowrap', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis',
                  display: 'inline-block', lineHeight: '16px', fontFamily: 'var(--font-mono)', flexShrink: 0, cursor: 'default', userSelect: 'none',
                  textDecoration: isAbsolute ? 'line-through' : 'none', opacity: isAbsolute ? 0.45 : 1 }}>
                  <GlobalOutlined style={{ marginRight: 4, fontSize: 11 }} />{baseUrl}
                </span>
              </Tooltip>
            )}
            <Input spellCheck={false} size="small" value={urlPathOnly} style={{ fontFamily: 'var(--font-mono)', fontSize: 12, borderRadius: baseUrl ? '0 4px 4px 0' : undefined }}
              placeholder="/api/auth/login"
              onChange={e => {
                // 输入框里只编辑路径；${BASE_URL} 前缀由 restoreBase 补回，
                // 用户删不掉也打不错。要打到别的地址就直接填完整 http(s) URL。
                const v = restoreBase(e.target.value)
                if (v.includes('?')) syncParamsFromUrl(v)
                else up('url', v)
              }}
              onPaste={e => {
                const text = e.clipboardData?.getData('text') || ''
                if (text.trimStart().toLowerCase().startsWith('curl ')) {
                  e.preventDefault()
                  setCurlText(text)
                  setImportCurlOpen(true)
                }
              }} />
            <VarPicker onInsert={v => up('url', (step.url || '') + v)} />
          </div>
          <Tooltip title="导入 cURL"><Button size="small" icon={<ImportOutlined />} onClick={() => setImportCurlOpen(true)} style={{ color: '#86909c' }} /></Tooltip>
          <Tooltip title="复制 cURL"><Button size="small" icon={<CopyOutlined />} onClick={copyCurl} style={{ color: '#86909c' }} /></Tooltip>
          <Tooltip title="Ctrl+Enter">
            <Button type="primary" size="small" icon={sending ? <LoadingOutlined /> : <SendOutlined />}
              loading={sending} onClick={handleSend}
              style={{ background: '#0ea5a0', borderColor: '#0ea5a0', fontWeight: 600, minWidth: 64 }}>
              发送
            </Button>
          </Tooltip>
        </div>
        {/* 完整 URL 预览 */}
        {paramCount > 0 && (
          <div style={{ fontSize: 11, color: '#86909c', marginTop: 4, fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            title={resolvedUrl}>
            {resolvedUrl}
          </div>
        )}
      </div>

      {/* Import cURL Modal */}
      <Modal open={importCurlOpen} onCancel={() => { setImportCurlOpen(false); setCurlText('') }}
        title="导入 cURL" width={560} okText="导入" onOk={handleImportCurl}>
        <div style={{ marginBottom: 8, fontSize: 12, color: '#86909c' }}>
          粘贴 cURL 命令，自动解析为请求配置。也可以直接在 URL 栏粘贴 cURL 触发导入。
        </div>
        <Input.TextArea spellCheck={false} value={curlText} onChange={e => setCurlText(e.target.value)}
          placeholder={`curl -X POST 'https://api.example.com/login' \\\n  -H 'Content-Type: application/json' \\\n  -d '{"username":"admin","password":"123456"}'`}
          autoSize={{ minRows: 6, maxRows: 14 }} style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} />
        {curlText.trim() && (() => {
          const preview = parseCurl(curlText)
          return (
            <div style={{ marginTop: 10, padding: 10, background: 'rgba(0,0,0,0.02)', borderRadius: 6, fontSize: 11 }}>
              <div style={{ fontWeight: 600, marginBottom: 4, color: '#4e5969' }}>解析预览：</div>
              <div><Tag color={methodColors[preview.method]?.color}>{preview.method}</Tag> <span style={{ fontFamily: 'var(--font-mono)' }}>{preview.url}</span></div>
              {preview.headers.length > 0 && <div style={{ color: '#86909c', marginTop: 4 }}>Headers: {preview.headers.map(h => h.key).join(', ')}</div>}
              {preview.params.length > 0 && <div style={{ color: '#86909c' }}>Params: {preview.params.map(p => `${p.key}=${p.value}`).join(', ')}</div>}
              {preview.body && <div style={{ color: '#86909c' }}>Body: {preview.body.slice(0, 100)}{preview.body.length > 100 ? '...' : ''}</div>}
              {preview.auth.type !== 'none' && <div style={{ color: '#86909c' }}>Auth: {preview.auth.type}</div>}
            </div>
          )
        })()}
      </Modal>

      {/* Tab 栏 */}
      <div style={{ display: 'flex', borderBottom: '1px solid rgba(0,0,0,0.06)', background: '#fafbfc', flexShrink: 0, paddingLeft: 4, overflowX: 'auto' }}>
        {tabs.map(t => (
          <div key={t.key} onClick={() => setActiveTab(t.key)} style={{
            padding: '7px 12px', fontSize: 12, cursor: 'pointer', whiteSpace: 'nowrap',
            color: t.highlight ? '#0ea5a0' : activeTab === t.key ? '#0ea5a0' : '#86909c',
            fontWeight: activeTab === t.key ? 600 : 400,
            borderBottom: activeTab === t.key ? `2px solid ${t.highlight ? '#0ea5a0' : '#0ea5a0'}` : '2px solid transparent',
            transition: 'all 0.12s',
          }}>
            {t.icon}{t.label}
            {t.count > 0 && <span style={{ fontSize: 11, marginLeft: 3, color: activeTab === t.key ? '#0ea5a0' : '#86909c' }}>{t.count}</span>}
          </div>
        ))}
      </div>

      {/* Tab 内容 */}
      <div style={{ padding: '12px 16px', flex: 1, overflow: 'auto' }}>
        {activeTab === 'params' && <KvEditor items={step.params || []} onChange={v => up('params', v)} keyPh="参数名" valPh="参数值" />}
        {activeTab === 'headers' && <KvEditor items={step.headers || []} onChange={v => up('headers', v)} keyPh="Header" valPh="Value" />}
        {activeTab === 'body' && (
          <div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6 }}>
              <Select size="small" value={step.bodyType || 'json'} onChange={v => up('bodyType', v)} style={{ width: 140 }}
                options={[
                  { value: 'json', label: 'JSON' },
                  { value: 'form', label: 'x-www-form-urlencoded' },
                  { value: 'form-data', label: 'form-data' },
                  { value: 'raw', label: 'Raw' },
                  { value: 'none', label: '无 Body' },
                ]} />
              {(step.bodyType || 'json') === 'json' && (
                <Tooltip title="格式化 JSON">
                  <Button size="small" type="text" icon={<FormatPainterOutlined />} onClick={() => {
                    try { up('body', JSON.stringify(JSON.parse(step.body || '{}'), null, 2)) } catch {}
                  }}>格式化</Button>
                </Tooltip>
              )}
            </div>
            {(step.bodyType || 'json') === 'none' && (
              <div style={{ padding: '16px 0', textAlign: 'center', color: '#86909c', fontSize: 12 }}>此请求不包含 Body</div>
            )}
            {((step.bodyType || 'json') === 'json' || step.bodyType === 'raw') && (
              <Input.TextArea spellCheck={false} value={step.body || ''} onChange={e => up('body', e.target.value)}
                placeholder='{\n  "username": "admin"\n}' autoSize={{ minRows: 6, maxRows: 18 }}
                style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} />
            )}
            {step.bodyType === 'form' && (
              <KvEditor items={step.formParams || []} onChange={v => up('formParams', v)} keyPh="字段名" valPh="字段值" />
            )}
            {step.bodyType === 'form-data' && (
              <KvEditor items={step.formDataParams || []} onChange={v => up('formDataParams', v)} keyPh="字段名" valPh="字段值" />
            )}
          </div>
        )}
        {activeTab === 'auth' && <AuthEditor auth={step.auth} onChange={v => up('auth', v)} />}
        {activeTab === 'timing' && (
          <div style={{ padding: '8px 4px', fontSize: 12, color: '#4e5969', lineHeight: 1.9 }}>
            {/* 不做成隐形魔法：一步为什么慢、为什么会重发，页面上得看得见 */}
            <div style={{ marginBottom: 10, color: '#86909c' }}>
              被测系统的配置下发常是异步的（实测网关 0.06~0.5s 且抖动），而步骤之间只隔几毫秒 ——
              「发布完立刻打网关」会抢跑，跑出来是红的，但那不是缺陷。
              <b style={{ color: '#4e5969' }}>优先用重试，别用固定等待</b> —— 固定等待要么白等要么不够，换台机器就崩。
            </div>
            <Space size={16} wrap>
              <span>发请求前先等
                <InputNumber size="small" min={0} max={60000} step={100}
                  value={step.waitMs || 0} style={{ width: 92, margin: '0 4px' }}
                  onChange={v => up('waitMs', v || 0)} /> ms</span>
              <span>断言没过就重发，最多等
                <InputNumber size="small" min={0} max={120000} step={500}
                  value={step.retryTimeoutMs || 0} style={{ width: 100, margin: '0 4px' }}
                  onChange={v => up('retryTimeoutMs', v || 0)} /> ms（0=不重试）</span>
              <span>每隔
                <InputNumber size="small" min={50} max={10000} step={100}
                  value={step.retryIntervalMs || 300} style={{ width: 88, margin: '0 4px' }}
                  onChange={v => up('retryIntervalMs', v || 300)} /> ms 重发</span>
            </Space>
            {step.retryTimeoutMs > 0 && !['GET', 'HEAD', 'OPTIONS'].includes((step.method || 'GET').toUpperCase()) && (
              <div style={{ marginTop: 10, color: '#e8453c' }}>
                ⚠ {step.method} 上开了重试 —— 重试会<b>重发请求</b>，写操作重发会造出多份数据。
                确认这个接口幂等再用，否则该把重试放到后面那个「读回来确认」的步骤上。
              </div>
            )}
          </div>
        )}
        {activeTab === 'pre' && (
          <OperationList
            operations={preOps}
            onChange={ops => onChange({ ...step, preOperations: ops })}
            addItems={preAddItems}
            snippets={preScriptSnippets}
            infoBg="#e0f7f6" infoBorder="#bae7ff" infoColor="#0ea5a0"
            infoIcon="⚡" infoTitle="请求前执行"
            infoDesc="可修改请求参数、设置 Header、生成签名、准备数据"
          />
        )}
        {activeTab === 'post' && (
          <OperationList
            operations={postOps}
            onChange={ops => onChange({ ...step, postOperations: ops })}
            addItems={postAddItems}
            snippets={postScriptSnippets}
            infoBg="#fff7e6" infoBorder="rgba(255,125,0,0.3)" infoColor="#ff7d00"
            infoIcon="📋" infoTitle="请求后执行"
            infoDesc="执行顺序按列表排列，可拖拽调整"
          />
        )}
        {activeTab === 'code' && (() => {
          const genPython = () => {
            const m = (step.method || 'GET').toLowerCase()
            const lines = ['import httpx', '', `response = httpx.${m}(`]
            lines.push(`    "${resolvedUrl}",`)
            const hdrs = (step.headers || []).filter(h => h.key && h.enabled !== false)
            if (step.auth?.type === 'bearer' && step.auth.token) hdrs.push({ key: 'Authorization', value: `Bearer ${step.auth.token}` })
            if (hdrs.length) { lines.push(`    headers={${JSON.stringify(Object.fromEntries(hdrs.map(h => [h.key, h.value])))}},`) }
            if (m !== 'get' && step.body?.trim()) { lines.push(`    json=${step.body},`) }
            lines.push(')')
            lines.push('', 'print(response.status_code)', 'print(response.json())')
            return lines.join('\n')
          }
          const genJs = () => {
            const m = (step.method || 'GET').toUpperCase()
            const lines = [`const response = await fetch("${resolvedUrl}", {`, `  method: "${m}",`]
            const hdrs = (step.headers || []).filter(h => h.key && h.enabled !== false)
            if (step.auth?.type === 'bearer' && step.auth.token) hdrs.push({ key: 'Authorization', value: `Bearer ${step.auth.token}` })
            if (hdrs.length || (m !== 'GET' && step.body?.trim())) {
              const hObj = Object.fromEntries([...hdrs.map(h => [h.key, h.value]), ...(m !== 'GET' && (step.bodyType || 'json') === 'json' ? [['Content-Type', 'application/json']] : [])])
              lines.push(`  headers: ${JSON.stringify(hObj)},`)
            }
            if (m !== 'GET' && step.body?.trim()) { lines.push(`  body: JSON.stringify(${step.body}),`) }
            lines.push('})', '', 'const data = await response.json()', 'console.log(data)')
            return lines.join('\n')
          }
          const codeMap = { curl: generateCurl, python: genPython, javascript: genJs }
          const code = codeMap[codeLang]?.() || ''
          return (
            <div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                {['curl', 'python', 'javascript'].map(l => (
                  <div key={l} onClick={() => setCodeLang(l)} style={{
                    padding: '3px 10px', fontSize: 11, cursor: 'pointer', borderRadius: 8,
                    background: codeLang === l ? '#0ea5a0' : 'rgba(0,0,0,0.02)', color: codeLang === l ? '#fff' : '#4e5969',
                    fontWeight: codeLang === l ? 600 : 400, transition: 'all 0.12s',
                  }}>{l === 'curl' ? 'cURL' : l === 'python' ? 'Python' : 'JavaScript'}</div>
                ))}
                <div style={{ flex: 1 }} />
                <Tooltip title="复制"><Button size="small" icon={<CopyOutlined />} onClick={() => {
                  copyToClipboard(code).then(() => message.success('已复制'))
                }} /></Tooltip>
              </div>
              <pre style={{
                margin: 0, padding: 12, background: '#1e1e1e', color: '#d4d4d4', borderRadius: 6,
                fontFamily: 'var(--font-mono)', fontSize: 11, lineHeight: 1.6, maxHeight: 400, overflow: 'auto',
                whiteSpace: 'pre-wrap', wordBreak: 'break-all',
              }}>{code}</pre>
            </div>
          )
        })()}
        {activeTab === 'response' && (
          sending
            ? <div style={{ textAlign: 'center', padding: 40 }}><Spin tip="发送中..." /></div>
            : <ResponsePanel response={response} onAddAssertion={(path, value) => {
                const postOps = getOps(step, 'postOperations')
                const isStatus = path === 'status'
                const newAssertion = isStatus
                  ? { type: 'assertion', assertType: 'status', operator: 'eq', expected: String(value) }
                  : { type: 'assertion', assertType: 'jsonPath', path, operator: 'eq', expected: String(value) }
                onChange({ ...step, postOperations: [...postOps, newAssertion] })
                message.success(isStatus ? `已添加状态码断言: ${value}` : `已添加断言: ${path}`)
                setActiveTab('post')
              }} />
        )}
      </div>
    </div>
  )
}

// ===========================================================================
// 主组件：Apifox 风格左右分栏
// ===========================================================================

const addMenuItems = [
  { key: 'api', icon: <ApiOutlined />, label: 'API 请求' },
  { type: 'divider' },
  { key: 'group', icon: <FolderOutlined />, label: '分组' },
  { key: 'loop', icon: <RetweetOutlined />, label: '循环 (N 次)' },
  { key: 'forEach', icon: <UnorderedListOutlined />, label: 'ForEach 遍历' },
  { key: 'condition', icon: <BranchesOutlined />, label: '条件判断 (IF)' },
  { key: 'wait', icon: <ClockCircleOutlined />, label: '等待' },
]

function makeNewStep(key, seq) {
  if (key === 'api') return { nodeType: 'api', seq, method: 'GET', url: '', action: '', params: [], headers: [], body: '', bodyType: 'json', auth: { type: 'none' }, preOperations: [], postOperations: [{ type: 'assertion', assertType: 'status', operator: 'eq', expected: '200' }] }
  if (key === 'group') return { nodeType: 'group', seq, label: '', children: [] }
  if (key === 'loop') return { nodeType: 'loop', seq, label: '', times: 3, children: [] }
  if (key === 'forEach') return { nodeType: 'forEach', seq, iterVar: 'item', dataSource: '', children: [] }
  if (key === 'condition') return { nodeType: 'condition', seq, condition: '', thenSteps: [], elseSteps: [] }
  if (key === 'wait') return { nodeType: 'wait', seq, delay: 1000, label: '' }
}

// nodeTypes：允许添加的节点类型。默认全开（用例内嵌场景存 JSONB，什么结构都放得下）；
// 挂在 api_test_scenarios/api_test_steps 上时只能传 ['api'] —— 那张表没有
// node_type/children/times 这些列，存控制流会静默退化成一个 URL 为空的 GET。
// 收集树里所有 API 步骤的 id（分组/循环/条件都会嵌套，勾选要能穿透）。
function collectApiIds(nodes, out = []) {
  for (const n of nodes || []) {
    const nt = n.nodeType || 'api'
    if (nt === 'api') { if (n.id) out.push(n.id) }
    else {
      collectApiIds(n.children, out)
      collectApiIds(n.thenSteps, out)
      collectApiIds(n.elseSteps, out)
    }
  }
  return out
}

export default function ApiStepList({ steps, onChange, environments, runEnv, nodeTypes,
                                      runSelection, onRunSelectionChange }) {
  const allowedAddItems = useMemo(() => {
    if (!nodeTypes) return addMenuItems
    const allow = new Set(nodeTypes)
    return addMenuItems.filter(it => it.type === 'divider' ? false : allow.has(it.key))
  }, [nodeTypes])
  const [selected, setSelected] = useState(null)

  // ── 步骤列表宽度：默认宽一点 + 可拖拽，带上下限 ──
  //
  // 原来写死 300px。实际步骤名是「制备: 发布上线」「回滚前基准: 新路径通」这种
  // 带前缀的长句，300px 下大半被截成「记下「改路由之前」的...」，配上下面那行
  // 同样被截断的 URL，列表等于只能看出"这是第几步"。
  //
  // 上下限不是防御性代码，是这个布局的硬约束：
  //   下限 260 —— 再窄就装不下「方法标签 + 名字 + 断言/取值角标」，拖到那儿等于把功能拖没
  //   上限 720，且**不能吃掉右侧编辑器** —— 详情面板里 URL 行、Params/Headers 表格
  //             挤到 420 以下就开始换行错位，所以真正的上限取 min(720, 容器宽 - 420)
  const LIST_MIN = 260
  const LIST_MAX = 720
  const EDITOR_MIN = 420
  const wrapRef = useRef(null)
  const clampW = useCallback((w) => {
    const avail = wrapRef.current?.offsetWidth || 0
    // 容器还没量到（首帧）就先只用固定上限，量到之后再让编辑器的下限说话
    const max = avail ? Math.max(LIST_MIN, Math.min(LIST_MAX, avail - EDITOR_MIN)) : LIST_MAX
    return Math.round(Math.min(max, Math.max(LIST_MIN, w)))
  }, [])
  const [listW, setListW] = useState(() => {
    const saved = Number(localStorage.getItem('apiStepListWidth'))
    return saved >= LIST_MIN && saved <= LIST_MAX ? saved : 380
  })
  const [dragging, setDragging] = useState(false)

  const startResize = useCallback((e) => {
    e.preventDefault()
    const startX = e.clientX
    const startW = listW
    setDragging(true)
    const onMove = (ev) => setListW(clampW(startW + ev.clientX - startX))
    const onUp = () => {
      setDragging(false)
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      // 存的是钳过的值，别把越界值写进去 —— 下次读出来又要再钳一遍
      setListW(w => { localStorage.setItem('apiStepListWidth', String(w)); return w })
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [listW, clampW])

  // 窗口变窄时（或侧边栏收起）重新钳一次，否则列表会把编辑器挤没
  useEffect(() => {
    const onResize = () => setListW(w => clampW(w))
    window.addEventListener('resize', onResize)
    onResize()
    return () => window.removeEventListener('resize', onResize)
  }, [clampW])

  const baseUrl = useMemo(() => {
    if (!runEnv || !environments?.length) return ''
    const env = environments.find(e => e.id === runEnv)
    if (!env?.variables) return ''
    const v = env.variables.find(v => v.key === 'BASE_URL')
    return v?.value || ''
  }, [runEnv, environments])

  const handleSelect = useCallback((step, onStepChange) => {
    setSelected({ step, onStepChange })
  }, [])

  const handleDetailChange = useCallback((newStep) => {
    if (selected) {
      selected.onStepChange(newStep)
      setSelected(prev => prev ? { ...prev, step: newStep } : null)
    }
  }, [selected])

  useEffect(() => {
    if (!selected && steps.length > 0) {
      const firstApi = steps.findIndex(s => (s.nodeType || 'api') === 'api')
      if (firstApi >= 0) {
        const s = steps[firstApi]
        handleSelect(s, ns => onChange(steps.map((ss, j) => j === firstApi ? { ...ns, seq: j + 1 } : ss)))
      }
    }
  }, [steps.length])

  // ── 运行勾选（Apifox 那种）：只决定「运行全部」跑哪几步，不写库 ──
  // runSelection == null 表示"全选"。刻意不在挂载时展开成全量 id 集合：
  // 那样新增的步骤会默认不跑，而人的预期是"新加的当然要跑"。
  const allApiIds = useMemo(() => collectApiIds(steps), [steps])
  const runSel = useMemo(() => {
    if (!onRunSelectionChange) return null
    const isChecked = (s) => !s.id || runSelection == null || runSelection.has(s.id)
    return {
      isChecked,
      toggle: (s, checked) => {
        if (!s.id) return
        const next = new Set(runSelection == null ? allApiIds : runSelection)
        if (checked) next.add(s.id); else next.delete(s.id)
        // 又变回全选就还原成 null，避免"看着全勾着、其实是一份会过期的快照"
        onRunSelectionChange(next.size === allApiIds.length ? null : next)
      },
    }
  }, [runSelection, allApiIds, onRunSelectionChange])

  const checkedCount = runSelection == null ? allApiIds.length
    : allApiIds.filter(id => runSelection.has(id)).length

  // 勾掉的步骤如果是别人的上游，后面那些会静默全挂 —— 实测第一次用就踩到：
  // 勾掉「租户管理员登录」，剩下 17 步因为 ${token} 没解析全红，而页面只说
  // 「17 步失败」，完全看不出是自己勾掉了前置。这里把断掉的变量名直接点出来。
  const brokenVars = useMemo(() => {
    if (!runSel || runSelection == null) return []
    const flat = []
    const walk = (ns) => (ns || []).forEach(n => {
      const nt = n.nodeType || 'api'
      if (nt === 'api') flat.push(n)
      else { walk(n.children); walk(n.thenSteps); walk(n.elseSteps) }
    })
    walk(steps)
    const dropped = new Set()
    for (const s of flat) {
      if (s.id && !runSelection.has(s.id)) {
        for (const op of getOps(s, 'postOperations')) {
          if (op.type === 'extractor' && op.variable) dropped.add(op.variable)
        }
      }
    }
    if (!dropped.size) return []
    const used = new Set()
    for (const s of flat) {
      if (!s.id || !runSelection.has(s.id)) continue
      const text = [s.url, JSON.stringify(s.headers || ''), JSON.stringify(s.body || ''),
                    JSON.stringify(s.params || '')].join(' ')
      for (const m of text.matchAll(/\$\{(\w+)\}/g)) if (dropped.has(m[1])) used.add(m[1])
    }
    return [...used]
  }, [runSel, runSelection, steps])

  const handleAdd = ({ key }) => {
    const newSteps = [...steps, makeNewStep(key, steps.length + 1)]
    onChange(newSteps)
    if (key === 'api') {
      const newStep = newSteps[newSteps.length - 1]
      handleSelect(newStep, ns => {
        const idx = newSteps.length - 1
        onChange(newSteps.map((s, j) => j === idx ? { ...ns, seq: j + 1 } : s))
      })
    }
  }

  return (
    <div ref={wrapRef} style={{ display: 'flex', border: '1px solid rgba(0,0,0,0.05)', borderRadius: 8, overflow: 'hidden', minHeight: 480, height: 'calc(100vh - 340px)', maxHeight: 800, background: 'transparent',
      // 拖动时禁掉选中，否则一拖就把整页文字刷蓝
      userSelect: dragging ? 'none' : undefined }}>
      {/* 左侧：紧凑步骤列表（宽度可拖，见上面 LIST_MIN/LIST_MAX） */}
      <div style={{ width: listW, display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
        <div style={{ padding: '8px 10px', borderBottom: '1px solid rgba(0,0,0,0.06)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: '#fafbfc' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {runSel && allApiIds.length > 0 && (
              <Tooltip title="只决定「运行全部」跑哪几步，不改步骤本身，也不写库">
                <Checkbox
                  checked={checkedCount === allApiIds.length}
                  indeterminate={checkedCount > 0 && checkedCount < allApiIds.length}
                  onChange={e => onRunSelectionChange(e.target.checked ? null : new Set())} />
              </Tooltip>
            )}
            <span style={{ fontSize: 12, color: '#1d2129', fontWeight: 600 }}>步骤 ({steps.length})</span>
            {runSel && checkedCount < allApiIds.length && (
              // 少跑了几步必须在列表头写死 —— 否则跑完看到「全通过 6/6 步」，
              // 没人会想起来那是因为自己勾掉了 13 步。
              <span style={{ fontSize: 11, color: '#ff7d00', fontWeight: 600 }}>
                已选 {checkedCount}/{allApiIds.length}
              </span>
            )}
          </span>
          <Dropdown menu={{ items: allowedAddItems, onClick: handleAdd }} trigger={['click']}>
            <Button type="primary" size="small" icon={<PlusOutlined />} ghost>添加</Button>
          </Dropdown>
        </div>
        {brokenVars.length > 0 && (
          <div style={{ padding: '6px 10px', background: 'var(--orange-bg)', borderBottom: '1px solid rgba(250,173,20,0.3)',
            fontSize: 11, color: '#ad6800', lineHeight: 1.5 }}>
            勾掉的步骤负责提取 <b style={{ fontFamily: 'var(--font-mono)' }}>
              {brokenVars.map(v => '${' + v + '}').join('、')}</b>
            ，后面用到它的步骤会因「变量未解析」全部失败。
          </div>
        )}
        <div style={{ flex: 1, overflow: 'auto' }}>
          <CompactStepList steps={steps} onChange={onChange} selectedId={selected?.step} onSelect={handleSelect} runSel={runSel} />
        </div>
      </div>

      {/* 拖拽分隔条。它同时承担原来左栏的右边框 —— 否则拖动时会看到两条线。
          命中区做到 5px（1px 的边框鼠标够不着），hover/拖动时才染色，
          平时看起来就是一条普通分隔线。 */}
      <div
        onMouseDown={startResize}
        onDoubleClick={() => { const w = clampW(380); setListW(w); localStorage.setItem('apiStepListWidth', String(w)) }}
        title="拖动调整宽度，双击恢复默认"
        style={{
          width: 5, flexShrink: 0, cursor: 'col-resize',
          background: dragging ? 'rgba(14,165,160,0.45)' : 'rgba(0,0,0,0.06)',
          transition: dragging ? 'none' : 'background 0.15s',
        }}
        onMouseEnter={e => { if (!dragging) e.currentTarget.style.background = 'rgba(14,165,160,0.25)' }}
        onMouseLeave={e => { if (!dragging) e.currentTarget.style.background = 'rgba(0,0,0,0.06)' }}
      />

      {/* 右侧：步骤详情 */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        {selected ? (
          <StepDetailPanel step={selected.step} onChange={handleDetailChange} baseUrl={baseUrl} />
        ) : (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#86909c' }}>
            <div style={{ textAlign: 'center' }}>
              <ApiOutlined style={{ fontSize: 40, marginBottom: 12 }} />
              <div style={{ fontSize: 13 }}>请选择一个 API 步骤查看详情</div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ===========================================================================
// 代码生成（不变）
// ===========================================================================
function resolveVars(s) {
  return s.replace(/\{\{\$uuid\}\}/g, 'str(uuid.uuid4())')
    .replace(/\{\{\$timestamp\}\}/g, 'str(int(time.time()))')
    .replace(/\{\{\$timestampMs\}\}/g, 'str(int(time.time() * 1000))')
    .replace(/\{\{\$isoDate\}\}/g, 'datetime.now(timezone.utc).isoformat()')
    .replace(/\{\{\$randomInt\}\}/g, 'str(random.randint(0, 99999))')
    .replace(/\{\{\$randomFloat\}\}/g, 'str(round(random.random(), 4))')
    .replace(/\{\{\$randomEmail\}\}/g, 'f"user_{random.randint(1000,9999)}@test.com"')
    .replace(/\{\{\$randomPhone\}\}/g, 'f"138{random.randint(10000000,99999999)}"')
    .replace(/\{\{\$randomString\}\}/g, '"".join(random.choices(string.ascii_letters + string.digits, k=8))')
    .replace(/\{\{\$randomName\}\}/g, 'random.choice(["张三","李四","王五","赵六"])')
    .replace(/\{\{(\w+)\}\}/g, "{context.get('$1', '')}")
}

function genStepsCode(steps, indent = '    ') {
  const lines = []
  for (const s of steps) {
    const nt = s.nodeType || 'api'
    if (nt === 'group') {
      lines.push(`${indent}# ── ${s.label || '分组'} ──`)
      lines.push(...genStepsCode(s.children || [], indent))
      lines.push(`${indent}# ── /${s.label || '分组'} ──`)
      lines.push('')
      continue
    }
    if (nt === 'loop') {
      lines.push(`${indent}for _i in range(${s.times || 3}):  # ${s.label || '循环'}`)
      lines.push(...genStepsCode(s.children || [], indent + '    '))
      lines.push('')
      continue
    }
    if (nt === 'forEach') {
      lines.push(`${indent}for ${s.iterVar || 'item'} in ${s.dataSource || '[]'}:`)
      lines.push(...genStepsCode(s.children || [], indent + '    '))
      lines.push('')
      continue
    }
    if (nt === 'condition') {
      lines.push(`${indent}if ${s.condition || 'True'}:`)
      if ((s.thenSteps || []).length) lines.push(...genStepsCode(s.thenSteps, indent + '    '))
      else lines.push(`${indent}    pass`)
      if ((s.elseSteps || []).length) { lines.push(`${indent}else:`); lines.push(...genStepsCode(s.elseSteps, indent + '    ')) }
      lines.push('')
      continue
    }
    if (nt === 'wait') {
      lines.push(`${indent}time.sleep(${(s.delay || 1000) / 1000})  # ${s.label || '等待'}`)
      lines.push('')
      continue
    }
    lines.push(`${indent}# Step ${s.seq}: ${s.action || s.method + ' ' + s.url}`)
    const preOps = getOps(s, 'preOperations')
    const method = (s.method || 'GET').toLowerCase()
    // 两种 BASE_URL 写法都要剥掉：httpx client 已经带了 base_url，
    // 留着会生成 client.get("${BASE_URL}/api/..") 这种双份 base
    let url = resolveVars((s.url || '/').replace(/^\{\{\s*base_url\s*\}\}/gi, '').replace(/^\$\{\s*BASE_URL\s*\}/gi, ''))
    const hasBodySetter = preOps.some(op => op.type === 'bodySetter')
    const hasHeaderSetter = preOps.some(op => op.type === 'headerSetter')
    const stepHeaders = (s.headers || []).filter(h => h.key && h.enabled !== false)
    if (stepHeaders.length) {
      const obj = stepHeaders.map(h => `"${h.key}": "${h.value || ''}"`).join(', ')
      lines.push(`${indent}headers.update({${obj}})`)
    }
    if (hasBodySetter && !preOps.some(op => op.type === 'bodySetter' && op.mode === 'replace') && (s.bodyType || 'json') !== 'none' && s.body?.trim()) {
      lines.push(`${indent}body = '${s.body.replace(/'/g, "\\'").replace(/\n/g, '\\n')}'`)
    }
    for (const op of preOps) {
      if (op.type === 'headerSetter' && op.headerKey) {
        const val = op.sourceType === 'context' ? `context.get("${op.value || ''}", "")` : `"${op.value || ''}"`
        if (op.headerKey === 'Authorization' && op.sourceType === 'context') {
          lines.push(`${indent}headers["${op.headerKey}"] = f"Bearer {${val}}"`)
        } else {
          lines.push(`${indent}headers["${op.headerKey}"] = ${val}`)
        }
      }
      if (op.type === 'bodySetter') {
        if (op.mode === 'replace') {
          const val = op.sourceType === 'context' ? `context.get("${op.value || ''}", "{}")` : `'${(op.value || '{}').replace(/'/g, "\\'")}'`
          lines.push(`${indent}body = ${val}`)
        } else if (op.path) {
          lines.push(`${indent}_body = json.loads(body)`)
          const parts = op.path.split('.')
          let accessor = '_body'
          for (const p of parts.slice(0, -1)) accessor += `["${p}"]`
          const lastKey = parts[parts.length - 1]
          const val = op.sourceType === 'context' ? `context.get("${op.value || ''}", "")` : `"${op.value || ''}"`
          lines.push(`${indent}${accessor}["${lastKey}"] = ${val}`)
          lines.push(`${indent}body = json.dumps(_body, ensure_ascii=False)`)
        }
      }
      if (op.type === 'script' && op.code?.trim()) {
        lines.push(`${indent}# 前置脚本`)
        for (const line of op.code.trim().split('\n')) lines.push(`${indent}${line}`)
      }
      if (op.type === 'wait') lines.push(`${indent}time.sleep(${(op.delay || 1000) / 1000})`)
    }
    let kwargs = []
    if (s.params?.filter(p => p.key && p.enabled !== false).length) { const obj = s.params.filter(p => p.key && p.enabled !== false).map(p => `"${p.key}": "${p.value || ''}"`).join(', '); kwargs.push(`params={${obj}}`) }
    if (stepHeaders.length || hasHeaderSetter) { kwargs.push('headers=headers') }
    if (hasBodySetter) {
      kwargs.push('json=json.loads(body)' + (s.bodyType === 'form' ? '' : ''))
    } else if ((s.bodyType || 'json') !== 'none' && s.body?.trim()) {
      kwargs.push(s.bodyType === 'form' ? `data="${s.body}"` : `json=${s.body}`)
    }
    const argStr = kwargs.length ? `, ${kwargs.join(', ')}` : ''
    lines.push(`${indent}response = client.${method}(f"${url}"${argStr})`)
    const postOps = getOps(s, 'postOperations')
    for (const op of postOps) {
      if (op.type === 'assertion') {
        const a = op
        if (a.assertType === 'status') lines.push(`${indent}assert response.status_code == ${a.expected || 200}`)
        else if (a.assertType === 'jsonPath' && a.path) {
          const expr = 'response.json()' + a.path.replace('$.', '').split('.').map(p => `["${p}"]`).join('')
          // 每个 operator 都要出一行。认不出来的**必须留一条注释**——
          // 静默少导一条断言，导出的脚本比步骤本身弱，而看的人以为是等价的。
          const v = isNaN(a.expected) ? `"${a.expected}"` : a.expected
          const cmp = { eq: '==', ne: '!=', gt: '>', lt: '<', gte: '>=', lte: '<=' }[a.operator]
          if (a.operator === 'notEmpty') lines.push(`${indent}assert ${expr}`)
          else if (a.operator === 'isEmpty') lines.push(`${indent}assert len(${expr}) == 0`)
          else if (a.operator === 'notExists') lines.push(`${indent}assert ${expr.replace('response.json()', 'response.json()')} is None`)
          else if (a.operator === 'length') lines.push(`${indent}assert len(${expr}) == ${a.expected}`)
          else if (cmp) lines.push(`${indent}assert ${expr} ${cmp} ${v}`)
          else if (a.operator === 'contains') lines.push(`${indent}assert "${a.expected}" in str(${expr})`)
          else if (a.operator === 'notContains') lines.push(`${indent}assert "${a.expected}" not in str(${expr})`)
          else if (a.operator === 'in') lines.push(`${indent}assert ${expr} in [${String(a.expected || '').split(',').map(x => (isNaN(x) ? `"${x.trim()}"` : x.trim())).join(', ')}]`)
          else lines.push(`${indent}# TODO 这条断言导不出来：${a.operator} ${a.expected ?? ''}`)
        } else if (a.assertType === 'contains' && a.expected) lines.push(`${indent}assert "${a.expected}" in response.text`)
      }
      if (op.type === 'extractor' && op.variable && op.path) {
        const expr = 'response.json()' + op.path.replace('$.', '').split('.').map(p => `["${p}"]`).join('')
        lines.push(`${indent}context["${op.variable}"] = ${expr}`)
      }
      if (op.type === 'script' && op.code?.trim()) {
        lines.push(`${indent}# 后置脚本`)
        for (const line of op.code.trim().split('\n')) lines.push(`${indent}${line}`)
      }
      if (op.type === 'wait') lines.push(`${indent}time.sleep(${(op.delay || 1000) / 1000})`)
    }
    lines.push('')
  }
  return lines
}

export function generateApiCodeFromSteps(steps, title, baseUrl) {
  const fnName = 'test_' + (title || 'scenario').replace(/[^a-zA-Z0-9]/g, '_').replace(/_+/g, '_').substring(0, 40).toLowerCase()
  const needsExtras = JSON.stringify(steps).includes('{{$')
  const stepsJson = JSON.stringify(steps)
  const needsJson = stepsJson.includes('"bodySetter"')
  const resolvedBase = baseUrl || 'http://localhost:8000'
  const header = [
    'import httpx', 'import pytest', 'import time', 'import os',
    ...(needsJson ? ['import json'] : []),
    ...(needsExtras ? ['import uuid', 'import random', 'import string', 'from datetime import datetime, timezone'] : []),
    '', '', `BASE_URL = os.environ.get("BASE_URL", "${resolvedBase}")`, '', '',
    `def ${fnName}():`, `    """${title || '接口测试'}"""`, '    client = httpx.Client(base_url=BASE_URL)',
    '    context = {}', '    headers = {}', '',
  ]
  return [...header, ...genStepsCode(steps)].join('\n')
}
