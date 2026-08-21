import { useEffect, useMemo, useState } from 'react'
import { Input, Tooltip } from 'antd'
import { SearchOutlined, UpOutlined, DownOutlined } from '@ant-design/icons'

const MONO = 'var(--font-mono)'
// 命中太多时只高亮当前那一处：几千个 <mark> 节点会把长响应的渲染拖死
const HIGHLIGHT_LIMIT = 800
const MATCH_LIMIT = 5000

// 模糊匹配 = 忽略英文大小写的「包含」匹配，返回互不重叠的命中区间 [start, end)
export function findMatches(text, keyword) {
  if (!text || !keyword) return []
  const hay = String(text).toLowerCase()
  const needle = String(keyword).toLowerCase()
  const out = []
  let i = hay.indexOf(needle)
  while (i !== -1 && out.length < MATCH_LIMIT) {
    out.push([i, i + needle.length])
    i = hay.indexOf(needle, i + needle.length)
  }
  return out
}

// 搜索状态：关键词 + 当前命中下标（文本变了自动重算，下标越界自动收敛）
export function useTextSearch(text) {
  const [keyword, setKeyword] = useState('')
  const [cursor, setCursor] = useState(0)
  const matches = useMemo(() => findMatches(text, keyword), [text, keyword])
  const total = matches.length
  const index = total ? Math.min(cursor, total - 1) : 0
  useEffect(() => { setCursor(0) }, [keyword])
  return {
    keyword, setKeyword, matches, total, index,
    capped: total >= MATCH_LIMIT,  // 扫到上限就停了，别把 5000 当成确切数报出去
    active: total ? matches[index] : null,
    prev: () => { if (total) setCursor((index - 1 + total) % total) },
    next: () => { if (total) setCursor((index + 1) % total) },
  }
}

// 搜索条：输入框 + 命中计数 + 上一个/下一个
export function TextSearchBar({ search, width = 170, placeholder = '搜索（忽略大小写）' }) {
  const { keyword, setKeyword, total, index, prev, next, capped } = search
  const navStyle = (on) => ({ fontSize: 11, padding: 2, cursor: on ? 'pointer' : 'not-allowed', color: on ? '#86909c' : '#e5e6eb' })
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
      <Input
        size="small" allowClear spellCheck={false} value={keyword} placeholder={placeholder}
        prefix={<SearchOutlined style={{ fontSize: 11, color: '#c9cdd4' }} />}
        onChange={e => setKeyword(e.target.value)}
        onPressEnter={e => { if (e.shiftKey) prev(); else next() }}
        onKeyDown={e => { if (e.key === 'Escape') setKeyword('') }}
        style={{ width, fontSize: 11 }}
      />
      {!!keyword && (<>
        <span style={{ fontSize: 11, fontFamily: MONO, minWidth: 38, textAlign: 'center', color: total ? '#86909c' : '#e8453c' }}>
          {total ? `${index + 1}/${total}${capped ? '+' : ''}` : '0/0'}
        </span>
        <Tooltip title="上一个 (Shift+Enter)"><UpOutlined style={navStyle(total > 1)} onClick={prev} /></Tooltip>
        <Tooltip title="下一个 (Enter)"><DownOutlined style={navStyle(total > 1)} onClick={next} /></Tooltip>
      </>)}
    </span>
  )
}

// 把命中片段包成 <mark>，当前那一处挂 ref 以便滚动定位
export function highlightText(text, matches, activeIndex, activeRef) {
  if (!matches?.length) return text
  const activeStart = matches[activeIndex]?.[0]
  const show = matches.length > HIGHLIGHT_LIMIT ? [matches[activeIndex]] : matches
  const nodes = []
  let pos = 0
  show.forEach(([s, e], i) => {
    if (s > pos) nodes.push(text.slice(pos, s))
    const isActive = s === activeStart
    nodes.push(
      // color 一律 inherit：高亮层垫在 textarea 底下，这里再画一遍字就会和真文字重影
      <mark key={`${s}-${i}`} ref={isActive ? activeRef : undefined} style={{
        background: isActive ? '#ffc069' : 'rgba(250,173,20,0.28)', color: 'inherit',
        boxShadow: isActive ? '0 0 0 1px #ff7d00' : 'none', padding: 0, borderRadius: 2,
      }}>{text.slice(s, e)}</mark>
    )
    pos = e
  })
  if (pos < text.length) nodes.push(text.slice(pos))
  return nodes
}

// 把命中滚到滚动容器中间。用 rect 差值算，不依赖 offsetParent
export function scrollMatchIntoView(box, mark) {
  if (!box || !mark) return
  const mr = mark.getBoundingClientRect()
  const br = box.getBoundingClientRect()
  box.scrollTop = Math.max(0, box.scrollTop + (mr.top - br.top) - box.clientHeight / 2 + mr.height / 2)
}
