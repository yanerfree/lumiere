import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import {
  Button, Input, Select, Tag, Space, message, Popconfirm, Tooltip, Modal, Dropdown, Radio, Switch, InputNumber
} from 'antd'
import {
  SendOutlined, DeleteOutlined, CopyOutlined, LoadingOutlined,
  ClockCircleOutlined, ReloadOutlined, ClearOutlined,
  FolderAddOutlined, FileAddOutlined, FolderOutlined, EditOutlined,
  PlusOutlined, ImportOutlined, CodeOutlined, SaveOutlined, CheckOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, ArrowUpOutlined, ArrowDownOutlined, StopOutlined
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'
import { useTextSearch, TextSearchBar, highlightText, scrollMatchIntoView } from '../../components/TextSearch'

const { TextArea } = Input
const MONO = 'var(--font-mono)'
const MC = { GET: '#0ea5a0', POST: '#ff7d00', PUT: '#4e8af0', DELETE: '#e8453c', PATCH: '#7c5cbf', HEAD: '#86909c', OPTIONS: '#86909c', MCP: '#7c5cbf' }

// ── MCP 请求：选「调用方式」自动生成请求体，人只改地址和工具名 ──
const MCP_METHODS = [
  { value: 'tools/call', label: '调用工具（tools/call）' },
  { value: 'tools/list', label: '列出工具（tools/list）' },
  { value: 'initialize', label: '初始化（initialize）' },
  { value: 'resources/list', label: '列出资源（resources/list）' },
  { value: 'resources/read', label: '读取资源（resources/read）' },
  { value: 'prompts/list', label: '列出提示词（prompts/list）' },
  { value: 'prompts/get', label: '获取提示词（prompts/get）' },
  { value: 'ping', label: '连通测试（ping）' },
]
const MCP_SESSIONS = [
  { value: 'none', label: '不打招呼（不带会话号）' },
  { value: 'batch', label: '整批同一会话（点一次发送算一批）' },
  { value: 'each', label: '每条单独会话' },
]
const MCP_DEFAULT = { method: 'tools/call', toolName: '', arguments: '{}', uri: '', promptName: '', session: 'none' }
const MCP_HEADERS = [
  { key: 'Content-Type', value: 'application/json', enabled: true },
  { key: 'Accept', value: 'application/json, text/event-stream', enabled: true },
]
function parseArgs(text) {
  if (!text || !text.trim()) return {}
  try { const v = JSON.parse(text); return v && typeof v === 'object' && !Array.isArray(v) ? v : null } catch { return null }
}
function buildMcpBody(cfg) {
  const c = { ...MCP_DEFAULT, ...(cfg || {}) }
  const msg = { jsonrpc: '2.0', id: 1, method: c.method }
  const args = parseArgs(c.arguments) || {}
  if (c.method === 'initialize') msg.params = { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'lumiere-http-client', version: '1.0' } }
  else if (c.method === 'tools/call') msg.params = { name: c.toolName || '', arguments: args }
  else if (c.method === 'resources/read') msg.params = { uri: c.uri || '' }
  else if (c.method === 'prompts/get') msg.params = { name: c.promptName || '', arguments: args }
  else if (c.method !== 'ping') msg.params = {}
  return JSON.stringify(msg, null, 2)
}
// 必需的两个请求头没有就补上，已有的（不分大小写）不动
function ensureMcpHeaders(headers) {
  const hs = (headers || []).filter(h => h.key?.trim() || h.value?.trim())
  const have = new Set(hs.map(h => h.key.trim().toLowerCase()))
  return [...MCP_HEADERS.filter(h => !have.has(h.key.toLowerCase())), ...hs]
}
const tabFromItem = (item) => ({
  id: item.id, name: item.name, method: item.method || 'GET', url: item.url || '',
  headers: item.headers || [{ key: '', value: '', enabled: true }], body: item.body || '', bodyType: item.bodyType || 'none',
  kind: item.kind || 'http', mcpConfig: item.mcpConfig || null, _dirty: false,
})

// ── cURL 工具 ──
function parseCurl(curl) {
  const str = curl.replace(/\\\n\s*/g, ' ').trim()
  let method = 'GET', url = '', headers = [], body = ''
  const xMatch = str.match(/-X\s+(\w+)/)
  if (xMatch) method = xMatch[1].toUpperCase()
  const urlMatch = str.match(/curl\s+(?:.*?\s+)?['"]?(https?:\/\/[^\s'"]+)['"]?/) || str.match(/['"]?(https?:\/\/[^\s'"]+)['"]?/)
  if (urlMatch) url = urlMatch[1]
  const hMatches = [...str.matchAll(/-H\s+['"]([^'"]+)['"]/g)]
  hMatches.forEach(m => { const idx = m[1].indexOf(':'); if (idx > 0) headers.push({ key: m[1].substring(0, idx).trim(), value: m[1].substring(idx + 1).trim(), enabled: true }) })
  const dMatch = str.match(/-d\s+['"](.+?)['"]\s*(?:-|$)/) || str.match(/--data(?:-raw)?\s+['"](.+?)['"]/)
  if (dMatch) { body = dMatch[1]; if (!xMatch) method = 'POST' }
  return { method, url, headers: headers.length ? headers : [{ key: '', value: '', enabled: true }], body }
}

function toCurl(item) {
  const parts = [`curl -X ${item.method}`, `  '${item.url || 'http://example.com'}'`]
  ;(item.headers || []).forEach(h => { if (h.key?.trim() && h.enabled !== false) parts.push(`  -H '${h.key}: ${h.value || ''}'`) })
  if (['POST', 'PUT', 'PATCH'].includes(item.method) && item.body?.trim()) parts.push(`  -d '${item.body.replace(/'/g, "\\'")}'`)
  return parts.join(' \\\n')
}

function toCode(item, lang) {
  const url = item.url || 'http://example.com'
  const hdrs = (item.headers || []).filter(h => h.key?.trim() && h.enabled !== false)
  const hasBody = ['POST', 'PUT', 'PATCH'].includes(item.method) && item.body?.trim()
  if (lang === 'python') {
    let c = 'import requests\n\n'
    if (hdrs.length) c += `headers = {\n${hdrs.map(h => `    '${h.key}': '${h.value}'`).join(',\n')}\n}\n\n`
    c += `resp = requests.${item.method.toLowerCase()}('${url}'`
    if (hdrs.length) c += ', headers=headers'
    if (hasBody) c += `, data='${item.body.replace(/'/g, "\\'")}'`
    return c + ')\nprint(resp.status_code, resp.json())'
  }
  if (lang === 'javascript') {
    const opts = [`  method: '${item.method}'`]
    if (hdrs.length) opts.push(`  headers: {\n${hdrs.map(h => `    '${h.key}': '${h.value}'`).join(',\n')}\n  }`)
    if (hasBody) opts.push(`  body: '${item.body.replace(/'/g, "\\'")}'`)
    return `fetch('${url}', {\n${opts.join(',\n')}\n}).then(r => r.json()).then(console.log)`
  }
  return toCurl(item)
}

// 列表里第 i 行往上(-1)/往下(+1)挪一格，返回新数组
function moveRow(list, i, delta) {
  const j = i + delta
  if (j < 0 || j >= list.length) return list
  const out = [...list]
  ;[out[i], out[j]] = [out[j], out[i]]
  return out
}

// ── Headers 批量编辑：一行一个「参数名: 参数值」，行首 // 表示停用，行尾「 // 」后面是备注 ──
// 备注的分隔要求 // 前面有空白，否则 http://x 这种值会被从中间劈开
const BULK_DESC_RE = /\s+\/\/\s?(.*)$/
function headersToBulk(headers) {
  return (headers || []).filter(h => h.key || h.value || h.desc).map(h => {
    let line = `${h.key || ''}: ${h.value || ''}`
    if (h.desc) line += ` // ${h.desc}`
    return h.enabled === false ? `// ${line}` : line
  }).join('\n')
}
function bulkToHeaders(text) {
  return text.split('\n').map(raw => {
    let line = raw.trim()
    if (!line) return null
    let enabled = true
    if (line.startsWith('//')) { enabled = false; line = line.slice(2).trim() }
    let desc = ''
    const m = line.match(BULK_DESC_RE)
    if (m) { desc = m[1].trim(); line = line.slice(0, m.index) }
    const idx = line.indexOf(':')
    const key = (idx >= 0 ? line.slice(0, idx) : line).trim()
    const value = idx >= 0 ? line.slice(idx + 1).trim() : ''
    return { key, value, desc, enabled }
  }).filter(Boolean)
}

// ── 刷新后回到原样：开着的请求、当前请求、目录展开、发送设置等记在本浏览器（按登录用户分开存）──
function viewKey() {
  try { return 'httpClient.view.' + (JSON.parse(localStorage.getItem('user'))?.id || 'anon') } catch { return 'httpClient.view.anon' }
}
function loadView() {
  try { return JSON.parse(localStorage.getItem(viewKey())) || {} } catch { return {} }
}

export default function HttpClient() {
  const savedView = useRef(loadView()).current
  const viewRestored = useRef(false)   // 标签页恢复完之前不写回，免得把上次的记录先冲成空的
  const [items, setItems] = useState([])
  const [openTabs, setOpenTabs] = useState([])
  const [activeTabId, setActiveTabId] = useState(null)
  const [responses, setResponses] = useState({})
  const [sending, setSending] = useState({})
  const [batchRuns, setBatchRuns] = useState({})   // 标签页 id → 正在批量发送的 runId
  const [requestTimeout, setRequestTimeout] = useState(savedView.timeout ?? 120)
  const [sendCount, setSendCount] = useState(savedView.count ?? 1)       // 共发几次
  const [sendPerRound, setSendPerRound] = useState(savedView.perRound ?? 1) // 一次几条（每轮同时发几条）
  const [sendInterval, setSendInterval] = useState(savedView.interval ?? null) // 间隔几秒，空 = 不等
  const [showBatchRows, setShowBatchRows] = useState(true)   // 逐条默认展开
  const [history, setHistory] = useState([])
  const [showHistory, setShowHistory] = useState(!!savedView.showHistory)
  const [editingNameId, setEditingNameId] = useState(null)
  const [editingNameValue, setEditingNameValue] = useState('')
  const [expandedFolders, setExpandedFolders] = useState(savedView.expanded || {})
  const [hoverItemId, setHoverItemId] = useState(null)
  const [popupItemId, setPopupItemId] = useState(null)
  const [selectedFolderId, setSelectedFolderId] = useState(savedView.selectedFolderId ?? null)
  const [dragId, setDragId] = useState(null)
  const [dragOverId, setDragOverId] = useState(null)
  const [dragPos, setDragPos] = useState(null)
  const [reqTab, setReqTab] = useState(savedView.reqTab || 'params')
  const [headerBulk, setHeaderBulk] = useState(null)  // 非 null = 批量编辑中，存的是文本框原文
  const [respTab, setRespTab] = useState('body')
  const [importOpen, setImportOpen] = useState(false)
  const [importCurl, setImportCurl] = useState('')
  const [codeOpen, setCodeOpen] = useState(false)
  const [codeLang, setCodeLang] = useState('curl')
  const [navCollapsed, setNavCollapsed] = useState(!!savedView.navCollapsed)
  const saveTimerRef = useRef({})
  const tabsRef = useRef(openTabs)
  tabsRef.current = openTabs

  const fetchItems = useCallback(async () => { try { const r = await api.get('/http-client/requests'); setItems(r.data || []) } catch {} }, [])
  const fetchHistory = async () => { try { const r = await api.get('/http-client/history?limit=100'); setHistory(r.data?.data || r.data || []) } catch {} }
  useEffect(() => { fetchItems(); fetchHistory() }, [])

  // 请求列表第一次拿到后，把上次开着的标签页按原顺序重新打开（已被删掉的跳过）
  useEffect(() => {
    if (viewRestored.current || !items.length) return
    viewRestored.current = true
    const byId = Object.fromEntries(items.map(i => [i.id, i]))
    const tabs = (savedView.tabIds || []).map(id => byId[id]).filter(i => i && i.type !== 'folder')
      .map(tabFromItem)
    if (tabs.length) {
      setOpenTabs(tabs)
      setActiveTabId(tabs.some(t => t.id === savedView.activeId) ? savedView.activeId : tabs[tabs.length - 1].id)
      const runs = Object.fromEntries(Object.entries(savedView.runs || {}).filter(([tid]) => tabs.some(t => t.id === tid)))
      if (Object.keys(runs).length) setBatchRuns(runs)
    }
  }, [items, savedView])
  useEffect(() => {
    if (!viewRestored.current) return
    try {
      localStorage.setItem(viewKey(), JSON.stringify({
        tabIds: openTabs.map(t => t.id), activeId: activeTabId, expanded: expandedFolders, selectedFolderId,
        showHistory, reqTab, navCollapsed,
        timeout: requestTimeout, count: sendCount, perRound: sendPerRound, interval: sendInterval,
        runs: batchRuns,   // 刷新时还在发的，刷新后接着看进度
      }))
    } catch { /* 存不下就算了，不影响使用 */ }
  }, [openTabs, activeTabId, expandedFolders, selectedFolderId, showHistory, reqTab, navCollapsed, requestTimeout, sendCount, sendPerRound, sendInterval, batchRuns])

  const activeItem = openTabs.find(t => t.id === activeTabId) || null
  // 切到别的请求就退出批量编辑，否则文本框里还是上一个请求的内容
  useEffect(() => { setHeaderBulk(null) }, [activeTabId])
  const updateTabField = (id, field, value) => {
    setOpenTabs(tabs => tabs.map(t => t.id === id ? { ...t, [field]: value, _dirty: true } : t))
    if (saveTimerRef.current[id]) clearTimeout(saveTimerRef.current[id])
    saveTimerRef.current[id] = setTimeout(() => autoSave(id), 800)
  }
  // 改 MCP 设置：同时重新生成请求体、补齐两个必需的请求头（「会话」只影响怎么发，不动请求体）
  const updateMcp = (tab, patch) => {
    const cfg = { ...MCP_DEFAULT, ...(tab.mcpConfig || {}), ...patch }
    updateTabField(tab.id, 'mcpConfig', cfg)
    if (!('session' in patch && Object.keys(patch).length === 1)) {
      updateTabField(tab.id, 'body', buildMcpBody(cfg))
      updateTabField(tab.id, 'headers', ensureMcpHeaders(tab.headers))
    }
  }
  const autoSave = async (id) => {
    const tab = tabsRef.current.find(t => t.id === id)
    if (!tab || !tab._dirty) return
    try {
      await api.put(`/http-client/requests/${id}`, { name: tab.name, method: tab.method, url: tab.url, headers: tab.headers, body: tab.body, body_type: tab.bodyType, ...(tab.kind === 'mcp' ? { mcp_config: tab.mcpConfig } : {}) })
      setOpenTabs(tabs => tabs.map(t => t.id === id ? { ...t, _dirty: false } : t))
      fetchItems()
    } catch {}
  }
  const openRequest = (item) => {
    if (item.type === 'folder') return
    if (openTabs.find(t => t.id === item.id)) { setActiveTabId(item.id); return }
    setOpenTabs(tabs => [...tabs, tabFromItem(item)])
    setActiveTabId(item.id)
  }
  const closeTab = (id) => {
    const tab = openTabs.find(t => t.id === id)
    if (tab?._dirty) autoSave(id)
    const remaining = openTabs.filter(t => t.id !== id)
    setOpenTabs(remaining)
    if (activeTabId === id) setActiveTabId(remaining.length ? remaining[remaining.length - 1].id : null)
  }
  const handleCreate = async (type, parentId, kind = 'http') => {
    try {
      if (type === 'request' && kind === 'mcp') {
        const r = await api.post('/http-client/requests', { type, name: '新 MCP 请求', method: 'POST', kind: 'mcp', mcp_config: MCP_DEFAULT, parent_id: parentId || null })
        const d = r.data || r
        const extra = { headers: MCP_HEADERS, body: buildMcpBody(MCP_DEFAULT), bodyType: 'json' }
        await api.put(`/http-client/requests/${d.id}`, { headers: extra.headers, body: extra.body, body_type: 'json' })
        await fetchItems()
        openRequest({ ...d, ...extra, mcpConfig: MCP_DEFAULT })
        return
      }
      const r = await api.post('/http-client/requests', { type, name: type === 'folder' ? '新文件夹' : '新请求', parent_id: parentId || null })
      await fetchItems()
      if (type === 'request') openRequest(r.data || r)
      if (type === 'folder') { setEditingNameId((r.data || r).id); setEditingNameValue('新文件夹') }
    } catch {}
  }
  const handleRename = async (id, name) => {
    if (!name.trim()) { setEditingNameId(null); return }
    try { await api.put(`/http-client/requests/${id}`, { name: name.trim() }); setEditingNameId(null); fetchItems(); setOpenTabs(tabs => tabs.map(t => t.id === id ? { ...t, name: name.trim() } : t)) } catch {}
  }
  const handleDelete = async (id) => {
    try {
      const item = items.find(i => i.id === id)
      if (item?.type === 'folder') {
        // 目录有两层，子目录里的请求也会被后端一起删掉 —— 标签页不跟着关就会留下一个 id 已经不存在的 tab，
        // 之后自动保存会 404 在一个看不出来源的地方
        const kids = items.filter(i => i.parentId === id)
        const grandKids = items.filter(i => kids.some(k => k.id === i.parentId))
        ;[...kids, ...grandKids].forEach(c => closeTab(c.id))
      }
      await api.delete(`/http-client/requests/${id}`)
      message.success('已删除')
      closeTab(id)
      fetchItems()
    } catch {}
  }
  const handleMove = async (id, targetFolderId) => {
    try {
      await api.put(`/http-client/requests/${id}`, { parent_id: targetFolderId || '' })
      message.success('已移动')
      fetchItems()
    } catch {}
  }
  // 目录只有两层：一级目录 → 子目录 → 请求。往目录里放目录之前先过这一关，返回非空就是不能放
  const folderNestError = (folder, parent) => {
    if (!parent) return null
    if (parent.parentId) return '目录最多两层，子目录里不能再放目录'
    if (items.some(i => i.type === 'folder' && i.parentId === folder.id)) return '这个目录下还有子目录，挪进去会超过两层'
    return null
  }
  const handleDragDrop = async (srcId, targetId, pos) => {
    if (!srcId || srcId === targetId) return
    const src = items.find(i => i.id === srcId)
    const target = items.find(i => i.id === targetId)
    if (!src || !target) return
    const srcIsFolder = src.type === 'folder'
    const targetIsFolder = target.type === 'folder'

    // 落在目录中间 = 放进这个目录
    if (targetIsFolder && pos === 'inside') {
      if (srcIsFolder) {
        const err = folderNestError(src, target)
        if (err) { message.warning(err); return }
        if (src.parentId === targetId) return
      }
      await handleMove(srcId, targetId)
      setExpandedFolders(s => ({ ...s, [targetId]: true }))
      return
    }

    // 落在上下边缘 = 和 target 做同级。目录和请求各排一串（树上目录也是先渲染的），
    // 拖到另一类旁边就落到本类末尾，别混进对方的序里
    const newParentId = target.parentId || null
    if (srcIsFolder && newParentId) {
      const err = folderNestError(src, items.find(i => i.id === newParentId))
      if (err) { message.warning(err); return }
    }
    const siblings = items.filter(i => {
      if (i.id === srcId) return false
      if ((i.type === 'folder') !== srcIsFolder) return false
      return newParentId ? i.parentId === newParentId : !i.parentId
    })
    let insertIdx = siblings.length
    if (targetIsFolder === srcIsFolder) {
      const targetIdx = siblings.findIndex(i => i.id === targetId)
      if (targetIdx < 0) return
      insertIdx = pos === 'below' ? targetIdx + 1 : targetIdx
    }
    siblings.splice(insertIdx, 0, src)
    const sortItems = siblings.map((s, idx) => ({
      id: s.id,
      sort_order: (idx + 1) * 100,
      parent_id: newParentId || ''
    }))
    try {
      await api.post('/http-client/requests/batch-sort', { items: sortItems })
      fetchItems()
    } catch {}
  }
  const handleImportCurl = async () => {
    if (!importCurl.trim()) return
    const parsed = parseCurl(importCurl)
    try {
      const name = parsed.url ? (new URL(parsed.url)).pathname.split('/').filter(Boolean).pop() || '导入的请求' : '导入的请求'
      const r = await api.post('/http-client/requests', { type: 'request', name, method: parsed.method, url: parsed.url, parent_id: selectedFolderId || null })
      const d = r.data || r
      await api.put(`/http-client/requests/${d.id}`, { headers: parsed.headers, body: parsed.body || null })
      await fetchItems(); openRequest({ ...d, ...parsed, name }); setImportOpen(false); setImportCurl(''); message.success('已导入')
    } catch { message.error('导入失败') }
  }
  const handleDuplicateItem = async (item) => {
    try {
      const r = await api.post('/http-client/requests', { type: item.type, name: item.name + ' 副本', method: item.method, url: item.url, kind: item.kind || 'http', mcp_config: item.mcpConfig || null, parent_id: item.parentId || null })
      const d = r.data || r
      if (item.type === 'folder') {
        const copyRequestInto = async (parentId, req) => {
          const cr = await api.post('/http-client/requests', { type: 'request', name: req.name, method: req.method, url: req.url, kind: req.kind || 'http', mcp_config: req.mcpConfig || null, parent_id: parentId })
          const cd = cr.data || cr
          await api.put(`/http-client/requests/${cd.id}`, { headers: req.headers, body: req.body, body_type: req.bodyType })
        }
        for (const child of items.filter(i => i.parentId === item.id)) {
          if (child.type === 'folder') {
            // 只复制到第二层就停 —— 树本来就只有两层，再往下递归只会造出后端拒收的第三层
            const sr = await api.post('/http-client/requests', { type: 'folder', name: child.name, parent_id: d.id })
            const sd = sr.data || sr
            for (const g of items.filter(i => i.parentId === child.id && i.type === 'request')) await copyRequestInto(sd.id, g)
          } else {
            await copyRequestInto(d.id, child)
          }
        }
      } else {
        await api.put(`/http-client/requests/${d.id}`, { headers: item.headers, body: item.body, body_type: item.bodyType })
      }
      await fetchItems()
      if (item.type !== 'folder') openRequest({ ...d, headers: item.headers, body: item.body, bodyType: item.bodyType })
      else setExpandedFolders(s => ({ ...s, [d.id]: true }))
      message.success('已复制')
    } catch {}
  }
  const handleDuplicate = async () => {
    if (!activeItem) return
    const original = items.find(i => i.id === activeItem.id)
    handleDuplicateItem({ ...activeItem, parentId: original?.parentId || null })
  }
  const handleSend = async () => {
    if (!activeItem) return
    const id = activeItem.id; await autoSave(id)
    setSending(s => ({ ...s, [id]: true })); setResponses(r => ({ ...r, [id]: null })); setRespTab('body')
    try {
      let finalUrl = activeItem.url.trim()
      if (!finalUrl) { message.warning('请输入 URL'); setSending(s => ({ ...s, [id]: false })); return }
      if (!/^https?:\/\//i.test(finalUrl)) finalUrl = 'http://' + finalUrl
      const hdrs = {}; (activeItem.headers || []).forEach(h => { if (h.enabled !== false && h.key?.trim()) hdrs[h.key.trim()] = h.value || '' })
      const payload = { method: activeItem.method, url: finalUrl, headers: Object.keys(hdrs).length ? hdrs : null, body: ['POST','PUT','PATCH'].includes(activeItem.method) && activeItem.body?.trim() ? activeItem.body.trim() : null, timeout: requestTimeout, count: sendCount, perRound: sendPerRound, interval: sendInterval || null, name: activeItem.name, ...(activeItem.kind === 'mcp' ? { mcp: { session: activeItem.mcpConfig?.session || 'none' } } : {}) }
      if (sendCount > 1) {
        // 发多次：后台发，页面每隔一会儿问一次进度 —— 每条回来就能看到，不用等全部发完
        const r = await api.post('/http-client/send-batch', payload)
        const runId = r.data?.runId
        if (!runId) { setResponses(rs => ({ ...rs, [id]: { error: r.error || '没能开始发送' } })); setSending(s => ({ ...s, [id]: false })); return }
        setBatchRuns(m => ({ ...m, [id]: runId }))
        return  // 「发送中」由轮询结束时收起
      }
      const res = await api.post('/http-client/send', payload)
      const d = res.data?.data || res.data || res
      if (d.error || res.error) { setResponses(r => ({ ...r, [id]: { error: d.error || res.error, batch: d.batch } })); fetchHistory(); return }
      setResponses(r => ({ ...r, [id]: d })); fetchHistory()
      setSending(s => ({ ...s, [id]: false }))
    } catch (e) { setResponses(r => ({ ...r, [id]: { error: e.message } })); setSending(s => ({ ...s, [id]: false })) }
  }
  const stopBatch = async (id) => {
    const runId = batchRuns[id]
    if (runId) { try { await api.post(`/http-client/send-batch/${runId}/stop`) } catch { /* 已提示 */ } }
  }
  // 批量发送进度：每 0.7 秒问一次，发完（或停了）就收起「发送中」并刷新发送记录
  useEffect(() => {
    const entries = Object.entries(batchRuns)
    if (!entries.length) return
    let stopped = false
    const tick = async () => {
      for (const [tabId, runId] of entries) {
        try {
          const r = await api.get(`/http-client/send-batch/${runId}`, { silent: true })
          if (stopped) return
          const d = r.data || {}
          setResponses(rs => ({ ...rs, [tabId]: d }))
          if (!d.batch?.running) {
            setBatchRuns(m => { const n = { ...m }; delete n[tabId]; return n })
            setSending(s => ({ ...s, [tabId]: false }))
            fetchHistory()
          } else setSending(s => (s[tabId] ? s : { ...s, [tabId]: true }))
        } catch (e) {
          if (stopped) return
          setResponses(rs => ({ ...rs, [tabId]: { error: e.message } }))
          setBatchRuns(m => { const n = { ...m }; delete n[tabId]; return n })
          setSending(s => ({ ...s, [tabId]: false }))
        }
      }
    }
    tick()
    const t = setInterval(tick, 700)
    return () => { stopped = true; clearInterval(t) }
  }, [batchRuns])

  // 解析 URL 参数
  const urlParams = activeItem ? (() => {
    try { const u = new URL(activeItem.url.startsWith('http') ? activeItem.url : 'http://x' + activeItem.url); return [...u.searchParams.entries()].map(([k, v]) => ({ key: k, value: v })) } catch { return [] }
  })() : []

  const folders = items.filter(d => d.type === 'folder')
  const requests = items.filter(d => d.type === 'request')
  const resp = activeItem ? responses[activeItem.id] : null
  const isSending = activeItem ? sending[activeItem.id] : false
  const rootRequests = requests.filter(r => !r.parentId)
  const rootFolders = folders.filter(f => !f.parentId)
  // 移动菜单的候选要带路径：两个一级目录下各有一个「上游」子目录时，光看名字在菜单里分不出来
  const folderOptions = rootFolders.flatMap(f => [
    { id: f.id, label: f.name },
    ...folders.filter(s => s.parentId === f.id).map(s => ({ id: s.id, label: `${f.name} / ${s.name}` })),
  ])
  const moveTargets = (item) => {
    const out = item.parentId ? [{ id: null, label: '根目录' }] : []
    folderOptions.forEach(o => {
      if (o.id === item.id || o.id === item.parentId) return
      if (item.type === 'folder' && folderNestError(item, folders.find(f => f.id === o.id))) return
      out.push(o)
    })
    return out
  }

  // ── 文本域搜索：请求 Body + 响应 Body，忽略大小写的包含匹配 ──
  const respBodyText = useMemo(() => {
    if (!resp || resp.error || resp.body == null) return ''
    try { return JSON.stringify(JSON.parse(resp.body), null, 2) } catch { return resp.body }
  }, [resp])
  const bodySearch = useTextSearch(activeItem?.body || '')
  const respSearch = useTextSearch(respBodyText)
  const bodyHasSearch = !!bodySearch.keyword && bodySearch.total > 0
  const bodyRef = useRef(null)
  const bodyBackdropRef = useRef(null)
  const bodyInnerRef = useRef(null)
  const bodyBgRef = useRef(null)
  const bodyMarkRef = useRef(null)
  const respScrollRef = useRef(null)
  const respMarkRef = useRef(null)
  const getBodyEl = () => bodyRef.current?.resizableTextArea?.textArea || null

  // 高亮层要和 textarea 逐像素对齐，度量直接从真实元素上抄，别猜 antd 的 token
  useEffect(() => {
    const el = getBodyEl()
    if (!el) return
    const cs = getComputedStyle(el)
    // 搜索期间 textarea 自身要透明（不然会挡住底下的高亮），先把原背景记下来照搬给高亮层
    if (!bodyHasSearch) { bodyBgRef.current = cs.backgroundColor; return }
    const bd = bodyBackdropRef.current, inner = bodyInnerRef.current
    if (!bd || !inner) return
    Object.assign(bd.style, {
      borderTopWidth: cs.borderTopWidth, borderRightWidth: cs.borderRightWidth,
      borderBottomWidth: cs.borderBottomWidth, borderLeftWidth: cs.borderLeftWidth,
      borderRadius: cs.borderRadius, background: bodyBgRef.current || '#fff',
    })
    Object.assign(inner.style, {
      paddingTop: cs.paddingTop, paddingRight: cs.paddingRight, paddingBottom: cs.paddingBottom, paddingLeft: cs.paddingLeft,
      fontFamily: cs.fontFamily, fontSize: cs.fontSize, fontWeight: cs.fontWeight, fontStyle: cs.fontStyle,
      lineHeight: cs.lineHeight, letterSpacing: cs.letterSpacing, tabSize: cs.tabSize,
      whiteSpace: cs.whiteSpace, overflowWrap: cs.overflowWrap, wordBreak: cs.wordBreak,
      transform: `translate(${-el.scrollLeft}px, ${-el.scrollTop}px)`,  // 开搜时可能已经是滚着的
    })
  }, [bodyHasSearch, activeTabId, reqTab])

  // 用 transform 平移内容层跟随滚动：高亮层自己的 scrollTop 会被它的内容高度夹住，对不齐
  const syncBodyBackdrop = () => {
    const el = getBodyEl(), inner = bodyInnerRef.current
    if (el && inner) inner.style.transform = `translate(${-el.scrollLeft}px, ${-el.scrollTop}px)`
  }

  // 跳转到当前命中：滚 textarea，高亮层跟着滚，顺手把光标放过去（不抢焦点，搜索框还能继续打字）
  useEffect(() => {
    const el = getBodyEl()
    if (!el || !bodySearch.active) return
    scrollMatchIntoView(el, bodyMarkRef.current)
    syncBodyBackdrop()
    try { el.setSelectionRange(bodySearch.active[0], bodySearch.active[1]) } catch {}
  }, [bodySearch.active])

  useEffect(() => {
    if (respSearch.active) scrollMatchIntoView(respScrollRef.current, respMarkRef.current)
  }, [respSearch.active])

  const renderTreeItem = (item, depth = 0) => {
    const isFolder = item.type === 'folder'
    const children = isFolder
      ? [...folders.filter(f => f.parentId === item.id), ...requests.filter(r => r.parentId === item.id)]
      : []
    const isEditing = editingNameId === item.id
    const isActive = !isFolder && activeTabId === item.id
    const isHover = hoverItemId === item.id || popupItemId === item.id
    const isExpanded = expandedFolders[item.id] === true  // 默认收起，点开才展开
    const isDragOver = dragOverId === item.id
    const showAbove = isDragOver && dragPos === 'above'
    const showBelow = isDragOver && dragPos === 'below'
    const showInside = isDragOver && dragPos === 'inside'

    const getDragPos = (e, el) => {
      const rect = el.getBoundingClientRect()
      const y = e.clientY - rect.top
      const ratio = y / rect.height
      if (isFolder) {
        if (ratio < 0.25) return 'above'
        if (ratio > 0.75) return 'below'
        return 'inside'
      }
      return ratio < 0.5 ? 'above' : 'below'
    }

    return (
      <div key={item.id}>
        <div draggable={!isEditing}
          onDragStart={e => { setDragId(item.id); e.dataTransfer.effectAllowed = 'move' }}
          onDragEnd={() => { setDragId(null); setDragOverId(null); setDragPos(null) }}
          onDragOver={e => { if (dragId && dragId !== item.id) { e.preventDefault(); const p = getDragPos(e, e.currentTarget); setDragOverId(item.id); setDragPos(p) } }}
          onDragLeave={() => { if (dragOverId === item.id) { setDragOverId(null); setDragPos(null) } }}
          onDrop={async e => {
            e.preventDefault(); e.stopPropagation()
            const pos = dragPos
            setDragOverId(null); setDragPos(null)
            if (dragId && dragId !== item.id) {
              await handleDragDrop(dragId, item.id, pos)
              setDragId(null)
            }
          }}
          onClick={() => { if (isFolder) { setExpandedFolders(s => ({ ...s, [item.id]: !isExpanded })); setSelectedFolderId(item.id) } else openRequest(item) }}
          onMouseEnter={() => setHoverItemId(item.id)}
          onMouseLeave={() => { if (popupItemId !== item.id) setHoverItemId(null) }}
          style={{
            padding: `4px 8px 4px ${10 + depth * 16}px`, cursor: dragId ? 'grabbing' : 'pointer',
            display: 'flex', alignItems: 'center', gap: 5, fontSize: 12,
            background: showInside ? 'rgba(250,173,20,0.06)' : isActive ? 'rgba(14,165,160,0.06)' : 'transparent',
            borderLeft: isActive ? '3px solid #0ea5a0' : '3px solid transparent',
            borderTop: showAbove ? '2px solid #0ea5a0' : '2px solid transparent',
            borderBottom: showBelow ? '2px solid #0ea5a0' : showInside ? '1px dashed #ff7d00' : '2px solid transparent',
            borderRight: showInside ? '1px dashed #ff7d00' : 'none',
            borderRadius: showInside ? 4 : 0, opacity: dragId === item.id ? 0.4 : 1,
            marginTop: showAbove ? -2 : 0, marginBottom: showBelow ? -2 : 0,
          }}>
          {isFolder && <span style={{ fontSize: 11, color: '#c9cdd4', width: 12, textAlign: 'center' }}>{isExpanded ? '▾' : '▸'}</span>}
          {isFolder && <FolderOutlined style={{ color: '#ff7d00', fontSize: 12 }} />}
          {!isFolder && <Tag color={MC[item.kind === 'mcp' ? 'MCP' : item.method]} style={{ margin: 0, fontSize: 11, lineHeight: '14px', padding: '0 3px', borderRadius: 6 }}>{item.kind === 'mcp' ? 'MCP' : item.method}</Tag>}
          {isEditing ? (
            <Input size="small" autoFocus value={editingNameValue} onChange={e => setEditingNameValue(e.target.value)}
              onBlur={() => handleRename(item.id, editingNameValue)} onPressEnter={() => handleRename(item.id, editingNameValue)}
              style={{ flex: 1, fontSize: 12, height: 22 }} onClick={e => e.stopPropagation()} />
          ) : <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: isFolder ? '#1d2129' : '#4e5969', fontWeight: isFolder ? 500 : 400 }}>{item.name}</span>}
          {isHover && !isEditing && (
            <span style={{ display: 'flex', gap: 2, flexShrink: 0 }} onClick={e => e.stopPropagation()}>
              {isFolder && (
                <Dropdown trigger={['click']} menu={{ items: [
                  { key: 'http', label: 'HTTP 请求', onClick: () => { handleCreate('request', item.id); setExpandedFolders(s => ({ ...s, [item.id]: true })) } },
                  { key: 'mcp', label: 'MCP 请求', onClick: () => { handleCreate('request', item.id, 'mcp'); setExpandedFolders(s => ({ ...s, [item.id]: true })) } },
                ] }}>
                  <Tooltip title="新建请求"><PlusOutlined style={{ fontSize: 11, color: '#86909c', padding: 2 }} onClick={e => e.stopPropagation()} /></Tooltip>
                </Dropdown>
              )}
              {/* 子目录里不给这个入口 —— 目录一共两层 */}
              {isFolder && !item.parentId && <Tooltip title="新建子目录"><FolderAddOutlined style={{ fontSize: 11, color: '#86909c', padding: 2 }} onClick={() => { handleCreate('folder', item.id); setExpandedFolders(s => ({ ...s, [item.id]: true })) }} /></Tooltip>}
              {moveTargets(item).length > 0 && (
                <Dropdown
                  onOpenChange={open => { if (open) setPopupItemId(item.id); else { setPopupItemId(null); setHoverItemId(null) } }}
                  menu={{ items: moveTargets(item).map(t => ({ key: t.id || '__root', label: t.label, onClick: () => handleMove(item.id, t.id) })) }}
                  trigger={['click']}
                >
                  <Tooltip title="移动"><FolderOutlined style={{ fontSize: 11, color: '#86909c', padding: 2 }} /></Tooltip>
                </Dropdown>
              )}
              <Tooltip title="复制"><CopyOutlined style={{ fontSize: 11, color: '#86909c', padding: 2 }} onClick={() => handleDuplicateItem(item)} /></Tooltip>
              <Tooltip title="重命名"><EditOutlined style={{ fontSize: 11, color: '#86909c', padding: 2 }} onClick={() => { setEditingNameId(item.id); setEditingNameValue(item.name) }} /></Tooltip>
              <Popconfirm
                title="删除？"
                onConfirm={() => handleDelete(item.id)}
                onOpenChange={open => { if (open) setPopupItemId(item.id); else { setPopupItemId(null); setHoverItemId(null) } }}
                placement="right"
              >
                <DeleteOutlined style={{ fontSize: 11, color: '#c9cdd4', padding: 2 }} />
              </Popconfirm>
            </span>
          )}
        </div>
        {isFolder && isExpanded && children.map(c => renderTreeItem(c, depth + 1))}
        {isFolder && isExpanded && children.length === 0 && (
          <div style={{ padding: `3px 8px 3px ${26 + depth * 16}px`, fontSize: 11, color: '#c9cdd4', display: 'flex', gap: 10 }}>
            <span style={{ cursor: 'pointer' }} onClick={() => handleCreate('request', item.id)}>+ 新建请求</span>
            <span style={{ cursor: 'pointer' }} onClick={() => handleCreate('request', item.id, 'mcp')}>+ MCP 请求</span>
            {!item.parentId && <span style={{ cursor: 'pointer' }} onClick={() => handleCreate('folder', item.id)}>+ 新建子目录</span>}
          </div>
        )}
      </div>
    )
  }

  const moreMenu = activeItem ? [
    { key: 'curl', label: '复制为 cURL', icon: <CopyOutlined />, onClick: () => { copyToClipboard(toCurl(activeItem)); message.success('已复制') } },
    { key: 'python', label: '复制为 Python', icon: <CodeOutlined />, onClick: () => { copyToClipboard(toCode(activeItem, 'python')); message.success('已复制') } },
    { key: 'js', label: '复制为 JavaScript', icon: <CodeOutlined />, onClick: () => { copyToClipboard(toCode(activeItem, 'javascript')); message.success('已复制') } },
    { type: 'divider' },
    { key: 'dup', label: '复制请求', icon: <CopyOutlined />, onClick: handleDuplicate },
    { key: 'code', label: '查看代码', icon: <CodeOutlined />, onClick: () => setCodeOpen(true) },
  ] : []

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 70px)', background: 'transparent' }}>
      {/* 顶栏 */}
      <div className="lum-page-strip" style={{ padding: '8px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <SendOutlined style={{ fontSize: 18, color: '#0ea5a0' }} />
          <span style={{ fontWeight: 600, fontSize: 16 }}>HTTP 请求</span>
          <span style={{ fontSize: 12, color: '#86909c' }}>{requests.length} 个请求</span>
        </div>
        <Space size={8}>
          <Button size="small" icon={<ImportOutlined />} onClick={() => setImportOpen(true)}>导入 cURL</Button>
          <Button size="small" icon={<ClockCircleOutlined />} type={showHistory ? 'primary' : 'default'} onClick={() => { setShowHistory(!showHistory); if (!showHistory) fetchHistory() }}>发送记录</Button>
        </Space>
      </div>

      <div style={{ flex: 1, display: 'flex', minHeight: 0, padding: 10, gap: 10 }}>
        {/* 左栏 */}
        {navCollapsed ? (
          <Tooltip title="展开导航" placement="right">
            <div
              onClick={() => setNavCollapsed(false)}
              style={{
                width: 20, flexShrink: 0, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: 'rgba(255,255,255,0.35)', borderRadius: '12px 0 0 12px', transition: 'background 0.2s',
              }}
              onMouseEnter={e => e.currentTarget.style.background = 'rgba(14,165,160,0.08)'}
              onMouseLeave={e => e.currentTarget.style.background = 'rgba(255,255,255,0.35)'}
            >
              <MenuUnfoldOutlined style={{ fontSize: 11, color: '#86909c' }} />
            </div>
          </Tooltip>
        ) : (
          <div style={{ width: 240, flexShrink: 0, background: 'var(--panel-bg)', backdropFilter: 'blur(16px)', borderRadius: 16, display: 'flex', flexDirection: 'column', boxShadow: '0 2px 12px rgba(0,0,0,0.04)' }}>
            <div style={{ padding: '6px 8px', borderBottom: '1px solid rgba(0,0,0,0.04)', display: 'flex', gap: 4, alignItems: 'center' }}>
              <Dropdown trigger={['click']} menu={{ items: [
                { key: 'http', label: 'HTTP 请求', onClick: () => handleCreate('request', selectedFolderId) },
                { key: 'mcp', label: 'MCP 请求', onClick: () => handleCreate('request', selectedFolderId, 'mcp') },
              ] }}>
                <Button type="primary" size="small" icon={<FileAddOutlined />} style={{ flex: 1 }}>新建请求</Button>
              </Dropdown>
              <Button size="small" icon={<FolderAddOutlined />} onClick={() => handleCreate('folder')}>文件夹</Button>
              <Tooltip title="收起导航">
                <Button type="text" size="small" icon={<MenuFoldOutlined />} onClick={() => setNavCollapsed(true)} style={{ color: '#c9cdd4', flexShrink: 0 }} />
              </Tooltip>
            </div>
            <div style={{ flex: 1, overflow: 'auto' }}
              onDragOver={e => { if (dragId) e.preventDefault() }}
              onDrop={async e => { e.preventDefault(); if (dragId) { await handleMove(dragId, null); setDragId(null); setDragOverId(null); setDragPos(null) } }}>
              {rootFolders.map(f => renderTreeItem(f))}
              {rootRequests.map(r => renderTreeItem(r))}
              {items.length === 0 && <div style={{ textAlign: 'center', padding: 30, color: '#c9cdd4', fontSize: 12 }}>点击上方新建</div>}
            </div>
          </div>
        )}

        {/* 右栏 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: 'var(--panel-bg)', backdropFilter: 'blur(12px)', borderRadius: 16, boxShadow: '0 2px 12px rgba(0,0,0,0.04)', overflow: 'hidden' }}>
          {/* Tab 栏 */}
          {openTabs.length > 0 && (
            <div style={{ display: 'flex', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0, overflow: 'auto' }}>
              {openTabs.map(t => (
                <div key={t.id} onClick={() => setActiveTabId(t.id)} style={{
                  padding: '6px 10px', cursor: 'pointer', fontSize: 12, display: 'flex', alignItems: 'center', gap: 5,
                  borderBottom: activeTabId === t.id ? '2px solid #0ea5a0' : '2px solid transparent',
                  color: activeTabId === t.id ? '#0ea5a0' : '#4e5969', fontWeight: activeTabId === t.id ? 600 : 400,
                  whiteSpace: 'nowrap', background: activeTabId === t.id ? 'rgba(230,81,0,0.05)' : 'transparent',
                }}>
                  <Tag color={MC[t.kind === 'mcp' ? 'MCP' : t.method]} style={{ margin: 0, fontSize: 11, lineHeight: '14px', padding: '0 3px', borderRadius: 6 }}>{t.kind === 'mcp' ? 'MCP' : t.method}</Tag>
                  <span style={{ maxWidth: 100, overflow: 'hidden', textOverflow: 'ellipsis' }}>{t.name}</span>
                  {t._dirty && <span style={{ color: '#ff7d00', fontSize: 11 }}>●</span>}
                  <span onClick={e => { e.stopPropagation(); closeTab(t.id) }} style={{ marginLeft: 2, color: '#c9cdd4', fontSize: 11, cursor: 'pointer' }}>×</span>
                </div>
              ))}
            </div>
          )}

          {activeItem ? (
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              {/* 请求名 + URL */}
              <div style={{ padding: '8px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0 }}>
                <Input value={activeItem.name} onChange={e => updateTabField(activeItem.id, 'name', e.target.value)}
                  variant="borderless" style={{ fontSize: 14, fontWeight: 600, padding: '0 0 4px 0', color: '#1d2129' }} />
                <div style={{ display: 'flex', gap: 8 }}>
                  {activeItem.kind === 'mcp' ? (
                    <Tooltip title="MCP 请求固定用 POST 发"><div style={{ width: 110, textAlign: 'center', color: MC.MCP, fontWeight: 600, fontFamily: MONO, fontSize: 13, lineHeight: '24px', border: '1px solid rgba(124,92,191,0.3)', borderRadius: 6, background: 'rgba(124,92,191,0.06)' }}>MCP</div></Tooltip>
                  ) : (
                  <Select value={activeItem.method} onChange={v => updateTabField(activeItem.id, 'method', v)} style={{ width: 110 }} size="small"
                    options={['GET','POST','PUT','DELETE','PATCH','HEAD','OPTIONS'].map(m => ({ value: m, label: <span style={{ color: MC[m], fontWeight: 600, fontFamily: MONO }}>{m}</span> }))} />
                  )}
                  <Input spellCheck={false} value={activeItem.url} onChange={e => updateTabField(activeItem.id, 'url', e.target.value)}
                    placeholder={activeItem.kind === 'mcp' ? 'MCP 服务地址，例如 http://host:port/mcp' : '输入请求 URL'} style={{ flex: 1, fontFamily: MONO, fontSize: 13 }} onPressEnter={handleSend} size="small" />
                  <Button size="small" disabled={!activeItem._dirty} onClick={() => autoSave(activeItem.id)} icon={<SaveOutlined />}>
                    {activeItem._dirty ? '保存' : '已保存'}
                  </Button>
                  <Dropdown menu={{ items: moreMenu }} trigger={['click']}><Button size="small">更多</Button></Dropdown>
                  <Tooltip title="请求超时时间（秒），范围 1-600">
                    <InputNumber value={requestTimeout} onChange={v => setRequestTimeout(v || 120)}
                      min={1} max={600} size="small" style={{ width: 80 }}
                      addonAfter="s" />
                  </Tooltip>
                  {batchRuns[activeItem.id] ? (
                    <Button danger size="small" icon={<StopOutlined />} onClick={() => stopBatch(activeItem.id)}
                      style={{ paddingLeft: 16, paddingRight: 16 }}>停止</Button>
                  ) : (
                  <Button type="primary" icon={isSending ? <LoadingOutlined /> : <SendOutlined />} loading={isSending} onClick={handleSend} size="small"
                    style={{ background: '#0ea5a0', borderColor: '#0ea5a0', paddingLeft: 16, paddingRight: 16 }}>发送</Button>
                  )}
                </div>
                {activeItem.kind === 'mcp' && (() => {
                  const cfg = { ...MCP_DEFAULT, ...(activeItem.mcpConfig || {}) }
                  const argsBad = ['tools/call', 'prompts/get'].includes(cfg.method) && parseArgs(cfg.arguments) === null
                  const lbl = { whiteSpace: 'nowrap' }
                  return (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6, fontSize: 12, color: '#4e5969', flexWrap: 'wrap' }}>
                      <span style={lbl}>调用方式</span>
                      <Select size="small" style={{ width: 210 }} value={cfg.method} options={MCP_METHODS} onChange={v => updateMcp(activeItem, { method: v })} />
                      {cfg.method === 'tools/call' && (<>
                        <span style={lbl}>工具名</span>
                        <Input size="small" spellCheck={false} style={{ width: 180, fontFamily: MONO }} placeholder="例如 get_weather"
                          value={cfg.toolName} onChange={e => updateMcp(activeItem, { toolName: e.target.value })} />
                      </>)}
                      {cfg.method === 'prompts/get' && (<>
                        <span style={lbl}>提示词名</span>
                        <Input size="small" spellCheck={false} style={{ width: 160, fontFamily: MONO }}
                          value={cfg.promptName} onChange={e => updateMcp(activeItem, { promptName: e.target.value })} />
                      </>)}
                      {cfg.method === 'resources/read' && (<>
                        <span style={lbl}>资源地址</span>
                        <Input size="small" spellCheck={false} style={{ width: 240, fontFamily: MONO }} placeholder="例如 file:///readme.md"
                          value={cfg.uri} onChange={e => updateMcp(activeItem, { uri: e.target.value })} />
                      </>)}
                      {['tools/call', 'prompts/get'].includes(cfg.method) && (<>
                        <Tooltip title='JSON 对象，例如 {"city": "北京"}；不需要参数就留 {}'><span style={lbl}>参数</span></Tooltip>
                        <Input size="small" spellCheck={false} style={{ width: 220, fontFamily: MONO }} status={argsBad ? 'error' : undefined}
                          value={cfg.arguments} onChange={e => updateMcp(activeItem, { arguments: e.target.value })} />
                        {argsBad && <span style={{ color: '#e8453c' }}>参数不是合法的 JSON 对象</span>}
                      </>)}
                    </div>
                  )
                })()}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6, fontSize: 12, color: '#4e5969' }}>
                  {activeItem.kind === 'mcp' && (<>
                    {/* 会话决定「怎么发」，和共发几次放一排 */}
                    <span style={{ whiteSpace: 'nowrap' }}>会话</span>
                    <Tooltip title="不打招呼：不带会话号，要求会话号的服务会直接拒绝；整批同一会话：点一次「发送」先打一次招呼，这一批都带同一个会话号；每条单独会话：每条都先打招呼，各用各的会话号">
                      <Select size="small" style={{ width: 270, flexShrink: 0 }} value={activeItem.mcpConfig?.session || 'none'} options={MCP_SESSIONS}
                        onChange={v => updateMcp(activeItem, { session: v })} />
                    </Tooltip>
                    <span style={{ width: 8 }} />
                  </>)}
                  <span style={{ whiteSpace: 'nowrap' }}>共发几次</span>
                  <InputNumber value={sendCount} onChange={v => setSendCount(v || 1)} min={1} max={1000} precision={0}
                    size="small" style={{ width: 80, flexShrink: 0 }} />
                  <span style={{ marginLeft: 8, whiteSpace: 'nowrap' }}>一次几条</span>
                  <InputNumber value={sendPerRound} onChange={v => setSendPerRound(v || 1)} min={1} max={50} precision={0}
                    size="small" style={{ width: 70, flexShrink: 0 }} />
                  <span style={{ marginLeft: 8, whiteSpace: 'nowrap' }}>间隔</span>
                  <InputNumber value={sendInterval} onChange={v => setSendInterval(v || null)} min={0} max={3600} step={0.5}
                    placeholder="无" size="small" style={{ width: 90, flexShrink: 0 }} suffix="秒" />
                  <span title="每一轮同时发「一次几条」，这一轮都回来了、隔「间隔」秒再发下一轮；间隔不填就不等"
                    style={{ color: '#c9cdd4', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    每一轮同时发「一次几条」，这一轮都回来了、隔「间隔」秒再发下一轮；间隔不填就不等</span>
                </div>
              </div>

              {/* 请求参数区 — Params / Headers / Body */}
              <div style={{ flexShrink: 0, maxHeight: '40%', overflow: 'auto', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid rgba(0,0,0,0.03)', paddingLeft: 16, alignItems: 'center' }}>
                  {[
                    { key: 'params', label: `Params${urlParams.length ? ` ${urlParams.length}` : ''}` },
                    { key: 'headers', label: `Headers${(activeItem.headers || []).filter(h => h.key).length ? ` ${(activeItem.headers || []).filter(h => h.key).length}` : ''}` },
                    ...(['POST','PUT','PATCH'].includes(activeItem.method) ? [{ key: 'body', label: 'Body' }] : []),
                  ].map(t => (
                    <div key={t.key} onClick={() => setReqTab(t.key)} style={{
                      padding: '6px 12px', cursor: 'pointer', fontSize: 12,
                      color: reqTab === t.key ? '#0ea5a0' : '#86909c', fontWeight: reqTab === t.key ? 600 : 400,
                      borderBottom: reqTab === t.key ? '2px solid #0ea5a0' : '2px solid transparent',
                    }}>{t.label}</div>
                  ))}
                  <span style={{ flex: 1 }} />
                  {reqTab === 'body' && (
                    <span style={{ paddingRight: 16 }}><TextSearchBar search={bodySearch} placeholder="搜索请求体" /></span>
                  )}
                </div>
                <div style={{ padding: '8px 16px' }}>
                  {reqTab === 'params' && (
                    urlParams.length > 0 ? (
                      <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                        <thead><tr style={{ background: 'rgba(0,0,0,0.02)' }}><th style={{ padding: '4px 8px', textAlign: 'left', fontSize: 11, color: '#86909c' }}>参数名</th><th style={{ padding: '4px 8px', textAlign: 'left', fontSize: 11, color: '#86909c' }}>参数值</th></tr></thead>
                        <tbody>{urlParams.map((p, i) => <tr key={i} style={{ borderBottom: '1px solid rgba(0,0,0,0.03)' }}><td style={{ padding: '4px 8px', fontFamily: MONO, fontSize: 11 }}>{p.key}</td><td style={{ padding: '4px 8px', fontFamily: MONO, fontSize: 11, color: '#4e5969' }}>{p.value}</td></tr>)}</tbody>
                      </table>
                    ) : <div style={{ color: '#c9cdd4', fontSize: 12, padding: 8 }}>URL 中没有 Query 参数</div>
                  )}
                  {reqTab === 'headers' && (<>
                    <div style={{ display: 'flex', gap: 4, marginBottom: 6, alignItems: 'center' }}>
                      {headerBulk === null && (<>
                        <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={() => updateTabField(activeItem.id, 'headers', [...(activeItem.headers || []), { key: '', value: '', enabled: true }])}>添加</Button>
                        {[{ k: 'Content-Type', v: 'application/json' }, { k: 'Authorization', v: 'Bearer ' }, { k: 'Accept', v: 'application/json' }].map(p =>
                          <Tag key={p.k} style={{ cursor: 'pointer', fontSize: 11 }} onClick={() => updateTabField(activeItem.id, 'headers', [...(activeItem.headers || []), { key: p.k, value: p.v, enabled: true }])}>{p.k}</Tag>
                        )}
                      </>)}
                      {headerBulk !== null && <span style={{ fontSize: 11, color: '#86909c' }}>一行一个「参数名: 参数值」，行首加 // 停用，行尾「 // 」后写备注</span>}
                      <span style={{ flex: 1 }} />
                      <Tooltip title="中文会自动编码后发送，「实际请求」里能看到编码后的值">
                        <span style={{ fontSize: 11, color: '#c9cdd4', cursor: 'default' }}>支持中文</span>
                      </Tooltip>
                      <Button size="small" type="link" data-testid="header-bulk-toggle" style={{ fontSize: 12, padding: '0 4px' }}
                        onClick={() => setHeaderBulk(headerBulk === null ? headersToBulk(activeItem.headers) : null)}>
                        {headerBulk === null ? '批量编辑' : '退出批量编辑'}
                      </Button>
                    </div>
                    {headerBulk !== null ? (
                      <TextArea spellCheck={false} data-testid="header-bulk-text" value={headerBulk} rows={6}
                        placeholder={'Content-Type: application/json // 请求体格式\nX-User: 张三\n// X-Debug: 1'}
                        style={{ fontFamily: MONO, fontSize: 12 }}
                        onChange={e => { setHeaderBulk(e.target.value); updateTabField(activeItem.id, 'headers', bulkToHeaders(e.target.value)) }} />
                    ) : (
                    <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                      <thead><tr style={{ background: 'rgba(0,0,0,0.02)' }}>
                        <th style={{ width: 30 }}></th>
                        <th style={{ padding: '4px 8px', textAlign: 'left', fontSize: 11, color: '#86909c', width: '30%' }}>参数名</th>
                        <th style={{ padding: '4px 8px', textAlign: 'left', fontSize: 11, color: '#86909c' }}>参数值</th>
                        <th style={{ padding: '4px 8px', textAlign: 'left', fontSize: 11, color: '#86909c', width: '25%' }}>备注</th>
                        <th style={{ width: 96 }}></th>
                      </tr></thead>
                      <tbody>{(activeItem.headers || []).map((h, i) => (
                        <tr key={i} style={{ borderBottom: '1px solid rgba(0,0,0,0.03)', opacity: h.enabled === false ? 0.4 : 1 }}>
                          <td style={{ padding: '2px 4px', textAlign: 'center' }}>
                            <CheckOutlined style={{ fontSize: 11, color: h.enabled !== false ? '#0ea5a0' : '#c9cdd4', cursor: 'pointer' }}
                              onClick={() => { const hs = [...activeItem.headers]; hs[i] = { ...hs[i], enabled: !hs[i].enabled }; updateTabField(activeItem.id, 'headers', hs) }} />
                          </td>
                          <td style={{ padding: '2px 4px' }}><Input spellCheck={false} size="small" variant="borderless" value={h.key} placeholder="Header name" style={{ fontFamily: MONO, fontSize: 11 }}
                            onChange={e => { const hs = [...activeItem.headers]; hs[i] = { ...hs[i], key: e.target.value }; updateTabField(activeItem.id, 'headers', hs) }} /></td>
                          <td style={{ padding: '2px 4px' }}><Input spellCheck={false} size="small" variant="borderless" value={h.value} placeholder="Value" style={{ fontFamily: MONO, fontSize: 11 }}
                            onChange={e => { const hs = [...activeItem.headers]; hs[i] = { ...hs[i], value: e.target.value }; updateTabField(activeItem.id, 'headers', hs) }} /></td>
                          <td style={{ padding: '2px 4px' }}><Input spellCheck={false} size="small" variant="borderless" value={h.desc || ''} placeholder="备注" style={{ fontSize: 11 }}
                            onChange={e => { const hs = [...activeItem.headers]; hs[i] = { ...hs[i], desc: e.target.value }; updateTabField(activeItem.id, 'headers', hs) }} /></td>
                          <td style={{ whiteSpace: 'nowrap' }}>
                            <Tooltip title="上移"><Button size="small" type="text" icon={<ArrowUpOutlined />} disabled={i === 0} style={{ padding: '0 3px', fontSize: 11 }}
                              onClick={() => updateTabField(activeItem.id, 'headers', moveRow(activeItem.headers, i, -1))} /></Tooltip>
                            <Tooltip title="下移"><Button size="small" type="text" icon={<ArrowDownOutlined />} disabled={i === activeItem.headers.length - 1} style={{ padding: '0 3px', fontSize: 11 }}
                              onClick={() => updateTabField(activeItem.id, 'headers', moveRow(activeItem.headers, i, 1))} /></Tooltip>
                            <Tooltip title="复制这一行（插在下面，先不勾选，改好值再勾上）"><Button size="small" type="text" icon={<CopyOutlined />} style={{ padding: '0 3px', fontSize: 11 }}
                              onClick={() => { const hs = [...activeItem.headers]; hs.splice(i + 1, 0, { ...h, enabled: false }); updateTabField(activeItem.id, 'headers', hs) }} /></Tooltip>
                            <Tooltip title="删除"><Button size="small" type="text" danger style={{ padding: '0 4px', fontSize: 11 }} onClick={() => updateTabField(activeItem.id, 'headers', activeItem.headers.filter((_, idx) => idx !== i))}>×</Button></Tooltip>
                          </td>
                        </tr>
                      ))}</tbody>
                    </table>
                    )}
                  </>)}
                  {reqTab === 'body' && (
                    <div style={{ position: 'relative', display: 'flex' }}>
                      {bodyHasSearch && (
                        <div ref={bodyBackdropRef} aria-hidden data-testid="req-body-highlight" style={{
                          position: 'absolute', inset: 0, overflow: 'hidden', pointerEvents: 'none',
                          borderStyle: 'solid', borderColor: 'transparent', boxSizing: 'border-box',
                        }}>
                          <div ref={bodyInnerRef} style={{ color: 'transparent' }}>
                            {highlightText(activeItem.body || '', bodySearch.matches, bodySearch.index, bodyMarkRef)}
                          </div>
                        </div>
                      )}
                      <TextArea ref={bodyRef} spellCheck={false} value={activeItem.body} onChange={e => updateTabField(activeItem.id, 'body', e.target.value)}
                        onScroll={syncBodyBackdrop} rows={6} placeholder='{"key": "value"}'
                        style={{ fontFamily: MONO, fontSize: 12, ...(bodyHasSearch ? { background: 'transparent', position: 'relative' } : null) }} />
                    </div>
                  )}
                </div>
              </div>

              {/* 响应区 — Body / Header / 实际请求 */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                <div style={{ padding: '4px 16px 0', display: 'flex', alignItems: 'center', flexShrink: 0, borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                  {[
                    { key: 'body', label: 'Body' },
                    { key: 'respHeaders', label: `Header${resp && !resp.error ? ` ${Object.keys(resp.headers || {}).length}` : ''}` },
                    { key: 'actual', label: '实际请求' },
                  ].map(t => (
                    <div key={t.key} onClick={() => setRespTab(t.key)} style={{
                      padding: '6px 12px', cursor: 'pointer', fontSize: 12,
                      color: respTab === t.key ? '#0ea5a0' : '#86909c', fontWeight: respTab === t.key ? 600 : 400,
                      borderBottom: respTab === t.key ? '2px solid #0ea5a0' : '2px solid transparent',
                    }}>{t.label}</div>
                  ))}
                  <span style={{ flex: 1 }} />
                  {respTab === 'body' && !!respBodyText && (
                    <span style={{ marginRight: 10 }}><TextSearchBar search={respSearch} placeholder="搜索响应体" /></span>
                  )}
                  {resp && !resp.error && (<>
                    <Tag color={resp.statusCode < 300 ? 'cyan' : resp.statusCode < 400 ? 'blue' : 'red'} style={{ margin: 0, borderRadius: 8, fontFamily: MONO }}>{resp.statusCode}</Tag>
                    <span style={{ fontSize: 11, color: '#86909c', marginLeft: 6 }}>{resp.elapsed}ms</span>
                    <span style={{ fontSize: 11, color: '#86909c', marginLeft: 6 }}>{resp.size > 1024 ? `${(resp.size/1024).toFixed(1)}KB` : `${resp.size}B`}</span>
                    {resp.mcpSessionId && <Tooltip title="这次打招呼拿到的会话号"><span style={{ fontSize: 11, color: MC.MCP, marginLeft: 6, fontFamily: MONO }}>会话 {resp.mcpSessionId}</span></Tooltip>}
                    <Button type="link" size="small" icon={<CopyOutlined />} style={{ fontSize: 11, padding: '0 0 0 8px' }}
                      onClick={() => copyToClipboard(resp.body).then(() => message.success('已复制'))}>复制</Button>
                  </>)}
                </div>
                <div ref={respScrollRef} style={{ flex: 1, overflow: 'auto', padding: '8px 16px' }}>
                  {resp?.batch && (() => {
                    const b = resp.batch
                    return (
                      <div style={{ marginBottom: 10, padding: '8px 12px', background: 'rgba(14,165,160,0.06)', borderRadius: 8, fontSize: 12 }}>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, alignItems: 'center' }}>
                          {b.running && <LoadingOutlined style={{ color: '#0ea5a0' }} />}
                          <span style={{ fontWeight: 600 }}>
                            {b.running ? `正在发：已发 ${b.sent ?? b.rows.length} / ${b.total} 次` : (b.cancelled ? `已停止：发了 ${b.sent ?? b.rows.length} / ${b.total} 次` : `共发 ${b.total} 次`)}
                            {` · 一次 ${b.perRound} 条 · ${b.rounds} 轮`}{b.interval ? ` · 每轮间隔 ${b.interval} 秒` : ''}
                          </span>
                          <span style={{ color: '#0ea5a0' }}>成功 {b.success}</span>
                          <span style={{ color: b.failed ? '#e8453c' : '#86909c' }}>失败 {b.failed}</span>
                          <span style={{ color: '#86909c' }}>平均 {b.avgMs ?? '-'}ms（最快 {b.minMs ?? '-'} / 最慢 {b.maxMs ?? '-'}）</span>
                          <span style={{ color: '#86909c' }}>总用时 {b.elapsedMs}ms</span>
                          <span style={{ color: '#86909c' }}>状态码：{Object.entries(b.statusCodes || {}).map(([k, v]) => `${k}×${v}`).join('  ')}</span>
                          <span style={{ flex: 1 }} />
                          <a onClick={() => setShowBatchRows(v => !v)}>{showBatchRows ? '收起逐条' : '展开逐条'}</a>
                          <Popconfirm title="清空这次的结果？" description="只清页面上这次的结果，发送记录里那一行还在" okText="清空" cancelText="取消"
                            onConfirm={() => setResponses(rs => ({ ...rs, [activeItem.id]: null }))}>
                            <a style={{ color: '#e8453c' }}>清空</a>
                          </Popconfirm>
                        </div>
                        {showBatchRows && (
                          // 条数多时在这一块里自己滚，最多占约 8 行高，下面的响应体始终露出来
                          <div ref={el => { if (el && b.running) el.scrollTop = el.scrollHeight }}  // 发送中跟着最新一条往下滚
                            style={{ marginTop: 8, maxHeight: 200, overflow: 'auto', background: 'rgba(255,255,255,0.6)', borderRadius: 6 }}>
                            <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse', tableLayout: 'fixed' }}>
                              <thead><tr style={{ color: '#86909c', textAlign: 'left' }}>
                                <th style={{ ...{ padding: '3px 6px', position: 'sticky', top: 0, background: '#eef7f6', fontWeight: 500 }, width: 60 }}>第几条</th>
                                <th style={{ ...{ padding: '3px 6px', position: 'sticky', top: 0, background: '#eef7f6', fontWeight: 500 }, width: 60 }}>第几轮</th>
                                <th style={{ ...{ padding: '3px 6px', position: 'sticky', top: 0, background: '#eef7f6', fontWeight: 500 }, width: 70 }}>状态码</th>
                                <th style={{ ...{ padding: '3px 6px', position: 'sticky', top: 0, background: '#eef7f6', fontWeight: 500 }, width: 90 }}>用时</th>
                                {b.rows.some(r => r.sessionId) && <th style={{ ...{ padding: '3px 6px', position: 'sticky', top: 0, background: '#eef7f6', fontWeight: 500 }, width: 200 }}>会话号</th>}
                                <th style={{ ...{ padding: '3px 6px', position: 'sticky', top: 0, background: '#eef7f6', fontWeight: 500 } }}>出错原因</th>
                              </tr></thead>
                              <tbody>{b.rows.map(r => (
                                <tr key={r.seq} style={{ borderTop: '1px solid rgba(0,0,0,0.04)' }}>
                                  <td style={{ padding: '2px 6px' }}>{r.seq}</td><td style={{ padding: '2px 6px' }}>{r.round}</td>
                                  <td style={{ padding: '2px 6px', fontFamily: MONO, color: r.statusCode && r.statusCode < 400 ? undefined : '#e8453c' }}>{r.statusCode ?? '-'}</td>
                                  <td style={{ padding: '2px 6px' }}>{r.elapsed ?? '-'}ms</td>
                                  {b.rows.some(x => x.sessionId) && <td style={{ padding: '2px 6px', fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.sessionId || ''}>{r.sessionId || '-'}</td>}
                                  <td style={{ padding: '2px 6px', color: '#e8453c', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.error || ''}>{r.error || ''}</td>
                                </tr>
                              ))}</tbody>
                            </table>
                          </div>
                        )}
                        <div style={{ color: '#86909c', marginTop: 4 }}>下面显示的是最后一条的响应</div>
                      </div>
                    )
                  })()}
                  {resp ? (resp.error ? (
                    <div style={{ color: '#e8453c', padding: 12, background: 'rgba(232,69,60,0.06)', borderRadius: 8, fontSize: 12 }}>{resp.error}</div>
                  ) : (<>
                    {respTab === 'body' && <pre style={{ margin: 0, fontFamily: MONO, fontSize: 12, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{highlightText(respBodyText, respSearch.matches, respSearch.index, respMarkRef)}</pre>}
                    {respTab === 'respHeaders' && (
                      <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                        <thead><tr style={{ background: 'rgba(0,0,0,0.02)' }}><th style={{ padding: '4px 8px', textAlign: 'left', fontSize: 11, color: '#86909c' }}>名称</th><th style={{ padding: '4px 8px', textAlign: 'left', fontSize: 11, color: '#86909c' }}>值</th></tr></thead>
                        <tbody>{Object.entries(resp.headers || {}).map(([k, v]) => <tr key={k} style={{ borderBottom: '1px solid rgba(0,0,0,0.03)' }}><td style={{ padding: '4px 8px', fontFamily: MONO, fontSize: 11, fontWeight: 500 }}>{k}</td><td style={{ padding: '4px 8px', fontFamily: MONO, fontSize: 11, color: '#4e5969', wordBreak: 'break-all' }}>{v}</td></tr>)}</tbody>
                      </table>
                    )}
                    {respTab === 'actual' && resp.actualRequest && (
                      <div style={{ fontSize: 12 }}>
                        <div style={{ marginBottom: 12 }}>
                          <div style={{ fontWeight: 600, marginBottom: 6 }}>请求 URL</div>
                          <div style={{ padding: 8, background: 'rgba(0,0,0,0.02)', borderRadius: 6, fontFamily: MONO, fontSize: 12 }}>
                            <Tag color={MC[resp.actualRequest.method]} style={{ margin: '0 6px 0 0', fontFamily: MONO }}>{resp.actualRequest.method}</Tag>{resp.actualRequest.url}
                          </div>
                        </div>
                        <div style={{ marginBottom: 12 }}>
                          <div style={{ fontWeight: 600, marginBottom: 6 }}>Header</div>
                          <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                            <thead><tr style={{ background: 'rgba(0,0,0,0.02)' }}><th style={{ padding: '4px 8px', textAlign: 'left', fontSize: 11, color: '#86909c' }}>名称</th><th style={{ padding: '4px 8px', textAlign: 'left', fontSize: 11, color: '#86909c' }}>值</th></tr></thead>
                            <tbody>{Object.entries(resp.actualRequest.headers || {}).map(([k, v]) => <tr key={k} style={{ borderBottom: '1px solid rgba(0,0,0,0.03)' }}><td style={{ padding: '3px 8px', fontFamily: MONO, fontSize: 11, fontWeight: 500 }}>{k}</td><td style={{ padding: '3px 8px', fontFamily: MONO, fontSize: 11, color: '#4e5969', wordBreak: 'break-all' }}>{v}</td></tr>)}</tbody>
                          </table>
                        </div>
                        {resp.actualRequest.body && <div><div style={{ fontWeight: 600, marginBottom: 6 }}>Body</div><pre style={{ margin: 0, padding: 8, background: 'rgba(0,0,0,0.02)', borderRadius: 6, fontFamily: MONO, fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{(() => { try { return JSON.stringify(JSON.parse(resp.actualRequest.body), null, 2) } catch { return resp.actualRequest.body } })()}</pre></div>}
                        <div style={{ marginTop: 16 }}>
                          <div style={{ fontWeight: 600, marginBottom: 6 }}>请求代码</div>
                          <div style={{ marginBottom: 8 }}>
                            <Radio.Group value={codeLang} onChange={e => setCodeLang(e.target.value)} size="small" buttonStyle="solid">
                              <Radio.Button value="curl">Shell</Radio.Button>
                              <Radio.Button value="python">Python</Radio.Button>
                              <Radio.Button value="javascript">JavaScript</Radio.Button>
                            </Radio.Group>
                            <Button type="link" size="small" icon={<CopyOutlined />} style={{ float: 'right' }}
                              onClick={() => { copyToClipboard(toCode(activeItem, codeLang)); message.success('已复制') }}>复制</Button>
                          </div>
                          <pre style={{ padding: 12, background: '#1e1e2e', color: '#cdd6f4', borderRadius: 8, fontFamily: MONO, fontSize: 11, lineHeight: 1.6, overflow: 'auto', maxHeight: 200, whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>{toCode(activeItem, codeLang)}</pre>
                        </div>
                      </div>
                    )}
                  </>)) : (
                    <div style={{ textAlign: 'center', padding: 40, color: '#c9cdd4', fontSize: 12 }}>{isSending ? '请求发送中...' : '点击「发送」查看响应'}</div>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#c9cdd4' }}>
              <div style={{ textAlign: 'center' }}>
                <SendOutlined style={{ fontSize: 36, marginBottom: 12, opacity: 0.2 }} />
                <div style={{ fontSize: 13 }}>新建或选择一个请求</div>
              </div>
            </div>
          )}
        </div>

        {/* 历史面板 */}
        {showHistory && (
          <div style={{ width: 260, flexShrink: 0, background: 'var(--panel-bg)', backdropFilter: 'blur(16px)', borderRadius: 16, display: 'flex', flexDirection: 'column', boxShadow: '0 2px 12px rgba(0,0,0,0.04)' }}>
            <div style={{ padding: '6px 10px', borderBottom: '1px solid rgba(0,0,0,0.04)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>我的发送记录 ({history.length})</span>
              <Space size={4}>
                <Button type="text" size="small" icon={<ReloadOutlined />} onClick={fetchHistory} />
                <Popconfirm title="清空我的发送记录？" onConfirm={async () => { await api.delete('/http-client/history'); setHistory([]) }}><Button type="text" size="small" icon={<ClearOutlined />} danger /></Popconfirm>
              </Space>
            </div>
            <div style={{ flex: 1, overflow: 'auto' }}>
              {history.map(h => (
                <div key={h.id} style={{ padding: '6px 10px', borderBottom: '1px solid rgba(0,0,0,0.03)', fontSize: 11 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ color: '#4e5969' }}>{h.timestamp ? new Date(h.timestamp).toLocaleString('zh-CN', { hour12: false }) : ''}</span>
                    <Tag color={MC[h.method]} style={{ margin: 0, fontSize: 11, lineHeight: '14px', padding: '0 3px', borderRadius: 6 }}>{h.method}</Tag>
                  </div>
                  {h.name && <div style={{ fontWeight: 500, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.name}</div>}
                  <div style={{ fontFamily: MONO, fontSize: 11, color: '#86909c', marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={h.url}>{h.url}</div>
                  <div style={{ marginTop: 2, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <span>共 {h.total} 次 · 一次 {h.perRound} 条</span>
                    <span style={{ color: '#0ea5a0' }}>成功 {h.success}</span>
                    {h.failed > 0 && <span style={{ color: '#e8453c' }}>失败 {h.failed}</span>}
                    {h.avgMs != null && <span style={{ color: '#c9cdd4' }}>{h.avgMs}ms</span>}
                  </div>
                </div>
              ))}
              {history.length === 0 && <div style={{ textAlign: 'center', padding: 20, color: '#c9cdd4', fontSize: 12 }}>还没有发送记录</div>}
            </div>
          </div>
        )}
      </div>

      {/* 导入 cURL */}
      <Modal title="导入 cURL" open={importOpen} onCancel={() => setImportOpen(false)} onOk={handleImportCurl} okText="导入" cancelText="取消" width={600} okButtonProps={{ disabled: !importCurl.trim() }}>
        <div style={{ fontSize: 12, color: '#86909c', marginBottom: 8 }}>粘贴 cURL 命令，自动解析为请求</div>
        <TextArea spellCheck={false} value={importCurl} onChange={e => setImportCurl(e.target.value)} rows={8} style={{ fontFamily: MONO, fontSize: 12 }}
          placeholder={`curl -X POST 'https://api.example.com/users' \\\n  -H 'Content-Type: application/json' \\\n  -H 'Authorization: Bearer token123' \\\n  -d '{"name":"test"}'`} />
      </Modal>

      {/* 查看代码 */}
      <Modal title="请求代码" open={codeOpen} onCancel={() => setCodeOpen(false)} footer={null} width={640}>
        {activeItem && (<>
          <div style={{ marginBottom: 12 }}>
            <Radio.Group value={codeLang} onChange={e => setCodeLang(e.target.value)} size="small" buttonStyle="solid">
              <Radio.Button value="curl">cURL</Radio.Button><Radio.Button value="python">Python</Radio.Button><Radio.Button value="javascript">JavaScript</Radio.Button>
            </Radio.Group>
            <Button type="link" size="small" icon={<CopyOutlined />} style={{ float: 'right' }} onClick={() => { copyToClipboard(toCode(activeItem, codeLang)); message.success('已复制') }}>复制</Button>
          </div>
          <pre style={{ padding: 16, background: '#1e1e2e', color: '#cdd6f4', borderRadius: 8, fontFamily: MONO, fontSize: 12, lineHeight: 1.6, overflow: 'auto', maxHeight: 400, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{toCode(activeItem, codeLang)}</pre>
        </>)}
      </Modal>
    </div>
  )
}
