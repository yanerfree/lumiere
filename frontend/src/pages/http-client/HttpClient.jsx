import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import {
  Button, Input, Select, Tag, Space, message, Popconfirm, Tooltip, Modal, Dropdown, Radio, Switch, InputNumber
} from 'antd'
import {
  SendOutlined, DeleteOutlined, CopyOutlined, LoadingOutlined,
  ClockCircleOutlined, ReloadOutlined, ClearOutlined,
  FolderAddOutlined, FileAddOutlined, FolderOutlined, EditOutlined,
  PlusOutlined, ImportOutlined, CodeOutlined, SaveOutlined, CheckOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'
import { useTextSearch, TextSearchBar, highlightText, scrollMatchIntoView } from '../../components/TextSearch'

const { TextArea } = Input
const MONO = 'var(--font-mono)'
const MC = { GET: '#0ea5a0', POST: '#ff7d00', PUT: '#4e8af0', DELETE: '#e8453c', PATCH: '#7c5cbf', HEAD: '#86909c', OPTIONS: '#86909c' }

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

export default function HttpClient() {
  const [items, setItems] = useState([])
  const [openTabs, setOpenTabs] = useState([])
  const [activeTabId, setActiveTabId] = useState(null)
  const [responses, setResponses] = useState({})
  const [sending, setSending] = useState({})
  const [requestTimeout, setRequestTimeout] = useState(120)
  const [history, setHistory] = useState([])
  const [showHistory, setShowHistory] = useState(false)
  const [editingNameId, setEditingNameId] = useState(null)
  const [editingNameValue, setEditingNameValue] = useState('')
  const [expandedFolders, setExpandedFolders] = useState({})
  const [hoverItemId, setHoverItemId] = useState(null)
  const [popupItemId, setPopupItemId] = useState(null)
  const [selectedFolderId, setSelectedFolderId] = useState(null)
  const [dragId, setDragId] = useState(null)
  const [dragOverId, setDragOverId] = useState(null)
  const [dragPos, setDragPos] = useState(null)
  const [reqTab, setReqTab] = useState('params')
  const [respTab, setRespTab] = useState('body')
  const [importOpen, setImportOpen] = useState(false)
  const [importCurl, setImportCurl] = useState('')
  const [codeOpen, setCodeOpen] = useState(false)
  const [codeLang, setCodeLang] = useState('curl')
  const [navCollapsed, setNavCollapsed] = useState(false)
  const saveTimerRef = useRef({})
  const tabsRef = useRef(openTabs)
  tabsRef.current = openTabs

  const fetchItems = useCallback(async () => { try { const r = await api.get('/http-client/requests'); setItems(r.data || []) } catch {} }, [])
  const fetchHistory = async () => { try { const r = await api.get('/http-client/history?limit=100'); setHistory(r.data?.data || r.data || []) } catch {} }
  useEffect(() => { fetchItems(); fetchHistory() }, [])

  const activeItem = openTabs.find(t => t.id === activeTabId) || null
  const updateTabField = (id, field, value) => {
    setOpenTabs(tabs => tabs.map(t => t.id === id ? { ...t, [field]: value, _dirty: true } : t))
    if (saveTimerRef.current[id]) clearTimeout(saveTimerRef.current[id])
    saveTimerRef.current[id] = setTimeout(() => autoSave(id), 800)
  }
  const autoSave = async (id) => {
    const tab = tabsRef.current.find(t => t.id === id)
    if (!tab || !tab._dirty) return
    try {
      await api.put(`/http-client/requests/${id}`, { name: tab.name, method: tab.method, url: tab.url, headers: tab.headers, body: tab.body, body_type: tab.bodyType })
      setOpenTabs(tabs => tabs.map(t => t.id === id ? { ...t, _dirty: false } : t))
      fetchItems()
    } catch {}
  }
  const openRequest = (item) => {
    if (item.type === 'folder') return
    if (openTabs.find(t => t.id === item.id)) { setActiveTabId(item.id); return }
    setOpenTabs(tabs => [...tabs, { id: item.id, name: item.name, method: item.method || 'GET', url: item.url || '', headers: item.headers || [{ key: '', value: '', enabled: true }], body: item.body || '', bodyType: item.bodyType || 'none', _dirty: false }])
    setActiveTabId(item.id)
  }
  const closeTab = (id) => {
    const tab = openTabs.find(t => t.id === id)
    if (tab?._dirty) autoSave(id)
    const remaining = openTabs.filter(t => t.id !== id)
    setOpenTabs(remaining)
    if (activeTabId === id) setActiveTabId(remaining.length ? remaining[remaining.length - 1].id : null)
  }
  const handleCreate = async (type, parentId) => {
    try {
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
        const children = items.filter(i => i.parentId === id)
        children.forEach(c => closeTab(c.id))
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
  const handleDragDrop = async (srcId, targetId, pos) => {
    if (!srcId || srcId === targetId) return
    const src = items.find(i => i.id === srcId)
    const target = items.find(i => i.id === targetId)
    if (!src || !target) return
    const targetIsFolder = target.type === 'folder'

    if (targetIsFolder && pos === 'inside') {
      await handleMove(srcId, targetId)
      setExpandedFolders(s => ({ ...s, [targetId]: true }))
      return
    }

    if (targetIsFolder && !target.parentId) {
      const allFolders = items.filter(i => i.type === 'folder' && i.id !== srcId)
      const targetIdx = allFolders.findIndex(i => i.id === targetId)
      if (targetIdx < 0) return
      const insertIdx = pos === 'below' ? targetIdx + 1 : targetIdx
      if (src.type === 'folder') {
        allFolders.splice(insertIdx, 0, src)
      } else {
        const sortItems = [{ id: srcId, sort_order: 0, parent_id: '' }]
        try {
          await api.post('/http-client/requests/batch-sort', { items: sortItems })
          fetchItems()
        } catch {}
        return
      }
      const sortItems = allFolders.map((s, idx) => ({
        id: s.id, sort_order: (idx + 1) * 100, parent_id: ''
      }))
      try {
        await api.post('/http-client/requests/batch-sort', { items: sortItems })
        fetchItems()
      } catch {}
      return
    }

    const newParentId = target.parentId || null
    const siblings = items.filter(i => {
      if (i.id === srcId) return false
      if (i.type === 'folder') return false
      return newParentId ? i.parentId === newParentId : !i.parentId
    })
    const targetIdx = siblings.findIndex(i => i.id === targetId)
    if (targetIdx < 0) return
    const insertIdx = pos === 'below' ? targetIdx + 1 : targetIdx
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
      const r = await api.post('/http-client/requests', { type: item.type, name: item.name + ' 副本', method: item.method, url: item.url, parent_id: item.parentId || null })
      const d = r.data || r
      if (item.type === 'folder') {
        const children = items.filter(i => i.parentId === item.id)
        for (const child of children) {
          const cr = await api.post('/http-client/requests', { type: 'request', name: child.name, method: child.method, url: child.url, parent_id: d.id })
          const cd = cr.data || cr
          await api.put(`/http-client/requests/${cd.id}`, { headers: child.headers, body: child.body, body_type: child.bodyType })
        }
      } else {
        await api.put(`/http-client/requests/${d.id}`, { headers: item.headers, body: item.body, body_type: item.bodyType })
      }
      await fetchItems()
      if (item.type !== 'folder') openRequest({ ...d, headers: item.headers, body: item.body })
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
      const res = await api.post('/http-client/send', { method: activeItem.method, url: finalUrl, headers: Object.keys(hdrs).length ? hdrs : null, body: ['POST','PUT','PATCH'].includes(activeItem.method) && activeItem.body?.trim() ? activeItem.body.trim() : null, timeout: requestTimeout })
      const d = res.data?.data || res.data || res
      if (d.error || res.error) { setResponses(r => ({ ...r, [id]: { error: d.error || res.error } })); return }
      setResponses(r => ({ ...r, [id]: d })); fetchHistory()
    } catch (e) { setResponses(r => ({ ...r, [id]: { error: e.message } })) }
    finally { setSending(s => ({ ...s, [id]: false })) }
  }

  // 解析 URL 参数
  const urlParams = activeItem ? (() => {
    try { const u = new URL(activeItem.url.startsWith('http') ? activeItem.url : 'http://x' + activeItem.url); return [...u.searchParams.entries()].map(([k, v]) => ({ key: k, value: v })) } catch { return [] }
  })() : []

  const folders = items.filter(d => d.type === 'folder')
  const requests = items.filter(d => d.type === 'request')
  const resp = activeItem ? responses[activeItem.id] : null
  const isSending = activeItem ? sending[activeItem.id] : false
  const rootRequests = requests.filter(r => !r.parentId)

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
    const children = isFolder ? requests.filter(r => r.parentId === item.id) : []
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
          {!isFolder && <Tag color={MC[item.method]} style={{ margin: 0, fontSize: 11, lineHeight: '14px', padding: '0 3px', borderRadius: 6 }}>{item.method}</Tag>}
          {isEditing ? (
            <Input size="small" autoFocus value={editingNameValue} onChange={e => setEditingNameValue(e.target.value)}
              onBlur={() => handleRename(item.id, editingNameValue)} onPressEnter={() => handleRename(item.id, editingNameValue)}
              style={{ flex: 1, fontSize: 12, height: 22 }} onClick={e => e.stopPropagation()} />
          ) : <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: isFolder ? '#1d2129' : '#4e5969', fontWeight: isFolder ? 500 : 400 }}>{item.name}</span>}
          {isHover && !isEditing && (
            <span style={{ display: 'flex', gap: 2, flexShrink: 0 }} onClick={e => e.stopPropagation()}>
              {isFolder && <Tooltip title="新建请求"><PlusOutlined style={{ fontSize: 11, color: '#86909c', padding: 2 }} onClick={() => { handleCreate('request', item.id); setExpandedFolders(s => ({ ...s, [item.id]: true })) }} /></Tooltip>}
              {!isFolder && folders.length > 0 && (
                <Dropdown
                  onOpenChange={open => { if (open) setPopupItemId(item.id); else { setPopupItemId(null); setHoverItemId(null) } }}
                  menu={{ items: [
                    ...(item.parentId ? [{ key: '__root', label: '根目录', onClick: () => handleMove(item.id, null) }] : []),
                    ...folders.filter(f => f.id !== item.parentId).map(f => ({ key: f.id, label: f.name, onClick: () => handleMove(item.id, f.id) }))
                  ] }}
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
        {isFolder && isExpanded && children.length === 0 && <div style={{ padding: `3px 8px 3px ${26 + depth * 16}px`, fontSize: 11, color: '#c9cdd4', cursor: 'pointer' }} onClick={() => handleCreate('request', item.id)}>+ 新建请求</div>}
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
      <div style={{ padding: '8px 20px', background: 'rgba(255,255,255,0.7)', backdropFilter: 'blur(16px)', borderBottom: '1px solid rgba(255,255,255,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <SendOutlined style={{ fontSize: 18, color: '#0ea5a0' }} />
          <span style={{ fontWeight: 600, fontSize: 16 }}>HTTP 请求</span>
          <span style={{ fontSize: 12, color: '#86909c' }}>{requests.length} 个请求</span>
        </div>
        <Space size={8}>
          <Button size="small" icon={<ImportOutlined />} onClick={() => setImportOpen(true)}>导入 cURL</Button>
          <Button size="small" icon={<ClockCircleOutlined />} type={showHistory ? 'primary' : 'default'} onClick={() => { setShowHistory(!showHistory); if (!showHistory) fetchHistory() }}>历史</Button>
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
          <div style={{ width: 240, flexShrink: 0, background: 'rgba(255,255,255,0.65)', backdropFilter: 'blur(16px)', borderRadius: 16, display: 'flex', flexDirection: 'column', boxShadow: '0 2px 12px rgba(0,0,0,0.04)' }}>
            <div style={{ padding: '6px 8px', borderBottom: '1px solid rgba(0,0,0,0.04)', display: 'flex', gap: 4, alignItems: 'center' }}>
              <Button type="primary" size="small" icon={<FileAddOutlined />} onClick={() => handleCreate('request', selectedFolderId)} style={{ flex: 1 }}>新建请求</Button>
              <Button size="small" icon={<FolderAddOutlined />} onClick={() => handleCreate('folder')}>文件夹</Button>
              <Tooltip title="收起导航">
                <Button type="text" size="small" icon={<MenuFoldOutlined />} onClick={() => setNavCollapsed(true)} style={{ color: '#c9cdd4', flexShrink: 0 }} />
              </Tooltip>
            </div>
            <div style={{ flex: 1, overflow: 'auto' }}
              onDragOver={e => { if (dragId) e.preventDefault() }}
              onDrop={async e => { e.preventDefault(); if (dragId) { await handleMove(dragId, null); setDragId(null); setDragOverId(null); setDragPos(null) } }}>
              {folders.map(f => renderTreeItem(f))}
              {rootRequests.map(r => renderTreeItem(r))}
              {items.length === 0 && <div style={{ textAlign: 'center', padding: 30, color: '#c9cdd4', fontSize: 12 }}>点击上方新建</div>}
            </div>
          </div>
        )}

        {/* 右栏 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: 'rgba(255,255,255,0.55)', backdropFilter: 'blur(12px)', borderRadius: 16, boxShadow: '0 2px 12px rgba(0,0,0,0.04)', overflow: 'hidden' }}>
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
                  <Tag color={MC[t.method]} style={{ margin: 0, fontSize: 11, lineHeight: '14px', padding: '0 3px', borderRadius: 6 }}>{t.method}</Tag>
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
                  <Select value={activeItem.method} onChange={v => updateTabField(activeItem.id, 'method', v)} style={{ width: 110 }} size="small"
                    options={['GET','POST','PUT','DELETE','PATCH','HEAD','OPTIONS'].map(m => ({ value: m, label: <span style={{ color: MC[m], fontWeight: 600, fontFamily: MONO }}>{m}</span> }))} />
                  <Input spellCheck={false} value={activeItem.url} onChange={e => updateTabField(activeItem.id, 'url', e.target.value)}
                    placeholder="输入请求 URL" style={{ flex: 1, fontFamily: MONO, fontSize: 13 }} onPressEnter={handleSend} size="small" />
                  <Button size="small" disabled={!activeItem._dirty} onClick={() => autoSave(activeItem.id)} icon={<SaveOutlined />}>
                    {activeItem._dirty ? '保存' : '已保存'}
                  </Button>
                  <Dropdown menu={{ items: moreMenu }} trigger={['click']}><Button size="small">更多</Button></Dropdown>
                  <Tooltip title="请求超时时间（秒），范围 1-600">
                    <InputNumber value={requestTimeout} onChange={v => setRequestTimeout(v || 120)}
                      min={1} max={600} size="small" style={{ width: 80 }}
                      addonAfter="s" />
                  </Tooltip>
                  <Button type="primary" icon={isSending ? <LoadingOutlined /> : <SendOutlined />} loading={isSending} onClick={handleSend} size="small"
                    style={{ background: '#0ea5a0', borderColor: '#0ea5a0', paddingLeft: 16, paddingRight: 16 }}>发送</Button>
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
                      <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={() => updateTabField(activeItem.id, 'headers', [...(activeItem.headers || []), { key: '', value: '', enabled: true }])}>添加</Button>
                      {[{ k: 'Content-Type', v: 'application/json' }, { k: 'Authorization', v: 'Bearer ' }, { k: 'Accept', v: 'application/json' }].map(p =>
                        <Tag key={p.k} style={{ cursor: 'pointer', fontSize: 11 }} onClick={() => updateTabField(activeItem.id, 'headers', [...(activeItem.headers || []), { key: p.k, value: p.v, enabled: true }])}>{p.k}</Tag>
                      )}
                    </div>
                    <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                      <thead><tr style={{ background: 'rgba(0,0,0,0.02)' }}>
                        <th style={{ width: 30 }}></th>
                        <th style={{ padding: '4px 8px', textAlign: 'left', fontSize: 11, color: '#86909c', width: '35%' }}>参数名</th>
                        <th style={{ padding: '4px 8px', textAlign: 'left', fontSize: 11, color: '#86909c' }}>参数值</th>
                        <th style={{ width: 30 }}></th>
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
                          <td><Button size="small" type="text" danger style={{ padding: '0 4px', fontSize: 11 }} onClick={() => updateTabField(activeItem.id, 'headers', activeItem.headers.filter((_, idx) => idx !== i))}>×</Button></td>
                        </tr>
                      ))}</tbody>
                    </table>
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
                    <Button type="link" size="small" icon={<CopyOutlined />} style={{ fontSize: 11, padding: '0 0 0 8px' }}
                      onClick={() => copyToClipboard(resp.body).then(() => message.success('已复制'))}>复制</Button>
                  </>)}
                </div>
                <div ref={respScrollRef} style={{ flex: 1, overflow: 'auto', padding: '8px 16px' }}>
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
          <div style={{ width: 260, flexShrink: 0, background: 'rgba(255,255,255,0.65)', backdropFilter: 'blur(16px)', borderRadius: 16, display: 'flex', flexDirection: 'column', boxShadow: '0 2px 12px rgba(0,0,0,0.04)' }}>
            <div style={{ padding: '6px 10px', borderBottom: '1px solid rgba(0,0,0,0.04)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>历史 ({history.length})</span>
              <Space size={4}>
                <Button type="text" size="small" icon={<ReloadOutlined />} onClick={fetchHistory} />
                <Popconfirm title="清空？" onConfirm={async () => { await api.delete('/http-client/history'); setHistory([]) }}><Button type="text" size="small" icon={<ClearOutlined />} danger /></Popconfirm>
              </Space>
            </div>
            <div style={{ flex: 1, overflow: 'auto' }}>
              {history.map(h => (
                <div key={h.id} style={{ padding: '6px 10px', borderBottom: '1px solid rgba(0,0,0,0.03)', fontSize: 11 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span><Tag color={MC[h.method]} style={{ margin: 0, fontSize: 11, lineHeight: '14px', padding: '0 3px', borderRadius: 6 }}>{h.method}</Tag>{' '}<Tag color={h.statusCode < 400 ? 'cyan' : 'red'} style={{ margin: 0, fontSize: 11, lineHeight: '14px', padding: '0 3px', borderRadius: 6 }}>{h.statusCode}</Tag></span>
                    <span style={{ color: '#c9cdd4', fontSize: 11 }}>{h.elapsed}ms</span>
                  </div>
                  <div style={{ fontFamily: MONO, fontSize: 11, color: '#4e5969', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.url}</div>
                  <div style={{ fontSize: 11, color: '#c9cdd4' }}>{new Date(h.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}</div>
                </div>
              ))}
              {history.length === 0 && <div style={{ textAlign: 'center', padding: 20, color: '#c9cdd4', fontSize: 12 }}>暂无历史</div>}
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
