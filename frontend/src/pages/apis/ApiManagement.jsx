import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useParams } from 'react-router-dom'
import { Input, Select, Button, Tag, Space, Tooltip, Dropdown, Popover, Checkbox, Spin, message, Modal, Upload, Empty, AutoComplete } from 'antd'
import {
  PlusOutlined, DeleteOutlined, FolderOutlined, FolderOpenOutlined, ApiOutlined,
  ImportOutlined, ExportOutlined, SearchOutlined, EditOutlined, CopyOutlined,
  SendOutlined, LoadingOutlined, GlobalOutlined, FormatPainterOutlined,
  LockOutlined, CodeOutlined, CaretRightOutlined, CaretDownOutlined,
  HolderOutlined, MoreOutlined, CheckCircleOutlined, FieldStringOutlined,
  ThunderboltOutlined, ClockCircleOutlined, CloseOutlined, SwapOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { useBranch } from '../../utils/branch'
import { useEnv, buildEnvOptions } from '../../utils/env'
import { copyToClipboard } from '../../utils/clipboard'

const methodColors = {
  GET: { color: '#0ea5a0', bg: '#e0f7f6' },
  POST: { color: '#fa8c16', bg: '#fff7e6' },
  PUT: { color: '#faad14', bg: '#fffbe6' },
  PATCH: { color: '#7c5cbf', bg: 'rgba(124,92,191,0.06)' },
  DELETE: { color: '#e8453c', bg: '#fff2f0' },
}

const commonHeaders = [
  { value: 'Content-Type', desc: 'application/json' },
  { value: 'Accept', desc: 'application/json' },
  { value: 'Authorization', desc: 'Bearer <token>' },
  { value: 'X-Request-ID', desc: 'UUID 追踪' },
  { value: 'Cache-Control', desc: 'no-cache' },
  { value: 'User-Agent', desc: 'testBench/1.0' },
]
const headerOptions = commonHeaders.map(h => ({ value: h.value, label: <span>{h.value} <span style={{ fontSize: 10, color: '#86909c' }}>{h.desc}</span></span> }))

// =========== Body 片段模板 ===========
const bodySnippets = [
  { key: 'login', label: '登录请求', code: JSON.stringify({ username: "admin", password: "admin123" }, null, 2) },
  { key: 'page', label: '分页查询', code: JSON.stringify({ page: 1, pageSize: 20, keyword: "" }, null, 2) },
  { key: 'create', label: '创建资源', code: JSON.stringify({ name: "", description: "", status: "active" }, null, 2) },
  { key: 'update', label: '更新资源', code: '{\n  "id": "{{ID}}",\n  "name": "",\n  "status": ""\n}' },
  { key: 'batch', label: '批量操作', code: '{\n  "ids": ["{{ID_1}}", "{{ID_2}}"],\n  "action": "delete"\n}' },
  { key: 'graphql', label: 'GraphQL', code: JSON.stringify({ query: "{ users { id name } }", variables: {} }, null, 2) },
  { key: 'upload', label: '文件上传元数据', code: JSON.stringify({ fileName: "", fileType: "image/png", size: 0 }, null, 2) },
]

// =========== Header 预设组 ===========
const headerPresets = [
  { key: 'json', label: 'JSON API', headers: [{ key: 'Content-Type', value: 'application/json' }, { key: 'Accept', value: 'application/json' }] },
  { key: 'form', label: 'Form 提交', headers: [{ key: 'Content-Type', value: 'application/x-www-form-urlencoded' }] },
  { key: 'multipart', label: '文件上传', headers: [{ key: 'Content-Type', value: 'multipart/form-data' }] },
  { key: 'bearer', label: '带 Token', headers: [{ key: 'Authorization', value: 'Bearer {{TOKEN}}' }] },
  { key: 'nocache', label: '缓存控制', headers: [{ key: 'Cache-Control', value: 'no-cache' }, { key: 'Pragma', value: 'no-cache' }] },
  { key: 'cors', label: 'CORS 调试', headers: [{ key: 'Origin', value: 'http://localhost:5173' }, { key: 'X-Requested-With', value: 'XMLHttpRequest' }] },
]

// =========== 代码生成 ===========
function genPythonCode(data, envVars) {
  const rv = (t) => resolveVars(t, envVars)
  const method = (data.method || 'GET').toLowerCase()
  const url = rv(data.url || '')
  const headers = (data.headers || []).filter(h => h.key && h.enabled !== false)
  const lines = ['import requests', '']
  if (headers.length) {
    lines.push('headers = {')
    headers.forEach(h => lines.push(`    "${rv(h.key)}": "${rv(h.value)}",`))
    lines.push('}')
  }
  const hasBody = ['post', 'put', 'patch'].includes(method) && data.body?.trim()
  if (hasBody) {
    if ((data.bodyType || 'json') === 'json') {
      lines.push('', 'payload = ' + (rv(data.body) || '{}'))
    } else {
      lines.push('', 'payload = """' + rv(data.body) + '"""')
    }
  }
  const args = []
  if (headers.length) args.push('headers=headers')
  if (hasBody) args.push((data.bodyType || 'json') === 'json' ? 'json=payload' : 'data=payload')
  lines.push('', `response = requests.${method}("${url}"${args.length ? ', ' + args.join(', ') : ''})`)
  lines.push('', 'print(response.status_code)', 'print(response.json())')
  return lines.join('\n')
}

function genJavaScriptCode(data, envVars) {
  const rv = (t) => resolveVars(t, envVars)
  const method = (data.method || 'GET').toUpperCase()
  const url = rv(data.url || '')
  const headers = (data.headers || []).filter(h => h.key && h.enabled !== false)
  const hasBody = ['POST', 'PUT', 'PATCH'].includes(method) && data.body?.trim()
  const opts = [`  method: '${method}',`]
  if (headers.length) {
    opts.push('  headers: {')
    headers.forEach(h => opts.push(`    '${rv(h.key)}': '${rv(h.value)}',`))
    opts.push('  },')
  }
  if (hasBody) opts.push(`  body: JSON.stringify(${rv(data.body) || '{}'}),`)
  const lines = [
    `const response = await fetch('${url}', {`,
    ...opts,
    '});', '',
    'const data = await response.json();',
    'console.log(response.status, data);',
  ]
  return lines.join('\n')
}

// =========== 变量插入按钮 ===========
function VarInsertBtn({ envVars, onInsert }) {
  if (!envVars?.length) return null
  return (
    <Popover trigger="click" placement="bottomRight" arrow={false}
      content={
        <div style={{ width: 260, maxHeight: 300, overflow: 'auto' }}>
          <div style={{ fontSize: 11, color: '#86909c', padding: '4px 8px 6px', fontWeight: 600 }}>点击插入变量</div>
          {envVars.map(v => (
            <div key={v.key} onClick={() => onInsert(`{{${v.key}}}`)}
              style={{ padding: '6px 10px', cursor: 'pointer', borderRadius: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
              onMouseEnter={e => e.currentTarget.style.background = 'rgba(14,165,160,0.06)'}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: '#fa8c16', fontWeight: 600 }}>{`{{${v.key}}}`}</span>
              <span style={{ fontSize: 10, color: '#86909c', maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v.value}</span>
            </div>
          ))}
        </div>
      }>
      <Tooltip title="插入环境变量">
        <Button type="text" size="small" style={{ color: '#fa8c16', fontSize: 12, padding: '0 4px' }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700 }}>{'{x}'}</span>
        </Button>
      </Tooltip>
    </Popover>
  )
}

// =========== KvEditor ===========
function KvEditor({ items = [], onChange, keyPh = 'Key', valPh = 'Value' }) {
  const [bulkMode, setBulkMode] = useState(false)
  const [bulkText, setBulkText] = useState('')
  const up = (i, f, v) => onChange(items.map((r, j) => j === i ? { ...r, [f]: v } : r))
  const isHeader = keyPh === 'Header'
  const toBulk = () => {
    setBulkText(items.filter(r => r.key).map(r => `${r.enabled === false ? '// ' : ''}${r.key}: ${r.value || ''}${r.desc ? `  // ${r.desc}` : ''}`).join('\n'))
    setBulkMode(true)
  }
  const fromBulk = () => {
    const newItems = bulkText.split('\n').filter(l => l.trim()).map(line => {
      const disabled = line.trimStart().startsWith('//')
      const clean = disabled ? line.replace(/^\s*\/\/\s*/, '') : line
      const descMatch = clean.match(/\s+\/\/\s*(.+)$/)
      const desc = descMatch ? descMatch[1] : ''
      const withoutDesc = descMatch ? clean.slice(0, descMatch.index) : clean
      const ci = withoutDesc.indexOf(':')
      if (ci < 0) return { key: withoutDesc.trim(), value: '', enabled: !disabled, desc }
      return { key: withoutDesc.slice(0, ci).trim(), value: withoutDesc.slice(ci + 1).trim(), enabled: !disabled, desc }
    })
    onChange(newItems)
    setBulkMode(false)
  }
  if (bulkMode) return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
        <span style={{ fontSize: 11, color: '#4e5969' }}>格式: <code style={{ fontSize: 10, background: 'rgba(0,0,0,0.04)', padding: '1px 4px', borderRadius: 4 }}>key: value  // 描述</code></span>
        <Space size={4}><Button size="small" onClick={() => setBulkMode(false)}>取消</Button><Button size="small" type="primary" onClick={fromBulk}>确定</Button></Space>
      </div>
      <Input.TextArea spellCheck={false} value={bulkText} onChange={e => setBulkText(e.target.value)} autoSize={{ minRows: 5, maxRows: 14 }} style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} />
    </div>
  )
  return (
    <div>
      {items.length > 0 && (
        <div style={{ display: 'flex', gap: 4, marginBottom: 4, padding: '0 4px', fontSize: 10, color: '#86909c', fontWeight: 600, alignItems: 'center' }}>
          <span style={{ width: 20 }}></span>
          <span style={{ flex: 3 }}>{keyPh}</span>
          <span style={{ flex: 4 }}>{valPh}</span>
          <span style={{ flex: 3 }}>描述</span>
          <span style={{ width: 24 }}></span>
          <Tooltip title="批量编辑"><Button type="text" size="small" icon={<EditOutlined />} onClick={toBulk} style={{ width: 20, height: 16, fontSize: 10, color: '#86909c' }} /></Tooltip>
        </div>
      )}
      {items.length === 0 && <div style={{ padding: '12px 0', textAlign: 'center', color: '#86909c', fontSize: 12 }}>暂无，点击添加</div>}
      {items.map((r, i) => (
        <div key={i} style={{ display: 'flex', gap: 4, marginBottom: 3, alignItems: 'center', opacity: r.enabled === false ? 0.45 : 1 }}>
          <Checkbox checked={r.enabled !== false} onChange={e => up(i, 'enabled', e.target.checked)} style={{ marginRight: -2 }} />
          {isHeader ? (
            <AutoComplete size="small" value={r.key} placeholder={keyPh} onChange={v => up(i, 'key', v)} options={headerOptions.filter(o => !r.key || o.value.toLowerCase().includes(r.key.toLowerCase()))} style={{ flex: 3, fontFamily: 'var(--font-mono)', fontSize: 11 }} />
          ) : (
            <Input spellCheck={false} size="small" value={r.key} placeholder={keyPh} onChange={e => up(i, 'key', e.target.value)} style={{ flex: 3, fontFamily: 'var(--font-mono)', fontSize: 11 }} />
          )}
          <Input spellCheck={false} size="small" value={r.value} placeholder={valPh} onChange={e => up(i, 'value', e.target.value)} style={{ flex: 4, fontFamily: 'var(--font-mono)', fontSize: 11 }} />
          <Input size="small" value={r.desc || ''} placeholder="描述" onChange={e => up(i, 'desc', e.target.value)} style={{ flex: 3, fontSize: 11, color: '#4e5969' }} />
          <Button type="text" size="small" icon={<DeleteOutlined />} danger onClick={() => onChange(items.filter((_, j) => j !== i))} />
        </div>
      ))}
      <Button type="dashed" size="small" block icon={<PlusOutlined />} onClick={() => onChange([...items, { key: '', value: '', enabled: true, desc: '' }])}>添加</Button>
    </div>
  )
}

// =========== AuthEditor ===========
function AuthEditor({ auth, onChange }) {
  const a = auth || { type: 'none' }
  const up = (f, v) => onChange({ ...a, [f]: v })
  return (
    <div>
      <Select size="small" value={a.type || 'none'} onChange={v => up('type', v)} style={{ width: 200, marginBottom: 12 }}
        options={[{ value: 'none', label: '无认证' }, { value: 'bearer', label: 'Bearer Token' }, { value: 'basic', label: 'Basic Auth' }, { value: 'apikey', label: 'API Key' }]} />
      {a.type === 'bearer' && <div><div style={{ fontSize: 11, color: '#4e5969', marginBottom: 4 }}>Token</div><Input spellCheck={false} size="small" value={a.token || ''} onChange={e => up('token', e.target.value)} placeholder="输入 Token" style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} /></div>}
      {a.type === 'basic' && <div style={{ display: 'flex', gap: 8, flexDirection: 'column' }}><div><div style={{ fontSize: 11, color: '#4e5969', marginBottom: 4 }}>用户名</div><Input size="small" value={a.username || ''} onChange={e => up('username', e.target.value)} /></div><div><div style={{ fontSize: 11, color: '#4e5969', marginBottom: 4 }}>密码</div><Input.Password size="small" value={a.password || ''} onChange={e => up('password', e.target.value)} /></div></div>}
      {a.type === 'apikey' && <div style={{ display: 'flex', gap: 8, flexDirection: 'column' }}><div style={{ display: 'flex', gap: 8 }}><div style={{ flex: 1 }}><div style={{ fontSize: 11, color: '#4e5969', marginBottom: 4 }}>Key</div><Input size="small" value={a.keyName || ''} onChange={e => up('keyName', e.target.value)} /></div><div style={{ flex: 1 }}><div style={{ fontSize: 11, color: '#4e5969', marginBottom: 4 }}>Value</div><Input spellCheck={false} size="small" value={a.keyValue || ''} onChange={e => up('keyValue', e.target.value)} style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} /></div></div><div><div style={{ fontSize: 11, color: '#4e5969', marginBottom: 4 }}>添加到</div><Select size="small" value={a.keyIn || 'header'} onChange={v => up('keyIn', v)} style={{ width: 160 }} options={[{ value: 'header', label: 'Header' }, { value: 'query', label: 'Query Params' }]} /></div></div>}
      {a.type === 'none' && <div style={{ padding: '12px 0', textAlign: 'center', color: '#86909c', fontSize: 12 }}>不使用认证</div>}
    </div>
  )
}

// =========== ResponsePanel ===========
function ResponsePanel({ response, onUseAsBody }) {
  const [viewTab, setViewTab] = useState('body')
  const [bodyMode, setBodyMode] = useState('pretty')
  const [search, setSearch] = useState('')
  if (!response) return null
  const r = response
  const sc = r.statusCode || 0
  const statusColor = sc === 0 ? '#e8453c' : sc < 300 ? '#0ea5a0' : sc < 400 ? '#faad14' : '#e8453c'
  const durationMs = r.durationMs || 0
  const durationColor = durationMs < 200 ? '#0ea5a0' : durationMs < 1000 ? '#faad14' : '#e8453c'
  const rawBody = r.body || ''
  let prettyBody = rawBody, isJson = false
  try { prettyBody = JSON.stringify(JSON.parse(rawBody), null, 2); isJson = true } catch {}
  const displayBody = bodyMode === 'pretty' && isJson ? prettyBody : rawBody
  const sizeStr = r.size > 1024 ? `${(r.size / 1024).toFixed(1)} KB` : `${r.size || 0} B`
  const respHeaders = r.headers || []
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', marginBottom: 8, borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
        <span style={{ fontWeight: 700, fontSize: 14, color: statusColor, background: statusColor + '10', padding: '2px 8px', borderRadius: 12 }}>{sc} {r.statusText || ''}</span>
        <span style={{ fontSize: 11, color: durationColor, fontWeight: 600 }}>{durationMs} ms</span>
        <span style={{ fontSize: 11, color: '#4e5969' }}>{sizeStr}</span>
        <div style={{ flex: 1 }} />
        {['body', 'headers'].map(t => (
          <div key={t} onClick={() => setViewTab(t)} style={{ padding: '2px 10px', fontSize: 11, cursor: 'pointer', color: viewTab === t ? '#0ea5a0' : '#666', fontWeight: viewTab === t ? 600 : 400, borderBottom: viewTab === t ? '2px solid #0ea5a0' : '2px solid transparent' }}>
            {t === 'body' ? 'Body' : `Headers (${respHeaders.length})`}
          </div>
        ))}
      </div>
      {sc === 0 && (
        <div style={{ marginBottom: 8, padding: '8px 12px', background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: 12, fontSize: 12, color: '#e8453c' }}>
          请求失败，请检查：URL 是否正确、服务是否在运行、环境变量是否配置（如 <code style={{ background: 'transparent', padding: '0 4px', borderRadius: 3 }}>{'{{BASE_URL}}'}</code>）
        </div>
      )}
      {viewTab === 'body' && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <div style={{ display: 'flex', gap: 0 }}>
              {isJson && ['pretty', 'raw'].map(m => (
                <div key={m} onClick={() => setBodyMode(m)} style={{ padding: '2px 8px', fontSize: 10, cursor: 'pointer', borderRadius: 6, background: bodyMode === m ? '#e0f7f6' : 'transparent', color: bodyMode === m ? '#0ea5a0' : '#666' }}>{m === 'pretty' ? 'Pretty' : 'Raw'}</div>
              ))}
            </div>
            <Space size={4}>
              <Input size="small" prefix={<SearchOutlined style={{ color: '#86909c' }} />} value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索" allowClear style={{ width: 140, fontSize: 11 }} />
              <Tooltip title="复制响应体"><Button size="small" icon={<CopyOutlined />} onClick={() => copyToClipboard(displayBody).then(() => message.success('已复制'))} /></Tooltip>
              {isJson && onUseAsBody && <Tooltip title="填入请求 Body"><Button size="small" icon={<SwapOutlined />} onClick={() => onUseAsBody(prettyBody)}>用作 Body</Button></Tooltip>}
            </Space>
          </div>
          <pre style={{ margin: 0, padding: 12, background: 'rgba(255,255,255,0.35)', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 12, fontFamily: 'var(--font-mono)', fontSize: 11, lineHeight: 1.6, maxHeight: 400, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', cursor: isJson ? 'text' : 'default' }}>{displayBody}</pre>
        </div>
      )}
      {viewTab === 'headers' && respHeaders.map((h, i) => (
        <div key={i} style={{ display: 'flex', gap: 8, padding: '5px 8px', borderBottom: '1px solid rgba(0,0,0,0.04)', fontSize: 11, borderRadius: 12, cursor: 'pointer' }}
          onClick={() => copyToClipboard(`${h.key}: ${h.value}`).then(() => message.success('已复制'))}
          onMouseEnter={e => e.currentTarget.style.background = 'rgba(0,0,0,0.02)'}
          onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
          <span style={{ fontWeight: 600, color: '#4e5969', width: 200, flexShrink: 0, fontFamily: 'var(--font-mono)' }}>{h.key}</span>
          <span style={{ color: '#4e5969', fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>{h.value}</span>
        </div>
      ))}
    </div>
  )
}

// =========== 左侧树节点 ===========
function TreeNode({ node, children, level, isSelected, onClick, onContextMenu, onRename, autoEdit }) {
  const [expanded, setExpanded] = useState(true)
  const [hovered, setHovered] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState(node.name)
  const isFolder = node.nodeType === 'folder'
  const mc = methodColors[node.method] || methodColors.GET

  useEffect(() => {
    if (autoEdit) { setEditName(node.name || ''); setEditing(true) }
  }, [autoEdit])

  const commitRename = () => {
    setEditing(false)
    if (editName.trim() && editName !== node.name) onRename(node.id, editName.trim())
  }

  if (isFolder) {
    return (
      <div>
        <div onClick={() => { setExpanded(!expanded); onClick(node) }}
          onDoubleClick={e => { e.stopPropagation(); setEditName(node.name); setEditing(true) }}
          onContextMenu={e => { e.preventDefault(); onContextMenu(node, e) }}
          onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
          style={{
            display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', paddingLeft: 10 + level * 16,
            cursor: 'pointer', background: isSelected ? '#e0f7f6' : hovered ? 'rgba(14,165,160,0.06)' : 'transparent',
          }}>
          {expanded ? <CaretDownOutlined style={{ fontSize: 9, color: '#4e5969' }} /> : <CaretRightOutlined style={{ fontSize: 9, color: '#4e5969' }} />}
          {expanded ? <FolderOpenOutlined style={{ fontSize: 12, color: '#faad14' }} /> : <FolderOutlined style={{ fontSize: 12, color: '#faad14' }} />}
          {editing ? (
            <Input size="small" autoFocus value={editName} onChange={e => setEditName(e.target.value)}
              onClick={e => e.stopPropagation()} onBlur={commitRename}
              onPressEnter={commitRename} onKeyDown={e => { if (e.key === 'Escape') setEditing(false) }}
              style={{ flex: 1, fontSize: 11, height: 20, padding: '0 4px' }} />
          ) : (
            <span style={{ fontSize: 12, color: '#1d2129', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{node.name}</span>
          )}
          {hovered && !editing && <MoreOutlined style={{ color: '#86909c', fontSize: 12 }} onClick={e => { e.stopPropagation(); onContextMenu(node, e) }} />}
        </div>
        {expanded && children}
      </div>
    )
  }

  const displayName = node.name || (node.url ? node.url.split('?')[0] : '未命名接口')
  const showUrlHint = node.name && node.url && node.url !== node.name

  return (
    <div onClick={() => onClick(node)}
      onDoubleClick={e => { e.stopPropagation(); setEditName(node.name || ''); setEditing(true) }}
      onContextMenu={e => { e.preventDefault(); onContextMenu(node, e) }}
      onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px', paddingLeft: 10 + level * 16,
        cursor: 'pointer', background: isSelected ? mc.bg : hovered ? 'rgba(14,165,160,0.06)' : 'transparent',
        borderLeft: isSelected ? `3px solid ${mc.color}` : '3px solid transparent',
      }}>
      <Tag style={{ margin: 0, fontWeight: 700, fontSize: 8, background: mc.bg, color: mc.color, border: 'none', padding: '0 4px', lineHeight: '14px', minWidth: 32, textAlign: 'center' }}>{node.method || 'GET'}</Tag>
      {editing ? (
        <Input size="small" autoFocus value={editName} onChange={e => setEditName(e.target.value)}
          onClick={e => e.stopPropagation()} onBlur={commitRename}
          onPressEnter={commitRename} onKeyDown={e => { if (e.key === 'Escape') setEditing(false) }}
          style={{ flex: 1, fontSize: 11, height: 20, padding: '0 4px' }} />
      ) : (
        <span style={{ fontSize: 12, color: '#1d2129', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {displayName}
          {showUrlHint && (
            <span style={{ fontSize: 10, color: '#86909c', marginLeft: 4 }}>{node.url.split('?')[0]}</span>
          )}
        </span>
      )}
      {hovered && !editing && <MoreOutlined style={{ color: '#86909c', fontSize: 12 }} onClick={e => { e.stopPropagation(); onContextMenu(node, e) }} />}
    </div>
  )
}

function buildTree(nodes) {
  const map = {}
  const roots = []
  for (const n of nodes) map[n.id] = { ...n, children: [] }
  for (const n of nodes) {
    if (n.parentId && map[n.parentId]) map[n.parentId].children.push(map[n.id])
    else roots.push(map[n.id])
  }
  return roots
}

function renderTree(nodes, level, selectedId, onSelect, onCtx, onRename, newNodeId) {
  return nodes.map(n => (
    <TreeNode key={n.id} node={n} level={level} isSelected={selectedId === n.id}
      onClick={onSelect} onContextMenu={onCtx} onRename={onRename} autoEdit={n.id === newNodeId}>
      {n.children?.length > 0 && renderTree(n.children, level + 1, selectedId, onSelect, onCtx, onRename, newNodeId)}
    </TreeNode>
  ))
}

// =========== URL ↔ Params 双向同步 ===========
function urlToParams(url) {
  try {
    const q = url.includes('?') ? url.split('?')[1] : ''
    if (!q) return []
    return q.split('&').filter(Boolean).map(s => {
      const [k, ...rest] = s.split('=')
      return { key: decodeURIComponent(k), value: decodeURIComponent(rest.join('=')), enabled: true, desc: '' }
    })
  } catch { return [] }
}

function paramsToQuery(params) {
  const active = (params || []).filter(p => p.key && p.enabled !== false)
  if (!active.length) return ''
  return '?' + active.map(p => `${encodeURIComponent(p.key)}=${encodeURIComponent(p.value || '')}`).join('&')
}

function getUrlBase(url) {
  return (url || '').split('?')[0]
}

// =========== cURL 解析器 ===========
function parseCurl(curlStr) {
  const s = curlStr.replace(/\\\n/g, ' ').replace(/\\\r\n/g, ' ').trim()
  const result = { method: 'GET', url: '', headers: [], body: '', bodyType: 'json', auth: null, params: [] }
  const tokenRe = /'([^']*)'|"([^"]*)"|(\S+)/g
  const tokens = []
  let m
  while ((m = tokenRe.exec(s))) tokens.push(m[1] ?? m[2] ?? m[3])
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i]
    if (t === 'curl') continue
    if ((t === '-X' || t === '--request') && tokens[i + 1]) { result.method = tokens[++i].toUpperCase(); continue }
    if ((t === '-H' || t === '--header') && tokens[i + 1]) {
      const h = tokens[++i], ci = h.indexOf(':')
      if (ci > 0) result.headers.push({ key: h.slice(0, ci).trim(), value: h.slice(ci + 1).trim(), enabled: true, desc: '' })
      continue
    }
    if ((t === '-d' || t === '--data' || t === '--data-raw' || t === '--data-binary') && tokens[i + 1]) {
      result.body = tokens[++i]; if (result.method === 'GET') result.method = 'POST'; continue
    }
    if ((t === '-u' || t === '--user') && tokens[i + 1]) {
      const [u, p] = tokens[++i].split(':')
      result.auth = { type: 'basic', username: u, password: p || '' }; continue
    }
    if (t.startsWith('http://') || t.startsWith('https://')) result.url = t
  }
  if (result.url.includes('?')) { result.params = urlToParams(result.url); result.url = getUrlBase(result.url) }
  const authH = result.headers.find(h => h.key.toLowerCase() === 'authorization')
  if (authH && !result.auth) {
    if (authH.value.toLowerCase().startsWith('bearer ')) result.auth = { type: 'bearer', token: authH.value.slice(7) }
    result.headers = result.headers.filter(h => h !== authH)
  }
  try { JSON.parse(result.body); result.bodyType = 'json' } catch { if (result.body.includes('=')) result.bodyType = 'form'; else result.bodyType = 'raw' }
  return result
}

// =========== 变量替换引擎 ===========
function resolveVars(text, vars) {
  if (!text || !vars?.length) return text
  return String(text).replace(/\{\{(\w+)\}\}/g, (match, key) => {
    const v = vars.find(v => v.key === key)
    return v ? v.value : match
  })
}

function hasVars(text) {
  return /\{\{\w+\}\}/.test(text || '')
}

// =========== 右侧编辑器 ===========
function EndpointEditor({ node, onSave, onSend, sending, response, envVars, onDirtyChange, onPreviewChange }) {
  const [data, setData] = useState(node)
  const [activeTab, setActiveTab] = useState('params')
  const [dirty, setDirty] = useState(false)
  const [importCurlOpen, setImportCurlOpen] = useState(false)
  const [curlText, setCurlText] = useState('')
  const [codeGenOpen, setCodeGenOpen] = useState(false)
  const [codeGenLang, setCodeGenLang] = useState('python')
  const syncingRef = useRef(false)
  const bodyRef = useRef(null)

  useEffect(() => { setData(node); setDirty(false); setActiveTab('params') }, [node.id])

  useEffect(() => {
    if (response && !sending) setActiveTab('response')
  }, [response, sending])

  const up = (f, v) => {
    setData(prev => ({ ...prev, [f]: v }))
    setDirty(true); onDirtyChange?.(true)
    if (f === 'name' || f === 'method') onPreviewChange?.({ [f]: v })
  }
  const method = data.method || 'GET'
  const mc = methodColors[method] || methodColors.GET

  const handleUrlChange = (newUrl) => {
    if (syncingRef.current) return
    syncingRef.current = true
    up('url', newUrl)
    if (newUrl.includes('?')) {
      const newParams = urlToParams(newUrl)
      setData(prev => ({ ...prev, url: newUrl, params: newParams }))
      setDirty(true)
    }
    syncingRef.current = false
  }

  const handleParamsChange = (newParams) => {
    if (syncingRef.current) return
    syncingRef.current = true
    const base = getUrlBase(data.url || '')
    const query = paramsToQuery(newParams)
    setData(prev => ({ ...prev, params: newParams, url: base + query }))
    setDirty(true)
    syncingRef.current = false
  }

  const handleImportCurl = () => {
    const parsed = parseCurl(curlText)
    setData(prev => ({
      ...prev,
      method: parsed.method, url: parsed.url + paramsToQuery(parsed.params),
      headers: parsed.headers.length ? parsed.headers : prev.headers,
      body: parsed.body || prev.body, bodyType: parsed.bodyType,
      params: parsed.params.length ? parsed.params : prev.params,
      auth: parsed.auth || prev.auth,
    }))
    setDirty(true)
    setImportCurlOpen(false)
    setCurlText('')
    message.success('cURL 已导入')
  }

  const paramCount = (data.params || []).filter(p => p.key && p.enabled !== false).length
  const headerCount = (data.headers || []).filter(h => h.key && h.enabled !== false).length
  const hasAuth = data.auth?.type && data.auth.type !== 'none'

  const handleSend = () => {
    if (!data.url?.trim()) { message.warning('请输入请求 URL'); return }
    onSend(data)
  }

  const resolvedUrl = resolveVars(data.url || '', envVars)
  const urlHasVars = hasVars(data.url)
  const isGet = method === 'GET'

  let jsonError = null
  if ((data.bodyType || 'json') === 'json' && data.body?.trim()) {
    try { JSON.parse(data.body) } catch (e) { jsonError = e.message }
  }

  const generatedCode = codeGenLang === 'python' ? genPythonCode(data, envVars) : genJavaScriptCode(data, envVars)

  const tabs = [
    { key: 'params', label: 'Params', count: paramCount },
    { key: 'body', label: 'Body', count: data.body?.trim() ? 1 : 0, dim: isGet },
    { key: 'headers', label: 'Headers', count: headerCount },
    { key: 'auth', label: 'Auth', count: hasAuth ? 1 : 0 },
    { key: 'desc', label: '说明', count: data.description?.trim() ? 1 : 0 },
    ...(response ? [{ key: 'response', label: 'Response', count: 0, highlight: true }] : []),
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }} onKeyDown={e => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); handleSend() }
      if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); if (dirty) { onSave(data); setDirty(false); onDirtyChange?.(false) } }
    }}>
      {/* Name + Save */}
      <div style={{ padding: '8px 16px', flexShrink: 0, display: 'flex', alignItems: 'center', gap: 8, borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
        <Input variant="borderless" value={data.name || ''} onChange={e => up('name', e.target.value)}
          placeholder="输入接口名称" style={{ fontSize: 14, fontWeight: 600, color: '#1d2129', flex: 1, padding: '2px 4px' }} />
        <Button size="small" type={dirty ? 'primary' : 'default'} disabled={!dirty} onClick={() => { onSave(data); setDirty(false); onDirtyChange?.(false) }}
          style={dirty ? {} : { color: '#c9cdd4', borderColor: 'rgba(0,0,0,0.06)' }}>保存</Button>
      </div>

      {/* Method + URL + Send */}
      <div style={{ padding: '8px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Select size="middle" value={method} onChange={v => up('method', v)} style={{ width: 100 }} popupMatchSelectWidth={false}
            options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map(m => ({ value: m, label: <span style={{ color: methodColors[m]?.color, fontWeight: 700, fontSize: 13 }}>{m}</span> }))} />
          <Input spellCheck={false} value={data.url || ''} onChange={e => handleUrlChange(e.target.value)}
            placeholder="{{BASE_URL}}/api/users 或 https://example.com/api"
            style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 13 }}
            onPaste={e => {
              const text = e.clipboardData?.getData('text') || ''
              if (text.trimStart().toLowerCase().startsWith('curl ')) {
                e.preventDefault(); setCurlText(text); setImportCurlOpen(true)
              }
            }} />
          <Dropdown menu={{ items: [
            { key: 'import-curl', icon: <ImportOutlined />, label: '导入 cURL', onClick: () => setImportCurlOpen(true) },
            { key: 'copy-curl', icon: <CopyOutlined />, label: '复制 cURL', onClick: () => {
              const fullUrl = resolvedUrl
              const parts = [`curl -X ${method}`, `  '${fullUrl}'`]
              ;(data.headers || []).filter(h => h.key && h.enabled !== false).forEach(h => parts.push(`  -H '${resolveVars(h.key, envVars)}: ${resolveVars(h.value, envVars)}'`))
              if (method !== 'GET' && data.body?.trim()) parts.push(`  -d '${resolveVars(data.body, envVars).replace(/'/g, "'\\''")}'`)
              copyToClipboard(parts.join(' \\\n')).then(() => message.success('cURL 已复制'))
            }},
            { type: 'divider' },
            { key: 'gen-python', icon: <CodeOutlined />, label: '生成 Python 代码', onClick: () => { setCodeGenLang('python'); setCodeGenOpen(true) } },
            { key: 'gen-js', icon: <CodeOutlined />, label: '生成 JavaScript 代码', onClick: () => { setCodeGenLang('javascript'); setCodeGenOpen(true) } },
          ]}} trigger={['click']}>
            <Button icon={<CodeOutlined />} style={{ color: '#4e5969' }} />
          </Dropdown>
          <VarInsertBtn envVars={envVars} onInsert={v => { const cur = data.url || ''; up('url', cur + v) }} />
          <Tooltip title="Ctrl+Enter 发送">
            <Button type="primary" icon={sending ? <LoadingOutlined /> : <SendOutlined />} loading={sending} onClick={handleSend}
              style={{ fontWeight: 600, minWidth: 72, height: 32 }}>发送</Button>
          </Tooltip>
        </div>
        {urlHasVars && (
          <div style={{ marginTop: 4, fontSize: 11, color: '#86909c', fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            <span style={{ color: '#0ea5a0', marginRight: 4 }}>→</span>{resolvedUrl}
          </div>
        )}
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', borderBottom: '1px solid rgba(0,0,0,0.04)', background: 'transparent', flexShrink: 0, paddingLeft: 8, overflowX: 'auto' }}>
        {tabs.map(t => (
          <div key={t.key} onClick={() => setActiveTab(t.key)} style={{
            padding: '8px 14px', fontSize: 12, cursor: 'pointer', whiteSpace: 'nowrap',
            color: t.highlight ? '#0ea5a0' : activeTab === t.key ? '#0ea5a0' : t.dim ? '#bbb' : '#666',
            fontWeight: activeTab === t.key ? 600 : 400,
            borderBottom: activeTab === t.key ? `2px solid ${t.highlight ? '#0ea5a0' : '#0ea5a0'}` : '2px solid transparent',
          }}>{t.label}{t.count > 0 && <span style={{ fontSize: 10, marginLeft: 3, color: '#86909c' }}>{t.count}</span>}</div>
        ))}
      </div>

      {/* Tab content */}
      <div style={{ padding: '12px 16px', flex: 1, overflow: 'auto' }}>
        {activeTab === 'params' && <KvEditor items={data.params || []} onChange={handleParamsChange} keyPh="参数名" valPh="参数值" />}
        {activeTab === 'headers' && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 6, gap: 4 }}>
              <Dropdown menu={{ items: headerPresets.map(p => ({
                key: p.key, label: p.label,
                onClick: () => {
                  const cur = data.headers || []
                  const existKeys = new Set(cur.map(h => h.key.toLowerCase()))
                  const newHeaders = p.headers.filter(h => !existKeys.has(h.key.toLowerCase())).map(h => ({ ...h, enabled: true, desc: '' }))
                  if (newHeaders.length) { up('headers', [...cur, ...newHeaders]); message.success(`已添加 ${newHeaders.length} 个 Header`) }
                  else message.info('Header 已存在，跳过')
                },
              })) }} trigger={['click']}>
                <Button size="small" icon={<ThunderboltOutlined />} style={{ fontSize: 11 }}>快速添加</Button>
              </Dropdown>
            </div>
            <KvEditor items={data.headers || []} onChange={v => up('headers', v)} keyPh="Header" valPh="Value" />
          </div>
        )}
        {activeTab === 'body' && (
          <div>
            {isGet && <div style={{ marginBottom: 8, padding: '4px 10px', background: '#fff7e8', border: '1px solid rgba(255,125,0,0.2)', borderRadius: 12, fontSize: 11, color: '#ff7d00' }}>GET 请求通常不携带 Body，如需要请切换 Method</div>}
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6 }}>
              <Select size="small" value={data.bodyType || 'json'} onChange={v => up('bodyType', v)} style={{ width: 140 }}
                options={[{ value: 'json', label: 'JSON' }, { value: 'form', label: 'x-www-form-urlencoded' }, { value: 'form-data', label: 'form-data' }, { value: 'raw', label: 'Raw' }, { value: 'none', label: '无 Body' }]} />
              {(data.bodyType || 'json') === 'json' && (
                <>
                  <Tooltip title="格式化 JSON"><Button size="small" type="text" icon={<FormatPainterOutlined />} onClick={() => { try { up('body', JSON.stringify(JSON.parse(data.body || '{}'), null, 2)) } catch {} }}>格式化</Button></Tooltip>
                  <Popover trigger="click" placement="bottomLeft" arrow={false}
                    content={
                      <div style={{ width: 280, maxHeight: 360, overflow: 'auto' }}>
                        <div style={{ fontSize: 11, color: '#86909c', padding: '4px 8px 6px', fontWeight: 600 }}>点击插入 Body 模板</div>
                        {bodySnippets.map(s => (
                          <div key={s.key} onClick={() => { up('body', s.code); up('bodyType', 'json') }}
                            style={{ padding: '6px 10px', cursor: 'pointer', borderRadius: 12, marginBottom: 2 }}
                            onMouseEnter={e => e.currentTarget.style.background = 'rgba(14,165,160,0.06)'}
                            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: '#1d2129', marginBottom: 2 }}>{s.label}</div>
                            <pre style={{ margin: 0, fontSize: 10, color: '#4e5969', fontFamily: 'var(--font-mono)', lineHeight: 1.4, maxHeight: 60, overflow: 'hidden', whiteSpace: 'pre-wrap' }}>{s.code}</pre>
                          </div>
                        ))}
                      </div>
                    }>
                    <Button size="small" type="text" icon={<CodeOutlined />} style={{ color: '#0ea5a0' }}>插入片段</Button>
                  </Popover>
                </>
              )}
              <div style={{ flex: 1 }} />
              <VarInsertBtn envVars={envVars} onInsert={v => up('body', (data.body || '') + v)} />
            </div>
            {(data.bodyType || 'json') === 'none' && <div style={{ padding: '12px 0', textAlign: 'center', color: '#86909c', fontSize: 12 }}>无 Body</div>}
            {((data.bodyType || 'json') === 'json' || data.bodyType === 'raw') && (
              <>
                <Input.TextArea spellCheck={false} ref={bodyRef} value={data.body || ''} onChange={e => up('body', e.target.value)} placeholder='{"key": "value"}' autoSize={{ minRows: 6, maxRows: 18 }} style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} />
                {jsonError && <div style={{ marginTop: 4, fontSize: 11, color: '#e8453c', padding: '2px 8px', background: '#fff2f0', borderRadius: 12 }}>JSON 语法错误: {jsonError}</div>}
              </>
            )}
            {data.bodyType === 'form' && <KvEditor items={data.formParams || []} onChange={v => up('formParams', v)} keyPh="字段名" valPh="字段值" />}
            {data.bodyType === 'form-data' && <KvEditor items={data.formDataParams || []} onChange={v => up('formDataParams', v)} keyPh="字段名" valPh="字段值" />}
          </div>
        )}
        {activeTab === 'auth' && <AuthEditor auth={data.auth} onChange={v => up('auth', v)} />}
        {activeTab === 'desc' && <Input.TextArea value={data.description || ''} onChange={e => up('description', e.target.value)} placeholder="接口说明、备注..." autoSize={{ minRows: 4, maxRows: 12 }} style={{ fontSize: 12 }} />}
        {activeTab === 'response' && (sending ? <div style={{ textAlign: 'center', padding: 40 }}><Spin tip="发送中..." /></div> : <ResponsePanel response={response} onUseAsBody={body => { up('body', body); up('bodyType', 'json'); setActiveTab('body'); message.success('已填入 Body') }} />)}
      </div>

      {/* cURL Import Modal */}
      <Modal open={importCurlOpen} onCancel={() => { setImportCurlOpen(false); setCurlText('') }}
        title="导入 cURL" width={560} okText="导入" onOk={handleImportCurl} okButtonProps={{ disabled: !curlText.trim() }}>
        <div style={{ fontSize: 12, color: '#4e5969', marginBottom: 8 }}>粘贴 cURL 命令，自动解析为请求参数。也可以直接在 URL 栏粘贴 cURL 命令。</div>
        <Input.TextArea spellCheck={false} value={curlText} onChange={e => setCurlText(e.target.value)}
          placeholder={'curl -X GET \'https://api.example.com/users\' \\\n  -H \'Authorization: Bearer token\' \\\n  -H \'Content-Type: application/json\''}
          autoSize={{ minRows: 6, maxRows: 14 }} style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} />
      </Modal>

      {/* Code Generation Modal */}
      <Modal open={codeGenOpen} onCancel={() => setCodeGenOpen(false)} title="生成代码" width={600} footer={null}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          {['python', 'javascript'].map(lang => (
            <Button key={lang} size="small" type={codeGenLang === lang ? 'primary' : 'default'} onClick={() => setCodeGenLang(lang)}>
              {lang === 'python' ? 'Python (requests)' : 'JavaScript (fetch)'}
            </Button>
          ))}
          <div style={{ flex: 1 }} />
          <Tooltip title="复制代码"><Button size="small" icon={<CopyOutlined />} onClick={() => copyToClipboard(generatedCode).then(() => message.success('代码已复制'))}>复制</Button></Tooltip>
        </div>
        <pre style={{ margin: 0, padding: 16, background: '#1e1e1e', color: '#d4d4d4', borderRadius: 12, fontFamily: 'var(--font-mono)', fontSize: 12, lineHeight: 1.6, maxHeight: 400, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{generatedCode}</pre>
      </Modal>
    </div>
  )
}

// =========== 主页面 ===========
export default function ApiManagement() {
  const { projectId } = useParams()
  const [branchId] = useBranch(projectId)
  const [nodes, setNodes] = useState([])
  const [loading, setLoading] = useState(true)
  const [openTabs, setOpenTabs] = useState([])
  const [activeTabId, setActiveTabId] = useState(null)
  const [search, setSearch] = useState('')
  const [importOpen, setImportOpen] = useState(false)
  const [importFile, setImportFile] = useState(null)
  const [importing, setImporting] = useState(false)
  const [sendingMap, setSendingMap] = useState({})
  const [responses, setResponses] = useState({})
  const [environments, setEnvironments] = useState([])
  const [runEnv, setRunEnv] = useEnv(projectId)
  const [envVars, setEnvVars] = useState([])
  const [ctxMenu, setCtxMenu] = useState(null)
  const [newNodeId, setNewNodeId] = useState(null)
  const [dirtyTabs, setDirtyTabs] = useState(new Set())
  const [navCollapsed, setNavCollapsed] = useState(false)

  const loadNodes = async () => {
    try {
      const res = await api.get(`/projects/${projectId}/api-nodes${branchId ? `?branch_id=${branchId}` : ''}`)
      setNodes(res.data || [])
    } catch { message.error('加载失败') }
    setLoading(false)
  }

  useEffect(() => {
    loadNodes()
    api.get(`/projects/${projectId}/environments`).then(res => {
      const envs = res.data || []
      setEnvironments(envs)
      const activeId = runEnv || (envs.length ? envs[0].id : null)
      if (activeId && !runEnv) setRunEnv(activeId)
      if (activeId) {
        api.get(`/projects/${projectId}/environments/${activeId}/variables`).then(r => {
          const env = envs.find(e => e.id === activeId)
          if (env) env.variables = r.data || []
          setEnvironments([...envs])
          setEnvVars(r.data || [])
        }).catch(() => {})
      }
    }).catch(() => {})
  }, [projectId, branchId])

  const selected = nodes.find(n => n.id === activeTabId)
  const tree = useMemo(() => buildTree(nodes), [nodes])

  const handleEnvChange = useCallback((envId) => {
    setRunEnv(envId)
    if (!envId) { setEnvVars([]); return }
    const env = environments.find(e => e.id === envId)
    if (env?.variables) {
      setEnvVars(env.variables)
    } else {
      api.get(`/projects/${projectId}/environments/${envId}/variables`).then(res => {
        env.variables = res.data || []
        setEnvironments([...environments])
        setEnvVars(res.data || [])
      }).catch(() => {})
    }
  }, [environments])
  const filteredTree = useMemo(() => {
    if (!search) return tree
    const filtered = nodes.filter(n => n.name?.toLowerCase().includes(search.toLowerCase()) || n.url?.toLowerCase().includes(search.toLowerCase()))
    const ids = new Set(filtered.map(n => n.id))
    filtered.forEach(n => { let p = n.parentId; while (p) { ids.add(p); p = nodes.find(nn => nn.id === p)?.parentId } })
    return buildTree(nodes.filter(n => ids.has(n.id)))
  }, [tree, search, nodes])

  const openTab = (node) => {
    if (!openTabs.includes(node.id)) setOpenTabs(prev => [...prev, node.id])
    setActiveTabId(node.id)
  }

  const closeTab = (id, e) => {
    e?.stopPropagation()
    setOpenTabs(prev => {
      const next = prev.filter(t => t !== id)
      if (activeTabId === id) setActiveTabId(next.length > 0 ? next[next.length - 1] : null)
      return next
    })
    setResponses(prev => { const n = { ...prev }; delete n[id]; return n })
  }

  const handleCreate = async (parentId, type) => {
    const body = type === 'folder'
      ? { parentId, nodeType: 'folder', name: '新建文件夹', sortOrder: nodes.length }
      : { parentId, nodeType: 'endpoint', name: '新建接口', method: 'GET', url: '', sortOrder: nodes.length }
    try {
      const res = await api.post(`/projects/${projectId}/api-nodes${branchId ? `?branch_id=${branchId}` : ''}`, body)
      setNodes(prev => [...prev, res.data])
      if (type === 'endpoint') openTab(res.data)
      setNewNodeId(res.data.id)
      setTimeout(() => setNewNodeId(null), 500)
      message.success(`${type === 'folder' ? '文件夹' : '接口'}已创建`)
    } catch { message.error('创建失败') }
  }

  const handleSave = async (data) => {
    try {
      const res = await api.put(`/projects/${projectId}/api-nodes/${data.id}`, data)
      setNodes(prev => prev.map(n => n.id === data.id ? { ...n, ...res.data } : n))
      message.success('已保存')
    } catch { message.error('保存失败') }
  }

  const handleRename = async (id, newName) => {
    setNodes(prev => prev.map(n => n.id === id ? { ...n, name: newName } : n))
    try {
      await api.put(`/projects/${projectId}/api-nodes/${id}`, { name: newName })
    } catch { message.error('重命名失败') }
  }

  const handleDelete = async (id) => {
    try {
      await api.del(`/projects/${projectId}/api-nodes/${id}`)
      const idsToRemove = new Set([id])
      let changed = true
      while (changed) {
        changed = false
        for (const n of nodes) {
          if (n.parentId && idsToRemove.has(n.parentId) && !idsToRemove.has(n.id)) {
            idsToRemove.add(n.id)
            changed = true
          }
        }
      }
      setNodes(prev => prev.filter(n => !idsToRemove.has(n.id)))
      idsToRemove.forEach(rid => closeTab(rid))
      message.success('已删除')
    } catch { message.error('删除失败') }
  }

  const handleDuplicate = async (id) => {
    try {
      const res = await api.post(`/projects/${projectId}/api-nodes/${id}/duplicate`)
      setNodes(prev => [...prev, res.data])
      openTab(res.data)
      message.success('已复制')
    } catch { message.error('复制失败') }
  }

  const handleSend = async (data) => {
    setSendingMap(prev => ({ ...prev, [data.id]: true }))
    setResponses(prev => ({ ...prev, [data.id]: null }))
    try {
      const fullUrl = resolveVars(data.url || '', envVars)
      const resolvedHeaders = (data.headers || []).map(h => ({
        ...h, key: resolveVars(h.key, envVars), value: resolveVars(h.value, envVars),
      }))
      const resolvedBody = resolveVars(data.body || '', envVars)
      const res = await api.post('/debug/send', {
        method: data.method || 'GET', url: fullUrl,
        params: data.params || [], headers: resolvedHeaders,
        body: resolvedBody, bodyType: data.bodyType || 'json', auth: data.auth || null,
      })
      setResponses(prev => ({ ...prev, [data.id]: res.data }))
    } catch (e) {
      setResponses(prev => ({ ...prev, [data.id]: { statusCode: 0, statusText: '连接失败', headers: [], body: e.message, durationMs: 0, size: 0 } }))
    }
    setSendingMap(prev => ({ ...prev, [data.id]: false }))
  }

  const handleImport = async () => {
    if (!importFile) return
    setImporting(true)
    try {
      const text = await importFile.text()
      const collection = JSON.parse(text)
      await api.post(`/projects/${projectId}/api-nodes/import/postman${branchId ? `?branch_id=${branchId}` : ''}`, { collection })
      await loadNodes()
      setImportOpen(false)
      setImportFile(null)
      message.success('导入成功')
    } catch (e) {
      message.error('导入失败: ' + (e.message || '格式错误'))
    }
    setImporting(false)
  }

  const contextMenuItems = (node) => [
    ...(node.nodeType === 'folder' ? [
      { key: 'add-endpoint', icon: <ApiOutlined />, label: '新建接口', onClick: () => handleCreate(node.id, 'endpoint') },
      { key: 'add-folder', icon: <FolderOutlined />, label: '新建子文件夹', onClick: () => handleCreate(node.id, 'folder') },
      { type: 'divider' },
    ] : []),
    { key: 'duplicate', icon: <CopyOutlined />, label: '复制', onClick: () => handleDuplicate(node.id) },
    { key: 'delete', icon: <DeleteOutlined />, label: '删除', danger: true, onClick: () => {
      Modal.confirm({ title: `确认删除「${node.name}」?`, content: node.nodeType === 'folder' ? '文件夹下的所有接口也会被删除' : undefined, onOk: () => handleDelete(node.id) })
    }},
  ]

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>

  return (
    <div style={{ display: 'flex', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 12, overflow: 'hidden', height: 'calc(100vh - 80px)', background: 'transparent' }}>
      {/* 左侧：接口树 */}
      {navCollapsed ? (
        <div
          onClick={() => setNavCollapsed(false)}
          style={{ width: 20, borderRight: '1px solid rgba(0,0,0,0.05)', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', flexShrink: 0, background: 'rgba(255,255,255,0.35)' }}
          title="展开侧栏"
        >
          <MenuUnfoldOutlined style={{ fontSize: 12, color: '#86909c' }} />
        </div>
      ) : (
      <div style={{ width: 300, borderRight: '1px solid rgba(0,0,0,0.05)', display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
        {/* 工具栏 */}
        <div style={{ padding: '8px 10px', borderBottom: '1px solid rgba(0,0,0,0.05)', display: 'flex', alignItems: 'center', gap: 6, background: 'rgba(255,255,255,0.35)' }}>
          <Input size="small" prefix={<SearchOutlined style={{ color: '#86909c' }} />} placeholder="搜索接口..."
            value={search} onChange={e => setSearch(e.target.value)} allowClear style={{ flex: 1, fontSize: 11 }} />
          <Tooltip title="收起侧栏">
            <Button type="text" size="small" icon={<MenuFoldOutlined style={{ fontSize: 12 }} />} onClick={() => setNavCollapsed(true)} style={{ flexShrink: 0 }} />
          </Tooltip>
          <Dropdown menu={{ items: [
            { key: 'endpoint', icon: <ApiOutlined />, label: '新建接口', onClick: () => handleCreate(null, 'endpoint') },
            { key: 'folder', icon: <FolderOutlined />, label: '新建文件夹', onClick: () => handleCreate(null, 'folder') },
            { type: 'divider' },
            { key: 'import', icon: <ImportOutlined />, label: '导入 Postman', onClick: () => setImportOpen(true) },
          ]}} trigger={['click']}>
            <Button type="primary" size="small" icon={<PlusOutlined />} ghost />
          </Dropdown>
        </div>

        {/* 全局环境选择器 */}
        <div style={{ padding: '6px 10px', borderBottom: '1px solid rgba(0,0,0,0.05)', display: 'flex', alignItems: 'center', gap: 6, background: 'transparent' }}>
          <GlobalOutlined style={{ fontSize: 12, color: '#0ea5a0' }} />
          {/* minWidth: 0 不能省。flex 子项默认 min-width:auto —— 不会缩到比内容更窄，
              而环境标签是「stoa (网关管理平台-本地 | http://192.168.51.108:5176)」这种
              一整串不可断的文本，于是它顶着容器往外撑，直接盖到右边的接口详情区上。
              popupMatchSelectWidth={false} 只管下拉浮层，管不了收起来时的框。 */}
          <Select size="small" value={runEnv || '__none__'} onChange={v => handleEnvChange(v === '__none__' ? null : v)}
            style={{ flex: 1, minWidth: 0 }} popupMatchSelectWidth={false}
            options={[{ value: '__none__', label: '无环境' }, ...buildEnvOptions(environments)]} />
          {envVars.length > 0 && (
            <Popover trigger="click" placement="rightTop" arrow={false}
              content={
                <div style={{ width: 360 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#1d2129', padding: '4px 0 8px', borderBottom: '1px solid rgba(0,0,0,0.04)', marginBottom: 8 }}>
                    环境变量 — {environments.find(e => e.id === runEnv)?.name || ''}
                  </div>
                  {envVars.map((v, i) => (
                    <div key={v.key || i} style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 4 }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: '#fa8c16', fontWeight: 600, width: 100, flexShrink: 0 }}>{v.key}</span>
                      <Input spellCheck={false} size="small" value={v.value} onChange={e => {
                        const newVars = envVars.map((vv, j) => j === i ? { ...vv, value: e.target.value } : vv)
                        setEnvVars(newVars)
                      }} style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 11 }} />
                    </div>
                  ))}
                  <Button size="small" type="primary" block style={{ marginTop: 8 }} onClick={async () => {
                    try {
                      await api.put(`/projects/${projectId}/environments/${runEnv}/variables`, envVars.map(v => ({ key: v.key, value: v.value, description: v.description })))
                      const env = environments.find(e => e.id === runEnv)
                      if (env) env.variables = [...envVars]
                      message.success('变量已保存')
                    } catch { message.error('保存失败') }
                  }}>保存变量</Button>
                </div>
              }>
              <span style={{ fontSize: 10, color: '#0ea5a0', cursor: 'pointer', textDecoration: 'underline', whiteSpace: 'nowrap', flexShrink: 0 }}>{envVars.length} 变量</span>
            </Popover>
          )}
        </div>

        {/* 树 */}
        <div style={{ flex: 1, overflow: 'auto' }}>
          {filteredTree.length === 0 ? (
            <Empty description="暂无接口" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ padding: 40 }}>
              <Space direction="vertical" size={8}>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => handleCreate(null, 'endpoint')}>新建接口</Button>
                <Button icon={<ImportOutlined />} onClick={() => setImportOpen(true)}>导入 Postman</Button>
              </Space>
            </Empty>
          ) : (
            renderTree(filteredTree, 0, activeTabId, (node) => {
              if (node.nodeType === 'endpoint') openTab(node)
              else setActiveTabId(node.id)
            }, (node, e) => {
              setCtxMenu({ node, items: contextMenuItems(node), x: e.clientX, y: e.clientY })
            }, handleRename, newNodeId)
          )}
        </div>
      </div>
      )}

      {/* 右侧 */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        {/* 多标签栏 */}
        {openTabs.length > 0 && (
          <div style={{ display: 'flex', borderBottom: '1px solid rgba(0,0,0,0.05)', background: 'transparent', overflowX: 'auto', flexShrink: 0 }}>
            {openTabs.map(tid => {
              const tn = nodes.find(n => n.id === tid)
              if (!tn) return null
              const mc = methodColors[tn.method] || methodColors.GET
              const isActive = tid === activeTabId
              return (
                <div key={tid} onClick={() => setActiveTabId(tid)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 6, padding: '7px 14px', cursor: 'pointer',
                    borderBottom: isActive ? `2px solid ${mc.color}` : '2px solid transparent',
                    background: isActive ? 'rgba(255,255,255,0.5)' : 'transparent', whiteSpace: 'nowrap', fontSize: 12,
                    borderRight: '1px solid rgba(0,0,0,0.06)',
                  }}>
                  <Tag style={{ margin: 0, fontWeight: 700, fontSize: 8, background: mc.bg, color: mc.color, border: 'none', padding: '0 3px', lineHeight: '13px' }}>{tn.method || 'GET'}</Tag>
                  <span style={{ color: isActive ? '#1d2129' : '#666', maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {dirtyTabs.has(tid) && <span style={{ color: '#fa8c16', marginRight: 2 }}>●</span>}
                    {tn.name || '未命名接口'}
                  </span>
                  <CloseOutlined onClick={e => closeTab(tid, e)} style={{ fontSize: 9, color: '#c9cdd4', marginLeft: 4 }} />
                </div>
              )
            })}
          </div>
        )}

        {/* 编辑器内容 */}
        {selected && selected.nodeType === 'endpoint' ? (
          <EndpointEditor key={activeTabId} node={selected} onSave={handleSave} onSend={handleSend}
            sending={!!sendingMap[activeTabId]} response={responses[activeTabId]} envVars={envVars}
            onDirtyChange={isDirty => setDirtyTabs(prev => {
              const next = new Set(prev)
              isDirty ? next.add(activeTabId) : next.delete(activeTabId)
              return next
            })}
            onPreviewChange={patch => setNodes(prev => prev.map(n => n.id === activeTabId ? { ...n, ...patch } : n))} />
        ) : selected && selected.nodeType === 'folder' ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
              <FolderOpenOutlined style={{ fontSize: 20, color: '#faad14' }} />
              <Input value={selected.name} onChange={e => {
                const newName = e.target.value
                setNodes(prev => prev.map(n => n.id === selected.id ? { ...n, name: newName } : n))
              }} onBlur={() => handleSave({ ...selected, name: nodes.find(n => n.id === selected.id)?.name })}
                variant="borderless" style={{ fontSize: 16, fontWeight: 600, flex: 1 }} />
            </div>
            <div style={{ color: '#4e5969', fontSize: 12 }}>
              包含 {nodes.filter(n => n.parentId === selected.id).length} 个子项
            </div>
            <div style={{ marginTop: 16 }}>
              <Space>
                <Button icon={<ApiOutlined />} onClick={() => handleCreate(selected.id, 'endpoint')}>新建接口</Button>
                <Button icon={<FolderOutlined />} onClick={() => handleCreate(selected.id, 'folder')}>新建子文件夹</Button>
              </Space>
            </div>
          </div>
        ) : (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#86909c' }}>
            <div style={{ textAlign: 'center' }}>
              <ApiOutlined style={{ fontSize: 40, marginBottom: 12 }} />
              <div style={{ fontSize: 13 }}>选择一个接口开始调试</div>
              <div style={{ fontSize: 11, marginTop: 6 }}>或导入 Postman Collection 快速开始</div>
            </div>
          </div>
        )}
      </div>

      {/* 右键菜单 */}
      {ctxMenu && (
        <div style={{ position: 'fixed', left: ctxMenu.x, top: ctxMenu.y, zIndex: 1000 }} onClick={() => setCtxMenu(null)}>
          <div style={{ background: 'rgba(255,255,255,0.85)', backdropFilter: 'blur(12px)', borderRadius: 12, boxShadow: '0 6px 16px rgba(0,0,0,0.12)', padding: '4px 0', minWidth: 160 }}>
            {ctxMenu.items.map((item, i) => item.type === 'divider'
              ? <div key={i} style={{ height: 1, background: 'rgba(0,0,0,0.06)', margin: '4px 0' }} />
              : <div key={item.key} onClick={item.onClick} style={{
                  padding: '6px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
                  color: item.danger ? '#e8453c' : '#4e5969',
                }} onMouseEnter={e => e.currentTarget.style.background = 'rgba(14,165,160,0.06)'} onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                  {item.icon}<span>{item.label}</span>
                </div>
            )}
          </div>
        </div>
      )}
      {ctxMenu && <div style={{ position: 'fixed', inset: 0, zIndex: 999 }} onClick={() => setCtxMenu(null)} />}

      {/* Import Modal */}
      <Modal open={importOpen} onCancel={() => { setImportOpen(false); setImportFile(null) }}
        title="导入 Postman Collection" width={520} okText="导入" onOk={handleImport} confirmLoading={importing}>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, color: '#4e5969', marginBottom: 8 }}>
            支持 Postman Collection v2.0/v2.1 JSON 格式。从 Postman 中 Export → Collection v2.1 即可。
          </div>
          <Upload.Dragger accept=".json"
            beforeUpload={file => { setImportFile(file); return false }}
            fileList={importFile ? [importFile] : []}
            onRemove={() => setImportFile(null)}>
            <p style={{ fontSize: 12, color: '#4e5969' }}><ImportOutlined style={{ fontSize: 24, color: '#0ea5a0', display: 'block', marginBottom: 8 }} />点击或拖拽 JSON 文件到此处</p>
          </Upload.Dragger>
        </div>
      </Modal>
    </div>
  )
}
